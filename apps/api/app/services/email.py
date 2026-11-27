"""Transactional email. Plain-text bodies plus a minimal HTML version in Verity's voice."""

import asyncio
import html
import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("verity.email")


@dataclass
class Sent:
    to: str
    subject: str
    text: str
    links: list[str] = field(default_factory=list)
    headers: dict = field(default_factory=dict)


# The "memory" backend keeps messages here (tests and local runs without Mailpit).
OUTBOX: list[Sent] = []


def _html(text: str, links: list[str]) -> str:
    body = html.escape(text)
    for link in links:
        esc = html.escape(link)
        body = body.replace(
            esc,
            f'<a href="{esc}" style="color:#0d6655;font-weight:500">{esc}</a>',
        )
    return (
        '<div style="font-family:Geist,system-ui,sans-serif;font-size:14px;line-height:22px;color:#15171a;'
        'background:#f5f3ed;padding:32px">'
        '<div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #d9d5c9;border-radius:10px;'
        'padding:24px">'
        "<div style=\"font-family:'Instrument Serif',Georgia,serif;font-size:28px;margin-bottom:16px\">Verity"
        '<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#d6f35b;'
        'margin-left:6px"></span></div>'
        f'<div style="white-space:pre-line">{body}</div></div></div>'
    )


def _send_smtp(msg: EmailMessage) -> None:
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        if s.smtp_tls:
            smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password or "")
        smtp.send_message(msg)


async def send_email(to: str, subject: str, text: str, links: list[str] | None = None) -> None:
    s = get_settings()
    links = links or []
    if s.email_backend == "memory":
        OUTBOX.append(Sent(to, subject, text, links))
        return
    if s.email_backend == "console":
        log.warning("EMAIL to=%s subject=%s\n%s", to, subject, text)
        return
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(_html(text, links), subtype="html")
    try:
        await asyncio.to_thread(_send_smtp, msg)
    except OSError:
        log.exception("Could not send email to %s", to)


async def send_prepared(msg: EmailMessage) -> None:
    """Sends a fully built message (customer mail with thread headers) through the platform's own sender.

    Unlike send_email, a failure raises OSError so the caller (a queued job) can retry and record it.
    """
    s = get_settings()
    if not msg["From"]:
        msg["From"] = s.email_from
    if s.email_backend == "memory":
        body = msg.get_body(("plain",))
        OUTBOX.append(
            Sent(
                str(msg["To"]).lower(),
                str(msg["Subject"]),
                body.get_content() if body is not None else "",
                [],
                {k.lower(): str(v) for k, v in msg.items()},
            )
        )
        return
    if s.email_backend == "console":
        log.warning("EMAIL to=%s subject=%s", msg["To"], msg["Subject"])
        return
    await asyncio.to_thread(_send_smtp, msg)


def link(path: str) -> str:
    return get_settings().app_url.rstrip("/") + path


async def send_verification(to: str, name: str, token: str) -> None:
    url = link(f"/verify-email?token={token}")
    hours = get_settings().verify_token_ttl_hours
    await send_email(
        to,
        "Verify your email for Verity",
        f"Hi {name},\n\nConfirm this is your email address to finish creating your Verity account:\n\n{url}\n\n"
        f"The link works once and expires in {hours} hours. If you didn't sign up, ignore this email.",
        [url],
    )


async def send_password_reset(to: str, name: str, token: str) -> None:
    url = link(f"/reset-password?token={token}")
    minutes = get_settings().reset_token_ttl_minutes
    await send_email(
        to,
        "Reset your Verity password",
        f"Hi {name},\n\nSomeone asked to reset the password for this account. To choose a new one, open:\n\n{url}\n\n"
        f"The link works once and expires in {minutes} minutes. If it wasn't you, ignore this email; "
        "your password stays the same.",
        [url],
    )


async def send_invitation(to: str, inviter: str, workspace: str, role: str, token: str) -> None:
    url = link(f"/invite/{token}")
    await send_email(
        to,
        f"{inviter} invited you to {workspace} on Verity",
        f"{inviter} invited you to join the {workspace} workspace on Verity as {role}.\n\n"
        f"Accept the invitation:\n\n{url}\n\nThe invitation expires in {get_settings().invite_ttl_days} days.",
        [url],
    )


async def send_usage_alert(to: str, workspace: str, percent: int, remaining: int) -> None:
    url = link("/usage")
    await send_email(
        to,
        f"{workspace} has used {percent}% of its AI tokens",
        f"The {workspace} workspace has used {percent}% of this period's AI token allowance. "
        f"{remaining:,} weighted tokens remain.\n\nWhen the allowance runs out, AI answers pause. Manual "
        f"ticketing, email ingestion and forms keep working.\n\nSee usage or upgrade:\n\n{url}",
        [url],
    )


async def send_payment_receipt(to: str, workspace: str, plan: str, amount: str, period_end: str) -> None:
    url = link("/billing")
    await send_email(
        to,
        f"Receipt: Verity {plan}",
        f"Payment received for the {workspace} workspace.\n\nPlan: {plan}\nAmount: {amount}\n"
        f"Renews on: {period_end}\n\nBilling history:\n\n{url}",
        [url],
    )


async def send_payment_failed(to: str, workspace: str, plan: str, amount: str, reason: str | None, next_retry: str, grace_end: str) -> None:
    url = link("/billing")
    retry = f"We'll try again on {next_retry}." if next_retry else "There are no automatic retries left."
    await send_email(
        to,
        f"Payment failed: Verity {plan}",
        f"The {amount} renewal of {plan} for the {workspace} workspace didn't go through"
        f"{': ' + reason if reason else '.'}\n\n{retry} Until it's paid, AI answers are paused. Tickets, email and "
        f"forms keep working until {grace_end}; after that the subscription ends.\n\n"
        f"Update the payment method or retry now:\n\n{url}",
        [url],
    )


async def send_payment_recovered(to: str, workspace: str, plan: str) -> None:
    url = link("/billing")
    await send_email(
        to,
        f"Payment received: Verity {plan}",
        f"The renewal of {plan} for the {workspace} workspace has been paid. AI answers are back on.\n\n{url}",
        [url],
    )


async def send_subscription_ended(to: str, workspace: str, plan: str) -> None:
    url = link("/plans")
    await send_email(
        to,
        f"Your Verity {plan} subscription has ended",
        f"The {plan} subscription of the {workspace} workspace ended because its renewal couldn't be charged. "
        f"Your data is kept. Choose a plan to start again:\n\n{url}",
        [url],
    )


async def send_package_receipt(to: str, workspace: str, package: str, tokens: int, amount: str) -> None:
    url = link("/billing/tokens")
    await send_email(
        to,
        f"Receipt: {package}",
        f"Payment received for the {workspace} workspace.\n\nToken package: {package}\nTokens: {tokens:,}\n"
        f"Amount: {amount}\n\nThese tokens are used after the plan's allowance, carry over from period to period "
        f"and expire 12 months after purchase.\n\n{url}",
        [url],
    )


async def send_escalation(to: str, workspace: str, chatbot: str, question: str, conversation_id: str) -> None:
    url = link(f"/conversations/{conversation_id}")
    snippet = " ".join(question.split())[:300]
    await send_email(
        to,
        f"A customer is waiting for a reply in {workspace}",
        f"A conversation with {chatbot} was handed to your team. The assistant stays quiet until someone resolves it."
        f"\n\nLast message from the customer:\n“{snippet}”\n\nReply here:\n\n{url}",
        [url],
    )


async def send_ticket_created(to: str, workspace: str, number: int, subject: str, ticket_id: str) -> None:
    url = link(f"/tickets/{ticket_id}")
    await send_email(
        to,
        f"New ticket #{number} in {workspace}",
        f"A customer opened ticket #{number}:\n\n“{' '.join(subject.split())[:300]}”\n\nOpen it here:\n\n{url}",
        [url],
    )
