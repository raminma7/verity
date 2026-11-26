"""Workspace AI token quota.

Every AI call goes: reserve(estimate) → call the model → settle(actual) (or release on failure).
`reserve` is one conditional UPDATE, so concurrent requests can't take more than is available; `settle`
swaps the hold for the real weighted cost. A request can only overshoot by the gap between its estimate
and its actual use, never by the number of requests in flight.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import ModelMultiplier, Notification, TokenLedger, TokenReservation, UsageEvent, Workspace
from app.models.base import utcnow
from app.services import email as mail
from app.services.subscriptions import billing_contacts

THRESHOLDS = (80, 90, 100)


class QuotaExhausted(ApiError):
    def __init__(self):
        super().__init__(402, "quota_exhausted", "This workspace has used its AI token allowance for this period.")


@dataclass
class Weights:
    input: float = 1.0
    output: float = 1.0
    input_cost_per_m: float = 0.0
    output_cost_per_m: float = 0.0


async def weights_for(db: AsyncSession, model: str, operation: str) -> Weights:
    rows = (
        (await db.execute(select(ModelMultiplier).where(ModelMultiplier.model == model, ModelMultiplier.operation.in_([operation, "*"]))))
        .scalars()
        .all()
    )
    if not rows:
        return Weights()
    m = sorted(rows, key=lambda r: r.operation == "*")[0]  # an exact operation beats the wildcard
    return Weights(m.input_multiplier, m.output_multiplier, m.input_cost_per_m, m.output_cost_per_m)


def weighted(w: Weights, input_tokens: int, output_tokens: int) -> int:
    return int(round(input_tokens * w.input + output_tokens * w.output))


async def current_ledger(db: AsyncSession, workspace_id: uuid.UUID) -> TokenLedger | None:
    q = select(TokenLedger).where(
        TokenLedger.workspace_id == workspace_id, TokenLedger.is_current.is_(True), TokenLedger.period_end > utcnow()
    )
    return (await db.execute(q)).scalar_one_or_none()


def available(ledger: TokenLedger | None) -> int:
    if ledger is None:
        return 0
    return max(0, ledger.allowance + ledger.bonus - ledger.reserved - ledger.consumed)


async def ai_available(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    ws = await db.get(Workspace, workspace_id)
    return bool(ws and ws.status == "active" and available(await current_ledger(db, workspace_id)) > 0)


async def reserve(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    model: str,
    operation: str,
    est_input: int,
    est_output: int,
) -> TokenReservation:
    """Holds the estimated weighted cost of a call. Commits immediately so the hold is visible to others."""
    ws = await db.get(Workspace, workspace_id)
    if ws is None or ws.status != "active":
        raise QuotaExhausted()
    amount = max(1, weighted(await weights_for(db, model, operation), est_input, est_output))
    row = (
        await db.execute(
            text(
                "UPDATE token_ledgers SET reserved = reserved + :amount "
                "WHERE workspace_id = :ws AND is_current AND period_end > now() "
                "AND allowance + bonus - reserved - consumed >= :amount RETURNING id"
            ),
            {"amount": amount, "ws": workspace_id},
        )
    ).first()
    if row is None:
        await db.rollback()
        raise QuotaExhausted()
    res = TokenReservation(
        ledger_id=row[0], workspace_id=workspace_id, amount=amount, operation=operation, model=model, created_at=utcnow()
    )
    db.add(res)
    await db.commit()
    return res


async def _claim(db: AsyncSession, reservation_id: uuid.UUID, status: str) -> TokenReservation | None:
    """Moves a held reservation to `status` exactly once (settle and release are idempotent)."""
    row = (
        await db.execute(
            text("UPDATE token_reservations SET status = :status, settled_at = now() WHERE id = :id AND status = 'held' RETURNING id"),
            {"id": reservation_id, "status": status},
        )
    ).first()
    if row is None:
        return None
    res = await db.get(TokenReservation, reservation_id)
    await db.refresh(res)
    return res


async def settle(
    db: AsyncSession,
    reservation_id: uuid.UUID,
    *,
    input_tokens: int,
    output_tokens: int,
    kind: str = "llm",
) -> UsageEvent | None:
    res = await _claim(db, reservation_id, "settled")
    if res is None:
        await db.rollback()
        return None
    w = await weights_for(db, res.model, res.operation)
    cost = weighted(w, input_tokens, output_tokens)
    await db.execute(
        text("UPDATE token_ledgers SET reserved = reserved - :held, consumed = consumed + :cost WHERE id = :id"),
        {"held": res.amount, "cost": cost, "id": res.ledger_id},
    )
    event = UsageEvent(
        workspace_id=res.workspace_id,
        ledger_id=res.ledger_id,
        kind=kind,
        operation=res.operation,
        model=res.model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_multiplier=w.input,
        output_multiplier=w.output,
        weighted_tokens=cost,
        provider_cost_usd=(input_tokens * w.input_cost_per_m + output_tokens * w.output_cost_per_m) / 1_000_000,
        created_at=utcnow(),
    )
    db.add(event)
    await db.commit()
    await check_thresholds(db, res.ledger_id)
    return event


async def release(db: AsyncSession, reservation_id: uuid.UUID) -> bool:
    res = await _claim(db, reservation_id, "released")
    if res is None:
        await db.rollback()
        return False
    await db.execute(text("UPDATE token_ledgers SET reserved = reserved - :held WHERE id = :id"), {"held": res.amount, "id": res.ledger_id})
    await db.commit()
    return True


async def release_stale(db: AsyncSession, older_than: timedelta = timedelta(minutes=15)) -> int:
    """Frees holds left by crashed requests."""
    ids = (
        (
            await db.execute(
                select(TokenReservation.id).where(TokenReservation.status == "held", TokenReservation.created_at < utcnow() - older_than)
            )
        )
        .scalars()
        .all()
    )
    n = 0
    for rid in ids:
        n += await release(db, rid)
    return n


async def check_thresholds(db: AsyncSession, ledger_id: uuid.UUID) -> list[int]:
    """Sends one alert per threshold per period. The JSONB guard makes each alert fire once under concurrency."""
    ledger = await db.get(TokenLedger, ledger_id)
    await db.refresh(ledger)
    total = ledger.allowance + ledger.bonus
    if total <= 0:
        return []
    pct = ledger.consumed * 100 / total
    fired = []
    for t in THRESHOLDS:
        if pct < t:
            continue
        row = (
            await db.execute(
                text(
                    "UPDATE token_ledgers SET notified = notified || to_jsonb(CAST(:t AS int)) "
                    "WHERE id = :id AND NOT notified @> to_jsonb(CAST(:t AS int)) RETURNING id"
                ),
                {"t": t, "id": ledger_id},
            )
        ).first()
        if row:
            fired.append(t)
    if not fired:
        return []
    ws = await db.get(Workspace, ledger.workspace_id)
    t = max(fired)
    remaining = max(0, total - ledger.consumed)
    title = "AI token allowance used up" if t >= 100 else f"{t}% of AI tokens used"
    db.add(
        Notification(
            workspace_id=ws.id,
            kind="usage_threshold",
            title=title,
            body=f"{remaining:,} weighted tokens remain this period.",
            data={"threshold": t},
            created_at=utcnow(),
        )
    )
    await db.commit()
    for u in await billing_contacts(db, ws.id):
        if u.notification_prefs.get("usage_alerts", True):
            await mail.send_usage_alert(u.email, ws.name, min(t, 100), remaining)
    return fired
