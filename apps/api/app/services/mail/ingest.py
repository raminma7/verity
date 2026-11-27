"""Incoming mail → a ticket, a reply on an existing ticket or conversation, or the Spam section.

For each message fetched from a mailbox:

1. parse it (headers, text, sanitized HTML, attachments);
2. claim it: the row is inserted with the unique (account, Message-ID) index, so a message delivered twice,
   or fetched by two workers, is processed once;
3. drop bounces, automatic replies and our own mail coming back (loops);
4. thread it: In-Reply-To and References against the Message-IDs we stored, then a "[#42]" ticket number in
   the subject, which counts only when the sender is that ticket's requester. A reply goes through
   tickets.customer_replied() (so reopen_on_reply applies) or, for chat threads, into the conversation;
5. otherwise: spam rules, then one AI call for the spam verdict and the category (without AI, the rules
   decide and the default category is used), then tickets.create() with source "email" and the automatic
   first reply.

Everything in an email is untrusted: it is only ever data for the AI, sanitized for display, and its
attachments pass the same checks as uploads.
"""

import logging
import re
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.models import Conversation, EmailAccount, Ticket, TicketCategory, Workspace
from app.models import EmailMessage as EmailRow
from app.models.base import utcnow
from app.services import email as platform_mail
from app.services import tickets as tk
from app.services.forms import default_category
from app.services.mail import attachments, auto_reply, loops, spam
from app.services.mail.accounts import email_settings, workspace_addresses
from app.services.mail.classify import classify
from app.services.mail.parse import ParsedMail, parse, strip_quoted
from app.services.mail.transport import RawMail

log = logging.getLogger("verity.mail")

STALE_CLAIM = timedelta(minutes=10)
_TICKET_TAG = re.compile(r"\[#(\d{1,9})\]")


async def _claim(db: AsyncSession, account: EmailAccount, p: ParsedMail, raw: RawMail) -> uuid.UUID | None:
    """Inserts the message as "processing", or resumes one whose processing was cut off. None: already handled."""
    row_id = uuid.uuid4()
    now = utcnow()
    inserted = (
        await db.execute(
            insert(EmailRow)
            .values(
                id=row_id,
                workspace_id=account.workspace_id,
                account_id=account.id,
                direction="in",
                message_id=p.message_id,
                in_reply_to=p.in_reply_to,
                references=p.references,
                from_addr=p.from_addr,
                from_name=p.from_name,
                to=p.to,
                cc=p.cc,
                subject=p.subject,
                text_body=p.text,
                html_body=p.html,
                headers=p.headers,
                uid=raw.uid,
                size=raw.size or len(raw.data),
                status="processing",
                verdict={},
                attempts=1,
                received_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["account_id", "message_id"])
            .returning(EmailRow.id)
        )
    ).scalar_one_or_none()
    if inserted is None:
        # Already stored. Resume only if an earlier run died mid-way (a worker crash), never a live one.
        inserted = (
            await db.execute(
                update(EmailRow)
                .where(
                    EmailRow.account_id == account.id,
                    EmailRow.message_id == p.message_id,
                    EmailRow.status == "processing",
                    EmailRow.updated_at < now - STALE_CLAIM,
                )
                .values(updated_at=now, attempts=EmailRow.attempts + 1)
                .returning(EmailRow.id)
            )
        ).scalar_one_or_none()
    await db.commit()
    return inserted


async def _finish(db: AsyncSession, row: EmailRow, status: str, **verdict) -> str:
    row.status = status
    if verdict:
        row.verdict = {**(row.verdict or {}), **verdict}
    await db.commit()
    return status


async def find_thread(db: AsyncSession, workspace_id: uuid.UUID, p: ParsedMail) -> tuple[Ticket | None, Conversation | None]:
    """The ticket or conversation this message replies to, inside this workspace only."""
    mids = [m for m in [p.in_reply_to, *reversed(p.references)] if m]
    if mids:
        rows = (
            (
                await db.execute(
                    select(EmailRow).where(
                        EmailRow.workspace_id == workspace_id,
                        EmailRow.message_id.in_(mids),
                        (EmailRow.ticket_id.is_not(None)) | (EmailRow.conversation_id.is_not(None)),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_mid = {r.message_id: r for r in rows}
        for mid in mids:
            r = by_mid.get(mid)
            if r is None:
                continue
            t = await db.get(Ticket, r.ticket_id) if r.ticket_id else None
            c = await db.get(Conversation, r.conversation_id) if r.conversation_id else None
            if t is not None and t.workspace_id != workspace_id:
                t = None
            if c is not None and c.workspace_id != workspace_id:
                c = None
            if c is None and t is not None and t.conversation_id:
                c = await db.get(Conversation, t.conversation_id)
            if t is not None or c is not None:
                return t, c
    m = _TICKET_TAG.search(p.subject or "")
    if m and p.from_addr:
        t = (
            await db.execute(
                select(Ticket).where(
                    Ticket.workspace_id == workspace_id, Ticket.number == int(m.group(1)), Ticket.requester_email == p.from_addr.lower()
                )
            )
        ).scalar_one_or_none()
        if t is not None:
            c = await db.get(Conversation, t.conversation_id) if t.conversation_id else None
            return t, c
    return None, None


async def _append(db: AsyncSession, row: EmailRow, t: Ticket | None, c: Conversation | None) -> str:
    from app.services import conversations as convs

    reply = strip_quoted(row.text_body)
    if c is not None and c.channel == "widget":
        row.conversation_id = c.id
        landed = await convs.email_reply(db, c, reply, email_message_id=row.id)
        await db.refresh(row)
        row.ticket_id = (landed.id if landed is not None else None) or (t.id if t is not None else None)
        return await _finish(db, row, "appended")
    row.ticket_id = t.id
    await tk.customer_replied(db, t, reply, channel="email", email_message_id=str(row.id))
    return await _finish(db, row, "appended")


async def create_ticket(
    db: AsyncSession, ws: Workspace, account: EmailAccount, row: EmailRow, category_id: uuid.UUID | None, *, suppress_reply: bool
) -> Ticket:
    """A legitimate new email becomes a ticket through the one ticket path, then gets its first reply."""
    category = await db.get(TicketCategory, category_id) if category_id else None
    if category is None or category.workspace_id != ws.id or category.archived_at is not None:
        category = await default_category(db, ws.id)
    t = await tk.create(
        db,
        ws.id,
        tk.TicketSpec(
            subject=row.subject or "(no subject)",
            source="email",
            category_id=category.id,
            description=row.text_body,
            requester_name=row.from_name or None,
            requester_email=row.from_addr or None,
        ),
    )
    t.email_account_id = account.id
    row.ticket_id = t.id
    row.status = "ticketed"
    tk.notify_created(db, t)
    await db.commit()
    from app.services.conversations import responders

    for u in await responders(db, ws.id, "tickets.manage"):
        if u.notification_prefs.get("tickets", True):
            await platform_mail.send_ticket_created(u.email, ws.name, t.number, t.subject, str(t.id))
    if not suppress_reply:
        await auto_reply.send(db, ws, account, t, row)
    return t


async def classify_and_ticket(
    db: AsyncSession,
    ws: Workspace,
    account: EmailAccount,
    row: EmailRow,
    p: ParsedMail,
    *,
    with_verdict: bool,
    suppress_reply: bool,
    hints: list[str],
) -> str:
    """The AI step and its result. Used for new mail, and for mail a person marked "not spam" (no verdict then)."""
    model = email_settings(ws).get("model") or get_settings().mail_default_model
    cls = await classify(db, ws.id, p, model=model, hints=hints, with_verdict=with_verdict)
    # A failed metered call rolls the session back, which expires loaded rows; reload them.
    for o in (ws, account, row):
        await db.refresh(o)
    row.verdict = {
        **(row.verdict or {}),
        "ai_used": cls.ai_used,
        "ai_verdict": cls.verdict if cls.ai_used else None,
        "confidence": cls.confidence,
    }
    if cls.note:
        row.verdict["ai_note"] = cls.note
    if with_verdict and cls.verdict in ("spam", "promotion"):
        return await _finish(db, row, cls.verdict, decided_by="ai")
    row.verdict["decided_by"] = "ai" if cls.ai_used else "rules"
    await create_ticket(db, ws, account, row, cls.category_id, suppress_reply=suppress_reply)
    return "ticketed"


def _parsed_from_row(row: EmailRow) -> ParsedMail:
    return ParsedMail(
        message_id=row.message_id,
        in_reply_to=row.in_reply_to,
        references=list(row.references or []),
        from_addr=row.from_addr,
        from_name=row.from_name,
        to=list(row.to or []),
        cc=list(row.cc or []),
        subject=row.subject,
        text=row.text_body,
        html=row.html_body,
        headers=dict(row.headers or {}),
    )


async def process_incoming(db: AsyncSession, account: EmailAccount, raw: RawMail) -> str:
    """Handles one fetched message. Returns what happened to it (its status, or "duplicate")."""
    try:
        p = parse(raw.data)
    except Exception:  # noqa: BLE001 - a message we can't read must not stop the mailbox
        log.exception("unreadable message uid=%s on account %s", raw.uid, account.id)
        p = parse(b"Subject: (unreadable message)\r\n\r\n")
        p.message_id = f"<uid.{raw.uid}.{account.id}@verity.invalid>"
        raw = RawMail(raw.uid, b"", raw.size, too_large=False)
        row_id = await _claim(db, account, p, raw)
        if row_id is None:
            return "duplicate"
        row = await db.get(EmailRow, row_id)
        row.error = "This message couldn't be read."
        return await _finish(db, row, "failed")

    row_id = await _claim(db, account, p, raw)
    if row_id is None:
        return "duplicate"
    row = await db.get(EmailRow, row_id)
    ws = await db.get(Workspace, account.workspace_id)
    if raw.too_large:
        row.error = f"This message is larger than {get_settings().mail_max_message_mb} MB, so only its header was kept."
        return await _finish(db, row, "failed", reason="too_large")

    loop = loops.check(p, await workspace_addresses(db, ws.id))
    row.verdict = {"loop": loop.reasons} if loop.reasons else {}
    if loop.status in ("loop", "bounce"):
        return await _finish(db, row, loop.status)

    t, c = await find_thread(db, ws.id, p)
    if t is not None or c is not None:
        if loop.status == "auto_reply":
            # An out-of-office on a thread: keep it with the thread, but it doesn't reopen anything.
            row.ticket_id = t.id if t else None
            row.conversation_id = c.id if c else None
            return await _finish(db, row, "auto_reply")
        await attachments.store(db, ws.id, row.id, p.attachments)
        return await _append(db, row, t, c)
    if loop.status == "auto_reply":
        return await _finish(db, row, "auto_reply")

    # A new thread. Flood cap per sender and mailbox first: past it, mail waits in the Spam section.
    if not await ratelimit.allow(f"mail:tickets:{account.id}:{p.from_addr}", get_settings().rl_mail_tickets_per_sender):
        await attachments.store(db, ws.id, row.id, p.attachments)
        return await _finish(db, row, "rate_limited", decided_by="rules", rules=["sender_rate_limit"])

    await attachments.store(db, ws.id, row.id, p.attachments)
    rules = spam.check(p, email_settings(ws))
    row.verdict = {**row.verdict, "rules": rules.hits, "rule_verdict": rules.verdict}
    await db.commit()
    if rules.strong and rules.verdict in ("spam", "promotion"):
        return await _finish(db, row, rules.verdict, decided_by="rules")
    return await classify_and_ticket(
        db,
        ws,
        account,
        row,
        p,
        with_verdict=not (rules.strong and rules.verdict == "legitimate"),
        suppress_reply=loop.suppress_reply,
        hints=rules.hits,
    )


async def not_spam(db: AsyncSession, row: EmailRow, *, actor_id: uuid.UUID) -> Ticket | None:
    """A person says a held message is legitimate: classify its category (no verdict) and open its ticket."""
    account = await db.get(EmailAccount, row.account_id) if row.account_id else None
    ws = await db.get(Workspace, row.workspace_id)
    if account is None:
        raise ValueError("no account")
    p = _parsed_from_row(row)
    loop = loops.check(p, await workspace_addresses(db, ws.id))
    row.verdict = {**(row.verdict or {}), "reviewed_by": str(actor_id), "reviewed": "not_spam"}
    await classify_and_ticket(db, ws, account, row, p, with_verdict=False, suppress_reply=loop.suppress_reply, hints=[])
    await db.refresh(row)
    return await db.get(Ticket, row.ticket_id) if row.ticket_id else None
