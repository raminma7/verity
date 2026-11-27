"""A small in-memory Stripe API for tests, served through httpx.MockTransport (no network, ever).

It keeps the objects Verity creates (products, prices, customers, checkout sessions, subscriptions,
invoices, charges, portal configurations), records every request with its decoded form, and lets a test
play Stripe's part: complete a checkout, renew a subscription, fail a payment, refund a charge, then send the
signed webhook for it.
"""

import json
import time
import uuid
from urllib.parse import parse_qsl

import httpx

from app.services.stripe_provider import sign

SECRET = "sk_test_fake"
WEBHOOK_SECRET = "whsec_test_fake"
DAY = 86400


class FakeStripe:
    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []
        self.objects: dict[str, dict] = {}
        self.counter = 0
        self.fail_next: dict[str, int] = {}  # path prefix → status code for the next request
        self.upgrade_outcome = "paid"  # paid | requires_action | declined

    # ---- helpers -----------------------------------------------------------------------------------------

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter:04d}"

    def find(self, method: str, path: str) -> list[dict]:
        return [form for m, p, form in self.requests if m == method and p == path]

    def last(self, method: str, path: str) -> dict:
        found = self.find(method, path)
        assert found, f"no {method} {path} in {[(m, p) for m, p, _ in self.requests]}"
        return found[-1]

    def _price(self, pid: str) -> dict:
        return self.objects[pid]

    def _line(self, price_id: str, start: int, end: int, amount: int, proration=False) -> dict:
        return {"amount": amount, "proration": proration, "price": self._price(price_id), "period": {"start": start, "end": end}}

    def _invoice(self, *, sub: dict | None, customer: str, reason: str, lines: list, tax: int, status="paid", meta=None, attempt=1) -> dict:
        subtotal = sum(ln["amount"] for ln in lines)
        inv = {
            "id": self._id("in"),
            "object": "invoice",
            "status": status,
            "billing_reason": reason,
            "currency": lines[0]["price"]["currency"] if lines else "usd",
            "subtotal_excluding_tax": subtotal,
            "tax": tax,
            "total": subtotal + tax,
            "amount_due": subtotal + tax,
            "number": f"VER-{self.counter:04d}",
            "customer": customer,
            "subscription": sub["id"] if sub else None,
            "subscription_details": {"metadata": sub["metadata"]} if sub else None,
            "metadata": meta or {},
            "lines": {"data": lines},
            "hosted_invoice_url": f"https://invoice.stripe.test/{self.counter}",
            "invoice_pdf": f"https://invoice.stripe.test/{self.counter}.pdf",
            "attempt_count": attempt,
            "next_payment_attempt": None,
            "payment_intent": {"id": self._id("pi"), "status": "succeeded" if status == "paid" else "requires_payment_method"},
        }
        if status != "paid":
            inv["payment_intent"]["last_payment_error"] = {"message": "Your card was declined."}
            inv["next_payment_attempt"] = int(time.time()) + 3 * DAY
        self.objects[inv["id"]] = inv
        return inv

    def complete_checkout(self, session_id: str, *, tax: int = 0) -> dict:
        """Stripe's side of a paid hosted checkout. Returns the first invoice."""
        s = self.objects[session_id]
        price = s["line_items"][0]["price"]
        now = int(time.time())
        s["status"], s["payment_status"] = "complete", "paid"
        if s["mode"] == "subscription":
            interval = self._price(price)["recurring"]["interval"]
            end = now + (365 if interval == "year" else 30) * DAY
            sub = {
                "id": self._id("sub"),
                "object": "subscription",
                "status": "active",
                "customer": s["customer"],
                "cancel_at_period_end": False,
                "current_period_start": now,
                "current_period_end": end,
                "metadata": s["subscription_data"]["metadata"],
                "items": {"data": [{"id": self._id("si"), "price": self._price(price)}]},
            }
            self.objects[sub["id"]] = sub
            inv = self._invoice(
                sub=sub,
                customer=s["customer"],
                reason="subscription_create",
                lines=[self._line(price, now, end, self._price(price)["unit_amount"])],
                tax=tax,
            )
            sub["latest_invoice"] = inv["id"]
            s["subscription"], s["invoice"] = sub["id"], inv["id"]
        else:
            meta = s["invoice_creation"]["invoice_data"]["metadata"]
            inv = self._invoice(
                sub=None,
                customer=s["customer"],
                reason="manual",
                lines=[self._line(price, now, now, self._price(price)["unit_amount"])],
                tax=tax,
                meta=meta,
            )
            s["invoice"] = inv["id"]
            charge = {
                "id": self._id("ch"),
                "invoice": inv["id"],
                "payment_intent": inv["payment_intent"]["id"],
                "amount": inv["total"],
                "amount_refunded": 0,
            }
            self.objects[charge["id"]] = charge
            inv["charge"] = charge["id"]
        return inv

    def renew(self, sub_id: str, *, paid: bool = True, tax: int = 0) -> dict:
        """Stripe rolls the subscription into its next period and charges (or fails to charge) the invoice."""
        sub = self.objects[sub_id]
        start = sub["current_period_end"]
        price = sub["items"]["data"][0]["price"]
        end = start + (365 if price["recurring"]["interval"] == "year" else 30) * DAY
        sub["current_period_start"], sub["current_period_end"] = start, end
        inv = self._invoice(
            sub=sub, customer=sub["customer"], reason="subscription_cycle",
            lines=[self._line(price["id"], start, end, price["unit_amount"])],
            tax=tax, status="paid" if paid else "open",
        )  # fmt: skip
        sub["latest_invoice"] = inv["id"]
        sub["status"] = "active" if paid else "past_due"
        return inv

    def pay(self, invoice_id: str) -> dict:
        inv = self.objects[invoice_id]
        inv["status"] = "paid"
        inv["attempt_count"] += 1
        inv["next_payment_attempt"] = None
        inv["payment_intent"] = {"id": inv["payment_intent"]["id"], "status": "succeeded"}
        self.objects[inv["subscription"]]["status"] = "active"
        return inv

    def event(self, type_: str, obj_id: str, *, event_id: str | None = None) -> tuple[bytes, dict]:
        body = json.dumps({"id": event_id or f"evt_{uuid.uuid4().hex[:12]}", "type": type_, "data": {"object": {"id": obj_id}}}).encode()
        return body, {"Stripe-Signature": sign(body, WEBHOOK_SECRET), "Content-Type": "application/json"}

    # ---- the API -----------------------------------------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.headers["stripe-version"] == "2024-06-20"
        path = request.url.path
        form = dict(parse_qsl(request.content.decode())) if request.method == "POST" else dict(request.url.params.multi_items())
        self.requests.append((request.method, path, form))
        for prefix, code in list(self.fail_next.items()):
            if path.startswith(prefix):
                del self.fail_next[prefix]
                return httpx.Response(code, json={"error": {"type": "api_error", "message": "Internal error"}})
        return self.route(request.method, path, form)

    def route(self, method: str, path: str, f: dict) -> httpx.Response:
        parts = path.strip("/").split("/")[1:]  # drop "v1"
        res, rid = parts[0], parts[1] if len(parts) > 1 else None
        if res == "products" and method == "POST":
            p = {"id": self._id("prod"), "name": f["name"], "metadata": _meta(f)}
            self.objects[p["id"]] = p
            return _ok(p)
        if res == "prices" and method == "POST" and rid is None:
            p = {
                "id": self._id("price"),
                "product": f["product"],
                "unit_amount": int(f["unit_amount"]),
                "currency": f["currency"],
                "tax_behavior": f["tax_behavior"],
                "active": True,
                "metadata": _meta(f),
            }
            if "recurring[interval]" in f:
                p["recurring"] = {"interval": f["recurring[interval]"]}
            self.objects[p["id"]] = p
            return _ok(p)
        if res == "prices" and rid:
            self.objects[rid]["active"] = f.get("active") != "false"
            return _ok(self.objects[rid])
        if res == "customers":
            c = {"id": self._id("cus"), "email": f["email"]}
            self.objects[c["id"]] = c
            return _ok(c)
        if res == "checkout" and parts[1] == "sessions":
            if method == "POST":
                s = {
                    "id": self._id("cs"),
                    "url": f"https://checkout.stripe.test/{self.counter}",
                    "mode": f["mode"],
                    "status": "open",
                    "payment_status": "unpaid",
                    "customer": f["customer"],
                    "client_reference_id": f["client_reference_id"],
                    "metadata": _meta(f),
                    "line_items": [{"price": f["line_items[0][price]"]}],
                    "subscription": None,
                    "invoice": None,
                }
                if f["mode"] == "subscription":
                    s["subscription_data"] = {"metadata": _meta(f, "subscription_data[metadata]")}
                else:
                    s["invoice_creation"] = {"invoice_data": {"metadata": _meta(f, "invoice_creation[invoice_data][metadata]")}}
                self.objects[s["id"]] = s
                return _ok(s)
            return _ok(self.objects[parts[2]])
        if res == "subscriptions":
            sub = self.objects[rid]
            if method == "GET":
                return _ok(sub)
            return self.update_subscription(sub, f)
        if res == "invoices" and rid == "create_preview":
            sub = self.objects[f["subscription"]]
            new = self._price(f["subscription_details[items][0][price]"])
            old = sub["items"]["data"][0]["price"]
            credit = old["unit_amount"] // 2
            lines = [{"amount": -credit, "proration": True}, {"amount": new["unit_amount"], "proration": False}]
            tax = 100
            return _ok({"amount_due": new["unit_amount"] - credit + tax, "tax": tax, "lines": {"data": lines}})
        if res == "invoices" and len(parts) == 3 and parts[2] == "pay":
            return _ok(self.pay(rid))
        if res == "invoices":
            return _ok(self.objects[rid])
        if res == "charges":
            return _ok(self.objects[rid])
        if res == "billing_portal" and parts[1] == "configurations":
            c = {"id": self._id("bpc")}
            return _ok(c)
        if res == "billing_portal" and parts[1] == "sessions":
            return _ok({"id": self._id("bps"), "url": "https://billing.stripe.test/session"})
        return httpx.Response(404, json={"error": {"type": "invalid_request_error", "message": f"No route {method} {path}"}})

    def update_subscription(self, sub: dict, f: dict) -> httpx.Response:
        if "cancel_at_period_end" in f:
            sub["cancel_at_period_end"] = f["cancel_at_period_end"] == "true"
        if "items[0][price]" in f:
            new = self._price(f["items[0][price]"])
            if f.get("proration_behavior") == "always_invoice":
                if self.upgrade_outcome == "declined":
                    return httpx.Response(402, json={"error": {"type": "card_error", "message": "Your card was declined."}})
                now = int(time.time())
                old = sub["items"]["data"][0]["price"]
                if self.upgrade_outcome == "requires_action":
                    inv = self._invoice(sub=sub, customer=sub["customer"], reason="subscription_update", lines=[], tax=0, status="open")
                    inv["payment_intent"]["status"] = "requires_action"
                    sub["pending_update"] = {"subscription_items": [{"price": new["id"]}]}
                    sub["latest_invoice"] = inv
                    return _ok(sub)
                end = now + (365 if new["recurring"]["interval"] == "year" else 30) * DAY
                lines = [
                    {
                        "amount": -(old["unit_amount"] // 2),
                        "proration": True,
                        "price": old,
                        "period": {"start": now, "end": sub["current_period_end"]},
                    },
                    self._line(new["id"], now, end, new["unit_amount"]),
                ]
                sub["items"]["data"][0]["price"] = new
                sub["current_period_start"], sub["current_period_end"] = now, end
                inv = self._invoice(sub=sub, customer=sub["customer"], reason="subscription_update", lines=lines, tax=200)
                sub["latest_invoice"] = inv
                out = dict(sub)
                self.objects[sub["id"]] = {**sub, "latest_invoice": inv["id"]}
                return _ok(out)
            sub["items"]["data"][0]["price"] = new
        return _ok(sub)


def _ok(obj: dict) -> httpx.Response:
    return httpx.Response(200, json=obj)


def _meta(f: dict, prefix: str = "metadata") -> dict:
    out = {}
    for k, v in f.items():
        if k.startswith(prefix + "[") and k.count("[") == prefix.count("[") + 1:
            out[k[len(prefix) + 1 : -1]] = v
    return out
