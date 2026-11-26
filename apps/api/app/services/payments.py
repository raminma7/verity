"""Payment providers. Phase 1 ships a mock provider so the purchase → activation path is real and testable;
a Stripe provider implementing the same interface replaces it in Phase 5."""

from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings
from app.core.security import new_token


@dataclass
class CheckoutSession:
    provider: str
    ref: str
    url: str


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, *, amount_cents: int, currency: str, description: str, local_id: str) -> CheckoutSession: ...

    def confirm(self, ref: str) -> bool:
        """True when the provider reports the checkout as paid."""
        ...


class MockPaymentProvider:
    """Test mode: the web app shows a checkout page whose button calls the confirm endpoint."""

    name = "mock"

    def create_checkout(self, *, amount_cents: int, currency: str, description: str, local_id: str) -> CheckoutSession:
        ref = "mock_" + new_token(12)
        url = get_settings().app_url.rstrip("/") + f"/checkout/{local_id}"
        return CheckoutSession(provider=self.name, ref=ref, url=url)

    def confirm(self, ref: str) -> bool:
        return ref.startswith("mock_")


def get_provider() -> PaymentProvider:
    name = get_settings().payment_provider
    if name == "mock":
        return MockPaymentProvider()
    raise RuntimeError(f"Unknown payment provider: {name}")


def format_money(cents: int, currency: str) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency.upper())
    amount = f"{cents / 100:,.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency.upper()}"
