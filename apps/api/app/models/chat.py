"""Chatbots, their conversations and messages.

A conversation is either from the embedded widget (a visitor) or from the dashboard's Ask screen (a member
testing the knowledge base). Escalated conversations are answered by people; the AI stays silent until
an agent resolves them.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

DEFAULT_APPEARANCE = {"primary_color": "#0f766e", "position": "right", "launcher_label": "Ask us", "theme": "light"}
DEFAULT_ESCALATION = {"enabled": True, "ask_email": True}


class Chatbot(IdMixin, TimestampMixin, Base):
    __tablename__ = "chatbots"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # Public identifier embedded in the snippet. Not a secret: the allowed-domain check protects it.
    public_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    allowed_domains: Mapped[list] = mapped_column(JSONB, default=list)
    appearance: Mapped[dict] = mapped_column(JSONB, default=lambda: dict(DEFAULT_APPEARANCE))
    logo_key: Mapped[str | None] = mapped_column(String(200))
    logo_mime: Mapped[str | None] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(10), default="en")
    tone: Mapped[str] = mapped_column(String(20), default="friendly")  # friendly | professional | concise
    welcome_message: Mapped[str] = mapped_column(String(500), default="Hi! Ask me anything about our product.")
    instructions: Mapped[str] = mapped_column(Text, default="")
    fallback_message: Mapped[str] = mapped_column(
        String(500), default="Our assistant isn't available right now. Leave your question and our team will get back to you."
    )
    no_answer_message: Mapped[str] = mapped_column(
        String(500), default="I couldn't find this in our documentation. Would you like to talk to someone from our team?"
    )
    model: Mapped[str] = mapped_column(String(80), default="claude-opus-5-5")
    escalation: Mapped[dict] = mapped_column(JSONB, default=lambda: dict(DEFAULT_ESCALATION))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused


class Conversation(IdMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_ws_status", "workspace_id", "status", "last_message_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    chatbot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chatbots.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="widget")  # widget | dashboard
    visitor_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    visitor_name: Mapped[str | None] = mapped_column(String(120))
    visitor_email: Mapped[str | None] = mapped_column(String(320))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))  # dashboard channel
    origin: Mapped[str | None] = mapped_column(String(300))
    page_url: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | escalated | resolved
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_reason: Mapped[str | None] = mapped_column(String(40))  # visitor_request | no_answer | ai_unavailable
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), default="")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(IdMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # visitor | assistant | agent | system
    # text, or a typed block the widget renders: form (Phase 3), event (escalated / resolved / joined)
    kind: Mapped[str] = mapped_column(String(20), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    # [{n, chunk_id, source_id, title, type, url, page, cited_text, excerpt, score}]
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[str | None] = mapped_column(String(10))  # high | medium | low | none
    model: Mapped[str | None] = mapped_column(String(80))
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    trace: Mapped[list] = mapped_column(JSONB, default=list)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
