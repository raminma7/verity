"""Conversations: visitor and member messages, AI answers, and the live handoff to people.

    open ──(escalate: visitor asks, no answer, AI unavailable; or an agent replies)──▶ escalated
    escalated ──(agent resolves)──▶ resolved ──(visitor writes again)──▶ open

While a conversation is escalated the AI never answers; visitor messages wait for an agent. `escalate()` is
the single entry point for handing a conversation to the team, so Phase 3 can extend it to open a ticket.
"""

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chatbot, Conversation, Membership, Message, Notification, User, Workspace
from app.models.base import utcnow
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


def public_message_out(m: Message, author: str | None = None) -> dict:
    """What a visitor sees: no models, traces or token counts."""
    return {
        "id": m.id,
        "role": m.role,
        "kind": m.kind,
        "content": m.content,
        "data": {k: v for k, v in (m.data or {}).items() if k in ("blocks", "event", "offer_escalation")},
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
    await db.commit()
    yield {"type": "message", "message": question}
    if conv.status == "escalated":
        yield {"type": "waiting"}
        return

    final = None
    async for ev in rag.answer(db, ws, text, persona=persona_for(bot, ws), history=history, operation=operation):
        if ev["type"] == "delta":
            yield ev
        else:
            final = ev
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


async def responders(db: AsyncSession, workspace_id: uuid.UUID) -> list[User]:
    rows = (
        await db.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == workspace_id))
    ).all()
    return [u for m, u in rows if "conversations.reply" in m.role.permissions and u.status == "active"]


async def escalate(
    db: AsyncSession,
    conv: Conversation,
    *,
    reason: str,
    email: str | None = None,
    name: str | None = None,
    actor_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> bool:
    """Hands the conversation to the team. Returns False if people already have it."""
    if email:
        conv.visitor_email = email.strip().lower()[:320]
    if name:
        conv.visitor_name = " ".join(name.split())[:120]
    if conv.status == "escalated":
        await db.commit()
        return False
    conv.status = "escalated"
    conv.escalated_at = utcnow()
    conv.escalation_reason = reason
    conv.resolved_at = None
    add_message(db, conv, "system", "", kind="event", data={"event": "escalated", "reason": reason})
    ws = await db.get(Workspace, conv.workspace_id)
    bot = await db.get(Chatbot, conv.chatbot_id) if conv.chatbot_id else None
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
    await db.commit()
    for u in await responders(db, ws.id):
        if u.notification_prefs.get("escalations", True):
            await mail.send_escalation(u.email, ws.name, bot.name if bot else "the assistant", conv.title, str(conv.id))
    return True


async def agent_reply(db: AsyncSession, conv: Conversation, user: User, text: str, *, ip: str | None = None) -> Message:
    """A person answers. Replying takes the conversation over: the AI stays quiet until it's resolved."""
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
    return m


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
