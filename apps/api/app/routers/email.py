"""Email: connected mailboxes, Settings → Email, the Spam section and email attachments.

Mailboxes and email settings: integrations.manage. The Spam section: email.review. Attachments: tickets.read.
Every query filters on ctx.workspace.id; IDs from another workspace answer 404. Passwords are encrypted
before they are stored and never returned; connects, changes and disconnects are audit-logged.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.core.security import encrypt_secret
from app.models import EmailAccount, EmailAttachment, Ticket
from app.models import EmailMessage as EmailRow
from app.models.base import utcnow
from app.models.email import REVIEW_STATUSES
from app.services.audit import audit
from app.services.mail import accounts as acc
from app.services.mail.attachments import attachment_out
from app.services.mail.auto_reply import check_template
from app.services.mail.fetch import queue_fetch
from app.services.mail.transport import AccountConfig, TransportError, get_transport
from app.services.storage import get_storage
from app.services.subscriptions import effective_limits

router = APIRouter(tags=["email"])

MANAGE = require("integrations.manage", subscription=True)
REVIEW = require("email.review", subscription=True)
READ_TICKETS = require("tickets.read", subscription=True)

Security = Literal["ssl", "starttls", "none"]


def _transport_error(e: TransportError) -> ApiError:
    return ApiError(422, e.code, e.message)


# ---- Mailboxes -----------------------------------------------------------------------------------


class ServerIn(BaseModel):
    imap_host: str = Field(min_length=1, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_security: Security = "ssl"
    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Security = "ssl"


class AccountIn(ServerIn):
    name: str = Field(default="", max_length=80)
    address: EmailStr
    display_name: str = Field(default="", max_length=120)
    kind: Literal["personal", "organization"] = "organization"
    username: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=1, max_length=500)


class AutoReplyIn(BaseModel):
    mode: Literal["off", "template", "ai"]
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=5000)


class AccountUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    kind: Literal["personal", "organization"] | None = None
    status: Literal["active", "paused"] | None = None
    auto_reply: AutoReplyIn | None = None
    inherit_auto_reply: bool = False
    # Changing any of these signs in again before anything is saved.
    username: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, min_length=1, max_length=500)
    imap_host: str | None = Field(default=None, min_length=1, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security: Security | None = None
    smtp_host: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Security | None = None


CONNECTION_FIELDS = ("username", "imap_host", "imap_port", "imap_security", "smtp_host", "smtp_port", "smtp_security")


async def _account(db: AsyncSession, ctx: WorkspaceContext, account_id: uuid.UUID) -> EmailAccount:
    a = (
        await db.execute(select(EmailAccount).where(EmailAccount.id == account_id, EmailAccount.workspace_id == ctx.workspace.id))
    ).scalar_one_or_none()
    if a is None:
        raise not_found("Email account")
    return a


async def _limit(db: AsyncSession, ws_id: uuid.UUID) -> int:
    return (await effective_limits(db, ws_id)).get("email_accounts", 0)


def _limit_error() -> ApiError:
    return ApiError(
        402,
        "limit_reached",
        "Your plan's email account limit is reached. Upgrade or disconnect a mailbox to add another.",
        limit="email_accounts",
    )


def _can_add(limit: int, used: int) -> bool:
    return limit < 0 or used < limit


@router.get("/email-accounts")
async def list_accounts(db: Db, include_disconnected: bool = False, ctx: WorkspaceContext = MANAGE):
    q = select(EmailAccount).where(EmailAccount.workspace_id == ctx.workspace.id)
    if not include_disconnected:
        q = q.where(EmailAccount.status != "disconnected")
    rows = (await db.execute(q.order_by(EmailAccount.created_at))).scalars().all()
    return {
        "items": [acc.account_out(a) for a in rows],
        "limit": await _limit(db, ctx.workspace.id),
        "used": await acc.connected_count(db, ctx.workspace.id),
        "default_account_id": acc.email_settings(ctx.workspace).get("default_account_id"),
    }


@router.post("/email-accounts", status_code=201)
async def connect_account(body: AccountIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    ws_id = ctx.workspace.id
    address = str(body.address).strip().lower()
    # Quick checks first, so a full plan doesn't wait for a sign-in; repeated under the lock below.
    if not _can_add(await _limit(db, ws_id), await acc.connected_count(db, ws_id)):
        raise _limit_error()
    cfg = AccountConfig(
        address=address,
        username=(body.username or address).strip(),
        password=body.password,
        imap_host=body.imap_host.strip().lower(),
        imap_port=body.imap_port,
        imap_security=body.imap_security,
        smtp_host=body.smtp_host.strip().lower(),
        smtp_port=body.smtp_port,
        smtp_security=body.smtp_security,
    )
    try:
        state = await get_transport().verify(cfg)
    except TransportError as e:
        raise _transport_error(e) from None

    await acc.lock_workspace(db, ws_id)
    if not _can_add(await _limit(db, ws_id), await acc.connected_count(db, ws_id)):
        raise _limit_error()
    taken = (
        await db.execute(
            select(EmailAccount.id).where(
                EmailAccount.workspace_id == ws_id, func.lower(EmailAccount.address) == address, EmailAccount.status != "disconnected"
            )
        )
    ).first()
    if taken:
        raise ApiError(409, "email_account_exists", "This mailbox is already connected.", field="address")
    a = EmailAccount(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name=(body.name or "").strip() or address,
        address=address,
        display_name=body.display_name.strip(),
        kind=body.kind,
        transport="imap",
        imap_host=cfg.imap_host,
        imap_port=cfg.imap_port,
        imap_security=cfg.imap_security,
        smtp_host=cfg.smtp_host,
        smtp_port=cfg.smtp_port,
        smtp_security=cfg.smtp_security,
        username=cfg.username,
        secret_enc=encrypt_secret(body.password),
        status="active",
        fetch_status="idle",
        fetch_state=state,
        created_by=ctx.user.id,
    )
    db.add(a)
    await db.flush()
    settings = acc.email_settings(ctx.workspace)
    if not settings.get("default_account_id"):
        _save_settings(ctx, {"default_account_id": str(a.id)})
    audit(
        db,
        "email_account.connected",
        actor_id=ctx.user.id,
        workspace_id=ws_id,
        target_type="email_account",
        target_id=a.id,
        ip=client_ip(request),
        address=address,
        kind=a.kind,
        imap_host=a.imap_host,
        smtp_host=a.smtp_host,
    )
    await db.commit()
    return acc.account_out(a)


@router.get("/email-accounts/{account_id}")
async def get_account(account_id: uuid.UUID, db: Db, ctx: WorkspaceContext = MANAGE):
    return acc.account_out(await _account(db, ctx, account_id))


@router.patch("/email-accounts/{account_id}")
async def update_account(account_id: uuid.UUID, body: AccountUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    a = await _account(db, ctx, account_id)
    if a.status == "disconnected":
        raise ApiError(409, "email_account_disconnected", "This mailbox is disconnected. Connect it again to use it.")
    changes: dict = {}
    for f in ("name", "display_name", "kind"):
        v = getattr(body, f)
        if v is not None and v != getattr(a, f):
            changes[f] = {"from": getattr(a, f), "to": v.strip() if isinstance(v, str) else v}
            setattr(a, f, v.strip() if isinstance(v, str) else v)
    conn_changed = body.password is not None or any(
        getattr(body, f) is not None and getattr(body, f) != getattr(a, f) for f in CONNECTION_FIELDS
    )
    if conn_changed:
        cfg = acc.config_for(a) if a.secret_enc else None
        new = AccountConfig(
            address=a.address,
            username=(body.username or a.username).strip(),
            password=body.password if body.password is not None else (cfg.password if cfg else ""),
            imap_host=(body.imap_host or a.imap_host).strip().lower(),
            imap_port=body.imap_port or a.imap_port,
            imap_security=body.imap_security or a.imap_security,
            smtp_host=(body.smtp_host or a.smtp_host).strip().lower(),
            smtp_port=body.smtp_port or a.smtp_port,
            smtp_security=body.smtp_security or a.smtp_security,
        )
        try:
            await get_transport().verify(new)
        except TransportError as e:
            raise _transport_error(e) from None
        for f in CONNECTION_FIELDS:
            setattr(a, f, getattr(new, f))
        if body.password is not None:
            a.secret_enc = encrypt_secret(body.password)
        changes["credentials_changed"] = True
        if a.status == "error":
            a.status = "active"
        a.last_error_code = a.last_error = None
        a.consecutive_failures = 0
        a.fetch_status = "idle"
    if body.status is not None and body.status != a.status:
        changes["status"] = {"from": a.status, "to": body.status}
        a.status = body.status
    if body.inherit_auto_reply:
        if a.auto_reply is not None:
            changes["auto_reply"] = "inherit"
        a.auto_reply = None
    elif body.auto_reply is not None:
        conf = body.auto_reply.model_dump()
        check_template(conf)
        a.auto_reply = conf
        changes["auto_reply"] = conf["mode"]
    if changes:
        audit(
            db,
            "email_account.updated",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="email_account",
            target_id=a.id,
            ip=client_ip(request),
            **changes,
        )
    await db.commit()
    return acc.account_out(a)


@router.post("/email-accounts/{account_id}/fetch")
async def fetch_now(account_id: uuid.UUID, db: Db, ctx: WorkspaceContext = MANAGE):
    a = await _account(db, ctx, account_id)
    if a.status != "active":
        raise ApiError(409, "email_account_inactive", "Resume this mailbox (or update its app password) to fetch mail.")
    if a.fetch_status != "fetching":
        a.fetch_status = "queued"
        await db.commit()
    await queue_fetch(a.id)
    await db.refresh(a)
    return acc.account_out(a)


@router.delete("/email-accounts/{account_id}")
async def disconnect_account(account_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    """Stops fetching and sending, and erases the stored password. Tickets and their emails stay."""
    a = await _account(db, ctx, account_id)
    if a.status != "disconnected":
        a.status = "disconnected"
        a.secret_enc = None
        a.disconnected_at = utcnow()
        a.fetch_status = "idle"
        if acc.email_settings(ctx.workspace).get("default_account_id") == str(a.id):
            _save_settings(ctx, {"default_account_id": None})
        audit(
            db,
            "email_account.disconnected",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="email_account",
            target_id=a.id,
            ip=client_ip(request),
            address=a.address,
        )
        await db.commit()
    return acc.account_out(a)


# ---- Settings → Email ------------------------------------------------------------------------------


class EmailSettingsIn(BaseModel):
    auto_reply: AutoReplyIn | None = None
    form_confirmation: bool | None = None
    default_account_id: uuid.UUID | None = None
    spam_filter: bool | None = None
    allow_senders: list[str] | None = Field(default=None, max_length=500)
    block_senders: list[str] | None = Field(default=None, max_length=500)
    model: str | None = None


def _save_settings(ctx: WorkspaceContext, patch: dict) -> None:
    ws = ctx.workspace
    stored = dict((ws.settings or {}).get("email") or {})
    stored.update(patch)
    ws.settings = {**(ws.settings or {}), "email": stored}


def _sender_list(items: list[str], field: str) -> list[str]:
    out = []
    for raw in items:
        v = raw.strip().lower()
        if not v:
            continue
        if len(v) > 320 or not (("@" in v and "." in v.rsplit("@", 1)[-1]) or v.startswith("*.") or "." in v):
            raise ApiError(422, "invalid_sender", f"“{raw[:80]}” isn't an email address or domain.", field=field)
        if v not in out:
            out.append(v)
    return out


@router.get("/email-settings")
async def get_email_settings(db: Db, ctx: WorkspaceContext = MANAGE):
    return {**acc.email_settings(ctx.workspace), "models": get_settings().chat_models}


@router.patch("/email-settings")
async def update_email_settings(body: EmailSettingsIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    patch: dict = {}
    if body.auto_reply is not None:
        conf = body.auto_reply.model_dump()
        check_template(conf)
        patch["auto_reply"] = conf
    if body.form_confirmation is not None:
        patch["form_confirmation"] = body.form_confirmation
    if body.spam_filter is not None:
        patch["spam_filter"] = body.spam_filter
    if "default_account_id" in body.model_fields_set:
        if body.default_account_id is not None:
            a = await _account(db, ctx, body.default_account_id)
            if a.status == "disconnected":
                raise ApiError(422, "email_account_disconnected", "Choose a connected mailbox.", field="default_account_id")
        patch["default_account_id"] = str(body.default_account_id) if body.default_account_id else None
    if body.allow_senders is not None:
        patch["allow_senders"] = _sender_list(body.allow_senders, "allow_senders")
    if body.block_senders is not None:
        patch["block_senders"] = _sender_list(body.block_senders, "block_senders")
    if body.model is not None:
        if body.model not in get_settings().chat_models:
            raise ApiError(422, "invalid_model", "Choose one of the available models.", field="model")
        patch["model"] = body.model
    if patch:
        _save_settings(ctx, patch)
        audit(
            db,
            "email_settings.updated",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="workspace",
            target_id=ctx.workspace.id,
            ip=client_ip(request),
            changed=sorted(patch),
        )
        await db.commit()
    return {**acc.email_settings(ctx.workspace), "models": get_settings().chat_models}


# ---- Spam section ----------------------------------------------------------------------------------


def message_out(m: EmailRow, *, full: bool = False, atts: list[EmailAttachment] | None = None) -> dict:
    out = {
        "id": m.id,
        "direction": m.direction,
        "status": m.status,
        "from_addr": m.from_addr,
        "from_name": m.from_name,
        "to": m.to,
        "cc": m.cc,
        "subject": m.subject,
        "snippet": " ".join((m.text_body or "").split())[:200],
        "verdict": {k: v for k, v in (m.verdict or {}).items() if k != "reviewed_by"},
        "account_id": m.account_id,
        "ticket_id": m.ticket_id,
        "kind": m.kind_out,
        "error": m.error,
        "received_at": m.received_at,
        "sent_at": m.sent_at,
        "created_at": m.created_at,
    }
    if full:
        out["text"] = m.text_body
        out["html"] = m.html_body
        out["attachments"] = [attachment_out(a) for a in atts or []]
    return out


async def _message(db: AsyncSession, ctx: WorkspaceContext, message_id: uuid.UUID, *, lock: bool = False) -> EmailRow:
    q = select(EmailRow).where(EmailRow.id == message_id, EmailRow.workspace_id == ctx.workspace.id)
    if lock:
        q = q.with_for_update()
    m = (await db.execute(q)).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "email_message_not_found", "This email isn't here any more.")
    return m


@router.get("/email/spam")
async def list_spam(db: Db, reason: str | None = None, q: str | None = None, limit: int = 100, ctx: WorkspaceContext = REVIEW):
    base = select(EmailRow).where(EmailRow.workspace_id == ctx.workspace.id, EmailRow.direction == "in")
    statuses = (reason,) if reason in REVIEW_STATUSES else REVIEW_STATUSES
    base = base.where(EmailRow.status.in_(statuses))
    if q and q.strip():
        like = f"%{q.strip()[:100].lower()}%"
        base = base.where(or_(func.lower(EmailRow.subject).like(like), func.lower(EmailRow.from_addr).like(like)))
    rows = (await db.execute(base.order_by(EmailRow.received_at.desc()).limit(max(1, min(limit, 200))))).scalars().all()
    counts = dict(
        (
            await db.execute(
                select(EmailRow.status, func.count())
                .where(EmailRow.workspace_id == ctx.workspace.id, EmailRow.direction == "in", EmailRow.status.in_(REVIEW_STATUSES))
                .group_by(EmailRow.status)
            )
        ).all()
    )
    return {"items": [message_out(m) for m in rows], "counts": {s: counts.get(s, 0) for s in REVIEW_STATUSES}}


@router.get("/email/messages/{message_id}")
async def get_message(message_id: uuid.UUID, db: Db, ctx: WorkspaceContext = REVIEW):
    m = await _message(db, ctx, message_id)
    atts = (await db.execute(select(EmailAttachment).where(EmailAttachment.email_message_id == m.id))).scalars().all()
    return message_out(m, full=True, atts=list(atts))


class NotSpamIn(BaseModel):
    allow_sender: bool = False


@router.post("/email/messages/{message_id}/not-spam")
async def mark_not_spam(message_id: uuid.UUID, body: NotSpamIn, request: Request, db: Db, ctx: WorkspaceContext = REVIEW):
    """The message is legitimate: it becomes a ticket (and gets the automatic reply). Doing it twice is harmless."""
    from app.services.mail.ingest import not_spam

    m = await _message(db, ctx, message_id, lock=True)
    if m.status == "ticketed" and m.ticket_id:
        t = await db.get(Ticket, m.ticket_id)
        return {"message": message_out(m), "ticket": {"id": t.id, "number": t.number} if t else None}
    if m.direction != "in" or m.status not in REVIEW_STATUSES:
        raise ApiError(409, "email_already_processed", "This email isn't waiting for review.")
    if m.account_id is None:
        raise ApiError(409, "email_account_disconnected", "The mailbox this email came to is gone.")
    if body.allow_sender and m.from_addr:
        allow = acc.email_settings(ctx.workspace).get("allow_senders", [])
        if m.from_addr not in allow:
            _save_settings(ctx, {"allow_senders": [*allow, m.from_addr]})
    m.status = "processing"
    audit(
        db,
        "email.not_spam",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="email_message",
        target_id=m.id,
        ip=client_ip(request),
        sender=m.from_addr,
        allow_sender=body.allow_sender,
    )
    await db.commit()
    t = await not_spam(db, m, actor_id=ctx.user.id)
    await db.refresh(m)
    return {"message": message_out(m), "ticket": {"id": t.id, "number": t.number} if t else None}


async def _delete(db: AsyncSession, ctx: WorkspaceContext, m: EmailRow, ip: str | None) -> None:
    if m.status in ("spam", "promotion"):  # the team confirms it's unwanted: the workspace's model learns from it
        from app.services.mail import spam_model

        await spam_model.learn(
            db, ctx.workspace.id, spam=True, subject=m.subject, text_body=m.text_body, html=m.html_body, from_addr=m.from_addr
        )
    atts = (await db.execute(select(EmailAttachment).where(EmailAttachment.email_message_id == m.id))).scalars().all()
    for a in atts:
        if a.storage_key:
            await get_storage().delete(a.storage_key)
        await db.delete(a)
    m.status = "deleted"
    m.text_body, m.html_body, m.headers = "", None, {}
    audit(
        db,
        "email.deleted",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="email_message",
        target_id=m.id,
        ip=ip,
        sender=m.from_addr,
        was=m.verdict.get("rule_verdict") if m.verdict else None,
    )


@router.delete("/email/messages/{message_id}", status_code=204)
async def delete_message(message_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = REVIEW):
    m = await _message(db, ctx, message_id, lock=True)
    if m.status == "deleted":
        return Response(status_code=204)
    if m.status not in REVIEW_STATUSES:
        raise ApiError(409, "email_already_processed", "Only email in the Spam section can be deleted here.")
    await _delete(db, ctx, m, client_ip(request))
    await db.commit()
    return Response(status_code=204)


class BulkDeleteIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


@router.post("/email/spam/delete")
async def bulk_delete(body: BulkDeleteIn, request: Request, db: Db, ctx: WorkspaceContext = REVIEW):
    rows = (
        (
            await db.execute(
                select(EmailRow)
                .where(EmailRow.workspace_id == ctx.workspace.id, EmailRow.id.in_(body.ids), EmailRow.status.in_(REVIEW_STATUSES))
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for m in rows:
        await _delete(db, ctx, m, client_ip(request))
    await db.commit()
    return {"deleted": len(rows)}


# ---- Attachments -------------------------------------------------------------------------------------


@router.get("/email-attachments/{attachment_id}")
async def download_attachment(attachment_id: uuid.UUID, db: Db, ctx: WorkspaceContext = READ_TICKETS):
    a = (
        await db.execute(
            select(EmailAttachment).where(EmailAttachment.id == attachment_id, EmailAttachment.workspace_id == ctx.workspace.id)
        )
    ).scalar_one_or_none()
    if a is None or a.storage_key is None:
        raise not_found("Attachment")
    m = await db.get(EmailRow, a.email_message_id)
    # Held mail (spam review) is for reviewers only.
    if m is not None and m.status in REVIEW_STATUSES and not ctx.can("email.review"):
        raise not_found("Attachment")
    data = await get_storage().get(a.storage_key)
    name = a.filename.replace('"', "")
    return Response(
        content=data,
        media_type=a.detected_mime if a.detected_mime.startswith(("image/", "application/pdf", "text/")) else "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=\"{name.encode('ascii', 'replace').decode()}\"; filename*=UTF-8''{_quote(name)}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")
