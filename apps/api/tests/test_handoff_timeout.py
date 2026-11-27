"""When a visitor asks for a person and nobody replies in time, the chatbot offers a form that opens a ticket."""

import asyncio
import time
from datetime import timedelta

from sqlalchemy import func, select

from app import jobs
from app.core.db import SessionLocal
from app.jobs import enqueue, run_delayed
from app.jobs.tasks import sweep_handoffs
from app.models import Conversation, Message, UsageEvent
from app.services.quota import current_ledger
from tests.conftest import new_client
from tests.phase3 import category, form
from tests.test_chatbots import say, setup_bot, start
from tests.test_jobs import real_queue, record  # noqa: F401  (fixture)
from tests.test_workspaces import add_member


def forms_in(messages):
    return [m for m in messages if m["kind"] == "form"]


async def test_no_reply_offers_the_contact_form_and_the_ai_takes_the_chat_back(client):
    bot = await setup_bot(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra xylophone quantum")
        # No email needed up front: the form asks for it if nobody answers.
        r = await v.post(f"{base}/conversations/{conv}/escalate", json={})
        assert r.status_code == 200 and r.json()["status"] == "escalated"
        assert [f for f, _ in jobs.DELAYED] == ["handoff_check"]
        assert await run_delayed() == 1
        polled = (await v.get(f"{base}/conversations/{conv}")).json()
        assert polled["status"] == "escalated"
        offer = forms_in(polled["messages"])[-1]
        assert offer["data"]["reason"] == "handoff_timeout" and offer["data"]["form"]["title"] == "Contact the team"
        assert next(x for x in offer["data"]["form"]["fields"] if x["key"] == "email")["required"] is True

        url = f"{base}/conversations/{conv}/forms/{offer['id']}/submit"
        r = await v.post(url, json={"values": {"message": "Please call me back"}, "confirmed": True})
        assert r.json()["detail"]["errors"] == {"email": "This field is required."}
        r = await v.post(
            url, json={"values": {"email": "v@example.com", "message": "Please call me back", "name": "Vi"}, "confirmed": True}
        )
        assert r.status_code == 200 and r.json()["status"] == "open" and r.json()["ticket_number"] == 1
        assert r.json()["messages"][-1]["data"]["notice"] == "ticket_created"
        # The assistant answers again.
        evs = await say(v, base, conv, "How long do refunds take?")
        assert evs[-2][1]["role"] == "assistant"
    t = (await client.get("/api/tickets")).json()["items"][0]
    assert t["category"] == "General" and t["requester_email"] == "v@example.com" and t["requester_name"] == "Vi"
    assert (await client.get("/api/conversations?status=escalated")).json()["waiting"] == 0


async def test_a_workspace_form_that_fits_is_offered_instead(client):
    bot = await setup_bot(client)
    cat = await category(client, "Sales")
    lead = await form(
        client,
        cat["id"],
        name="Contact me later",
        activation_condition="The visitor seems interested in the product and would like to be contacted later.",
        fields=[
            {"key": "email", "label": "Email", "type": "email", "required": True},
            {"key": "best_time", "label": "Best time to call", "type": "select", "options": ["Morning", "Afternoon"]},
        ],
        chatbot_ids=[bot["id"]],
    )
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        evs = await say(v, base, conv, "I'm interested in the product, please contact me in the afternoon")
        first = next(d for e, d in evs if e == "message" and d["kind"] == "form")
        assert first["data"]["form"]["title"] == "Contact me later"
        await v.post(f"{base}/conversations/{conv}/forms/{first['id']}/dismiss")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        await run_delayed()
        offer = forms_in((await v.get(f"{base}/conversations/{conv}")).json()["messages"])[-1]
        assert offer["data"]["reason"] == "handoff_timeout" and offer["data"]["form"]["form_id"] == lead["id"]
        prefill = (await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/prefill")).json()
        assert prefill["values"] == {"best_time": "Afternoon"}


async def test_an_agent_reply_in_time_means_no_form(client):
    bot = await setup_bot(client)
    async with new_client() as v, new_client() as agent:
        await add_member(client, agent, "agent@example.com", "agent")
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        await agent.post(f"/api/conversations/{conv}/reply", json={"text": "I'm here."})
        await run_delayed()
        assert forms_in((await v.get(f"{base}/conversations/{conv}")).json()["messages"]) == []


async def test_stale_checks_and_disabled_fallback_do_nothing(client):
    bot = await setup_bot(client)
    async with new_client() as v, new_client() as agent:
        await add_member(client, agent, "agent@example.com", "agent")
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        stale = list(jobs.DELAYED)
        jobs.DELAYED.clear()
        await agent.post(f"/api/conversations/{conv}/resolve")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        jobs.DELAYED[:0] = stale  # the first escalation's check fires late, then the current one
        await run_delayed()
        offers = forms_in((await v.get(f"{base}/conversations/{conv}")).json()["messages"])
        assert len(offers) == 1  # only the current escalation gets an offer

        await client.patch(f"/api/chatbots/{bot['id']}", json={"escalation": {"enabled": True, "ticket_fallback": False}})
        base2, conv2 = await start(v, bot["public_key"])
        await v.post(f"{base2}/conversations/{conv2}/escalate", json={"email": "v@example.com"})
        assert jobs.DELAYED == []


async def test_sweeper_catches_lost_checks_once(client):
    bot = await setup_bot(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        jobs.DELAYED.clear()  # the worker was down: the scheduled check is lost
    async with SessionLocal() as db:
        c = await db.get(Conversation, conv)
        c.escalated_at = c.escalated_at - timedelta(minutes=5)
        await db.commit()
    assert await sweep_handoffs({}) == 1
    assert await sweep_handoffs({}) == 0
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count()).where(Message.conversation_id == conv, Message.kind == "form"))).scalar_one() == 1


async def test_without_ai_the_contact_form_is_offered_and_no_model_is_called(client):
    bot = await setup_bot(client)
    cat = await category(client)
    await form(client, cat["id"], chatbot_ids=[bot["id"]])
    async with SessionLocal() as db:
        ledger = await current_ledger(db, client.headers["X-Workspace-Id"])
        ledger.consumed = ledger.allowance
        await db.commit()
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "I want to return order 12345")
        await v.post(f"{base}/conversations/{conv}/escalate", json={"reason": "ai_unavailable"})
        await run_delayed()
        offer = forms_in((await v.get(f"{base}/conversations/{conv}")).json()["messages"])[-1]
        assert offer["data"]["form"]["title"] == "Contact the team"
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count()).where(UsageEvent.operation == "form_activation"))).scalar_one() == 0


async def test_handoff_check_runs_on_a_real_worker_at_its_time(real_queue, client):  # noqa: F811
    from saq import Worker

    from app.jobs.tasks import handoff_check

    bot = await setup_bot(client)
    await client.patch(f"/api/chatbots/{bot['id']}", json={"escalation": {"enabled": True, "ticket_fallback": True, "wait_seconds": 10}})
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        job = [j for j in (await real_queue.info(jobs=True))["jobs"] if j["function"] == "handoff_check"]
        assert len(job) == 1 and int(job[0]["scheduled"]) >= int(time.time()) + 8
        # Bring it forward instead of waiting ten seconds.
        async with SessionLocal() as db:
            c = await db.get(Conversation, conv)
            escalated_at = c.escalated_at.isoformat()
        await enqueue("handoff_check", key="now", conversation_id=conv, escalated_at=escalated_at)
        worker = Worker(real_queue, functions=[handoff_check, record], concurrency=1, dequeue_timeout=0.1)
        task = asyncio.create_task(worker.start())
        try:
            for _ in range(100):
                if forms_in((await v.get(f"{base}/conversations/{conv}")).json()["messages"]):
                    break
                await asyncio.sleep(0.1)
        finally:
            await worker.stop()
            task.cancel()
        assert len(forms_in((await v.get(f"{base}/conversations/{conv}")).json()["messages"])) == 1


async def email_left(v, base, conv, email="left@example.com"):
    """The visitor leaves their email while waiting (the widget's optional box re-sends escalate with it)."""
    r = await v.post(f"{base}/conversations/{conv}/escalate", json={"email": email})
    assert r.status_code == 200 and r.json()["status"] == "escalated"


async def test_visitor_who_left_an_email_gets_a_ticket_without_filling_anything(client):
    bot = await setup_bot(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra xylophone quantum")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        await email_left(v, base, conv)
        assert len(jobs.DELAYED) == 1  # leaving the email doesn't schedule a second check
        await run_delayed()
        polled = (await v.get(f"{base}/conversations/{conv}")).json()
        assert polled["status"] == "open" and polled["has_open_ticket"] is True
        assert forms_in(polled["messages"]) == []
        notice = polled["messages"][-1]
        assert notice["data"]["notice"] == "ticket_created_auto" and notice["data"]["email"] == "left@example.com"
        assert "#1" in notice["content"] and "left@example.com" in notice["content"]
    t = (await client.get("/api/tickets")).json()["items"][0]
    assert t["source"] == "conversation" and t["requester_email"] == "left@example.com" and t["category"] == "General"
    assert "nobody replied within 120 seconds" in t["description"]
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert detail["conversation"]["id"] == conv
    events = [m["data"] for m in detail["messages"] if m["kind"] == "event"]
    assert events[-1]["event"] == "ticket_created" and events[-1]["reason"] == "handoff_timeout"


async def test_the_ticket_goes_to_the_category_of_the_form_that_fits(client):
    bot = await setup_bot(client)
    cat = await category(client, "Returns")
    await form(client, cat["id"], chatbot_ids=[bot["id"]])
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        await say(v, base, conv, "I want to return order 12345")  # waits for the team: no AI answer, no offer
        await email_left(v, base, conv)
        await run_delayed()
    t = (await client.get("/api/tickets")).json()["items"][0]
    assert t["category"] == "Returns" and t["requester_email"] == "left@example.com"


async def test_with_an_email_an_agent_reply_in_time_still_means_nothing_happens(client):
    bot = await setup_bot(client)
    async with new_client() as v, new_client() as agent:
        await add_member(client, agent, "agent@example.com", "agent")
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra")
        await v.post(f"{base}/conversations/{conv}/escalate", json={"email": "left@example.com"})
        await agent.post(f"/api/conversations/{conv}/reply", json={"text": "I'm here."})
        await run_delayed()
        assert (await v.get(f"{base}/conversations/{conv}")).json()["status"] == "escalated"
    assert (await client.get("/api/tickets")).json()["items"] == []


async def test_job_and_sweeper_together_open_one_ticket(client):
    bot = await setup_bot(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra")
        await v.post(f"{base}/conversations/{conv}/escalate", json={"email": "left@example.com"})
    async with SessionLocal() as db:
        c = await db.get(Conversation, conv)
        c.escalated_at = c.escalated_at - timedelta(minutes=5)
        await db.commit()
        escalated_at = c.escalated_at
    from app.jobs.tasks import handoff_check

    jobs.DELAYED.clear()
    results = await asyncio.gather(sweep_handoffs({}), handoff_check({}, conversation_id=conv, escalated_at=escalated_at.isoformat()))
    # Exactly one of them opens the ticket; the other finds the conversation no longer waiting.
    assert list(results) in ([1, "not_waiting"], [0, "ticket"])
    assert len((await client.get("/api/tickets")).json()["items"]) == 1
