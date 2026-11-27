"""Customer email that Verity sends: automatic replies, agent replies, chat replies and form confirmations.

Every message is stored first (status "queued") and sent by a job (`send_email_message`, keyed per message,
retried with backoff), so a slow or failing mail server never blocks an agent and the ticket shows whether
each email went out. Messages are threaded: In-Reply-To and References point at the last message of the
thread, the subject carries the ticket number ("[#42]"), and the Message-ID is signed so a copy that comes
back is recognised as ours. Automatic messages carry Auto-Submitted (RFC 3834) and ask other systems not to
answer them automatically.

With no mailbox connected, mail goes out through the platform's own sender and says that replies to it
aren't read.
"""

import html
import logging
import re
import uuid
from email.message import EmailMessage as MimeMessage
from email.utils import format_datetime, formataddr

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.models import EmailAccount, Ticket, Workspace
from app.models import EmailMessage as EmailRow
from app.models.base import utcnow
from app.services.mail.accounts import config_for
from app.services.mail.loops import LOOP_HEADER
from app.services.mail.parse import REPLY_MARKER, new_message_id
from app.services.mail.transport import TransportError, get_transport

log = logging.getLogger("verity.mail")

AUTOMATIC = ("auto_reply", "form_confirmation")
PLATFORM_NOTE = "This message was sent from an address that doesn't receive replies. To answer, write to us on our website."
_TAG = re.compile(r"\[#\d{1,9}\]")


def ticket_subject(subject: str, number: int | None) -> str:
    """`Re: <subject> [#42]`, without stacking prefixes or tags."""
    base = re.sub(r"^\s*((re|aw|sv|fw|fwd|wg)\s*:\s*)+", "", subject or "", flags=re.I).strip()
    base = _TAG.sub("", base).strip() or "Your request"
    tag = f" [#{number}]" if number else ""
    return f"Re: {base}{tag}"[:900]


async def thread_parent(
    db: AsyncSession, *, ticket_id: uuid.UUID | None = None, conversation_id: uuid.UUID | None = None
) -> EmailRow | None:
    """The newest message of the thread a reply should continue."""
    conds = []
    if ticket_id:
        conds.append(EmailRow.ticket_id == ticket_id)
    if conversation_id:
        conds.append(EmailRow.conversation_id == conversation_id)
    if not conds:
        return None
    from sqlalchemy import or_

    return (
        await db.execute(
            select(EmailRow)
            .where(or_(*conds), EmailRow.status.not_in(("deleted", "failed", "loop", "bounce")))
            .order_by(EmailRow.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def queue(
    db: AsyncSession,
    ws: Workspace,
    *,
    account: EmailAccount | None,
    to: str,
    subject: str,
    text: str,
    kind: str,
    parent: EmailRow | None = None,
    ticket: Ticket | None = None,
    conversation_id: uuid.UUID | None = None,
    author_id: uuid.UUID | None = None,
    ticket_event_id: uuid.UUID | None = None,
) -> EmailRow:
    """Stores the outgoing message and queues it. Commits."""
    refs = [*(parent.references or []), parent.message_id][-20:] if parent else []
    row = EmailRow(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        account_id=account.id if account else None,
        direction="out",
        message_id=new_message_id(account.address if account else None),
        in_reply_to=parent.message_id if parent else None,
        references=refs,
        from_addr=account.address if account else "",
        from_name=(account.display_name or ws.name) if account else ws.name,
        to=[to.lower()],
        subject=subject[:998],
        text_body=text,
        status="queued",
        kind_out=kind,
        ticket_id=ticket.id if ticket else (parent.ticket_id if parent else None),
        conversation_id=conversation_id or (ticket.conversation_id if ticket else None) or (parent.conversation_id if parent else None),
        author_id=author_id,
        ticket_event_id=ticket_event_id,
    )
    db.add(row)
    await db.commit()
    from app.jobs import enqueue

    await enqueue("send_email_message", key=f"send:{row.id}", retries=4, email_id=str(row.id))
    return row


def _body(row: EmailRow, *, platform: bool) -> str:
    parts = []
    if row.kind_out in ("agent_reply", "chat_reply", "auto_reply", "form_confirmation") and not platform:
        parts.append(REPLY_MARKER)
    parts.append(row.text_body.strip())
    if platform:
        parts.append("—\n" + PLATFORM_NOTE)
    return "\n\n".join(parts)


def build(row: EmailRow, ws: Workspace, account: EmailAccount | None) -> MimeMessage:
    platform = account is None
    msg = MimeMessage()
    if account is not None:
        msg["From"] = formataddr((account.display_name or ws.name, account.address))
    else:
        msg["From"] = get_settings().email_from
    msg["To"] = row.to[0]
    msg["Subject"] = row.subject
    msg["Message-ID"] = row.message_id
    msg["Date"] = format_datetime(utcnow())
    if row.in_reply_to:
        msg["In-Reply-To"] = row.in_reply_to
    if row.references:
        msg["References"] = " ".join(row.references)
    msg[LOOP_HEADER] = str(ws.id)
    if row.kind_out in AUTOMATIC:
        msg["Auto-Submitted"] = "auto-replied" if row.kind_out == "auto_reply" else "auto-generated"
        msg["X-Auto-Response-Suppress"] = "All"
        msg["Precedence"] = "auto_reply"
    text = _body(row, platform=platform)
    msg.set_content(text)
    msg.add_alternative(
        f'<div style="font-family:system-ui,sans-serif;font-size:14px;line-height:22px;white-space:pre-line">{html.escape(text)}</div>',
        subtype="html",
    )
    return msg


async def send(db: AsyncSession, email_id: uuid.UUID, *, final_attempt: bool = True) -> str:
    """Sends one queued message. Returns its status. Raises TransportError to ask the queue to retry."""
    from app.services import email as platform_mail

    row = await db.get(EmailRow, email_id, with_for_update=True)
    if row is None or row.direction != "out" or row.status == "sent":
        return row.status if row else "missing"
    ws = await db.get(Workspace, row.workspace_id)
    account = await db.get(EmailAccount, row.account_id) if row.account_id else None
    if account is not None and (account.status == "disconnected" or not account.secret_enc):
        account = None  # the mailbox was disconnected since: send through the platform sender instead
    row.attempts += 1
    try:
        if account is not None:
            if not await ratelimit.allow(f"mail:out:{account.id}", get_settings().rl_mail_outbound_per_account):
                raise TransportError("email_rate_limited", "This mailbox reached its hourly sending limit.", retryable=True)
            msg = build(row, ws, account)
            await get_transport().send(config_for(account), msg)
        else:
            msg = build(row, ws, None)
            row.from_addr = str(msg["From"])
            try:
                await platform_mail.send_prepared(msg)
            except OSError as e:
                raise TransportError("email_send_failed", "Couldn't reach the mail server.", retryable=True) from e
    except TransportError as e:
        row.error = e.message[:500]
        if e.retryable and not final_attempt:
            await db.commit()
            raise
        row.status = "failed"
        await db.commit()
        log.warning("email %s not sent: %s", row.id, e.code)
        return "failed"
    row.status, row.sent_at, row.error = "sent", utcnow(), None
    await db.commit()
    return "sent"


def delivery_out(row: EmailRow) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "to": row.to,
        "from": row.from_addr,
        "subject": row.subject,
        "kind": row.kind_out,
        "error": row.error,
        "sent_at": row.sent_at,
        "via_platform": row.account_id is None,
    }


async def email_chat_reply(db: AsyncSession, conv, ticket: Ticket | None, user, text: str, *, to: str) -> EmailRow | None:
    """An agent answered a chat whose visitor left an email: send the answer there too, in the chat's own thread.

    Never raises: the chat reply itself has already been saved.
    """
    from app.services.mail.accounts import sending_account

    try:
        ws = await db.get(Workspace, conv.workspace_id)
        parent = await thread_parent(db, conversation_id=conv.id, ticket_id=ticket.id if ticket else None)
        preferred = parent.account_id if parent else (ticket.email_account_id if ticket else None)
        account = await sending_account(db, ws, preferred)
        title = ticket.subject if ticket else (parent.subject if parent else (conv.title or f"Your conversation with {ws.name}"))
        body = f"{user.full_name} from {ws.name} replied in your conversation:\n\n{text.strip()}"
        if account is not None:
            body += "\n\nReply to this email to continue the conversation."
        return await queue(
            db,
            ws,
            account=account,
            to=to,
            subject=ticket_subject(title, ticket.number if ticket else None),
            text=body,
            kind="chat_reply",
            parent=parent,
            ticket=ticket,
            conversation_id=conv.id,
            author_id=user.id,
        )
    except Exception:  # noqa: BLE001 - email is a second channel; it must never undo the chat reply
        log.exception("couldn't queue the chat reply email for conversation %s", conv.id)
        await db.rollback()
        return None


async def send_ticket_confirmation(db: AsyncSession, ticket: Ticket, *, reason: str) -> EmailRow | None:
    """Confirms a ticket opened from the chat (a submitted form, or the handoff timeout) to the customer's email."""
    from app.services.forms import display_value
    from app.services.mail.accounts import email_settings, sending_account

    if not ticket.requester_email:
        return None
    try:
        ws = await db.get(Workspace, ticket.workspace_id)
        if not email_settings(ws).get("form_confirmation", True):
            return None
        account = await sending_account(db, ws)
        name = (ticket.requester_name or "").split(" ")[0] or "there"
        if reason == "handoff_timeout":
            lead = (
                f"You asked for a person in the chat on our website and nobody was free in time, so we opened ticket "
                f"#{ticket.number} for you. The team will get back to you at this address."
            )
        else:
            lead = (
                f"We received your request \u201c{ticket.subject}\u201d and opened ticket #{ticket.number}. "
                "The team will get back to you at this address."
            )
        labels = {f["key"]: f for f in ticket.form_snapshot or []}
        lines = [
            f"{labels[k]['label']}: {display_value(labels[k], v)}"
            for k, v in (ticket.form_data or {}).items()
            if k in labels and labels[k].get("type") != "checkbox" and v not in (None, "", [])
        ]
        summary = ("\n\nWhat you sent:\n" + "\n".join(lines)) if lines else ""
        tail = "\n\nReply to this email to add anything; it goes straight to your ticket." if account is not None else ""
        return await queue(
            db,
            ws,
            account=account,
            to=ticket.requester_email,
            subject=f"Your request is ticket #{ticket.number} [#{ticket.number}]",
            text=f"Hi {name},\n\n{lead}{summary}{tail}",
            kind="form_confirmation",
            ticket=ticket,
        )
    except Exception:  # noqa: BLE001 - the ticket exists; a missing confirmation must not fail the request
        log.exception("couldn't queue the confirmation for ticket %s", ticket.id)
        await db.rollback()
        return None
