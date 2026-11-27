"""Phase 6 summaries: a ticket on demand and a chat when it is handed to a person, metered as "summary",
never shown to visitors, skipped or refused cleanly without tokens, every role and tenant isolation."""

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import Conversation, TokenLedger, UsageEvent
from tests.conftest import new_client, signup, subscribe
from tests.test_phase3_access import team


async def setup(client):
    await signup(client, "sum@example.com")
    await subscribe(client, "growth")
    t = (await client.post("/api/tickets", json={"subject": "Refund for order 4411", "description": "The parcel arrived broken."})).json()
    await client.post(f"/api/tickets/{t['id']}/notes", json={"text": "Asked the warehouse for photos."})
    return t


async def test_ticket_summary_is_metered_and_kept(client):
    t = await setup(client)
    assert (await client.get(f"/api/tickets/{t['id']}")).json()["summary"] is None
    r = await client.post(f"/api/tickets/{t['id']}/summary")
    assert r.status_code == 200, r.text
    assert r.json()["text"].startswith(f"Ticket #{t['number']}: Refund for order 4411") and r.json()["at"]
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert detail["summary"]["text"] == r.json()["text"]
    async with SessionLocal() as db:
        ops = (await db.execute(select(UsageEvent.operation))).scalars().all()
    assert ops == ["summary"]


async def test_without_tokens_the_summary_says_so(client):
    t = await setup(client)
    async with SessionLocal() as db:
        await db.execute(update(TokenLedger).values(consumed=TokenLedger.allowance))
        await db.commit()
    r = await client.post(f"/api/tickets/{t['id']}/summary")
    assert r.status_code == 402 and r.json()["detail"]["code"] == "ai_unavailable"
    assert (await client.get(f"/api/tickets/{t['id']}")).status_code == 200  # the ticket itself is fine


async def test_summaries_follow_ticket_access(client):
    t = await setup(client)
    clients = await team(client)
    for role, c in clients.items():
        r = await c.post(f"/api/tickets/{t['id']}/summary")
        assert r.status_code in (200, 429), (role, r.text)  # everyone who reads tickets (custom role included)
    async with new_client() as other:
        other.headers["X-Forwarded-For"] = "198.51.100.9"  # a sixth sign-up needs its own address
        await signup(other, "else@example.com")
        await subscribe(other)
        assert (await other.post(f"/api/tickets/{t['id']}/summary")).status_code == 404
    for c in clients.values():
        if c is not client:
            await c.aclose()


async def test_a_handoff_gets_a_summary_for_the_agent_only(client):
    from tests.test_chatbots import say, setup_bot, start

    bot = await setup_bot(client, "chat@example.com")
    await client.patch(f"/api/chatbots/{bot['id']}", json={"escalation": {"enabled": True, "ask_email": False, "ticket_fallback": False}})
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "How long do refunds take?")
        r = await v.post(f"{base}/conversations/{conv}/escalate", json={})
        assert r.status_code == 200, r.text
        public = await v.get(f"{base}/conversations/{conv}")
        assert "summary" not in public.text  # visitors never see it
    async with SessionLocal() as db:
        c = (await db.execute(select(Conversation))).scalar_one()
        ops = (await db.execute(select(UsageEvent.operation))).scalars().all()
    assert c.summary and "messages so far" in c.summary and "summary" in ops
    detail = (await client.get(f"/api/conversations/{conv}")).json()
    assert detail["conversation"]["summary"] == c.summary
