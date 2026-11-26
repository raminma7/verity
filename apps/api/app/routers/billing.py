import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.models import Checkout, Payment, Plan, Workspace
from app.schemas import CheckoutIn, PlanOut
from app.services.audit import audit
from app.services.payments import get_provider
from app.services.subscriptions import is_active, record_payment, send_receipts, start_period

router = APIRouter(tags=["billing"])


def monthly_price(price_cents: int, cycle: str) -> float:
    return price_cents / 12 if cycle == "year" else price_cents


@router.get("/plans", response_model=list[PlanOut])
async def public_plans(db: Db):
    q = select(Plan).where(Plan.is_public.is_(True), Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.price_cents)
    return list((await db.execute(q)).scalars())


@router.get("/billing")
async def billing_overview(db: Db, ctx: WorkspaceContext = require("billing.read")):
    sub = ctx.subscription
    pending = await db.get(Plan, sub.pending_plan_id) if sub and sub.pending_plan_id else None
    payments = (
        await db.execute(select(Payment).where(Payment.workspace_id == ctx.workspace.id).order_by(Payment.created_at.desc()).limit(50))
    ).scalars()
    return {
        "subscription": None
        if sub is None
        else {
            "active": is_active(sub),
            "status": sub.status,
            "plan_id": sub.plan_id,
            "terms": sub.terms,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "pending_plan": None if pending is None else {"id": pending.id, "name": pending.name},
        },
        "payments": [
            {
                "id": p.id,
                "description": p.description,
                "amount_cents": p.amount_cents,
                "currency": p.currency,
                "status": p.status,
                "period_start": p.period_start,
                "period_end": p.period_end,
                "created_at": p.created_at,
            }
            for p in payments
        ],
        "can_manage": ctx.can("billing.manage"),
    }


@router.post("/billing/checkout", status_code=201)
async def create_checkout(body: CheckoutIn, request: Request, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    plan = await db.get(Plan, body.plan_id)
    if plan is None or not plan.is_active or not plan.is_public:
        raise not_found("Plan")
    sub = ctx.subscription
    if is_active(sub):
        if sub.plan_id == plan.id:
            raise ApiError(409, "same_plan", "This workspace is already on that plan.")
        current = monthly_price(sub.terms["price_cents"], sub.terms["billing_cycle"])
        if monthly_price(plan.price_cents, plan.billing_cycle) <= current:
            # Downgrades take effect at renewal so the paid period isn't cut short.
            sub.pending_plan_id = plan.id
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
            return {"scheduled": True, "effective_at": sub.current_period_end}
    provider = get_provider()
    checkout = Checkout(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        plan_id=plan.id,
        provider=provider.name,
        provider_ref="pending",
        amount_cents=plan.price_cents,
        currency=plan.currency,
    )
    session = provider.create_checkout(
        amount_cents=plan.price_cents, currency=plan.currency, description=f"Verity {plan.name}", local_id=str(checkout.id)
    )
    checkout.provider_ref = session.ref
    db.add(checkout)
    await db.commit()
    return {"checkout_id": checkout.id, "url": session.url, "scheduled": False}


@router.get("/billing/checkout/{checkout_id}")
async def get_checkout(checkout_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("billing.read")):
    c = await db.get(Checkout, checkout_id)
    if c is None or c.workspace_id != ctx.workspace.id:
        raise not_found("Checkout")
    plan = await db.get(Plan, c.plan_id)
    return {
        "id": c.id,
        "status": c.status,
        "amount_cents": c.amount_cents,
        "currency": c.currency,
        "provider": c.provider,
        "plan": PlanOut.model_validate(plan),
    }


async def complete_checkout(db, checkout_id: uuid.UUID, actor_id: uuid.UUID | None) -> Checkout:
    """Marks a checkout paid and activates the plan. Safe to call twice (webhook retries)."""
    c = (await db.execute(select(Checkout).where(Checkout.id == checkout_id).with_for_update())).scalar_one()
    if c.status == "paid":
        return c
    if c.status != "open":
        raise ApiError(409, "checkout_closed", "This checkout is no longer open.")
    if not get_provider().confirm(c.provider_ref):
        c.status = "failed"
        await db.commit()
        raise ApiError(402, "payment_failed", "The payment didn't go through.")
    ws = await db.get(Workspace, c.workspace_id)
    plan = await db.get(Plan, c.plan_id)
    sub = await start_period(db, ws, plan)
    c.status = "paid"
    await record_payment(db, ws, plan, sub, provider=c.provider, ref=c.provider_ref, checkout_id=c.id)
    audit(
        db,
        "subscription.activated",
        actor_id=actor_id,
        workspace_id=ws.id,
        target_type="plan",
        target_id=plan.key,
        amount_cents=c.amount_cents,
    )
    await db.commit()
    await send_receipts(db, ws, sub)
    return c


@router.post("/billing/checkout/{checkout_id}/confirm")
async def confirm_checkout(checkout_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    c = await db.get(Checkout, checkout_id)
    if c is None or c.workspace_id != ctx.workspace.id:
        raise not_found("Checkout")
    if c.provider != "mock":
        raise ApiError(400, "use_provider", "This checkout is confirmed by the payment provider.")
    c = await complete_checkout(db, checkout_id, ctx.user.id)
    return {"status": c.status}


@router.post("/billing/cancel")
async def cancel(request: Request, db: Db, ctx: WorkspaceContext = require("billing.manage")):
    sub = ctx.subscription
    if not is_active(sub):
        raise ApiError(409, "no_subscription", "There's no active subscription to cancel.")
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
    sub.cancel_at_period_end = False
    audit(db, "subscription.resumed", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, target_type="subscription", target_id=sub.id)
    await db.commit()
    return {"status": "ok"}


@router.delete("/billing/pending-change", status_code=204)
async def drop_pending_change(db: Db, ctx: WorkspaceContext = require("billing.manage")):
    if ctx.subscription:
        ctx.subscription.pending_plan_id = None
        await db.commit()
