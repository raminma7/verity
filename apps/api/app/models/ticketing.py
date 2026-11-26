"""Ticket categories, chatbot forms, tickets and their timeline (Phase 3).

A form belongs to a workspace, is mapped to one ticket category and is offered by the chatbots linked to it
(`chatbot_forms`). Submitting a form in the widget creates a ticket that keeps a snapshot of the form's
fields, the submitted values and a link to the conversation it came from.

    new ──▶ open ──▶ pending ──▶ resolved ──▶ closed
     └──────┴──────────┴───────────▲   │ (customer replies, if the workspace reopens on reply)
                                   └───┘──▶ open
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, Computed, DateTime, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

TICKET_STATUSES = ("new", "open", "pending", "resolved", "closed")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
# email arrives in Phase 4; the API refuses it as an input until then.
TICKET_SOURCES = ("chatbot_form", "conversation", "manual", "email")
FIELD_TYPES = ("text", "email", "number", "select", "multiselect", "date", "textarea", "checkbox")


class TicketCategory(IdMixin, TimestampMixin, Base):
    __tablename__ = "ticket_categories"
    __table_args__ = (Index("ix_ticket_categories_ws_position", "workspace_id", "position"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


chatbot_forms = Table(
    "chatbot_forms",
    Base.metadata,
    Column("chatbot_id", ForeignKey("chatbots.id", ondelete="CASCADE"), primary_key=True),
    Column("form_id", ForeignKey("forms.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class Form(IdMixin, TimestampMixin, Base):
    __tablename__ = "forms"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500), default="")
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ticket_categories.id"), index=True)
    # Plain-language condition the AI checks against the conversation ("the customer wants to return an order").
    activation_condition: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | active | archived
    # [{key, label, type, required, help, placeholder, options: [str], validation: {...}}]
    fields: Mapped[list] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    submit_label: Mapped[str] = mapped_column(String(40), default="Send")
    success_message: Mapped[str] = mapped_column(String(500), default="")
    # The built-in contact form offered when nobody replies to a handoff and no other form fits.
    is_contact: Mapped[bool] = mapped_column(Boolean, default=False)


class TicketCounter(Base):
    """Per-workspace ticket numbers (#1, #2, …), taken with one UPDATE … RETURNING so they never repeat."""

    __tablename__ = "ticket_counters"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class Ticket(IdMixin, TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "number"),
        Index("ix_tickets_ws_status_activity", "workspace_id", "status", "last_activity_at"),
        Index("ix_tickets_search", "search_vector", postgresql_using="gin"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ticket_categories.id"), index=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    form_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forms.id", ondelete="SET NULL"))
    form_version: Mapped[int | None] = mapped_column(Integer)
    # The form's fields as they were when the customer submitted, so later edits never change what was asked.
    form_snapshot: Mapped[list] = mapped_column(JSONB, default=list)
    form_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    requester_name: Mapped[str | None] = mapped_column(String(120))
    requester_email: Mapped[str | None] = mapped_column(String(320), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_count: Mapped[int] = mapped_column(Integer, default=0)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Subject, description, requester and form values; kept by services.tickets.
    search_text: Mapped[str] = mapped_column(Text, default="")
    search_vector = mapped_column(TSVECTOR, Computed("to_tsvector('simple', coalesce(search_text, ''))", persisted=True))


class TicketEvent(IdMixin, Base):
    """The ticket timeline: messages, internal notes and every change."""

    __tablename__ = "ticket_events"
    __table_args__ = (Index("ix_ticket_events_ticket_created", "ticket_id", "created_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    # created | customer_message | agent_reply | note | status | assignment | priority | category | reopened
    kind: Mapped[str] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
