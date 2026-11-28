import uuid
from calendar import monthrange
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import LIMIT_KEYS, Membership, Payment, Plan, Subscription, SystemSetting, TokenLedger, User, Workspace
from app.models.base import utcnow
from app.services import email as mail
from app.services.audit import audit
from app.services.payments import format_money, get_provider, monthly_value

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


def grace_ends_at(sub: Subscription) -> datetime | None:
    """A past-due subscription stays open (without AI) until this moment while payment is retried."""
    if sub.status != "past_due" or sub.past_due_since is None:
        return None
    return sub.past_due_since + timedelta(days=get_settings().past_due_grace_days)


def is_active(sub: Subscription | None, now: datetime | None = None) -> bool:
    """Paid up, or past due within the grace period. A provider that renews on its own (Stripe) reports the
    renewal by webhook, so its subscriptions get a short leeway after the period end."""
    if sub is None:
        return False
    now = now or utcnow()
    if sub.status == "active":
        end = sub.current_period_end
        if sub.provider != "mock":
            end = end + timedelta(hours=get_settings().renewal_leeway_hours)
        return end > now
    if sub.status == "past_due":
        until = grace_ends_at(sub)
        return bool(until and until > now)
    return False


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
    db: AsyncSession,
    ws: Workspace,
    plan: Plan,
    sub: Subscription,
    *,
    provider: str,
    ref: str,
    checkout_id=None,
    kind: str = "subscription",
) -> Payment:
    p = Payment(
        workspace_id=ws.id,
        checkout_id=checkout_id,
        plan_id=plan.id,
        description=f"{plan.name} ({plan.billing_cycle}ly)",
        amount_cents=sub.terms["price_cents"],
        subtotal_cents=sub.terms["price_cents"],
        tax_cents=0,
        mrr_cents=monthly_value(sub.terms["price_cents"], sub.terms["billing_cycle"]),
        currency=sub.terms["currency"],
        status="succeeded",
        kind=kind,
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
                sub.current_period_end,
                locale=u.locale,
            )


async def end_subscription(db: AsyncSession, sub: Subscription, *, reason: str = "canceled") -> None:
    """Ends a subscription at its period end (cancellation, a suspended workspace, an unpaid renewal)."""
    sub.status = "canceled"
    sub.next_retry_at = None
    await db.execute(update(TokenLedger).where(TokenLedger.workspace_id == sub.workspace_id).values(is_current=False))
    audit(db, "subscription.canceled", workspace_id=sub.workspace_id, target_type="subscription", target_id=sub.id, reason=reason)


async def renew_one(
    db: AsyncSession,
    ws: Workspace,
    sub: Subscription,
    plan: Plan,
    *,
    now: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> None:
    """Rolls a subscription into its next period on `plan`: refreshes terms from the plan (so admin price and
    limit edits take effect now), clears a scheduled change and resets the token allowance.

    The period follows on from the last one (catching up if the job didn't run for a while), unless the provider
    that charged it says which period it paid for (`start`, `end`)."""
    if start is None or end is None:
        start = sub.current_period_end
        end = period_end(start, plan.billing_cycle)
        while end <= now:  # catch up if the job didn't run for a while
            start, end = end, period_end(end, plan.billing_cycle)
    sub.plan_id = plan.id
    sub.pending_plan_id = None
    sub.status = "active"
    sub.past_due_since = None
    sub.retry_count = 0
    sub.next_retry_at = None
    sub.terms = plan_terms(plan)
    sub.current_period_start = start
    sub.current_period_end = end
    await open_ledger(db, ws.id, start, end, plan.token_allowance)
    audit(db, "subscription.renewed", workspace_id=ws.id, target_type="subscription", target_id=sub.id, plan=plan.key)


async def renew_due(db: AsyncSession, now: datetime | None = None, *, workspace_id: uuid.UUID | None = None) -> dict:
    """Renews every subscription Verity charges itself (the mock provider) whose period has ended, and retries
    past-due ones on schedule. Stripe renews its own subscriptions and reports them by webhook.

    A cancellation, a suspended workspace or an inactive plan ends the subscription. A declined card makes it
    past due: AI pauses (the new token period isn't opened), the rest stays open for the grace period, payment
    is retried on the mock schedule, and the subscription ends when the grace period is over.
    """
    from app.services import billing  # billing builds on this module

    now = now or utcnow()
    due = (
        (
            await db.execute(
                select(Subscription.id).where(
                    Subscription.provider == "mock",
                    or_(
                        (Subscription.status == "active") & (Subscription.current_period_end <= now),
                        (Subscription.status == "past_due") & (Subscription.next_retry_at <= now),
                        (Subscription.status == "past_due") & Subscription.next_retry_at.is_(None),
                    ),
                    *([Subscription.workspace_id == workspace_id] if workspace_id else []),
                )
            )
        )
        .scalars()
        .all()
    )
    result = {"renewed": 0, "canceled": 0, "failed": 0}
    provider = get_provider("mock")
    for sub_id in due:
        await billing.lock(db, (await db.get(Subscription, sub_id)).workspace_id)
        sub = (await db.execute(select(Subscription).where(Subscription.id == sub_id).with_for_update())).scalar_one()
        await db.refresh(sub)
        after: list = []
        ws = await db.get(Workspace, sub.workspace_id)
        grace = grace_ends_at(sub)
        if sub.cancel_at_period_end or ws is None or ws.status != "active":
            await end_subscription(db, sub)
            result["canceled"] += 1
        elif sub.status == "past_due" and grace and grace <= now:
            await end_subscription(db, sub, reason="payment_failed")
            billing.notify_ended_unpaid(db, ws, sub, after)
            result["canceled"] += 1
        else:
            plan = await db.get(Plan, sub.pending_plan_id or sub.plan_id)
            if plan is None or not plan.is_active:
                await end_subscription(db, sub, reason="plan_inactive")
                result["canceled"] += 1
            else:
                account = await billing.get_account(db, ws.id, "mock")
                failed = await billing.last_failed_renewal(db, ws.id) if sub.status == "past_due" else None
                inv = await provider.charge_renewal(
                    sub, account, plan, invoice_ref=failed.provider_invoice_ref if failed else None, now=now
                )
                if failed is not None:  # a retry of the failed invoice
                    inv.attempt_count = (failed.attempt_count or 1) + 1
                if inv.status == "paid":
                    was_past_due = sub.status == "past_due"
                    await renew_one(db, ws, sub, plan, now=now)
                    await billing.record_invoice(db, ws, inv, kind="renewal", plan=plan, sub=sub, provider="mock")
                    if was_past_due:
                        billing.notify_recovered(db, ws, sub, after)
                    after.append(lambda ws=ws, sub=sub: send_receipts(db, ws, sub))
                    result["renewed"] += 1
                else:
                    await billing.payment_failed(db, ws, sub, inv, plan=plan, provider="mock", now=now, after=after)
                    result["failed"] += 1
        await db.commit()
        for f in after:
            await f()
    await db.commit()
    return result
