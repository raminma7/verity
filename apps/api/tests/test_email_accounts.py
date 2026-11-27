"""Connecting mailboxes: sign-in checks, the plan limit under parallel connects, encrypted credentials, audit."""

import asyncio

import pytest
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import decrypt_secret
from app.models import AuditLog, EmailAccount
from app.services.mail.transport import FAKE_BAD_PASSWORD, FAKE_UNREACHABLE, TransportError, resolve_checked
from tests.conftest import signup
from tests.mail import SUPPORT, account_body, connect, owner


async def test_connect_stores_the_password_encrypted_and_never_returns_it(client):
    await owner(client)
    a = await connect(client)
    assert a["address"] == SUPPORT and a["status"] == "active" and a["has_password"] is True
    assert "password" not in a and "secret_enc" not in a
    listing = (await client.get("/api/email-accounts")).json()
    assert listing["used"] == 1 and listing["limit"] == 3 and listing["default_account_id"] == a["id"]
    assert "app-password-123" not in str(listing)
    async with SessionLocal() as db:
        raw = (await db.execute(text("SELECT secret_enc FROM email_accounts"))).scalar_one()
        assert "app-password-123" not in raw and decrypt_secret(raw) == "app-password-123"
        logs = (await db.execute(select(AuditLog).where(AuditLog.action.like("email_account.%")))).scalars().all()
    assert [entry.action for entry in logs] == ["email_account.connected"]
    assert "app-password-123" not in str([entry.data for entry in logs])


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"password": FAKE_BAD_PASSWORD}, "email_auth_failed"),
        ({"imap_security": "none"}, "email_tls_required"),
        ({"smtp_security": "none"}, "email_tls_required"),
        ({"imap_host": FAKE_UNREACHABLE}, "email_connect_failed"),
    ],
)
async def test_connect_refuses_what_cant_sign_in(client, over, code):
    await owner(client)
    r = await client.post("/api/email-accounts", json=account_body(**over))
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert (await client.get("/api/email-accounts")).json()["used"] == 0


def test_private_and_metadata_hosts_are_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "crawl_allow_private", False)
    for host in ("127.0.0.1", "169.254.169.254", "10.0.0.5", "localhost"):
        with pytest.raises(TransportError) as e:
            resolve_checked(host, 993)
        assert e.value.code == "email_host_blocked"
    with pytest.raises(TransportError) as e:
        resolve_checked("bad host;rm -rf", 993)
    assert e.value.code == "email_connect_failed"


async def test_the_same_mailbox_twice_is_refused(client):
    await owner(client)
    await connect(client)
    r = await client.post("/api/email-accounts", json=account_body(SUPPORT.upper()))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_account_exists"


async def test_plan_limit_holds_under_parallel_connects(client):
    await owner(client)
    results = await asyncio.gather(*[client.post("/api/email-accounts", json=account_body(f"box{i}@acmehelp.com")) for i in range(6)])
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 201, 201, 402, 402, 402]
    assert all(
        r.json()["detail"]["code"] == "limit_reached" and r.json()["detail"]["limit"] == "email_accounts"
        for r in results
        if r.status_code == 402
    )
    assert (await client.get("/api/email-accounts")).json()["used"] == 3


async def test_disconnect_erases_the_password_and_frees_the_slot(client):
    await owner(client)
    boxes = [await connect(client, f"box{i}@acmehelp.com") for i in range(3)]
    r = await client.post("/api/email-accounts", json=account_body("box9@acmehelp.com"))
    assert r.status_code == 402
    r = await client.delete(f"/api/email-accounts/{boxes[0]['id']}")
    assert r.status_code == 200 and r.json()["status"] == "disconnected" and r.json()["has_password"] is False
    async with SessionLocal() as db:
        a = await db.get(EmailAccount, boxes[0]["id"])
        assert a.secret_enc is None
        actions = (await db.execute(select(AuditLog.action).where(AuditLog.target_id == boxes[0]["id"]))).scalars().all()
    assert "email_account.disconnected" in actions
    # The default sending account moved off the disconnected mailbox.
    assert (await client.get("/api/email-settings")).json()["default_account_id"] is None
    await connect(client, "box9@acmehelp.com")
    # Reconnecting the disconnected address works too, as a new account.
    r = await client.delete(f"/api/email-accounts/{boxes[1]['id']}")
    await connect(client, "box0@acmehelp.com")


async def test_changing_credentials_signs_in_again_and_is_audited_without_the_value(client):
    await owner(client)
    a = await connect(client)
    r = await client.patch(f"/api/email-accounts/{a['id']}", json={"password": FAKE_BAD_PASSWORD})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "email_auth_failed"
    async with SessionLocal() as db:
        assert decrypt_secret((await db.get(EmailAccount, a["id"])).secret_enc) == "app-password-123"
    r = await client.patch(f"/api/email-accounts/{a['id']}", json={"password": "new-app-password", "name": "Help desk"})
    assert r.status_code == 200 and r.json()["name"] == "Help desk"
    async with SessionLocal() as db:
        assert decrypt_secret((await db.get(EmailAccount, a["id"])).secret_enc) == "new-app-password"
        log = (await db.execute(select(AuditLog).where(AuditLog.action == "email_account.updated"))).scalars().all()
    assert log[-1].data["credentials_changed"] is True and "new-app-password" not in str(log[-1].data)


async def test_pause_and_resume(client):
    await owner(client)
    a = await connect(client)
    r = await client.patch(f"/api/email-accounts/{a['id']}", json={"status": "paused"})
    assert r.json()["status"] == "paused"
    r = await client.post(f"/api/email-accounts/{a['id']}/fetch")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_account_inactive"
    r = await client.patch(f"/api/email-accounts/{a['id']}", json={"status": "active"})
    assert r.json()["status"] == "active"


async def test_email_settings_validate_templates_and_lists(client):
    await owner(client)
    s = (await client.get("/api/email-settings")).json()
    assert s["auto_reply"]["mode"] == "template" and "{ticket_number}" in s["auto_reply"]["body"] and s["model"] == "claude-haiku-5-5"
    r = await client.patch("/api/email-settings", json={"auto_reply": {"mode": "template", "subject": "Thanks", "body": "We got it."}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "template_missing_ticket_number"
    r = await client.patch("/api/email-settings", json={"block_senders": ["not an address"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_sender"
    r = await client.patch("/api/email-settings", json={"model": "gpt-4"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_model"
    r = await client.patch(
        "/api/email-settings",
        json={"auto_reply": {"mode": "ai"}, "block_senders": ["Spam@Bad.test", "@worse.test"], "form_confirmation": False},
    )
    assert r.status_code == 200
    s = r.json()
    assert s["auto_reply"]["mode"] == "ai" and s["block_senders"] == ["spam@bad.test", "@worse.test"] and s["form_confirmation"] is False


async def test_without_a_subscription_mailboxes_are_gated(client):
    await signup(client, "nosub@example.com")
    r = await client.post("/api/email-accounts", json=account_body())
    assert r.status_code == 402 and r.json()["detail"]["code"] == "subscription_required"
