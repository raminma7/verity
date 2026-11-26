import pytest
from sqlalchemy import select

from app.models import AuditLog
from tests.conftest import login, new_client, signup, subscribe


@pytest.fixture
async def admin():
    async with new_client() as c:
        r = await login(c, "root@example.org", "Admin-pass-123")
        assert r.status_code == 200, r.text
        yield c


async def test_non_admin_refused(client):
    await signup(client, "u@example.com")
    assert (await client.get("/api/admin/overview")).status_code == 403


async def test_suspend_and_reactivate_user(client, db, admin):
    await signup(client, "target@example.com")
    users = (await admin.get("/api/admin/users", params={"q": "target"})).json()
    uid = users[0]["id"]
    r = await admin.patch(f"/api/admin/users/{uid}/status", json={"status": "suspended", "reason": "abuse report"})
    assert r.status_code == 200
    assert (await client.get("/api/me")).status_code == 401  # sessions revoked
    assert (await login(client, "target@example.com")).status_code == 403
    await admin.patch(f"/api/admin/users/{uid}/status", json={"status": "active"})
    assert (await login(client, "target@example.com")).status_code == 200
    actions = (await db.execute(select(AuditLog.action))).scalars().all()
    assert "admin.user_suspended" in actions


async def test_adjust_tokens_with_audit(client, db, admin):
    await signup(client, "tok@example.com")
    await subscribe(client)
    ws_id = client.headers["X-Workspace-Id"]
    r = await admin.post(f"/api/admin/workspaces/{ws_id}/tokens", json={"delta": 50_000, "reason": "goodwill credit"})
    assert r.status_code == 200
    assert r.json()["bonus"] == 50_000
    r = await admin.post(f"/api/admin/workspaces/{ws_id}/tokens", json={"delta": -10_000_000, "reason": "too much"})
    assert r.status_code == 422
    assert (await client.get("/api/usage/summary")).json()["total_allowance"] == 1_050_000
    log = (await db.execute(select(AuditLog).where(AuditLog.action == "admin.tokens_adjusted"))).scalar_one()
    assert log.data["delta"] == 50_000
    assert log.data["reason"] == "goodwill credit"


async def test_plan_crud_and_limits_validation(admin):
    body = {
        "key": "enterprise",
        "name": "Enterprise",
        "price_cents": 99900,
        "token_allowance": 100_000_000,
        "limits": {"members": -1, "documents": 10000},
    }
    r = await admin.post("/api/admin/plans", json=body)
    assert r.status_code == 201
    assert r.json()["limits"]["members"] == -1
    assert r.json()["limits"]["domains"] == 0
    pid = r.json()["id"]
    r = await admin.patch(f"/api/admin/plans/{pid}", json={"limits": {"bogus": 1}})
    assert r.status_code == 422
    r = await admin.patch(f"/api/admin/plans/{pid}", json={"is_public": False})
    assert r.json()["is_public"] is False
    async with new_client() as anon:
        keys = [p["key"] for p in (await anon.get("/api/plans")).json()]
        assert "enterprise" not in keys


async def test_multipliers_and_default_limits(client, admin):
    r = await admin.put(
        "/api/admin/multipliers",
        json={"model": "claude-haiku-5-5", "operation": "classification", "input_multiplier": 0.5, "output_multiplier": 1},
    )
    assert r.status_code == 200
    r = await admin.put("/api/admin/settings", json={"no_plan_limits": {"members": 2}})
    assert r.json()["no_plan_limits"]["members"] == 2
    await signup(client, "lim@example.com")
    assert (await client.get("/api/workspace")).json()["limits"]["members"] == 2
    await subscribe(client)
    await client.post("/api/dev/simulate-usage", json={"operation": "classification", "input_tokens": 1000, "output_tokens": 100})
    # exact operation (0.5/1) beats the model wildcard (1/5)
    assert (await client.get("/api/usage/summary")).json()["consumed_weighted"] == 600
    mult = (await client.get("/api/usage/multipliers")).json()
    assert any(m["operation"] == "classification" for m in mult)


async def test_overview_and_reports(client, admin):
    await signup(client, "rep@example.com")
    await subscribe(client, "growth")
    await client.post("/api/dev/simulate-usage", json={"input_tokens": 1_000_000, "output_tokens": 0})
    o = (await admin.get("/api/admin/overview")).json()
    assert o["active_subscriptions"] == 1
    assert o["mrr_cents"] == 9900
    assert o["provider_cost_this_month_usd"] == 1.0
    rep = (await admin.get("/api/admin/reports/usage")).json()
    assert rep[0]["weighted"] == 1_000_000
    ws = (await admin.get("/api/admin/workspaces", params={"q": "rep@"})).json()
    assert ws[0]["plan"] == "Growth"
    logs = (await admin.get("/api/admin/audit-logs", params={"action": "subscription"})).json()
    assert logs[0]["action"] == "subscription.activated"
