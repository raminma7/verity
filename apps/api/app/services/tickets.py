"""Tickets: numbering, creation, the timeline, status changes and reopening on a customer reply.

Tickets come from chatbot forms and agent conversions (both through conversations.escalate()), and from
manual submissions in the dashboard. Email (Phase 4) will create them through `create()` with source "email".
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import Membership, Notification, Ticket, TicketCategory, TicketCounter, TicketEvent, User, Workspace
from app.models.base import utcnow
from app.services.audit import audit, jsonable
from app.services.forms import display_value

# Where each status may go. "new" is only ever the starting point.
TRANSITIONS: dict[str, set[str]] = {
    "new": {"open", "pending", "resolved", "closed"},
    "open": {"pending", "resolved", "closed"},
    "pending": {"open", "resolved", "closed"},
    "resolved": {"open", "closed"},
    "closed": {"open"},
}
STATUS_WORDS = {"new": "New", "open": "Open", "pending": "Pending", "resolved": "Resolved", "closed": "Closed"}


@dataclass
class TicketSpec:
    """What a new ticket is made of. `form` is set for chatbot-form tickets."""

    subject: str
    source: str
    category_id: uuid.UUID | None = None
    description: str = ""
    priority: str = "normal"
    assignee_id: uuid.UUID | None = None
    requester_name: str | None = None
    requester_email: str | None = None
    form: object | None = None  # app.models.Form
    form_fields: list | None = None  # the fields the customer saw (defaults to the form's current fields)
    form_version: int | None = None
    form_data: dict = field(default_factory=dict)
    created_by: uuid.UUID | None = None


async def next_number(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    await db.execute(
        text("INSERT INTO ticket_counters (workspace_id, last) VALUES (:ws, 0) ON CONFLICT (workspace_id) DO NOTHING"), {"ws": workspace_id}
    )
    return (
        await db.execute(
            update(TicketCounter)
            .where(TicketCounter.workspace_id == workspace_id)
            .values(last=TicketCounter.last + 1)
            .returning(TicketCounter.last)
        )
    ).scalar_one()


def search_text(t: Ticket) -> str:
    labels = {f["key"]: f for f in t.form_snapshot or []}
    values = " ".join(display_value(labels.get(k), v) for k, v in (t.form_data or {}).items())
    parts = [f"#{t.number}", str(t.number), t.subject, t.description, t.requester_name or "", t.requester_email or "", values]
    return " ".join(p for p in parts if p)[:20000]


def add_event(db: AsyncSession, t: Ticket, kind: str, body: str = "", *, author_id=None, internal: bool = False, **data) -> TicketEvent:
    now = utcnow()
    e = TicketEvent(
        workspace_id=t.workspace_id,
        ticket_id=t.id,
        kind=kind,
        body=body,
        data=jsonable(data),
        internal=internal,
        author_id=author_id,
        created_at=now,
    )
    db.add(e)
    t.last_activity_at = now
    return e


async def check_category(db: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID | None) -> TicketCategory | None:
    if category_id is None:
        return None
    c = (
        await db.execute(select(TicketCategory).where(TicketCategory.id == category_id, TicketCategory.workspace_id == workspace_id))
    ).scalar_one_or_none()
    if c is None:
        raise ApiError(404, "category_not_found", "Category not found.", field="category_id")
    if c.archived_at is not None:
        raise ApiError(422, "category_archived", "This category is archived. Choose another one.", field="category_id")
    return c


async def check_assignee(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID | None) -> User | None:
    """Only members whose role can work tickets can be assigned. Anyone else is reported as not found."""
    if user_id is None:
        return None
    row = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == workspace_id, Membership.user_id == user_id)
        )
    ).first()
    if row is None:
        raise ApiError(404, "assignee_not_found", "That person isn't a member of this workspace.", field="assignee_id")
    m, u = row
    if "tickets.manage" not in m.role.permissions or u.status != "active":
        raise ApiError(422, "assignee_not_allowed", "That member's role can't work tickets.", field="assignee_id")
    return u


async def create(db: AsyncSession, workspace_id: uuid.UUID, spec: TicketSpec, *, ip: str | None = None) -> Ticket:
    """Adds the ticket and its "created" event to the current transaction (the caller commits)."""
    form = spec.form
    t = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        number=await next_number(db, workspace_id),
        subject=" ".join(spec.subject.split())[:200] or "New request",
        description=spec.description.strip()[:20000],
        status="new",
        priority=spec.priority,
        category_id=spec.category_id,
        assignee_id=spec.assignee_id,
        source=spec.source,
        form_id=form.id if form else None,
        form_version=(spec.form_version or form.version) if form else None,
        form_snapshot=list(spec.form_fields if spec.form_fields is not None else form.fields) if form else [],
        form_data=spec.form_data,
        requester_name=(" ".join(spec.requester_name.split())[:120] or None) if spec.requester_name else None,
        requester_email=spec.requester_email.strip().lower()[:320] if spec.requester_email else None,
        created_by=spec.created_by,
        last_activity_at=utcnow(),
    )
    if spec.assignee_id:
        t.status = "open"
    t.search_text = search_text(t)
    db.add(t)
    await db.flush()
    add_event(db, t, "created", author_id=spec.created_by, source=spec.source, form=form.name if form else None)
    audit(
        db,
        "ticket.created",
        actor_id=spec.created_by,
        workspace_id=workspace_id,
        target_type="ticket",
        target_id=t.id,
        ip=ip,
        number=t.number,
        source=t.source,
        category_id=t.category_id,
    )
    if spec.assignee_id and spec.assignee_id != spec.created_by:
        notify_assignee(db, t, spec.assignee_id)
    return t


def notify_created(db: AsyncSession, t: Ticket, *, conversation_id: uuid.UUID | None = None) -> None:
    """The in-app notification for a new ticket (everyone in the workspace who works tickets sees it)."""
    data = {"ticket_id": str(t.id)}
    if conversation_id:
        data["conversation_id"] = str(conversation_id)
    db.add(
        Notification(
            workspace_id=t.workspace_id,
            kind="ticket_created",
            title=f"New ticket #{t.number}",
            body=t.subject[:300],
            data=data,
            created_at=utcnow(),
        )
    )


def notify_assignee(db: AsyncSession, t: Ticket, user_id: uuid.UUID, *, title: str | None = None) -> None:
    db.add(
        Notification(
            workspace_id=t.workspace_id,
            user_id=user_id,
            kind="ticket_assigned",
            title=title or f"Ticket #{t.number} is assigned to you",
            body=t.subject[:300],
            data={"ticket_id": str(t.id)},
            created_at=utcnow(),
        )
    )


def set_status(db: AsyncSession, t: Ticket, status: str, *, actor_id=None) -> bool:
    """Moves the ticket if the transition is allowed. Returns False when it's already there."""
    if status == t.status:
        return False
    if status not in TRANSITIONS.get(t.status, set()):
        raise ApiError(
            422,
            "invalid_status_transition",
            f"A {STATUS_WORDS[t.status].lower()} ticket can't move to {STATUS_WORDS.get(status, status).lower()}.",
            field="status",
        )
    before = t.status
    now = utcnow()
    t.status = status
    if status == "resolved":
        t.resolved_at = now
    elif status == "closed":
        t.closed_at = now
        t.resolved_at = t.resolved_at or now
    else:
        t.resolved_at = None
        t.closed_at = None
    add_event(db, t, "status", author_id=actor_id, **{"from": before, "to": status})
    return True


async def customer_replied(db: AsyncSession, t: Ticket, text_in: str, **data) -> bool:
    """A customer wrote again. Adds the message to the timeline; reopens per the workspace's setting.

    Pending → Open always (the team was waiting for this). Resolved → Open only when the workspace reopens on
    reply. Closed tickets stay closed. Returns True if the ticket was reopened.
    """
    add_event(db, t, "customer_message", text_in[:4000], **data)
    if t.status == "pending":
        set_status(db, t, "open")
        return False
    if t.status != "resolved":
        return False
    ws = await db.get(Workspace, t.workspace_id)
    if not (ws.settings or {}).get("reopen_on_reply", True):
        return False
    t.status = "open"
    t.resolved_at = None
    t.reopened_count += 1
    add_event(db, t, "reopened", reason="customer_reply")
    audit(db, "ticket.reopened", workspace_id=t.workspace_id, target_type="ticket", target_id=t.id, reason="customer_reply")
    if t.assignee_id:
        notify_assignee(db, t, t.assignee_id, title=f"Ticket #{t.number} was reopened by the customer")
    return True


async def open_ticket_for(db: AsyncSession, conversation_id: uuid.UUID) -> Ticket | None:
    """The conversation's most recent ticket that isn't closed."""
    return (
        await db.execute(
            select(Ticket)
            .where(Ticket.conversation_id == conversation_id, Ticket.status != "closed")
            .order_by(Ticket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def ticket_out(t: Ticket, *, category: str | None = None, assignee: str | None = None) -> dict:
    return {
        "id": t.id,
        "number": t.number,
        "subject": t.subject,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "category_id": t.category_id,
        "category": category,
        "assignee_id": t.assignee_id,
        "assignee": assignee,
        "source": t.source,
        "form_id": t.form_id,
        "form_version": t.form_version,
        "conversation_id": t.conversation_id,
        "requester_name": t.requester_name,
        "requester_email": t.requester_email,
        "reopened_count": t.reopened_count,
        "resolved_at": t.resolved_at,
        "closed_at": t.closed_at,
        "last_activity_at": t.last_activity_at,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def event_out(e: TicketEvent, author: str | None = None) -> dict:
    return {
        "id": e.id,
        "kind": e.kind,
        "body": e.body,
        "data": e.data,
        "internal": e.internal,
        "author_id": e.author_id,
        "author": author,
        "created_at": e.created_at,
    }
