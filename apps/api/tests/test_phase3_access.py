"""Phase 3 permissions for every role, the subscription gate, and tenant isolation."""

import pytest

from tests.conftest import last_token, new_client, signup, subscribe
from tests.phase3 import category, form, ticket
from tests.test_chatbots import say, setup_bot, start
from tests.test_workspaces import add_member

ROLES = ("owner", "admin", "agent", "viewer", "custom")
# What each role's permissions allow. The custom role has only tickets.read.
ALLOWED = {
    "tickets.read": {"owner", "admin", "agent", "viewer", "custom"},
    "tickets.manage": {"owner", "admin", "agent"},
    "tickets.configure": {"owner", "admin"},
    "chatbots.read": {"owner", "admin", "agent", "viewer"},
    "chatbots.manage": {"owner", "admin"},
}


async def team(owner_client) -> dict:
    """A Growth workspace with one member per role (plus a custom role with only tickets.read)."""
    clients = {"owner": owner_client}
    for role in ("admin", "agent", "viewer"):
        c = new_client()
        await add_member(owner_client, c, f"{role}@example.com", role)
        clients[role] = c
    r = await owner_client.post("/api/workspace/roles", json={"name": "Ticket reader", "permissions": ["tickets.read", "workspace.read"]})
    assert r.status_code == 201, r.text
    r = await owner_client.post("/api/workspace/invitations", json={"email": "custom@example.com", "role_id": r.json()["id"]})
    assert r.status_code == 201, r.text
    c = new_client()
    token = last_token("custom@example.com", "/invite/")
    await signup(c, "custom@example.com", "Custom Person")
    assert (await c.post(f"/api/invitations/{token}/accept")).status_code == 200
    c.headers["X-Workspace-Id"] = owner_client.headers["X-Workspace-Id"]
    clients["custom"] = c
    return clients


@pytest.fixture
async def setup(client):
    await signup(client, "owner@example.com")
    await subscribe(client, "growth")
    clients = await team(client)
    cat = await category(client)
    f = await form(client, cat["id"])
    t = await ticket(client, category_id=cat["id"])
    yield clients, cat, f, t
    for name, c in clients.items():
        if name != "owner":
            await c.aclose()


def requests(cat, f, t):
    return [
        ("tickets.read", "GET", "/api/ticket-categories", None),
        ("tickets.configure", "PUT", "/api/ticket-categories/order", {"ids": [cat["id"]]}),
        ("tickets.configure", "POST", "/api/ticket-categories", {"name": "New one"}),
        ("tickets.configure", "PATCH", f"/api/ticket-categories/{cat['id']}", {"description": "x"}),
        ("tickets.read", "GET", "/api/tickets", None),
        ("tickets.read", "GET", f"/api/tickets/{t['id']}", None),
        ("tickets.read", "GET", "/api/tickets/assignees", None),
        ("tickets.manage", "POST", "/api/tickets", {"subject": "From a role test"}),
        ("tickets.manage", "PATCH", f"/api/tickets/{t['id']}", {"priority": "high"}),
        ("tickets.manage", "POST", f"/api/tickets/{t['id']}/notes", {"text": "note"}),
        ("chatbots.read", "GET", "/api/forms", None),
        ("chatbots.read", "GET", f"/api/forms/{f['id']}", None),
        ("chatbots.manage", "POST", "/api/forms", {"name": "F", "category_id": cat["id"]}),
        ("chatbots.manage", "PATCH", f"/api/forms/{f['id']}", {"description": "x"}),
    ]


@pytest.mark.parametrize("role", ROLES)
async def test_every_endpoint_for_every_role(setup, role):
    clients, cat, f, t = setup
    c = clients[role]
    for perm, method, path, body in requests(cat, f, t):
        r = await c.request(method, path, json=body)
        if role in ALLOWED[perm]:
            assert r.status_code < 400, (role, method, path, r.text)
        else:
            assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden", (role, method, path, r.status_code)


async def test_conversation_to_ticket_needs_tickets_manage(client):
    await signup(client, "owner@example.com")
    await subscribe(client, "growth")
    bot = (await client.post("/api/chatbots", json={"name": "Help"})).json()
    await client.patch(f"/api/chatbots/{bot['id']}", json={"allowed_domains": ["example.com"]})
    clients = await team(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "Hello")
        for role in ("viewer", "custom", "agent"):
            r = await clients[role].post(f"/api/conversations/{conv}/ticket", json={})
            assert r.status_code == (201 if role == "agent" else 403), (role, r.text)
    for name, c in clients.items():
        if name != "owner":
            await c.aclose()


async def test_tickets_need_a_subscription(client):
    await signup(client, "nosub@example.com")
    for method, path in (("GET", "/api/tickets"), ("GET", "/api/ticket-categories"), ("GET", "/api/forms"), ("POST", "/api/tickets")):
        r = await client.request(method, path, json={"subject": "x"} if method == "POST" else None)
        assert r.status_code == 402 and r.json()["detail"]["code"] == "subscription_required", path


async def test_tenant_isolation(client):
    bot = await setup_bot(client, "alice@example.com")
    cat = await category(client)
    f = await form(client, cat["id"], chatbot_ids=[bot["id"]])
    t = await ticket(client, category_id=cat["id"])
    alice_id = (await client.get("/api/me")).json()["user"]["id"]
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        offer = (await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": f["id"]})).json()

    async with new_client() as bob:
        bob_bot = await setup_bot(bob, "bob@example.com", domains=("bob.example.org",))
        bob_cat = await category(bob, "Bob's")
        # Bob can't reach Alice's rows through his own workspace...
        for method, path, body in (
            ("GET", f"/api/tickets/{t['id']}", None),
            ("PATCH", f"/api/tickets/{t['id']}", {"status": "closed"}),
            ("POST", f"/api/tickets/{t['id']}/notes", {"text": "x"}),
            ("POST", f"/api/tickets/{t['id']}/reply", {"text": "x"}),
            ("PATCH", f"/api/ticket-categories/{cat['id']}", {"name": "Hacked"}),
            ("POST", f"/api/ticket-categories/{cat['id']}/archive", None),
            ("GET", f"/api/forms/{f['id']}", None),
            ("PATCH", f"/api/forms/{f['id']}", {"name": "Hacked"}),
            ("POST", f"/api/conversations/{conv}/ticket", {}),
        ):
            r = await bob.request(method, path, json=body)
            assert r.status_code == 404, (method, path, r.status_code)
        # ...or use Alice's ids inside his own requests.
        assert (await bob.post("/api/tickets", json={"subject": "x", "category_id": cat["id"]})).status_code == 404
        assert (await bob.post("/api/tickets", json={"subject": "x", "assignee_id": alice_id})).status_code == 404
        r = await bob.post("/api/forms", json={"name": "F", "category_id": bob_cat["id"], "chatbot_ids": [bot["id"]]})
        assert r.status_code == 404
        r = await bob.patch(f"/api/chatbots/{bob_bot['id']}", json={"escalation": {"fallback_form_id": f["id"]}})
        assert r.status_code == 404
        # ...or point at Alice's workspace.
        assert (await bob.get("/api/tickets", headers={"X-Workspace-Id": client.headers["X-Workspace-Id"]})).status_code == 404
        # Lists stay separate.
        assert (await bob.get("/api/tickets")).json()["items"] == []
        assert [c["name"] for c in (await bob.get("/api/ticket-categories")).json()] == ["Bob's"]
        assert f["id"] not in [x["id"] for x in (await bob.get("/api/forms")).json()]

        # Through Bob's widget, Alice's form and form messages don't exist.
        async with new_client() as v:
            bbase, bconv = await start(v, bob_bot["public_key"], origin="https://bob.example.org")
            assert (await v.post(f"{bbase}/conversations/{bconv}/forms", json={"form_id": f["id"]})).status_code == 404
            r = await v.post(f"{bbase}/conversations/{bconv}/forms/{offer['id']}/prefill")
            assert r.status_code == 404
            assert f["id"] not in str((await v.get(f"{bbase}/forms")).json())
    # Alice's ticket is untouched.
    assert (await client.get(f"/api/tickets/{t['id']}")).json()["ticket"]["status"] == "new"
