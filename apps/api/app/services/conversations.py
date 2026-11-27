"""Conversations: visitor and member messages, AI answers, and handing a conversation to the team.

    open ──(escalate: visitor asks, no answer, AI unavailable; or an agent replies)──▶ escalated
    escalated ──(agent resolves)──▶ resolved ──(visitor writes again)──▶ open
    escalated ──(nobody replies within wait_seconds)──▶ a form is offered in the chat
    any ──(visitor submits a form: escalate(ticket=…, live=False, hand_back=True))──▶ open, with a ticket

While a conversation is escalated the AI never answers; visitor messages wait for an agent. `escalate()` is
the single entry point for handing a conversation to the team: live (a person takes over), as a ticket
(from a chatbot form or an agent's conversion), or both.
"""

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiTool, Chatbot, Conversation, Membership, Message, Notification, User, Workspace
from app.models.base import utcnow
from app.models.integrations import chatbot_tools
from app.services import email as mail
from app.services import rag
from app.services.audit import audit

MAX_MESSAGE_CHARS = 4000


def persona_for(bot: Chatbot | None, ws: Workspace) -> rag.Persona:
    if bot is None:
        return rag.Persona(workspace_name=ws.name)
    return rag.Persona(
        name=bot.name,
        workspace_name=ws.name,
        language=bot.language,
        tone=bot.tone,
        instructions=bot.instructions,
        no_answer_message=bot.no_answer_message,
        fallback_message=bot.fallback_message,
        model=bot.model,
    )


def message_out(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "kind": m.kind,
        "content": m.content,
        "data": m.data,
        "citations": m.citations,
        "confidence": m.confidence,
        "is_fallback": m.is_fallback,
        "model": m.model,
        "latency_ms": m.latency_ms,
        "trace": m.trace,
        "created_at": m.created_at,
    }


# What the widget may see of a message's data. Never prefill values, usage ids or ticket ids.
PUBLIC_DATA = ("blocks", "event", "offer_escalation", "form", "state", "reason", "ticket_number", "notice", "custom", "email")


def public_message_out(m: Message, author: str | None = None) -> dict:
    """What a visitor sees: no models, traces or token counts."""
    return {
        "id": m.id,
        "role": m.role,
        "kind": m.kind,
        "content": m.content,
        "data": {k: v for k, v in (m.data or {}).items() if k in PUBLIC_DATA},
        "citations": [{k: c.get(k) for k in ("n", "title", "type", "url", "page", "section", "cited_text")} for c in m.citations],
        "author": author,
        "created_at": m.created_at,
    }


def conversation_out(c: Conversation) -> dict:
    return {
        "id": c.id,
        "chatbot_id": c.chatbot_id,
        "channel": c.channel,
        "status": c.status,
        "title": c.title,
        "visitor_name": c.visitor_name,
        "visitor_email": c.visitor_email,
        "origin": c.origin,
        "page_url": c.page_url,
        "escalated_at": c.escalated_at,
        "escalation_reason": c.escalation_reason,
        "resolved_at": c.resolved_at,
        "assigned_to": c.assigned_to,
        "message_count": c.message_count,
        "last_message_at": c.last_message_at,
        "created_at": c.created_at,
    }


def add_message(db: AsyncSession, conv: Conversation, role: str, content: str = "", **fields) -> Message:
    now = utcnow()
    m = Message(
        workspace_id=conv.workspace_id,
        conversation_id=conv.id,
        role=role,
        content=content,
        kind=fields.pop("kind", "text"),
        data=fields.pop("data", {}),
        citations=fields.pop("citations", []),
        trace=fields.pop("trace", []),
        created_at=now,
        **fields,
    )
    db.add(m)
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = now
    if not conv.title and role in ("visitor", "member") and content:
        conv.title = " ".join(content.split())[:200]
    return m


async def history_of(db: AsyncSession, conv: Conversation) -> list[tuple[str, str]]:
    rows = (
        await db.execute(
            select(Message.role, Message.content)
            .where(Message.conversation_id == conv.id, Message.kind == "text")
            .order_by(Message.created_at.desc())
            .limit(rag.HISTORY_TURNS * 2)
        )
    ).all()
    return [(r.role, r.content) for r in reversed(rows)]


async def tools_for(db: AsyncSession, bot: Chatbot | None, conv: Conversation) -> list[ApiTool]:
    """The chatbot's active API tools that this conversation can use (context values like the visitor's
    email must be known)."""
    if bot is None:
        return []
    from app.services.tools.runner import available_for

    rows = (
        await db.execute(
            select(ApiTool)
            .join(chatbot_tools, chatbot_tools.c.tool_id == ApiTool.id)
            .where(chatbot_tools.c.chatbot_id == bot.id, ApiTool.workspace_id == conv.workspace_id, ApiTool.status == "active")
            .order_by(ApiTool.name)
        )
    ).scalars()
    return [t for t in rows if available_for(t, conv)]


async def ask(db: AsyncSession, conv: Conversation, text: str, *, role: str, operation: str) -> AsyncIterator[dict]:
    """Saves the question and, unless people have the conversation, streams and saves the AI's answer.

    Yields {"type": "message", "message"} for the saved question, {"type": "delta"} while answering, then
    {"type": "message", "message"} for the saved answer — or {"type": "waiting"} when an agent has it.
    """
    ws = await db.get(Workspace, conv.workspace_id)
    bot = await db.get(Chatbot, conv.chatbot_id) if conv.chatbot_id else None
    history = await history_of(db, conv)
    if conv.status == "resolved":
        conv.status = "open"
        conv.resolved_at = None
    question = add_message(db, conv, role, text[:MAX_MESSAGE_CHARS])
    if role == "visitor":
        from app.services import tickets as tk

        ticket = await tk.open_ticket_for(db, conv.id)
        if ticket is not None:
            await tk.customer_replied(db, ticket, text)
    await db.commit()
    yield {"type": "message", "message": question}
    if conv.status == "escalated":
        yield {"type": "waiting"}
        return

    final = None
    tools = await tools_for(db, bot, conv)
    async for ev in rag.answer(
        db, ws, text, persona=persona_for(bot, ws), history=history, operation=operation, tools=tools, conversation=conv
    ):
        if ev["type"] == "delta":
            yield ev
        else:
            final = ev
    # A failed metered call rolls the session back, which expires loaded rows; reload the ones still in use.
    for o in (conv, bot):
        if o is not None and sa_inspect(o).expired_attributes:
            await db.refresh(o)
    escalation_on = bot is None or bot.escalation.get("enabled", True)
    offer = final["outcome"] in ("no_answer", "fallback") and escalation_on and conv.channel == "widget"
    reply = add_message(
        db,
        conv,
        "assistant",
        final["content"],
        data={
            "blocks": final["blocks"],
            "outcome": final["outcome"],
            "sources": final["sources"],
            "offer_escalation": offer,
            "usage_event_id": str(final["usage_event_id"]) if final.get("usage_event_id") else None,
            # API tools the answer called (members only; never in PUBLIC_DATA).
            "tools": final.get("tools", []),
        },
        citations=final["citations"],
        confidence=final["confidence"],
        model=final["model"],
        is_fallback=final["outcome"] == "fallback",
        input_tokens=final["input_tokens"],
        output_tokens=final["output_tokens"],
        latency_ms=final["latency_ms"],
        trace=final["trace"],
    )
    await db.commit()
    yield {"type": "message", "message": reply}
    if conv.channel == "widget" and final["outcome"] in ("answered", "no_answer"):
        from app.services import forms_ai

        form = await forms_ai.maybe_offer(db, conv)
        if form is not None:
            yield {"type": "message", "message": form}


async def responders(db: AsyncSession, workspace_id: uuid.UUID, permission: str = "conversations.reply") -> list[User]:
    rows = (
        await db.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == workspace_id))
    ).all()
    return [u for m, u in rows if permission in m.role.permissions and u.status == "active"]


@dataclass
class EscalationResult:
    escalated: bool  # the conversation was newly handed to people live
    ticket: object | None = None  # app.models.Ticket

    def __bool__(self) -> bool:
        return self.escalated


async def escalate(
    db: AsyncSession,
    conv: Conversation,
    *,
    reason: str,
    email: str | None = None,
    name: str | None = None,
    actor_id: uuid.UUID | None = None,
    ip: str | None = None,
    ticket=None,
    live: bool = True,
    hand_back: bool = False,
) -> EscalationResult:
    """Hands the conversation to the team.

    live: people take the conversation over (the AI goes quiet) and are notified; if the chatbot's ticket
    fallback is on, a check is scheduled to offer a form when nobody replies within `wait_seconds`.
    ticket (a services.tickets.TicketSpec): also opens a ticket linked to this conversation and notifies
    the people who work tickets.
    hand_back: after a ticket from a chatbot form, the assistant takes the chat back and tells the customer
    their ticket number.
    """
    from app.services import tickets as tk

    if email:
        conv.visitor_email = email.strip().lower()[:320]
    if name:
        conv.visitor_name = " ".join(name.split())[:120]
    ws = await db.get(Workspace, conv.workspace_id)
    bot = await db.get(Chatbot, conv.chatbot_id) if conv.chatbot_id else None
    result = EscalationResult(escalated=False)

    if ticket is not None:
        ticket.requester_email = ticket.requester_email or conv.visitor_email
        ticket.requester_name = ticket.requester_name or conv.visitor_name
        t = await tk.create(db, ws.id, ticket, ip=ip)
        t.conversation_id = conv.id
        result.ticket = t
        add_message(db, conv, "system", "", kind="event", data={"event": "ticket_created", "ticket_number": t.number, "reason": reason})
        tk.notify_created(db, t, conversation_id=conv.id)
        if hand_back:
            if conv.status == "escalated":
                conv.status = "open"
                conv.resolved_at = None
            form = ticket.form
            if reason == "handoff_timeout":
                # Opened for a visitor who may have left: say where the team will reply.
                add_message(
                    db,
                    conv,
                    "assistant",
                    f"Nobody from the team was free in time, so we opened ticket #{t.number}. "
                    f"The team will get back to you at {conv.visitor_email}.",
                    data={"notice": "ticket_created_auto", "ticket_number": t.number, "email": conv.visitor_email},
                )
            else:
                add_message(
                    db,
                    conv,
                    "assistant",
                    (getattr(form, "success_message", "") or "").strip()
                    or f"Your request is registered as ticket #{t.number}. Someone from our support team will contact you soon.",
                    data={"notice": "ticket_created", "ticket_number": t.number, "custom": bool(getattr(form, "success_message", ""))},
                )

    if live:
        if conv.status == "escalated":
            await db.commit()
            return result
        conv.status = "escalated"
        conv.escalated_at = utcnow()
        conv.escalation_reason = reason
        conv.resolved_at = None
        add_message(db, conv, "system", "", kind="event", data={"event": "escalated", "reason": reason})
        db.add(
            Notification(
                workspace_id=ws.id,
                kind="conversation_escalated",
                title="A customer is waiting for a reply",
                body=(conv.title or "New conversation")[:300],
                data={"conversation_id": str(conv.id)},
                created_at=utcnow(),
            )
        )
        audit(
            db,
            "conversation.escalated",
            actor_id=actor_id,
            workspace_id=ws.id,
            target_type="conversation",
            target_id=conv.id,
            ip=ip,
            reason=reason,
        )
        result.escalated = True
    await db.commit()

    if result.escalated:
        for u in await responders(db, ws.id):
            if u.notification_prefs.get("escalations", True):
                await mail.send_escalation(u.email, ws.name, bot.name if bot else "the assistant", conv.title, str(conv.id))
        esc = (bot.escalation if bot else {}) or {}
        # An email reply on a conversation that already has an open ticket is the ticket's business: no new check.
        already_ticketed = reason == "email_reply" and await tk.open_ticket_for(db, conv.id) is not None
        if bot is not None and conv.channel == "widget" and esc.get("ticket_fallback", True) and not already_ticketed:
            from app.jobs import enqueue

            wait = int(esc.get("wait_seconds", 120))
            await enqueue(
                "handoff_check",
                key=f"handoff:{conv.id}:{conv.escalated_at.timestamp():.0f}",
                scheduled=int(time.time()) + wait,
                conversation_id=str(conv.id),
                escalated_at=conv.escalated_at.isoformat(),
            )
    if result.ticket is not None:
        t = result.ticket
        if reason in ("form_submitted", "handoff_timeout") and t.requester_email:
            from app.services.mail import outbound

            await outbound.send_ticket_confirmation(db, t, reason=reason)
            await db.refresh(t)
        for u in await responders(db, ws.id, "tickets.manage"):
            if u.notification_prefs.get("tickets", True):
                await mail.send_ticket_created(u.email, ws.name, t.number, t.subject, str(t.id))
    return result


async def agent_reply(db: AsyncSession, conv: Conversation, user: User, text: str, *, ip: str | None = None, ticket=None) -> Message:
    """A person answers. Replying takes the conversation over: the AI stays quiet until it's resolved.

    When the conversation has an open ticket (or `ticket` is given), the reply is also added to its timeline.
    """
    from app.services import tickets as tk

    ticket = ticket or await tk.open_ticket_for(db, conv.id)
    if ticket is not None and ticket.status != "closed":
        tk.add_event(db, ticket, "agent_reply", text[:MAX_MESSAGE_CHARS], author_id=user.id)
        tk.mark_first_response(ticket)
        if ticket.status == "new":
            tk.set_status(db, ticket, "open", actor_id=user.id)
        ticket.assignee_id = ticket.assignee_id or user.id
    if conv.status != "escalated":
        conv.status = "escalated"
        conv.escalated_at = conv.escalated_at or utcnow()
        conv.escalation_reason = conv.escalation_reason or "agent_joined"
        conv.resolved_at = None
        add_message(db, conv, "system", "", kind="event", data={"event": "agent_joined", "name": user.full_name})
    conv.assigned_to = conv.assigned_to or user.id
    m = add_message(db, conv, "agent", text[:MAX_MESSAGE_CHARS], author_id=user.id)
    audit(
        db, "conversation.replied", actor_id=user.id, workspace_id=conv.workspace_id, target_type="conversation", target_id=conv.id, ip=ip
    )
    await db.commit()
    # The visitor may have left: when we have their email (from the chat or a submitted form), send it there too.
    from app.services.mail import outbound

    to = conv.visitor_email or (ticket.requester_email if ticket is not None else None)
    if to and conv.channel == "widget":
        sent = await outbound.email_chat_reply(db, conv, ticket, user, text[:MAX_MESSAGE_CHARS], to=to)
        if sent is not None:
            await db.refresh(m)
            m.data = {**(m.data or {}), "emailed_to": to, "email_message_id": str(sent.id)}
            await db.commit()
    return m


async def email_reply(db: AsyncSession, conv: Conversation, text: str, *, email_message_id: uuid.UUID) -> object | None:
    """The visitor answered one of our emails: their reply joins the conversation (and its open ticket).

    The AI never answers email; the conversation goes to the team. Returns the ticket it landed on, if any.
    """
    from app.services import tickets as tk

    if conv.status == "resolved":
        conv.status = "open"
        conv.resolved_at = None
    add_message(db, conv, "visitor", text[:MAX_MESSAGE_CHARS], data={"channel": "email", "email_message_id": str(email_message_id)})
    ticket = await tk.open_ticket_for(db, conv.id)
    if ticket is not None:
        await tk.customer_replied(db, ticket, text, channel="email", email_message_id=str(email_message_id))
    await db.commit()
    if conv.status != "escalated":
        await escalate(db, conv, reason="email_reply")
    return ticket


async def resolve(db: AsyncSession, conv: Conversation, user: User, *, ip: str | None = None) -> None:
    if conv.status == "resolved":
        return
    conv.status = "resolved"
    conv.resolved_at = utcnow()
    add_message(db, conv, "system", "", kind="event", data={"event": "resolved", "name": user.full_name})
    audit(
        db, "conversation.resolved", actor_id=user.id, workspace_id=conv.workspace_id, target_type="conversation", target_id=conv.id, ip=ip
    )
    await db.commit()
