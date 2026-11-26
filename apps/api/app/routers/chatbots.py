"""Chatbot settings, conversation history, live handoff replies, and the dashboard's Ask screen.

The chatbot_domains plan limit counts authorized domains across all of a workspace's chatbots and is
checked under a workspace lock.
"""

import json
import re
import secrets
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.models import Chatbot, Conversation, Message, User
from app.services import conversations as convs
from app.services.audit import audit
from app.services.storage import get_storage
from app.services.subscriptions import effective_limits

router = APIRouter(tags=["chatbots"])

READ = require("chatbots.read", subscription=True)
MANAGE = require("chatbots.manage", subscription=True)
REPLY = require("conversations.reply", subscription=True)
ASK = require("knowledge.read", subscription=True)

LANGUAGES = {
    "auto",
    "en",
    "de",
    "fr",
    "es",
    "it",
    "nl",
    "pt",
    "sv",
    "da",
    "nb",
    "fi",
    "pl",
    "cs",
    "tr",
    "fa",
    "ar",
    "he",
    "hi",
    "ja",
    "ko",
    "zh",
}
LOGO_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}
HOST = re.compile(r"^(\*\.)?(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), separators=(',', ':'))}\n\n"


def normalize_domain(raw: str) -> str:
    d = raw.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split("?")[0]
    d = d.split(":")[0] if not d.startswith("[") else d
    d = d.rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if not HOST.match(d) or ("." not in d and d != "localhost"):
        raise ValueError(f"“{raw.strip()}” isn't a domain. Use a host name like shop.example.com or *.example.com.")
    return d


# ---- Schemas ------------------------------------------------------------------------------------


class AppearanceIn(BaseModel):
    primary_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    position: str = Field(pattern="^(left|right)$")
    launcher_label: str = Field(min_length=1, max_length=30)
    theme: str = Field(default="light", pattern="^(light|dark|auto)$")


class EscalationIn(BaseModel):
    enabled: bool = True
    ask_email: bool = True


class ChatbotIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ChatbotUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_domains: list[str] | None = Field(default=None, max_length=50)
    appearance: AppearanceIn | None = None
    language: str | None = None
    tone: str | None = Field(default=None, pattern="^(friendly|professional|concise)$")
    welcome_message: str | None = Field(default=None, max_length=500)
    instructions: str | None = Field(default=None, max_length=4000)
    fallback_message: str | None = Field(default=None, min_length=1, max_length=500)
    no_answer_message: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = None
    escalation: EscalationIn | None = None
    status: str | None = Field(default=None, pattern="^(active|paused)$")

    @field_validator("allowed_domains")
    @classmethod
    def domains(cls, v):
        if v is None:
            return v
        out = []
        for d in v:
            n = normalize_domain(d)
            if n not in out:
                out.append(n)
        return out

    @field_validator("language")
    @classmethod
    def lang(cls, v):
        if v is not None and v not in LANGUAGES:
            raise ValueError("Unsupported language.")
        return v

    @field_validator("model")
    @classmethod
    def model_ok(cls, v):
        if v is not None and v not in get_settings().chat_models:
            raise ValueError("Choose one of the available models.")
        return v


def bot_out(b: Chatbot) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "public_key": b.public_key,
        "allowed_domains": b.allowed_domains,
        "appearance": b.appearance,
        "logo_url": f"/api/widget/{b.public_key}/logo?v={b.logo_key.rsplit('/', 1)[-1][:8]}" if b.logo_key else None,
        "language": b.language,
        "tone": b.tone,
        "welcome_message": b.welcome_message,
        "instructions": b.instructions,
        "fallback_message": b.fallback_message,
        "no_answer_message": b.no_answer_message,
        "model": b.model,
        "escalation": b.escalation,
        "status": b.status,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
    }


async def _bot(db: AsyncSession, ctx: WorkspaceContext, bot_id: uuid.UUID) -> Chatbot:
    b = (await db.execute(select(Chatbot).where(Chatbot.id == bot_id, Chatbot.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if b is None:
        raise not_found("Chatbot")
    return b


def snippet(b: Chatbot) -> str:
    app = get_settings().app_url.rstrip("/")
    return f'<script src="{app}/widget.js" data-verity-key="{b.public_key}" async></script>'


# ---- Chatbots -----------------------------------------------------------------------------------


@router.get("/chatbots")
async def list_bots(db: Db, ctx: WorkspaceContext = READ):
    rows = (await db.execute(select(Chatbot).where(Chatbot.workspace_id == ctx.workspace.id).order_by(Chatbot.created_at))).scalars()
    limits = await effective_limits(db, ctx.workspace.id)
    bots = [bot_out(b) for b in rows]
    return {
        "chatbots": bots,
        "domains_used": sum(len(b["allowed_domains"]) for b in bots),
        "domains_limit": limits.get("chatbot_domains", 0),
        "models": get_settings().chat_models,
    }


@router.post("/chatbots", status_code=201)
async def create_bot(body: ChatbotIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    b = Chatbot(
        workspace_id=ctx.workspace.id,
        name=body.name.strip(),
        public_key="vb_" + secrets.token_urlsafe(18),
        model=get_settings().default_chat_model,
        allowed_domains=[],
    )
    db.add(b)
    await db.flush()
    audit(
        db,
        "chatbot.created",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="chatbot",
        target_id=b.id,
        ip=client_ip(request),
    )
    await db.commit()
    await db.refresh(b)
    return bot_out(b)


@router.get("/chatbots/{bot_id}")
async def get_bot(bot_id: uuid.UUID, db: Db, ctx: WorkspaceContext = READ):
    b = await _bot(db, ctx, bot_id)
    return {**bot_out(b), "snippet": snippet(b)}


@router.patch("/chatbots/{bot_id}")
async def update_bot(bot_id: uuid.UUID, body: ChatbotUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    b = await _bot(db, ctx, bot_id)
    changes = body.model_dump(exclude_none=True)
    if "allowed_domains" in changes:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"chatbots:{ctx.workspace.id}"})
        others = (
            await db.execute(select(Chatbot.allowed_domains).where(Chatbot.workspace_id == ctx.workspace.id, Chatbot.id != b.id))
        ).scalars()
        total = sum(len(d) for d in others) + len(changes["allowed_domains"])
        limit = int((await effective_limits(db, ctx.workspace.id)).get("chatbot_domains", 0))
        if limit >= 0 and total > limit and len(changes["allowed_domains"]) > len(b.allowed_domains):
            raise ApiError(
                402,
                "limit_reached",
                f"Your plan allows {limit} chatbot domain{'s' if limit != 1 else ''}. Upgrade to add more.",
                limit="chatbot_domains",
            )
    for k, v in changes.items():
        setattr(b, k, v.strip() if isinstance(v, str) and k in ("name",) else v)
    action = "chatbot.domains_changed" if set(changes) == {"allowed_domains"} else "chatbot.updated"
    audit(
        db,
        action,
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="chatbot",
        target_id=b.id,
        ip=client_ip(request),
        changes=sorted(changes),
        **({"domains": changes["allowed_domains"]} if "allowed_domains" in changes else {}),
    )
    await db.commit()
    await db.refresh(b)
    return {**bot_out(b), "snippet": snippet(b)}


@router.delete("/chatbots/{bot_id}", status_code=204)
async def delete_bot(bot_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    b = await _bot(db, ctx, bot_id)
    logo = b.logo_key
    audit(
        db,
        "chatbot.deleted",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="chatbot",
        target_id=b.id,
        ip=client_ip(request),
        name=b.name,
    )
    await db.delete(b)
    await db.commit()
    if logo:
        await get_storage().delete(logo)


@router.post("/chatbots/{bot_id}/logo")
async def upload_logo(bot_id: uuid.UUID, request: Request, db: Db, file: Annotated[UploadFile, File()], ctx: WorkspaceContext = MANAGE):
    b = await _bot(db, ctx, bot_id)
    data = await file.read(512 * 1024 + 1)
    if len(data) > 512 * 1024:
        raise ApiError(413, "file_too_large", "Logos can be up to 512 KB.")
    mime = next((m for sig, m in LOGO_TYPES.items() if data.startswith(sig)), None)
    if mime == "image/webp" and data[8:12] != b"WEBP":
        mime = None
    if mime is None:
        raise ApiError(422, "unsupported_type", "Upload a PNG, JPEG or WebP image.")
    old = b.logo_key
    b.logo_key = await get_storage().put(ctx.workspace.id, data, suffix="." + mime.split("/")[1])
    b.logo_mime = mime
    audit(
        db,
        "chatbot.updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="chatbot",
        target_id=b.id,
        ip=client_ip(request),
        changes=["logo"],
    )
    await db.commit()
    if old:
        await get_storage().delete(old)
    await db.refresh(b)
    return bot_out(b)


@router.delete("/chatbots/{bot_id}/logo")
async def delete_logo(bot_id: uuid.UUID, db: Db, ctx: WorkspaceContext = MANAGE):
    b = await _bot(db, ctx, bot_id)
    old, b.logo_key, b.logo_mime = b.logo_key, None, None
    await db.commit()
    if old:
        await get_storage().delete(old)
    await db.refresh(b)
    return bot_out(b)


# ---- Conversations ------------------------------------------------------------------------------


async def _conv(db: AsyncSession, ctx: WorkspaceContext, conv_id: uuid.UUID) -> Conversation:
    c = (
        await db.execute(select(Conversation).where(Conversation.id == conv_id, Conversation.workspace_id == ctx.workspace.id))
    ).scalar_one_or_none()
    if c is None or (c.channel == "dashboard" and c.user_id != ctx.user.id):
        raise not_found("Conversation")
    return c


@router.get("/conversations")
async def list_conversations(
    db: Db,
    status: str | None = None,
    chatbot_id: uuid.UUID | None = None,
    q: str | None = None,
    limit: int = 50,
    before: datetime | None = None,
    ctx: WorkspaceContext = READ,
):
    query = select(Conversation).where(Conversation.workspace_id == ctx.workspace.id, Conversation.channel == "widget")
    if status in ("open", "escalated", "resolved"):
        query = query.where(Conversation.status == status)
    if chatbot_id:
        query = query.where(Conversation.chatbot_id == chatbot_id)
    if q:
        like = f"%{q.strip()[:100].replace('%', r'\%').replace('_', r'\_')}%"
        query = query.where(or_(Conversation.title.ilike(like), Conversation.visitor_email.ilike(like)))
    if before:
        query = query.where(Conversation.last_message_at < before)
    rows = (
        (await db.execute(query.order_by(Conversation.last_message_at.desc().nulls_last()).limit(max(1, min(limit, 100))))).scalars().all()
    )
    waiting = (
        await db.execute(
            select(func.count()).where(
                Conversation.workspace_id == ctx.workspace.id, Conversation.channel == "widget", Conversation.status == "escalated"
            )
        )
    ).scalar_one()
    # Evidence badge per conversation: the confidence of its last AI answer.
    ids = [c.id for c in rows]
    last_conf: dict = {}
    if ids:
        last_conf = dict(
            (
                await db.execute(
                    select(Message.conversation_id, Message.confidence)
                    .where(Message.conversation_id.in_(ids), Message.role == "assistant")
                    .distinct(Message.conversation_id)
                    .order_by(Message.conversation_id, Message.created_at.desc())
                )
            ).all()
        )
    return {"items": [{**convs.conversation_out(c), "confidence": last_conf.get(c.id)} for c in rows], "waiting": waiting}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: uuid.UUID, db: Db, ctx: WorkspaceContext = READ):
    c = await _conv(db, ctx, conv_id)
    rows = (
        await db.execute(
            select(Message, User.full_name)
            .outerjoin(User, User.id == Message.author_id)
            .where(Message.conversation_id == c.id)
            .order_by(Message.created_at)
        )
    ).all()
    return {
        "conversation": convs.conversation_out(c),
        "messages": [{**convs.message_out(m), "author": name} for m, name in rows],
        "can_reply": ctx.can("conversations.reply"),
    }


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=convs.MAX_MESSAGE_CHARS)


@router.post("/conversations/{conv_id}/reply", status_code=201)
async def reply(conv_id: uuid.UUID, body: ReplyIn, request: Request, db: Db, ctx: WorkspaceContext = REPLY):
    c = await _conv(db, ctx, conv_id)
    if c.channel != "widget":
        raise ApiError(422, "not_a_customer_conversation", "Only customer conversations can be answered by people.")
    m = await convs.agent_reply(db, c, ctx.user, body.text.strip(), ip=client_ip(request))
    return {**convs.message_out(m), "author": ctx.user.full_name, "conversation": convs.conversation_out(c)}


@router.post("/conversations/{conv_id}/resolve")
async def resolve(conv_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = REPLY):
    c = await _conv(db, ctx, conv_id)
    await convs.resolve(db, c, ctx.user, ip=client_ip(request))
    return convs.conversation_out(c)


# ---- Ask (members test the knowledge base) -------------------------------------------------------


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=convs.MAX_MESSAGE_CHARS)
    conversation_id: uuid.UUID | None = None
    chatbot_id: uuid.UUID | None = None


@router.get("/ask/conversations")
async def my_ask_history(db: Db, ctx: WorkspaceContext = ASK):
    rows = (
        await db.execute(
            select(Conversation)
            .where(Conversation.workspace_id == ctx.workspace.id, Conversation.channel == "dashboard", Conversation.user_id == ctx.user.id)
            .order_by(Conversation.last_message_at.desc().nulls_last())
            .limit(50)
        )
    ).scalars()
    return [convs.conversation_out(c) for c in rows]


@router.post("/ask")
async def ask(body: AskIn, db: Db, ctx: WorkspaceContext = ASK):
    """Streams an answer as server-sent events: message (question saved), delta…, message (answer)."""
    if body.conversation_id:
        conv = await _conv(db, ctx, body.conversation_id)
        if conv.channel != "dashboard":
            raise not_found("Conversation")
    else:
        bot_id = None
        if body.chatbot_id:
            bot_id = (await _bot(db, ctx, body.chatbot_id)).id
        conv = Conversation(workspace_id=ctx.workspace.id, chatbot_id=bot_id, channel="dashboard", user_id=ctx.user.id, status="open")
        db.add(conv)
        await db.commit()
    conv_id, text_in = conv.id, body.text.strip()

    async def events():
        async with SessionLocal() as s:
            c = await s.get(Conversation, conv_id)
            yield sse("conversation", convs.conversation_out(c))
            async for ev in convs.ask(s, c, text_in, role="member", operation="kb_ask"):
                if ev["type"] == "delta":
                    yield sse("delta", {"text": ev["text"]})
                elif ev["type"] == "message":
                    yield sse("message", convs.message_out(ev["message"]))
            yield sse("end", {})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
