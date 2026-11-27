"""Mail Verity sends: agent replies in the thread, chat replies by email, confirmations and their replies."""

from sqlalchemy import select

from app.core.db import SessionLocal
from app.jobs import run_delayed
from app.models import EmailMessage
from app.services import email as mail_svc
from app.services.mail import transport as tr
from app.services.mail.outbound import PLATFORM_NOTE
from app.services.mail.transport import FakeTransport, TransportError
from tests.conftest import new_client
from tests.mail import CUSTOMER, SUPPORT, connect, deliver, mail, owner, sent, text_of, tickets
from tests.phase3 import VALID_RETURN, category, form
from tests.test_chatbots import say, setup_bot, start


async def email_ticket(client):
    await owner(client)
    a = await connect(client)
    raw, mid = mail()
    await deliver(client, a["id"], raw)
    [t] = await tickets(client)
    return a, t, mid


async def test_agent_reply_on_an_email_ticket_goes_out_in_the_original_thread(client):
    a, t, mid = await email_ticket(client)
    [auto] = await sent()
    r = await client.post(f"/api/tickets/{t['id']}/reply", json={"text": "We found your parcel; it arrives tomorrow."})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["ticket"]["status"] == "open" and d["ticket"]["assignee_id"] is not None
    reply_event = [e for e in d["events"] if e["kind"] == "agent_reply"][-1]
    assert reply_event["data"]["channel"] == "email" and reply_event["data"]["email_message_id"]
    out = d["emails"][-1]
    assert out["kind"] == "agent_reply" and out["status"] == "sent" and out["event_id"] == reply_event["id"]
    _, agent = await sent()
    assert agent["In-Reply-To"] == auto["Message-ID"] and mid in agent["References"] and auto["Message-ID"] in agent["References"]
    assert agent["Subject"] == f"Re: My order hasn't arrived [#{t['number']}]" and not agent["Auto-Submitted"]
    assert "We found your parcel" in text_of(agent)
    # The customer answers the agent: it lands on the same ticket.
    await deliver(client, a["id"], mail(f"Re: {agent['Subject']}", "Great, thanks!", in_reply_to=agent["Message-ID"])[0])
    d = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert [e["body"] for e in d["events"] if e["kind"] == "customer_message"] == ["Great, thanks!"]


async def test_a_failed_send_shows_and_can_be_retried(client, monkeypatch):
    _, t, _ = await email_ticket(client)
    real = tr.get_transport()

    class Refusing(FakeTransport):
        async def send(self, acct, msg):
            raise TransportError("email_send_failed", "The mail server refused the message (554).")

    tr.set_transport(Refusing())
    try:
        d = (await client.post(f"/api/tickets/{t['id']}/reply", json={"text": "Hello"})).json()
    finally:
        tr.set_transport(real)
    out = d["emails"][-1]
    assert out["status"] == "failed" and "554" in out["error"]
    d = (await client.post(f"/api/tickets/{t['id']}/emails/{out['id']}/retry")).json()
    assert d["emails"][-1]["status"] == "sent"


async def test_with_the_mailbox_disconnected_replies_use_the_platform_sender_and_still_thread(client):
    a, t, mid = await email_ticket(client)
    await client.delete(f"/api/email-accounts/{a['id']}")
    mail_svc.OUTBOX.clear()
    d = (await client.post(f"/api/tickets/{t['id']}/reply", json={"text": "Following up."})).json()
    assert d["emails"][-1]["via_platform"] is True and d["emails"][-1]["status"] == "sent"
    [m] = [m for m in mail_svc.OUTBOX if m.to == CUSTOMER]
    assert m.headers["in-reply-to"] and mid in m.headers["references"] and PLATFORM_NOTE in m.text


async def test_tickets_without_chat_or_email_still_refuse_replies(client):
    await owner(client)
    t = (await client.post("/api/tickets", json={"subject": "Internal"})).json()
    r = await client.post(f"/api/tickets/{t['id']}/reply", json={"text": "x"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "ticket_reply_unavailable"


# ---- Chat conversations by email ----------------------------------------------------------------------


async def chat_with_email(client, email="visitor@example.org"):
    bot = await setup_bot(client)
    a = await connect(client)
    v = new_client()
    base, conv = await start(v, bot["public_key"])
    await say(v, base, conv, "zebra xylophone quantum")
    r = await v.post(f"{base}/conversations/{conv}/escalate", json={"email": email})
    assert r.status_code == 200
    return bot, a, v, base, conv


async def test_agent_chat_replies_reach_the_visitor_by_email_and_their_email_reply_comes_back(client):
    _, a, v, base, conv = await chat_with_email(client)
    try:
        r = await client.post(f"/api/conversations/{conv}/reply", json={"text": "Hi! I can help with that."})
        assert r.status_code == 201 and r.json()["data"]["emailed_to"] == "visitor@example.org"
        [out] = await sent()
        assert out["To"] == "visitor@example.org" and "Hi! I can help with that." in text_of(out)
        reply, _ = mail(f"Re: {out['Subject']}", "Thanks, it's order 55555.", sender="visitor@example.org", in_reply_to=out["Message-ID"])
        await deliver(client, a["id"], reply)
        msgs = (await v.get(f"{base}/conversations/{conv}")).json()["messages"]
        mine = [m for m in msgs if m["role"] == "visitor"]
        assert mine[-1]["content"] == "Thanks, it's order 55555."
        assert (await client.get(f"/api/conversations/{conv}")).json()["conversation"]["status"] == "escalated"
        assert await tickets(client) == []  # it's the conversation's reply, not a new email ticket
    finally:
        await v.aclose()


async def test_the_auto_ticket_promise_comes_true_end_to_end(client):
    """Nobody replies in time → ticket + confirmation; the agent answers from the ticket; the reply comes back."""
    _, a, v, base, conv = await chat_with_email(client, "left@example.com")
    try:
        await run_delayed()
        [confirm] = await sent()
        [t] = (await client.get("/api/tickets")).json()["items"]
        assert (
            confirm["To"] == "left@example.com" and f"#{t['number']}" in text_of(confirm) and confirm["Auto-Submitted"] == "auto-generated"
        )
        detail = (await client.get(f"/api/tickets/{t['id']}")).json()
        assert detail["reply_channel"]["kind"] == "chat" and detail["reply_channel"]["email"] == "left@example.com"
        d = (await client.post(f"/api/tickets/{t['id']}/reply", json={"text": "Sorry for the wait, here is the answer."})).json()
        _, answer = await sent()
        assert answer["To"] == "left@example.com" and answer["In-Reply-To"] == confirm["Message-ID"]
        assert f"[#{t['number']}]" in answer["Subject"] and "Sorry for the wait" in text_of(answer)
        await client.patch(f"/api/tickets/{t['id']}", json={"status": "resolved"})
        back, _ = mail("Re: x", "It works now, but one more question.", sender="left@example.com", in_reply_to=answer["Message-ID"])
        await deliver(client, a["id"], back)
        d = (await client.get(f"/api/tickets/{t['id']}")).json()
        assert d["ticket"]["status"] == "open" and d["ticket"]["reopened_count"] == 1
        assert [e["body"] for e in d["events"] if e["kind"] == "customer_message"][-1] == "It works now, but one more question."
        assert d["messages"][-1]["content"] == "It works now, but one more question." or any(
            m["content"] == "It works now, but one more question." and m["role"] == "visitor" for m in d["messages"]
        )
        # One ticket only: the email reply didn't start another, and no second handoff ticket was scheduled.
        await run_delayed()
        assert len((await client.get("/api/tickets")).json()["items"]) == 1
    finally:
        await v.aclose()


async def test_form_submissions_get_a_confirmation_and_replies_land_on_the_ticket(client):
    bot = await setup_bot(client)
    a = await connect(client)
    cat = await category(client)
    f = await form(client, cat["id"], chatbot_ids=[bot["id"]], activation_condition="")
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        offer = (await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": f["id"]})).json()
        r = await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/submit", json={"values": VALID_RETURN, "confirmed": True})
        assert r.status_code == 200, r.text
        number = r.json()["ticket_number"]
    [confirm] = await sent()
    body = text_of(confirm)
    assert confirm["To"] == "jane@example.com" and f"#{number}" in body and "Order number: 12345" in body and "agree" not in body
    t = (await client.get("/api/tickets")).json()["items"][0]
    await deliver(
        client, a["id"], mail("Re: x", "I forgot: it was two pairs.", sender="jane@example.com", in_reply_to=confirm["Message-ID"])[0]
    )
    d = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert [e["body"] for e in d["events"] if e["kind"] == "customer_message"] == ["I forgot: it was two pairs."]
    # Turned off, no confirmation goes out.
    await client.patch("/api/email-settings", json={"form_confirmation": False})
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        offer = (await v.post(f"{base}/conversations/{conv}/forms", json={"form_id": f["id"]})).json()
        await v.post(f"{base}/conversations/{conv}/forms/{offer['id']}/submit", json={"values": VALID_RETURN, "confirmed": True})
    assert len(await sent()) == 1


async def test_without_a_mailbox_chat_replies_still_reach_the_visitor_through_the_platform_sender(client):
    bot = await setup_bot(client)
    async with new_client() as v:
        base, conv = await start(v, bot["public_key"])
        await say(v, base, conv, "zebra")
        await v.post(f"{base}/conversations/{conv}/escalate", json={"email": "far@example.org"})
        mail_svc.OUTBOX.clear()
        await client.post(f"/api/conversations/{conv}/reply", json={"text": "Here you go."})
    [m] = [m for m in mail_svc.OUTBOX if m.to == "far@example.org"]
    assert "Here you go." in m.text and PLATFORM_NOTE in m.text
    async with SessionLocal() as db:
        row = (await db.execute(select(EmailMessage).where(EmailMessage.direction == "out"))).scalar_one()
        assert row.account_id is None and row.status == "sent" and str(row.conversation_id) == conv
    assert SUPPORT not in str(m.headers)
