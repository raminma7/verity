"""System administration. Every route needs a verified system administrator."""

import contextlib
import uuid
from datetime import timedelta

from fastapi import APIRouter, Request
from sqlalchemy import delete, func, or_, select, update

from app.core.config import get_settings
from app.core.deps import Db, SystemAdmin, client_ip
from app.core.errors import ApiError, not_found
from app.models import (
    LIMIT_KEYS,
    AuditLog,
    BillingEvent,
    Membership,
    ModelMultiplier,
    Plan,
    Session,
    Subscription,
    SystemSetting,
    TokenLedger,
    TokenPackage,
    UsageEvent,
    User,
    Workspace,
)
from app.models.base import utcnow
from app.schemas import (
    AdjustTokensIn,
    MultiplierIn,
    PlanIn,
    PlanOut,
    PlanUpdateIn,
    StatusIn,
    TokenPackageIn,
    TokenPackageOut,
    TokenPackageUpdateIn,
)
from app.services import quota
from app.services.audit import audit
from app.services.payments import ProviderError, get_provider
from app.services.subscriptions import default_limits, renew_due

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview")
async def overview(_: SystemAdmin, db: Db):
    now = utcnow()
    active_subs = (
        (await db.execute(select(Subscription).where(Subscription.status == "active", Subscription.current_period_end > now)))
        .scalars()
        .all()
    )
    mrr = sum(s.terms["price_cents"] / (12 if s.terms["billing_cycle"] == "year" else 1) for s in active_subs)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usage = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageEvent.weighted_tokens), 0), func.coalesce(func.sum(UsageEvent.provider_cost_usd), 0.0)
            ).where(UsageEvent.created_at >= month_start)
        )
    ).one()
    return {
        "users": await db.scalar(select(func.count()).select_from(User)),
        "users_suspended": await db.scalar(select(func.count()).select_from(User).where(User.status == "suspended")),
        "workspaces": await db.scalar(select(func.count()).select_from(Workspace)),
        "active_subscriptions": len(active_subs),
        "mrr_cents": int(mrr),
        "tokens_this_month": int(usage[0]),
        "provider_cost_this_month_usd": round(float(usage[1]), 2),
    }


# ---- Users --------------------------------------------------------------------------------------


@router.get("/users")
async def users(_: SystemAdmin, db: Db, q: str = "", offset: int = 0, limit: int = 50):
    stmt = select(User).order_by(User.created_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.full_name.ilike(like)))
    rows = (await db.execute(stmt.offset(max(offset, 0)).limit(min(limit, 200)))).scalars().all()
    counts = dict(
        (
            await db.execute(
                select(Membership.user_id, func.count()).where(Membership.user_id.in_([u.id for u in rows])).group_by(Membership.user_id)
            )
        ).all()
    )
    return [
        {
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "status": u.status,
            "email_verified": u.is_verified,
            "is_system_admin": u.is_system_admin,
            "totp_enabled": u.totp_enabled,
            "workspaces": counts.get(u.id, 0),
            "created_at": u.created_at,
            "last_login_at": u.last_login_at,
        }
        for u in rows
    ]


@router.patch("/users/{user_id}/status")
async def set_user_status(user_id: uuid.UUID, body: StatusIn, request: Request, admin: SystemAdmin, db: Db):
    user = await db.get(User, user_id)
    if user is None:
        raise not_found("User")
    if user.id == admin.id:
        raise ApiError(400, "self", "You can't change your own status.")
    user.status = body.status
    if body.status == "suspended":
        await db.execute(update(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit(
        db, f"admin.user_{body.status}", actor_id=admin.id, target_type="user", target_id=user.id, reason=body.reason, ip=client_ip(request)
    )
    await db.commit()
    return {"status": user.status}


# ---- Workspaces ---------------------------------------------------------------------------------


@router.get("/workspaces")
async def workspaces(_: SystemAdmin, db: Db, q: str = "", offset: int = 0, limit: int = 50):
    stmt = select(Workspace, User).join(User, User.id == Workspace.owner_id).order_by(Workspace.created_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Workspace.name.ilike(like), Workspace.slug.ilike(like), User.email.ilike(like)))
    rows = (await db.execute(stmt.offset(max(offset, 0)).limit(min(limit, 200)))).all()
    out = []
    for ws, owner in rows:
        sub = (await db.execute(select(Subscription).where(Subscription.workspace_id == ws.id))).scalar_one_or_none()
        ledger = await quota.current_ledger(db, ws.id)
        members = await db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == ws.id))
        out.append(
            {
                "id": ws.id,
                "name": ws.name,
                "slug": ws.slug,
                "status": ws.status,
                "owner": {"id": owner.id, "email": owner.email, "full_name": owner.full_name},
                "members": members,
                "plan": sub.terms["plan_name"] if sub else None,
                "subscription_status": sub.status if sub else None,
                "renews_at": sub.current_period_end if sub else None,
                "tokens": None
                if ledger is None
                else {
                    "allowance": ledger.allowance,
                    "bonus": ledger.bonus,
                    "consumed": ledger.consumed,
                    "reserved": ledger.reserved,
                },
                "created_at": ws.created_at,
            }
        )
    return out


@router.patch("/workspaces/{workspace_id}/status")
async def set_workspace_status(workspace_id: uuid.UUID, body: StatusIn, request: Request, admin: SystemAdmin, db: Db):
    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise not_found("Workspace")
    ws.status = body.status
    audit(
        db,
        f"admin.workspace_{body.status}",
        actor_id=admin.id,
        workspace_id=ws.id,
        target_type="workspace",
        target_id=ws.id,
        reason=body.reason,
        ip=client_ip(request),
    )
    await db.commit()
    return {"status": ws.status}


@router.post("/workspaces/{workspace_id}/tokens")
async def adjust_tokens(workspace_id: uuid.UUID, body: AdjustTokensIn, request: Request, admin: SystemAdmin, db: Db):
    ledger = (
        await db.execute(
            select(TokenLedger)
            .where(TokenLedger.workspace_id == workspace_id, TokenLedger.is_current.is_(True), TokenLedger.period_end > utcnow())
            .with_for_update()
        )
    ).scalar_one_or_none()
    if ledger is None:
        raise ApiError(409, "no_period", "This workspace has no active token period to adjust.")
    if ledger.allowance + ledger.bonus + body.delta < 0:
        raise ApiError(422, "negative_allowance", "The adjustment would make the allowance negative.")
    ledger.bonus += body.delta
    audit(
        db,
        "admin.tokens_adjusted",
        actor_id=admin.id,
        workspace_id=workspace_id,
        target_type="token_ledger",
        target_id=ledger.id,
        delta=body.delta,
        reason=body.reason,
        ip=client_ip(request),
    )
    await db.commit()
    return {"allowance": ledger.allowance, "bonus": ledger.bonus, "remaining": quota.available(ledger)}


# ---- Plans --------------------------------------------------------------------------------------


def _clean_limits(limits: dict[str, int]) -> dict[str, int]:
    unknown = set(limits) - set(LIMIT_KEYS)
    if unknown:
        raise ApiError(422, "unknown_limit", f"Unknown limits: {', '.join(sorted(unknown))}")
    if any(v < -1 for v in limits.values()):
        raise ApiError(422, "invalid_limit", "Limits are -1 (unlimited) or a number of zero or more.")
    return {k: int(limits.get(k, 0)) for k in LIMIT_KEYS}


@router.get("/plans", response_model=list[PlanOut])
async def all_plans(_: SystemAdmin, db: Db):
    return list((await db.execute(select(Plan).order_by(Plan.sort_order, Plan.price_cents))).scalars())


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(body: PlanIn, admin: SystemAdmin, db: Db):
    if (await db.execute(select(Plan.id).where(Plan.key == body.key))).first():
        raise ApiError(409, "plan_exists", "A plan with that key exists.")
    plan = Plan(**body.model_dump() | {"limits": _clean_limits(body.limits)})
    db.add(plan)
    audit(db, "admin.plan_created", actor_id=admin.id, target_type="plan", target_id=body.key)
    await db.commit()
    return plan


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(plan_id: uuid.UUID, body: PlanUpdateIn, admin: SystemAdmin, db: Db):
    """Changes apply to new purchases now and to existing subscriptions at their next renewal."""
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise not_found("Plan")
    changes = body.model_dump(exclude_unset=True)
    if "limits" in changes:
        changes["limits"] = _clean_limits({**plan.limits, **changes["limits"]})
    if changes.get("currency") not in (None, plan.currency) and await _has_subscribers(db, plan.id):
        # A workspace stays in the currency it first paid in: make a new plan for another currency.
        raise ApiError(409, "plan_currency_locked", "This plan has subscribers, so its currency can't change. Create a new plan instead.")
    repriced = any(k in changes and changes[k] != getattr(plan, k) for k in ("price_cents", "currency", "billing_cycle"))
    for k, v in changes.items():
        setattr(plan, k, v)
    if repriced:
        await _retire_price(plan)
    audit(db, "admin.plan_updated", actor_id=admin.id, target_type="plan", target_id=plan.key, changes=list(changes))
    await db.commit()
    return plan


async def _has_subscribers(db, plan_id: uuid.UUID) -> bool:
    q = select(Subscription.id).where(
        or_(Subscription.plan_id == plan_id, Subscription.pending_plan_id == plan_id), Subscription.status.in_(("active", "past_due"))
    )
    return (await db.execute(q.limit(1))).first() is not None


async def _retire_price(item) -> None:
    """Provider prices are immutable: a new one is created at the next sale or the hourly sync, which moves
    existing subscribers to it from their next renewal. The old one stops being offered."""
    old = item.provider_price_ref
    item.provider_price_ref = None
    if old:
        with contextlib.suppress(ProviderError):  # an unarchived old price is harmless: nothing sells it any more
            await get_provider().archive_price(old)


# ---- Token packages -----------------------------------------------------------------------------


@router.get("/token-packages", response_model=list[TokenPackageOut])
async def all_packages(_: SystemAdmin, db: Db):
    return list((await db.execute(select(TokenPackage).order_by(TokenPackage.sort_order, TokenPackage.price_cents))).scalars())


@router.post("/token-packages", response_model=TokenPackageOut, status_code=201)
async def create_package(body: TokenPackageIn, admin: SystemAdmin, db: Db):
    if (await db.execute(select(TokenPackage.id).where(TokenPackage.key == body.key))).first():
        raise ApiError(409, "package_exists", "A token package with that key exists.")
    pkg = TokenPackage(**body.model_dump())
    db.add(pkg)
    audit(db, "admin.token_package_created", actor_id=admin.id, target_type="token_package", target_id=body.key, tokens=body.tokens)
    await db.commit()
    return pkg


@router.patch("/token-packages/{package_id}", response_model=TokenPackageOut)
async def update_package(package_id: uuid.UUID, body: TokenPackageUpdateIn, admin: SystemAdmin, db: Db):
    """Changes apply to new purchases; tokens already bought keep what was paid for."""
    pkg = await db.get(TokenPackage, package_id)
    if pkg is None:
        raise not_found("Token package")
    changes = body.model_dump(exclude_unset=True)
    repriced = any(k in changes and changes[k] != getattr(pkg, k) for k in ("price_cents", "currency"))
    for k, v in changes.items():
        setattr(pkg, k, v)
    if repriced:
        await _retire_price(pkg)
    audit(db, "admin.token_package_updated", actor_id=admin.id, target_type="token_package", target_id=pkg.key, changes=list(changes))
    await db.commit()
    return pkg


# ---- Payment provider ---------------------------------------------------------------------------


@router.get("/billing/events")
async def billing_events_list(_: SystemAdmin, db: Db, status: str | None = None, limit: int = 100):
    """Recent provider events (webhooks) and how each was applied. Only ids and types are kept."""
    q = select(BillingEvent, Workspace.name).outerjoin(Workspace, Workspace.id == BillingEvent.workspace_id)
    if status:
        q = q.where(BillingEvent.status == status)
    rows = (await db.execute(q.order_by(BillingEvent.received_at.desc()).limit(max(1, min(limit, 500))))).all()
    s = get_settings()
    return {
        "provider": s.payment_provider,
        "webhook_url": s.app_url.rstrip("/") + "/api/billing/webhooks/stripe" if s.payment_provider == "stripe" else None,
        "events": [
            {
                "id": e.id,
                "event_id": e.event_id,
                "type": e.type,
                "status": e.status,
                "error": e.error,
                "workspace": name,
                "received_at": e.received_at,
                "processed_at": e.processed_at,
            }
            for e, name in rows
        ],
    }


# ---- Multipliers --------------------------------------------------------------------------------


@router.get("/multipliers")
async def list_multipliers(_: SystemAdmin, db: Db):
    rows = (await db.execute(select(ModelMultiplier).order_by(ModelMultiplier.model, ModelMultiplier.operation))).scalars()
    return [
        {
            "id": m.id,
            "model": m.model,
            "operation": m.operation,
            "input_multiplier": m.input_multiplier,
            "output_multiplier": m.output_multiplier,
            "input_cost_per_m": m.input_cost_per_m,
            "output_cost_per_m": m.output_cost_per_m,
        }
        for m in rows
    ]


@router.put("/multipliers")
async def upsert_multiplier(body: MultiplierIn, admin: SystemAdmin, db: Db):
    m = (
        await db.execute(select(ModelMultiplier).where(ModelMultiplier.model == body.model, ModelMultiplier.operation == body.operation))
    ).scalar_one_or_none()
    if m is None:
        m = ModelMultiplier(model=body.model, operation=body.operation)
        db.add(m)
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    audit(
        db,
        "admin.multiplier_set",
        actor_id=admin.id,
        target_type="multiplier",
        target_id=f"{body.model}:{body.operation}",
        **body.model_dump(),
    )
    await db.commit()
    return {"id": m.id}


@router.delete("/multipliers/{multiplier_id}", status_code=204)
async def delete_multiplier(multiplier_id: uuid.UUID, admin: SystemAdmin, db: Db):
    m = await db.get(ModelMultiplier, multiplier_id)
    if m is None:
        raise not_found("Multiplier")
    await db.delete(m)
    audit(db, "admin.multiplier_deleted", actor_id=admin.id, target_type="multiplier", target_id=f"{m.model}:{m.operation}")
    await db.commit()


# ---- System settings ----------------------------------------------------------------------------

SETTING_DEFAULTS = {"audit_retention_days": 365}


@router.get("/settings")
async def get_settings_(_: SystemAdmin, db: Db):
    retention = await db.get(SystemSetting, "audit_retention_days")
    return {
        "no_plan_limits": await default_limits(db),
        "audit_retention_days": retention.value if retention else SETTING_DEFAULTS["audit_retention_days"],
    }


@router.put("/settings")
async def put_settings(body: dict, admin: SystemAdmin, db: Db):
    if "no_plan_limits" in body:
        limits = _clean_limits(body["no_plan_limits"])
        await db.merge(SystemSetting(key="no_plan_limits", value=limits, updated_at=utcnow()))
    if "audit_retention_days" in body:
        days = int(body["audit_retention_days"])
        if days < 30:
            raise ApiError(422, "retention_too_short", "Keep audit logs for at least 30 days.")
        await db.merge(SystemSetting(key="audit_retention_days", value=days, updated_at=utcnow()))
    audit(db, "admin.settings_updated", actor_id=admin.id, target_type="settings", keys=list(body))
    await db.commit()
    return await get_settings_(admin, db)


# ---- Audit and reports --------------------------------------------------------------------------


@router.get("/audit-logs")
async def audit_logs(_: SystemAdmin, db: Db, workspace_id: uuid.UUID | None = None, action: str = "", limit: int = 100):
    stmt = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_id).order_by(AuditLog.created_at.desc())
    if workspace_id:
        stmt = stmt.where(AuditLog.workspace_id == workspace_id)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    rows = (await db.execute(stmt.limit(min(limit, 500)))).all()
    return [
        {
            "id": a.id,
            "action": a.action,
            "actor": email,
            "workspace_id": a.workspace_id,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "data": a.data,
            "ip": a.ip,
            "created_at": a.created_at,
        }
        for a, email in rows
    ]


@router.get("/reports/usage")
async def usage_report(_: SystemAdmin, db: Db, days: int = 30):
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    rows = (
        await db.execute(
            select(
                Workspace.id,
                Workspace.name,
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.weighted_tokens),
                func.sum(UsageEvent.provider_cost_usd),
            )
            .join(Workspace, Workspace.id == UsageEvent.workspace_id)
            .where(UsageEvent.created_at >= since)
            .group_by(Workspace.id, Workspace.name)
            .order_by(func.sum(UsageEvent.provider_cost_usd).desc())
        )
    ).all()
    return [
        {
            "workspace_id": i,
            "workspace": n,
            "input_tokens": int(a or 0),
            "output_tokens": int(b or 0),
            "weighted": int(w or 0),
            "provider_cost_usd": round(float(c or 0), 4),
        }
        for i, n, a, b, w, c in rows
    ]


# ---- Jobs (also run on a schedule by the worker) ------------------------------------------------


@router.post("/jobs/renewals")
async def run_renewals(admin: SystemAdmin, db: Db):
    return await renew_due(db)


@router.post("/jobs/release-stale-reservations")
async def run_release(admin: SystemAdmin, db: Db):
    return {"released": await quota.release_stale(db)}


@router.post("/jobs/purge-audit")
async def run_purge(admin: SystemAdmin, db: Db):
    return {"deleted": await purge_audit(db)}


async def purge_audit(db) -> int:
    row = await db.get(SystemSetting, "audit_retention_days")
    days = int(row.value) if row else SETTING_DEFAULTS["audit_retention_days"]
    res = await db.execute(delete(AuditLog).where(AuditLog.created_at < utcnow() - timedelta(days=days)))
    await db.commit()
    return res.rowcount or 0
