"""Provider events → Verity's billing state. The one path for both Stripe webhooks and the mock provider.

Each event is claimed by (provider, event id) in the same transaction that applies it: a second delivery of
the same event waits on the unique index while the first is applied, then finds it done. A failed event is
recorded as failed and applied again when the provider retries it. Objects are re-read from the provider
(the mock sends them whole), so an event that arrives late or out of order still applies the current state.

Only ids, types and outcomes are stored, never payloads, and nothing is logged about the customer's card:
Verity never receives card data.
"""

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingAccount, Checkout, Payment, Plan, Subscription, TokenPackage, Workspace
from app.models.base import utcnow
from app.services import billing
from app.services.payments import CheckoutInfo, InvoiceInfo, ProviderEvent, RefundInfo, SubscriptionInfo, get_provider
from app.services.subscriptions import end_subscription, renew_one

log = logging.getLogger("verity.billing")

HANDLED = {
    "checkout.completed",
    "checkout.expired",
    "invoice.paid",
    "invoice.payment_failed",
    "subscription.updated",
    "subscription.deleted",
    "charge.refunded",
}


class Ignored(Exception):
    """The event needs nothing from Verity (with the reason, for the admin's event list)."""


async def handle(db: AsyncSession, provider: str, event: ProviderEvent) -> str:
    """Applies `event` once. Returns processed | ignored | duplicate. Raises (and records `failed`) on errors,
    so the provider retries the delivery."""
    claimed = (
        await db.execute(
            text(
                "INSERT INTO billing_events (id, provider, event_id, type, object_ref, status, received_at) "
                "VALUES (:id, :p, :e, :t, :o, 'processing', now()) "
                "ON CONFLICT (provider, event_id) DO UPDATE SET status = 'processing', error = NULL "
                "WHERE billing_events.status = 'failed' RETURNING id"
            ),
            {"id": uuid.uuid4(), "p": provider, "e": event.id[:120], "t": event.type[:80], "o": (event.object_ref or "")[:120] or None},
        )
    ).first()
    if claimed is None:
        await db.rollback()
        return "duplicate"
    row_id = claimed[0]
    after: billing.After = []
    status, note, ws_id = "processed", None, None
    try:
        if event.type not in HANDLED:
            raise Ignored("Not used by Verity.")
        ws_id = await _dispatch(db, provider, event, after)
    except Ignored as e:
        status, note = "ignored", str(e)[:300]
    except Exception as e:
        await db.rollback()
        log.warning("billing event %s (%s) failed: %s", event.id, event.type, type(e).__name__)
        await db.execute(
            text(
                "INSERT INTO billing_events (id, provider, event_id, type, object_ref, status, error, received_at) "
                "VALUES (:id, :p, :e, :t, :o, 'failed', :err, now()) "
                "ON CONFLICT (provider, event_id) DO UPDATE SET status = 'failed', error = :err"
            ),
            {
                "id": uuid.uuid4(),
                "p": provider,
                "e": event.id[:120],
                "t": event.type[:80],
                "o": (event.object_ref or "")[:120] or None,
                "err": f"{type(e).__name__}: {getattr(e, 'message', '') or ''}"[:300],
            },
        )
        await db.commit()
        raise
    await db.execute(
        text("UPDATE billing_events SET status = :s, error = :n, workspace_id = :w, processed_at = now() WHERE id = :id"),
        {"s": status, "n": note, "w": ws_id, "id": row_id},
    )
    await db.commit()
    for f in after:
        try:
            await f()
        except Exception:  # noqa: BLE001 - an email failure must not undo a payment
            log.exception("billing notification failed")
    return status


async def _sub_by_ref(db: AsyncSession, ref: str | None) -> Subscription | None:
    if not ref:
        return None
    return (await db.execute(select(Subscription).where(Subscription.provider_subscription_ref == ref))).scalar_one_or_none()


async def _workspace_for_invoice(db: AsyncSession, inv: InvoiceInfo) -> Workspace | None:
    if inv.workspace_id:
        return await db.get(Workspace, inv.workspace_id)
    sub = await _sub_by_ref(db, inv.subscription_ref)
    if sub:
        return await db.get(Workspace, sub.workspace_id)
    if inv.customer_ref:
        acc = (await db.execute(select(BillingAccount).where(BillingAccount.customer_ref == inv.customer_ref))).scalar_one_or_none()
        if acc:
            return await db.get(Workspace, acc.workspace_id)
    return None


async def _plan_for(db: AsyncSession, inv: InvoiceInfo | SubscriptionInfo, fallback: uuid.UUID | None) -> Plan | None:
    plan = await db.get(Plan, inv.plan_id) if inv.plan_id else None
    if plan is None and getattr(inv, "price_ref", None):
        plan = (await db.execute(select(Plan).where(Plan.provider_price_ref == inv.price_ref))).scalar_one_or_none()
    if plan is None and fallback:
        plan = await db.get(Plan, fallback)
    return plan


async def _dispatch(db: AsyncSession, provider: str, event: ProviderEvent, after: billing.After) -> uuid.UUID | None:
    prov = get_provider(provider)
    t = event.type

    if t in ("checkout.completed", "checkout.expired"):
        info = event.obj if isinstance(event.obj, CheckoutInfo) else await prov.get_checkout(event.object_ref)
        if t == "checkout.expired" or info.status == "expired":
            c = await db.get(Checkout, info.local_id) if info.local_id else None
            if c is None:
                raise Ignored("Not a Verity checkout.")
            if c.status == "open":
                c.status = "expired"
            return c.workspace_id
        if not info.paid:
            raise Ignored("Waiting for the payment to clear.")
        c = await billing.complete_checkout(db, provider, info, after)
        if c is None:
            raise Ignored("Not a Verity checkout.")
        return c.workspace_id

    if t in ("invoice.paid", "invoice.payment_failed"):
        inv = event.obj if isinstance(event.obj, InvoiceInfo) else await prov.get_invoice(event.object_ref)
        if inv.reason == "create" and inv.subscription_ref and await _sub_by_ref(db, inv.subscription_ref) is None:
            # The first invoice arrived before its checkout: complete the checkout from here.
            s = await prov.get_subscription(inv.subscription_ref)
            if not s.checkout_id:
                raise Ignored("Not a Verity subscription.")
            if t != "invoice.paid" or inv.status != "paid":
                raise Ignored("First payment not completed.")
            info = CheckoutInfo(
                ref=f"sub:{s.ref}", local_id=s.checkout_id, paid=True, status="complete",
                customer_ref=s.customer_ref, subscription_ref=s.ref, invoice=inv,
            )  # fmt: skip
            c = await billing.complete_checkout(db, provider, info, after)
            if c is None:
                raise Ignored("Not a Verity checkout.")
            return c.workspace_id
        ws = await _workspace_for_invoice(db, inv)
        if ws is None:
            raise Ignored("No Verity workspace for this invoice.")
        await billing.lock(db, ws.id)
        sub = (await db.execute(select(Subscription).where(Subscription.workspace_id == ws.id).with_for_update())).scalar_one_or_none()
        if sub is not None:
            await db.refresh(sub)

        if inv.reason == "package" or inv.package_id:
            package = await db.get(TokenPackage, inv.package_id) if inv.package_id else None
            existing = (await db.execute(select(Payment).where(Payment.provider_invoice_ref == inv.ref))).scalar_one_or_none()
            if existing is None:
                raise Ignored("The package is credited by its checkout.")
            await billing.record_invoice(db, ws, inv, kind="package", package=package, provider=provider)
            return ws.id

        if sub is None:
            raise Ignored("No subscription for this invoice.")
        plan = await _plan_for(db, inv, sub.pending_plan_id or sub.plan_id)
        existing = (await db.execute(select(Payment).where(Payment.provider_invoice_ref == inv.ref))).scalar_one_or_none()

        if t == "invoice.payment_failed":
            if inv.reason == "cycle":
                await billing.payment_failed(db, ws, sub, inv, plan=plan, provider=provider, now=utcnow(), after=after)
            else:  # a failed upgrade leaves the plan as it was; the customer saw why on screen
                await billing.record_invoice(
                    db, ws, inv, kind="upgrade" if inv.reason == "update" else "subscription", plan=plan, provider=provider
                )
            return ws.id

        if existing is not None and existing.status == "succeeded":
            await billing.record_invoice(db, ws, inv, kind=existing.kind, plan=plan, sub=sub, provider=provider)
            return ws.id  # already applied (a retried delivery, or the upgrade applied on screen)
        if inv.reason == "update":
            await billing.activate_upgrade(
                db, ws, sub, plan, inv, provider=provider, after=after, period=(inv.period_start, inv.period_end)
            )
        elif inv.reason == "create":
            await billing.record_invoice(db, ws, inv, kind="subscription", plan=plan, sub=sub, provider=provider)
        else:  # a renewal (or a retried one after a failure)
            was_past_due = sub.status == "past_due"
            if inv.period_start and sub.current_period_start and inv.period_start <= sub.current_period_start:
                pass  # this period is already open
            else:
                await renew_one(db, ws, sub, plan, now=utcnow(), start=inv.period_start, end=inv.period_end)
                billing.mark_price(sub, plan)
            await billing.record_invoice(db, ws, inv, kind="renewal", plan=plan, sub=sub, provider=provider)
            if was_past_due:
                billing.notify_recovered(db, ws, sub, after)
            from app.services.subscriptions import send_receipts

            after.append(lambda: send_receipts(db, ws, sub))
        return ws.id

    if t in ("subscription.updated", "subscription.deleted"):
        info = event.obj if isinstance(event.obj, SubscriptionInfo) else await prov.get_subscription(event.object_ref)
        sub = await _sub_by_ref(db, info.ref)
        if sub is None:
            raise Ignored("No Verity subscription for this id.")
        await billing.lock(db, sub.workspace_id)
        sub = (await db.execute(select(Subscription).where(Subscription.id == sub.id).with_for_update())).scalar_one()
        await db.refresh(sub)
        if t == "subscription.deleted" or info.status in ("canceled", "incomplete_expired"):
            if sub.status != "canceled":
                was_past_due = sub.status == "past_due"
                await end_subscription(db, sub, reason="payment_failed" if was_past_due else "canceled")
                if was_past_due:
                    ws = await db.get(Workspace, sub.workspace_id)
                    billing.notify_ended_unpaid(db, ws, sub, after)
            return sub.workspace_id
        sub.cancel_at_period_end = info.cancel_at_period_end
        if info.status in ("past_due", "unpaid") and sub.status == "active" and sub.current_period_end <= utcnow():
            sub.status = "past_due"
            sub.past_due_since = sub.past_due_since or utcnow()
        return sub.workspace_id

    if t == "charge.refunded":
        info = event.obj if isinstance(event.obj, RefundInfo) else await prov.get_refund(event.object_ref)
        p = await billing.apply_refund(db, info)
        if p is None:
            raise Ignored("No Verity invoice for this charge.")
        return p.workspace_id

    raise Ignored("Not used by Verity.")
