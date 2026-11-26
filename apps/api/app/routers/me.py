import uuid
import zoneinfo

import pyotp
from fastapi import APIRouter, Request
from sqlalchemy import select, update

from app.core import ratelimit
from app.core.config import get_settings
from app.core.deps import CurrentUser, Db, VerifiedUser, client_ip
from app.core.errors import ApiError, not_found
from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    hash_token,
    new_token,
    validate_password_strength,
    verify_password,
)
from app.models import Membership, Notification, Session, Workspace
from app.models.base import utcnow
from app.schemas import CodeIn, Disable2faIn, PasswordChangeIn, ProfileIn, UserOut
from app.services.audit import audit
from app.services.subscriptions import get_subscription, is_active

router = APIRouter(prefix="/me", tags=["me"])

LOCALES = {"en", "de", "fr", "es", "fa", "ar", "tr"}


@router.get("")
async def me(user: CurrentUser, db: Db):
    rows = (
        await db.execute(
            select(Membership, Workspace)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(Membership.user_id == user.id)
            .order_by(Workspace.created_at)
        )
    ).all()
    workspaces = []
    for m, ws in rows:
        sub = await get_subscription(db, ws.id)
        workspaces.append(
            {
                "id": ws.id,
                "name": ws.name,
                "slug": ws.slug,
                "status": ws.status,
                "role": {"id": m.role.id, "key": m.role.key, "name": m.role.name},
                "permissions": m.role.permissions,
                "subscribed": is_active(sub),
                "plan_name": sub.terms["plan_name"] if is_active(sub) else None,
            }
        )
    return {"user": UserOut.model_validate(user), "workspaces": workspaces}


@router.patch("", response_model=UserOut)
async def update_profile(body: ProfileIn, user: CurrentUser, db: Db):
    if body.full_name is not None:
        user.full_name = " ".join(body.full_name.split())
    if body.locale is not None:
        if body.locale not in LOCALES:
            raise ApiError(422, "invalid_locale", "Unsupported language.", field="locale")
        user.locale = body.locale
    if body.timezone is not None:
        if body.timezone not in zoneinfo.available_timezones():
            raise ApiError(422, "invalid_timezone", "Unknown timezone.", field="timezone")
        user.timezone = body.timezone
    if body.notification_prefs is not None:
        user.notification_prefs = {**user.notification_prefs, **body.notification_prefs}
    await db.commit()
    return user


@router.post("/switch-workspace/{workspace_id}")
async def switch_workspace(workspace_id: uuid.UUID, user: VerifiedUser, db: Db):
    m = (
        await db.execute(select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id))
    ).scalar_one_or_none()
    if m is None:
        raise not_found("Workspace")
    user.last_workspace_id = workspace_id
    await db.commit()
    return {"status": "ok"}


@router.post("/password")
async def change_password(body: PasswordChangeIn, request: Request, user: CurrentUser, db: Db):
    await ratelimit.hit(f"pwchange:{user.id}", "5/600")
    if not verify_password(body.current_password, user.password_hash):
        raise ApiError(400, "wrong_password", "Your current password is incorrect.", field="current_password")
    problem = validate_password_strength(body.new_password)
    if problem:
        raise ApiError(422, "weak_password", problem, field="new_password")
    user.password_hash = hash_password(body.new_password)
    # Sign out everywhere else; keep this session.
    current = hash_token(request.cookies.get(get_settings().session_cookie, ""))
    await db.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None), Session.token_hash != current)
        .values(revoked_at=utcnow())
    )
    audit(db, "user.password_changed", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
    await db.commit()
    return {"status": "ok"}


@router.get("/sessions")
async def list_sessions(request: Request, user: CurrentUser, db: Db):
    current = hash_token(request.cookies.get(get_settings().session_cookie, ""))
    rows = (
        await db.execute(
            select(Session)
            .where(Session.user_id == user.id, Session.revoked_at.is_(None), Session.expires_at > utcnow())
            .order_by(Session.last_seen_at.desc())
        )
    ).scalars()
    return [
        {
            "id": s.id,
            "ip": s.ip,
            "user_agent": s.user_agent,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "current": s.token_hash == current,
        }
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: uuid.UUID, user: CurrentUser, db: Db):
    await db.execute(update(Session).where(Session.id == session_id, Session.user_id == user.id).values(revoked_at=utcnow()))
    await db.commit()


# ---- Two-factor authentication -------------------------------------------------------------


@router.post("/2fa/setup")
async def setup_2fa(user: VerifiedUser, db: Db):
    if user.totp_enabled:
        raise ApiError(409, "already_enabled", "Two-factor authentication is already on.")
    secret = pyotp.random_base32()
    user.totp_secret_enc = encrypt_secret(secret)
    await db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Verity")
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/2fa/enable")
async def enable_2fa(body: CodeIn, request: Request, user: VerifiedUser, db: Db):
    await ratelimit.hit(f"2fa:{user.id}", "5/300")
    if user.totp_enabled or not user.totp_secret_enc:
        raise ApiError(409, "not_in_setup", "Start two-factor setup first.")
    if not pyotp.TOTP(decrypt_secret(user.totp_secret_enc)).verify(body.code.strip(), valid_window=1):
        raise ApiError(400, "invalid_code", "That code didn't match. Check the time on your device and try again.")
    codes = [new_token(6).replace("-", "a").replace("_", "b").lower()[:10] for _ in range(10)]
    user.recovery_codes = [hash_token(c) for c in codes]
    user.totp_enabled = True
    audit(db, "user.2fa_enabled", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
    await db.commit()
    return {"recovery_codes": codes}


@router.post("/2fa/disable")
async def disable_2fa(body: Disable2faIn, request: Request, user: VerifiedUser, db: Db):
    """Turning 2FA off needs both the password and a current code."""
    await ratelimit.hit(f"2fa:{user.id}", "5/300")
    if not user.totp_enabled:
        return {"status": "ok"}
    if not verify_password(body.password, user.password_hash):
        raise ApiError(400, "wrong_password", "Your password is incorrect.", field="password")
    if not pyotp.TOTP(decrypt_secret(user.totp_secret_enc)).verify(body.code.strip(), valid_window=1):
        raise ApiError(400, "invalid_code", "That code didn't match.", field="code")
    user.totp_enabled = False
    user.totp_secret_enc = None
    user.recovery_codes = None
    audit(db, "user.2fa_disabled", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
    await db.commit()
    return {"status": "ok"}


# ---- Notifications --------------------------------------------------------------------------


@router.get("/notifications")
async def notifications(user: VerifiedUser, db: Db):
    ws_ids = select(Membership.workspace_id).where(Membership.user_id == user.id)
    rows = (
        await db.execute(
            select(Notification)
            .where((Notification.user_id == user.id) | (Notification.workspace_id.in_(ws_ids)))
            .order_by(Notification.created_at.desc())
            .limit(30)
        )
    ).scalars()
    return [
        {"id": n.id, "workspace_id": n.workspace_id, "kind": n.kind, "title": n.title, "body": n.body, "created_at": n.created_at}
        for n in rows
    ]
