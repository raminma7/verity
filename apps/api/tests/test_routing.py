"""Ticket routing: rules, teams, least busy, availability, caps and skills, every source, roles and isolation."""

import asyncio

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import Conversation, Ticket, TicketEvent
from app.services import conversations as convs
from app.services import tickets as tk
from app.services.quota import current_ledger
from tests.conftest import new_client, signup, subscribe
from tests.mail import connect, deliver, mail
from tests.phase3 import category, ticket
from tests.test_phase3_access import team as role_team
from tests.test_workspaces import add_member


@pytest.fixture
async def ws(client):
    """Owner plus two agents (Ana, Ben) and a viewer; categories Billing and Shipping; routing on."""
    await signup(client, "owner@example.com", "Olga Owner")
    await subscribe(client, "growth")
    people = {}
    for name in ("ana", "ben", "vic"):
        c = new_client()
        await add_member(client, c, f"{name}@example.com", "viewer" if name == "vic" else "agent")
        people[name] = c
    members = (await client.get("/api/routing/members")).json()["items"]
    ids = {m["email"].split("@")[0]: m["user_id"] for m in members}
    billing = await category(client, "Billing")
    shipping = await category(client, "Shipping")
    r = await client.put("/api/routing/settings", json={"enabled": True})
    assert r.status_code == 200, r.text
    yield {"c": client, "people": people, "ids": ids, "billing": billing, "shipping": shipping}
    for c in people.values():
        await c.aclose()


async def make_team(c, name, members, categories=()):
    r = await c.post("/api/teams", json={"name": name, "member_ids": members, "category_ids": list(categories)})
    assert r.status_code == 201, r.text
    return r.json()


async def events_of(t_id) -> list[TicketEvent]:
    async with SessionLocal() as db:
        return list((await db.execute(select(TicketEvent).where(TicketEvent.ticket_id == t_id).order_by(TicketEvent.created_at))).scalars())


async def test_viewers_are_not_routable_and_cant_join_teams(ws):
    ids = ws["ids"]
    assert "vic" not in ids  # a viewer can't work tickets, so isn't in the routing list
    me = (await ws["people"]["vic"].get("/api/me")).json()["user"]
    r = await ws["c"].post("/api/teams", json={"name": "X", "member_ids": [me["id"]]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "member_cannot_work_tickets"


async def test_category_team_and_least_busy(ws):
    c, ids = ws["c"], ws["ids"]
    await make_team(c, "Billing team", [ids["ana"], ids["ben"]], [ws["billing"]["id"]])
    t1 = await ticket(c, subject="Charged twice", category_id=ws["billing"]["id"])
    t2 = await ticket(c, subject="Invoice wrong", category_id=ws["billing"]["id"])
    t3 = await ticket(c, subject="Refund", category_id=ws["billing"]["id"])
    who = [t["assignee_id"] for t in (t1, t2, t3)]
    # Fewest open tickets wins; a tie goes to whoever was assigned longest ago.
    assert set(who[:2]) == {ids["ana"], ids["ben"]} and who[2] == who[0]
    assert t1["status"] == "new" and t1["team"] == "Billing team" and t1["assigned_at"]
    ev = [e for e in await events_of(t1["id"]) if e.kind == "routed"][0]
    assert "least busy in Billing team" in ev.data["reason"] and ev.data["team"] == "Billing team"
    # Resolving Ana's tickets makes her the least busy again.
    ana_tickets = [t for t in (t1, t2, t3) if t["assignee_id"] == ids["ana"]]
    for t in ana_tickets:
        await c.patch(f"/api/tickets/{t['id']}", json={"status": "resolved"})
    t4 = await ticket(c, subject="Another", category_id=ws["billing"]["id"])
    assert t4["assignee_id"] == ids["ana"]


async def test_rules_come_first_in_order(ws):
    c, ids = ws["c"], ws["ids"]
    vip = await make_team(c, "VIP", [ids["ben"]])
    general = await make_team(c, "General", [ids["ana"]], [ws["billing"]["id"]])
    r1 = await c.post(
        "/api/routing/rules",
        json={"name": "Big customer", "conditions": {"requester_domains": ["bigcorp.com"]}, "action": {"team_id": vip["id"]}},
    )
    r2 = await c.post(
        "/api/routing/rules",
        json={
            "name": "Urgent outage",
            "conditions": {"keywords": ["outage"], "priorities": ["urgent"]},
            "action": {"assignee_id": ids["owner"]},
        },
    )
    assert r1.status_code == r2.status_code == 201
    t = await ticket(c, subject="Hello", category_id=ws["billing"]["id"], requester_email="ceo@eu.bigcorp.com")
    assert t["assignee_id"] == ids["ben"] and t["team_id"] == vip["id"]
    t = await ticket(c, subject="Total outage!", priority="urgent")
    assert t["assignee_id"] == ids["owner"] and t["team_id"] is None
    # Not urgent: the second rule doesn't match; the category team takes it.
    t = await ticket(c, subject="Small outage", priority="normal", category_id=ws["billing"]["id"])
    assert t["team_id"] == general["id"]
    # Reorder: the outage rule first.
    order = [r2.json()["id"], r1.json()["id"]]
    assert (await c.put("/api/routing/rules/order", json={"ids": order})).status_code == 200
    t = await ticket(c, subject="outage", priority="urgent", requester_email="x@bigcorp.com")
    assert t["assignee_id"] == ids["owner"]
    # A disabled rule is skipped.
    await c.patch(f"/api/routing/rules/{r2.json()['id']}", json={"enabled": False})
    t = await ticket(c, subject="outage", priority="urgent", requester_email="x@bigcorp.com")
    assert t["assignee_id"] == ids["ben"]


async def test_away_caps_and_skills(ws):
    c, ids, people = ws["c"], ws["ids"], ws["people"]
    await make_team(c, "Support", [ids["ana"], ids["ben"]], [ws["billing"]["id"], ws["shipping"]["id"]])
    # Ben handles shipping: shipping tickets go to him first even when he is busier.
    r = await c.put(f"/api/routing/members/{ids['ben']}", json={"skills": [ws["shipping"]["id"]]})
    assert r.status_code == 200 and r.json()["skills"] == [ws["shipping"]["id"]]
    for _ in range(2):
        t = await ticket(c, subject="Where is it", category_id=ws["shipping"]["id"])
        assert t["assignee_id"] == ids["ben"]
    # Ana goes away: billing goes to Ben.
    assert (await people["ana"].put("/api/routing/me", json={"availability": "away"})).json() == {"availability": "away"}
    t = await ticket(c, subject="Bill", category_id=ws["billing"]["id"])
    assert t["assignee_id"] == ids["ben"]
    # Ben reaches his cap: nobody qualifies, the ticket waits in the team's queue.
    await c.put(f"/api/routing/members/{ids['ben']}", json={"max_open": 3})
    t = await ticket(c, subject="Bill 2", category_id=ws["billing"]["id"])
    assert t["assignee_id"] is None and t["team"] == "Support"
    ev = [e for e in await events_of(t["id"]) if e.kind == "routed"][0]
    assert "nobody in Support is available" in ev.data["reason"]
    queue = (await c.get("/api/tickets", params={"team": t["team_id"], "assignee": "none"})).json()["items"]
    assert [x["id"] for x in queue] == [t["id"]]
    # Ana is back: Auto-assign gives her the waiting ticket.
    await people["ana"].put("/api/routing/me", json={"availability": "available"})
    r = await people["ana"].post(f"/api/tickets/{t['id']}/route")
    assert r.status_code == 200 and r.json()["assignee_id"] == ids["ana"]
    assert (await people["ana"].get("/api/tickets", params={"team": "mine"})).json()["items"]


async def test_suspended_or_demoted_members_are_skipped(ws):
    c, ids = ws["c"], ws["ids"]
    await make_team(c, "Support", [ids["ana"], ids["ben"]], [ws["billing"]["id"]])
    async with SessionLocal() as db:
        from app.models import User

        await db.execute(update(User).where(User.id == ids["ana"]).values(status="suspended"))
        await db.commit()
    for _ in range(3):
        assert (await ticket(c, subject="x", category_id=ws["billing"]["id"]))["assignee_id"] == ids["ben"]


async def test_manual_assignee_and_team_choice(ws):
    c, ids = ws["c"], ws["ids"]
    team = await make_team(c, "Support", [ids["ana"]])
    t = await ticket(c, subject="Mine", assignee_id=ids["ben"])
    assert t["assignee_id"] == ids["ben"] and t["status"] == "open"
    assert not [e for e in await events_of(t["id"]) if e.kind == "routed"]
    t = await ticket(c, subject="For the team", team_id=team["id"])
    assert t["assignee_id"] == ids["ana"]
    # Routing off: tickets stay unassigned unless a team is chosen.
    await c.put("/api/routing/settings", json={"enabled": False})
    assert (await ticket(c, subject="Off"))["assignee_id"] is None
    assert (await ticket(c, subject="Off but team", team_id=team["id"]))["assignee_id"] == ids["ana"]


async def test_default_team_and_no_tokens_needed(ws):
    c, ids = ws["c"], ws["ids"]
    team = await make_team(c, "Everyone", [ids["ana"]])
    await c.put("/api/routing/settings", json={"enabled": True, "default_team_id": team["id"]})
    async with SessionLocal() as db:
        ledger = await current_ledger(db, c.headers["X-Workspace-Id"])
        ledger.consumed = ledger.allowance
        await db.commit()
    t = await ticket(c, subject="No AI left")
    assert t["assignee_id"] == ids["ana"] and t["team"] == "Everyone"
    # Deleting the default team clears the setting.
    assert (await c.delete(f"/api/teams/{team['id']}")).status_code == 204
    assert (await c.get("/api/routing/settings")).json()["default_team_id"] is None


async def test_email_tickets_are_routed(ws):
    c, ids = ws["c"], ws["ids"]
    await make_team(c, "Shipping desk", [ids["ben"]], [ws["shipping"]["id"]])
    await c.post(
        "/api/routing/rules",
        json={
            "name": "Email",
            "conditions": {"sources": ["email"]},
            "action": {"team_id": (await make_team(c, "Inbox", [ids["ana"]]))["id"]},
        },
    )
    acct = await connect(c)
    raw, _ = mail()
    await deliver(c, acct["id"], raw)
    t = (await c.get("/api/tickets", params={"source": "email"})).json()["items"][0]
    assert t["assignee_id"] == ids["ana"] and t["team"] == "Inbox"


async def test_conversation_and_form_tickets_are_routed(ws):
    c, ids = ws["c"], ws["ids"]
    await make_team(c, "Chat desk", [ids["ben"]])
    await c.post(
        "/api/routing/rules",
        json={"name": "Chats", "conditions": {"sources": ["conversation", "chatbot_form"]}, "action": {"assignee_id": ids["ben"]}},
    )
    bot = (await c.post("/api/chatbots", json={"name": "Help"})).json()
    async with SessionLocal() as db:
        conv = Conversation(workspace_id=c.headers["X-Workspace-Id"], chatbot_id=bot["id"], channel="widget", visitor_email="v@example.org")
        db.add(conv)
        await db.flush()
        r1 = await convs.escalate(
            db, conv, reason="agent_conversion", live=False, ticket=tk.TicketSpec(subject="From chat", source="conversation")
        )
        r2 = await convs.escalate(
            db, conv, reason="form_submitted", live=False, ticket=tk.TicketSpec(subject="From form", source="chatbot_form")
        )
        await db.commit()
        assert r1.ticket.assignee_id == r2.ticket.assignee_id == __import__("uuid").UUID(ids["ben"])


async def test_parallel_tickets_are_balanced(ws):
    c, ids = ws["c"], ws["ids"]
    await make_team(c, "Support", [ids["ana"], ids["ben"]], [ws["billing"]["id"]])

    async def one(i):
        async with SessionLocal() as db:
            await tk.create(
                db, c.headers["X-Workspace-Id"], tk.TicketSpec(subject=f"T{i}", source="manual", category_id=ws["billing"]["id"])
            )
            await db.commit()

    await asyncio.gather(*(one(i) for i in range(10)))
    async with SessionLocal() as db:
        rows = (await db.execute(select(Ticket.assignee_id))).scalars().all()
    counts = {str(a): rows.count(a) for a in set(rows)}
    assert counts == {ids["ana"]: 5, ids["ben"]: 5}


async def test_first_response_time(ws):
    c = ws["c"]
    acct = await connect(c)
    raw, _ = mail()
    await deliver(c, acct["id"], raw)
    t = (await c.get("/api/tickets", params={"source": "email"})).json()["items"][0]
    assert t["first_response_at"] is None
    r = await c.post(f"/api/tickets/{t['id']}/reply", json={"text": "We're on it."})
    assert r.status_code in (200, 201), r.text
    first = (await c.get(f"/api/tickets/{t['id']}")).json()["ticket"]["first_response_at"]
    assert first
    await c.post(f"/api/tickets/{t['id']}/reply", json={"text": "Update."})
    assert (await c.get(f"/api/tickets/{t['id']}")).json()["ticket"]["first_response_at"] == first


async def test_validation(ws):
    c, ids = ws["c"], ws["ids"]
    team = await make_team(c, "Support", [ids["ana"]])
    assert (await c.post("/api/teams", json={"name": "support"})).json()["detail"]["code"] == "team_name_taken"
    r = await c.post("/api/routing/rules", json={"name": "x", "action": {}})
    assert r.json()["detail"]["code"] == "rule_action_required"
    r = await c.post("/api/routing/rules", json={"name": "x", "action": {"team_id": team["id"], "assignee_id": ids["ana"]}})
    assert r.json()["detail"]["code"] == "rule_action_required"
    r = await c.post("/api/routing/rules", json={"name": "x", "action": {"team_id": team["id"]}})
    assert (await c.delete(f"/api/teams/{team['id']}")).json()["detail"]["code"] == "team_in_use"
    assert (await c.put("/api/routing/rules/order", json={"ids": []})).json()["detail"]["code"] == "invalid_order"


ROUTING_ALLOWED = {
    "configure": {"owner", "admin"},
    "read": {"owner", "admin", "agent", "viewer", "custom"},
    "work": {"owner", "admin", "agent"},
}


async def test_every_role(client):
    await signup(client, "owner@example.com")
    await subscribe(client, "growth")
    clients = await role_team(client)
    me = (await client.get("/api/me")).json()["user"]
    team = (await client.post("/api/teams", json={"name": "Base"})).json()
    rule = (await client.post("/api/routing/rules", json={"name": "r", "action": {"team_id": team["id"]}})).json()
    t = (await client.post("/api/tickets", json={"subject": "x"})).json()
    try:
        for role, c in clients.items():
            checks = [
                ("read", await c.get("/api/teams")),
                ("read", await c.get("/api/routing/members")),
                ("configure", await c.post("/api/teams", json={"name": f"T {role}"})),
                ("configure", await c.patch(f"/api/teams/{team['id']}", json={"description": role})),
                ("configure", await c.put(f"/api/teams/{team['id']}/members", json={"member_ids": [me["id"]]})),
                ("configure", await c.get("/api/routing/rules")),
                ("configure", await c.patch(f"/api/routing/rules/{rule['id']}", json={"name": role})),
                ("configure", await c.get("/api/routing/settings")),
                ("configure", await c.put("/api/routing/settings", json={"enabled": True})),
                ("configure", await c.put(f"/api/routing/members/{me['id']}", json={"max_open": 9})),
                ("work", await c.put("/api/routing/me", json={"availability": "available"})),
                ("work", await c.get("/api/routing/me")),
            ]
            for kind, r in checks:
                if role in ROUTING_ALLOWED[kind]:
                    assert r.status_code < 400, (role, r.request.url, r.text)
                else:
                    assert r.status_code == 403, (role, r.request.url, r.text)
            r = await c.post(f"/api/tickets/{t['id']}/route")
            assert (r.status_code == 403) == (role not in ROUTING_ALLOWED["work"]), (role, r.text)
    finally:
        for name, c in clients.items():
            if name != "owner":
                await c.aclose()


async def test_tenant_isolation(client):
    await signup(client, "alice@example.com")
    await subscribe(client)
    a_team = (await client.post("/api/teams", json={"name": "A"})).json()
    a_rule = (await client.post("/api/routing/rules", json={"name": "r", "action": {"team_id": a_team["id"]}})).json()
    a_cat = await category(client, "A cat")
    a_ticket = await ticket(client)
    alice = (await client.get("/api/me")).json()["user"]
    async with new_client() as bob:
        await signup(bob, "bob@example.com")
        await subscribe(bob)
        b_team = (await bob.post("/api/teams", json={"name": "B"})).json()
        for r in (
            await bob.patch(f"/api/teams/{a_team['id']}", json={"name": "x"}),
            await bob.put(f"/api/teams/{a_team['id']}/members", json={"member_ids": []}),
            await bob.delete(f"/api/teams/{a_team['id']}"),
            await bob.patch(f"/api/routing/rules/{a_rule['id']}", json={"name": "x"}),
            await bob.delete(f"/api/routing/rules/{a_rule['id']}"),
            await bob.post(f"/api/tickets/{a_ticket['id']}/route"),
            await bob.post("/api/routing/rules", json={"name": "x", "action": {"team_id": a_team["id"]}}),
            await bob.post("/api/routing/rules", json={"name": "x", "action": {"assignee_id": alice["id"]}}),
            await bob.post(
                "/api/routing/rules", json={"name": "x", "conditions": {"category_ids": [a_cat["id"]]}, "action": {"team_id": b_team["id"]}}
            ),
            await bob.put(f"/api/teams/{b_team['id']}/members", json={"member_ids": [alice["id"]]}),
            await bob.patch(f"/api/teams/{b_team['id']}", json={"category_ids": [a_cat["id"]]}),
            await bob.put("/api/routing/settings", json={"enabled": True, "default_team_id": a_team["id"]}),
            await bob.put(f"/api/routing/members/{alice['id']}", json={"max_open": 1}),
            await bob.post("/api/tickets", json={"subject": "x", "team_id": a_team["id"]}),
        ):
            assert r.status_code == 404, r.text
        assert [t["name"] for t in (await bob.get("/api/teams")).json()["items"]] == ["B"]
        assert all(m["email"] == "bob@example.com" for m in (await bob.get("/api/routing/members")).json()["items"])
