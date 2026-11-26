import json

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Notification, UsageEvent
from app.services import email as mail
from app.services.quota import current_ledger
from tests.conftest import new_client, signup, subscribe
from tests.test_knowledge import set_limits
from tests.test_workspaces import add_member

ORIGIN = "https://www.example.com"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def setup_bot(client, email="bot@example.com", domains=("example.com",)):
    await signup(client, email)
    await subscribe(client)
    await client.post("/api/knowledge/files", files={"file": ("refunds.txt", b"Refunds are processed within five business days.")})
    bot = (await client.post("/api/chatbots", json={"name": "Acme Help"})).json()
    if len(domains) > 1:
        await set_limits(client.headers["X-Workspace-Id"], chatbot_domains=-1)
    r = await client.patch(f"/api/chatbots/{bot['id']}", json={"allowed_domains": list(domains)})
    assert r.status_code == 200, r.text
    return r.json()


def widget(c, key, origin=ORIGIN):
    c.headers["X-Verity-Parent-Origin"] = origin
    return f"/api/widget/{key}"


async def start(c, key, origin=ORIGIN):
    base = widget(c, key, origin)
    r = await c.post(f"{base}/conversations", json={"page_url": f"{origin}/pricing"})
    assert r.status_code == 201, r.text
    c.headers["X-Visitor-Token"] = r.json()["visitor_token"]
    return base, r.json()["conversation_id"]


async def say(c, base, conv, text):
    r = await c.post(f"{base}/conversations/{conv}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    return events(r.text)


async def test_settings_snippet_and_validation(client):
    bot = await setup_bot(client)
    assert bot["allowed_domains"] == ["example.com"]
    assert bot["public_key"] in bot["snippet"] and "widget.js" in bot["snippet"]
    r = await client.patch(
        f"/api/chatbots/{bot['id']}",
        json={
            "appearance": {"primary_color": "#123456", "position": "left", "launcher_label": "Help", "theme": "dark"},
            "language": "de",
            "tone": "concise",
            "welcome_message": "Hallo!",
            "instructions": "Never promise delivery dates.",
            "fallback_message": "Bitte später erneut versuchen.",
            "model": "claude-sonnet-5-5",
        },
    )
    assert r.status_code == 200 and r.json()["language"] == "de" and r.json()["model"] == "claude-sonnet-5-5"
    for bad in ({"allowed_domains": ["not a domain"]}, {"model": "gpt-9"}, {"language": "xx"}, {"appearance": {"primary_color": "red"}}):
        assert (await client.patch(f"/api/chatbots/{bot['id']}", json=bad)).status_code == 422
    r = await client.patch(f"/api/chatbots/{bot['id']}", json={"allowed_domains": ["https://www.Example.com/path"]})
    assert r.json()["allowed_domains"] == ["example.com"]


async def test_chatbot_domain_limit_across_chatbots(client):
    bot = await setup_bot(client)  # starter: 1 chatbot domain, already used
    r = await client.patch(f"/api/chatbots/{bot['id']}", json={"allowed_domains": ["example.com", "example.org"]})
    assert r.status_code == 402 and r.json()["detail"]["limit"] == "chatbot_domains"
    other = (await client.post("/api/chatbots", json={"name": "Second"})).json()
    r = await client.patch(f"/api/chatbots/{other['id']}", json={"allowed_domains": ["example.org"]})
    assert r.status_code == 402
    await set_limits(client.headers["X-Workspace-Id"], chatbot_domains=-1)
    assert (await client.patch(f"/api/chatbots/{other['id']}", json={"allowed_domains": ["example.org"]})).status_code == 200


async def test_widget_only_works_on_authorized_domains(client):
    bot = await setup_bot(client, domains=("example.com", "*.acme.io"))
    key = bot["public_key"]
    async with new_client() as v:
        for origin, ok in [
            ("https://example.com", True),
            ("https://www.example.com", True),
            ("https://shop.example.com", False),
            ("https://acme.io", True),
            ("https://eu.app.acme.io", True),
            ("https://example.com.evil.net", False),
            ("https://evil.net", False),
            ("javascript:alert(1)", False),
        ]:
            r = await v.get(f"{widget(v, key, origin)}/config")
            assert (r.status_code == 200) == ok, (origin, r.text)
            if not ok:
                assert r.json()["detail"]["code"] == "domain_not_allowed"
        v.headers.pop("X-Verity-Parent-Origin")
        assert (await v.get(f"/api/widget/{key}/config")).status_code == 403
    policy = (await client.get(f"/api/widget/{key}/frame-policy")).json()["ancestors"]
    assert "https://example.com" in policy and "https://*.acme.io" in policy and "https://acme.io" in policy


async def test_visitor_chat_streams_cited_answer(client):
    bot = await setup_bot(client)
    async with new_client() as v:
        cfg = (await v.get(f"{widget(v, bot['public_key'])}/config")).json()
        assert cfg["name"] == "Acme Help" and cfg["ai_available"] is True
        base, conv = await start(v, bot["public_key"])
        evs = await say(v, base, conv, "How long do refunds take?")
        kinds = [e for e, _ in evs]
        assert kinds[0] == "message" and "delta" in kinds and kinds[-2] == "message" and kinds[-1] == "end"
        answer = evs[-2][1]
        assert answer["role"] == "assistant" and answer["citations"][0]["title"] == "refunds.txt"
        assert "model" not in answer and "trace" not in answer  # visitors don't see internals
        polled = (await v.get(f"{base}/conversations/{conv}")).json()
        assert [m["role"] for m in polled["messages"]] == ["visitor", "assistant"]
        # Wrong token, or the right token on another bot's key: not found.
        v.headers["X-Visitor-Token"] = "nope"
        assert (await v.get(f"{base}/conversations/{conv}")).status_code == 404
    convs = (await client.get("/api/conversations")).json()
    assert convs["items"][0]["title"] == "How long do refunds take?" and convs["items"][0]["confidence"] in ("high", "medium")
    detail = (await client.get(f"/api/conversations/{conv}")).json()
    assert detail["messages"][1]["trace"][-1]["name"] == "Verify citations"
    async with SessionLocal() as db:
        assert (await db.execute(select(UsageEvent.operation).where(UsageEvent.kind == "llm"))).scalar_one() == "chat_response"


async def test_handoff_silences_ai_until_resolved(client):
    bot = await setup_bot(client)
    async with new_client() as v, new_client() as agent, new_client() as viewer:
        await add_member(client, agent, "agent@example.com", "agent")
        await add_member(client, viewer, "viewer@example.com", "viewer")
        base, conv = await start(v, bot["public_key"])
        evs = await say(v, base, conv, "zebra xylophone quantum")
        assert evs[-2][1]["data"]["offer_escalation"] is True
        r = await v.post(f"{base}/conversations/{conv}/escalate", json={})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "email_required"
        mail.OUTBOX.clear()
        r = await v.post(f"{base}/conversations/{conv}/escalate", json={"email": "Visitor@Example.com", "name": "Vi"})
        assert r.status_code == 200 and r.json()["status"] == "escalated"
        assert {m.to for m in mail.OUTBOX} == {"bot@example.com", "agent@example.com"}  # responders, not the viewer
        async with SessionLocal() as db:
            assert (await db.execute(select(Notification.kind))).scalars().all()[-1] == "conversation_escalated"
            llm_before = len((await db.execute(select(UsageEvent).where(UsageEvent.kind == "llm"))).scalars().all())

        evs = await say(v, base, conv, "Hello? Anyone there?")
        assert [e for e, _ in evs] == ["message", "waiting", "end"]
        async with SessionLocal() as db:
            assert len((await db.execute(select(UsageEvent).where(UsageEvent.kind == "llm"))).scalars().all()) == llm_before

        waiting = (await agent.get("/api/conversations?status=escalated")).json()
        assert waiting["waiting"] == 1 and waiting["items"][0]["visitor_email"] == "visitor@example.com"
        assert (await viewer.post(f"/api/conversations/{conv}/reply", json={"text": "hi"})).status_code == 403
        r = await agent.post(f"/api/conversations/{conv}/reply", json={"text": "Hi, I'm here to help."})
        assert r.status_code == 201
        polled = (await v.get(f"{base}/conversations/{conv}")).json()
        last = polled["messages"][-1]
        assert last["role"] == "agent" and last["author"] == "Member" and last["content"] == "Hi, I'm here to help."

        assert (await agent.post(f"/api/conversations/{conv}/resolve")).json()["status"] == "resolved"
        evs = await say(v, base, conv, "How long do refunds take?")
        assert evs[-2][1]["role"] == "assistant"  # the AI answers again
    actions = [a["action"] for a in (await client.get("/api/workspace/audit-logs")).json()]
    assert {"conversation.escalated", "conversation.replied", "conversation.resolved"} <= set(actions)


async def test_agent_reply_takes_over_an_open_conversation(client):
    bot = await setup_bot(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "How long do refunds take?")
        await client.post(f"/api/conversations/{conv}/reply", json={"text": "Jumping in."})
        evs = await say(v, base, conv, "Thanks!")
        assert [e for e, _ in evs] == ["message", "waiting", "end"]


async def test_fallback_message_when_ai_unavailable(client):
    bot = await setup_bot(client)
    await client.patch(f"/api/chatbots/{bot['id']}", json={"fallback_message": "We're offline, leave a message."})
    async with SessionLocal() as db:
        ledger = await current_ledger(db, client.headers["X-Workspace-Id"])
        ledger.consumed = ledger.allowance
        await db.commit()
    async with new_client() as v:
        cfg = (await v.get(f"{widget(v, bot['public_key'])}/config")).json()
        assert cfg["ai_available"] is False
        base, conv = await start(v, bot["public_key"])
        answer = (await say(v, base, conv, "How long do refunds take?"))[-2][1]
        assert answer["content"] == "We're offline, leave a message." and answer["data"]["offer_escalation"] is True


async def test_paused_bot_and_rate_limit(client, monkeypatch):
    bot = await setup_bot(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        monkeypatch.setattr(get_settings(), "rl_widget_message", "2/60")
        await say(v, base, conv, "one")
        await say(v, base, conv, "two")
        r = await v.post(f"{base}/conversations/{conv}/messages", json={"text": "three"})
        assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
        await client.patch(f"/api/chatbots/{bot['id']}", json={"status": "paused"})
        r = await v.get(f"{base}/config")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "chatbot_unavailable"


async def test_logo(client):
    bot = await setup_bot(client)
    r = await client.post(f"/api/chatbots/{bot['id']}/logo", files={"file": ("logo.svg", b"<svg onload=alert(1)>")})
    assert r.status_code == 422
    r = await client.post(f"/api/chatbots/{bot['id']}/logo", files={"file": ("logo.png", PNG)})
    assert r.status_code == 200 and r.json()["logo_url"]
    img = await client.get(r.json()["logo_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content == PNG


async def test_ask_screen_streams_and_is_private_to_its_author(client):
    await setup_bot(client)
    r = await client.post("/api/ask", json={"text": "How long do refunds take?"})
    evs = events(r.text)
    conv_id = evs[0][1]["id"]
    answer = [d for e, d in evs if e == "message"][-1]
    assert answer["role"] == "assistant" and answer["citations"] and answer["trace"]
    async with SessionLocal() as db:
        assert (await db.execute(select(UsageEvent.operation).where(UsageEvent.kind == "llm"))).scalar_one() == "kb_ask"
    r = await client.post("/api/ask", json={"text": "And shipping?", "conversation_id": conv_id})
    assert r.status_code == 200
    assert len((await client.get("/api/ask/conversations")).json()) == 1
    assert (await client.get("/api/conversations")).json()["items"] == []  # not a customer conversation
    async with new_client() as admin:
        await add_member(client, admin, "admin@example.com", "admin")
        assert (await admin.get(f"/api/conversations/{conv_id}")).status_code == 404
        assert (await admin.post("/api/ask", json={"text": "x", "conversation_id": conv_id})).status_code == 404


async def test_tenant_isolation(client):
    bot = await setup_bot(client, "a@example.com")
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "How long do refunds take?")
    async with new_client() as other:
        other_bot = await setup_bot(other, "b@example.com", domains=("example.com",))
        assert [b["id"] for b in (await other.get("/api/chatbots")).json()["chatbots"]] == [other_bot["id"]]
        assert (await other.get(f"/api/chatbots/{bot['id']}")).status_code == 404
        assert (await other.patch(f"/api/chatbots/{bot['id']}", json={"name": "x"})).status_code == 404
        assert (await other.delete(f"/api/chatbots/{bot['id']}")).status_code == 404
        assert (await other.get("/api/conversations")).json()["items"] == []
        assert (await other.get(f"/api/conversations/{conv}")).status_code == 404
        assert (await other.post(f"/api/conversations/{conv}/reply", json={"text": "x"})).status_code == 404
        assert (await other.post(f"/api/conversations/{conv}/resolve")).status_code == 404
        # A visitor of B's chatbot can't reach A's conversation through B's key.
        async with new_client() as v2:
            v2.headers["X-Visitor-Token"] = "x"
            assert (await v2.get(f"{widget(v2, other_bot['public_key'])}/conversations/{conv}")).status_code == 404
