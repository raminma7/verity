from datetime import timedelta

import pyotp
from sqlalchemy import select, update

from app.models import EmailToken, Membership, User, Workspace
from app.models.base import utcnow
from app.services import email as mail
from tests.conftest import PASSWORD, last_token, login, new_client, signup


async def test_register_sends_verification_and_blocks_dashboard(client):
    r = await client.post("/api/auth/register", json={"full_name": "Ada Lovelace", "email": "Ada@Example.com", "password": PASSWORD})
    assert r.status_code == 202
    assert mail.OUTBOX[-1].to == "ada@example.com"
    assert "verify-email?token=" in mail.OUTBOX[-1].links[0]

    r = await login(client, "ada@example.com")
    assert r.status_code == 200
    assert r.json()["user"]["email_verified"] is False
    # Workspace routes refuse unverified accounts.
    r = await client.get("/api/workspace")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "email_not_verified"


async def test_verify_creates_workspace_with_owner_role(client, db):
    me = await signup(client, "owner@example.com", "Grace Hopper")
    assert me["user"]["email_verified"] is True
    assert len(me["workspaces"]) == 1
    ws = me["workspaces"][0]
    assert ws["name"] == "Grace's workspace"
    assert ws["role"]["key"] == "owner"
    assert ws["subscribed"] is False
    user = (await db.execute(select(User).where(User.email == "owner@example.com"))).scalar_one()
    workspace = await db.get(Workspace, ws["id"])
    assert workspace.owner_id == user.id


async def test_verification_link_is_single_use(client):
    await client.post("/api/auth/register", json={"full_name": "A", "email": "a@example.com", "password": PASSWORD})
    token = last_token("a@example.com", "/verify-email")
    assert (await client.post("/api/auth/verify-email", json={"token": token})).status_code == 200
    r = await client.post("/api/auth/verify-email", json={"token": token})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_token"


async def test_expired_verification_link(client, db):
    await client.post("/api/auth/register", json={"full_name": "A", "email": "late@example.com", "password": PASSWORD})
    token = last_token("late@example.com", "/verify-email")
    await db.execute(update(EmailToken).values(expires_at=utcnow() - timedelta(minutes=1)))
    await db.commit()
    r = await client.post("/api/auth/verify-email", json={"token": token})
    assert r.json()["detail"]["code"] == "expired_token"


async def test_resend_invalidates_previous_link(client):
    await client.post("/api/auth/register", json={"full_name": "A", "email": "re@example.com", "password": PASSWORD})
    first = last_token("re@example.com", "/verify-email")
    assert (await client.post("/api/auth/resend-verification", json={"email": "re@example.com"})).status_code == 202
    second = last_token("re@example.com", "/verify-email")
    assert first != second
    assert (await client.post("/api/auth/verify-email", json={"token": first})).status_code == 400
    assert (await client.post("/api/auth/verify-email", json={"token": second})).status_code == 200


async def test_register_does_not_reveal_existing_accounts(client):
    await signup(client, "taken@example.com")
    mail.OUTBOX.clear()
    async with new_client() as other:
        r = await other.post("/api/auth/register", json={"full_name": "B", "email": "taken@example.com", "password": PASSWORD})
    assert r.status_code == 202
    assert mail.OUTBOX == []  # verified account: no email, same answer


async def test_weak_password_rejected(client):
    r = await client.post("/api/auth/register", json={"full_name": "A", "email": "w@example.com", "password": "password"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "weak_password"


async def test_login_logout(client):
    await signup(client, "l@example.com")
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/me")).status_code == 401
    r = await login(client, "l@example.com", "wrong-Password-1")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"
    assert (await login(client, "L@example.com")).status_code == 200
    assert (await client.get("/api/me")).status_code == 200


async def test_lockout_after_repeated_failures(client):
    await signup(client, "lock@example.com")
    for _ in range(5):
        await login(client, "lock@example.com", "Wrong-password-1")
    r = await login(client, "lock@example.com")
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "locked"


async def test_login_rate_limit_per_ip(client):
    for _ in range(10):
        await login(client, "nobody@example.com", "Whatever-123")
    r = await login(client, "someone-else@example.com", "Whatever-123")
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"


async def test_password_reset_revokes_sessions(client):
    await signup(client, "reset@example.com")
    assert (await client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})).status_code == 202
    token = last_token("reset@example.com", "/reset-password")
    r = await client.post("/api/auth/reset-password", json={"token": token, "password": "Brand-new-pass-9"})
    assert r.status_code == 200
    assert (await client.get("/api/me")).status_code == 401  # old session revoked
    assert (await login(client, "reset@example.com")).status_code == 401
    assert (await login(client, "reset@example.com", "Brand-new-pass-9")).status_code == 200
    # single use
    r = await client.post("/api/auth/reset-password", json={"token": token, "password": "Another-pass-9"})
    assert r.status_code == 400


async def test_forgot_password_unknown_email_is_silent(client):
    r = await client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    assert r.status_code == 202
    assert mail.OUTBOX == []


async def test_change_password_keeps_current_session_only(client):
    await signup(client, "cp@example.com")
    async with new_client() as other:
        await login(other, "cp@example.com")
        r = await client.post("/api/me/password", json={"current_password": PASSWORD, "new_password": "Changed-pass-77"})
        assert r.status_code == 200
        assert (await client.get("/api/me")).status_code == 200
        assert (await other.get("/api/me")).status_code == 401


async def test_two_factor_flow(client):
    await signup(client, "mfa@example.com")
    setup = (await client.post("/api/me/2fa/setup")).json()
    totp = pyotp.TOTP(setup["secret"])
    r = await client.post("/api/me/2fa/enable", json={"code": totp.now()})
    assert r.status_code == 200
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10

    await client.post("/api/auth/logout")
    r = await login(client, "mfa@example.com")
    assert r.json()["mfa_required"] is True
    r = await client.get("/api/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "mfa_required"
    assert (await client.post("/api/auth/2fa", json={"code": "000000"})).status_code == 401
    assert (await client.post("/api/auth/2fa", json={"code": totp.now()})).status_code == 200
    assert (await client.get("/api/me")).status_code == 200

    # Recovery codes work once.
    await client.post("/api/auth/logout")
    await login(client, "mfa@example.com")
    assert (await client.post("/api/auth/2fa", json={"code": codes[0]})).status_code == 200
    await client.post("/api/auth/logout")
    await login(client, "mfa@example.com")
    assert (await client.post("/api/auth/2fa", json={"code": codes[0]})).status_code == 401


async def test_suspended_user_cannot_sign_in(client, db):
    await signup(client, "sus@example.com")
    await db.execute(update(User).where(User.email == "sus@example.com").values(status="suspended"))
    await db.commit()
    assert (await client.get("/api/me")).status_code == 403
    r = await login(client, "sus@example.com")
    assert r.json()["detail"]["code"] == "account_suspended"


async def test_cross_site_write_refused(client):
    r = await client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


async def test_profile_update(client, db):
    await signup(client, "p@example.com")
    r = await client.patch(
        "/api/me", json={"full_name": "Ada  King", "timezone": "Asia/Tehran", "locale": "fa", "notification_prefs": {"usage_alerts": False}}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["full_name"] == "Ada King"
    assert body["timezone"] == "Asia/Tehran"
    assert body["notification_prefs"]["usage_alerts"] is False
    assert body["notification_prefs"]["billing"] is True
    r = await client.patch("/api/me", json={"timezone": "Mars/Base"})
    assert r.status_code == 422


async def test_tokens_stored_hashed(client, db):
    await client.post("/api/auth/register", json={"full_name": "A", "email": "h@example.com", "password": PASSWORD})
    token = last_token("h@example.com", "/verify-email")
    rows = (await db.execute(select(EmailToken.token_hash))).scalars().all()
    assert token not in rows
    user = (await db.execute(select(User).where(User.email == "h@example.com"))).scalar_one()
    assert user.password_hash.startswith("$argon2")
    assert not (await db.execute(select(Membership).where(Membership.user_id == user.id))).first()
