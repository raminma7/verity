"""The landing page's contact form: one plain message to the team's own mailbox, with the visitor in Reply-To.

The mailbox is connected to a workspace like any other, so the message becomes a ticket for the visitor (the
requester is taken from Reply-To for this sender only, see mail.parse) and replies go to the visitor.
"""

from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import get_settings

LOCALES = {"en", "es", "de", "fr", "it", "ar"}


def one_line(s: str) -> str:
    return " ".join(s.split())


def build(name: str, email: str, message: str, locale: str) -> EmailMessage:
    s = get_settings()
    name = one_line(name)[:120]
    msg = EmailMessage()
    msg["From"] = formataddr(("Verity contact form", s.contact_sender or ""))
    msg["To"] = s.contact_email
    msg["Reply-To"] = formataddr((name, email))
    msg["Subject"] = f"Contact form: {name}"[:200]
    msg["Content-Language"] = locale if locale in LOCALES else "en"
    msg.set_content(message.strip() + f"\n\n--\n{name} <{email}>\nSent from the website ({locale})")
    return msg
