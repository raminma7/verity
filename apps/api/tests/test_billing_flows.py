"""Phase 5B payments with the mock provider: purchase, prorated upgrades, downgrades at renewal, cancellation,
failed renewals (past due, retries, grace period, recovery), invoices, the currency lock, roles and tenants."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import AuditLog, BillingEvent, Notification, Payment, Plan, Subscription, TokenLedger
from app.models.base import utcnow
from app.services import email as mail
from app.services.subscriptions import renew_due
from tests.conftest import new_client, signup, subscribe
from tests.test_phase3_access import team


async def plan_id(client, key: str) -> str:
    return next(p["id"] for p in (await client.get("/api/plans")).json() if p["key"] == key)


async def billing(client) -> dict:
    r = await client.get("/api/billing")
    assert r.status_code == 200, r.text
    return r.json()


async def half_way(db) -> None:
    """Moves the current period so that half of it is left."""
    now = utcnow()
    await db.execute(
        update(Subscription).values(current_period_start=now - timedelta(days=15), current_period_end=now + timedelta(days=15))
    )
    await db.commit()


async def test_purchase_records_an_invoice_through_the_event_path(client, db):
    await signup(client, "buy@example.com")
    await subscribe(client, "starter")
    b = await billing(client)
    assert b["provider"] == "mock"
    assert b["account"]["currency"] == "USD"
    assert b["subscription"]["status"] == "active" and b["subscription"]["provider"] == "mock"
    [p] = b["payments"]
    assert p["kind"] == "subscription" and p["status"] == "succeeded" and p["number"].startswith("TEST-")
    assert p["subtotal_cents"] == 2900 and p["tax_cents"] == 0 and p["amount_cents"] == 2900
    events = (await db.execute(select(BillingEvent))).scalars().all()
    assert [(e.provider, e.type, e.status) for e in events] == [("mock", "checkout.completed", "processed")]
    assert (await db.execute(select(Payment.mrr_cents))).scalar_one() == 2900


async def test_upgrade_is_immediate_and_prorated(client, db):
    await signup(client, "up@example.com")
    await subscribe(client, "starter")
    await half_way(db)
    growth = await plan_id(client, "growth")
    preview = (await client.get("/api/billing/change-preview", params={"plan_id": growth})).json()
    assert preview["kind"] == "upgrade"
    assert 1440 <= preview["credit_cents"] <= 1450  # half of $29 left
    assert preview["amount_due_cents"] == 9900 - preview["credit_cents"]
    assert preview["tax_note"]
    # The checkout endpoint doesn't take upgrades: they're charged to the card on file.
    r = await client.post("/api/billing/checkout", json={"plan_id": growth})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "use_plan_change"
    r = await client.post("/api/billing/change", json={"plan_id": growth})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "upgraded"
    b = await billing(client)
    assert b["subscription"]["terms"]["plan_key"] == "growth"
    start = b["subscription"]["current_period_start"]
    assert abs((utcnow() - __import__("datetime").datetime.fromisoformat(start)).total_seconds()) < 60  # a new period starts now
    upgrade = next(p for p in b["payments"] if p["kind"] == "upgrade")
    assert upgrade["amount_cents"] == preview["amount_due_cents"]
    assert upgrade["description"].startswith("Upgrade to Growth")
    usage = (await client.get("/api/usage/summary")).json()
    assert usage["allowance"] == 5_000_000 and usage["consumed_weighted"] == 0
    assert any("Receipt" in m.subject for m in mail.OUTBOX[-2:])
    assert (await db.execute(select(AuditLog).where(AuditLog.action == "subscription.upgraded"))).scalar_one()


async def test_declined_upgrade_changes_nothing(client):
    await signup(client, "decl@example.com")
    await subscribe(client, "starter")
    assert (await client.put("/api/billing/test-card", json={"card": "declines"})).status_code == 200
    r = await client.post("/api/billing/change", json={"plan_id": await plan_id(client, "growth")})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "payment_failed"
    assert "declined" in r.json()["detail"]["message"]
    b = await billing(client)
    assert b["subscription"]["terms"]["plan_key"] == "starter" and len(b["payments"]) == 1


async def test_downgrade_waits_for_renewal_and_can_be_dropped(client, db):
    await signup(client, "down@example.com")
    await subscribe(client, "growth")
    starter = await plan_id(client, "starter")
    preview = (await client.get("/api/billing/change-preview", params={"plan_id": starter})).json()
    assert preview["kind"] == "downgrade" and preview["amount_due_cents"] == 0
    r = await client.post("/api/billing/change", json={"plan_id": starter})
    assert r.json()["status"] == "scheduled"
    assert (await billing(client))["subscription"]["pending_plan"]["name"] == "Starter"
    assert (await client.delete("/api/billing/pending-change")).status_code == 204
    assert (await billing(client))["subscription"]["pending_plan"] is None
    await client.post("/api/billing/change", json={"plan_id": starter})
    await db.execute(update(Subscription).values(current_period_end=utcnow() - timedelta(minutes=1)))
    await db.commit()
    assert (await renew_due(db))["renewed"] == 1
    b = await billing(client)
    assert b["subscription"]["terms"]["plan_key"] == "starter"
    assert b["payments"][0]["kind"] == "renewal" and b["payments"][0]["amount_cents"] == 2900


async def test_cancel_and_resume(client, db):
    await signup(client, "cr@example.com")
    await subscribe(client)
    assert (await client.post("/api/billing/cancel")).status_code == 200
    assert (await billing(client))["subscription"]["cancel_at_period_end"] is True
    assert (await client.post("/api/billing/resume")).status_code == 200
    assert (await billing(client))["subscription"]["cancel_at_period_end"] is False


async def test_failed_renewal_is_past_due_then_recovers(client, db):
    await signup(client, "late@example.com")
    await subscribe(client)
    await client.put("/api/billing/test-card", json={"card": "declines"})
    r = await client.post("/api/dev/billing/renew")
    assert r.json()["failed"] == 1
    b = await billing(client)
    sub = b["subscription"]
    assert sub["status"] == "past_due" and sub["active"] is True and sub["grace_ends_at"]
    failed = b["payments"][0]
    assert failed["status"] == "failed" and failed["kind"] == "renewal" and failed["failure_message"] == "Your card was declined."
    assert failed["next_retry_at"]
    ws = (await client.get("/api/workspace")).json()
    # AI pauses (no new token period); manual ticketing stays open during the grace period.
    assert ws["subscription"]["active"] is True and ws["ai_available"] is False
    t = await client.post("/api/tickets", json={"subject": "Still works"})
    assert t.status_code == 201, t.text
    assert any(m.subject.startswith("Payment failed") for m in mail.OUTBOX)
    assert (await db.execute(select(Notification).where(Notification.kind == "payment_failed"))).scalar_one()
    # Plan changes wait until the payment is settled.
    r = await client.post("/api/billing/change", json={"plan_id": await plan_id(client, "growth")})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "past_due"
    # Retrying with the same declining card fails again; a working card recovers it.
    assert (await client.post("/api/billing/retry")).status_code == 402
    await client.put("/api/billing/test-card", json={"card": "ok"})
    r = await client.post("/api/billing/retry")
    assert r.status_code == 200 and r.json()["status"] == "active"
    b = await billing(client)
    assert b["subscription"]["status"] == "active" and b["subscription"]["past_due_since"] is None
    assert [p["status"] for p in b["payments"]] == ["succeeded", "succeeded"]  # the failed invoice was paid
    assert b["payments"][0]["attempt_count"] >= 2
    assert (await client.get("/api/workspace")).json()["ai_available"] is True
    assert any(m.subject.startswith("Payment received") for m in mail.OUTBOX)


async def test_retries_follow_the_schedule_then_the_subscription_ends(client, db):
    await signup(client, "gone@example.com")
    await subscribe(client)
    await client.put("/api/billing/test-card", json={"card": "declines"})
    await client.post("/api/dev/billing/renew")
    sub = (await db.execute(select(Subscription))).scalar_one()
    since = sub.past_due_since
    assert sub.next_retry_at - since == timedelta(days=1)
    for day, expected in ((1, 3), (3, 7), (7, 14)):
        r = await renew_due(db, now=since + timedelta(days=day, minutes=1))
        assert r["failed"] == 1, r
        await db.refresh(sub)
        assert sub.next_retry_at - since == timedelta(days=expected)
    assert (await renew_due(db, now=since + timedelta(days=14, minutes=1)))["canceled"] == 1
    await db.refresh(sub)
    assert sub.status == "canceled"
    ws = (await client.get("/api/workspace")).json()
    assert ws["subscription"]["active"] is False
    assert any("has ended" in m.subject for m in mail.OUTBOX)
    assert (await client.post("/api/tickets", json={"subject": "x"})).status_code == 402


async def test_currency_stays_the_first_one_paid_in(client, db):
    await signup(client, "eur@example.com")
    db.add(
        Plan(
            key="starter-eur",
            name="Starter EUR",
            price_cents=2700,
            currency="EUR",
            billing_cycle="month",
            token_allowance=1_000_000,
            limits={},
        )
    )
    db.add(
        Plan(
            key="growth-eur",
            name="Growth EUR",
            price_cents=9500,
            currency="EUR",
            billing_cycle="month",
            token_allowance=5_000_000,
            limits={},
        )
    )
    await db.commit()
    await subscribe(client, "starter")
    r = await client.post("/api/billing/change", json={"plan_id": await plan_id(client, "growth-eur")})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "currency_mismatch"
    assert r.json()["detail"]["workspace_currency"] == "USD"
    await client.post("/api/billing/cancel")
    await db.execute(update(Subscription).values(current_period_end=utcnow() - timedelta(minutes=1)))
    await db.commit()
    await renew_due(db)
    r = await client.post("/api/billing/checkout", json={"plan_id": await plan_id(client, "starter-eur")})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "currency_mismatch"
    assert (await client.post("/api/billing/checkout", json={"plan_id": await plan_id(client, "growth")})).status_code == 201
    await db.execute(Plan.__table__.delete().where(Plan.key.in_(("starter-eur", "growth-eur"))))
    await db.commit()


async def test_portal_and_test_card(client):
    await signup(client, "portal@example.com")
    await subscribe(client)
    r = await client.post("/api/billing/portal")
    assert r.status_code == 200 and r.json()["url"].endswith("/billing/test-portal")
    assert (await client.put("/api/billing/test-card", json={"card": "stolen"})).status_code == 422
    assert (await billing(client))["account"]["test_card"] == "ok"


@pytest.mark.parametrize("role", ["admin", "agent", "viewer", "custom"])
async def test_only_billing_managers_change_billing(client, role):
    await signup(client, "boss@example.com")
    await subscribe(client, "growth")
    clients = await team(client)
    c = clients[role]
    growth = await plan_id(client, "scale")
    for method, path, body in (
        ("post", "/api/billing/change", {"plan_id": growth}),
        ("post", "/api/billing/cancel", None),
        ("post", "/api/billing/retry", None),
        ("post", "/api/billing/portal", None),
        ("put", "/api/billing/test-card", {"card": "declines"}),
        ("post", "/api/dev/billing/renew", None),
    ):
        r = await getattr(c, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (role, path, r.text)
    # No default role but the owner reads billing (admins are kept out of billing by design).
    assert (await c.get("/api/billing")).status_code == 403
    for other in clients.values():
        if other is not client:
            await other.aclose()


async def test_checkouts_and_retries_are_per_workspace(client):
    await signup(client, "a@example.com")
    r = await client.post("/api/billing/checkout", json={"plan_id": await plan_id(client, "starter")})
    cid = r.json()["checkout_id"]
    async with new_client() as other:
        await signup(other, "b@example.com")
        assert (await other.get(f"/api/billing/checkout/{cid}")).status_code == 404
        assert (await other.post(f"/api/billing/checkout/{cid}/confirm")).status_code == 404
        assert (await other.post("/api/billing/retry")).status_code == 409
    assert (await client.post(f"/api/billing/checkout/{cid}/confirm")).status_code == 200


async def test_stripe_webhook_is_closed_without_stripe(client):
    r = await client.post("/api/billing/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})
    assert r.status_code == 404


async def test_renewal_keeps_ledgers_per_period(client, db):
    await signup(client, "ledger@example.com")
    await subscribe(client)
    await client.post("/api/dev/billing/renew")
    async with SessionLocal() as s:
        ledgers = (await s.execute(select(TokenLedger).order_by(TokenLedger.period_start))).scalars().all()
    assert [lg.is_current for lg in ledgers] == [False, True]
