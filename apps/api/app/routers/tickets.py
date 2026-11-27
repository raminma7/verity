"""Ticket categories and tickets.

Categories: tickets.read to list, tickets.configure to change. Tickets: tickets.read to see, tickets.manage
to create, update, note and reply. Every query filters on ctx.workspace.id; IDs from another workspace
answer 404.
"""

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.models import (
    TICKET_PRIORITIES,
    TICKET_STATUSES,
    Conversation,
    EmailAttachment,
    EmailMessage,
    Form,
    Membership,
    Message,
    Ticket,
    TicketCategory,
    TicketEvent,
    User,
)
from app.models.base import utcnow
from app.services import conversations as convs
from app.services import tickets as svc
from app.services.audit import audit
from app.services.mail import accounts as mail_accounts
from app.services.mail import outbound
from app.services.mail.attachments import attachment_out

router = APIRouter(tags=["tickets"])

READ = require("tickets.read", subscription=True)
MANAGE = require("tickets.manage", subscription=True)
CONFIGURE = require("tickets.configure", subscription=True)

PRIORITY = "^(" + "|".join(TICKET_PRIORITIES) + ")$"
STATUS = "^(" + "|".join(TICKET_STATUSES) + ")$"


# ---- Categories ---------------------------------------------------------------------------------


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)


class CategoryUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)


class OrderIn(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=500)


def category_out(c: TicketCategory, counts: dict | None = None) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "position": c.position,
        "archived": c.archived_at is not None,
        "archived_at": c.archived_at,
        "open_tickets": (counts or {}).get(c.id, 0),
        "created_at": c.created_at,
    }


async def _category(db: AsyncSession, ctx: WorkspaceContext, cat_id: uuid.UUID) -> TicketCategory:
    c = (
        await db.execute(select(TicketCategory).where(TicketCategory.id == cat_id, TicketCategory.workspace_id == ctx.workspace.id))
    ).scalar_one_or_none()
    if c is None:
        raise not_found("Category")
    return c


async def _name_free(db: AsyncSession, ws_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> None:
    q = select(TicketCategory.id).where(
        TicketCategory.workspace_id == ws_id, TicketCategory.archived_at.is_(None), func.lower(TicketCategory.name) == name.lower()
    )
    if exclude:
        q = q.where(TicketCategory.id != exclude)
    if (await db.execute(q)).first():
        raise ApiError(409, "category_name_taken", "A category with this name already exists.", field="name")


@router.get("/ticket-categories")
async def list_categories(db: Db, include_archived: bool = False, ctx: WorkspaceContext = READ):
    q = select(TicketCategory).where(TicketCategory.workspace_id == ctx.workspace.id)
    if not include_archived:
        q = q.where(TicketCategory.archived_at.is_(None))
    rows = (
        (await db.execute(q.order_by(TicketCategory.archived_at.is_not(None), TicketCategory.position, TicketCategory.created_at)))
        .scalars()
        .all()
    )
    counts = dict(
        (
            await db.execute(
                select(Ticket.category_id, func.count())
                .where(Ticket.workspace_id == ctx.workspace.id, Ticket.status.in_(("new", "open", "pending")))
                .group_by(Ticket.category_id)
            )
        ).all()
    )
    return [category_out(c, counts) for c in rows]


@router.post("/ticket-categories", status_code=201)
async def create_category(body: CategoryIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    name = " ".join(body.name.split())
    await _name_free(db, ctx.workspace.id, name)
    last = (await db.execute(select(func.max(TicketCategory.position)).where(TicketCategory.workspace_id == ctx.workspace.id))).scalar_one()
    c = TicketCategory(workspace_id=ctx.workspace.id, name=name, description=body.description.strip(), position=(last or 0) + 1)
    db.add(c)
    await db.flush()
    audit(
        db,
        "ticket_category.created",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="ticket_category",
        target_id=c.id,
        ip=client_ip(request),
        name=name,
    )
    await db.commit()
    return category_out(c)


@router.patch("/ticket-categories/{cat_id}")
async def update_category(cat_id: uuid.UUID, body: CategoryUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    c = await _category(db, ctx, cat_id)
    changes = {}
    if body.name is not None:
        name = " ".join(body.name.split())
        if c.archived_at is None:
            await _name_free(db, ctx.workspace.id, name, exclude=c.id)
        changes["name"] = {"from": c.name, "to": name}
        c.name = name
    if body.description is not None:
        c.description = body.description.strip()
        changes["description"] = True
    audit(
        db,
        "ticket_category.updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="ticket_category",
        target_id=c.id,
        ip=client_ip(request),
        **changes,
    )
    await db.commit()
    return category_out(c)


@router.post("/ticket-categories/{cat_id}/archive")
async def archive_category(cat_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    c = await _category(db, ctx, cat_id)
    if c.archived_at is None:
        using = (
            (await db.execute(select(Form.name).where(Form.category_id == c.id, Form.status != "archived").order_by(Form.name)))
            .scalars()
            .all()
        )
        if using:
            raise ApiError(
                409,
                "category_in_use",
                f"Forms still create tickets in this category: {', '.join(using)}. Move them to another category first.",
                forms=list(using),
            )
        c.archived_at = utcnow()
        audit(
            db,
            "ticket_category.archived",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="ticket_category",
            target_id=c.id,
            ip=client_ip(request),
        )
        await db.commit()
    return category_out(c)


@router.post("/ticket-categories/{cat_id}/restore")
async def restore_category(cat_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    c = await _category(db, ctx, cat_id)
    if c.archived_at is not None:
        await _name_free(db, ctx.workspace.id, c.name, exclude=c.id)
        last = (
            await db.execute(
                select(func.max(TicketCategory.position)).where(
                    TicketCategory.workspace_id == ctx.workspace.id, TicketCategory.archived_at.is_(None)
                )
            )
        ).scalar_one()
        c.archived_at = None
        c.position = (last or 0) + 1
        audit(
            db,
            "ticket_category.restored",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="ticket_category",
            target_id=c.id,
            ip=client_ip(request),
        )
        await db.commit()
    return category_out(c)


@router.put("/ticket-categories/order")
async def order_categories(body: OrderIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    rows = (
        (
            await db.execute(
                select(TicketCategory).where(TicketCategory.workspace_id == ctx.workspace.id, TicketCategory.archived_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    by_id = {c.id: c for c in rows}
    if len(body.ids) != len(set(body.ids)) or set(body.ids) != set(by_id):
        raise ApiError(422, "invalid_order", "Send every active category exactly once, in the new order.", field="ids")
    for i, cid in enumerate(body.ids):
        by_id[cid].position = i + 1
    audit(db, "ticket_category.reordered", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, ip=client_ip(request))
    await db.commit()
    return [category_out(by_id[i]) for i in body.ids]


# ---- Tickets ------------------------------------------------------------------------------------


class TicketIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    category_id: uuid.UUID | None = None
    priority: str = Field(default="normal", pattern=PRIORITY)
    assignee_id: uuid.UUID | None = None
    requester_name: str | None = Field(default=None, max_length=120)
    requester_email: EmailStr | None = None


class TicketUpdateIn(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern=STATUS)
    priority: str | None = Field(default=None, pattern=PRIORITY)
    category_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    unassign: bool = False


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=convs.MAX_MESSAGE_CHARS)


async def _ticket(db: AsyncSession, ctx: WorkspaceContext, ticket_id: uuid.UUID) -> Ticket:
    t = (await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if t is None:
        raise not_found("Ticket")
    return t


async def _names(db: AsyncSession, tickets: list[Ticket]) -> tuple[dict, dict]:
    cat_ids = {t.category_id for t in tickets if t.category_id}
    user_ids = {t.assignee_id for t in tickets if t.assignee_id}
    cats = (
        dict((await db.execute(select(TicketCategory.id, TicketCategory.name).where(TicketCategory.id.in_(cat_ids)))).all())
        if cat_ids
        else {}
    )
    users = dict((await db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))).all()) if user_ids else {}
    return cats, users


async def _out(db: AsyncSession, t: Ticket) -> dict:
    cats, users = await _names(db, [t])
    return svc.ticket_out(t, category=cats.get(t.category_id), assignee=users.get(t.assignee_id))


def _tsquery(q: str) -> str | None:
    terms = re.findall(r"\w+", q.lower())[:12]
    return " & ".join(f"{t}:*" for t in terms) if terms else None


@router.get("/tickets")
async def list_tickets(
    db: Db,
    status: str | None = None,
    priority: str | None = None,
    category_id: uuid.UUID | None = None,
    assignee: str | None = None,
    source: str | None = None,
    q: str | None = None,
    before: datetime | None = None,
    limit: int = 50,
    ctx: WorkspaceContext = READ,
):
    """status: one status, or "active" (new, open, pending). assignee: "me", "none" or a member's id."""
    base = select(Ticket).where(Ticket.workspace_id == ctx.workspace.id)
    if status == "active":
        base = base.where(Ticket.status.in_(("new", "open", "pending")))
    elif status in TICKET_STATUSES:
        base = base.where(Ticket.status == status)
    if priority in TICKET_PRIORITIES:
        base = base.where(Ticket.priority == priority)
    if category_id:
        base = base.where(Ticket.category_id == category_id)
    if assignee == "me":
        base = base.where(Ticket.assignee_id == ctx.user.id)
    elif assignee == "none":
        base = base.where(Ticket.assignee_id.is_(None))
    elif assignee:
        try:
            base = base.where(Ticket.assignee_id == uuid.UUID(assignee))
        except ValueError:
            raise ApiError(422, "invalid_input", "Unknown assignee filter.", field="assignee") from None
    if source:
        base = base.where(Ticket.source == source)
    if q and q.strip():
        qs = q.strip()[:200]
        num = re.fullmatch(r"#?(\d{1,9})", qs)
        tsq = _tsquery(qs)
        conds = []
        if num:
            conds.append(Ticket.number == int(num.group(1)))
        if tsq:
            # Whole tokens (emails, order numbers) and word prefixes ("zipp" finds "zipper").
            conds.append(Ticket.search_vector.op("@@")(func.plainto_tsquery("simple", qs)))
            conds.append(Ticket.search_vector.op("@@")(func.to_tsquery("simple", tsq)))
        if conds:
            base = base.where(or_(*conds))
    page = base
    if before:
        page = page.where(Ticket.last_activity_at < before)
    rows = (await db.execute(page.order_by(Ticket.last_activity_at.desc().nulls_last()).limit(max(1, min(limit, 100))))).scalars().all()
    counts = dict(
        (await db.execute(select(Ticket.status, func.count()).where(Ticket.workspace_id == ctx.workspace.id).group_by(Ticket.status))).all()
    )
    cats, users = await _names(db, rows)
    return {
        "items": [svc.ticket_out(t, category=cats.get(t.category_id), assignee=users.get(t.assignee_id)) for t in rows],
        "counts": {s: counts.get(s, 0) for s in TICKET_STATUSES},
    }


@router.get("/tickets/assignees")
async def assignees(db: Db, ctx: WorkspaceContext = READ):
    """Members whose role can work tickets: the people a ticket can be assigned to."""
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == ctx.workspace.id)
            .order_by(User.full_name)
        )
    ).all()
    return [
        {"id": u.id, "name": u.full_name, "email": u.email}
        for m, u in rows
        if "tickets.manage" in m.role.permissions and u.status == "active"
    ]


@router.post("/tickets", status_code=201)
async def create_ticket(body: TicketIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    await svc.check_category(db, ctx.workspace.id, body.category_id)
    await svc.check_assignee(db, ctx.workspace.id, body.assignee_id)
    t = await svc.create(
        db,
        ctx.workspace.id,
        svc.TicketSpec(
            subject=body.subject,
            description=body.description,
            source="manual",
            category_id=body.category_id,
            priority=body.priority,
            assignee_id=body.assignee_id,
            requester_name=body.requester_name,
            requester_email=body.requester_email,
            created_by=ctx.user.id,
        ),
        ip=client_ip(request),
    )
    await db.commit()
    return await _out(db, t)


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: uuid.UUID, db: Db, ctx: WorkspaceContext = READ):
    t = await _ticket(db, ctx, ticket_id)
    events = (
        await db.execute(
            select(TicketEvent, User.full_name)
            .outerjoin(User, User.id == TicketEvent.author_id)
            .where(TicketEvent.ticket_id == t.id)
            .order_by(TicketEvent.created_at)
        )
    ).all()
    conversation = None
    messages: list[dict] = []
    if t.conversation_id:
        c = await db.get(Conversation, t.conversation_id)
        if c is not None and c.workspace_id == ctx.workspace.id:
            conversation = convs.conversation_out(c)
            rows = (
                await db.execute(
                    select(Message, User.full_name)
                    .outerjoin(User, User.id == Message.author_id)
                    .where(Message.conversation_id == c.id)
                    .order_by(Message.created_at)
                )
            ).all()
            messages = [{**convs.message_out(m), "author": name} for m, name in rows]
    labels = {f["key"]: f for f in t.form_snapshot or []}
    form_name = (await db.execute(select(Form.name).where(Form.id == t.form_id))).scalar_one_or_none() if t.form_id else None
    return {
        "ticket": await _out(db, t),
        "form": {
            "name": form_name,
            "version": t.form_version,
            "fields": [
                {"key": f["key"], "label": f["label"], "type": f["type"], "value": t.form_data.get(f["key"])}
                for f in t.form_snapshot or []
                if f["key"] in t.form_data
            ]
            + [{"key": k, "label": k, "type": "text", "value": v} for k, v in t.form_data.items() if k not in labels],
        }
        if t.form_snapshot or t.form_data
        else None,
        "events": [svc.event_out(e, name) for e, name in events],
        "conversation": conversation,
        "messages": messages,
        "emails": await _emails(db, t),
        "reply_channel": await _reply_channel(db, ctx, t, conversation),
        "can_manage": ctx.can("tickets.manage"),
        "can_reply": ctx.can("tickets.manage") and (await _reply_channel(db, ctx, t, conversation)) is not None,
    }


async def _emails(db: AsyncSession, t: Ticket) -> list[dict]:
    """The ticket's email thread: what the customer sent and what went out, with delivery state and attachments."""
    rows = (
        (
            await db.execute(
                select(EmailMessage)
                .where(EmailMessage.ticket_id == t.id, EmailMessage.workspace_id == t.workspace_id, EmailMessage.status != "deleted")
                .order_by(EmailMessage.created_at)
                # Delivery state is written by the send job in its own session: always read it fresh.
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    atts: dict = {}
    if rows:
        for a in (
            (await db.execute(select(EmailAttachment).where(EmailAttachment.email_message_id.in_([r.id for r in rows])))).scalars().all()
        ):
            atts.setdefault(a.email_message_id, []).append(attachment_out(a))
    return [
        {
            "id": r.id,
            "direction": r.direction,
            "status": r.status,
            "kind": r.kind_out,
            "from_addr": r.from_addr,
            "from_name": r.from_name,
            "to": r.to,
            "cc": r.cc,
            "subject": r.subject,
            "text": r.text_body,
            "html": r.html_body,
            "error": r.error,
            "event_id": r.ticket_event_id,
            "attachments": atts.get(r.id, []),
            "via_platform": r.direction == "out" and r.account_id is None,
            "created_at": r.created_at,
            "sent_at": r.sent_at,
            "received_at": r.received_at,
        }
        for r in rows
    ]


async def _reply_channel(db: AsyncSession, ctx: WorkspaceContext, t: Ticket, conversation: dict | None) -> dict | None:
    """Where an agent's reply goes: the ticket's chat, or email in the original thread."""
    if conversation is not None and conversation["channel"] == "widget":
        return {"kind": "chat", "email": conversation.get("visitor_email") or t.requester_email}
    if t.source == "email" or t.email_account_id:
        if not t.requester_email:
            return None
        acct = await mail_accounts.sending_account(db, ctx.workspace, t.email_account_id)
        return {"kind": "email", "to": t.requester_email, "from": acct.address if acct else None}
    return None


@router.patch("/tickets/{ticket_id}")
async def update_ticket(ticket_id: uuid.UUID, body: TicketUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    t = await _ticket(db, ctx, ticket_id)
    changes: dict = {}
    if body.subject is not None and body.subject.strip() != t.subject:
        changes["subject"] = {"from": t.subject, "to": body.subject.strip()}
        t.subject = " ".join(body.subject.split())
    if body.priority is not None and body.priority != t.priority:
        changes["priority"] = {"from": t.priority, "to": body.priority}
        svc.add_event(db, t, "priority", author_id=ctx.user.id, **changes["priority"])
        t.priority = body.priority
    if "category_id" in body.model_fields_set and body.category_id != t.category_id:
        await svc.check_category(db, ctx.workspace.id, body.category_id)
        changes["category_id"] = {"from": t.category_id, "to": body.category_id}
        svc.add_event(db, t, "category", author_id=ctx.user.id, **{"from": t.category_id, "to": body.category_id})
        t.category_id = body.category_id
    if body.unassign or ("assignee_id" in body.model_fields_set and body.assignee_id != t.assignee_id):
        new = None if body.unassign else body.assignee_id
        if new != t.assignee_id:
            await svc.check_assignee(db, ctx.workspace.id, new)
            changes["assignee_id"] = {"from": t.assignee_id, "to": new}
            svc.add_event(db, t, "assignment", author_id=ctx.user.id, **{"from": t.assignee_id, "to": new})
            t.assignee_id = new
            if new and new != ctx.user.id:
                svc.notify_assignee(db, t, new)
            if new and t.status == "new" and body.status is None:
                svc.set_status(db, t, "open", actor_id=ctx.user.id)
    resolved_conv = False
    if body.status is not None and body.status != t.status:
        before = t.status
        svc.set_status(db, t, body.status, actor_id=ctx.user.id)
        changes["status"] = {"from": before, "to": body.status}
        resolved_conv = body.status in ("resolved", "closed")
    if changes:
        t.search_text = svc.search_text(t)
        audit(
            db,
            "ticket.updated",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="ticket",
            target_id=t.id,
            ip=client_ip(request),
            **changes,
        )
    await db.commit()
    if resolved_conv and t.conversation_id:
        # Resolving the ticket hands its live conversation back to the assistant.
        c = await db.get(Conversation, t.conversation_id)
        if c is not None and c.status == "escalated":
            await convs.resolve(db, c, ctx.user, ip=client_ip(request))
    return await _out(db, t)


@router.post("/tickets/{ticket_id}/notes", status_code=201)
async def add_note(ticket_id: uuid.UUID, body: NoteIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    t = await _ticket(db, ctx, ticket_id)
    e = svc.add_event(db, t, "note", body.text.strip(), author_id=ctx.user.id, internal=True)
    audit(
        db,
        "ticket.note_added",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="ticket",
        target_id=t.id,
        ip=client_ip(request),
    )
    await db.commit()
    return svc.event_out(e, ctx.user.full_name)


@router.post("/tickets/{ticket_id}/reply", status_code=201)
async def reply(ticket_id: uuid.UUID, body: ReplyIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    """Answers the customer: in the ticket's chat (and by email when they left one), or by email in the thread."""
    t = await _ticket(db, ctx, ticket_id)
    c = await db.get(Conversation, t.conversation_id) if t.conversation_id else None
    chat = c is not None and c.workspace_id == ctx.workspace.id and c.channel == "widget"
    by_email = not chat and (t.source == "email" or t.email_account_id is not None) and bool(t.requester_email)
    if not chat and not by_email:
        raise ApiError(
            422,
            "ticket_reply_unavailable",
            "This ticket has no chat or email address to reply to. Add an internal note instead.",
        )
    if t.status == "closed":
        raise ApiError(422, "ticket_closed", "This ticket is closed. Reopen it to reply.")
    text_out = body.text.strip()
    if chat:
        await convs.agent_reply(db, c, ctx.user, text_out, ip=client_ip(request), ticket=t)
        return await get_ticket(ticket_id, db, ctx)
    e = svc.add_event(db, t, "agent_reply", text_out, author_id=ctx.user.id, channel="email", to=t.requester_email)
    if t.status == "new":
        svc.set_status(db, t, "open", actor_id=ctx.user.id)
    t.assignee_id = t.assignee_id or ctx.user.id
    audit(
        db,
        "ticket.replied",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="ticket",
        target_id=t.id,
        ip=client_ip(request),
    )
    await db.flush()
    parent = await outbound.thread_parent(db, ticket_id=t.id)
    account = await mail_accounts.sending_account(db, ctx.workspace, t.email_account_id)
    row = await outbound.queue(
        db,
        ctx.workspace,
        account=account,
        to=t.requester_email,
        subject=outbound.ticket_subject(t.subject, t.number),
        text=text_out,
        kind="agent_reply",
        parent=parent,
        ticket=t,
        author_id=ctx.user.id,
        ticket_event_id=e.id,
    )
    await db.refresh(e)
    e.data = {**e.data, "email_message_id": str(row.id)}
    await db.commit()
    return await get_ticket(ticket_id, db, ctx)


@router.post("/tickets/{ticket_id}/emails/{email_id}/retry")
async def retry_email(ticket_id: uuid.UUID, email_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    """Sends a failed email again."""
    t = await _ticket(db, ctx, ticket_id)
    row = (
        await db.execute(
            select(EmailMessage).where(
                EmailMessage.id == email_id, EmailMessage.ticket_id == t.id, EmailMessage.workspace_id == ctx.workspace.id
            )
        )
    ).scalar_one_or_none()
    if row is None or row.direction != "out":
        raise ApiError(404, "email_message_not_found", "This email isn't here any more.")
    if row.status == "failed":
        row.status, row.error = "queued", None
        audit(
            db,
            "ticket.email_retried",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="ticket",
            target_id=t.id,
            ip=client_ip(request),
            email_id=row.id,
        )
        await db.commit()
        from app.jobs import enqueue

        await enqueue("send_email_message", key=f"send:{row.id}:{row.attempts}", retries=4, email_id=str(row.id))
    return await get_ticket(ticket_id, db, ctx)
