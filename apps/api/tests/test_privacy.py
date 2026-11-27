"""Phase 6 privacy: retention per workspace with platform maximums, deleting a workspace or an account with a
30-day grace period (and undoing it), erasure of rows and files, data exports, every role and tenant isolation."""

import io
import re
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import AuditLog, Conversation, DataExport, EmailAttachment, EmailMessage, Payment, Ticket, TicketEvent, User, Workspace
from app.models.base import utcnow
from app.services import email as mail
from app.services import privacy
from tests.conftest import PASSWORD, login, new_client, signup, subscribe
from tests.mail import connect, deliver
from tests.mail import mail as make_mail
from tests.test_phase3_access import team

PDF = b"%PDF-1.4\n" + b"z" * 64


async def owner(client, email="priv@example.com"):
    me = await signup(client, email)
    await subscribe(client, "growth")
    return me


def storage_files(ws_id) -> list[Path]:
    root = Path(get_settings().storage_dir) / "ws" / str(ws_id)
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


# ---- retention --------------------------------------------------------------------------------------------


async def test_retention_settings_and_platform_maximums(client):
    await owner(client)
    r = (await client.get("/api/workspace/retention")).json()
    assert r["retention"] == dict.fromkeys(privacy.RETENTION_KEYS) and r["can_edit"] is True
    body = {"retention": {"closed_tickets": 365, "resolved_conversations": 90, "email_content": None, "spam": 30}}
    r = await client.put("/api/workspace/retention", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["effective"]["spam"] == 30
    bad = await client.put("/api/workspace/retention", json={"retention": {"spam": 2}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_retention"
    async with new_client() as admin:
        assert (await login(admin, "root@example.org", "Admin-pass-123")).status_code == 200
        r = await admin.put("/api/admin/settings", json={"retention_max": {"email_content": 180, "spam": 14}})
        assert r.status_code == 200 and r.json()["retention_max"]["spam"] == 14
    r = (await client.get("/api/workspace/retention")).json()
    assert r["effective"]["email_content"] == 180 and r["effective"]["spam"] == 14  # "forever" and 30 are capped
    over = await client.put("/api/workspace/retention", json={"retention": {"spam": 30}})
    assert over.status_code == 422 and over.json()["detail"]["code"] == "retention_over_max"
    async with SessionLocal() as db:
        assert (await db.execute(select(AuditLog).where(AuditLog.action == "workspace.retention_updated"))).scalar_one()
        await db.execute(update(privacy.SystemSetting).where(privacy.SystemSetting.key == "retention_max").values(value={}))
        await db.commit()


async def test_only_workspace_updaters_change_retention(client):
    await owner(client)
    clients = await team(client)
    for role, c in clients.items():
        assert (await c.get("/api/workspace/retention")).status_code == 200
        r = await c.put("/api/workspace/retention", json={"retention": {"spam": 30}})
        assert r.status_code == (200 if role in ("owner", "admin") else 403), role
    for c in clients.values():
        if c is not client:
            await c.aclose()


async def test_the_sweep_erases_only_what_is_too_old(client, db):
    await owner(client)
    ws_id = uuid.UUID(client.headers["X-Workspace-Id"])
    a = await connect(client)
    await deliver(client, a["id"], make_mail("Old order", "Where is it?", attachments=[("old.pdf", "application/pdf", PDF)])[0])
    await deliver(client, a["id"], make_mail("New order", "And this one?")[0])
    await deliver(client, a["id"], make_mail("Win", "Prize", sender="spam@example.net", headers={"X-Spam-Flag": "YES"})[0])
    tickets = (await client.get("/api/tickets", params={"source": "email"})).json()["items"]
    old = next(t for t in tickets if t["subject"] == "Old order")
    assert (await client.patch(f"/api/tickets/{old['id']}", json={"status": "closed"})).status_code == 200
    assert len(storage_files(ws_id)) == 1
    long_ago = utcnow() - timedelta(days=100)
    await db.execute(update(Ticket).where(Ticket.id == uuid.UUID(old["id"])).values(closed_at=long_ago))
    await db.execute(update(EmailMessage).where(EmailMessage.subject.in_(("Old order", "Win"))).values(created_at=long_ago))
    conv = Conversation(workspace_id=ws_id, channel="widget", status="resolved", resolved_at=long_ago)
    db.add(conv)
    await db.commit()
    await client.put(
        "/api/workspace/retention",
        json={"retention": {"closed_tickets": 30, "resolved_conversations": 30, "spam": 30, "email_content": None}},
    )
    # Another workspace with the same old data keeps it (no retention set).
    async with new_client() as other:
        await owner(other, "other@example.com")
        other_t = (await other.post("/api/tickets", json={"subject": "Keep me"})).json()
        await other.patch(f"/api/tickets/{other_t['id']}", json={"status": "closed"})
        await db.execute(update(Ticket).where(Ticket.id == uuid.UUID(other_t["id"])).values(closed_at=long_ago))
        await db.commit()
        async with SessionLocal() as s:
            result = await privacy.sweep_retention(s)
        assert result["closed_tickets"] == 1 and result["resolved_conversations"] == 1 and result["spam"] == 1
        assert (await other.get(f"/api/tickets/{other_t['id']}")).status_code == 200
    assert (await client.get(f"/api/tickets/{old['id']}")).status_code == 404
    subjects = (await db.execute(select(EmailMessage.subject).where(EmailMessage.workspace_id == ws_id))).scalars().all()
    assert set(subjects) == {"New order"}  # the old ticket's email went with it; the spam was erased
    assert storage_files(ws_id) == [] and (await db.execute(select(EmailAttachment))).scalars().all() == []
    entry = (await db.execute(select(AuditLog).where(AuditLog.action == "privacy.retention_applied"))).scalar_one()
    assert entry.data["closed_tickets"] == 1 and "Old order" not in str(entry.data)


async def test_email_content_retention_keeps_the_thread_but_not_the_text(client, db):
    await owner(client)
    ws_id = uuid.UUID(client.headers["X-Workspace-Id"])
    a = await connect(client)
    await deliver(client, a["id"], make_mail("Invoice", "My card number ends 1234", attachments=[("i.pdf", "application/pdf", PDF)])[0])
    await db.execute(update(EmailMessage).values(created_at=utcnow() - timedelta(days=60)))
    await db.commit()
    await client.put("/api/workspace/retention", json={"retention": {"email_content": 30}})
    async with SessionLocal() as s:
        assert (await privacy.sweep_retention(s))["email_content"] == 2  # the customer's email and the automatic reply
    m = (await db.execute(select(EmailMessage).where(EmailMessage.workspace_id == ws_id, EmailMessage.direction == "in"))).scalar_one()
    await db.refresh(m)
    assert m.text_body == "" and m.html_body is None and m.verdict["erased"] is True and m.ticket_id is not None
    assert storage_files(ws_id) == []
    async with SessionLocal() as s:
        assert (await privacy.sweep_retention(s))["email_content"] == 0  # already erased


# ---- deleting a workspace ---------------------------------------------------------------------------------


async def test_deleting_a_workspace_has_a_grace_period_then_erases_it(client, db):
    await owner(client)
    ws_id = uuid.UUID(client.headers["X-Workspace-Id"])
    ws_name = (await client.get("/api/workspace")).json()["workspace"]["name"]
    clients = await team(client)
    a = await connect(client)
    await deliver(client, a["id"], make_mail("Order", "Hi", attachments=[("a.pdf", "application/pdf", PDF)])[0])
    assert storage_files(ws_id)
    # Only the owner, with the password and the exact name.
    assert (await clients["admin"].post("/api/workspace/delete", json={"password": PASSWORD, "confirm_name": ws_name})).status_code == 403
    r = await client.post("/api/workspace/delete", json={"password": "nope", "confirm_name": ws_name})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "wrong_password"
    r = await client.post("/api/workspace/delete", json={"password": PASSWORD, "confirm_name": "something else"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "name_mismatch"
    r = await client.post("/api/workspace/delete", json={"password": PASSWORD, "confirm_name": ws_name})
    assert r.status_code == 200, r.text
    # Closed at once for everyone; the subscription ended; members were told.
    for c in clients.values():
        r = await c.get("/api/tickets")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "workspace_deleting"
    me = (await client.get("/api/me")).json()
    assert me["workspaces"][0]["deletion_requested_at"] and me["workspaces"][0]["is_owner"] is True
    assert sum(1 for m in mail.OUTBOX if "will be deleted" in m.subject) == 5
    # Undo, then delete again and let the grace period pass.
    assert (await clients["admin"].post(f"/api/workspaces/{ws_id}/restore")).status_code == 403
    assert (await client.post(f"/api/workspaces/{ws_id}/restore")).status_code == 200
    assert (await client.get("/api/tickets")).status_code == 402  # back, but the subscription has to be bought again
    assert (await client.post("/api/workspace/delete", json={"password": PASSWORD, "confirm_name": ws_name})).status_code == 200
    async with SessionLocal() as s:
        assert await privacy.erase_due(s) == {"workspaces": 0, "accounts": 0}  # still in the grace period
        assert await privacy.erase_due(s, now=utcnow() + timedelta(days=31)) == {"workspaces": 1, "accounts": 0}
    assert await db.get(Workspace, ws_id) is None
    assert storage_files(ws_id) == [] and not (Path(get_settings().storage_dir) / "ws" / str(ws_id)).exists()
    invoices = (await db.execute(select(Payment))).scalars().all()
    assert invoices and all(p.workspace_id is None for p in invoices)  # kept for accounting, de-identified
    assert (await db.execute(select(AuditLog).where(AuditLog.action == "workspace.erased"))).scalar_one()
    for c in clients.values():
        if c is not client:
            await c.aclose()


async def test_other_workspaces_are_untouched(client):
    await owner(client)
    async with new_client() as other:
        await owner(other, "safe@example.com")
        name = (await other.get("/api/workspace")).json()["workspace"]["name"]
        ws_other = other.headers["X-Workspace-Id"]
        # Someone else can't restore or delete it.
        assert (await client.post(f"/api/workspaces/{ws_other}/restore")).status_code == 404
        await client.post("/api/workspace/delete", json={"password": PASSWORD, "confirm_name": name})
        assert (await other.get("/api/tickets")).status_code == 200


# ---- deleting an account ----------------------------------------------------------------------------------


async def test_deleting_an_account_and_undoing_it_by_signing_in(client, db):
    await owner(client)
    clients = await team(client)
    agent = clients["agent"]
    agent_id = uuid.UUID((await agent.get("/api/me")).json()["user"]["id"])
    t = (await client.post("/api/tickets", json={"subject": "Hello"})).json()
    await agent.post(f"/api/tickets/{t['id']}/notes", json={"text": "I'm on it"})
    r = await client.post("/api/me/delete", json={"password": PASSWORD})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "owner_must_transfer"  # others use the workspace
    agent_own = uuid.UUID(next(w["id"] for w in (await agent.get("/api/me")).json()["workspaces"] if w["is_owner"]))
    assert (await agent.post("/api/me/delete", json={"password": "wrong"})).status_code == 403
    r = await agent.post("/api/me/delete", json={"password": PASSWORD})
    assert r.status_code == 200
    assert (await agent.get("/api/me")).status_code == 401  # signed out everywhere
    assert any("account will be deleted" in m.subject for m in mail.OUTBOX)
    assert (await db.get(Workspace, agent_own)).deletion_requested_at is not None  # their own workspace goes with it
    r = await login(agent, "agent@example.com")
    assert r.status_code == 200 and r.json()["deletion_cancelled"] is True
    own = await db.get(Workspace, agent_own)
    await db.refresh(own)
    assert own.deletion_requested_at is None and own.status == "active"
    r = await agent.post("/api/me/delete", json={"password": PASSWORD})
    assert r.status_code == 200
    async with SessionLocal() as s:
        assert await privacy.erase_due(s, now=utcnow() + timedelta(days=31)) == {"workspaces": 1, "accounts": 1}
    u = await db.get(User, agent_id)
    await db.refresh(u)
    assert u.full_name == "Deleted user" and u.email.endswith("@deleted.invalid") and u.status == "deleted"
    assert (await login(agent, "agent@example.com")).status_code == 401
    # The note stays on the timeline, without the person's name.
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert "agent@example.com" not in str(detail)
    assert (await db.execute(select(TicketEvent).where(TicketEvent.author_id == agent_id))).scalars().first() is not None
    members = (await client.get("/api/workspace/members")).json()["members"]
    assert all(m["email"] != "agent@example.com" for m in members)
    for c in clients.values():
        if c is not client:
            await c.aclose()


# ---- exports ----------------------------------------------------------------------------------------------


def _link_token(to: str) -> str:
    for m in reversed(mail.OUTBOX):
        if m.to == to and "export is ready" in m.subject:
            return re.search(r"/exports/([A-Za-z0-9_\-]+)", m.text).group(1)
    raise AssertionError("no export email")


async def test_workspace_export(client, db):
    await owner(client, "exp@example.com")
    clients = await team(client)
    a = await connect(client)
    await deliver(client, a["id"], make_mail("Order", "Hi", attachments=[("receipt.pdf", "application/pdf", PDF)])[0])
    assert (await clients["admin"].post("/api/workspace/exports")).status_code == 403  # owners only
    r = await client.post("/api/workspace/exports")
    assert r.status_code == 202, r.text
    listing = (await client.get("/api/exports")).json()
    assert listing[0]["status"] == "ready" and listing[0]["kind"] == "workspace"
    token = _link_token("exp@example.com")
    assert (await clients["admin"].get(f"/api/exports/{token}/download")).status_code == 404  # not theirs
    r = await client.get(f"/api/exports/{token}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert {"tickets.json", "members.json", "email_messages.json", "invoices.json"} <= set(names)
    assert any(n.startswith("files/email/") and n.endswith("receipt.pdf") for n in names)
    assert "password_hash" not in z.read("members.json").decode() and "secret_enc" not in z.read("email_accounts.json").decode()
    assert (await client.get(f"/api/exports/{token}/download")).status_code == 410  # once
    for c in clients.values():
        if c is not client:
            await c.aclose()


async def test_personal_export_and_expiry(client, db):
    await signup(client, "me@example.com")
    r = await client.post("/api/me/exports")
    assert r.status_code == 202
    token = _link_token("me@example.com")
    async with SessionLocal() as s:
        await s.execute(update(DataExport).values(expires_at=utcnow() - timedelta(minutes=1)))
        await s.commit()
        assert await privacy.expire_exports(s) == 1
    assert (await client.get(f"/api/exports/{token}/download")).status_code in (404, 410)
    for _ in range(2):
        await client.post("/api/me/exports")
    assert (await client.post("/api/me/exports")).status_code == 429  # three a day
