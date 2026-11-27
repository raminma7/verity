"""Chatbot forms in the widget: AI activation, prefill, confirmation and the tickets they open."""

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Ticket, TokenReservation, UsageEvent
from app.services import email as mail
from app.services.quota import current_ledger
from tests.conftest import new_client
from tests.phase3 import VALID_RETURN, category, form
from tests.test_chatbots import say, setup_bot, start, widget
from tests.test_workspaces import add_member


async def bot_with_return_form(client, **form_over):
    bot = await setup_bot(client)
    cat = await category(client)
    f = await form(client, cat["id"], chatbot_ids=[bot["id"]], **form_over)
    return bot, f


async def ticket_count() -> int:
    async with SessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(Ticket))).scalar_one()


def form_message(evs):
    return next(d for e, d in evs if e == "message" and d["kind"] == "form")


async def test_activation_prefill_confirm_and_ticket(client):
    bot, f = await bot_with_return_form(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        evs = await say(v, base, conv, "Hi, I want to return order 12345, I got 2 pairs. My email is jane@example.com")
        offer = form_message(evs)
        assert [e for e, _ in evs][-1] == "end"
        assert offer["role"] == "assistant" and offer["data"]["state"] == "offered" and offer["data"]["reason"] == "activation"
        assert offer["data"]["form"]["title"] == "Return request" and len(offer["data"]["form"]["fields"]) == 8
        assert "category_id" not in offer["data"]["form"] and "prefill" not in offer["data"]

        # Prefill suggests values; nothing is submitted.
        r = await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/prefill")
        assert r.status_code == 200 and r.json()["ai"] is True
        values = r.json()["values"]
        assert values["order_number"] == "12345" and values["email"] == "jane@example.com" and values["quantity"] == 2
        assert "consent" not in values and "reason" not in values
        assert await ticket_count() == 0
        # Asking again doesn't call the model again.
        async with SessionLocal() as db:
            prefills = (await db.execute(select(func.count()).where(UsageEvent.operation == "form_prefill"))).scalar_one()
        await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/prefill")
        async with SessionLocal() as db:
            assert (await db.execute(select(func.count()).where(UsageEvent.operation == "form_prefill"))).scalar_one() == prefills == 1
            ops = set((await db.execute(select(UsageEvent.operation).where(UsageEvent.kind == "llm"))).scalars())
            assert {"form_activation", "form_prefill"} <= ops

        # Nothing goes without the customer's confirmation, and the server checks every field.
        url = f"{base}/conversations/{conv}/forms/{offer['id']}/submit"
        r = await v.post(url, json={"values": VALID_RETURN})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "confirmation_required"
        r = await v.post(url, json={"values": {**values}, "confirmed": True})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_form"
        assert set(r.json()["detail"]["errors"]) == {"reason", "consent"}
        assert await ticket_count() == 0

        mail.OUTBOX.clear()
        r = await v.post(url, json={"values": {**values, "reason": "Damaged", "consent": True}, "confirmed": True})
        assert r.status_code == 200 and r.json()["ticket_number"] == 1
        assert r.json()["status"] == "open"
        notice = r.json()["messages"][-1]
        assert notice["role"] == "assistant" and notice["data"]["notice"] == "ticket_created" and "#1" in notice["content"]
        assert any(m.to == "bot@example.com" and "ticket #1" in m.subject for m in mail.OUTBOX)
        # Submitting again (a double click) returns the same ticket.
        r = await v.post(url, json={"values": {**values, "reason": "Damaged", "consent": True}, "confirmed": True})
        assert r.json()["ticket_number"] == 1 and await ticket_count() == 1

        # The AI keeps the chat; the customer's next message lands on the ticket, and the form isn't offered again.
        evs = await say(v, base, conv, "Thanks! Can I return the other order too?")
        assert evs[-2][1]["role"] == "assistant" and all(d.get("kind") != "form" for e, d in evs if e == "message")

    tickets = (await client.get("/api/tickets")).json()["items"]
    t = tickets[0]
    assert t["source"] == "chatbot_form" and t["category"] == "Returns" and t["requester_email"] == "jane@example.com"
    assert t["subject"].startswith("Return request")
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    by_key = {x["key"]: x for x in detail["form"]["fields"]}
    assert by_key["order_number"]["value"] == "12345" and by_key["reason"]["value"] == "Damaged" and by_key["consent"]["value"] is True
    assert detail["form"]["name"] == "Return request" and detail["form"]["version"] == 1
    assert detail["conversation"]["id"] == conv and any(m["kind"] == "form" for m in detail["messages"])
    assert [e["kind"] for e in detail["events"]] == ["created", "customer_message"]
    assert detail["can_reply"] is True
    # Editing the form later doesn't change what this ticket shows.
    await client.patch(f"/api/forms/{f['id']}", json={"fields": [{"key": "other", "label": "Other", "type": "text"}]})
    again = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert [x["label"] for x in again["form"]["fields"]][0] == "Order number"


async def test_visitor_can_choose_and_dismiss_forms(client):
    bot, f = await bot_with_return_form(client, activation_condition="")
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        listed = (await v.get(f"{base}/forms")).json()
        assert [x["title"] for x in listed] == ["Return request", "Contact the team"]
        r = await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": f["id"]})
        assert r.status_code == 201 and r.json()["data"]["reason"] == "visitor_choice"
        same = await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": f["id"]})
        assert same.json()["id"] == r.json()["id"]
        d = await v.post(f"{base}/conversations/{conv}/forms/{r.json()['id']}/dismiss")
        assert d.json()["data"]["state"] == "dismissed"
        closed = await v.post(
            f"{base}/conversations/{conv}/forms/{r.json()['id']}/submit", json={"values": VALID_RETURN, "confirmed": True}
        )
        assert closed.status_code == 409 and closed.json()["detail"]["code"] == "form_not_offered"
        # A form the chatbot doesn't offer can't be opened, and a form message from another chat can't be used.
        other = await form(client, (await category(client, "Other"))["id"], name="Secret")
        assert (await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": other["id"]})).status_code == 404
        base2, conv2 = await start(v, bot["public_key"])
        r = await v.post(f"{base2}/conversations/{conv2}/forms/{d.json()['id']}/prefill")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "form_not_offered"


async def test_no_activation_without_condition_or_while_people_have_the_chat(client):
    bot, f = await bot_with_return_form(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        evs = await say(v, base, conv, "I want to return order 12345")
        assert [e for e, _ in evs] == ["message", "waiting", "end"]
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count()).where(UsageEvent.operation == "form_activation"))).scalar_one() == 0


async def test_forms_work_without_ai(client):
    bot, f = await bot_with_return_form(client)
    async with SessionLocal() as db:
        ledger = await current_ledger(db, client.headers["X-Workspace-Id"])
        ledger.consumed = ledger.allowance
        await db.commit()
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        evs = await say(v, base, conv, "I want to return order 12345")
        assert all(d.get("kind") != "form" for e, d in evs if e == "message")  # no AI, no automatic offer
        await v.post(f"{base}/conversations/{conv}/escalate", json={"email": "known@example.com"})
        offer = (await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": f["id"]})).json()
        r = await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/prefill")
        assert r.json() == {"values": {"email": "known@example.com"}, "ai": False}
        r = await v.post(
            f"{base}/conversations/{conv}/forms/{offer['id']}/submit",
            json={"values": {**VALID_RETURN, "email": "known@example.com"}, "confirmed": True},
        )
        assert r.status_code == 200 and r.json()["ticket_number"] == 1
    async with SessionLocal() as db:
        assert (
            await db.execute(select(func.count()).where(UsageEvent.operation.in_(("form_activation", "form_prefill"))))
        ).scalar_one() == 0


async def test_failed_prefill_releases_the_hold(client):
    bot, f = await bot_with_return_form(client, activation_condition="")
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "[[fail]] order 12345")
        offer = (await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": f["id"]})).json()
        r = await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/prefill")
        assert r.status_code == 200 and r.json()["ai"] is False
    async with SessionLocal() as db:
        statuses = set((await db.execute(select(TokenReservation.status).where(TokenReservation.operation == "form_prefill"))).scalars())
        assert statuses == {"released"}
        assert (await current_ledger(db, client.headers["X-Workspace-Id"])).reserved == 0


async def test_agent_turns_a_conversation_into_a_ticket_and_replies_in_the_chat(client):
    bot = await setup_bot(client)
    cat = await category(client, "Billing")
    async with new_client() as v, new_client() as agent, new_client() as viewer:
        await add_member(client, agent, "agent@example.com", "agent")
        await add_member(client, viewer, "viewer@example.com", "viewer")
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "My invoice is wrong")
        await v.post(f"{base}/conversations/{conv}/escalate", json={})
        assert (await viewer.post(f"/api/conversations/{conv}/ticket", json={})).status_code == 403
        r = await agent.post(f"/api/conversations/{conv}/ticket", json={"category_id": cat["id"], "priority": "high"})
        assert r.status_code == 201 and r.json()["number"] == 1
        t_id = r.json()["id"]
        detail = (await agent.get(f"/api/conversations/{conv}")).json()
        assert detail["conversation"]["status"] == "escalated" and detail["tickets"][0]["number"] == 1
        listed = (await agent.get("/api/conversations")).json()["items"][0]
        assert listed["ticket_number"] == 1

        # Replying on the ticket answers in the chat, takes the ticket, and moves it to open.
        r = await agent.post(f"/api/tickets/{t_id}/reply", json={"text": "I've corrected the invoice."})
        assert r.status_code == 201 and r.json()["ticket"]["status"] == "open" and r.json()["ticket"]["assignee"] == "Member Person"
        polled = (await v.get(f"{base}/conversations/{conv}")).json()
        assert polled["messages"][-1]["content"] == "I've corrected the invoice." and polled["has_open_ticket"] is True
        # The customer answers in the chat: it reaches the ticket.
        await say(v, base, conv, "Thank you!")
        kinds = [e["kind"] for e in (await agent.get(f"/api/tickets/{t_id}")).json()["events"]]
        assert kinds[-1] == "customer_message" and "agent_reply" in kinds
        # Resolving the ticket hands the chat back to the assistant.
        await agent.patch(f"/api/tickets/{t_id}", json={"status": "resolved"})
        assert (await agent.get(f"/api/conversations/{conv}")).json()["conversation"]["status"] == "resolved"
        evs = await say(v, base, conv, "How long do refunds take?")
        assert evs[-2][1]["role"] == "assistant"
        # That message reopened the resolved ticket (reopen_on_reply is on by default).
        assert (await agent.get(f"/api/tickets/{t_id}")).json()["ticket"]["status"] == "open"


async def test_widget_never_sees_private_data(client):
    bot, f = await bot_with_return_form(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        evs = await say(v, base, conv, "I want to return order 12345")
        offer = form_message(evs)
        await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/prefill")
        await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/submit", json={"values": {**VALID_RETURN}, "confirmed": True})
        t = (await client.get("/api/tickets")).json()["items"][0]
        await client.post(f"/api/tickets/{t['id']}/notes", json={"text": "Internal: refund approved"})
        polled = (await v.get(f"{base}/conversations/{conv}")).json()
        blob = str(polled)
        assert "Internal: refund approved" not in blob and "prefill" not in blob and t["id"] not in blob
        # A visitor of another site can't reach the conversation's forms.
        v.headers["X-Verity-Parent-Origin"] = "https://evil.example.net"
        assert (await v.get(f"{widget(v, bot['public_key'], 'https://evil.example.net')}/forms")).status_code == 403
