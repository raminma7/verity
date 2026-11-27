"""Data retention, deletion and export (GDPR).

- Retention: each workspace says how long it keeps closed tickets, resolved conversations, email content and
  mail held in Spam (default: forever); system administrators can cap each one. A sweep erases what is older,
  in batches under the workspace lock, and logs counts, never contents.
- Deleting a workspace or an account starts a 30-day grace period that can be undone; then the data is erased
  (stored files included). Invoices outlive a workspace for accounting, de-identified. Accounts are anonymized
  rather than removed, so timelines and audit logs keep their shape but no longer name the person.
- Exports: owners can export a whole workspace, anyone can export their own data. A job builds a ZIP of JSON
  plus the stored files; the download link is emailed, works once, for 7 days, and only for the person who asked.
"""

import asyncio
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, hash_token, new_token
from app.models import (
    AuditLog,
    Chatbot,
    Conversation,
    DataExport,
    EmailAccount,
    EmailAttachment,
    EmailMessage,
    EmailToken,
    Form,
    KnowledgeSource,
    Membership,
    Message,
    Payment,
    Session,
    Subscription,
    SystemSetting,
    Ticket,
    TicketCategory,
    TicketEvent,
    UsageEvent,
    User,
    Workspace,
)
from app.models.base import utcnow
from app.models.email import REVIEW_STATUSES
from app.services import email as mail
from app.services.audit import audit
from app.services.storage import get_storage

log = logging.getLogger("verity.privacy")

GRACE = timedelta(days=30)
EXPORT_TTL = timedelta(days=7)
BATCH = 200

# What a workspace can set a retention period for (days; None = keep forever).
RETENTION_KEYS = ("closed_tickets", "resolved_conversations", "email_content", "spam")
RETENTION_WORDS = {
    "closed_tickets": "Closed tickets, with their timeline and email",
    "resolved_conversations": "Resolved chatbot conversations",
    "email_content": "Email bodies and attachments",
    "spam": "Mail held in Spam",
}
MIN_DAYS = 7
MAX_DAYS = 3650


def clean_retention(values: dict) -> dict:
    from app.core.errors import ApiError

    out = {}
    for k in RETENTION_KEYS:
        v = values.get(k)
        if v is None:
            out[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, int) or not MIN_DAYS <= v <= MAX_DAYS:
            raise ApiError(422, "invalid_retention", f"Keep data for {MIN_DAYS} to {MAX_DAYS} days, or forever.", field=k)
        out[k] = v
    return out


async def retention_caps(db: AsyncSession) -> dict:
    row = await db.get(SystemSetting, "retention_max")
    saved = row.value if row and isinstance(row.value, dict) else {}
    return {k: saved.get(k) for k in RETENTION_KEYS}


def effective_retention(ws_settings: dict | None, caps: dict) -> dict:
    """The workspace's choice, never longer than the platform's maximum."""
    own = (ws_settings or {}).get("retention") or {}
    out = {}
    for k in RETENTION_KEYS:
        mine, cap = own.get(k), caps.get(k)
        out[k] = cap if mine is None else (mine if cap is None else min(mine, cap))
    return out


async def _lock(db: AsyncSession, ws_id: uuid.UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"privacy:{ws_id}"})


async def _drop_files(keys: list[str | None]) -> None:
    storage = get_storage()
    for k in keys:
        if k:
            try:
                await storage.delete(k)
            except (OSError, ValueError):
                log.warning("couldn't delete a stored file")


async def _erase_email(db: AsyncSession, message_ids: list[uuid.UUID], *, rows: bool) -> int:
    """Removes attachments (files and rows) of these messages; then the messages, or only their content."""
    if not message_ids:
        return 0
    keys = (await db.execute(select(EmailAttachment.storage_key).where(EmailAttachment.email_message_id.in_(message_ids)))).scalars().all()
    await db.execute(delete(EmailAttachment).where(EmailAttachment.email_message_id.in_(message_ids)))
    if rows:
        await db.execute(delete(EmailMessage).where(EmailMessage.id.in_(message_ids)))
    else:
        await db.execute(
            text(
                "UPDATE email_messages SET text_body = '', html_body = NULL, headers = '{}'::jsonb, "
                "verdict = verdict || '{\"erased\": true}'::jsonb WHERE id = ANY(:ids)"
            ),
            {"ids": list(message_ids)},
        )
    await db.flush()
    await _drop_files(list(keys))
    return len(message_ids)


async def sweep_workspace(db: AsyncSession, ws: Workspace, caps: dict, now: datetime | None = None) -> dict:
    """Erases what this workspace keeps for longer than its retention settings allow. Commits in batches."""
    now = now or utcnow()
    days = effective_retention(ws.settings, caps)
    counts = dict.fromkeys(RETENTION_KEYS, 0)

    if days["closed_tickets"]:
        cutoff = now - timedelta(days=days["closed_tickets"])
        while True:
            await _lock(db, ws.id)
            ids = (
                (
                    await db.execute(
                        select(Ticket.id)
                        .where(Ticket.workspace_id == ws.id, Ticket.status == "closed", Ticket.closed_at < cutoff)
                        .limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                break
            mids = (await db.execute(select(EmailMessage.id).where(EmailMessage.ticket_id.in_(ids)))).scalars().all()
            await _erase_email(db, list(mids), rows=True)
            await db.execute(delete(Ticket).where(Ticket.id.in_(ids)))  # timeline events cascade
            counts["closed_tickets"] += len(ids)
            await db.commit()

    if days["resolved_conversations"]:
        cutoff = now - timedelta(days=days["resolved_conversations"])
        while True:
            await _lock(db, ws.id)
            ids = (
                (
                    await db.execute(
                        select(Conversation.id)
                        .where(Conversation.workspace_id == ws.id, Conversation.status == "resolved", Conversation.resolved_at < cutoff)
                        .limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                break
            await db.execute(delete(Conversation).where(Conversation.id.in_(ids)))  # messages cascade
            counts["resolved_conversations"] += len(ids)
            await db.commit()

    if days["spam"]:
        cutoff = now - timedelta(days=days["spam"])
        while True:
            await _lock(db, ws.id)
            ids = (
                (
                    await db.execute(
                        select(EmailMessage.id)
                        .where(
                            EmailMessage.workspace_id == ws.id,
                            EmailMessage.status.in_((*REVIEW_STATUSES, "deleted")),
                            EmailMessage.created_at < cutoff,
                        )
                        .limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                break
            counts["spam"] += await _erase_email(db, list(ids), rows=True)
            await db.commit()

    if days["email_content"]:
        cutoff = now - timedelta(days=days["email_content"])
        while True:
            await _lock(db, ws.id)
            ids = (
                (
                    await db.execute(
                        select(EmailMessage.id)
                        .where(
                            EmailMessage.workspace_id == ws.id,
                            EmailMessage.created_at < cutoff,
                            or_(EmailMessage.verdict["erased"].is_(None), EmailMessage.verdict["erased"].astext != "true"),
                        )
                        .limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                break
            counts["email_content"] += await _erase_email(db, list(ids), rows=False)
            await db.commit()

    if any(counts.values()):
        audit(db, "privacy.retention_applied", workspace_id=ws.id, target_type="workspace", target_id=ws.id, **counts)
        await db.commit()
    return counts


async def sweep_retention(db: AsyncSession) -> dict:
    caps = await retention_caps(db)
    total = dict.fromkeys(RETENTION_KEYS, 0)
    workspaces = (await db.execute(select(Workspace).where(Workspace.deletion_requested_at.is_(None)))).scalars().all()
    for ws in workspaces:
        if not any(effective_retention(ws.settings, caps).values()):
            continue
        for k, v in (await sweep_workspace(db, ws, caps)).items():
            total[k] += v
    return total


# ---- Deleting a workspace ---------------------------------------------------------------------------------


async def request_workspace_deletion(db: AsyncSession, ws: Workspace, user: User, *, ip: str | None) -> datetime:
    """Suspends the workspace at once and ends its subscription now; erased after the grace period."""
    from app.services import billing
    from app.services.payments import ProviderError, get_provider
    from app.services.subscriptions import end_subscription

    now = utcnow()
    ws.deletion_requested_at = now
    ws.deletion_requested_by = user.id
    ws.status = "suspended"
    sub = (await db.execute(select(Subscription).where(Subscription.workspace_id == ws.id).with_for_update())).scalar_one_or_none()
    if sub is not None and sub.status in ("active", "past_due"):
        await billing.lock(db, ws.id)
        try:
            await get_provider(sub.provider).cancel_now(sub)
        except ProviderError:
            log.warning("the payment provider didn't cancel the subscription; it ends at its period end instead")
            sub.cancel_at_period_end = True
        await end_subscription(db, sub, reason="workspace_deleted")
    await db.execute(update(EmailAccount).where(EmailAccount.workspace_id == ws.id).values(status="disconnected"))
    audit(db, "workspace.deletion_requested", actor_id=user.id, workspace_id=ws.id, target_type="workspace", target_id=ws.id, ip=ip)
    return now + GRACE


async def workspace_member_emails(db: AsyncSession, ws_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = (
        await db.execute(
            select(User.email, User.locale).join(Membership, Membership.user_id == User.id).where(Membership.workspace_id == ws_id)
        )
    ).all()
    return [(e, loc) for e, loc in rows]


async def restore_workspace(db: AsyncSession, ws: Workspace, user: User) -> None:
    ws.deletion_requested_at = None
    ws.deletion_requested_by = None
    ws.status = "active"
    audit(db, "workspace.deletion_cancelled", actor_id=user.id, workspace_id=ws.id, target_type="workspace", target_id=ws.id)


async def erase_workspace(db: AsyncSession, ws_id: uuid.UUID) -> None:
    """Erases a workspace for good: stored files, then every row (foreign keys cascade). Invoices stay, de-identified."""
    await _lock(db, ws_id)
    ws = await db.get(Workspace, ws_id)
    if ws is None:
        return
    keys: list[str | None] = []
    keys += (await db.execute(select(EmailAttachment.storage_key).where(EmailAttachment.workspace_id == ws_id))).scalars().all()
    keys += (await db.execute(select(KnowledgeSource.storage_key).where(KnowledgeSource.workspace_id == ws_id))).scalars().all()
    keys += (await db.execute(select(Chatbot.logo_key).where(Chatbot.workspace_id == ws_id))).scalars().all()
    keys += (await db.execute(select(DataExport.storage_key).where(DataExport.workspace_id == ws_id))).scalars().all()
    await db.execute(update(Payment).where(Payment.workspace_id == ws_id).values(workspace_id=None, hosted_url=None))
    await db.execute(update(User).where(User.last_workspace_id == ws_id).values(last_workspace_id=None))
    await db.execute(delete(Workspace).where(Workspace.id == ws_id))
    audit(db, "workspace.erased", workspace_id=ws_id, target_type="workspace", target_id=ws_id, files=len([k for k in keys if k]))
    await db.commit()
    await _drop_files(keys)
    await get_storage().delete_workspace(ws_id)
    log.info("workspace erased (%s files)", len([k for k in keys if k]))


# ---- Deleting an account ----------------------------------------------------------------------------------


async def owned_workspaces(db: AsyncSession, user: User) -> tuple[list[Workspace], list[Workspace]]:
    """The person's workspaces that aren't being deleted: (shared with others, only them)."""
    owned = list(
        (await db.execute(select(Workspace).where(Workspace.owner_id == user.id, Workspace.deletion_requested_at.is_(None)))).scalars()
    )
    shared, alone = [], []
    for ws in owned:
        n = await db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == ws.id))
        (shared if n > 1 else alone).append(ws)
    return shared, alone


async def request_account_deletion(db: AsyncSession, user: User, *, ip: str | None) -> datetime:
    """Signs the person out and schedules the account's anonymization. Workspaces they own alone go with it."""
    now = utcnow()
    _, alone = await owned_workspaces(db, user)
    for ws in alone:
        await request_workspace_deletion(db, ws, user, ip=ip)
        ws.deletion_requested_at = now
    user.deletion_requested_at = now
    await db.execute(update(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None)).values(revoked_at=now))
    audit(db, "account.deletion_requested", actor_id=user.id, target_type="user", target_id=user.id, ip=ip)
    return now + GRACE


async def cancel_account_deletion(db: AsyncSession, user: User, *, ip: str | None) -> None:
    """Signing in within the grace period keeps the account, and the workspaces that were going with it."""
    since = user.deletion_requested_at
    user.deletion_requested_at = None
    if since is not None:
        for ws in (
            await db.execute(select(Workspace).where(Workspace.deletion_requested_by == user.id, Workspace.deletion_requested_at == since))
        ).scalars():
            await restore_workspace(db, ws, user)
    audit(db, "account.deletion_cancelled", actor_id=user.id, target_type="user", target_id=user.id, ip=ip)


async def erase_account(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Anonymizes the account: the row stays (so timelines and audit logs keep their shape), the person doesn't."""
    user = await db.get(User, user_id, with_for_update=True)
    if user is None or user.status == "deleted":
        return
    keys = (await db.execute(select(DataExport.storage_key).where(DataExport.user_id == user_id))).scalars().all()
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.execute(delete(EmailToken).where(EmailToken.user_id == user_id))
    await db.execute(delete(DataExport).where(DataExport.user_id == user_id))
    await db.execute(delete(Membership).where(Membership.user_id == user_id))
    user.email = f"deleted-{user.id.hex}@deleted.invalid"
    user.full_name = "Deleted user"
    user.password_hash = hash_password(new_token(32))
    user.status = "deleted"
    user.totp_enabled = False
    user.totp_secret_enc = None
    user.recovery_codes = None
    user.last_workspace_id = None
    user.deletion_requested_at = None
    await db.execute(update(AuditLog).where(AuditLog.actor_id == user_id).values(ip=None))
    audit(db, "account.erased", target_type="user", target_id=user_id)
    await db.commit()
    await _drop_files(list(keys))


async def erase_due(db: AsyncSession, now: datetime | None = None) -> dict:
    """Erases workspaces and accounts whose grace period is over."""
    cutoff = (now or utcnow()) - GRACE
    ws_ids = (await db.execute(select(Workspace.id).where(Workspace.deletion_requested_at < cutoff))).scalars().all()
    for ws_id in ws_ids:
        await erase_workspace(db, ws_id)
    user_ids = (await db.execute(select(User.id).where(User.deletion_requested_at < cutoff))).scalars().all()
    for uid in user_ids:
        await erase_account(db, uid)
    return {"workspaces": len(ws_ids), "accounts": len(user_ids)}


# ---- Exports ----------------------------------------------------------------------------------------------


def _jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v


SECRET_COLUMNS = {
    "password_hash",
    "totp_secret_enc",
    "recovery_codes",
    "secret_enc",
    "token_hash",
    "visitor_token_hash",
    "search_vector",
    "embedding",
}


def _row(obj) -> dict:
    return {c.key: _jsonable(getattr(obj, c.key)) for c in obj.__table__.columns if c.key not in SECRET_COLUMNS}


async def _rows(db: AsyncSession, model, *where) -> list[dict]:
    return [_row(o) for o in (await db.execute(select(model).where(*where))).scalars()]


async def _workspace_payload(db: AsyncSession, ws_id: uuid.UUID) -> tuple[dict[str, object], list[tuple[str, str]]]:
    ws = await db.get(Workspace, ws_id)
    members = (
        await db.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == ws_id))
    ).all()
    data: dict[str, object] = {
        "workspace.json": _row(ws),
        "members.json": [
            {"name": u.full_name, "email": u.email, "role": m.role.name, "joined_at": _jsonable(m.created_at)} for m, u in members
        ],
        "ticket_categories.json": await _rows(db, TicketCategory, TicketCategory.workspace_id == ws_id),
        "forms.json": await _rows(db, Form, Form.workspace_id == ws_id),
        "tickets.json": await _rows(db, Ticket, Ticket.workspace_id == ws_id),
        "ticket_events.json": await _rows(db, TicketEvent, TicketEvent.workspace_id == ws_id),
        "chatbots.json": await _rows(db, Chatbot, Chatbot.workspace_id == ws_id),
        "conversations.json": await _rows(db, Conversation, Conversation.workspace_id == ws_id),
        "messages.json": await _rows(db, Message, Message.workspace_id == ws_id),
        "email_accounts.json": await _rows(db, EmailAccount, EmailAccount.workspace_id == ws_id),
        "email_messages.json": await _rows(db, EmailMessage, EmailMessage.workspace_id == ws_id),
        "knowledge_sources.json": await _rows(db, KnowledgeSource, KnowledgeSource.workspace_id == ws_id),
        "usage_events.json": await _rows(db, UsageEvent, UsageEvent.workspace_id == ws_id),
        "invoices.json": await _rows(db, Payment, Payment.workspace_id == ws_id),
        "audit_log.json": await _rows(db, AuditLog, AuditLog.workspace_id == ws_id),
    }
    files: list[tuple[str, str]] = []
    for a in (await db.execute(select(EmailAttachment).where(EmailAttachment.workspace_id == ws_id))).scalars():
        if a.storage_key:
            files.append((f"files/email/{a.id}-{a.filename}", a.storage_key))
    for s in (await db.execute(select(KnowledgeSource).where(KnowledgeSource.workspace_id == ws_id))).scalars():
        if s.storage_key:
            files.append((f"files/knowledge/{s.id}-{(s.name or 'file').replace('/', '_')}", s.storage_key))
    return data, files


async def _personal_payload(db: AsyncSession, user_id: uuid.UUID) -> dict[str, object]:
    user = await db.get(User, user_id)
    memberships = (
        await db.execute(
            select(Membership, Workspace).join(Workspace, Workspace.id == Membership.workspace_id).where(Membership.user_id == user_id)
        )
    ).all()
    return {
        "profile.json": _row(user),
        "workspaces.json": [{"workspace": w.name, "role": m.role.name, "joined_at": _jsonable(m.created_at)} for m, w in memberships],
        "sessions.json": await _rows(db, Session, Session.user_id == user_id),
        "ticket_notes_and_replies.json": await _rows(db, TicketEvent, TicketEvent.author_id == user_id),
        "chat_replies.json": await _rows(db, Message, Message.author_id == user_id),
        "activity.json": await _rows(db, AuditLog, AuditLog.actor_id == user_id),
    }


async def build_export(db: AsyncSession, export_id: uuid.UUID) -> DataExport | None:
    ex = await db.get(DataExport, export_id)
    if ex is None or ex.status != "queued":
        return ex
    try:
        if ex.kind == "workspace":
            data, files = await _workspace_payload(db, ex.workspace_id)
        else:
            data, files = await _personal_payload(db, ex.user_id), []
        storage = get_storage()
        blobs = []
        for name, key in files:
            try:
                blobs.append((name, await storage.get(key)))
            except (OSError, ValueError):
                continue

        def pack() -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(
                    "README.txt", "Verity data export. JSON files hold the records; files/ holds stored attachments and documents.\n"
                )
                for name, value in data.items():
                    z.writestr(name, json.dumps(value, ensure_ascii=False, indent=1))
                for name, blob in blobs:
                    z.writestr(name, blob)
            return buf.getvalue()

        payload = await asyncio.to_thread(pack)
        owner_ws = ex.workspace_id or ex.user_id
        token = new_token(32)
        ex.storage_key = await storage.put(owner_ws, payload, suffix=".zip")
        ex.size = len(payload)
        ex.token_hash = hash_token(token)
        ex.status = "ready"
        ex.ready_at = utcnow()
        ex.expires_at = ex.ready_at + EXPORT_TTL
        await db.commit()
        user = await db.get(User, ex.user_id)
        ws = await db.get(Workspace, ex.workspace_id) if ex.workspace_id else None
        await mail.send_export_ready(user.email, ws.name if ws else None, token, ex.expires_at.strftime("%d %b %Y"), locale=user.locale)
    except Exception as e:  # noqa: BLE001 - recorded on the export, shown to the person
        await db.rollback()
        ex = await db.get(DataExport, export_id)
        ex.status = "failed"
        ex.error = type(e).__name__
        await db.commit()
        log.exception("export failed")
    return ex


async def expire_exports(db: AsyncSession, now: datetime | None = None) -> int:
    now = now or utcnow()
    rows = (
        (await db.execute(select(DataExport).where(DataExport.status.in_(("ready", "downloaded")), DataExport.expires_at < now)))
        .scalars()
        .all()
    )
    keys = [r.storage_key for r in rows]
    for r in rows:
        r.status = "expired"
        r.storage_key = None
        r.token_hash = None
    await db.commit()
    await _drop_files(keys)
    return len(rows)


async def privacy_sweep(db: AsyncSession) -> dict:
    """Hourly: retention, erasure after the grace period, expired exports."""
    return {
        "retention": await sweep_retention(db),
        "erased": await erase_due(db),
        "exports_expired": await expire_exports(db),
    }
