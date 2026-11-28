"""Transactional email. Plain-text bodies plus a minimal HTML version in Verity's voice."""

import asyncio
import html
import json
import logging
import smtplib
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage

from app.core.config import get_settings
from app.services.email_text import day, lang, number, phrase, render

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
DEV_OUTBOX_KEY = "verity:dev:outbox"


async def _mirror(m: Sent) -> None:
    """In development, also keeps the message in Redis, so /api/dev/outbox shows mail the worker sent too."""
    if not get_settings().is_dev:
        return
    try:
        from app.core.ratelimit import get_redis

        r = get_redis()
        row = {"to": m.to, "subject": m.subject, "text": m.text, "links": m.links, "headers": m.headers}
        await r.rpush(DEV_OUTBOX_KEY, json.dumps(row))
        await r.ltrim(DEV_OUTBOX_KEY, -500, -1)
    except Exception:  # noqa: BLE001 - a development convenience; never block sending
        log.debug("dev outbox mirror unavailable", exc_info=True)


def _html(text: str, links: list[str], locale: str = "en") -> str:
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
        f'<div style="white-space:pre-line" dir="{"rtl" if locale == "ar" else "ltr"}" lang="{locale}">{body}</div></div></div>'
    )


def _send_smtp(msg: EmailMessage) -> None:
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        if s.smtp_tls:
            smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password or "")
        smtp.send_message(msg)


async def send_email(to: str, subject: str, text: str, links: list[str] | None = None, *, locale: str = "en") -> None:
    s = get_settings()
    links = links or []
    if s.email_backend == "memory":
        sent = Sent(to, subject, text, links, {"Content-Language": locale})
        OUTBOX.append(sent)
        await _mirror(sent)
        return
    if s.email_backend == "console":
        log.warning("EMAIL to=%s subject=%s\n%s", to, subject, text)
        return
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Content-Language"] = locale
    msg.set_content(text)
    msg.add_alternative(_html(text, links, locale), subtype="html")
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


Day = date | datetime | str | None


async def _send(to: str, key: str, locale: str | None, url: str | None, **values: object) -> None:
    loc = lang(locale)
    subject, body = render(key, loc, url=url or "", **values)
    await send_email(to, subject, body, [url] if url else [], locale=loc)


async def send_verification(to: str, name: str, token: str, *, locale: str = "en") -> None:
    hours = get_settings().verify_token_ttl_hours
    await _send(to, "verify", locale, link(f"/verify-email?token={token}"), name=name, hours=hours)


async def send_password_reset(to: str, name: str, token: str, *, locale: str = "en") -> None:
    minutes = get_settings().reset_token_ttl_minutes
    await _send(to, "reset", locale, link(f"/reset-password?token={token}"), name=name, minutes=minutes)


async def send_invitation(to: str, inviter: str, workspace: str, role: str, token: str, *, locale: str = "en") -> None:
    days = get_settings().invite_ttl_days
    await _send(to, "invite", locale, link(f"/invite/{token}"), inviter=inviter, workspace=workspace, role=role, days=days)


async def send_usage_alert(to: str, workspace: str, percent: int, remaining: int, *, locale: str = "en") -> None:
    await _send(to, "usage", locale, link("/usage"), workspace=workspace, percent=percent, remaining=number(remaining, lang(locale)))


async def send_payment_receipt(to: str, workspace: str, plan: str, amount: str, period_end: Day, *, locale: str = "en") -> None:
    await _send(
        to, "receipt", locale, link("/billing"), workspace=workspace, plan=plan, amount=amount, period_end=day(period_end, lang(locale))
    )


async def send_payment_failed(
    to: str, workspace: str, plan: str, amount: str, reason: str | None, next_retry: Day, grace_end: Day, *, locale: str = "en"
) -> None:
    loc = lang(locale)
    retry = phrase("retry_on", loc, next_retry=day(next_retry, loc)) if next_retry else phrase("retry_none", loc)
    await _send(
        to,
        "failed",
        loc,
        link("/billing"),
        amount=amount,
        plan=plan,
        workspace=workspace,
        reason=f": {reason}" if reason else "",
        retry=retry,
        grace_end=day(grace_end, loc),
    )


async def send_payment_recovered(to: str, workspace: str, plan: str, *, locale: str = "en") -> None:
    await _send(to, "recovered", locale, link("/billing"), workspace=workspace, plan=plan)


async def send_subscription_ended(to: str, workspace: str, plan: str, *, locale: str = "en") -> None:
    await _send(to, "ended", locale, link("/plans"), workspace=workspace, plan=plan)


async def send_package_receipt(to: str, workspace: str, package: str, tokens: int, amount: str, *, locale: str = "en") -> None:
    loc = lang(locale)
    await _send(
        to, "package", loc, link("/billing/tokens"), workspace=workspace, package=package, tokens=number(tokens, loc), amount=amount
    )


async def send_export_ready(to: str, workspace: str | None, token: str, expires: Day, *, locale: str = "en") -> None:
    loc = lang(locale)
    what = phrase("export_workspace", loc, workspace=workspace) if workspace else phrase("export_personal", loc)
    await _send(to, "export", loc, link(f"/exports/{token}"), what=what, expires=day(expires, loc))


async def send_workspace_deletion_scheduled(to: str, workspace: str, erase_on: Day, owner: bool, *, locale: str = "en") -> None:
    loc = lang(locale)
    url = link("/workspaces")
    when = day(erase_on, loc)
    undo = phrase("ws_undo_owner", loc, erase_on=when, url=url) if owner else phrase("ws_undo_member", loc)
    await _send(to, "ws_delete", loc, url if owner else None, workspace=workspace, erase_on=when, undo=undo)


async def send_account_deletion_scheduled(to: str, erase_on: Day, *, locale: str = "en") -> None:
    loc = lang(locale)
    await _send(to, "account_delete", loc, link("/sign-in"), erase_on=day(erase_on, loc))


async def send_escalation(to: str, workspace: str, chatbot: str | None, question: str, conversation_id: str, *, locale: str = "en") -> None:
    loc = lang(locale)
    snippet = " ".join(question.split())[:300]
    bot = chatbot or phrase("assistant", loc)
    await _send(to, "escalation", loc, link(f"/conversations/{conversation_id}"), workspace=workspace, chatbot=bot, snippet=snippet)


async def send_ticket_created(to: str, workspace: str, number: int, subject: str, ticket_id: str, *, locale: str = "en") -> None:
    snippet = " ".join(subject.split())[:300]
    await _send(to, "ticket_created", locale, link(f"/tickets/{ticket_id}"), workspace=workspace, number=number, subject=snippet)
