import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    # The payment provider's product and price for this plan (Stripe: prod_…, price_…). A price is immutable at
    # the provider, so a change of price or cycle creates a new one; subscribers move to it at renewal.
    provider_product_ref: Mapped[str | None] = mapped_column(String(120))
    provider_price_ref: Mapped[str | None] = mapped_column(String(120))


class TokenPackage(IdMixin, TimestampMixin, Base):
    """Additional AI tokens a subscribed workspace can buy once. Defined by system administrators.

    Bought tokens roll over: they are kept in a grant of their own that survives renewals and expires
    12 months after purchase, and are spent only after the period's allowance.
    """

    __tablename__ = "token_packages"

    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    tokens: Mapped[int] = mapped_column(BigInteger)
    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    provider_product_ref: Mapped[str | None] = mapped_column(String(120))
    provider_price_ref: Mapped[str | None] = mapped_column(String(120))


class BillingAccount(IdMixin, TimestampMixin, Base):
    """A workspace's customer at the payment provider. The currency is fixed by the first successful payment."""

    __tablename__ = "billing_accounts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    customer_ref: Mapped[str | None] = mapped_column(String(120), unique=True)
    currency: Mapped[str | None] = mapped_column(String(3))
    # Mock provider only: the test card on file ("ok" or "declines"), set from the test billing portal.
    mock_card: Mapped[str] = mapped_column(String(20), default="ok")


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
    # Who charges renewals: "mock" (Verity's renewal job) or "stripe" (Stripe, reported through webhooks).
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    provider_subscription_ref: Mapped[str | None] = mapped_column(String(120), unique=True)
    # A failed renewal: the subscription is past_due from this moment, retried until the grace period ends.
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Checkout(IdMixin, TimestampMixin, Base):
    __tablename__ = "checkouts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20), default="subscription")  # subscription | package
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    package_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_packages.id"))
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str] = mapped_column(String(120), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | paid | expired | failed


class Payment(IdMixin, Base):
    """An invoice: what was charged (or attempted) and for what. Card data never reaches Verity."""

    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_created", "created_at"),)

    # Invoices outlive a deleted workspace for accounting, de-identified (workspace and checkout set to NULL).
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), index=True)
    checkout_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checkouts.id", ondelete="SET NULL"), unique=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    description: Mapped[str] = mapped_column(String(200))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed | refunded | void
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str] = mapped_column(String(120))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # subscription (first purchase) | upgrade | renewal | package
    kind: Mapped[str] = mapped_column(String(20), default="subscription")
    package_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_packages.id"))
    # amount_cents is the total charged, tax included; prices are tax-exclusive, so subtotal + tax = amount.
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Normalized monthly value of the plan this invoice pays for (0 for packages), for MRR reports.
    mrr_cents: Mapped[int] = mapped_column(Integer, default=0)
    number: Mapped[str | None] = mapped_column(String(60))
    provider_invoice_ref: Mapped[str | None] = mapped_column(String(120), unique=True)
    hosted_url: Mapped[str | None] = mapped_column(String(2000))
    pdf_url: Mapped[str | None] = mapped_column(String(2000))
    failure_message: Mapped[str | None] = mapped_column(String(300))
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingEvent(IdMixin, Base):
    """A webhook delivery, claimed once by (provider, event_id). Only ids and types are kept, never payloads."""

    __tablename__ = "billing_events"
    __table_args__ = (UniqueConstraint("provider", "event_id"), Index("ix_billing_events_received", "received_at"))

    provider: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(80))
    object_ref: Mapped[str | None] = mapped_column(String(120))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing | processed | ignored | failed
    error: Mapped[str | None] = mapped_column(String(300))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | int | str | bool] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
