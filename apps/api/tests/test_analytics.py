"""Phase 5B analytics: ranges and buckets in the viewer's time zone, every workspace metric, SLA targets,
platform reports per currency, every role and tenant isolation."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from app.core.db import SessionLocal
from app.core.errors import ApiError
from app.models import ApiToolCall, AuditLog, Chatbot, Conversation, Message, Payment, Plan, Ticket, TicketEvent, Workspace
from app.models.base import utcnow
from app.services import analytics as an
from tests.conftest import login, new_client, signup, subscribe
from tests.mail import connect, deliver, mail
from tests.test_phase3_access import team

SECTIONS = ("tickets", "sla", "agents", "chatbot", "tools", "email", "ai")


# ---- ranges -------------------------------------------------------------------------------------------------


def test_presets_are_local_days_including_today():
    now = datetime(2026, 3, 29, 10, 0, tzinfo=UTC)  # the day Berlin moves to summer time
    r = an.parse_range("7d", "Europe/Berlin", now=now)
    assert (r.first, r.last, r.days, r.bucket) == (date(2026, 3, 23), date(2026, 3, 29), 7, "day")
    assert r.start == datetime(2026, 3, 22, 23, 0, tzinfo=UTC)  # midnight CET
    assert r.end == datetime(2026, 3, 29, 22, 0, tzinfo=UTC)  # midnight CEST
    assert len(r.labels()) == 7
    prev = r.previous()
    assert (prev.first, prev.last) == (date(2026, 3, 16), date(2026, 3, 22))
    # Late evening in Los Angeles is already tomorrow in UTC: today is the local day.
    r = an.parse_range("30d", "America/Los_Angeles", now=datetime(2026, 7, 2, 3, 0, tzinfo=UTC))
    assert r.last == date(2026, 7, 1) and r.days == 30
    assert an.parse_range("90d", "UTC", now=now).bucket == "day"


def test_custom_ranges_and_weekly_buckets():
    now = datetime(2026, 11, 27, 12, tzinfo=UTC)
    r = an.parse_range("custom", "UTC", "2026-06-01", "2026-09-30", now=now)
    assert r.days == 122 and r.bucket == "week"
    labels = r.labels()
    assert labels[0] == date(2026, 6, 1) and all(d.weekday() == 0 for d in labels) and len(labels) == 18
    assert an.parse_range("custom", "UTC", "2026-01-01", "2026-03-31", now=now).bucket == "day"  # exactly 90 days
    assert an.parse_range("custom", "UTC", "2025-11-27", "2026-11-27", now=now).days == 366
    for args, field in (
        (("2025-11-26", "2026-11-27"), "from"),  # over a year
        (("2026-11-20", "2026-11-28"), "to"),  # ends in the future
        (("2026-11-20", "2026-11-10"), "from"),  # reversed
        (("yesterday", "2026-11-10"), "from"),
    ):
        with pytest.raises(ApiError) as e:
            an.parse_range("custom", "UTC", *args, now=now)
        assert e.value.detail["code"] == "invalid_range" and e.value.detail["field"] == field
    assert an.parse_range("7d", "Not/AZone", now=now).tz == "UTC"


# ---- workspace metrics --------------------------------------------------------------------------------------


async def owner(client, email="an@example.com", tz="UTC"):
    await signup(client, email)
    await subscribe(client, "growth")
    assert (await client.patch("/api/me", json={"timezone": tz})).status_code == 200


async def ticket(client, **body) -> dict:
    r = await client.post("/api/tickets", json={"subject": "Help", **body})
    assert r.status_code == 201, r.text
    return r.json()


async def at(ticket_id, **values):
    async with SessionLocal() as db:
        await db.execute(update(Ticket).where(Ticket.id == uuid.UUID(ticket_id)).values(**values))
        await db.commit()


async def get(client, section, **params):
    r = await client.get(f"/api/analytics/{section}", params={"range": "7d", **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_ticket_volume_times_and_reopens(client):
    await owner(client)
    cat = (await client.post("/api/ticket-categories", json={"name": "Billing"})).json()
    a = await ticket(client, category_id=cat["id"])
    b = await ticket(client)
    c = await ticket(client, priority="urgent")
    now = utcnow()
    await at(a["id"], created_at=now - timedelta(hours=5), first_response_at=now - timedelta(hours=4))
    await at(b["id"], created_at=now - timedelta(hours=3), first_response_at=now - timedelta(hours=1))
    await at(c["id"], created_at=now - timedelta(minutes=30))
    # Resolve a, reopen it, resolve it again.
    for status in ("resolved", "open", "resolved"):
        assert (await client.patch(f"/api/tickets/{a['id']}", json={"status": status})).status_code == 200
    data = await get(client, "tickets")
    t = data["totals"]
    assert t["created"] == 3 and t["responded"] == 2
    assert t["first_response_median_minutes"] == 90.0  # 60 and 120 minutes
    assert t["resolved"] == 1 and t["reopened"] == 1 and t["resolutions"] == 2 and t["reopen_rate"] == 50.0
    assert 299 <= t["resolution_median_minutes"] <= 301
    assert data["source_totals"]["manual"] == 3
    assert {"name": "Billing", "count": 1} in data["category_totals"]
    assert {"name": "No category", "count": 2} in data["category_totals"]
    assert len(data["by_source"]) == 7 and sum(p["manual"] for p in data["by_source"]) == 3
    assert data["previous"]["created"] == 0
    assert sum(p["reopened"] for p in data["reopens"]) == 1


async def test_buckets_follow_the_viewers_time_zone(client):
    await owner(client, tz="Asia/Tokyo")
    t = await ticket(client)
    today_tokyo = utcnow().astimezone(__import__("zoneinfo").ZoneInfo("Asia/Tokyo")).date()
    # 15:30 UTC yesterday is 00:30 today in Tokyo (UTC+9).
    created = datetime.combine(today_tokyo - timedelta(days=1), datetime.min.time(), tzinfo=UTC) + timedelta(hours=15, minutes=30)
    await at(t["id"], created_at=created)
    data = await get(client, "tickets")
    assert data["range"]["tz"] == "Asia/Tokyo" and data["range"]["to"] == today_tokyo.isoformat()
    last = data["by_source"][-1]
    assert last["date"] == today_tokyo.isoformat() and last["manual"] == 1


async def test_sla_breaches_follow_the_targets(client):
    await owner(client)
    now = utcnow()
    late = await ticket(client, priority="urgent")
    fine = await ticket(client, priority="normal")
    await at(late["id"], created_at=now - timedelta(hours=2))  # urgent: 1 h to first response
    await at(fine["id"], created_at=now - timedelta(hours=2), first_response_at=now - timedelta(hours=1))
    data = await get(client, "sla")
    urgent = next(p for p in data["by_priority"] if p["priority"] == "urgent")
    normal = next(p for p in data["by_priority"] if p["priority"] == "normal")
    assert urgent["response_breached"] == 1 and urgent["response_met_rate"] == 0.0
    assert normal["response_met"] == 1 and normal["resolution_pending"] == 1
    [now_late] = data["breaching_now"]
    assert now_late["number"] == late["number"] and now_late["target"] == "first_response" and 55 <= now_late["overdue_minutes"] <= 65
    # A longer target recomputes the report.
    targets = (await client.get("/api/sla-targets")).json()["targets"]
    assert targets["urgent"] == {"first_response_minutes": 60, "resolution_minutes": 480}
    targets["urgent"]["first_response_minutes"] = 180
    r = await client.put("/api/sla-targets", json={"targets": targets})
    assert r.status_code == 200, r.text
    data = await get(client, "sla")
    assert next(p for p in data["by_priority"] if p["priority"] == "urgent")["response_breached"] == 0
    assert data["breaching_now"] == []
    targets["low"]["resolution_minutes"] = 10  # shorter than its first response
    r = await client.put("/api/sla-targets", json={"targets": targets})
    assert r.status_code == 422 and r.json()["detail"]["field"] == "low.resolution_minutes"
    async with SessionLocal() as db:
        assert (await db.execute(select(AuditLog).where(AuditLog.action == "tickets.sla_updated"))).scalar_one()


async def test_agent_workload(client):
    await owner(client)
    clients = await team(client)
    agent_id = (await clients["agent"].get("/api/me")).json()["user"]["id"]
    t1 = await ticket(client)
    await ticket(client, assignee_id=agent_id)
    assert (await client.patch(f"/api/tickets/{t1['id']}", json={"assignee_id": agent_id})).status_code == 200
    assert (await clients["agent"].patch(f"/api/tickets/{t1['id']}", json={"status": "resolved"})).status_code == 200
    data = await get(client, "agents")
    agent = next(m for m in data["members"] if m["id"] == agent_id)
    assert agent["assigned"] == 2 and agent["open_now"] == 1 and agent["resolved"] == 1
    assert agent["availability"] == "available"
    names = {m["role"] for m in data["members"]}
    assert "Viewer" not in names  # only people who can work tickets
    for c in clients.values():
        if c is not client:
            await c.aclose()


async def test_chatbot_deflection_and_handoff(client):
    await owner(client)
    ws_id = uuid.UUID(client.headers["X-Workspace-Id"])
    now = utcnow()
    async with SessionLocal() as db:
        bot = Chatbot(workspace_id=ws_id, name="Help bot", public_key=uuid.uuid4().hex[:20])
        db.add(bot)
        await db.flush()

        def conv(**kw):
            c = Conversation(id=uuid.uuid4(), workspace_id=ws_id, chatbot_id=bot.id, **{"channel": "widget", **kw})
            db.add(c)
            return c

        def msg(c, role, **data):
            db.add(Message(workspace_id=ws_id, conversation_id=c.id, role=role, content="x", data=data, created_at=now))

        deflected = conv()
        msg(deflected, "visitor")
        msg(deflected, "assistant", outcome="answered")
        handed = conv(escalated_at=now, status="escalated")
        msg(handed, "visitor")
        msg(handed, "assistant", outcome="no_answer")
        unanswered = conv()
        msg(unanswered, "visitor")
        msg(unanswered, "assistant", outcome="no_answer")
        empty = conv()  # opened, never written in: not counted
        msg(empty, "assistant", outcome="answered")
        dash = conv(channel="dashboard")  # the Ask screen: not a customer conversation
        msg(dash, "visitor")
        await db.commit()
    data = await get(client, "chatbot")
    assert data["totals"] == {
        "conversations": 3,
        "deflected": 1,
        "handed_off": 1,
        "unanswered": 1,
        "deflection_rate": 33.3,
        "handoff_rate": 33.3,
    }
    assert data["chatbots"][0]["name"] == "Help bot" and data["chatbots"][0]["conversations"] == 3
    assert sum(p["deflected"] for p in data["series"]) == 1


async def test_tool_calls(client):
    await owner(client)
    ws_id = uuid.UUID(client.headers["X-Workspace-Id"])
    now = utcnow()
    async with SessionLocal() as db:
        for status, latency, origin in (("ok", 100, "chat"), ("ok", 300, "chat"), ("timeout", 0, "chat"), ("ok", 200, "test")):
            db.add(
                ApiToolCall(workspace_id=ws_id, tool_name="order_status", origin=origin, status=status, latency_ms=latency, created_at=now)
            )
        await db.commit()
    data = await get(client, "tools")
    assert data["totals"]["calls"] == 4 and data["totals"]["ok"] == 3 and data["totals"]["success_rate"] == 75.0
    assert data["totals"]["tests"] == 1 and data["totals"]["latency_p50_ms"] == 200
    assert data["tools"][0]["name"] == "order_status"
    assert {"status": "timeout", "count": 1} in data["by_status"]


async def test_spam_volume(client):
    await owner(client)
    a = await connect(client)
    await deliver(client, a["id"], mail("Order missing", "Where is it?")[0])
    await deliver(client, a["id"], mail("Win", "Prize", sender="spam@example.net", headers={"X-Spam-Flag": "YES"})[0])
    await deliver(
        client, a["id"], mail("Sale", "50% off", sender="news@example.net", headers={"List-Unsubscribe": "<mailto:x@example.net>"})[0]
    )
    data = await get(client, "email")
    t = data["totals"]
    assert t["received"] == 3 and t["legitimate"] == 1 and t["spam"] == 1 and t["promotion"] == 1
    assert t["spam_rate"] == 66.7


async def test_ai_usage_by_feature_and_model(client):
    await owner(client)
    await client.post(
        "/api/dev/simulate-usage", json={"operation": "chat_response", "input_tokens": 1000, "output_tokens": 100, "count": 2}
    )
    await client.post(
        "/api/dev/simulate-usage",
        json={"operation": "classification", "model": "claude-sonnet-5-5", "input_tokens": 100, "output_tokens": 0},
    )
    data = await get(client, "ai")
    assert data["totals"]["calls"] == 3
    features = {r["name"]: r for r in data["by_feature"]}
    assert features["chat_response"]["weighted_tokens"] == 2 * (1000 + 500) and features["classification"]["weighted_tokens"] == 300
    assert {r["name"] for r in data["by_model"]} == {"claude-haiku-5-5", "claude-sonnet-5-5"}
    assert sum(p["chat_response"] for p in data["series"]) == 3000
    assert "cost" not in str(data)  # provider costs are for system administrators only


async def test_custom_ranges_through_the_api(client):
    await owner(client)
    today = utcnow().date()
    r = await client.get(
        "/api/analytics/tickets", params={"range": "custom", "from": (today - timedelta(days=119)).isoformat(), "to": today.isoformat()}
    )
    assert r.status_code == 200 and r.json()["range"]["bucket"] == "week"
    r = await client.get(
        "/api/analytics/tickets", params={"range": "custom", "from": (today - timedelta(days=400)).isoformat(), "to": today.isoformat()}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_range"
    assert (await client.get("/api/analytics/tickets", params={"range": "1y"})).status_code == 422


# ---- access -------------------------------------------------------------------------------------------------


async def test_owners_and_admins_see_analytics_others_dont(client):
    await owner(client)
    clients = await team(client)
    for role, c in clients.items():
        for section in SECTIONS:
            r = await c.get(f"/api/analytics/{section}")
            assert r.status_code == (200 if role in ("owner", "admin") else 403), (role, section, r.text)
    # SLA targets: everyone who sees tickets reads them; only tickets.configure changes them.
    targets = (await clients["agent"].get("/api/sla-targets")).json()
    assert targets["can_edit"] is False
    assert (await clients["agent"].put("/api/sla-targets", json={"targets": targets["targets"]})).status_code == 403
    assert (await clients["admin"].put("/api/sla-targets", json={"targets": targets["targets"]})).status_code == 200
    # A custom role can be given analytics.read.
    r = await client.post("/api/workspace/roles", json={"name": "Analyst", "permissions": ["workspace.read", "analytics.read"]})
    assert r.status_code == 201, r.text
    members = (await client.get("/api/workspace/members")).json()["members"]
    viewer = next(m for m in members if m["email"] == "viewer@example.com")
    assert (await client.patch(f"/api/workspace/members/{viewer['id']}", json={"role_id": r.json()["id"]})).status_code == 200
    assert (await clients["viewer"].get("/api/analytics/tickets")).status_code == 200
    for c in clients.values():
        if c is not client:
            await c.aclose()


async def test_workspaces_only_see_their_own_numbers(client):
    await owner(client, "one@example.com")
    await ticket(client)
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 10, "output_tokens": 0})
    async with new_client() as other:
        await owner(other, "two@example.com")
        assert (await get(other, "tickets"))["totals"]["created"] == 0
        assert (await get(other, "ai"))["totals"]["calls"] == 0
        assert all(m["email"] == "two@example.com" for m in (await get(other, "agents"))["members"])
        assert (await other.get("/api/admin/analytics/revenue")).status_code == 403
    assert (await get(client, "tickets"))["totals"]["created"] == 1


# ---- platform reports ---------------------------------------------------------------------------------------


@pytest.fixture
async def admin():
    async with new_client() as c:
        assert (await login(c, "root@example.org", "Admin-pass-123")).status_code == 200
        yield c


async def test_platform_reports_keep_currencies_apart(client, admin, db):
    db.add(
        Plan(key="pro-eur", name="Pro EUR", price_cents=4900, currency="EUR", billing_cycle="month", token_allowance=1_000_000, limits={})
    )
    await db.commit()
    await owner(client, "usd@example.com")
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 1_000_000, "output_tokens": 0})
    async with new_client() as eur:
        await signup(eur, "eur@example.com")
        await subscribe(eur, "pro-eur")
        await eur.put("/api/billing/test-card", json={"card": "declines"})
        await eur.post("/api/dev/billing/renew")  # fails: past due
    # The first EUR invoice paid for the period that just ended.
    async with SessionLocal() as s:
        await s.execute(
            update(Payment)
            .where(Payment.currency == "EUR", Payment.status == "succeeded")
            .values(period_end=utcnow() - timedelta(seconds=1))
        )
        await s.commit()
    rev = (await admin.get("/api/admin/analytics/revenue", params={"range": "7d"})).json()
    mrr = {m["currency"]: m["mrr_cents"] for m in rev["mrr_now"]}
    assert mrr == {"USD": 9900, "EUR": 4900}
    assert rev["revenue"] == {"USD": 9900, "EUR": 4900}
    assert rev["mrr_series"][-1] == {"date": rev["mrr_series"][-1]["date"], "EUR": 0, "USD": 9900}  # EUR's renewal wasn't paid
    subs = (await admin.get("/api/admin/analytics/subscriptions")).json()
    assert subs["now"]["active"] == 1 and subs["now"]["past_due"] == 1 and subs["totals"]["new"] == 2
    failed = (await admin.get("/api/admin/analytics/failed-payments")).json()
    assert failed["totals"]["failed"] == 1 and failed["past_due"][0]["workspace"]
    assert failed["at_risk"] == [{"currency": "EUR", "amount_cents": 4900, "invoices": 1}]
    costs = (await admin.get("/api/admin/analytics/costs")).json()
    assert costs["total_usd"] == 1.0 and costs["by_model"][0]["name"] == "claude-haiku-5-5"
    assert costs["revenue"]["USD"] == 9900 and costs["usd_margin_rate"] == round((99 - 1) * 100 / 99, 1)
    usage = (await admin.get("/api/admin/analytics/usage")).json()
    assert usage["active_workspaces"] == 1 and usage["workspaces"][0]["weighted_tokens"] == 1_000_000
    async with SessionLocal() as s:  # plans outlive each test: remove this one and what uses it
        pid = (await s.execute(select(Plan.id).where(Plan.key == "pro-eur"))).scalar_one()
        await s.execute(text("DELETE FROM payments WHERE plan_id = :p"), {"p": pid})
        await s.execute(text("DELETE FROM checkouts WHERE plan_id = :p"), {"p": pid})
        await s.execute(text("DELETE FROM subscriptions WHERE plan_id = :p OR pending_plan_id = :p"), {"p": pid})
        await s.execute(Plan.__table__.delete().where(Plan.id == pid))
        await s.commit()
    assert (await client.get("/api/admin/analytics/usage")).status_code == 403


async def test_reports_count_only_the_range(client, db):
    await owner(client, "old@example.com")
    t = await ticket(client)
    await at(t["id"], created_at=utcnow() - timedelta(days=20))
    assert (await get(client, "tickets"))["totals"]["created"] == 0
    assert (await get(client, "tickets", range="30d"))["totals"]["created"] == 1
    ws = (await db.execute(select(Workspace))).scalars().first()
    assert ws is not None
    _ = TicketEvent  # timeline rows are counted through the API above
