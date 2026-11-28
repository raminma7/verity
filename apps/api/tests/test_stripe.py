"""Stripe behind the payment interface, against an in-memory Stripe (httpx.MockTransport; never the network):
request shapes (tax, address, tax ID, prorations), webhook signatures, idempotent and out-of-order events,
renewals, failed payments, upgrades, downgrades, cancellation, the portal, refunds and price changes."""

import asyncio
import logging
import time
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import BillingEvent, Payment, Plan, Subscription, SystemSetting, TokenGrant, TokenLedger
from app.services import email as mail
from app.services import payments
from app.services.stripe_provider import SignatureError, StripeProvider, encode, sign, verify_signature
from tests.conftest import login, new_client, signup
from tests.stripe_fake import SECRET, WEBHOOK_SECRET, FakeStripe

HOOK = "/api/billing/webhooks/stripe"


@pytest.fixture
async def stripe(db):
    s = get_settings()
    saved = (s.payment_provider, s.stripe_secret_key, s.stripe_webhook_secret)
    s.payment_provider, s.stripe_secret_key, s.stripe_webhook_secret = "stripe", SECRET, WEBHOOK_SECRET
    fake = FakeStripe()
    payments.set_provider("stripe", StripeProvider.from_settings(transport=httpx.MockTransport(fake.handler)))
    yield fake
    payments.set_provider("stripe", None)
    s.payment_provider, s.stripe_secret_key, s.stripe_webhook_secret = saved
    # Plans keep their Stripe prices between tests otherwise.
    async with SessionLocal() as x:
        for p in (await x.execute(select(Plan))).scalars():
            p.provider_price_ref = p.provider_product_ref = None
        await x.commit()


async def plan_id(client, key):
    return next(p["id"] for p in (await client.get("/api/plans")).json() if p["key"] == key)


async def deliver(client, fake, type_, obj_id, **kw):
    body, headers = fake.event(type_, obj_id, **kw)
    return await client.post(HOOK, content=body, headers=headers)


async def buy(client, fake, key="starter", *, tax=0):
    r = await client.post("/api/billing/checkout", json={"plan_id": await plan_id(client, key)})
    assert r.status_code == 201, r.text
    session = fake.last("POST", "/v1/checkout/sessions")
    cs = next(o for o in fake.objects.values() if o.get("client_reference_id") == r.json()["checkout_id"])
    assert r.json()["url"] == cs["url"]
    inv = fake.complete_checkout(cs["id"], tax=tax)
    return r.json()["checkout_id"], cs, inv, session


# ---- signatures -------------------------------------------------------------------------------------------


def test_signature_checks():
    body = b'{"id":"evt_1"}'
    verify_signature(body, sign(body, "whsec_a"), "whsec_a")
    with pytest.raises(SignatureError):
        verify_signature(body, sign(body, "whsec_b"), "whsec_a")  # wrong secret
    with pytest.raises(SignatureError):
        verify_signature(body + b" ", sign(body, "whsec_a"), "whsec_a")  # tampered body
    with pytest.raises(SignatureError):
        verify_signature(body, sign(body, "whsec_a", t=int(time.time()) - 301), "whsec_a")  # too old
    with pytest.raises(SignatureError):
        verify_signature(body, None, "whsec_a")
    with pytest.raises(SignatureError):
        verify_signature(body, "t=abc,v1=00", "whsec_a")
    # Several v1 signatures (a rotated secret): any match is enough.
    good = sign(body, "whsec_a")
    t = good.split(",")[0]
    verify_signature(body, f"{t},v1={'0' * 64},{good.split(',')[1]}", "whsec_a")


def test_form_encoding():
    assert encode({"a": {"b": [{"c": 1}], "d": True}, "e": None, "f": ["x", "y"]}) == [
        ("a[b][0][c]", "1"),
        ("a[d]", "true"),
        ("f[0]", "x"),
        ("f[1]", "y"),
    ]


def test_stripe_needs_both_keys():
    s = get_settings()
    saved = (s.payment_provider, s.stripe_secret_key, s.stripe_webhook_secret)
    try:
        s.payment_provider, s.stripe_secret_key, s.stripe_webhook_secret = "stripe", "sk_test_x", None
        with pytest.raises(RuntimeError, match="STRIPE_WEBHOOK_SECRET"):
            payments.check_configuration()
    finally:
        s.payment_provider, s.stripe_secret_key, s.stripe_webhook_secret = saved


async def test_webhooks_need_a_valid_signature(client, stripe):
    body, headers = stripe.event("invoice.paid", "in_1")
    r = await client.post(HOOK, content=body, headers={"Stripe-Signature": sign(body, "whsec_wrong")})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_signature"
    r = await client.post(HOOK, content=body)
    assert r.status_code == 400
    async with SessionLocal() as db:
        assert (await db.execute(select(BillingEvent))).scalars().all() == []


# ---- purchase ---------------------------------------------------------------------------------------------


async def test_checkout_collects_tax_address_and_tax_id(client, stripe):
    await signup(client, "tax@example.com")
    cid, cs, inv, session = await buy(client, stripe, tax=580)
    product = stripe.last("POST", "/v1/products")
    price = stripe.last("POST", "/v1/prices")
    assert product["metadata[verity_kind]"] == "plan"
    assert price["unit_amount"] == "2900" and price["currency"] == "usd" and price["recurring[interval]"] == "month"
    assert price["tax_behavior"] == "exclusive"
    assert stripe.last("POST", "/v1/customers")["email"] == "tax@example.com"
    assert session["mode"] == "subscription"
    assert session["automatic_tax[enabled]"] == "true"
    assert session["billing_address_collection"] == "required"
    assert session["tax_id_collection[enabled]"] == "true"
    assert session["customer_update[address]"] == "auto" and session["customer_update[name]"] == "auto"
    assert session["client_reference_id"] == cid
    assert session["subscription_data[metadata][workspace_id]"] == client.headers["X-Workspace-Id"]
    assert session["success_url"].endswith(f"/billing/return?checkout={cid}")
    # Nothing is active until Stripe says it's paid.
    assert (await client.get("/api/workspace")).json()["subscription"] is None

    r = await deliver(client, stripe, "checkout.session.completed", cs["id"])
    assert r.status_code == 200 and r.json()["status"] == "processed", r.text
    b = (await client.get("/api/billing")).json()
    sub = b["subscription"]
    assert sub["status"] == "active" and sub["provider"] == "stripe"
    [p] = b["payments"]
    assert (p["subtotal_cents"], p["tax_cents"], p["amount_cents"]) == (2900, 580, 3480)
    assert p["number"] == inv["number"] and p["pdf_url"].endswith(".pdf") and p["kind"] == "subscription"
    assert b["account"]["currency"] == "USD"
    async with SessionLocal() as db:
        s = (await db.execute(select(Subscription))).scalar_one()
        assert s.provider_subscription_ref == cs["subscription"]
        assert int(s.current_period_end.timestamp()) == stripe.objects[cs["subscription"]]["current_period_end"]
        ledger = (await db.execute(select(TokenLedger).where(TokenLedger.is_current))).scalar_one()
        assert ledger.period_end == s.current_period_end
    assert any("Receipt" in m.subject for m in mail.OUTBOX)


async def test_events_apply_once_in_any_order(client, stripe):
    await signup(client, "order@example.com")
    _, cs, inv, _ = await buy(client, stripe)
    # The first invoice arrives before the checkout; then both are redelivered.
    assert (await deliver(client, stripe, "invoice.paid", inv["id"], event_id="evt_inv")).json()["status"] == "processed"
    assert (await deliver(client, stripe, "checkout.session.completed", cs["id"], event_id="evt_cs")).json()["status"] == "processed"
    assert (await deliver(client, stripe, "invoice.paid", inv["id"], event_id="evt_inv")).json()["status"] == "duplicate"
    assert (await deliver(client, stripe, "checkout.session.completed", cs["id"], event_id="evt_cs")).json()["status"] == "duplicate"
    async with SessionLocal() as db:
        assert len((await db.execute(select(Payment))).scalars().all()) == 1
        assert len((await db.execute(select(TokenLedger))).scalars().all()) == 1
        statuses = sorted((await db.execute(select(BillingEvent.status))).scalars().all())
        assert statuses == ["processed", "processed"]


async def test_concurrent_deliveries_of_one_event_activate_once(client, stripe):
    await signup(client, "twice@example.com")
    _, cs, _, _ = await buy(client, stripe)
    body, headers = stripe.event("checkout.session.completed", cs["id"], event_id="evt_same")

    async def post():
        async with new_client() as c:
            return (await c.post(HOOK, content=body, headers=headers)).json()["status"]

    results = sorted(await asyncio.gather(post(), post(), post()))
    assert results == ["duplicate", "duplicate", "processed"]
    async with SessionLocal() as db:
        assert len((await db.execute(select(TokenLedger))).scalars().all()) == 1


async def test_a_failed_event_is_recorded_and_retried(client, stripe):
    await signup(client, "retry@example.com")
    _, cs, _, _ = await buy(client, stripe)
    stripe.fail_next["/v1/checkout/sessions/"] = 500
    r = await deliver(client, stripe, "checkout.session.completed", cs["id"], event_id="evt_flaky")
    assert r.status_code == 500
    async with SessionLocal() as db:
        ev = (await db.execute(select(BillingEvent))).scalar_one()
        assert ev.status == "failed" and "ProviderError" in ev.error
    r = await deliver(client, stripe, "checkout.session.completed", cs["id"], event_id="evt_flaky")
    assert r.json()["status"] == "processed"
    assert (await client.get("/api/billing")).json()["subscription"]["status"] == "active"


async def test_unknown_events_are_ignored_and_nothing_sensitive_is_logged(client, stripe, caplog):
    caplog.set_level(logging.DEBUG)
    await signup(client, "quiet@example.com")
    r = await deliver(client, stripe, "customer.created", "cus_x")
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    _, cs, _, _ = await buy(client, stripe)
    await deliver(client, stripe, "checkout.session.completed", cs["id"])
    assert "quiet@example.com" not in caplog.text and "4242" not in caplog.text and SECRET not in caplog.text
    cols = set(BillingEvent.__table__.columns.keys())
    assert not cols & {"payload", "body", "data"}


# ---- renewals and failed payments -------------------------------------------------------------------------


async def active(client, stripe, key="starter"):
    await signup(client, f"{uuid.uuid4().hex[:8]}@example.com")
    _, cs, _, _ = await buy(client, stripe, key)
    await deliver(client, stripe, "checkout.session.completed", cs["id"])
    return cs["subscription"]


async def test_renewal_follows_stripe(client, stripe, db):
    sub_ref = await active(client, stripe)
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 1000, "output_tokens": 0})
    inv = stripe.renew(sub_ref, tax=290)
    assert (await deliver(client, stripe, "invoice.paid", inv["id"])).json()["status"] == "processed"
    b = (await client.get("/api/billing")).json()
    assert b["payments"][0]["kind"] == "renewal" and b["payments"][0]["tax_cents"] == 290
    assert (
        int(__import__("datetime").datetime.fromisoformat(b["subscription"]["current_period_end"]).timestamp())
        == stripe.objects[sub_ref]["current_period_end"]
    )
    assert (await client.get("/api/usage/summary")).json()["consumed_weighted"] == 0
    # The renewal job leaves Stripe subscriptions to Stripe.
    from app.services.subscriptions import renew_due

    assert await renew_due(db) == {"renewed": 0, "canceled": 0, "failed": 0}


async def test_failed_renewal_then_recovery(client, stripe):
    sub_ref = await active(client, stripe)
    inv = stripe.renew(sub_ref, paid=False)
    assert (await deliver(client, stripe, "invoice.payment_failed", inv["id"])).json()["status"] == "processed"
    b = (await client.get("/api/billing")).json()
    assert b["subscription"]["status"] == "past_due" and b["subscription"]["next_retry_at"]
    failed = b["payments"][0]
    assert failed["status"] == "failed" and failed["failure_message"] == "Your card was declined."
    assert any(m.subject.startswith("Payment failed") for m in mail.OUTBOX)
    # The customer updates the card in the portal and retries from Verity.
    r = await client.post("/api/billing/retry")
    assert r.status_code == 200 and r.json()["status"] == "active", r.text
    assert stripe.find("POST", f"/v1/invoices/{inv['id']}/pay")
    # Stripe's own invoice.paid for the same invoice is then a no-op.
    await deliver(client, stripe, "invoice.paid", inv["id"])
    b = (await client.get("/api/billing")).json()
    assert [p["status"] for p in b["payments"]] == ["succeeded", "succeeded"]
    assert any(m.subject.startswith("Payment received") for m in mail.OUTBOX)


async def test_stripe_ends_an_unpaid_subscription(client, stripe):
    sub_ref = await active(client, stripe)
    inv = stripe.renew(sub_ref, paid=False)
    await deliver(client, stripe, "invoice.payment_failed", inv["id"])
    stripe.objects[sub_ref]["status"] = "canceled"
    await deliver(client, stripe, "customer.subscription.deleted", sub_ref)
    ws = (await client.get("/api/workspace")).json()
    assert ws["subscription"]["active"] is False
    assert any("has ended" in m.subject for m in mail.OUTBOX)


# ---- plan changes -----------------------------------------------------------------------------------------


async def test_upgrade_is_prorated_and_applied_once(client, stripe):
    sub_ref = await active(client, stripe)
    growth = await plan_id(client, "growth")
    preview = (await client.get("/api/billing/change-preview", params={"plan_id": growth})).json()
    assert preview["kind"] == "upgrade" and preview["credit_cents"] == 1450 and preview["tax_cents"] == 100
    assert preview["amount_due_cents"] == 9900 - 1450
    form = stripe.last("POST", "/v1/invoices/create_preview")
    assert form["subscription_details[proration_behavior]"] == "always_invoice"
    assert form["subscription_details[billing_cycle_anchor]"] == "now"
    r = await client.post("/api/billing/change", json={"plan_id": growth})
    assert r.status_code == 200 and r.json()["status"] == "upgraded", r.text
    form = stripe.last("POST", f"/v1/subscriptions/{sub_ref}")
    assert form["proration_behavior"] == "always_invoice"
    assert form["billing_cycle_anchor"] == "now"
    assert form["payment_behavior"] == "pending_if_incomplete"
    b = (await client.get("/api/billing")).json()
    assert b["subscription"]["terms"]["plan_key"] == "growth"
    up = b["payments"][0]
    assert up["kind"] == "upgrade" and up["tax_cents"] == 200 and up["amount_cents"] == 9900 - 1450 + 200
    # Stripe's webhook for the same invoice doesn't upgrade again.
    inv_id = stripe.objects[sub_ref]["latest_invoice"]
    assert (await deliver(client, stripe, "invoice.paid", inv_id)).json()["status"] == "processed"
    async with SessionLocal() as db:
        assert len((await db.execute(select(TokenLedger))).scalars().all()) == 2
        assert len((await db.execute(select(Payment))).scalars().all()) == 2


async def test_upgrade_needing_authentication_or_declined(client, stripe):
    await active(client, stripe)
    growth = await plan_id(client, "growth")
    stripe.upgrade_outcome = "requires_action"
    r = await client.post("/api/billing/change", json={"plan_id": growth})
    assert r.json()["status"] == "action_required" and r.json()["url"].startswith("https://invoice.stripe.test/")
    stripe.upgrade_outcome = "declined"
    r = await client.post("/api/billing/change", json={"plan_id": growth})
    assert r.status_code == 402 and r.json()["detail"]["message"] == "Your card was declined."
    assert (await client.get("/api/billing")).json()["subscription"]["terms"]["plan_key"] == "starter"


async def test_downgrade_is_billed_at_renewal(client, stripe):
    sub_ref = await active(client, stripe, "growth")
    r = await client.post("/api/billing/change", json={"plan_id": await plan_id(client, "starter")})
    assert r.json()["status"] == "scheduled"
    form = stripe.last("POST", f"/v1/subscriptions/{sub_ref}")
    assert form["proration_behavior"] == "none"
    assert (await client.get("/api/billing")).json()["subscription"]["terms"]["plan_key"] == "growth"
    inv = stripe.renew(sub_ref)  # Stripe bills the new (Starter) price
    await deliver(client, stripe, "invoice.paid", inv["id"])
    b = (await client.get("/api/billing")).json()
    assert b["subscription"]["terms"]["plan_key"] == "starter" and b["subscription"]["pending_plan"] is None


async def test_cancel_at_period_end_and_resume(client, stripe):
    sub_ref = await active(client, stripe)
    assert (await client.post("/api/billing/cancel")).status_code == 200
    assert stripe.objects[sub_ref]["cancel_at_period_end"] is True
    assert (await client.post("/api/billing/resume")).status_code == 200
    assert stripe.objects[sub_ref]["cancel_at_period_end"] is False


async def test_portal_has_no_plan_changes_and_is_configured_once(client, stripe):
    await active(client, stripe)
    for _ in range(2):
        r = await client.post("/api/billing/portal")
        assert r.status_code == 200 and r.json()["url"] == "https://billing.stripe.test/session"
    [config] = stripe.find("POST", "/v1/billing_portal/configurations")
    assert config["features[subscription_update][enabled]"] == "false"
    assert config["features[subscription_cancel][enabled]"] == "false"
    assert config["features[payment_method_update][enabled]"] == "true"
    assert config["features[invoice_history][enabled]"] == "true"
    assert stripe.last("POST", "/v1/billing_portal/sessions")["return_url"].endswith("/billing")
    async with SessionLocal() as db:
        assert (await db.get(SystemSetting, "stripe_portal_configuration")).value.startswith("bpc_")
    assert (await client.put("/api/billing/test-card", json={"card": "declines"})).status_code == 409


# ---- packages, refunds and prices -------------------------------------------------------------------------


async def test_package_purchase_and_refund(client, stripe):
    await active(client, stripe)
    pkg = (await client.get("/api/billing/packages")).json()["packages"][0]
    r = await client.post(f"/api/billing/packages/{pkg['id']}/checkout")
    assert r.status_code == 201, r.text
    session = stripe.last("POST", "/v1/checkout/sessions")
    assert session["mode"] == "payment" and session["invoice_creation[enabled]"] == "true"
    assert session["automatic_tax[enabled]"] == "true" and session["tax_id_collection[enabled]"] == "true"
    price = stripe.last("POST", "/v1/prices")
    assert "recurring[interval]" not in price and price["metadata[verity_kind]"] == "package"
    cs = next(o for o in stripe.objects.values() if o.get("client_reference_id") == r.json()["checkout_id"])
    inv = stripe.complete_checkout(cs["id"], tax=240)
    assert (await deliver(client, stripe, "checkout.session.completed", cs["id"])).json()["status"] == "processed"
    await deliver(client, stripe, "invoice.paid", inv["id"])  # fills in nothing new, credits nothing twice
    listing = (await client.get("/api/billing/packages")).json()
    assert listing["remaining"] == 1_000_000 and len(listing["grants"]) == 1
    b = (await client.get("/api/billing")).json()
    assert b["payments"][0]["kind"] == "package" and b["payments"][0]["tax_cents"] == 240
    charge = stripe.objects[inv["charge"]]
    charge["amount_refunded"] = charge["amount"]
    assert (await deliver(client, stripe, "charge.refunded", charge["id"])).json()["status"] == "processed"
    assert (await client.get("/api/billing/packages")).json()["remaining"] == 0
    async with SessionLocal() as db:
        assert (await db.execute(select(TokenGrant.status))).scalar_one() == "revoked"


async def test_price_changes_make_a_new_price_billed_from_renewal(client, stripe, db):
    sub_ref = await active(client, stripe)
    async with new_client() as admin:
        assert (await login(admin, "root@example.org", "Admin-pass-123")).status_code == 200
        starter = await plan_id(client, "starter")
        r = await admin.patch(f"/api/admin/plans/{starter}", json={"currency": "EUR"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_currency_locked"
        old = (await db.execute(select(Plan.provider_price_ref).where(Plan.key == "starter"))).scalar_one()
        r = await admin.patch(f"/api/admin/plans/{starter}", json={"price_cents": 3500})
        assert r.status_code == 200
    assert stripe.objects[old]["active"] is False
    from app.services.billing import sync_prices

    async with SessionLocal() as s:
        assert await sync_prices(s) == 1
        assert await sync_prices(s) == 0  # already moved
    form = stripe.last("POST", f"/v1/subscriptions/{sub_ref}")
    assert form["proration_behavior"] == "none"
    new = stripe.objects[form["items[0][price]"]]
    assert new["unit_amount"] == 3500 and new["id"] != old
    async with SessionLocal() as s:
        p = (await s.execute(select(Plan).where(Plan.key == "starter"))).scalar_one()
        p.price_cents = 2900
        p.provider_price_ref = None
        await s.commit()


async def test_deleting_the_workspace_cancels_the_stripe_subscription_now(client, stripe):
    from tests.conftest import PASSWORD

    sub_ref = await active(client, stripe)
    name = (await client.get("/api/workspace")).json()["workspace"]["name"]
    r = await client.post("/api/workspace/delete", json={"password": PASSWORD, "confirm_name": name})
    assert r.status_code == 200, r.text
    assert ("DELETE", f"/v1/subscriptions/{sub_ref}", {}) in stripe.requests
    async with SessionLocal() as db:
        assert (await db.execute(select(Subscription.status))).scalar_one() == "canceled"
