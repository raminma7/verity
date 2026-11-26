from datetime import timedelta

import pyotp
from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update

from app.core import ratelimit
from app.core.config import get_settings
from app.core.deps import CurrentUser, Db, client_ip, get_session_user
from app.core.errors import ApiError
from app.core.security import (
    decrypt_secret,
    hash_password,
    hash_token,
    needs_rehash,
    new_token,
    validate_password_strength,
    verify_password,
)
from app.models import EmailToken, Membership, Session, User
from app.models.base import utcnow
from app.schemas import CodeIn, EmailIn, LoginIn, RegisterIn, ResetIn, TokenIn, UserOut
from app.services import email as mail
from app.services.audit import audit
from app.services.workspaces import create_workspace, default_workspace_name

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_SENT = {"status": "sent", "message": "If that address can receive email from us, a message is on its way."}


def _password_or_400(password: str) -> None:
    problem = validate_password_strength(password)
    if problem:
        raise ApiError(422, "weak_password", problem, field="password")


async def _issue_email_token(db, user: User, purpose: str) -> str:
    s = get_settings()
    await db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    token = new_token()
    ttl = timedelta(hours=s.verify_token_ttl_hours) if purpose == "verify" else timedelta(minutes=s.reset_token_ttl_minutes)
    db.add(EmailToken(user_id=user.id, purpose=purpose, token_hash=hash_token(token), expires_at=utcnow() + ttl))
    return token


async def _consume_email_token(db, token: str, purpose: str) -> EmailToken:
    row = (
        await db.execute(
            select(EmailToken).where(EmailToken.token_hash == hash_token(token), EmailToken.purpose == purpose).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.used_at is not None:
        raise ApiError(400, "invalid_token", "This link is invalid or has already been used.")
    if row.expires_at <= utcnow():
        raise ApiError(400, "expired_token", "This link has expired. Request a new one.")
    row.used_at = utcnow()
    return row


async def _mark_verified(db, user: User, ip: str | None) -> None:
    """Verifies the address and, on first verification, creates the user's workspace as its Owner."""
    if user.is_verified:
        return
    user.email_verified_at = utcnow()
    has_ws = (await db.execute(select(Membership.id).where(Membership.user_id == user.id))).first()
    if not has_ws:
        ws = await create_workspace(db, user, default_workspace_name(user.full_name))
        audit(db, "workspace.created", actor_id=user.id, workspace_id=ws.id, target_type="workspace", target_id=ws.id)
    audit(db, "user.email_verified", actor_id=user.id, target_type="user", target_id=user.id, ip=ip)


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie,
        token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        secure=s.session_cookie_secure,
        samesite="lax",
        path="/",
    )


async def _start_session(db, request: Request, response: Response, user: User, *, mfa_pending: bool = False) -> None:
    token = new_token()
    db.add(
        Session(
            user_id=user.id,
            token_hash=hash_token(token),
            mfa_pending=mfa_pending,
            ip=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:400],
            expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
        )
    )
    _set_cookie(response, token)


@router.post("/register", status_code=202)
async def register(body: RegisterIn, request: Request, db: Db):
    s = get_settings()
    await ratelimit.hit(f"register:{client_ip(request)}", s.rl_register)
    _password_or_400(body.password)
    email = body.email.lower()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # Same answer whether or not the address is registered, so the endpoint can't be used to find accounts.
    if existing is not None:
        if not existing.is_verified:
            token = await _issue_email_token(db, existing, "verify")
            await db.commit()
            await mail.send_verification(existing.email, existing.full_name, token)
        return GENERIC_SENT | {"email": email}
    user = User(full_name=body.full_name, email=email, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    token = await _issue_email_token(db, user, "verify")
    audit(db, "user.registered", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
    await db.commit()
    await mail.send_verification(user.email, user.full_name, token)
    return GENERIC_SENT | {"email": email}


@router.post("/resend-verification", status_code=202)
async def resend_verification(body: EmailIn, request: Request, db: Db):
    email = body.email.lower()
    await ratelimit.hit(f"email:ip:{client_ip(request)}", get_settings().rl_email)
    await ratelimit.hit(f"email:verify:{email}", get_settings().rl_email)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None and not user.is_verified:
        token = await _issue_email_token(db, user, "verify")
        await db.commit()
        await mail.send_verification(user.email, user.full_name, token)
    return GENERIC_SENT


@router.post("/verify-email", response_model=UserOut)
async def verify_email(body: TokenIn, request: Request, response: Response, db: Db):
    row = await _consume_email_token(db, body.token, "verify")
    user = await db.get(User, row.user_id, with_for_update=True)
    if user.status != "active":
        raise ApiError(403, "account_suspended", "This account is suspended. Contact support.")
    await _mark_verified(db, user, client_ip(request))
    await _start_session(db, request, response, user, mfa_pending=user.totp_enabled)
    await db.commit()
    return user


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: Db):
    s = get_settings()
    email = body.email.lower()
    await ratelimit.hit(f"login:ip:{client_ip(request)}", s.rl_login)
    if (wait := await ratelimit.login_locked(email)) > 0:
        raise ApiError(429, "locked", "Too many failed attempts. Try again later or reset your password.", retry_after=wait)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None):
        await ratelimit.record_login_failure(email)
        if user:
            audit(db, "user.login_failed", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
            await db.commit()
        raise ApiError(401, "invalid_credentials", "That email and password don't match.")
    if user.status != "active":
        raise ApiError(403, "account_suspended", "This account is suspended. Contact support.")
    await ratelimit.clear_login_failures(email)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    await _start_session(db, request, response, user, mfa_pending=user.totp_enabled)
    audit(db, "user.login", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
    await db.commit()
    return {"user": UserOut.model_validate(user), "mfa_required": user.totp_enabled}


@router.post("/2fa")
async def login_second_factor(body: CodeIn, request: Request, response: Response, db: Db):
    sess, user = await get_session_user(request, db)
    if not sess.mfa_pending:
        return {"user": UserOut.model_validate(user), "mfa_required": False}
    await ratelimit.hit(f"2fa:{user.id}", "5/300")
    code = body.code.replace(" ", "").replace("-", "").lower()
    ok = False
    if user.totp_secret_enc and code.isdigit():
        ok = pyotp.TOTP(decrypt_secret(user.totp_secret_enc)).verify(code, valid_window=1)
    if not ok and user.recovery_codes:
        h = hash_token(code)
        if h in user.recovery_codes:
            user.recovery_codes = [c for c in user.recovery_codes if c != h]
            ok = True
            audit(db, "user.recovery_code_used", actor_id=user.id, target_type="user", target_id=user.id)
    if not ok:
        raise ApiError(401, "invalid_code", "That code didn't work. Check your authenticator app and try again.")
    # Rotate the token when the session gains full privileges.
    sess.revoked_at = utcnow()
    await _start_session(db, request, response, user)
    await db.commit()
    return {"user": UserOut.model_validate(user), "mfa_required": False}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: Db):
    token = request.cookies.get(get_settings().session_cookie)
    if token:
        await db.execute(update(Session).where(Session.token_hash == hash_token(token)).values(revoked_at=utcnow()))
        await db.commit()
    response.delete_cookie(get_settings().session_cookie, path="/")


@router.post("/forgot-password", status_code=202)
async def forgot_password(body: EmailIn, request: Request, db: Db):
    email = body.email.lower()
    await ratelimit.hit(f"email:ip:{client_ip(request)}", get_settings().rl_email)
    await ratelimit.hit(f"email:reset:{email}", get_settings().rl_email)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None and user.status == "active":
        token = await _issue_email_token(db, user, "reset")
        await db.commit()
        await mail.send_password_reset(user.email, user.full_name, token)
    return GENERIC_SENT


@router.post("/reset-password")
async def reset_password(body: ResetIn, request: Request, db: Db):
    _password_or_400(body.password)
    row = await _consume_email_token(db, body.token, "reset")
    user = await db.get(User, row.user_id)
    user.password_hash = hash_password(body.password)
    # Following a reset link proves control of the inbox.
    await _mark_verified(db, user, client_ip(request))
    await db.execute(update(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit(db, "user.password_reset", actor_id=user.id, target_type="user", target_id=user.id, ip=client_ip(request))
    await db.commit()
    await ratelimit.clear_login_failures(user.email)
    return {"status": "ok"}


@router.get("/session")
async def session_state(user: CurrentUser):
    return {"user": UserOut.model_validate(user)}
