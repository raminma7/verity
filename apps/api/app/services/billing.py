"""Billing operations shared by the routers, the renewal job and the provider events (`billing_events`).

Nothing here commits: callers commit once (a webhook claims its event in the same transaction), then run the
`after` callbacks (emails), so a retried delivery never sends a receipt twice. Everything that changes a
workspace's billing runs under the `billing:{workspace}` advisory lock, so a webhook and a click on "Upgrade"
are applied one after the other.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import (
    BillingAccount,
    Checkout,
    Notification,
    Payment,
    Plan,
    Subscription,
    TokenGrant,
    TokenPackage,
    User,
    Workspace,
)
from app.models.base import utcnow
from app.services import email as mail
from app.services.audit import audit
from app.services.payments import CheckoutInfo, InvoiceInfo, RefundInfo, format_money, get_provider, monthly_value
from app.services.subscriptions import (
    add_months,
    billing_contacts,
    grace_ends_at,
    send_receipts,
    start_period,
)

After = list[Callable[[], Awaitable[None]]]

SUBSCRIPTION_KINDS = ("subscription", "renewal", "upgrade")


async def lock(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"billing:{workspace_id}"})


async def get_account(db: AsyncSession, workspace_id: uuid.UUID, provider: str | None = None) -> BillingAccount:
    acc = (await db.execute(select(BillingAccount).where(BillingAccount.workspace_id == workspace_id))).scalar_one_or_none()
    if acc is None:
        acc = BillingAccount(
            id=uuid.uuid4(), workspace_id=workspace_id, provider=provider or get_settings().payment_provider, mock_card="ok"
        )
        db.add(acc)
        await db.flush()
    return acc


def check_currency(account: BillingAccount | None, currency: str) -> None:
    """A workspace stays in the currency it first paid in."""
    if account and account.currency and account.currency != currency:
        raise ApiError(
            409,
            "currency_mismatch",
            f"This is priced in {currency}, and this workspace pays in {account.currency}.",
            workspace_currency=account.currency,
        )


def _status(inv: InvoiceInfo) -> str:
    if inv.status == "paid":
        return "succeeded"
    if inv.status == "void":
        return "void"
    return "failed"


def _description(kind: str, plan: Plan | None, package: TokenPackage | None) -> str:
    if kind == "package" and package is not None:
        return f"Token package: {package.name}"[:200]
    if plan is None:
        return "Verity"
    cycle = f"{plan.billing_cycle}ly"
    if kind == "upgrade":
        return f"Upgrade to {plan.name} ({cycle}, prorated)"[:200]
    return f"{plan.name} ({cycle})"[:200]


async def record_invoice(
    db: AsyncSession,
    ws: Workspace,
    inv: InvoiceInfo,
    *,
    kind: str,
    provider: str,
    plan: Plan | None = None,
    package: TokenPackage | None = None,
    sub: Subscription | None = None,
    checkout_id: uuid.UUID | None = None,
) -> Payment:
    """Creates or updates the invoice row for `inv` (keyed by the provider's invoice id)."""
    p = (await db.execute(select(Payment).where(Payment.provider_invoice_ref == inv.ref).with_for_update())).scalar_one_or_none()
    if p is None and checkout_id is not None:
        p = (await db.execute(select(Payment).where(Payment.checkout_id == checkout_id).with_for_update())).scalar_one_or_none()
    if p is None:
        p = Payment(id=uuid.uuid4(), workspace_id=ws.id, created_at=utcnow(), provider=provider, kind=kind)
        db.add(p)
    status = _status(inv)
    p.kind = kind
    p.checkout_id = p.checkout_id or checkout_id
    p.plan_id = plan.id if plan else p.plan_id
    p.package_id = package.id if package else p.package_id
    p.description = _description(kind, plan, package)
    p.amount_cents = inv.total_cents
    p.subtotal_cents = inv.subtotal_cents
    p.tax_cents = inv.tax_cents
    p.currency = inv.currency.upper()
    if p.status != "refunded":
        p.status = status
    p.provider_ref = inv.payment_ref or inv.ref
    p.provider_invoice_ref = inv.ref
    p.number = inv.number or p.number
    p.hosted_url = inv.hosted_url or p.hosted_url
    p.pdf_url = inv.pdf_url or p.pdf_url
    p.failure_message = None if status == "succeeded" else (inv.failure_message or p.failure_message)
    p.attempt_count = max(inv.attempt_count, p.attempt_count or 1)
    p.next_retry_at = None if status == "succeeded" else inv.next_retry_at
    if kind in SUBSCRIPTION_KINDS and sub is not None and status == "succeeded":
        p.period_start, p.period_end = sub.current_period_start, sub.current_period_end
        p.mrr_cents = monthly_value(sub.terms["price_cents"], sub.terms["billing_cycle"])
    elif kind in SUBSCRIPTION_KINDS and plan is not None and status != "succeeded":
        p.period_start, p.period_end = inv.period_start, inv.period_end
        p.mrr_cents = 0
    if status == "succeeded":
        acc = await get_account(db, ws.id, provider)
        acc.currency = acc.currency or p.currency
        if inv.customer_ref and not acc.customer_ref:
            acc.customer_ref = inv.customer_ref
    await db.flush()
    return p


async def last_failed_renewal(db: AsyncSession, workspace_id: uuid.UUID) -> Payment | None:
    q = (
        select(Payment)
        .where(Payment.workspace_id == workspace_id, Payment.kind == "renewal", Payment.status == "failed")
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    return (await db.execute(q)).scalar_one_or_none()


def _notify(db: AsyncSession, ws: Workspace, kind: str, title: str, body: str, data: dict | None = None) -> None:
    db.add(Notification(workspace_id=ws.id, kind=kind, title=title, body=body, data=data or {}, created_at=utcnow()))


def _mail_contacts(db: AsyncSession, ws: Workspace, after: After, send: Callable[[User], Awaitable[None]]) -> None:
    async def run() -> None:
        for u in await billing_contacts(db, ws.id):
            if u.notification_prefs.get("billing", True):
                await send(u)

    after.append(run)


def _day(dt: datetime | None) -> str:
    return dt.strftime("%d %b %Y") if dt else ""


async def payment_failed(
    db: AsyncSession,
    ws: Workspace,
    sub: Subscription,
    inv: InvoiceInfo,
    *,
    plan: Plan,
    provider: str,
    now: datetime,
    after: After,
) -> Payment:
    """A renewal didn't go through: the subscription is past due and payment is retried until the grace period ends.
    AI pauses (no new token period); manual ticketing, email ingestion and forms stay open meanwhile."""
    first = sub.status != "past_due"
    sub.status = "past_due"
    sub.past_due_since = sub.past_due_since or now
    sub.retry_count = (sub.retry_count or 0) + (0 if first else 1)
    if provider == "mock":
        days = get_settings().mock_retry_days
        grace = grace_ends_at(sub)
        nxt = add_days(sub.past_due_since, days[sub.retry_count]) if sub.retry_count < len(days) else grace
        sub.next_retry_at = min(nxt, grace) if grace else nxt
        inv.next_retry_at = sub.next_retry_at if sub.next_retry_at and grace and sub.next_retry_at < grace else None
        inv.attempt_count = sub.retry_count + 1
    else:
        sub.retry_count = max(0, inv.attempt_count - 1)
        sub.next_retry_at = inv.next_retry_at
    p = await record_invoice(db, ws, inv, kind="renewal", plan=plan, sub=sub, provider=provider)
    audit(
        db,
        "subscription.payment_failed",
        workspace_id=ws.id,
        target_type="subscription",
        target_id=sub.id,
        attempt=p.attempt_count,
        amount_cents=p.amount_cents,
    )
    grace = grace_ends_at(sub)
    amount = format_money(p.amount_cents, p.currency)
    retry = f"We'll try again on {_day(p.next_retry_at)}." if p.next_retry_at else "There are no automatic retries left."
    _notify(
        db,
        ws,
        "payment_failed",
        "Payment failed",
        f"The {amount} renewal of {plan.name} didn't go through. {retry} AI answers are paused until it's paid; "
        f"tickets, email and forms keep working until {_day(grace)}.",
        {"payment_id": str(p.id)},
    )
    _mail_contacts(
        db,
        ws,
        after,
        lambda u: mail.send_payment_failed(u.email, ws.name, plan.name, amount, p.failure_message, p.next_retry_at, grace, locale=u.locale),
    )
    return p


def add_days(dt: datetime, days: int) -> datetime:
    return dt + timedelta(days=days)


def notify_recovered(db: AsyncSession, ws: Workspace, sub: Subscription, after: After) -> None:
    audit(db, "subscription.payment_recovered", workspace_id=ws.id, target_type="subscription", target_id=sub.id)
    _notify(db, ws, "payment_recovered", "Payment received", f"{sub.terms['plan_name']} is active again, and AI answers are back on.")
    _mail_contacts(db, ws, after, lambda u: mail.send_payment_recovered(u.email, ws.name, sub.terms["plan_name"], locale=u.locale))


def notify_ended_unpaid(db: AsyncSession, ws: Workspace, sub: Subscription, after: After) -> None:
    _notify(
        db,
        ws,
        "subscription_ended",
        "Subscription ended",
        f"{sub.terms['plan_name']} ended because the renewal couldn't be charged. Choose a plan to start again.",
    )
    _mail_contacts(db, ws, after, lambda u: mail.send_subscription_ended(u.email, ws.name, sub.terms["plan_name"], locale=u.locale))


async def align_period(db: AsyncSession, sub: Subscription, start: datetime | None, end: datetime | None) -> None:
    """Uses the period the provider charged for (its calendar can differ from ours by a day at month ends)."""
    if not start or not end or (start, end) == (sub.current_period_start, sub.current_period_end):
        return
    sub.current_period_start, sub.current_period_end = start, end
    await db.execute(
        text("UPDATE token_ledgers SET period_end = :e WHERE workspace_id = :ws AND is_current"),
        {"e": end, "ws": sub.workspace_id},
    )


def mark_price(sub: Subscription, plan: Plan | None) -> None:
    """Remembers which provider price the subscription is billed at, so a later price change can be applied."""
    if plan is not None and plan.provider_price_ref:
        sub.terms = {**sub.terms, "price_ref": plan.provider_price_ref}


async def sync_prices(db: AsyncSession) -> int:
    """Moves provider-managed subscriptions to their plan's current price (after an admin changed it), without
    proration: the new price is billed from the next renewal. Runs hourly."""
    subs = (
        (await db.execute(select(Subscription).where(Subscription.provider != "mock", Subscription.status.in_(("active", "past_due")))))
        .scalars()
        .all()
    )
    moved = 0
    for sub in subs:
        target = await db.get(Plan, sub.pending_plan_id or sub.plan_id)
        if target is None or not target.is_active or sub.cancel_at_period_end:
            continue
        prov = get_provider(sub.provider)
        if not target.provider_price_ref:
            refs = await prov.sync_price(target, "plan")
            target.provider_product_ref = refs.product_ref or target.provider_product_ref
            target.provider_price_ref = refs.price_ref
        if sub.terms.get("price_ref") == target.provider_price_ref:
            continue
        acc = await get_account(db, sub.workspace_id, sub.provider)
        try:
            await prov.change_plan(sub, target, acc, immediate=False, now=utcnow())
        except Exception:  # noqa: BLE001 - tried again next hour
            continue
        mark_price(sub, target)
        audit(db, "subscription.price_synced", workspace_id=sub.workspace_id, target_type="plan", target_id=target.key)
        moved += 1
        await db.commit()
    await db.commit()
    return moved


async def grant_package(db: AsyncSession, ws: Workspace, package: TokenPackage, payment: Payment) -> TokenGrant:
    """Bought tokens roll over: their own grant, spent after the period's allowance, expiring after 12 months."""
    existing = (await db.execute(select(TokenGrant).where(TokenGrant.payment_id == payment.id))).scalar_one_or_none()
    if existing:
        return existing
    now = utcnow()
    grant = TokenGrant(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        package_id=package.id,
        payment_id=payment.id,
        name=package.name,
        tokens=package.tokens,
        reserved=0,
        consumed=0,
        status="active",
        expires_at=add_months(now, get_settings().token_package_months),
        created_at=now,
    )
    db.add(grant)
    audit(db, "tokens.package_bought", workspace_id=ws.id, target_type="token_package", target_id=package.key, tokens=package.tokens)
    return grant


async def complete_checkout(
    db: AsyncSession, provider: str, info: CheckoutInfo, after: After, *, actor_id: uuid.UUID | None = None
) -> Checkout | None:
    """A hosted checkout was paid: activate the plan or credit the package. Safe to run twice (both the checkout
    and the first invoice report it; webhooks are retried)."""
    if info.local_id is None:
        return None
    c = await db.get(Checkout, info.local_id)
    if c is None:
        return None
    await lock(db, c.workspace_id)
    c = (await db.execute(select(Checkout).where(Checkout.id == info.local_id).with_for_update())).scalar_one()
    await db.refresh(c)
    ws = await db.get(Workspace, c.workspace_id)
    prov = get_provider(provider)
    inv = info.invoice
    if inv is None and info.invoice_ref:
        inv = await prov.get_invoice(info.invoice_ref)
    acc = await get_account(db, ws.id, provider)
    if info.customer_ref and not acc.customer_ref:
        acc.customer_ref = info.customer_ref
    if c.status == "paid":
        if inv is not None:  # fill in invoice details that arrived after the activation
            plan = await db.get(Plan, c.plan_id) if c.plan_id else None
            package = await db.get(TokenPackage, c.package_id) if c.package_id else None
            sub = (await db.execute(select(Subscription).where(Subscription.workspace_id == ws.id))).scalar_one_or_none()
            kind = "package" if c.kind == "package" else "subscription"
            await record_invoice(db, ws, inv, kind=kind, plan=plan, package=package, sub=sub, provider=provider, checkout_id=c.id)
        return c
    if c.status not in ("open", "failed"):
        return c
    if inv is None:  # paid but its invoice isn't known yet: record from the checkout's own amounts
        inv = InvoiceInfo(
            ref=f"{info.ref}:invoice", status="paid", reason="create", currency=c.currency,
            subtotal_cents=c.amount_cents, tax_cents=0, total_cents=c.amount_cents,
        )  # fmt: skip
    c.status = "paid"
    if c.kind == "package":
        package = await db.get(TokenPackage, c.package_id)
        p = await record_invoice(db, ws, inv, kind="package", package=package, provider=provider, checkout_id=c.id)
        await grant_package(db, ws, package, p)
        audit(db, "billing.package_paid", actor_id=actor_id, workspace_id=ws.id, target_type="token_package", target_id=package.key)
        amount = format_money(p.amount_cents, p.currency)
        _mail_contacts(
            db, ws, after, lambda u: mail.send_package_receipt(u.email, ws.name, package.name, package.tokens, amount, locale=u.locale)
        )
        return c
    plan = await db.get(Plan, c.plan_id)
    sub = await start_period(db, ws, plan)
    mark_price(sub, plan)
    sub.provider = provider
    sub.past_due_since = None
    sub.retry_count = 0
    sub.next_retry_at = None
    if info.subscription_ref:
        sub.provider_subscription_ref = info.subscription_ref
        try:
            s = await prov.get_subscription(info.subscription_ref)
            await align_period(db, sub, s.current_period_start, s.current_period_end)
        except Exception:  # noqa: BLE001 - the period is corrected by the next event
            pass
    await db.flush()
    await record_invoice(db, ws, inv, kind="subscription", plan=plan, sub=sub, provider=provider, checkout_id=c.id)
    audit(
        db,
        "subscription.activated",
        actor_id=actor_id,
        workspace_id=ws.id,
        target_type="plan",
        target_id=plan.key,
        amount_cents=inv.total_cents,
    )
    after.append(lambda: send_receipts(db, ws, sub))
    return c


async def activate_upgrade(
    db: AsyncSession,
    ws: Workspace,
    sub: Subscription,
    plan: Plan,
    inv: InvoiceInfo,
    *,
    provider: str,
    after: After,
    actor_id: uuid.UUID | None = None,
    period: tuple[datetime | None, datetime | None] = (None, None),
) -> Payment:
    """A paid, prorated upgrade: the new plan starts a fresh period now (unused time was credited)."""
    existing = (await db.execute(select(Payment).where(Payment.provider_invoice_ref == inv.ref))).scalar_one_or_none()
    if existing and existing.status == "succeeded" and sub.plan_id == plan.id:
        return existing
    from_key = sub.terms.get("plan_key")
    sub = await start_period(db, ws, plan)
    mark_price(sub, plan)
    await align_period(db, sub, *period)
    await db.flush()
    p = await record_invoice(db, ws, inv, kind="upgrade", plan=plan, sub=sub, provider=provider)
    audit(
        db,
        "subscription.upgraded",
        actor_id=actor_id,
        workspace_id=ws.id,
        target_type="plan",
        target_id=plan.key,
        from_plan=from_key,
        amount_cents=inv.total_cents,
    )
    after.append(lambda: send_receipts(db, ws, sub))
    return p


async def apply_refund(db: AsyncSession, info: RefundInfo) -> Payment | None:
    """A refund made at the provider: recorded on the invoice; a refunded package loses its unspent tokens."""
    p = None
    if info.invoice_ref:
        p = (await db.execute(select(Payment).where(Payment.provider_invoice_ref == info.invoice_ref))).scalar_one_or_none()
    if p is None and info.payment_ref:
        p = (await db.execute(select(Payment).where(Payment.provider_ref == info.payment_ref))).scalar_one_or_none()
    if p is None:
        return None
    await lock(db, p.workspace_id)
    p.refunded_cents = min(p.amount_cents, info.amount_refunded_cents)
    full = p.refunded_cents >= p.amount_cents
    if full:
        p.status = "refunded"
    grant = (await db.execute(select(TokenGrant).where(TokenGrant.payment_id == p.id).with_for_update())).scalar_one_or_none()
    if grant is not None:
        if full:
            grant.status = "revoked"
        else:
            kept = int(grant.tokens * (1 - p.refunded_cents / max(1, p.amount_cents)))
            grant.tokens = max(grant.consumed + grant.reserved, kept)
    audit(db, "billing.refunded", workspace_id=p.workspace_id, target_type="payment", target_id=p.id, refunded_cents=p.refunded_cents)
    return p


__all__ = [
    "After",
    "activate_upgrade",
    "apply_refund",
    "check_currency",
    "complete_checkout",
    "get_account",
    "grant_package",
    "last_failed_renewal",
    "lock",
    "notify_ended_unpaid",
    "notify_recovered",
    "payment_failed",
    "record_invoice",
]
