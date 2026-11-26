"""The public chatbot widget API, used by the iframe that widget.js embeds on a customer's site.

Domain restriction, in two layers:
1. The browser: the widget page is served with `Content-Security-Policy: frame-ancestors <allowed domains>`
   (see /frame-policy), so it only renders inside the chatbot's authorized sites.
2. This API: every call names the embedding page's origin (X-Verity-Parent-Origin, which the iframe takes
   from its own embedding context) and is refused unless it matches an authorized domain.

Visitors are anonymous. Each conversation gets a random token (only its hash is stored, like sessions);
the iframe keeps it and sends it as X-Visitor-Token. Requests are rate limited per visitor, IP and chatbot.
"""

import secrets
import uuid
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import Db, client_ip
from app.core.errors import ApiError, not_found
from app.core.security import hash_token
from app.models import Chatbot, Conversation, Form, Message, TicketCategory, User, Workspace
from app.routers.chatbots import sse
from app.services import conversations as convs
from app.services import forms as fsvc
from app.services import forms_ai
from app.services import tickets as tk
from app.services.quota import ai_available
from app.services.storage import get_storage
from app.services.subscriptions import get_subscription, is_active

router = APIRouter(prefix="/widget/{key}", tags=["widget"])


def domain_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    bare = host[4:] if host.startswith("www.") else host
    for d in allowed:
        if d.startswith("*."):
            root = d[2:]
            if host == root or host.endswith("." + root):
                return True
        elif bare == d:
            return True
    return False


def origin_host(origin: str | None) -> str | None:
    if not origin:
        return None
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1") and not get_settings().is_dev:
        return None
    return parts.hostname


async def load_bot(db, key: str) -> tuple[Chatbot, Workspace]:
    bot = (await db.execute(select(Chatbot).where(Chatbot.public_key == key))).scalar_one_or_none()
    if bot is None:
        raise not_found("Chatbot")
    ws = await db.get(Workspace, bot.workspace_id)
    return bot, ws


class WidgetContext:
    def __init__(self, bot: Chatbot, ws: Workspace, origin: str, host: str):
        self.bot, self.ws, self.origin, self.host = bot, ws, origin, host


async def widget_context(
    key: str,
    request: Request,
    db: Db,
    x_verity_parent_origin: Annotated[str | None, Header()] = None,
) -> WidgetContext:
    bot, ws = await load_bot(db, key)
    host = origin_host(x_verity_parent_origin)
    if host is None or not domain_allowed(host, bot.allowed_domains):
        raise ApiError(403, "domain_not_allowed", "This chatbot isn't enabled on this website.")
    if bot.status != "active" or ws.status != "active":
        raise ApiError(403, "chatbot_unavailable", "This chatbot is switched off.")
    s = get_settings()
    await ratelimit.hit(f"widget:bot:{bot.id}", s.rl_widget_bot)
    await ratelimit.hit(f"widget:ip:{client_ip(request)}", s.rl_widget_ip)
    return WidgetContext(bot, ws, x_verity_parent_origin.strip()[:300], host)


W = Annotated[WidgetContext, Depends(widget_context)]


async def visitor_conversation(db, w: WidgetContext, conv_id: uuid.UUID, token: str | None) -> Conversation:
    c = (
        await db.execute(
            select(Conversation).where(Conversation.id == conv_id, Conversation.chatbot_id == w.bot.id, Conversation.channel == "widget")
        )
    ).scalar_one_or_none()
    if c is None or not token or not secrets.compare_digest(c.visitor_token_hash or "", hash_token(token)):
        raise not_found("Conversation")
    return c


# ---- Public -------------------------------------------------------------------------------------


@router.get("/frame-policy")
async def frame_policy(key: str, db: Db):
    """Used by the web app to set frame-ancestors on the widget page. Unknown keys get an empty list."""
    bot = (await db.execute(select(Chatbot).where(Chatbot.public_key == key))).scalar_one_or_none()
    if bot is None or bot.status != "active":
        return {"ancestors": []}
    out = []
    dev = get_settings().is_dev
    for d in bot.allowed_domains:
        hosts = [d, d[2:]] if d.startswith("*.") else [d, f"www.{d}"]
        for h in hosts:
            out.append(f"https://{h}")
            if dev:
                out.append(f"http://{h}")
        if d == "localhost":
            out += ["http://localhost:*", "https://localhost:*"]
    return {"ancestors": sorted(set(out))}


@router.get("/logo")
async def logo(key: str, db: Db):
    bot = (await db.execute(select(Chatbot).where(Chatbot.public_key == key))).scalar_one_or_none()
    if bot is None or not bot.logo_key:
        raise not_found("Logo")
    data = await get_storage().get(bot.logo_key)
    return Response(data, media_type=bot.logo_mime or "image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/config")
async def config(w: W, db: Db):
    sub = await get_subscription(db, w.ws.id)
    b = w.bot
    return {
        "name": b.name,
        "workspace": w.ws.name,
        "appearance": b.appearance,
        "logo_url": f"/api/widget/{b.public_key}/logo?v={b.logo_key.rsplit('/', 1)[-1][:8]}" if b.logo_key else None,
        "language": b.language,
        "welcome_message": b.welcome_message,
        "escalation": b.escalation,
        "ai_available": is_active(sub) and await ai_available(db, w.ws.id),
        "fallback_message": b.fallback_message,
    }


class StartIn(BaseModel):
    page_url: str | None = Field(default=None, max_length=2000)


@router.post("/conversations", status_code=201)
async def start(body: StartIn, w: W, db: Db):
    token = secrets.token_urlsafe(32)
    c = Conversation(
        workspace_id=w.ws.id,
        chatbot_id=w.bot.id,
        channel="widget",
        visitor_token_hash=hash_token(token),
        origin=w.origin,
        page_url=(body.page_url or "")[:2000] or None,
        status="open",
    )
    db.add(c)
    await db.commit()
    return {"conversation_id": c.id, "visitor_token": token, "status": c.status}


async def _public_messages(db, c: Conversation, after: datetime | None = None) -> list[dict]:
    q = (
        select(Message, User.full_name)
        .outerjoin(User, User.id == Message.author_id)
        .where(Message.conversation_id == c.id)
        .order_by(Message.created_at)
    )
    if after:
        q = q.where(Message.created_at > after)
    return [convs.public_message_out(m, name.split()[0] if name else None) for m, name in (await db.execute(q)).all()]


@router.get("/conversations/{conv_id}")
async def poll(conv_id: uuid.UUID, w: W, db: Db, after: datetime | None = None, x_visitor_token: Annotated[str | None, Header()] = None):
    """The widget polls this every few seconds while a person has the conversation."""
    c = await visitor_conversation(db, w, conv_id, x_visitor_token)
    open_ticket = await tk.open_ticket_for(db, c.id)
    return {"status": c.status, "has_open_ticket": open_ticket is not None, "messages": await _public_messages(db, c, after)}


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=convs.MAX_MESSAGE_CHARS)


@router.post("/conversations/{conv_id}/messages")
async def send(conv_id: uuid.UUID, body: MessageIn, w: W, db: Db, x_visitor_token: Annotated[str | None, Header()] = None):
    """Server-sent events: message (the visitor's), delta… and message (the answer), or waiting."""
    c = await visitor_conversation(db, w, conv_id, x_visitor_token)
    await ratelimit.hit(f"widget:visitor:{c.id}", get_settings().rl_widget_message)
    text_in = body.text.strip()
    if not text_in:
        raise ApiError(422, "invalid_input", "Type a message.", field="text")

    async def events():
        async with SessionLocal() as s:
            conv = await s.get(Conversation, conv_id)
            async for ev in convs.ask(s, conv, text_in, role="visitor", operation="chat_response"):
                if ev["type"] == "delta":
                    yield sse("delta", {"text": ev["text"]})
                elif ev["type"] == "waiting":
                    yield sse("waiting", {"status": "escalated"})
                else:
                    yield sse("message", convs.public_message_out(ev["message"]))
            yield sse("end", {"status": conv.status})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class EscalateIn(BaseModel):
    email: EmailStr | None = None
    name: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="visitor_request", pattern="^(visitor_request|no_answer|ai_unavailable)$")


@router.post("/conversations/{conv_id}/escalate")
async def escalate(
    conv_id: uuid.UUID, body: EscalateIn, request: Request, w: W, db: Db, x_visitor_token: Annotated[str | None, Header()] = None
):
    c = await visitor_conversation(db, w, conv_id, x_visitor_token)
    esc = w.bot.escalation or {}
    if not esc.get("enabled", True):
        raise ApiError(403, "escalation_disabled", "Talking to a person isn't available here.")
    # With the ticket fallback on, the email is asked only if nobody replies in time (in the offered form).
    if esc.get("ask_email", True) and not esc.get("ticket_fallback", True) and not (body.email or c.visitor_email):
        raise ApiError(422, "email_required", "Leave your email so the team can reach you.", field="email")
    await convs.escalate(db, c, reason=body.reason, email=body.email, name=body.name, ip=client_ip(request))
    return {"status": c.status, "messages": await _public_messages(db, c)}


# ---- Forms ----------------------------------------------------------------------------------------


async def _offered_form(db, c: Conversation, message_id: uuid.UUID, *, lock: bool = False) -> Message:
    q = select(Message).where(Message.id == message_id, Message.conversation_id == c.id, Message.kind == "form")
    m = (await db.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "form_not_offered", "This form isn't part of the conversation.")
    return m


@router.get("/forms")
async def list_forms(w: W, db: Db):
    """Forms the visitor can open themselves ("Leave a request")."""
    forms = [f for f in await forms_ai.bot_forms(db, w.bot) if not f.is_contact]
    if (w.bot.escalation or {}).get("ticket_fallback", True):
        forms.append(await forms_ai.fallback_form(db, w.bot))
        await db.commit()
    seen, out = set(), []
    for f in forms:
        if f.id not in seen:
            seen.add(f.id)
            out.append({"id": f.id, "title": f.name, "description": f.description})
    return out


class ChooseIn(BaseModel):
    form_id: uuid.UUID


@router.post("/conversations/{conv_id}/forms", status_code=201)
async def choose_form(conv_id: uuid.UUID, body: ChooseIn, w: W, db: Db, x_visitor_token: Annotated[str | None, Header()] = None):
    c = await visitor_conversation(db, w, conv_id, x_visitor_token)
    allowed = {str(x["id"]) for x in await list_forms(w, db)}
    if str(body.form_id) not in allowed:
        raise not_found("Form")
    pending = forms_ai.pending_offer(await forms_ai.form_messages(db, c))
    if pending is not None and pending.data["form"]["form_id"] == str(body.form_id):
        return convs.public_message_out(pending)
    if pending is not None:
        pending.data = {**pending.data, "state": "dismissed"}
    form = await db.get(Form, body.form_id)
    m = forms_ai.offer(db, c, form, reason="visitor_choice")
    await db.commit()
    return convs.public_message_out(m)


@router.post("/conversations/{conv_id}/forms/{message_id}/prefill")
async def prefill_form(conv_id: uuid.UUID, message_id: uuid.UUID, w: W, db: Db, x_visitor_token: Annotated[str | None, Header()] = None):
    """Suggested values for the visitor to review. Never submits anything."""
    c = await visitor_conversation(db, w, conv_id, x_visitor_token)
    await ratelimit.hit(f"widget:visitor:{c.id}", get_settings().rl_widget_message)
    m = await _offered_form(db, c, message_id)
    if m.data.get("state") != "offered":
        raise ApiError(409, "form_already_submitted" if m.data.get("state") == "submitted" else "form_not_offered", "This form is closed.")
    if "prefill" in m.data:
        return {"values": m.data["prefill"], "ai": m.data.get("prefill_ai", False)}
    values, used_ai = await forms_ai.prefill(db, c, m.data["form"]["fields"], keep=(m,))
    m.data = {**m.data, "prefill": values, "prefill_ai": used_ai}
    await db.commit()
    return {"values": values, "ai": used_ai}


class SubmitIn(BaseModel):
    values: dict = Field(default_factory=dict)
    confirmed: bool = False


@router.post("/conversations/{conv_id}/forms/{message_id}/submit")
async def submit_form(
    conv_id: uuid.UUID,
    message_id: uuid.UUID,
    body: SubmitIn,
    request: Request,
    w: W,
    db: Db,
    x_visitor_token: Annotated[str | None, Header()] = None,
):
    """The visitor reviewed the values and pressed submit: open the ticket. Submitting twice returns the same ticket."""
    c = await visitor_conversation(db, w, conv_id, x_visitor_token)
    await ratelimit.hit(f"widget:visitor:{c.id}", get_settings().rl_widget_message)
    if not body.confirmed:
        raise ApiError(422, "confirmation_required", "Check the details and confirm to send the form.")
    m = await _offered_form(db, c, message_id, lock=True)
    state = m.data.get("state")
    if state == "submitted":
        number = m.data.get("ticket_number")
        if number is None:
            t = await tk.open_ticket_for(db, c.id)
            number = t.number if t else None
        return {"ticket_number": number, "status": c.status, "messages": await _public_messages(db, c)}
    if state != "offered":
        raise ApiError(409, "form_not_offered", "This form is closed.")
    fields = m.data["form"]["fields"]
    clean, errors = fsvc.validate_values(fields, body.values)
    if errors:
        raise ApiError(422, "invalid_form", "Some fields need attention.", errors=errors)
    form = await db.get(Form, uuid.UUID(m.data["form"]["form_id"]))
    if form is None or form.workspace_id != w.ws.id or form.status != "active":
        raise ApiError(409, "form_unavailable", "This form isn't available any more.")
    category = await db.get(TicketCategory, form.category_id)
    if category is None or category.archived_at is not None:
        category = await fsvc.default_category(db, w.ws.id)
    email = next((clean[f["key"]] for f in fields if f["type"] == "email" and f["key"] in clean), None)
    name = clean.get("name") or clean.get("full_name") or None
    texts = [clean[f["key"]] for f in fields if f["type"] in ("text", "textarea") and f["key"] in clean and f["key"] != "name"]
    first_text = texts[0] if texts else ""
    subject = f"{form.name}: {' '.join(str(first_text).split())[:120]}" if first_text else form.name
    spec = tk.TicketSpec(
        subject=subject,
        source="chatbot_form",
        category_id=category.id,
        description=f"Submitted in the chat with {w.bot.name}.",
        requester_name=name,
        requester_email=email,
        form=form,
        # The fields as the visitor saw them, so the ticket shows exactly what was asked.
        form_fields=fields,
        form_version=m.data["form"].get("version"),
        form_data=clean,
    )
    # Marked submitted in the same transaction as the ticket, while the row is locked: a second submit waits,
    # then sees "submitted" and gets the same ticket back.
    m.data = {**m.data, "state": "submitted", "values": clean}
    result = await convs.escalate(
        db, c, reason="form_submitted", email=email, name=name, ip=client_ip(request), ticket=spec, live=False, hand_back=True
    )
    await db.refresh(m)
    t = result.ticket
    m.data = {**m.data, "ticket_number": t.number, "ticket_id": str(t.id)}
    await db.commit()
    await db.refresh(c)
    return {"ticket_number": t.number, "status": c.status, "messages": await _public_messages(db, c)}


@router.post("/conversations/{conv_id}/forms/{message_id}/dismiss")
async def dismiss_form(conv_id: uuid.UUID, message_id: uuid.UUID, w: W, db: Db, x_visitor_token: Annotated[str | None, Header()] = None):
    c = await visitor_conversation(db, w, conv_id, x_visitor_token)
    m = await _offered_form(db, c, message_id)
    if m.data.get("state") == "offered":
        m.data = {**m.data, "state": "dismissed"}
        await db.commit()
    return convs.public_message_out(m)
