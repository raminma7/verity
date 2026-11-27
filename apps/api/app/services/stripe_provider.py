"""Stripe behind `PaymentProvider`: hosted Checkout, the Customer Portal, subscriptions and webhooks.

Talks to the Stripe REST API over httpx (form-encoded, API version pinned by `stripe_api_version`), so tests
run it on an `httpx.MockTransport` and CI never reaches Stripe. Verity never handles card data: customers pay
on Stripe's hosted pages and manage payment methods in its portal. Request and response bodies are never logged.

Decisions this implements:
- Checkout collects the billing address and tax ID; Stripe Tax calculates tax on tax-exclusive prices.
- One Stripe Price per plan (and per token package), in the plan's own currency; a Price is immutable, so a
  price change creates a new one and archives the old one.
- Upgrades apply now and are prorated: a new period starts now and unused time is credited
  (`billing_cycle_anchor=now`, `proration_behavior=always_invoice`); with `payment_behavior=pending_if_incomplete`
  nothing changes unless the payment succeeds. Downgrades change the price without proration, so the next
  renewal bills the new plan.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.services.payments import (
    ChangePreview,
    ChangeResult,
    CheckoutInfo,
    CheckoutSession,
    InvoiceInfo,
    PriceRefs,
    ProviderError,
    ProviderEvent,
    RefundInfo,
    SubscriptionInfo,
    is_upgrade,
)

SIGNATURE_TOLERANCE = 300  # seconds

# Stripe event types → Verity's provider-neutral events.
EVENT_TYPES = {
    "checkout.session.completed": "checkout.completed",
    "checkout.session.async_payment_succeeded": "checkout.completed",
    "checkout.session.expired": "checkout.expired",
    "invoice.paid": "invoice.paid",
    "invoice.payment_failed": "invoice.payment_failed",
    "customer.subscription.updated": "subscription.updated",
    "customer.subscription.deleted": "subscription.deleted",
    "charge.refunded": "charge.refunded",
}

REASONS = {"subscription_create": "create", "subscription_cycle": "cycle", "subscription_update": "update"}


class SignatureError(ProviderError):
    def __init__(self, message: str = "The webhook signature is not valid."):
        super().__init__(message, code="bad_signature")


def encode(params: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's form encoding: nested dicts as a[b]=…, lists as a[0]=…, booleans as true/false; None is left out."""
    out: list[tuple[str, str]] = []
    for k, v in params.items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if v is None:
            continue
        if isinstance(v, dict):
            out += encode(v, key)
        elif isinstance(v, list | tuple):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out += encode(item, f"{key}[{i}]")
                else:
                    out.append((f"{key}[{i}]", _scalar(item)))
        else:
            out.append((key, _scalar(v)))
    return out


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def verify_signature(
    body: bytes, header: str | None, secret: str, *, now: float | None = None, tolerance: int = SIGNATURE_TOLERANCE
) -> None:
    """Stripe-Signature: t=<unix>,v1=<hex hmac-sha256 of "t.body">[,v1=…]. Any v1 may match (secret rotation)."""
    if not header:
        raise SignatureError("The webhook has no signature.")
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in parts if k.strip() == "t"), None)
    sigs = [v.strip() for k, v in parts if k.strip() == "v1"]
    if ts is None or not sigs:
        raise SignatureError()
    try:
        t = int(ts)
    except ValueError:
        raise SignatureError() from None
    if abs((now if now is not None else time.time()) - t) > tolerance:
        raise SignatureError("The webhook signature is too old.")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise SignatureError()


def sign(body: bytes, secret: str, *, t: int | None = None) -> str:
    """Builds a Stripe-Signature header (tests and local webhook replays)."""
    t = int(time.time()) if t is None else t
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


def _ts(v: Any) -> datetime | None:
    return datetime.fromtimestamp(int(v), UTC) if v else None


def _id(v: Any) -> str | None:
    if isinstance(v, dict):
        return v.get("id")
    return v or None


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


def _meta(obj: dict | None) -> dict:
    return (obj or {}).get("metadata") or {}


class StripeProvider:
    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str, *, api_base: str, api_version: str, timeout: float, transport=None):
        self._key = secret_key
        self._webhook_secret = webhook_secret
        self._base = api_base.rstrip("/")
        self._version = api_version
        self._timeout = timeout
        self._transport = transport

    @classmethod
    def from_settings(cls, transport: httpx.AsyncBaseTransport | None = None) -> StripeProvider:
        s = get_settings()
        if not (s.stripe_secret_key and s.stripe_webhook_secret):
            raise RuntimeError("Stripe needs VERITY_STRIPE_SECRET_KEY and VERITY_STRIPE_WEBHOOK_SECRET.")
        return cls(
            s.stripe_secret_key,
            s.stripe_webhook_secret,
            api_base=s.stripe_api_base,
            api_version=s.stripe_api_version,
            timeout=s.stripe_timeout_seconds,
            transport=transport,
        )

    # ---- HTTP ---------------------------------------------------------------------------------------------

    async def _call(self, method: str, path: str, params: dict | None = None, *, idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._key}", "Stripe-Version": self._version}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = encode(params or {})
        try:
            async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout, transport=self._transport) as c:
                if method == "GET":
                    r = await c.get(path, params=data, headers=headers)
                elif method == "DELETE":
                    r = await c.delete(path, headers=headers)
                else:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    r = await c.post(path, content=urlencode(data), headers=headers)
        except httpx.HTTPError:
            raise ProviderError("The payment provider can't be reached. Try again in a moment.", code="provider_unavailable") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            err = body.get("error") or {}
            # Stripe's messages are written for customers ("Your card was declined.") and carry no card data.
            message = err.get("message") if err.get("type") == "card_error" else None
            raise ProviderError(
                message or "The payment provider refused the request.",
                code="card_declined" if err.get("type") == "card_error" else "provider_error",
            )
        return body

    # ---- Prices ---------------------------------------------------------------------------------------------

    async def sync_price(self, item, kind: str) -> PriceRefs:
        """Creates the product (once) and a price for the item's current amount, currency and cycle."""
        meta = {"verity_kind": kind, "verity_id": str(item.id)}
        product = item.provider_product_ref
        if not product:
            p = await self._call(
                "POST",
                "/v1/products",
                {"name": f"Verity {item.name}", "metadata": meta},
                idempotency_key=f"product:{kind}:{item.id}",
            )
            product = p["id"]
        params: dict[str, Any] = {
            "product": product,
            "unit_amount": item.price_cents,
            "currency": item.currency.lower(),
            "tax_behavior": "exclusive",
            "metadata": meta,
        }
        if kind == "plan":
            params["recurring"] = {"interval": item.billing_cycle}
        price = await self._call("POST", "/v1/prices", params)
        return PriceRefs(product_ref=product, price_ref=price["id"])

    async def archive_price(self, ref: str) -> None:
        await self._call("POST", f"/v1/prices/{ref}", {"active": False})

    # ---- Checkout -------------------------------------------------------------------------------------------

    async def _customer(self, account, email: str, workspace_id: uuid.UUID) -> str:
        if account.customer_ref:
            return account.customer_ref
        c = await self._call(
            "POST",
            "/v1/customers",
            {"email": email, "metadata": {"workspace_id": str(workspace_id)}},
            idempotency_key=f"customer:{workspace_id}",
        )
        account.customer_ref = c["id"]
        return c["id"]

    async def create_checkout(
        self, *, kind, item, account, customer_email, workspace_id, local_id, success_url, cancel_url
    ) -> CheckoutSession:
        if not item.provider_price_ref:
            raise ProviderError("This item has no Stripe price yet.")
        customer = await self._customer(account, customer_email, workspace_id)
        meta = {"workspace_id": str(workspace_id), "checkout_id": str(local_id)}
        params: dict[str, Any] = {
            "mode": "subscription" if kind == "subscription" else "payment",
            "customer": customer,
            "client_reference_id": str(local_id),
            "line_items": [{"price": item.provider_price_ref, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "automatic_tax": {"enabled": True},
            "billing_address_collection": "required",
            "tax_id_collection": {"enabled": True},
            "customer_update": {"address": "auto", "name": "auto"},
            "metadata": meta,
        }
        if kind == "subscription":
            params["subscription_data"] = {"metadata": meta}
        else:
            package_meta = meta | {"package_id": str(item.id)}
            params["invoice_creation"] = {"enabled": True, "invoice_data": {"metadata": package_meta}}
            params["payment_intent_data"] = {"metadata": package_meta}
        s = await self._call("POST", "/v1/checkout/sessions", params, idempotency_key=f"checkout:{local_id}")
        return CheckoutSession(provider=self.name, ref=s["id"], url=s["url"])

    async def get_checkout(self, ref: str) -> CheckoutInfo:
        s = await self._call("GET", f"/v1/checkout/sessions/{ref}")
        return CheckoutInfo(
            ref=s["id"],
            local_id=_uuid(s.get("client_reference_id") or _meta(s).get("checkout_id")),
            paid=s.get("payment_status") in ("paid", "no_payment_required") and s.get("status") == "complete",
            status=s.get("status") or "open",
            customer_ref=_id(s.get("customer")),
            subscription_ref=_id(s.get("subscription")),
            invoice_ref=_id(s.get("invoice")),
        )

    # ---- Invoices and subscriptions -------------------------------------------------------------------------

    def _invoice(self, i: dict) -> InvoiceInfo:
        meta = dict(_meta(i))
        meta.update((i.get("subscription_details") or {}).get("metadata") or {})
        lines = ((i.get("lines") or {}).get("data")) or []
        main = next((ln for ln in lines if not ln.get("proration")), lines[0] if lines else {})
        price = main.get("price") or {}
        pmeta = _meta(price) if isinstance(price, dict) else {}
        plan_id = _uuid(pmeta.get("verity_id")) if pmeta.get("verity_kind") == "plan" else None
        package_id = _uuid(pmeta.get("verity_id")) if pmeta.get("verity_kind") == "package" else _uuid(meta.get("package_id"))
        reason = REASONS.get(i.get("billing_reason") or "", "package" if package_id else "manual")
        tax = i.get("tax")
        if tax is None:
            tax = sum(int(t.get("amount") or 0) for t in i.get("total_tax_amounts") or [])
        subtotal = i.get("subtotal_excluding_tax")
        if subtotal is None:
            subtotal = int(i.get("total") or 0) - int(tax or 0)
        pi = i.get("payment_intent")
        failure = None
        if isinstance(pi, dict):
            failure = (pi.get("last_payment_error") or {}).get("message")
        period = main.get("period") or {}
        status = i.get("status") or "open"
        return InvoiceInfo(
            ref=i["id"],
            status=status if status in ("paid", "open", "void", "uncollectible", "draft") else "open",
            reason=reason,
            currency=(i.get("currency") or "usd").upper(),
            subtotal_cents=int(subtotal or 0),
            tax_cents=int(tax or 0),
            total_cents=int(i.get("total") or 0),
            number=i.get("number"),
            payment_ref=_id(pi),
            subscription_ref=_id(i.get("subscription")),
            customer_ref=_id(i.get("customer")),
            plan_id=plan_id,
            package_id=package_id,
            period_start=_ts(period.get("start")) if reason != "package" else None,
            period_end=_ts(period.get("end")) if reason != "package" else None,
            hosted_url=i.get("hosted_invoice_url"),
            pdf_url=i.get("invoice_pdf"),
            attempt_count=int(i.get("attempt_count") or 1),
            next_retry_at=_ts(i.get("next_payment_attempt")),
            failure_message=failure[:300] if failure else None,
            workspace_id=_uuid(meta.get("workspace_id")),
        )

    async def get_invoice(self, ref: str) -> InvoiceInfo:
        i = await self._call("GET", f"/v1/invoices/{ref}", {"expand": ["payment_intent", "lines.data.price"]})
        return self._invoice(i)

    def _subscription(self, s: dict) -> SubscriptionInfo:
        items = ((s.get("items") or {}).get("data")) or [{}]
        item = items[0]
        price = item.get("price") or {}
        pmeta = _meta(price)
        meta = _meta(s)
        return SubscriptionInfo(
            ref=s["id"],
            status=s.get("status") or "active",
            cancel_at_period_end=bool(s.get("cancel_at_period_end")),
            current_period_start=_ts(s.get("current_period_start") or item.get("current_period_start")),
            current_period_end=_ts(s.get("current_period_end") or item.get("current_period_end")),
            customer_ref=_id(s.get("customer")),
            item_ref=item.get("id"),
            price_ref=price.get("id"),
            plan_id=_uuid(pmeta.get("verity_id")) if pmeta.get("verity_kind") == "plan" else None,
            workspace_id=_uuid(meta.get("workspace_id")),
            checkout_id=_uuid(meta.get("checkout_id")),
            latest_invoice_ref=_id(s.get("latest_invoice")),
        )

    async def get_subscription(self, ref: str) -> SubscriptionInfo:
        return self._subscription(await self._call("GET", f"/v1/subscriptions/{ref}"))

    async def get_refund(self, ref: str) -> RefundInfo:
        c = await self._call("GET", f"/v1/charges/{ref}")
        return RefundInfo(
            ref=c["id"],
            invoice_ref=_id(c.get("invoice")),
            payment_ref=_id(c.get("payment_intent")),
            amount_refunded_cents=int(c.get("amount_refunded") or 0),
            amount_cents=int(c.get("amount") or 0),
        )

    # ---- Plan changes ---------------------------------------------------------------------------------------

    async def _item(self, sub) -> str:
        if not sub.provider_subscription_ref:
            raise ProviderError("This subscription isn't managed by Stripe.")
        info = await self.get_subscription(sub.provider_subscription_ref)
        if not info.item_ref:
            raise ProviderError("The Stripe subscription has no item.")
        return info.item_ref

    async def preview_change(self, sub, plan, account, *, now) -> ChangePreview:
        if not is_upgrade(sub, plan):
            return ChangePreview(False, plan.currency, 0, 0, None, sub.current_period_end)
        item = await self._item(sub)
        p = await self._call(
            "POST",
            "/v1/invoices/create_preview",
            {
                "customer": account.customer_ref,
                "subscription": sub.provider_subscription_ref,
                "automatic_tax": {"enabled": True},
                "subscription_details": {
                    "items": [{"id": item, "price": plan.provider_price_ref}],
                    "proration_behavior": "always_invoice",
                    "billing_cycle_anchor": "now",
                },
            },
        )
        lines = ((p.get("lines") or {}).get("data")) or []
        credit = -sum(int(ln.get("amount") or 0) for ln in lines if ln.get("proration") and int(ln.get("amount") or 0) < 0)
        tax = p.get("tax")
        if tax is None:
            tax = sum(int(t.get("amount") or 0) for t in p.get("total_tax_amounts") or [])
        due = int(p.get("amount_due") or 0)
        return ChangePreview(True, plan.currency, max(0, due - int(tax or 0)), credit, int(tax or 0), now)

    async def change_plan(self, sub, plan, account, *, immediate, now) -> ChangeResult:
        if not plan.provider_price_ref:
            raise ProviderError("This plan has no Stripe price yet.")
        item = await self._item(sub)
        if not immediate:
            await self._call(
                "POST",
                f"/v1/subscriptions/{sub.provider_subscription_ref}",
                {"items": [{"id": item, "price": plan.provider_price_ref}], "proration_behavior": "none"},
            )
            return ChangeResult(status="scheduled")
        try:
            s = await self._call(
                "POST",
                f"/v1/subscriptions/{sub.provider_subscription_ref}",
                {
                    "items": [{"id": item, "price": plan.provider_price_ref}],
                    "proration_behavior": "always_invoice",
                    "billing_cycle_anchor": "now",
                    "payment_behavior": "pending_if_incomplete",
                    "expand": ["latest_invoice.payment_intent"],
                },
            )
        except ProviderError as e:
            return ChangeResult(status="failed", message=e.message)
        latest = s.get("latest_invoice") or {}
        pi = latest.get("payment_intent") if isinstance(latest, dict) else None
        if s.get("pending_update"):
            if isinstance(pi, dict) and pi.get("status") == "requires_action":
                return ChangeResult(status="action_required", url=latest.get("hosted_invoice_url"))
            msg = ((pi or {}).get("last_payment_error") or {}).get("message") if isinstance(pi, dict) else None
            return ChangeResult(status="failed", message=msg or "The payment didn't go through.")
        info = self._subscription(s)
        inv = await self.get_invoice(_id(latest)) if _id(latest) else None
        if inv is None or inv.status != "paid":
            return ChangeResult(status="failed", message="The payment didn't go through.")
        return ChangeResult(status="applied", invoice=inv, period_start=info.current_period_start, period_end=info.current_period_end)

    async def cancel_now(self, sub) -> None:
        if sub.provider_subscription_ref:
            await self._call("DELETE", f"/v1/subscriptions/{sub.provider_subscription_ref}")

    async def set_cancel_at_period_end(self, sub, cancel: bool) -> None:
        if sub.provider_subscription_ref:
            await self._call("POST", f"/v1/subscriptions/{sub.provider_subscription_ref}", {"cancel_at_period_end": cancel})

    async def charge_renewal(self, sub, account, plan, *, invoice_ref, now) -> InvoiceInfo:
        raise ProviderError("Stripe charges renewals itself.")

    async def retry_invoice(self, ref: str) -> InvoiceInfo:
        await self._call("POST", f"/v1/invoices/{ref}/pay", idempotency_key=f"pay:{ref}:{int(time.time() // 60)}")
        return await self.get_invoice(ref)

    # ---- Customer Portal ------------------------------------------------------------------------------------

    async def create_portal_configuration(self) -> str | None:
        """Payment methods, invoices and billing details only: plan changes and cancellation stay in Verity."""
        c = await self._call(
            "POST",
            "/v1/billing_portal/configurations",
            {
                "business_profile": {"headline": "Verity billing: payment methods and invoices"},
                "features": {
                    "payment_method_update": {"enabled": True},
                    "invoice_history": {"enabled": True},
                    "customer_update": {"enabled": True, "allowed_updates": ["address", "email", "name", "tax_id"]},
                    "subscription_cancel": {"enabled": False},
                    "subscription_update": {"enabled": False},
                },
            },
            idempotency_key="portal-configuration:v1",
        )
        return c["id"]

    async def portal_url(self, account, return_url: str, *, configuration: str | None = None) -> str:
        if not account.customer_ref:
            raise ProviderError("There's nothing to manage before the first payment.", code="no_customer")
        s = await self._call(
            "POST",
            "/v1/billing_portal/sessions",
            {"customer": account.customer_ref, "return_url": return_url, "configuration": configuration},
        )
        return s["url"]

    # ---- Webhooks -------------------------------------------------------------------------------------------

    async def parse_webhook(self, body: bytes, headers: dict[str, str]) -> ProviderEvent:
        verify_signature(body, headers.get("stripe-signature"), self._webhook_secret)
        try:
            event = json.loads(body)
            obj = event["data"]["object"]
            return ProviderEvent(id=event["id"], type=EVENT_TYPES.get(event["type"], event["type"]), object_ref=obj.get("id"))
        except (ValueError, KeyError, TypeError):
            raise SignatureError("The webhook body isn't a Stripe event.") from None
