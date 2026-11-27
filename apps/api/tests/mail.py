"""Shared helpers for the Phase 4 email tests (fake transport, so mailboxes live in fakeredis)."""

from email.message import EmailMessage
from email.utils import make_msgid

from app.services.mail.transport import FakeTransport
from tests.conftest import signup, subscribe

SUPPORT = "support@acmehelp.com"
CUSTOMER = "jane@example.org"


def account_body(address=SUPPORT, **over) -> dict:
    return {
        "name": "Support",
        "address": address,
        "display_name": "Acme Support",
        "password": "app-password-123",
        "imap_host": "imap.acme.test",
        "smtp_host": "smtp.acme.test",
        **over,
    }


async def owner(client, email="owner4@example.com", plan="starter") -> dict:
    me = await signup(client, email)
    await subscribe(client, plan)
    return me


async def connect(client, address=SUPPORT, **over) -> dict:
    r = await client.post("/api/email-accounts", json=account_body(address, **over))
    assert r.status_code == 201, r.text
    return r.json()


def mail(
    subject="My order hasn't arrived",
    body="Hello, my order 12345 hasn't arrived yet. Can you check?",
    *,
    sender=CUSTOMER,
    name="Jane Doe",
    to=SUPPORT,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    headers: dict | None = None,
    html: str | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> tuple[bytes, str]:
    m = EmailMessage()
    m["From"] = f"{name} <{sender}>"
    m["To"] = to
    m["Subject"] = subject
    mid = message_id or make_msgid(domain="example.org")
    m["Message-ID"] = mid
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = references
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    for filename, ctype, data in attachments or []:
        maintype, subtype = ctype.split("/", 1)
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return m.as_bytes(), mid


async def deliver(client, account_id: str, raw: bytes, address=SUPPORT) -> dict:
    """New mail arrives and the mailbox is fetched (inline queue: the fetch runs at once)."""
    await FakeTransport.deliver(address, raw)
    r = await client.post(f"/api/email-accounts/{account_id}/fetch")
    assert r.status_code == 200, r.text
    return r.json()


async def sent(address=SUPPORT) -> list:
    from email import policy
    from email.parser import Parser

    return [Parser(policy=policy.default).parsestr(raw) for raw in await FakeTransport.sent(address)]


def text_of(m) -> str:
    body = m.get_body(("plain",))
    return body.get_content() if body is not None else ""


async def tickets(client, **params) -> list:
    r = await client.get("/api/tickets", params={"source": "email", **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]
