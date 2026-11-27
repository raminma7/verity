"""External API tools (Phase 5A).

A workspace describes its own HTTP APIs (order status, stock, …). Chatbots linked to a tool offer it to the
model, which decides when to call it (tool calling). The credential is Fernet-encrypted at rest and only ever
decrypted at the moment of a call; it is never returned to the browser.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

TOOL_METHODS = ("GET", "POST")
TOOL_AUTH_TYPES = ("none", "api_key", "bearer")
TOOL_STATUSES = ("draft", "active", "disabled")
PARAM_TYPES = ("string", "integer", "number", "boolean")
PARAM_LOCATIONS = ("path", "query", "body")
# Where a parameter's value comes from. Only "ai" values are chosen by the model; the others are filled by
# Verity from the conversation, so an API can authorize the request against the customer it belongs to.
PARAM_SOURCES = ("ai", "visitor_email", "visitor_name")
# ok | http_error | timeout | blocked | rate_limited | invalid_args | too_large | failed
CALL_STATUSES = ("ok", "http_error", "timeout", "blocked", "rate_limited", "invalid_args", "too_large", "failed")

chatbot_tools = Table(
    "chatbot_tools",
    Base.metadata,
    Column("chatbot_id", ForeignKey("chatbots.id", ondelete="CASCADE"), primary_key=True),
    Column("tool_id", ForeignKey("api_tools.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class ApiTool(IdMixin, TimestampMixin, Base):
    __tablename__ = "api_tools"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    # The tool name the model sees (snake_case, unique per workspace).
    name: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(80))
    # Tells the model when to use the tool ("Look up the shipping status of an order by its number").
    description: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(10), default="GET")
    # https URL with {placeholders} for path parameters. The host is fixed here; parameters never change it.
    url_template: Mapped[str] = mapped_column(String(2000))
    # Static, non-secret headers: {name: value}.
    headers: Mapped[dict] = mapped_column(JSONB, default=dict)
    # [{key, label, type, description, required, location, enum, pattern, max_length, source}]
    parameters: Mapped[list] = mapped_column(JSONB, default=list)
    auth_type: Mapped[str] = mapped_column(String(20), default="none")
    auth_header: Mapped[str | None] = mapped_column(String(80))  # for api_key, e.g. X-Api-Key
    secret_enc: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=8)
    max_response_kb: Mapped[int] = mapped_column(Integer, default=32)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ApiToolCall(IdMixin, Base):
    """Every call to an API tool, from a chat or a test, with its outcome. Never the credential."""

    __tablename__ = "api_tool_calls"
    __table_args__ = (Index("ix_api_tool_calls_ws_created", "workspace_id", "created_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    tool_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_tools.id", ondelete="SET NULL"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    origin: Mapped[str] = mapped_column(String(10))  # chat | test
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20))
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    response_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    args: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
