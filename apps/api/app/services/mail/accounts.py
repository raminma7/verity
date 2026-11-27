"""Connected mailboxes: credentials, the workspace's email settings and what the API shows of an account.

The password is decrypted only to build the `AccountConfig` handed to the transport for one call; it is
never returned, logged or written to the audit log.
"""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models import EmailAccount, Workspace
from app.services.mail.transport import AccountConfig, TransportError

PLACEHOLDERS = ("ticket_number", "customer_name", "subject", "workspace")

DEFAULT_AUTO_REPLY = {
    "mode": "template",  # off | template | ai
    "subject": "We received your request [#{ticket_number}]",
    "body": (
        "Hi {customer_name},\n\n"
        "Thanks for writing to {workspace}. Your request “{subject}” is registered as ticket #{ticket_number}. "
        "Someone from our support team will get back to you soon.\n\n"
        "Just reply to this email to add anything; it goes straight to your ticket."
    ),
}


def default_email_settings() -> dict:
    return {
        "auto_reply": dict(DEFAULT_AUTO_REPLY),
        "form_confirmation": True,
        "default_account_id": None,
        "spam_filter": True,
        "allow_senders": [],
        "block_senders": [],
        "model": get_settings().mail_default_model,
    }


def email_settings(ws: Workspace) -> dict:
    """The workspace's email settings with defaults filled in."""
    stored = (ws.settings or {}).get("email") or {}
    out = {**default_email_settings(), **{k: v for k, v in stored.items() if k in default_email_settings()}}
    out["auto_reply"] = {**DEFAULT_AUTO_REPLY, **(stored.get("auto_reply") or {})}
    return out


def auto_reply_for(ws: Workspace, account: EmailAccount | None) -> dict:
    """The account's own automatic-reply setting when it has one, otherwise the workspace's."""
    if account is not None and account.auto_reply:
        return {**DEFAULT_AUTO_REPLY, **account.auto_reply}
    return email_settings(ws)["auto_reply"]


def config_for(account: EmailAccount) -> AccountConfig:
    if not account.secret_enc:
        raise TransportError("email_account_disconnected", "This mailbox is disconnected.")
    return AccountConfig(
        address=account.address,
        username=account.username,
        password=decrypt_secret(account.secret_enc),
        imap_host=account.imap_host,
        imap_port=account.imap_port,
        imap_security=account.imap_security,
        smtp_host=account.smtp_host,
        smtp_port=account.smtp_port,
        smtp_security=account.smtp_security,
    )


async def lock_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Serializes connects in one workspace, so parallel requests can't pass the email_accounts limit."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"email:{workspace_id}"})


async def connected_count(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(EmailAccount)
            .where(EmailAccount.workspace_id == workspace_id, EmailAccount.status != "disconnected")
        )
    ).scalar_one()


async def workspace_addresses(db: AsyncSession, workspace_id: uuid.UUID) -> set[str]:
    """Every address the workspace's own mailboxes send from (mail from them is never a customer)."""
    rows = (await db.execute(select(EmailAccount.address).where(EmailAccount.workspace_id == workspace_id))).scalars().all()
    return {a.lower() for a in rows}


async def sending_account(db: AsyncSession, ws: Workspace, preferred_id: uuid.UUID | None = None) -> EmailAccount | None:
    """The mailbox to send from: the preferred one if usable, else the workspace's default, else any active one."""
    ids = [preferred_id, email_settings(ws).get("default_account_id")]
    for i in ids:
        if not i:
            continue
        try:
            acct = await db.get(EmailAccount, uuid.UUID(str(i)))
        except ValueError:
            continue
        if acct is not None and acct.workspace_id == ws.id and acct.status in ("active", "paused", "error") and acct.secret_enc:
            return acct
    return (
        await db.execute(
            select(EmailAccount)
            .where(EmailAccount.workspace_id == ws.id, EmailAccount.status == "active", EmailAccount.secret_enc.is_not(None))
            .order_by(EmailAccount.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()


STATUS_WORDS = {
    "active": "Connected",
    "paused": "Paused",
    "error": "Needs attention",
    "disconnected": "Disconnected",
}


def account_out(a: EmailAccount) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "address": a.address,
        "display_name": a.display_name,
        "kind": a.kind,
        "transport": a.transport,
        "imap_host": a.imap_host,
        "imap_port": a.imap_port,
        "imap_security": a.imap_security,
        "smtp_host": a.smtp_host,
        "smtp_port": a.smtp_port,
        "smtp_security": a.smtp_security,
        "username": a.username,
        "has_password": bool(a.secret_enc),
        "status": a.status,
        "fetch_status": a.fetch_status,
        "last_fetch_at": a.last_fetch_at,
        "last_success_at": a.last_success_at,
        "last_error_code": a.last_error_code,
        "last_error": a.last_error,
        "auto_reply": a.auto_reply,
        "created_at": a.created_at,
        "disconnected_at": a.disconnected_at,
    }
