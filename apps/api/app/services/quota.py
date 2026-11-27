"""Workspace AI token quota.

Every AI call goes: reserve(estimate) → call the model → settle(actual) (or release on failure).
`reserve` is one conditional UPDATE, so concurrent requests can't take more than is available; `settle`
swaps the hold for the real weighted cost. A request can only overshoot by the gap between its estimate
and its actual use, never by the number of requests in flight.

Tokens bought as packages are a second pool (`token_grants`) that rolls over: when the period's allowance
can't cover an estimate, what's left of it is held on the ledger and the rest on the grant that expires
first, each with a conditional UPDATE on a locked row. Settling charges the ledger part first.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import ModelMultiplier, Notification, TokenGrant, TokenLedger, TokenReservation, UsageEvent, Workspace
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


async def package_available(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    """Unspent tokens of the workspace's bought packages that haven't expired."""
    q = select(func.coalesce(func.sum(func.greatest(0, TokenGrant.tokens - TokenGrant.reserved - TokenGrant.consumed)), 0)).where(
        TokenGrant.workspace_id == workspace_id, TokenGrant.status == "active", TokenGrant.expires_at > utcnow()
    )
    return int(await db.scalar(q) or 0)


async def total_available(db: AsyncSession, workspace_id: uuid.UUID, ledger: TokenLedger | None = None) -> int:
    """Allowance left plus package tokens; packages are only usable during a paid period."""
    ledger = ledger or await current_ledger(db, workspace_id)
    if ledger is None:
        return 0
    return available(ledger) + await package_available(db, workspace_id)


async def ai_available(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    ws = await db.get(Workspace, workspace_id)
    return bool(ws and ws.status == "active" and await total_available(db, workspace_id) > 0)


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
    grant_id, grant_amount = None, 0
    if row is None:
        row, grant_id, grant_amount = await _reserve_with_package(db, workspace_id, amount)
    if row is None:
        await db.rollback()
        raise QuotaExhausted()
    res = TokenReservation(
        ledger_id=row[0],
        workspace_id=workspace_id,
        amount=amount,
        grant_id=grant_id,
        grant_amount=grant_amount,
        operation=operation,
        model=model,
        created_at=utcnow(),
    )
    db.add(res)
    await db.commit()
    return res


async def _reserve_with_package(db: AsyncSession, workspace_id: uuid.UUID, amount: int):
    """Holds what's left of the allowance plus the rest on one package grant (the one expiring first), or nothing."""
    ledger = (
        await db.execute(
            text(
                "WITH l AS (SELECT id, GREATEST(0, LEAST(:amount, allowance + bonus - reserved - consumed)) AS take "
                "FROM token_ledgers WHERE workspace_id = :ws AND is_current AND period_end > now() FOR UPDATE) "
                "UPDATE token_ledgers t SET reserved = t.reserved + l.take FROM l WHERE t.id = l.id RETURNING t.id, l.take"
            ),
            {"amount": amount, "ws": workspace_id},
        )
    ).first()
    if ledger is None:
        return None, None, 0
    rest = amount - int(ledger[1])
    grant = (
        await db.execute(
            text(
                "WITH g AS (SELECT id FROM token_grants WHERE workspace_id = :ws AND status = 'active' AND expires_at > now() "
                "AND tokens - reserved - consumed >= :rest ORDER BY expires_at, created_at LIMIT 1 FOR UPDATE) "
                "UPDATE token_grants t SET reserved = t.reserved + :rest FROM g WHERE t.id = g.id RETURNING t.id"
            ),
            {"rest": rest, "ws": workspace_id},
        )
    ).first()
    if grant is None:
        return None, None, 0
    return (ledger[0],), grant[0], rest


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
    ledger_held = res.amount - res.grant_amount
    grant_cost = 0
    if res.grant_id and res.grant_amount:
        # The allowance part is charged first; the rest (up to what was held there) goes to the package.
        grant_cost = min(max(0, cost - ledger_held), res.grant_amount)
        await db.execute(
            text("UPDATE token_grants SET reserved = reserved - :held, consumed = consumed + :cost WHERE id = :id"),
            {"held": res.grant_amount, "cost": grant_cost, "id": res.grant_id},
        )
    await db.execute(
        text("UPDATE token_ledgers SET reserved = reserved - :held, consumed = consumed + :cost WHERE id = :id"),
        {"held": ledger_held, "cost": cost - grant_cost, "id": res.ledger_id},
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
        grant_id=res.grant_id if grant_cost else None,
        grant_tokens=grant_cost,
        provider_cost_usd=(input_tokens * w.input_cost_per_m + output_tokens * w.output_cost_per_m) / 1_000_000,
        created_at=utcnow(),
    )
    db.add(event)
    await db.commit()
    await check_thresholds(db, res.ledger_id)
    return event


async def retarget(db: AsyncSession, reservation_id: uuid.UUID, model: str | None = None, *, operation: str | None = None) -> None:
    """Records the model that actually answered (a server-side fallback), or the operation the call turned out
    to be (a model round that asked for an API tool is billed as `tool_call`), so settle uses its weights."""
    if model:
        await db.execute(
            text("UPDATE token_reservations SET model = :model WHERE id = :id AND status = 'held'"), {"id": reservation_id, "model": model}
        )
    if operation:
        await db.execute(
            text("UPDATE token_reservations SET operation = :op WHERE id = :id AND status = 'held'"),
            {"id": reservation_id, "op": operation},
        )
    await db.commit()


async def release(db: AsyncSession, reservation_id: uuid.UUID) -> bool:
    res = await _claim(db, reservation_id, "released")
    if res is None:
        await db.rollback()
        return False
    await db.execute(
        text("UPDATE token_ledgers SET reserved = reserved - :held WHERE id = :id"),
        {"held": res.amount - res.grant_amount, "id": res.ledger_id},
    )
    if res.grant_id and res.grant_amount:
        await db.execute(
            text("UPDATE token_grants SET reserved = reserved - :held WHERE id = :id"), {"held": res.grant_amount, "id": res.grant_id}
        )
    await db.commit()
    return True


async def expire_grants(db: AsyncSession) -> int:
    """Marks package grants past their expiry (12 months after purchase) as expired, once nothing is held on them."""
    rows = (
        await db.execute(
            text("UPDATE token_grants SET status = 'expired' WHERE status = 'active' AND expires_at <= now() AND reserved = 0 RETURNING id")
        )
    ).all()
    await db.commit()
    return len(rows)


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
