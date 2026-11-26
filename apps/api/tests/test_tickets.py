import asyncio

from app.core.db import SessionLocal
from app.models import Ticket
from app.services import tickets as svc
from tests.conftest import new_client
from tests.phase3 import category, owner, ticket
from tests.test_workspaces import add_member


async def test_manual_ticket_numbers_and_defaults(client):
    me = await owner(client)
    cat = await category(client)
    t1 = await ticket(client, category_id=cat["id"], requester_email="Cust@Example.com", description="Smoke everywhere")
    t2 = await ticket(client, subject="Second")
    assert (t1["number"], t2["number"]) == (1, 2)
    assert t1["status"] == "new" and t1["priority"] == "normal" and t1["source"] == "manual"
    assert t1["requester_email"] == "cust@example.com" and t1["category"] == "Returns"
    t3 = await ticket(client, assignee_id=me["user"]["id"], priority="urgent")
    assert t3["status"] == "open" and t3["assignee"] == "Ada Lovelace"
    r = await client.post("/api/tickets", json={"subject": "x", "priority": "whenever"})
    assert r.status_code == 422


async def test_numbers_are_unique_under_parallel_creates(client):
    await owner(client)
    numbers = [t["number"] for t in await asyncio.gather(*[ticket(client, subject=f"T{i}") for i in range(15)])]
    assert sorted(numbers) == list(range(1, 16))


async def test_status_transitions(client):
    await owner(client)
    t = await ticket(client)
    path = [("pending", 200), ("open", 200), ("resolved", 200), ("closed", 200), ("pending", 422), ("open", 200), ("new", 422)]
    for status, code in path:
        r = await client.patch(f"/api/tickets/{t['id']}", json={"status": status})
        assert r.status_code == code, (status, r.text)
        if code == 422:
            assert r.json()["detail"]["code"] in ("invalid_status_transition", "invalid_input")
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert [e["data"]["to"] for e in detail["events"] if e["kind"] == "status"] == ["pending", "open", "resolved", "closed", "open"]
    assert detail["ticket"]["resolved_at"] is None and detail["ticket"]["closed_at"] is None


async def test_assignment_only_to_ticket_workers(client):
    await owner(client)
    t = await ticket(client)
    async with new_client() as agent_c, new_client() as viewer_c:
        await add_member(client, agent_c, "agent@example.com", "agent")
        await add_member(client, viewer_c, "viewer@example.com", "viewer")
        people = {p["email"]: p["id"] for p in (await client.get("/api/tickets/assignees")).json()}
        assert "agent@example.com" in people and "viewer@example.com" not in people
        viewer_id = (await viewer_c.get("/api/me")).json()["user"]["id"]
        r = await client.patch(f"/api/tickets/{t['id']}", json={"assignee_id": viewer_id})
        assert r.json()["detail"]["code"] == "assignee_not_allowed"
        r = await client.patch(f"/api/tickets/{t['id']}", json={"assignee_id": people["agent@example.com"]})
        assert r.json()["assignee"] == "Member Person" and r.json()["status"] == "open"
        # The agent is notified, and sees it under "mine".
        mine = (await agent_c.get("/api/tickets?assignee=me")).json()["items"]
        assert [x["id"] for x in mine] == [t["id"]]
        r = await client.patch(f"/api/tickets/{t['id']}", json={"unassign": True})
        assert r.json()["assignee_id"] is None
        assert [x["id"] for x in (await client.get("/api/tickets?assignee=none")).json()["items"]] == [t["id"]]


async def test_notes_are_internal_and_audited(client):
    await owner(client)
    t = await ticket(client)
    r = await client.post(f"/api/tickets/{t['id']}/notes", json={"text": "Customer is a VIP."})
    assert r.status_code == 201 and r.json()["internal"] and r.json()["author"] == "Ada Lovelace"
    log = (await client.get("/api/workspace/audit-logs")).json()
    actions = [e["action"] for e in log]
    assert "ticket.note_added" in actions and "ticket.created" in actions
    # No chat to answer in: replies wait for the email integration.
    r = await client.post(f"/api/tickets/{t['id']}/reply", json={"text": "Hi"})
    assert r.json()["detail"]["code"] == "ticket_reply_unavailable"


async def test_search_and_filters(client):
    me = await owner(client)
    a = await category(client, "Returns")
    b = await category(client, "Billing")
    t1 = await ticket(client, subject="Broken zipper on jacket", category_id=a["id"], priority="high", requester_email="zed@example.com")
    t2 = await ticket(client, subject="Invoice missing VAT", category_id=b["id"], assignee_id=me["user"]["id"])
    await client.patch(f"/api/tickets/{t2['id']}", json={"status": "resolved"})

    async def ids(qs):
        r = await client.get(f"/api/tickets?{qs}")
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()["items"]]

    assert await ids("q=zipp") == [t1["id"]]
    assert await ids("q=zed@example.com") == [t1["id"]]
    assert await ids(f"q=%23{t2['number']}") == [t2["id"]]
    assert await ids("q=invoice vat") == [t2["id"]]
    assert await ids("status=active") == [t1["id"]]
    assert await ids("status=resolved") == [t2["id"]]
    assert await ids("priority=high") == [t1["id"]]
    assert await ids(f"category_id={b['id']}") == [t2["id"]]
    assert await ids("assignee=me") == [t2["id"]]
    assert await ids("source=manual") == [t2["id"], t1["id"]]
    counts = (await client.get("/api/tickets")).json()["counts"]
    assert counts["new"] == 1 and counts["resolved"] == 1


async def _ticket_row(ticket_id) -> Ticket:
    async with SessionLocal() as db:
        return await db.get(Ticket, ticket_id)


async def test_customer_reply_reopens_per_workspace_setting(client):
    await owner(client)
    t = await ticket(client)
    for status in ("resolved",):
        await client.patch(f"/api/tickets/{t['id']}", json={"status": status})
    async with SessionLocal() as db:
        row = await db.get(Ticket, t["id"])
        assert await svc.customer_replied(db, row, "It broke again") is True
        await db.commit()
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert detail["ticket"]["status"] == "open" and detail["ticket"]["reopened_count"] == 1
    assert [e["kind"] for e in detail["events"]][-2:] == ["customer_message", "reopened"]

    # With reopen_on_reply off, a resolved ticket stays resolved; pending always moves to open.
    assert (await client.patch("/api/workspace", json={"settings": {"reopen_on_reply": False}})).status_code == 200
    await client.patch(f"/api/tickets/{t['id']}", json={"status": "resolved"})
    async with SessionLocal() as db:
        row = await db.get(Ticket, t["id"])
        assert await svc.customer_replied(db, row, "Hello?") is False
        await db.commit()
    assert (await _ticket_row(t["id"])).status == "resolved"
    await client.patch(f"/api/tickets/{t['id']}", json={"status": "open"})
    await client.patch(f"/api/tickets/{t['id']}", json={"status": "pending"})
    async with SessionLocal() as db:
        row = await db.get(Ticket, t["id"])
        await svc.customer_replied(db, row, "Here is the photo")
        await db.commit()
    assert (await _ticket_row(t["id"])).status == "open"
    # Closed tickets never reopen.
    await client.patch(f"/api/tickets/{t['id']}", json={"status": "closed"})
    await client.patch("/api/workspace", json={"settings": {"reopen_on_reply": True}})
    async with SessionLocal() as db:
        row = await db.get(Ticket, t["id"])
        assert await svc.customer_replied(db, row, "Anyone?") is False
        await db.commit()
    assert (await _ticket_row(t["id"])).status == "closed"
