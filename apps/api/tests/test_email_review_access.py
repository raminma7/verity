"""The Spam section, every Phase 4 endpoint for every role, the subscription gate and tenant isolation."""

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import AuditLog, EmailAttachment
from tests.conftest import new_client, signup, subscribe
from tests.mail import account_body, connect, deliver, mail, owner, sent, tickets
from tests.test_phase3_access import ROLES, team

PDF = b"%PDF-1.4\n" + b"y" * 50
SPAM_HEADERS = {"X-Spam-Flag": "YES"}


async def spam_message(client, a, subject="Totally legit", sender="maybe@example.net", **kw):
    await deliver(client, a["id"], mail(subject, "Please read", sender=sender, headers=SPAM_HEADERS, **kw)[0])
    items = (await client.get("/api/email/spam")).json()["items"]
    return next(m for m in items if m["subject"] == subject)


async def test_reviewers_mark_not_spam_which_opens_the_ticket_once(client):
    await owner(client)
    a = await connect(client)
    m = await spam_message(client, a, attachments=[("quote.pdf", "application/pdf", PDF)])
    listing = (await client.get("/api/email/spam")).json()
    assert listing["counts"] == {"spam": 1, "promotion": 0, "rate_limited": 0}
    assert listing["items"][0]["verdict"]["rules"] == ["spam_flag_header"]
    detail = (await client.get(f"/api/email/messages/{m['id']}")).json()
    assert detail["text"].startswith("Please read") and detail["attachments"][0]["filename"] == "quote.pdf"
    r = await client.post(f"/api/email/messages/{m['id']}/not-spam", json={"allow_sender": True})
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]
    assert ticket["number"] == 1 and r.json()["message"]["status"] == "ticketed"
    again = await client.post(f"/api/email/messages/{m['id']}/not-spam", json={})
    assert again.json()["ticket"]["id"] == ticket["id"]
    assert len(await tickets(client)) == 1 and len(await sent()) == 1  # the automatic reply went out once
    assert "maybe@example.net" in (await client.get("/api/email-settings")).json()["allow_senders"]
    assert (await client.get("/api/email/spam")).json()["items"] == []
    # The sender is now allowed: their next message is a ticket even with a spam flag.
    await deliver(client, a["id"], mail("Second", "Hi", sender="maybe@example.net", headers=SPAM_HEADERS)[0])
    assert len(await tickets(client)) == 2
    async with SessionLocal() as db:
        assert (await db.execute(select(AuditLog.action).where(AuditLog.action == "email.not_spam"))).scalars().all() == ["email.not_spam"]


async def test_delete_wipes_the_message_and_its_attachments(client):
    await owner(client)
    a = await connect(client)
    m1 = await spam_message(client, a, "One", attachments=[("a.pdf", "application/pdf", PDF)])
    m2 = await spam_message(client, a, "Two")
    m3 = await spam_message(client, a, "Three")
    assert (await client.delete(f"/api/email/messages/{m1['id']}")).status_code == 204
    r = await client.get(f"/api/email/messages/{m1['id']}")
    assert r.json()["status"] == "deleted" and r.json()["text"] == "" and r.json()["attachments"] == []
    async with SessionLocal() as db:
        assert (await db.execute(select(EmailAttachment))).scalars().all() == []
    r = await client.post("/api/email/spam/delete", json={"ids": [m2["id"], m3["id"]]})
    assert r.json() == {"deleted": 2}
    assert (await client.get("/api/email/spam")).json()["items"] == []
    # A message that became a ticket can't be deleted from here.
    await deliver(client, a["id"], mail("Real", "Real request")[0])
    real = (await client.get(f"/api/tickets/{(await tickets(client))[0]['id']}")).json()["emails"][0]
    r = await client.delete(f"/api/email/messages/{real['id']}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_already_processed"
    r = await client.post(f"/api/email/messages/{real['id']}/not-spam", json={})
    assert r.status_code == 200 and r.json()["ticket"] is not None  # already a ticket: same answer
    async with SessionLocal() as db:
        actions = (await db.execute(select(AuditLog.action).where(AuditLog.action == "email.deleted"))).scalars().all()
    assert len(actions) == 3


# ---- Roles ------------------------------------------------------------------------------------------

ALLOWED = {
    "integrations.manage": {"owner", "admin"},
    "email.review": {"owner", "admin", "agent"},
    "tickets.read": {"owner", "admin", "agent", "viewer", "custom"},
}


@pytest.fixture
async def setup(client):
    await signup(client, "owner@example.com")
    await subscribe(client, "growth")
    clients = await team(client)
    a = await connect(client)
    m = await spam_message(client, a, attachments=[("a.pdf", "application/pdf", PDF)])
    await deliver(client, a["id"], mail("Help", "Please help", attachments=[("b.pdf", "application/pdf", PDF)])[0])
    [t] = await tickets(client)
    att = (await client.get(f"/api/tickets/{t['id']}")).json()["emails"][0]["attachments"][0]
    yield clients, a, m, t, att
    for name, c in clients.items():
        if name != "owner":
            await c.aclose()


def requests(a, m, att):
    return [
        ("integrations.manage", "GET", "/api/email-accounts", None),
        ("integrations.manage", "GET", f"/api/email-accounts/{a['id']}", None),
        ("integrations.manage", "PATCH", f"/api/email-accounts/{a['id']}", {"name": "Renamed"}),
        ("integrations.manage", "POST", f"/api/email-accounts/{a['id']}/fetch", None),
        ("integrations.manage", "GET", "/api/email-settings", None),
        ("integrations.manage", "PATCH", "/api/email-settings", {"spam_filter": True}),
        ("email.review", "GET", "/api/email/spam", None),
        ("email.review", "GET", f"/api/email/messages/{m['id']}", None),
        ("tickets.read", "GET", f"/api/email-attachments/{att['id']}", None),
    ]


@pytest.mark.parametrize("role", ROLES)
async def test_every_email_endpoint_for_every_role(setup, role):
    clients, a, m, t, att = setup
    c = clients[role]
    for perm, method, path, body in requests(a, m, att):
        r = await c.request(method, path, json=body)
        if role in ALLOWED[perm]:
            assert r.status_code < 400, (role, method, path, r.text)
        else:
            assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden", (role, method, path, r.status_code)
    # Writes last, so the reads above saw the data.
    writes = [
        ("integrations.manage", "POST", "/api/email-accounts", account_body(f"{role}@acmehelp.com")),
        ("email.review", "POST", f"/api/email/messages/{m['id']}/not-spam", {}),
        ("tickets.manage", "POST", f"/api/tickets/{t['id']}/reply", {"text": "Hello from a role test"}),
        ("integrations.manage", "DELETE", f"/api/email-accounts/{a['id']}", None),
    ]
    allowed = {**ALLOWED, "tickets.manage": {"owner", "admin", "agent"}}
    for perm, method, path, body in writes:
        r = await c.request(method, path, json=body)
        if role in allowed[perm]:
            assert r.status_code < 400, (role, method, path, r.text)
        else:
            assert r.status_code == 403, (role, method, path, r.status_code)


async def test_held_mail_attachments_are_for_reviewers_only(setup):
    clients, a, m, t, att = setup
    detail = (await clients["owner"].get(f"/api/email/messages/{m['id']}")).json()
    held = detail["attachments"][0]["id"]
    assert (await clients["agent"].get(f"/api/email-attachments/{held}")).status_code == 200
    assert (await clients["viewer"].get(f"/api/email-attachments/{held}")).status_code == 404


async def test_email_endpoints_need_a_subscription(client):
    await signup(client, "nosub@example.com")
    for method, path in (("GET", "/api/email-accounts"), ("GET", "/api/email/spam"), ("GET", "/api/email-settings")):
        r = await client.request(method, path)
        assert r.status_code == 402 and r.json()["detail"]["code"] == "subscription_required", path


async def test_tenant_isolation(client):
    await owner(client, "alice@example.com")
    a = await connect(client)
    m = await spam_message(client, a)
    await deliver(client, a["id"], mail("Help", "Please help", attachments=[("b.pdf", "application/pdf", PDF)])[0])
    [t] = await tickets(client)
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    att = detail["emails"][0]["attachments"][0]
    out = detail["emails"][-1]
    async with new_client() as bob:
        await owner(bob, "bob@example.com")
        b = await connect(bob, "help@bobco.com")
        for method, path, body in (
            ("GET", f"/api/email-accounts/{a['id']}", None),
            ("PATCH", f"/api/email-accounts/{a['id']}", {"name": "Hacked"}),
            ("POST", f"/api/email-accounts/{a['id']}/fetch", None),
            ("DELETE", f"/api/email-accounts/{a['id']}", None),
            ("GET", f"/api/email/messages/{m['id']}", None),
            ("POST", f"/api/email/messages/{m['id']}/not-spam", {}),
            ("DELETE", f"/api/email/messages/{m['id']}", None),
            ("GET", f"/api/email-attachments/{att['id']}", None),
            ("POST", f"/api/tickets/{t['id']}/emails/{out['id']}/retry", None),
            ("PATCH", "/api/email-settings", {"default_account_id": a["id"]}),
        ):
            r = await bob.request(method, path, json=body)
            assert r.status_code == 404, (method, path, r.status_code, r.text)
        # Bulk delete only ever touches Bob's own rows.
        assert (await bob.post("/api/email/spam/delete", json={"ids": [m["id"]]})).json() == {"deleted": 0}
        assert (await bob.get("/api/email/spam")).json()["items"] == []
        assert [x["id"] for x in (await bob.get("/api/email-accounts")).json()["items"]] == [b["id"]]
    assert (await client.get(f"/api/email/messages/{m['id']}")).json()["status"] == "spam"
    assert (await client.get(f"/api/email-accounts/{a['id']}")).json()["name"] == "Support"
