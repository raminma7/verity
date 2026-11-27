"""API tools: definitions, secrets, the SSRF guard, limits, rate limits, the test button, roles, isolation."""

import asyncio
import json

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.security import decrypt_secret
from app.core.ssrf import GuardedClient, OutboundError
from app.models import ApiTool, ApiToolCall, AuditLog
from app.services.tools import runner
from app.services.tools.transport import FAKE_REQUESTS, fake_resolve
from tests.conftest import new_client, signup, subscribe
from tests.test_knowledge import set_limits
from tests.test_phase3_access import team
from tests.tools import ORDER_TOOL, make_tool

SECRET = ORDER_TOOL["secret"]


@pytest.fixture
async def owner(client):
    await signup(client, "owner@example.com")
    await subscribe(client)
    return client


async def test_create_validates_and_never_returns_the_secret(owner):
    t = await make_tool(owner, activate=False)
    assert t["has_secret"] is True and t["status"] == "draft" and t["host"] == "shop.example.com"
    assert SECRET not in json.dumps(t)
    for path in ("/api/tools", f"/api/tools/{t['id']}"):
        assert SECRET not in (await owner.get(path)).text
    async with SessionLocal() as db:
        row = await db.get(ApiTool, t["id"])
        assert row.secret_enc and SECRET not in row.secret_enc and decrypt_secret(row.secret_enc) == SECRET
        logs = (await db.execute(select(AuditLog).where(AuditLog.action.like("api_tool.%")))).scalars().all()
        assert logs and all(SECRET not in json.dumps(a.data) for a in logs)


@pytest.mark.parametrize(
    "over,code",
    [
        ({"name": "Bad Name"}, "tool_invalid_name"),
        ({"url_template": "http://shop.example.com/orders/{order_id}"}, "tool_invalid_url"),
        ({"url_template": "https://{order_id}.example.com/x"}, "tool_invalid_url"),
        ({"url_template": "https://user:pw@shop.example.com/orders/{order_id}"}, "tool_invalid_url"),
        ({"url_template": "https://shop.example.com/orders/{other}"}, "tool_url_params_mismatch"),
        ({"url_template": "https://10.0.0.5/orders/{order_id}"}, "blocked_address"),
        ({"url_template": "https://169.254.169.254/latest/{order_id}"}, "blocked_address"),
        ({"url_template": "https://localhost/orders/{order_id}"}, "blocked_address"),
        ({"headers": {"Host": "evil.example"}}, "tool_header_not_allowed"),
        ({"headers": {"Authorization": "x"}}, "tool_header_not_allowed"),
        ({"headers": {"X-Bad": "a\r\nInjected: 1"}}, "tool_header_not_allowed"),
        ({"auth_type": "api_key", "auth_header": "Cookie"}, "tool_header_not_allowed"),
        ({"auth_type": "bearer", "secret": None}, "tool_secret_required"),
        (
            {"parameters": [*ORDER_TOOL["parameters"], {"key": "q", "location": "body"}]},
            "tool_invalid_parameters",
        ),
        (
            {"parameters": [{**ORDER_TOOL["parameters"][0], "pattern": "(unclosed"}]},
            "tool_invalid_parameters",
        ),
    ],
)
async def test_definition_rules(owner, over, code):
    r = await owner.post("/api/tools", json={**ORDER_TOOL, **over})
    assert r.status_code in (409, 422), r.text
    assert r.json()["detail"]["code"] == code


async def test_names_are_unique_per_workspace(owner):
    await make_tool(owner, activate=False)
    r = await owner.post("/api/tools", json=ORDER_TOOL)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tool_name_taken"


async def test_secret_replace_and_erase_on_delete(owner):
    t = await make_tool(owner, activate=False)
    r = await owner.put(f"/api/tools/{t['id']}/secret", json={"secret": "new-token-456"})
    assert r.json() == {"has_secret": True}
    async with SessionLocal() as db:
        assert decrypt_secret((await db.get(ApiTool, t["id"])).secret_enc) == "new-token-456"
    # Switching authentication off erases the stored secret.
    r = await owner.patch(f"/api/tools/{t['id']}", json={"auth_type": "none"})
    assert r.json()["has_secret"] is False
    assert (await owner.delete(f"/api/tools/{t['id']}")).status_code == 204
    assert (await owner.get(f"/api/tools/{t['id']}")).status_code == 404
    async with SessionLocal() as db:
        actions = [a.action for a in (await db.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars()]
    assert {"api_tool.created", "api_tool.secret_changed", "api_tool.updated", "api_tool.deleted"} <= set(actions)


async def test_activation_needs_a_secret(owner):
    t = await make_tool(owner, activate=False, auth_type="api_key", auth_header="X-Api-Key")
    async with SessionLocal() as db:
        await db.execute(update(ApiTool).where(ApiTool.id == t["id"]).values(secret_enc=None))
        await db.commit()
    r = await owner.patch(f"/api/tools/{t['id']}", json={"status": "active"})
    assert r.json()["detail"]["code"] == "tool_secret_required"


async def test_limit_holds_under_parallel_creates(owner):
    await set_limits(owner.headers["X-Workspace-Id"], api_tools=3)
    rs = await asyncio.gather(*(owner.post("/api/tools", json={**ORDER_TOOL, "name": f"tool_{i}"}) for i in range(8)))
    assert sorted(r.status_code for r in rs).count(201) == 3
    assert all(r.json()["detail"]["code"] == "limit_reached" for r in rs if r.status_code != 201)
    assert (await owner.get("/api/tools")).json()["used"] == 3


async def test_test_button_calls_the_api_with_the_credential(owner):
    t = await make_tool(owner, activate=False)
    r = await owner.post(f"/api/tools/{t['id']}/test", json={"args": {"order_id": "12345"}})
    body = r.json()
    assert body["status"] == "ok" and body["status_word"] == "Succeeded" and body["http_status"] == 200
    assert "shipped" in body["preview"] and SECRET not in r.text
    req = FAKE_REQUESTS[-1]
    assert req["headers"]["authorization"] == f"Bearer {SECRET}"
    # Pinned: connected to the checked address, with the original Host header and TLS name.
    assert req["connected_to"] == "93.184.215.14" and req["host"] == "shop.example.com" and req["sni"] == "shop.example.com"
    assert req["path"] == "/orders/12345"
    t = (await owner.get(f"/api/tools/{t['id']}")).json()
    assert t["last_test_status"] == "ok" and t["last_test_word"] == "Succeeded"
    calls = (await owner.get(f"/api/tools/{t['id']}/calls")).json()["items"]
    assert calls[0]["origin"] == "test" and calls[0]["status"] == "ok" and calls[0]["args"] == {"order_id": "12345"}


async def test_api_key_header_and_query_and_body(owner):
    t = await make_tool(
        owner,
        activate=False,
        name="echo",
        method="POST",
        url_template="https://api.example.com/echo",
        auth_type="api_key",
        auth_header="X-Api-Key",
        secret="key-789",
        headers={"X-Client": "verity"},
        parameters=[
            {"key": "sku", "type": "string", "location": "query", "required": True},
            {"key": "qty", "type": "integer", "location": "body", "required": True},
            {"key": "email", "type": "string", "location": "body", "source": "visitor_email", "required": True},
        ],
    )
    r = await owner.post(f"/api/tools/{t['id']}/test", json={"args": {"sku": "A-1", "qty": 2}, "visitor_email": "c@example.org"})
    assert r.json()["status"] == "ok", r.text
    req = FAKE_REQUESTS[-1]
    assert req["headers"]["x-api-key"] == "key-789" and req["headers"]["x-client"] == "verity"
    assert req["query"] == {"sku": "A-1"} and json.loads(req["body"]) == {"qty": 2, "email": "c@example.org"}


@pytest.mark.parametrize(
    "url,status,code",
    [
        ("https://db.internal.example/orders/{order_id}", "blocked", "blocked_address"),
        ("https://mixed.example.com/orders/{order_id}", "blocked", "blocked_address"),
        ("https://nowhere.test/orders/{order_id}", "failed", "dns_failed"),
        ("https://shop.example.com/redirect/{order_id}", "failed", "redirect_refused"),
    ],
)
async def test_ssrf_guard_on_every_call(owner, url, status, code):
    t = await make_tool(owner, activate=False, url_template=url)
    if "redirect" in url:
        # The fake answers /redirect only; point the path there.
        async with SessionLocal() as db:
            await db.execute(
                update(ApiTool).where(ApiTool.id == t["id"]).values(url_template="https://shop.example.com/redirect", parameters=[])
            )
            await db.commit()
    r = await owner.post(f"/api/tools/{t['id']}/test", json={"args": {"order_id": "12345"}})
    assert r.json()["status"] == status and r.json()["error_code"] == code, r.text
    if code != "redirect_refused":
        assert not FAKE_REQUESTS  # never connected


async def test_timeout_and_size_cap(owner):
    slow = await make_tool(
        owner, activate=False, name="slow", url_template="https://shop.example.com/slow", parameters=[], timeout_seconds=1
    )
    r = await owner.post(f"/api/tools/{slow['id']}/test", json={})
    assert r.json()["status"] == "timeout" and r.json()["status_word"] == "Timed out"
    big = await make_tool(owner, activate=False, name="big", url_template="https://shop.example.com/big", parameters=[], max_response_kb=8)
    r = await owner.post(f"/api/tools/{big['id']}/test", json={})
    assert r.json()["status"] == "too_large"


async def test_http_errors_are_reported(owner):
    t = await make_tool(owner, activate=False)
    r = await owner.post(f"/api/tools/{t['id']}/test", json={"args": {"order_id": "40400"}})
    assert r.json()["status"] == "http_error" and r.json()["http_status"] == 404


@pytest.mark.parametrize(
    "args",
    [{}, {"order_id": "12"}, {"order_id": "123456789"}, {"order_id": "../../admin"}, {"order_id": ["1"]}, {"order_id": "12345\r\nX: 1"}],
)
async def test_arguments_are_checked_on_the_server(owner, args):
    t = await make_tool(owner, activate=False)
    r = await owner.post(f"/api/tools/{t['id']}/test", json={"args": args})
    assert r.json()["status"] == "invalid_args"
    assert not FAKE_REQUESTS


async def test_path_values_are_percent_encoded(owner):
    t = await make_tool(owner, activate=False, parameters=[{"key": "order_id", "type": "string", "location": "path"}])
    await owner.post(f"/api/tools/{t['id']}/test", json={"args": {"order_id": "a/../../x?y=1#z"}})
    req = FAKE_REQUESTS[-1]
    assert req["host"] == "shop.example.com" and req["path"] == "/orders/a%2F..%2F..%2Fx%3Fy%3D1%23z" and req["query"] == {}
    r = await owner.post(f"/api/tools/{t['id']}/test", json={"args": {"order_id": ".."}})
    assert r.json()["status"] == "invalid_args"


async def test_test_runs_are_rate_limited(owner):
    t = await make_tool(owner, activate=False)
    statuses = [(await owner.post(f"/api/tools/{t['id']}/test", json={"args": {"order_id": "12345"}})).json()["status"] for _ in range(22)]
    assert statuses[:20] == ["ok"] * 20 and statuses[-1] == "rate_limited"


async def test_chat_rate_limits_per_tool_and_conversation(owner):
    t = await make_tool(owner, rate_limit_per_minute=3)
    async with SessionLocal() as db:
        tool = await db.get(ApiTool, t["id"])
        outs = [await runner.run(db, tool, {"order_id": "12345"}) for _ in range(4)]
        assert [o.status for o in outs] == ["ok", "ok", "ok", "rate_limited"]
        await db.commit()
        logged = (await db.execute(select(ApiToolCall).where(ApiToolCall.tool_id == tool.id))).scalars().all()
        assert len(logged) == 4 and all(SECRET not in json.dumps(c.args) for c in logged)


async def test_guarded_client_directly():
    """Pinning, IPv6 and IPv4-mapped loopback, metadata, https only."""
    c = GuardedClient(resolve=fake_resolve)
    for url in ("https://[::1]/x", "https://[::ffff:127.0.0.1]/x", "https://169.254.169.254/x", "https://100.64.0.1/x"):
        with pytest.raises(OutboundError) as e:
            await c.request("GET", url)
        assert e.value.code == "blocked_address"
    with pytest.raises(OutboundError) as e:
        await c.request("GET", "http://shop.example.com/x")
    assert e.value.code == "https_required"


async def test_context_parameters_are_hidden_from_the_model(owner):
    from app.services.tools.definitions import claude_tool

    t = await make_tool(
        owner,
        activate=False,
        name="my_orders",
        url_template="https://shop.example.com/orders/{order_id}",
        parameters=[
            {"key": "order_id", "type": "string", "location": "path"},
            {"key": "email", "type": "string", "location": "query", "source": "visitor_email", "required": True},
            {"key": "note", "type": "string", "location": "query"},
        ],
    )
    async with SessionLocal() as db:
        tool = await db.get(ApiTool, t["id"])
        spec = claude_tool(tool)
        assert set(spec["input_schema"]["properties"]) == {"order_id", "note"} and spec["strict"] is True
        assert spec["input_schema"]["additionalProperties"] is False
        # Without the visitor's email the tool isn't offered; the model can't supply it either.
        from app.models import Conversation

        assert not runner.available_for(tool, Conversation())
        assert runner.available_for(tool, Conversation(visitor_email="v@example.org"))
        out = await runner.run(
            db, tool, {"order_id": "12345", "email": "someone-else@example.org"}, conversation=Conversation(visitor_email="v@example.org")
        )
        assert out.status == "ok"
        assert FAKE_REQUESTS[-1]["query"] == {"email": "v@example.org"}


async def test_disabling_a_tool_unlinks_it_from_chatbots(owner):
    t = await make_tool(owner)
    bot = (await owner.post("/api/chatbots", json={"name": "Help"})).json()
    r = await owner.put(f"/api/chatbots/{bot['id']}/tools", json={"tool_ids": [t["id"]]})
    assert [i["enabled"] for i in r.json()["items"]] == [True]
    assert (await owner.get(f"/api/tools/{t['id']}")).json()["chatbots"] == [{"id": bot["id"], "name": "Help"}]
    await owner.patch(f"/api/tools/{t['id']}", json={"status": "disabled"})
    assert (await owner.get(f"/api/chatbots/{bot['id']}/tools")).json()["items"] == []
    r = await owner.put(f"/api/chatbots/{bot['id']}/tools", json={"tool_ids": [t["id"]]})
    assert r.json()["detail"]["code"] == "tool_not_active"


async def test_tools_need_a_subscription(client):
    await signup(client, "nosub@example.com")
    r = await client.get("/api/tools")
    assert r.status_code == 402 and r.json()["detail"]["code"] == "subscription_required"


ALLOWED_TOOLS = {"owner", "admin"}
ALLOWED_BOT_READ = {"owner", "admin", "agent", "viewer"}


async def test_every_role(client):
    await signup(client, "owner@example.com")
    await subscribe(client, "growth")
    clients = await team(client)
    t = await make_tool(client)
    bot = (await client.post("/api/chatbots", json={"name": "Help"})).json()
    try:
        for role, c in clients.items():
            checks = [
                (ALLOWED_TOOLS, await c.get("/api/tools")),
                (ALLOWED_TOOLS, await c.get(f"/api/tools/{t['id']}")),
                (ALLOWED_TOOLS, await c.post("/api/tools", json={**ORDER_TOOL, "name": f"t_{role}"})),
                (ALLOWED_TOOLS, await c.patch(f"/api/tools/{t['id']}", json={"display_name": role})),
                (ALLOWED_TOOLS, await c.put(f"/api/tools/{t['id']}/secret", json={"secret": "s"})),
                (ALLOWED_TOOLS, await c.post(f"/api/tools/{t['id']}/test", json={"args": {"order_id": "12345"}})),
                (ALLOWED_TOOLS, await c.get(f"/api/tools/{t['id']}/calls")),
                (ALLOWED_BOT_READ, await c.get(f"/api/chatbots/{bot['id']}/tools")),
                ({"owner", "admin"}, await c.put(f"/api/chatbots/{bot['id']}/tools", json={"tool_ids": [t["id"]]})),
            ]
            for allowed, r in checks:
                if role in allowed:
                    assert r.status_code < 400, (role, r.request.url, r.text)
                else:
                    assert r.status_code == 403, (role, r.request.url, r.text)
            if role not in ALLOWED_TOOLS:
                assert (await c.delete(f"/api/tools/{t['id']}")).status_code == 403
    finally:
        for name, c in clients.items():
            if name != "owner":
                await c.aclose()


async def test_tenant_isolation(client):
    await signup(client, "alice@example.com")
    await subscribe(client)
    t = await make_tool(client)
    async with new_client() as bob:
        await signup(bob, "bob@example.com")
        await subscribe(bob)
        bob_bot = (await bob.post("/api/chatbots", json={"name": "Bob"})).json()
        alice_bot = (await client.post("/api/chatbots", json={"name": "Alice"})).json()
        for r in (
            await bob.get(f"/api/tools/{t['id']}"),
            await bob.patch(f"/api/tools/{t['id']}", json={"display_name": "x"}),
            await bob.put(f"/api/tools/{t['id']}/secret", json={"secret": "x"}),
            await bob.post(f"/api/tools/{t['id']}/test", json={}),
            await bob.get(f"/api/tools/{t['id']}/calls"),
            await bob.delete(f"/api/tools/{t['id']}"),
            await bob.get(f"/api/chatbots/{alice_bot['id']}/tools"),
            await bob.put(f"/api/chatbots/{bob_bot['id']}/tools", json={"tool_ids": [t["id"]]}),
        ):
            assert r.status_code == 404, r.text
        assert (await bob.get("/api/tools")).json()["items"] == []
