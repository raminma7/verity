import uuid
from calendar import monthrange
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LIMIT_KEYS, Membership, Payment, Plan, Subscription, SystemSetting, TokenLedger, User, Workspace
from app.models.base import utcnow
from app.services import email as mail
from app.services.audit import audit
from app.services.payments import format_money, get_provider

# Limits for a workspace with no active subscription: settings, plans and billing stay reachable,
# everything that consumes resources is closed.
NO_PLAN_LIMITS = dict.fromkeys(LIMIT_KEYS, 0) | {"members": 1}


def add_months(dt: datetime, months: int) -> datetime:
    m = dt.month - 1 + months
    year, month = dt.year + m // 12, m % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def period_end(start: datetime, cycle: str) -> datetime:
    return add_months(start, 12 if cycle == "year" else 1)


def plan_terms(plan: Plan) -> dict:
    return {
        "plan_key": plan.key,
        "plan_name": plan.name,
        "price_cents": plan.price_cents,
        "currency": plan.currency,
        "billing_cycle": plan.billing_cycle,
        "token_allowance": plan.token_allowance,
        "limits": {k: int(plan.limits.get(k, 0)) for k in LIMIT_KEYS},
    }


async def get_subscription(db: AsyncSession, workspace_id: uuid.UUID) -> Subscription | None:
    return (await db.execute(select(Subscription).where(Subscription.workspace_id == workspace_id))).scalar_one_or_none()


def is_active(sub: Subscription | None, now: datetime | None = None) -> bool:
    return bool(sub and sub.status == "active" and sub.current_period_end > (now or utcnow()))


async def default_limits(db: AsyncSession) -> dict:
    row = await db.get(SystemSetting, "no_plan_limits")
    return {**NO_PLAN_LIMITS, **(row.value if row and isinstance(row.value, dict) else {})}


async def effective_limits(db: AsyncSession, workspace_id: uuid.UUID) -> dict:
    sub = await get_subscription(db, workspace_id)
    if is_active(sub):
        return {**NO_PLAN_LIMITS, **sub.terms["limits"]}
    return await default_limits(db)


async def open_ledger(db: AsyncSession, workspace_id: uuid.UUID, start: datetime, end: datetime, allowance: int) -> TokenLedger:
    """Start a fresh token period. Unused recurring tokens do not roll over."""
    await db.execute(update(TokenLedger).where(TokenLedger.workspace_id == workspace_id).values(is_current=False))
    ledger = TokenLedger(
        workspace_id=workspace_id,
        period_start=start,
        period_end=end,
        allowance=allowance,
        bonus=0,
        reserved=0,
        consumed=0,
        is_current=True,
        notified=[],
    )
    db.add(ledger)
    return ledger


async def start_period(db: AsyncSession, ws: Workspace, plan: Plan, *, now: datetime | None = None) -> Subscription:
    """Activates `plan` for a fresh period starting now (new purchase or upgrade)."""
    now = now or utcnow()
    end = period_end(now, plan.billing_cycle)
    sub = await get_subscription(db, ws.id)
    if sub is None:
        sub = Subscription(workspace_id=ws.id)
        db.add(sub)
    sub.plan_id = plan.id
    sub.status = "active"
    sub.terms = plan_terms(plan)
    sub.current_period_start = now
    sub.current_period_end = end
    sub.cancel_at_period_end = False
    sub.pending_plan_id = None
    await open_ledger(db, ws.id, now, end, plan.token_allowance)
    return sub


async def billing_contacts(db: AsyncSession, workspace_id: uuid.UUID) -> list[User]:
    """Members who can manage billing (owners and custom roles with billing.manage)."""
    rows = (
        await db.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == workspace_id))
    ).all()
    return [u for m, u in rows if "billing.manage" in m.role.permissions and u.status == "active"]


async def record_payment(
    db: AsyncSession, ws: Workspace, plan: Plan, sub: Subscription, *, provider: str, ref: str, checkout_id=None
) -> Payment:
    p = Payment(
        workspace_id=ws.id,
        checkout_id=checkout_id,
        plan_id=plan.id,
        description=f"{plan.name} ({plan.billing_cycle}ly)",
        amount_cents=sub.terms["price_cents"],
        currency=sub.terms["currency"],
        status="succeeded",
        provider=provider,
        provider_ref=ref,
        period_start=sub.current_period_start,
        period_end=sub.current_period_end,
        created_at=utcnow(),
    )
    db.add(p)
    return p


async def send_receipts(db: AsyncSession, ws: Workspace, sub: Subscription) -> None:
    for u in await billing_contacts(db, ws.id):
        if u.notification_prefs.get("billing", True):
            await mail.send_payment_receipt(
                u.email,
                ws.name,
                sub.terms["plan_name"],
                format_money(sub.terms["price_cents"], sub.terms["currency"]),
                sub.current_period_end.strftime("%d %b %Y"),
            )


async def renew_due(db: AsyncSession, now: datetime | None = None) -> dict:
    """Rolls every subscription whose period has ended into the next one.

    Applies a scheduled plan change, refreshes terms from the current plan (so admin price and limit
    edits take effect now), charges through the provider and resets the token allowance.
    """
    now = now or utcnow()
    due = (
        (await db.execute(select(Subscription).where(Subscription.status == "active", Subscription.current_period_end <= now)))
        .scalars()
        .all()
    )
    result = {"renewed": 0, "canceled": 0}
    provider = get_provider()
    for sub in due:
        ws = await db.get(Workspace, sub.workspace_id)
        if sub.cancel_at_period_end or ws is None or ws.status != "active":
            sub.status = "canceled"
            await db.execute(update(TokenLedger).where(TokenLedger.workspace_id == sub.workspace_id).values(is_current=False))
            audit(db, "subscription.canceled", workspace_id=sub.workspace_id, target_type="subscription", target_id=sub.id)
            result["canceled"] += 1
            continue
        plan = await db.get(Plan, sub.pending_plan_id or sub.plan_id)
        if plan is None or not plan.is_active:
            sub.status = "canceled"
            result["canceled"] += 1
            continue
        start = sub.current_period_end
        end = period_end(start, plan.billing_cycle)
        while end <= now:  # catch up if the job didn't run for a while
            start, end = end, period_end(end, plan.billing_cycle)
        sub.plan_id = plan.id
        sub.pending_plan_id = None
        sub.terms = plan_terms(plan)
        sub.current_period_start = start
        sub.current_period_end = end
        await open_ledger(db, ws.id, start, end, plan.token_allowance)
        ref = provider.create_checkout(
            amount_cents=plan.price_cents, currency=plan.currency, description=plan.name, local_id=str(sub.id)
        ).ref
        await record_payment(db, ws, plan, sub, provider=provider.name, ref=ref)
        audit(db, "subscription.renewed", workspace_id=ws.id, target_type="subscription", target_id=sub.id, plan=plan.key)
        result["renewed"] += 1
    await db.commit()
    return result
