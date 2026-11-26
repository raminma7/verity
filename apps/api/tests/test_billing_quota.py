import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import Plan, Subscription, TokenLedger, UsageEvent
from app.models.base import utcnow
from app.services import email as mail
from app.services import quota
from app.services.subscriptions import renew_due
from tests.conftest import signup, subscribe


async def test_no_subscription_state(client):
    await signup(client, "o@example.com")
    ws = (await client.get("/api/workspace")).json()
    assert ws["subscription"] is None
    assert ws["ai_available"] is False
    assert ws["limits"]["documents"] == 0
    # Settings, plans and billing stay reachable.
    assert (await client.get("/api/plans")).status_code == 200
    assert (await client.get("/api/billing")).status_code == 200
    assert (await client.get("/api/usage/summary")).json() == {"active": False}


async def test_purchase_activates_plan_and_allowance(client):
    await signup(client, "buyer@example.com")
    plan = await subscribe(client, "starter")
    ws = (await client.get("/api/workspace")).json()
    assert ws["subscription"]["active"] is True
    assert ws["subscription"]["plan_name"] == "Starter"
    assert ws["limits"]["documents"] == 100
    assert ws["ai_available"] is True
    usage = (await client.get("/api/usage/summary")).json()
    assert usage["total_allowance"] == plan["token_allowance"]
    assert usage["remaining"] == plan["token_allowance"]
    billing = (await client.get("/api/billing")).json()
    assert len(billing["payments"]) == 1
    assert billing["payments"][0]["amount_cents"] == 2900
    assert any("Receipt" in m.subject for m in mail.OUTBOX)


async def test_confirm_is_idempotent(client):
    await signup(client, "twice@example.com")
    plans = (await client.get("/api/plans")).json()
    r = await client.post("/api/billing/checkout", json={"plan_id": plans[0]["id"]})
    cid = r.json()["checkout_id"]
    assert (await client.post(f"/api/billing/checkout/{cid}/confirm")).status_code == 200
    assert (await client.post(f"/api/billing/checkout/{cid}/confirm")).status_code == 200
    assert len((await client.get("/api/billing")).json()["payments"]) == 1


async def test_downgrade_scheduled_for_renewal(client, db):
    await signup(client, "dg@example.com")
    await subscribe(client, "growth")
    r = await subscribe(client, "starter")
    assert r["scheduled"] is True
    billing = (await client.get("/api/billing")).json()
    assert billing["subscription"]["terms"]["plan_key"] == "growth"
    assert billing["subscription"]["pending_plan"]["name"] == "Starter"

    await db.execute(update(Subscription).values(current_period_end=utcnow() - timedelta(minutes=1)))
    await db.commit()
    assert (await renew_due(db))["renewed"] == 1
    billing = (await client.get("/api/billing")).json()
    assert billing["subscription"]["terms"]["plan_key"] == "starter"
    assert billing["subscription"]["pending_plan"] is None


async def test_plan_edits_apply_at_renewal(client, db):
    await signup(client, "pe@example.com")
    await subscribe(client, "starter")
    await db.execute(update(Plan).where(Plan.key == "starter").values(price_cents=3900, token_allowance=2_000_000))
    await db.commit()
    terms = (await client.get("/api/billing")).json()["subscription"]["terms"]
    assert terms["price_cents"] == 2900  # unchanged mid-period
    await db.execute(update(Subscription).values(current_period_end=utcnow() - timedelta(minutes=1)))
    await db.commit()
    await renew_due(db)
    terms = (await client.get("/api/billing")).json()["subscription"]["terms"]
    assert terms["price_cents"] == 3900
    assert (await client.get("/api/usage/summary")).json()["total_allowance"] == 2_000_000
    await db.execute(update(Plan).where(Plan.key == "starter").values(price_cents=2900, token_allowance=1_000_000))
    await db.commit()


async def test_renewal_resets_tokens_without_rollover(client, db):
    await signup(client, "ro@example.com")
    await subscribe(client)
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 1000, "output_tokens": 0, "count": 3})
    assert (await client.get("/api/usage/summary")).json()["consumed_weighted"] == 3000
    await db.execute(update(Subscription).values(current_period_end=utcnow() - timedelta(minutes=1)))
    await db.commit()
    await renew_due(db)
    usage = (await client.get("/api/usage/summary")).json()
    assert usage["consumed_weighted"] == 0
    assert usage["remaining"] == usage["allowance"] == 1_000_000


async def test_cancel_at_period_end(client, db):
    await signup(client, "cx@example.com")
    await subscribe(client)
    assert (await client.post("/api/billing/cancel")).status_code == 200
    await db.execute(update(Subscription).values(current_period_end=utcnow() - timedelta(minutes=1)))
    await db.commit()
    assert (await renew_due(db))["canceled"] == 1
    ws = (await client.get("/api/workspace")).json()
    assert ws["subscription"]["active"] is False
    assert ws["ai_available"] is False


async def test_weighted_usage_and_breakdown(client):
    await signup(client, "w@example.com")
    await subscribe(client)
    # haiku: input ×1, output ×5
    await client.post("/api/dev/simulate-usage", json={"model": "claude-haiku-5-5", "input_tokens": 1000, "output_tokens": 200})
    await client.post(
        "/api/dev/simulate-usage", json={"model": "text-embedding", "operation": "embedding", "input_tokens": 10000, "output_tokens": 0}
    )
    s = (await client.get("/api/usage/summary")).json()
    assert s["actual_input_tokens"] == 11000
    assert s["actual_output_tokens"] == 200
    assert s["consumed_weighted"] == 1000 + 1000 + 1000  # 1000 + 200*5, then 10000*0.1
    assert s["embedding_weighted"] == 1000
    b = (await client.get("/api/usage/breakdown")).json()
    assert {x["key"] for x in b["by_model"]} == {"claude-haiku-5-5", "text-embedding"}
    daily = (await client.get("/api/usage/daily")).json()
    assert daily[-1]["weighted"] == 3000


async def test_threshold_alerts_once(client, db):
    await signup(client, "t@example.com")
    await subscribe(client)
    mail.OUTBOX.clear()
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 810_000, "output_tokens": 0})
    assert [m.subject for m in mail.OUTBOX if "80%" in m.subject]
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 1000, "output_tokens": 0})
    assert len([m for m in mail.OUTBOX if "%" in m.subject]) == 1
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 90_000, "output_tokens": 0})
    assert [m.subject for m in mail.OUTBOX if "90%" in m.subject]
    ledger = (await db.execute(select(TokenLedger))).scalar_one()
    assert sorted(ledger.notified) == [80, 90]


async def test_exhausted_allowance_blocks_ai(client):
    await signup(client, "ex@example.com")
    await subscribe(client)
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 1_000_000, "output_tokens": 0})
    r = await client.post("/api/dev/simulate-usage", json={"input_tokens": 1, "output_tokens": 0})
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "quota_exhausted"
    assert (await client.get("/api/workspace")).json()["ai_available"] is False
    # Non-AI parts keep working.
    assert (await client.get("/api/workspace/members")).status_code == 200


async def test_concurrent_reservations_never_exceed_allowance(client, db):
    await signup(client, "cc@example.com")
    await subscribe(client)
    ws_id = uuid.UUID(client.headers["X-Workspace-Id"])
    await db.execute(update(TokenLedger).values(allowance=10_000))
    await db.commit()

    async def one():
        async with SessionLocal() as s:
            try:
                return await quota.reserve(s, ws_id, model="claude-haiku-5-5", operation="chat_response", est_input=1000, est_output=0)
            except quota.QuotaExhausted:
                return None

    results = await asyncio.gather(*[one() for _ in range(50)])
    held = [r for r in results if r is not None]
    assert len(held) == 10
    ledger = (await db.execute(select(TokenLedger))).scalar_one()
    await db.refresh(ledger)
    assert ledger.reserved == 10_000

    # Settle half below estimate, release the rest; settle twice is a no-op.
    async with SessionLocal() as s:
        for r in held[:5]:
            await quota.settle(s, r.id, input_tokens=800, output_tokens=0)
            assert await quota.settle(s, r.id, input_tokens=800, output_tokens=0) is None
        for r in held[5:]:
            assert await quota.release(s, r.id) is True
    await db.refresh(ledger)
    assert ledger.reserved == 0
    assert ledger.consumed == 4000
    assert len((await db.execute(select(UsageEvent))).scalars().all()) == 5


async def test_suspended_workspace_is_closed(client, db):
    from app.models import Workspace

    await signup(client, "sw@example.com")
    await subscribe(client)
    await db.execute(update(Workspace).values(status="suspended"))
    await db.commit()
    r = await client.get("/api/workspace")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "workspace_suspended"
