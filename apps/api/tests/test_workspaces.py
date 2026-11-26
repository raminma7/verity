from tests.conftest import last_token, new_client, signup, subscribe


async def roles(c) -> dict:
    return {r["key"]: r["id"] for r in (await c.get("/api/workspace/roles")).json()}


async def add_member(owner, c, email: str, role_key: str):
    """Invites `email` with `role_key`, has them sign up on client `c` and accept."""
    r = await owner.post("/api/workspace/invitations", json={"email": email, "role_id": (await roles(owner))[role_key]})
    assert r.status_code == 201, r.text
    token = last_token(email, "/invite/")
    await signup(c, email, "Member Person")
    r = await c.post(f"/api/invitations/{token}/accept")
    assert r.status_code == 200, r.text
    c.headers["X-Workspace-Id"] = owner.headers["X-Workspace-Id"]


async def test_invitations_need_subscription(client):
    await signup(client, "o@example.com")
    r = await client.post("/api/workspace/invitations", json={"email": "x@example.com", "role_id": (await roles(client))["agent"]})
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "subscription_required"


async def test_invite_accept_and_roles(client):
    await signup(client, "owner@example.com")
    await subscribe(client)
    async with new_client() as agent:
        await add_member(client, agent, "agent@example.com", "agent")
        me = (await agent.get("/api/me")).json()
        assert len(me["workspaces"]) == 2  # their own + the invited one
        ws = (await agent.get("/api/workspace")).json()
        assert ws["role"]["key"] == "agent"
        assert "tickets.manage" in ws["permissions"]
        assert "billing.read" not in ws["permissions"]
        # Agents can't manage members or see billing.
        assert (await agent.get("/api/billing")).status_code == 403
        r = await agent.post("/api/workspace/invitations", json={"email": "z@example.com", "role_id": (await roles(client))["viewer"]})
        assert r.status_code == 403


async def test_invite_for_other_email_cannot_be_used(client):
    await signup(client, "owner@example.com")
    await subscribe(client)
    await client.post("/api/workspace/invitations", json={"email": "right@example.com", "role_id": (await roles(client))["viewer"]})
    token = last_token("right@example.com", "/invite/")
    async with new_client() as wrong:
        await signup(wrong, "wrong@example.com")
        r = await wrong.post(f"/api/invitations/{token}/accept")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "email_mismatch"


async def test_admin_cannot_touch_billing_or_owner(client):
    await signup(client, "owner@example.com")
    await subscribe(client)
    async with new_client() as admin:
        await add_member(client, admin, "admin@example.com", "admin")
        assert (await admin.get("/api/billing")).status_code == 403
        members = (await admin.get("/api/workspace/members")).json()["members"]
        owner = next(m for m in members if m["is_owner"])
        r = await admin.delete(f"/api/workspace/members/{owner['id']}")
        assert r.status_code == 403
        # Admin can't grant Owner or another Admin.
        r = await admin.post("/api/workspace/invitations", json={"email": "n@example.com", "role_id": (await roles(client))["admin"]})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "role_escalation"
        r = await admin.post("/api/workspace/invitations", json={"email": "n@example.com", "role_id": (await roles(client))["agent"]})
        assert r.status_code == 201


async def test_member_limit_enforced(client):
    await signup(client, "owner@example.com")
    await subscribe(client, "starter")  # 3 members
    vid = (await roles(client))["viewer"]
    assert (await client.post("/api/workspace/invitations", json={"email": "a@example.com", "role_id": vid})).status_code == 201
    assert (await client.post("/api/workspace/invitations", json={"email": "b@example.com", "role_id": vid})).status_code == 201
    r = await client.post("/api/workspace/invitations", json={"email": "c@example.com", "role_id": vid})
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "limit_reached"


async def test_tenant_isolation(client):
    await signup(client, "alice@example.com")
    await subscribe(client)
    alice_ws = client.headers["X-Workspace-Id"]
    async with new_client() as bob:
        await signup(bob, "bob@example.com")
        bob_ws = bob.headers["X-Workspace-Id"]
        # Bob points at Alice's workspace: refused as if it didn't exist.
        for path in ("/api/workspace", "/api/workspace/members", "/api/billing", "/api/usage/summary"):
            r = await bob.get(path, headers={"X-Workspace-Id": alice_ws})
            assert r.status_code == 404, path
        r = await bob.post(
            "/api/workspace/invitations",
            headers={"X-Workspace-Id": alice_ws},
            json={"email": "x@example.com", "role_id": (await roles(client))["viewer"]},
        )
        assert r.status_code == 404
        # Alice's member IDs can't be used through Bob's workspace.
        alice_members = (await client.get("/api/workspace/members")).json()["members"]
        r = await bob.delete(f"/api/workspace/members/{alice_members[0]['id']}")
        assert r.status_code in (403, 404)
        assert (await client.get("/api/workspace/members")).status_code == 200
        assert bob_ws != alice_ws


async def test_custom_role_cannot_escalate(client):
    await signup(client, "owner@example.com")
    await subscribe(client, "growth")
    async with new_client() as admin:
        await add_member(client, admin, "admin@example.com", "admin")
        r = await admin.post("/api/workspace/roles", json={"name": "Billing clerk", "permissions": ["billing.read"]})
        assert r.status_code == 403
        r = await admin.post("/api/workspace/roles", json={"name": "Triage", "permissions": ["tickets.read", "workspace.read"]})
        assert r.status_code == 201
        assert r.json()["rank"] < 80


async def test_owner_transfer_and_leave(client):
    await signup(client, "owner@example.com")
    await subscribe(client)
    async with new_client() as other:
        await add_member(client, other, "next@example.com", "admin")
        assert (await client.post("/api/workspace/leave")).status_code == 409
        members = (await client.get("/api/workspace/members")).json()["members"]
        target = next(m for m in members if m["email"] == "next@example.com")
        assert (await client.post(f"/api/workspace/transfer-ownership/{target['id']}")).status_code == 200
        ws = (await other.get("/api/workspace")).json()
        assert ws["role"]["key"] == "owner"
        assert (await client.post("/api/workspace/leave")).status_code == 204


async def test_multiple_workspaces_and_switch(client):
    await signup(client, "multi@example.com")
    r = await client.post("/api/workspaces", json={"name": "Second team"})
    assert r.status_code == 201
    second = r.json()["id"]
    assert (await client.post(f"/api/me/switch-workspace/{second}")).status_code == 200
    del client.headers["X-Workspace-Id"]
    ws = (await client.get("/api/workspace")).json()
    assert ws["workspace"]["name"] == "Second team"
    assert len((await client.get("/api/me")).json()["workspaces"]) == 2
