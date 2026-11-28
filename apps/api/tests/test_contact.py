"""The landing page's contact form: it emails the team's mailbox with the visitor in Reply-To, and the mailbox's
ticket is the visitor's. Reply-To is believed only on mail from the form's own sender."""

from app.core.config import get_settings
from app.services import email as mail
from tests.mail import CUSTOMER, SUPPORT, connect, deliver, owner, sent, tickets
from tests.mail import mail as raw_mail
from tests.phase3 import category

FORM_FROM = "contact-form@verity.example"
VISITOR = "sam@visitor.example"
BODY = {"name": "Sam Rivera", "email": VISITOR, "message": "Do you offer a yearly plan?", "locale": "es"}


def configure(monkeypatch, **over):
    s = get_settings()
    monkeypatch.setattr(s, "contact_email", over.get("contact_email", SUPPORT))
    monkeypatch.setattr(s, "contact_from", over.get("contact_from", FORM_FROM))


async def test_the_form_is_off_until_a_contact_address_is_set(client):
    assert (await client.get("/api/public/contact")).status_code == 404
    assert (await client.post("/api/public/contact", json=BODY)).status_code == 404
    assert mail.OUTBOX == []


async def test_a_message_is_emailed_to_the_team_with_the_visitor_in_reply_to(client, monkeypatch):
    configure(monkeypatch)
    assert (await client.get("/api/public/contact")).json() == {"enabled": True}
    r = await client.post("/api/public/contact", json=BODY)
    assert r.status_code == 202, r.text
    [m] = mail.OUTBOX
    assert m.to == SUPPORT and m.subject == "Contact form: Sam Rivera"
    assert m.headers["from"].endswith(f"<{FORM_FROM}>") and VISITOR in m.headers["reply-to"]
    assert "yearly plan" in m.text and VISITOR in m.text


async def test_a_name_cant_inject_headers(client, monkeypatch):
    configure(monkeypatch)
    r = await client.post("/api/public/contact", json={**BODY, "name": "Sam\r\nBcc: victim@example.org"})
    assert r.status_code == 202
    [m] = mail.OUTBOX
    assert "bcc" not in m.headers and "\n" not in m.subject


async def test_the_hidden_field_catches_bots_without_telling_them(client, monkeypatch):
    configure(monkeypatch)
    r = await client.post("/api/public/contact", json={**BODY, "website": "http://spam.example"})
    assert r.status_code == 202 and mail.OUTBOX == []


async def test_the_form_checks_its_input(client, monkeypatch):
    configure(monkeypatch)
    assert (await client.post("/api/public/contact", json={**BODY, "email": "not-an-email"})).status_code == 422
    assert (await client.post("/api/public/contact", json={**BODY, "message": "   "})).status_code == 422
    assert (await client.post("/api/public/contact", json={**BODY, "message": "x" * 4001})).status_code == 422
    assert mail.OUTBOX == []


async def test_one_address_is_limited_to_three_messages_an_hour(client, monkeypatch):
    configure(monkeypatch)
    for i in range(3):
        assert (await client.post("/api/public/contact", json={**BODY, "message": f"hello {i}"})).status_code == 202
    assert (await client.post("/api/public/contact", json=BODY)).status_code == 429
    assert len(mail.OUTBOX) == 3


async def test_one_connection_is_limited_to_five_messages_an_hour(client, monkeypatch):
    configure(monkeypatch)
    for i in range(5):
        assert (await client.post("/api/public/contact", json={**BODY, "email": f"v{i}@visitor.example"})).status_code == 202
    assert (await client.post("/api/public/contact", json={**BODY, "email": "v9@visitor.example"})).status_code == 429
    assert len(mail.OUTBOX) == 5


async def test_a_failed_send_is_reported(client, monkeypatch):
    configure(monkeypatch)

    async def boom(msg):
        raise OSError("smtp down")

    monkeypatch.setattr(mail, "send_prepared", boom)
    r = await client.post("/api/public/contact", json=BODY)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "send_failed"


def form_mail(reply_to=f"Sam Rivera <{VISITOR}>", sender=FORM_FROM, body="Do you offer a yearly plan?"):
    headers = {"Reply-To": reply_to} if reply_to else {}
    return raw_mail(subject="Contact form: Sam Rivera", body=body, sender=sender, name="Verity contact form", headers=headers)[0]


async def setup(client, monkeypatch):
    await owner(client)
    await category(client, "General")
    configure(monkeypatch)
    return await connect(client)


async def test_form_mail_becomes_a_ticket_for_the_visitor_and_the_reply_goes_to_them(client, monkeypatch):
    a = await setup(client, monkeypatch)
    await deliver(client, a["id"], form_mail())
    [t] = await tickets(client)
    assert t["requester_email"] == VISITOR and t["requester_name"] == "Sam Rivera"
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert detail["reply_channel"]["to"] == VISITOR
    assert [m["To"] for m in await sent()] == [VISITOR]  # the first reply acknowledges the visitor, not the form's address


async def test_reply_to_is_ignored_for_every_other_sender(client, monkeypatch):
    a = await setup(client, monkeypatch)
    await deliver(client, a["id"], form_mail(sender=CUSTOMER, reply_to="Someone Else <victim@example.org>"))
    [t] = await tickets(client)
    assert t["requester_email"] == CUSTOMER


async def test_without_a_contact_address_reply_to_is_never_believed(client, monkeypatch):
    a = await setup(client, monkeypatch)
    monkeypatch.setattr(get_settings(), "contact_email", None)
    await deliver(client, a["id"], form_mail())
    [t] = await tickets(client)
    assert t["requester_email"] == FORM_FROM
