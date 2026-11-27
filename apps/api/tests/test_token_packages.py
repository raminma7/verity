"""Token packages: defined by system admins, bought by subscribed workspaces, spent after the period's
allowance, carried over renewals, expiring after 12 months, revoked on refund, and safe under concurrency."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import AuditLog, Payment, Subscription, TokenGrant, TokenLedger, TokenPackage
from app.models.base import utcnow
from app.services import billing_events, quota
from app.services.payments import ProviderEvent, RefundInfo
from app.services.subscriptions import renew_due
from tests.conftest import login, new_client, signup, subscribe


@pytest.fixture
async def admin():
    async with new_client() as c:
        assert (await login(c, "root@example.org", "Admin-pass-123")).status_code == 200
        yield c


async def buy(client, key="tokens-1m") -> dict:
    listing = (await client.get("/api/billing/packages")).json()
    pkg = next(p for p in listing["packages"] if p["key"] == key)
    r = await client.post(f"/api/billing/packages/{pkg['id']}/checkout")
    assert r.status_code == 201, r.text
    assert r.json()["url"].endswith(f"/checkout/{r.json()['checkout_id']}")
    checkout = (await client.get(f"/api/billing/checkout/{r.json()['checkout_id']}")).json()
    assert checkout["kind"] == "package" and checkout["package"]["key"] == key
    r = await client.post(f"/api/billing/checkout/{r.json()['checkout_id']}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "paid"
    return pkg


async def test_buying_a_package_adds_tokens_that_roll_over(client, db):
    await signup(client, "pk@example.com")
    listing = (await client.get("/api/billing/packages")).json()
    assert [p["key"] for p in listing["packages"]] == ["tokens-1m", "tokens-5m"]
    assert listing["can_buy"] is False  # needs a subscription
    pkg = listing["packages"][0]
    r = await client.post(f"/api/billing/packages/{pkg['id']}/checkout")
    assert r.status_code == 402 and r.json()["detail"]["code"] == "subscription_required"

    await subscribe(client)
    await buy(client)
    listing = (await client.get("/api/billing/packages")).json()
    assert listing["remaining"] == 1_000_000
    [g] = listing["grants"]
    assert g["status"] == "active" and g["name"] == "1M tokens"
    expires = __import__("datetime").datetime.fromisoformat(g["expires_at"])
    assert 360 <= (expires - utcnow()).days <= 366
    usage = (await client.get("/api/usage/summary")).json()
    assert usage["package_remaining"] == 1_000_000 and usage["package_next_expiry"]
    b = (await client.get("/api/billing")).json()
    assert b["payments"][0]["kind"] == "package" and b["payments"][0]["description"] == "Token package: 1M tokens"
    assert b["payments"][0]["amount_cents"] == 1200
    assert (await db.execute(select(Payment.mrr_cents).where(Payment.kind == "package"))).scalar_one() == 0
    assert (await db.execute(select(AuditLog).where(AuditLog.action == "tokens.package_bought"))).scalar_one()
    # Confirming twice credits once.
    cid = (await db.execute(select(Payment.checkout_id).where(Payment.kind == "package"))).scalar_one()
    assert (await client.post(f"/api/billing/checkout/{cid}/confirm")).status_code == 200
    assert len((await db.execute(select(TokenGrant))).scalars().all()) == 1

    # Package tokens survive the renewal; the allowance resets.
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 1000, "output_tokens": 0})
    await db.execute(update(Subscription).values(current_period_end=utcnow()))
    await db.commit()
    await renew_due(db)
    assert (await client.get("/api/billing/packages")).json()["remaining"] == 1_000_000
    assert (await client.get("/api/usage/summary")).json()["consumed_weighted"] == 0


async def test_packages_are_spent_after_the_allowance(client, db):
    await signup(client, "spill@example.com")
    await subscribe(client)
    await buy(client)
    await db.execute(update(TokenLedger).values(consumed=1_000_000 - 100))
    await db.commit()
    r = await client.post("/api/dev/simulate-usage", json={"input_tokens": 1000, "output_tokens": 0})
    assert r.status_code == 200, r.text
    ledger = (await db.execute(select(TokenLedger).where(TokenLedger.is_current))).scalar_one()
    grant = (await db.execute(select(TokenGrant))).scalar_one()
    await db.refresh(ledger)
    await db.refresh(grant)
    assert ledger.consumed == 1_000_000 and ledger.reserved == 0
    assert grant.consumed == 900 and grant.reserved == 0
    assert (await client.get("/api/billing/packages")).json()["remaining"] == 1_000_000 - 900
    ws = (await client.get("/api/workspace")).json()
    assert ws["ai_available"] is True  # the allowance is used up, the package isn't


async def test_expired_and_refunded_packages_stop_counting(client, db):
    await signup(client, "exp@example.com")
    await subscribe(client)
    await buy(client)
    await buy(client, "tokens-5m")
    first, second = (await db.execute(select(TokenGrant).order_by(TokenGrant.tokens))).scalars().all()
    await db.execute(update(TokenGrant).where(TokenGrant.id == first.id).values(expires_at=utcnow() - timedelta(minutes=1)))
    await db.commit()
    assert (await client.get("/api/billing/packages")).json()["remaining"] == 5_000_000
    assert await quota.expire_grants(db) == 1
    await db.refresh(first)
    assert first.status == "expired"
    # A refund at the provider revokes the package's unspent tokens.
    pay = await db.get(Payment, second.payment_id)
    info = RefundInfo(ref="ch_1", invoice_ref=pay.provider_invoice_ref, payment_ref=None, amount_refunded_cents=5000, amount_cents=5000)
    async with SessionLocal() as s:
        assert await billing_events.handle(
            s, "mock", ProviderEvent(id="refund-1", type="charge.refunded", object_ref="ch_1", obj=info)
        ) == ("processed")
    await db.refresh(second)
    assert second.status == "revoked"
    assert (await client.get("/api/billing/packages")).json()["remaining"] == 0
    b = (await client.get("/api/billing")).json()
    assert next(p for p in b["payments"] if p["description"].endswith("5M tokens"))["status"] == "refunded"


async def test_partial_refund_keeps_a_share(client, db):
    await signup(client, "part@example.com")
    await subscribe(client)
    await buy(client)
    grant = (await db.execute(select(TokenGrant))).scalar_one()
    pay = await db.get(Payment, grant.payment_id)
    info = RefundInfo(ref="ch_2", invoice_ref=pay.provider_invoice_ref, payment_ref=None, amount_refunded_cents=600, amount_cents=1200)
    async with SessionLocal() as s:
        await billing_events.handle(s, "mock", ProviderEvent(id="refund-2", type="charge.refunded", object_ref="ch_2", obj=info))
    await db.refresh(grant)
    assert grant.status == "active" and grant.tokens == 500_000


async def test_parallel_reservations_never_overrun_either_pool(client, db):
    await signup(client, "race@example.com")
    await subscribe(client)
    await buy(client)
    ws_id = uuid.UUID(client.headers["X-Workspace-Id"])
    await db.execute(update(TokenLedger).values(allowance=10_000))
    await db.execute(update(TokenGrant).values(tokens=20_500))
    await db.commit()

    async def one():
        async with SessionLocal() as s:
            try:
                return await quota.reserve(s, ws_id, model="claude-haiku-5-5", operation="chat_response", est_input=1000, est_output=0)
            except quota.QuotaExhausted:
                return None

    held = [r for r in await asyncio.gather(*[one() for _ in range(50)]) if r is not None]
    assert len(held) == 30  # 10 from the allowance, 20 from the package
    ledger = (await db.execute(select(TokenLedger).where(TokenLedger.is_current))).scalar_one()
    grant = (await db.execute(select(TokenGrant))).scalar_one()
    await db.refresh(ledger)
    await db.refresh(grant)
    assert ledger.reserved == 10_000 and grant.reserved == 20_000
    async with SessionLocal() as s:
        for r in held[:10]:
            await quota.settle(s, r.id, input_tokens=500, output_tokens=0)
        for r in held[10:]:
            assert await quota.release(s, r.id) is True
    await db.refresh(ledger)
    await db.refresh(grant)
    assert ledger.reserved == 0 and grant.reserved == 0
    assert ledger.consumed + grant.consumed == 5000


async def test_without_tokens_manual_tickets_email_and_forms_keep_working(client, db):
    from tests.mail import connect, deliver, mail, tickets

    await signup(client, "dry@example.com")
    await subscribe(client)
    await buy(client)
    await db.execute(update(TokenLedger).values(consumed=1_000_000))
    await db.execute(update(TokenGrant).values(consumed=1_000_000))
    await db.commit()
    assert (await client.get("/api/workspace")).json()["ai_available"] is False
    r = await client.post("/api/dev/simulate-usage", json={"input_tokens": 10, "output_tokens": 0})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "quota_exhausted"
    assert (await client.post("/api/tickets", json={"subject": "Manual still works"})).status_code == 201
    a = await connect(client)
    await deliver(client, a["id"], mail("Order missing", "Where is my order?")[0])
    assert len(await tickets(client)) == 1
    forms = (await client.get("/api/forms")).json()
    assert forms  # the built-in contact form is still there to submit


async def test_admins_define_packages(client, admin, db):
    await signup(client, "user@example.com")
    assert (await client.get("/api/admin/token-packages")).status_code == 403
    r = await admin.post(
        "/api/admin/token-packages", json={"key": "tokens-20m", "name": "20M tokens", "tokens": 20_000_000, "price_cents": 18000}
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert (
        await admin.post("/api/admin/token-packages", json={"key": "tokens-20m", "name": "x", "tokens": 1000, "price_cents": 100})
    ).status_code == 409
    await db.execute(update(TokenPackage).where(TokenPackage.id == uuid.UUID(pid)).values(provider_price_ref="price_old"))
    await db.commit()
    r = await admin.patch(f"/api/admin/token-packages/{pid}", json={"price_cents": 17000, "is_public": False})
    assert r.status_code == 200 and r.json()["price_cents"] == 17000
    pkg = await db.get(TokenPackage, uuid.UUID(pid))
    await db.refresh(pkg)
    assert pkg.provider_price_ref is None  # a new provider price is created at the next sale
    assert "tokens-20m" not in [p["key"] for p in (await client.get("/api/billing/packages")).json()["packages"]]
    actions = (await db.execute(select(AuditLog.action).where(AuditLog.action.like("admin.token_package%")))).scalars().all()
    assert sorted(actions) == ["admin.token_package_created", "admin.token_package_updated"]


async def test_packages_follow_the_workspace_currency(client, db):
    await signup(client, "cur@example.com")
    await subscribe(client)
    db.add(TokenPackage(key="tokens-eur", name="1M EUR", tokens=1_000_000, price_cents=1100, currency="EUR"))
    await db.commit()
    pkg = next(p for p in (await client.get("/api/billing/packages")).json()["packages"] if p["key"] == "tokens-eur")
    r = await client.post(f"/api/billing/packages/{pkg['id']}/checkout")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "currency_mismatch"
