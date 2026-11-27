"""Payment providers behind one interface.

`MockPaymentProvider` (tests, CI, e2e and local development) and `StripeProvider` (production,
`app/services/stripe_provider.py`) both describe what happened at the provider in the same provider-neutral
objects (`CheckoutInfo`, `InvoiceInfo`, `SubscriptionInfo`, `RefundInfo`) and the same `ProviderEvent`s.
`app/services/billing_events.py` turns those into the activation logic (`start_period`, renewals, the token
ledger), so a mock "Pay" button and a Stripe webhook run exactly the same code.

Verity never sees card data: purchases happen on the provider's hosted pages, and payment methods are managed
in its portal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

from app.core.config import get_settings
from app.core.security import new_token
from app.models.base import utcnow

if TYPE_CHECKING:
    from app.models import BillingAccount, Plan, Subscription, TokenPackage


class ProviderError(Exception):
    """The provider refused or couldn't be reached. `message` is safe to show (never card data)."""

    def __init__(self, message: str, *, code: str = "provider_error"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class CheckoutSession:
    provider: str
    ref: str
    url: str


@dataclass
class InvoiceInfo:
    """One invoice at the provider. Amounts are in the currency's minor unit; prices are tax-exclusive."""

    ref: str
    status: Literal["paid", "open", "void", "uncollectible", "draft"]
    # create (first purchase) | cycle (renewal) | update (upgrade) | package | manual
    reason: str
    currency: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    number: str | None = None
    payment_ref: str | None = None
    subscription_ref: str | None = None
    customer_ref: str | None = None
    plan_id: uuid.UUID | None = None
    package_id: uuid.UUID | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    hosted_url: str | None = None
    pdf_url: str | None = None
    attempt_count: int = 1
    next_retry_at: datetime | None = None
    failure_message: str | None = None
    workspace_id: uuid.UUID | None = None


@dataclass
class CheckoutInfo:
    ref: str
    local_id: uuid.UUID | None  # Verity's checkout id (client_reference_id)
    paid: bool
    status: Literal["open", "complete", "expired"]
    customer_ref: str | None = None
    subscription_ref: str | None = None
    invoice_ref: str | None = None
    invoice: InvoiceInfo | None = None


@dataclass
class SubscriptionInfo:
    ref: str
    status: str  # active | past_due | unpaid | canceled | incomplete | incomplete_expired | trialing
    cancel_at_period_end: bool
    current_period_start: datetime | None
    current_period_end: datetime | None
    customer_ref: str | None = None
    item_ref: str | None = None
    price_ref: str | None = None
    plan_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    checkout_id: uuid.UUID | None = None
    latest_invoice_ref: str | None = None


@dataclass
class RefundInfo:
    ref: str
    invoice_ref: str | None
    payment_ref: str | None
    amount_refunded_cents: int
    amount_cents: int


@dataclass
class ProviderEvent:
    """A provider notification, normalized. `obj` carries the object when the provider sent it whole (the mock);
    otherwise the handler re-reads `object_ref` from the provider, so stale or out-of-order events are harmless."""

    id: str
    type: str  # checkout.completed | checkout.expired | invoice.paid | invoice.payment_failed |
    # subscription.updated | subscription.deleted | charge.refunded (anything else is ignored)
    object_ref: str | None
    obj: CheckoutInfo | InvoiceInfo | SubscriptionInfo | RefundInfo | None = None


@dataclass
class ChangePreview:
    immediate: bool
    currency: str
    amount_due_cents: int  # charged now, before tax (tax is added by the provider when it applies)
    credit_cents: int  # unused time on the current plan
    tax_cents: int | None = None  # known only when the provider calculated it
    effective_at: datetime | None = None


@dataclass
class ChangeResult:
    status: Literal["applied", "scheduled", "action_required", "failed"]
    invoice: InvoiceInfo | None = None
    url: str | None = None  # where to finish a payment that needs the customer (3-D Secure)
    message: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None


@dataclass
class PriceRefs:
    product_ref: str | None
    price_ref: str | None
    archived: list[str] = field(default_factory=list)


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(
        self,
        *,
        kind: Literal["subscription", "package"],
        item: Plan | TokenPackage,
        account: BillingAccount,
        customer_email: str,
        workspace_id: uuid.UUID,
        local_id: uuid.UUID,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession: ...

    async def get_checkout(self, ref: str) -> CheckoutInfo: ...

    async def get_invoice(self, ref: str) -> InvoiceInfo: ...

    async def get_subscription(self, ref: str) -> SubscriptionInfo: ...

    async def get_refund(self, ref: str) -> RefundInfo: ...

    async def preview_change(self, sub: Subscription, plan: Plan, account: BillingAccount, *, now: datetime) -> ChangePreview: ...

    async def change_plan(self, sub: Subscription, plan: Plan, account: BillingAccount, *, immediate: bool, now: datetime) -> ChangeResult:
        """immediate: prorated upgrade starting a new period now. Otherwise the next renewal bills `plan`."""
        ...

    async def set_cancel_at_period_end(self, sub: Subscription, cancel: bool) -> None: ...

    async def charge_renewal(
        self, sub: Subscription, account: BillingAccount, plan: Plan, *, invoice_ref: str | None, now: datetime
    ) -> InvoiceInfo:
        """Mock only: Verity's renewal job charges the card on file. Stripe charges renewals itself."""
        ...

    async def retry_invoice(self, ref: str) -> InvoiceInfo: ...

    async def portal_url(self, account: BillingAccount, return_url: str, *, configuration: str | None = None) -> str: ...

    async def create_portal_configuration(self) -> str | None: ...

    async def archive_price(self, ref: str) -> None: ...

    async def sync_price(self, item: Plan | TokenPackage, kind: Literal["plan", "package"]) -> PriceRefs: ...

    async def parse_webhook(self, body: bytes, headers: dict[str, str]) -> ProviderEvent: ...


def monthly_value(price_cents: int, cycle: str) -> int:
    return price_cents // 12 if cycle == "year" else price_cents


def is_upgrade(sub: Subscription, plan: Plan) -> bool:
    """Upgrades (a higher monthly price) apply now, prorated; anything else waits for the renewal."""
    return monthly_value(plan.price_cents, plan.billing_cycle) > monthly_value(sub.terms["price_cents"], sub.terms["billing_cycle"])


def prorated_credit(sub: Subscription, now: datetime) -> int:
    """Unused part of the current period's price, rounded down to the minor unit."""
    start, end = sub.current_period_start, sub.current_period_end
    total = (end - start).total_seconds()
    left = max(0.0, (end - now).total_seconds())
    if total <= 0:
        return 0
    return int(sub.terms["price_cents"] * min(1.0, left / total))


class MockPaymentProvider:
    """Test mode: the web app shows a checkout page whose button calls the confirm endpoint, and a test billing
    portal where the card on file can be switched to one that declines. Nothing is charged, nothing leaves Verity."""

    name = "mock"

    def _url(self, path: str) -> str:
        return get_settings().app_url.rstrip("/") + path

    def _invoice(self, *, reason: str, currency: str, amount: int, **kw) -> InvoiceInfo:
        now = utcnow()
        return InvoiceInfo(
            ref="mock_in_" + new_token(12),
            status=kw.pop("status", "paid"),
            reason=reason,
            currency=currency,
            subtotal_cents=amount,
            tax_cents=0,
            total_cents=amount,
            number=f"TEST-{now:%Y%m%d}-{new_token(3).upper()}",
            payment_ref="mock_pi_" + new_token(12),
            **kw,
        )

    async def create_checkout(
        self, *, kind, item, account, customer_email, workspace_id, local_id, success_url, cancel_url
    ) -> CheckoutSession:
        ref = "mock_" + new_token(12)
        return CheckoutSession(provider=self.name, ref=ref, url=self._url(f"/checkout/{local_id}"))

    async def get_checkout(self, ref: str) -> CheckoutInfo:
        raise ProviderError("The test provider sends checkouts with its events.")

    async def get_invoice(self, ref: str) -> InvoiceInfo:
        raise ProviderError("The test provider sends invoices with its events.")

    async def get_subscription(self, ref: str) -> SubscriptionInfo:
        raise ProviderError("The test provider sends subscriptions with its events.")

    async def get_refund(self, ref: str) -> RefundInfo:
        raise ProviderError("The test provider has no refunds.")

    def checkout_paid(
        self, *, checkout_id: uuid.UUID, ref: str, kind: str, currency: str, amount: int, plan_id, package_id
    ) -> CheckoutInfo:
        """What Stripe would report after a successful hosted checkout."""
        invoice = self._invoice(
            reason="create" if kind == "subscription" else "package",
            currency=currency,
            amount=amount,
            plan_id=plan_id,
            package_id=package_id,
        )
        return CheckoutInfo(ref=ref, local_id=checkout_id, paid=True, status="complete", invoice=invoice)

    async def preview_change(self, sub, plan, account, *, now) -> ChangePreview:
        if not is_upgrade(sub, plan):
            return ChangePreview(False, plan.currency, 0, 0, None, sub.current_period_end)
        credit = prorated_credit(sub, now)
        return ChangePreview(True, plan.currency, max(0, plan.price_cents - credit), credit, None, now)

    async def change_plan(self, sub, plan, account, *, immediate, now) -> ChangeResult:
        if not immediate:
            return ChangeResult(status="scheduled")
        preview = await self.preview_change(sub, plan, account, now=now)
        if account.mock_card == "declines":
            return ChangeResult(status="failed", message="Your card was declined.")
        invoice = self._invoice(reason="update", currency=plan.currency, amount=preview.amount_due_cents, plan_id=plan.id)
        return ChangeResult(status="applied", invoice=invoice)

    async def set_cancel_at_period_end(self, sub, cancel: bool) -> None:
        return None

    async def charge_renewal(self, sub, account, plan, *, invoice_ref, now) -> InvoiceInfo:
        declined = account.mock_card == "declines"
        inv = self._invoice(
            reason="cycle",
            currency=plan.currency,
            amount=plan.price_cents,
            plan_id=plan.id,
            status="open" if declined else "paid",
            failure_message="Your card was declined." if declined else None,
        )
        if invoice_ref:
            inv.ref = invoice_ref
        return inv

    async def retry_invoice(self, ref: str) -> InvoiceInfo:
        raise ProviderError("The test provider retries through the renewal job.")

    async def portal_url(self, account, return_url: str, *, configuration: str | None = None) -> str:
        return self._url("/billing/test-portal")

    async def create_portal_configuration(self) -> str | None:
        return None

    async def archive_price(self, ref: str) -> None:
        return None

    async def sync_price(self, item, kind) -> PriceRefs:
        return PriceRefs(product_ref=None, price_ref=None)

    async def parse_webhook(self, body: bytes, headers: dict[str, str]) -> ProviderEvent:
        raise ProviderError("The test provider doesn't send webhooks.", code="no_webhooks")


_PROVIDERS: dict[str, PaymentProvider] = {}


def get_provider(name: str | None = None) -> PaymentProvider:
    """The configured provider, or the one a subscription was bought with (`name`)."""
    name = name or get_settings().payment_provider
    if name in _PROVIDERS:
        return _PROVIDERS[name]
    if name == "mock":
        provider: PaymentProvider = MockPaymentProvider()
    elif name == "stripe":
        from app.services.stripe_provider import StripeProvider

        provider = StripeProvider.from_settings()
    else:
        raise RuntimeError(f"Unknown payment provider: {name}")
    _PROVIDERS[name] = provider
    return provider


def set_provider(name: str, provider: PaymentProvider | None) -> None:
    """Tests: install a provider (a Stripe provider on a mock HTTP transport), or None to forget it."""
    if provider is None:
        _PROVIDERS.pop(name, None)
    else:
        _PROVIDERS[name] = provider


def check_configuration() -> None:
    """Refuses to start with Stripe selected but not configured."""
    s = get_settings()
    if s.payment_provider == "stripe" and not (s.stripe_secret_key and s.stripe_webhook_secret):
        raise RuntimeError("VERITY_PAYMENT_PROVIDER=stripe needs VERITY_STRIPE_SECRET_KEY and VERITY_STRIPE_WEBHOOK_SECRET.")
    if s.payment_provider not in ("mock", "stripe"):
        raise RuntimeError(f"Unknown payment provider: {s.payment_provider}")


def format_money(cents: int, currency: str) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency.upper())
    amount = f"{cents / 100:,.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency.upper()}"
