"""Plans, the subscription, invoices, token packages and the payment provider's webhook.

Purchases go through the provider's hosted checkout (Stripe Checkout; the mock's test page). Plan changes,
cancellation and billing history are Verity's own screens; payment methods and invoice PDFs are in the
provider's portal. Activation always goes through `billing_events.handle`, the same path a webhook takes.
"""

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.models import BillingAccount, Checkout, Payment, Plan, Subscription, SystemSetting, TokenGrant, TokenPackage
from app.models.base import utcnow
from app.schemas import CheckoutIn, PlanOut
from app.services import billing, billing_events
from app.services.audit import audit
from app.services.payments import ProviderError, ProviderEvent, get_provider, is_upgrade
from app.services.subscriptions import grace_ends_at, is_active, renew_due

router = APIRouter(tags=["billing"])


def _provider_error(e: ProviderError) -> ApiError:
    status = 402 if e.code == "card_declined" else 502
    return ApiError(status, "payment_failed" if e.code == "card_declined" else e.code, e.message)


async def ensure_price(db, prov, item: Plan | TokenPackage, kind: str) -> None:
    """Creates the provider's price for a plan or package the first time it's sold (or after a price change)."""
    if prov.name == "mock" or item.provider_price_ref:
        return
    try:
        refs = await prov.sync_price(item, kind)
    except ProviderError as e:
        raise _provider_error(e) from None
    item.provider_product_ref = refs.product_ref or item.provider_product_ref
    item.provider_price_ref = refs.price_ref


@router.get("/plans", response_model=list[PlanOut])
async def public_plans(db: Db):
    q = select(Plan).where(Plan.is_public.is_(True), Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.price_cents)
    return list((await db.execute(q)).scalars())


def _payment(p: Payment) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "description": p.description,
        "number": p.number,
        "amount_cents": p.amount_cents,
        "subtotal_cents": p.subtotal_cents,
        "tax_cents": p.tax_cents,
        "refunded_cents": p.refunded_cents,
        "currency": p.currency,
        "status": p.status,
        "failure_message": p.failure_message,
        "attempt_count": p.attempt_count,
        "next_retry_at": p.next_retry_at,
        "hosted_url": p.hosted_url,
        "pdf_url": p.pdf_url,
        "period_start": p.period_start,
        "period_end": p.period_end,
        "created_at": p.created_at,
    }


@router.get("/billing")
async def billing_overview(db: Db, ctx: WorkspaceContext = require("billing.read")):
    sub = ctx.subscription
    pending = await db.get(Plan, sub.pending_plan_id) if sub and sub.pending_plan_id else None
    payments = (
        await db.execute(select(Payment).where(Payment.workspace_id == ctx.workspace.id).order_by(Payment.created_at.desc()).limit(100))
    ).scalars()
    acc = (await db.execute(select(BillingAccount).where(BillingAccount.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    return {
        "provider": get_settings().payment_provider,
        "subscription": None
        if sub is None
        else {
            "active": is_active(sub),
            "status": sub.status,
            "provider": sub.provider,
            "plan_id": sub.plan_id,
            "terms": sub.terms,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "pending_plan": None if pending is None else {"id": pending.id, "name": pending.name},
            "past_due_since": sub.past_due_since,
            "next_retry_at": sub.next_retry_at,
            "grace_ends_at": grace_ends_at(sub),
        },
        "account": {
            "currency": acc.currency if acc else None,
            "has_portal": bool(acc and (acc.customer_ref or acc.provider == "mock")),
            "test_card": acc.mock_card if acc and acc.provider == "mock" else None,
        },
        "payments": [_payment(p) for p in payments],
        "can_manage": ctx.can("billing.manage"),
    }


def _app(path: str) -> str:
    return get_settings().app_url.rstrip("/") + path


async def _new_checkout(db, ctx: WorkspaceContext, *, kind: str, item: Plan | TokenPackage) -> dict:
    prov = get_provider()
    acc = await billing.get_account(db, ctx.workspace.id, prov.name)
    billing.check_currency(acc, item.currency)
    await ensure_price(db, prov, item, "plan" if kind == "subscription" else "package")
    checkout = Checkout(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        kind=kind,
        plan_id=item.id if kind == "subscription" else None,
        package_id=item.id if kind == "package" else None,
        provider=prov.name,
        provider_ref="pending:" + uuid.uuid4().hex,
        amount_cents=item.price_cents,
        currency=item.currency,
    )
    db.add(checkout)
    await db.flush()
    try:
        session = await prov.create_checkout(
            kind=kind,
            item=item,
            account=acc,
            customer_email=ctx.user.email,
            workspace_id=ctx.workspace.id,
            local_id=checkout.id,
            success_url=_app(f"/billing/return?checkout={checkout.id}"),
            cancel_url=_app("/billing/tokens" if kind == "package" else "/plans"),
        )
    except ProviderError as e:
        await db.rollback()
        raise _provider_error(e) from None
    checkout.provider_ref = session.ref
    await db.commit()
    return {"checkout_id": checkout.id, "url": session.url, "scheduled": False}


@router.post("/billing/checkout", status_code=201)
async def create_checkout(body: CheckoutIn, request: Request, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    plan = await db.get(Plan, body.plan_id)
    if plan is None or not plan.is_active or not plan.is_public:
        raise not_found("Plan")
    await billing.lock(db, ctx.workspace.id)
    sub = ctx.subscription
    if sub is not None:
        await db.refresh(sub)
    if is_active(sub):
        if sub.plan_id == plan.id:
            raise ApiError(409, "same_plan", "This workspace is already on that plan.")
        if is_upgrade(sub, plan):
            raise ApiError(409, "use_plan_change", "Upgrades are applied with the plan change, not a new checkout.")
        return await _schedule_downgrade(db, ctx, sub, plan, request)
    return await _new_checkout(db, ctx, kind="subscription", item=plan)


@router.get("/billing/checkout/{checkout_id}")
async def get_checkout(checkout_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("billing.read")):
    c = await db.get(Checkout, checkout_id)
    if c is None or c.workspace_id != ctx.workspace.id:
        raise not_found("Checkout")
    plan = await db.get(Plan, c.plan_id) if c.plan_id else None
    package = await db.get(TokenPackage, c.package_id) if c.package_id else None
    return {
        "id": c.id,
        "kind": c.kind,
        "status": c.status,
        "amount_cents": c.amount_cents,
        "currency": c.currency,
        "provider": c.provider,
        "plan": PlanOut.model_validate(plan) if plan else None,
        "package": _package(package) if package else None,
    }


@router.post("/billing/checkout/{checkout_id}/confirm")
async def confirm_checkout(checkout_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    """The mock's hosted checkout page: reports the payment exactly as a Stripe webhook would."""
    c = await db.get(Checkout, checkout_id)
    if c is None or c.workspace_id != ctx.workspace.id:
        raise not_found("Checkout")
    if c.provider != "mock":
        raise ApiError(400, "use_provider", "This checkout is confirmed by the payment provider.")
    if c.status not in ("open", "paid"):
        raise ApiError(409, "checkout_closed", "This checkout is no longer open.")
    mock = get_provider("mock")
    info = mock.checkout_paid(
        checkout_id=c.id,
        ref=c.provider_ref,
        kind=c.kind,
        currency=c.currency,
        amount=c.amount_cents,
        plan_id=c.plan_id,
        package_id=c.package_id,
    )
    await billing_events.handle(
        db, "mock", ProviderEvent(id=f"mock_checkout_{c.id}", type="checkout.completed", object_ref=c.provider_ref, obj=info)
    )
    await db.refresh(c)
    return {"status": c.status, "kind": c.kind}


# ---- Plan changes -------------------------------------------------------------------------------------------


class ChangeIn(BaseModel):
    plan_id: uuid.UUID


async def _change_target(db, ctx: WorkspaceContext, plan_id: uuid.UUID) -> tuple[Subscription, Plan]:
    sub = ctx.subscription
    if not is_active(sub):
        raise ApiError(409, "no_subscription", "There's no active subscription to change. Choose a plan instead.")
    if sub.status == "past_due":
        raise ApiError(409, "past_due", "Settle the failed payment before changing plans.")
    plan = await db.get(Plan, plan_id)
    if plan is None or not plan.is_active or not plan.is_public:
        raise not_found("Plan")
    if sub.plan_id == plan.id:
        raise ApiError(409, "same_plan", "This workspace is already on that plan.")
    if plan.currency != sub.terms["currency"]:
        raise ApiError(
            409,
            "currency_mismatch",
            f"This plan is priced in {plan.currency}, and this workspace pays in {sub.terms['currency']}.",
            workspace_currency=sub.terms["currency"],
        )
    return sub, plan


@router.get("/billing/change-preview")
async def change_preview(plan_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("billing.read")):
    sub, plan = await _change_target(db, ctx, plan_id)
    prov = get_provider(sub.provider)
    await ensure_price(db, prov, plan, "plan")
    acc = await billing.get_account(db, ctx.workspace.id, sub.provider)
    try:
        p = await prov.preview_change(sub, plan, acc, now=utcnow())
    except ProviderError as e:
        raise _provider_error(e) from None
    await db.commit()
    return {
        "kind": "upgrade" if p.immediate else "downgrade",
        "plan": PlanOut.model_validate(plan),
        "currency": p.currency,
        "amount_due_cents": p.amount_due_cents,
        "credit_cents": p.credit_cents,
        "tax_cents": p.tax_cents,
        "effective_at": p.effective_at,
        "tax_note": "Tax is added where it applies." if p.tax_cents is None else None,
    }


async def _schedule_downgrade(db, ctx: WorkspaceContext, sub: Subscription, plan: Plan, request: Request) -> dict:
    prov = get_provider(sub.provider)
    await ensure_price(db, prov, plan, "plan")
    acc = await billing.get_account(db, ctx.workspace.id, sub.provider)
    try:
        await prov.change_plan(sub, plan, acc, immediate=False, now=utcnow())
    except ProviderError as e:
        raise _provider_error(e) from None
    # Downgrades take effect at renewal so the paid period isn't cut short.
    sub.pending_plan_id = plan.id
    billing.mark_price(sub, plan)
    audit(
        db,
        "subscription.change_scheduled",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="plan",
        target_id=plan.key,
        ip=client_ip(request),
    )
    await db.commit()
    return {"status": "scheduled", "scheduled": True, "effective_at": sub.current_period_end}


@router.post("/billing/change")
async def change_plan(body: ChangeIn, request: Request, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    """Upgrades apply now, prorated (unused time is credited and a new period starts); downgrades at renewal."""
    await billing.lock(db, ctx.workspace.id)
    if ctx.subscription is not None:
        await db.refresh(ctx.subscription)
    sub, plan = await _change_target(db, ctx, body.plan_id)
    if not is_upgrade(sub, plan):
        return await _schedule_downgrade(db, ctx, sub, plan, request)
    prov = get_provider(sub.provider)
    await ensure_price(db, prov, plan, "plan")
    acc = await billing.get_account(db, ctx.workspace.id, sub.provider)
    try:
        result = await prov.change_plan(sub, plan, acc, immediate=True, now=utcnow())
    except ProviderError as e:
        raise _provider_error(e) from None
    if result.status == "action_required":
        await db.commit()
        return {"status": "action_required", "url": result.url}
    if result.status != "applied" or result.invoice is None:
        await db.commit()
        raise ApiError(402, "payment_failed", result.message or "The payment didn't go through.")
    after: billing.After = []
    await billing.activate_upgrade(
        db,
        ctx.workspace,
        sub,
        plan,
        result.invoice,
        provider=sub.provider,
        after=after,
        actor_id=ctx.user.id,
        period=(result.period_start, result.period_end),
    )
    await db.commit()
    for f in after:
        await f()
    return {"status": "upgraded", "plan": plan.name}


@router.post("/billing/cancel")
async def cancel(request: Request, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    sub = ctx.subscription
    if not is_active(sub):
        raise ApiError(409, "no_subscription", "There's no active subscription to cancel.")
    await billing.lock(db, ctx.workspace.id)
    try:
        await get_provider(sub.provider).set_cancel_at_period_end(sub, True)
        if sub.pending_plan_id:
            plan = await db.get(Plan, sub.plan_id)
            acc = await billing.get_account(db, ctx.workspace.id, sub.provider)
            await get_provider(sub.provider).change_plan(sub, plan, acc, immediate=False, now=utcnow())
            billing.mark_price(sub, plan)
    except ProviderError as e:
        raise _provider_error(e) from None
    sub.cancel_at_period_end = True
    sub.pending_plan_id = None
    audit(
        db,
        "subscription.cancel_requested",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="subscription",
        target_id=sub.id,
        ip=client_ip(request),
    )
    await db.commit()
    return {"ends_at": sub.current_period_end}


@router.post("/billing/resume")
async def resume(db: Db, ctx: WorkspaceContext = require("billing.manage")):
    sub = ctx.subscription
    if not is_active(sub):
        raise ApiError(409, "no_subscription", "There's no active subscription.")
    try:
        await get_provider(sub.provider).set_cancel_at_period_end(sub, False)
    except ProviderError as e:
        raise _provider_error(e) from None
    sub.cancel_at_period_end = False
    audit(db, "subscription.resumed", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, target_type="subscription", target_id=sub.id)
    await db.commit()
    return {"status": "ok"}


@router.delete("/billing/pending-change", status_code=204)
async def drop_pending_change(db: Db, ctx: WorkspaceContext = require("billing.manage")):
    sub = ctx.subscription
    if sub and sub.pending_plan_id:
        plan = await db.get(Plan, sub.plan_id)
        acc = await billing.get_account(db, ctx.workspace.id, sub.provider)
        try:
            await get_provider(sub.provider).change_plan(sub, plan, acc, immediate=False, now=utcnow())
        except ProviderError as e:
            raise _provider_error(e) from None
        sub.pending_plan_id = None
        audit(
            db, "subscription.change_dropped", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, target_type="plan", target_id=plan.key
        )
        await db.commit()


# ---- Failed payments, the portal and the test card ----------------------------------------------------------


@router.post("/billing/retry")
async def retry_payment(db: Db, ctx: WorkspaceContext = require("billing.manage")):
    """Retries a failed renewal now (after the payment method was updated)."""
    sub = ctx.subscription
    if sub is None or sub.status != "past_due":
        raise ApiError(409, "not_past_due", "There's no failed payment to retry.")
    if sub.provider == "mock":
        sub.next_retry_at = utcnow()
        await db.commit()
        await renew_due(db, workspace_id=ctx.workspace.id)
    else:
        failed = await billing.last_failed_renewal(db, ctx.workspace.id)
        if failed is None or not failed.provider_invoice_ref:
            raise ApiError(409, "not_past_due", "There's no failed payment to retry.")
        try:
            inv = await get_provider(sub.provider).retry_invoice(failed.provider_invoice_ref)
        except ProviderError as e:
            raise _provider_error(e) from None
        if inv.status == "paid":
            event = ProviderEvent(id=f"retry:{inv.ref}:{inv.attempt_count}", type="invoice.paid", object_ref=inv.ref, obj=inv)
            await billing_events.handle(db, sub.provider, event)
    s = (await db.execute(select(Subscription).where(Subscription.workspace_id == ctx.workspace.id))).scalar_one()
    await db.refresh(s)
    if s.status == "past_due":
        raise ApiError(402, "payment_failed", "The payment didn't go through. Update the payment method and try again.")
    return {"status": s.status}


@router.post("/billing/portal")
async def portal(db: Db, ctx: WorkspaceContext = require("billing.manage")):
    """The provider's portal: payment methods, billing details and invoice PDFs."""
    acc = await billing.get_account(db, ctx.workspace.id, ctx.subscription.provider if ctx.subscription else None)
    prov = get_provider(acc.provider)
    config = None
    try:
        if prov.name == "stripe":
            row = await db.get(SystemSetting, "stripe_portal_configuration")
            config = row.value if row else None
            if not config:
                config = await prov.create_portal_configuration()
                db.add(SystemSetting(key="stripe_portal_configuration", value=config, updated_at=utcnow()))
        url = await prov.portal_url(acc, _app("/billing"), configuration=config)
    except ProviderError as e:
        raise ApiError(409 if e.code == "no_customer" else 502, e.code, e.message) from None
    await db.commit()
    return {"url": url}


class TestCardIn(BaseModel):
    card: str = Field(pattern=r"^(ok|declines)$")


@router.put("/billing/test-card")
async def set_test_card(body: TestCardIn, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    """Test portal (mock provider only): the card on file approves or declines the next charge."""
    acc = await billing.get_account(db, ctx.workspace.id, "mock")
    if acc.provider != "mock" or get_settings().payment_provider != "mock":
        raise ApiError(409, "mock_only", "Test cards exist only with the test payment provider.")
    acc.mock_card = body.card
    audit(db, "billing.test_card_changed", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, card=body.card)
    await db.commit()
    return {"card": acc.mock_card}


# ---- Token packages -----------------------------------------------------------------------------------------


def _package(p: TokenPackage) -> dict:
    return {
        "id": p.id,
        "key": p.key,
        "name": p.name,
        "description": p.description,
        "tokens": p.tokens,
        "price_cents": p.price_cents,
        "currency": p.currency,
    }


@router.get("/billing/packages")
async def packages(db: Db, ctx: WorkspaceContext = require("billing.read")):
    items = (
        await db.execute(
            select(TokenPackage)
            .where(TokenPackage.is_public.is_(True), TokenPackage.is_active.is_(True))
            .order_by(TokenPackage.sort_order, TokenPackage.price_cents)
        )
    ).scalars()
    now = utcnow()
    grants = (
        await db.execute(select(TokenGrant).where(TokenGrant.workspace_id == ctx.workspace.id).order_by(TokenGrant.created_at.desc()))
    ).scalars()
    out = []
    for g in grants:
        status = g.status if g.status != "active" or g.expires_at > now else "expired"
        remaining = max(0, g.tokens - g.consumed - g.reserved) if status == "active" else 0
        out.append(
            {
                "id": g.id,
                "name": g.name,
                "tokens": g.tokens,
                "consumed": g.consumed,
                "remaining": remaining,
                "status": "used_up" if status == "active" and remaining == 0 else status,
                "expires_at": g.expires_at,
                "created_at": g.created_at,
            }
        )
    active = [g for g in out if g["status"] == "active"]
    return {
        "packages": [_package(p) for p in items],
        "grants": out,
        "remaining": sum(g["remaining"] for g in active),
        "next_expiry": min((g["expires_at"] for g in active), default=None),
        "can_buy": ctx.can("billing.manage") and ctx.subscribed,
    }


@router.post("/billing/packages/{package_id}/checkout", status_code=201)
async def buy_package(package_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("billing.manage", subscription=True)):
    package = await db.get(TokenPackage, package_id)
    if package is None or not package.is_active or not package.is_public:
        raise not_found("Token package")
    if ctx.subscription and package.currency != ctx.subscription.terms["currency"]:
        raise ApiError(
            409,
            "currency_mismatch",
            f"This package is priced in {package.currency}, and this workspace pays in {ctx.subscription.terms['currency']}.",
            workspace_currency=ctx.subscription.terms["currency"],
        )
    return await _new_checkout(db, ctx, kind="package", item=package)


# ---- Webhooks -----------------------------------------------------------------------------------------------


@router.post("/billing/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: Db):
    """Stripe's events. Public, verified by signature; the body is never stored or logged."""
    if get_settings().payment_provider != "stripe":
        raise not_found()
    prov = get_provider("stripe")
    body = await request.body()
    try:
        event = await prov.parse_webhook(body, {k.lower(): v for k, v in request.headers.items()})
    except ProviderError as e:
        return JSONResponse({"detail": {"code": e.code, "message": e.message}}, status_code=400)
    try:
        status = await billing_events.handle(db, "stripe", event)
    except Exception:  # noqa: BLE001 - recorded as failed; Stripe retries the delivery
        return JSONResponse({"detail": {"code": "event_failed", "message": "The event couldn't be applied; it will be retried."}}, 500)
    return {"received": True, "status": status}
