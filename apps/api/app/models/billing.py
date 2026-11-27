import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

# Every resource limit a plan can set. -1 means unlimited.
LIMIT_KEYS = (
    "documents",
    "domains",
    "crawled_pages",
    "chatbot_domains",
    "email_accounts",
    "members",
    "storage_mb",
    "api_tools",
)


class Plan(IdMixin, TimestampMixin, Base):
    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    billing_cycle: Mapped[str] = mapped_column(String(10), default="month")  # month | year
    token_allowance: Mapped[int] = mapped_column(BigInteger)
    limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    features: Mapped[list] = mapped_column(JSONB, default=list)  # marketing bullets
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Subscription(IdMixin, TimestampMixin, Base):
    """One current subscription per workspace.

    `terms` snapshots the plan's price, allowance and limits at the start of each period, so edits to a
    plan reach existing subscribers at their next renewal, not mid-cycle.
    """

    __tablename__ = "subscriptions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(20))  # active | past_due | canceled
    terms: Mapped[dict] = mapped_column(JSONB)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))


class Checkout(IdMixin, TimestampMixin, Base):
    __tablename__ = "checkouts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"))
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str] = mapped_column(String(120), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | paid | expired | failed


class Payment(IdMixin, Base):
    __tablename__ = "payments"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    checkout_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checkouts.id"), unique=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    description: Mapped[str] = mapped_column(String(200))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed | refunded
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str] = mapped_column(String(120))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | int | str | bool] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
