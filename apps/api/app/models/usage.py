import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class TokenLedger(IdMixin, Base):
    """Token balance for one workspace and one billing period, in weighted tokens.

    available = allowance + bonus - reserved - consumed. Reservations are taken with a single conditional
    UPDATE so concurrent requests can never reserve more than is available.
    """

    __tablename__ = "token_ledgers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "period_start"),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    allowance: Mapped[int] = mapped_column(BigInteger, default=0)
    bonus: Mapped[int] = mapped_column(BigInteger, default=0)  # packages and manual adjustments
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    consumed: Mapped[int] = mapped_column(BigInteger, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notified: Mapped[list] = mapped_column(JSONB, default=list)  # thresholds already alerted, e.g. [80, 90]


class TokenGrant(IdMixin, Base):
    """Tokens bought as a package. Kept apart from the period ledger so they roll over; spent after the allowance.

    available = tokens - reserved - consumed, while status is active and expires_at is in the future.
    """

    __tablename__ = "token_grants"
    __table_args__ = (
        CheckConstraint("reserved >= 0", name="grant_reserved_non_negative"),
        Index("ix_token_grants_ws_expiry", "workspace_id", "status", "expires_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    package_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_packages.id", ondelete="SET NULL"))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    tokens: Mapped[int] = mapped_column(BigInteger)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    consumed: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | expired | revoked
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TokenReservation(IdMixin, Base):
    __tablename__ = "token_reservations"

    ledger_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("token_ledgers.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    # When the period's allowance can't cover the whole estimate, the rest is held on a package grant.
    grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_grants.id", ondelete="SET NULL"))
    grant_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    operation: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="held")  # held | settled | released
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(IdMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_ws_created", "workspace_id", "created_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_ledgers.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20), default="llm")  # llm | embedding
    # embedding | chat_response | kb_ask | tool_call | form_activation | form_prefill | classification | auto_reply
    operation: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    input_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    output_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    weighted_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    provider_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_grants.id", ondelete="SET NULL"))
    grant_tokens: Mapped[int] = mapped_column(BigInteger, default=0)  # part of weighted_tokens paid from a package
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelMultiplier(IdMixin, Base):
    """Weight applied to raw tokens. operation '*' matches every operation for that model."""

    __tablename__ = "model_multipliers"
    __table_args__ = (UniqueConstraint("model", "operation"),)

    model: Mapped[str] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(40), default="*")
    input_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    output_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    # Provider list price per 1M tokens, for admin cost reports.
    input_cost_per_m: Mapped[float] = mapped_column(Float, default=0.0)
    output_cost_per_m: Mapped[float] = mapped_column(Float, default=0.0)


class Notification(IdMixin, Base):
    __tablename__ = "notifications"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(1000), default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
