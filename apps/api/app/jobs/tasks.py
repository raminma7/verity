"""Job functions run by the worker. Each takes SAQ's `ctx` first and keyword arguments after."""

import logging
import uuid
from datetime import datetime, timedelta

from app.core.db import SessionLocal
from app.services import quota
from app.services.subscriptions import renew_due

log = logging.getLogger("verity.jobs")


async def maintenance(ctx) -> dict:
    """Subscription renewals, stale token holds and audit retention (every 5 minutes)."""
    from app.routers.admin import purge_audit

    async with SessionLocal() as db:
        renewed = await renew_due(db)
        released = await quota.release_stale(db)
        purged = await purge_audit(db)
    log.info("renewals=%s released=%s purged=%s", renewed, released, purged)
    return {"renewed": renewed, "released": released, "purged": purged}


def _final_attempt(ctx) -> bool:
    job = ctx.get("job") if ctx else None
    return job is None or job.attempts >= job.retries


async def ingest_source(ctx, *, source_id: str, version: int) -> None:
    from app.services.ingest.pipeline import ingest_file

    await ingest_file(uuid.UUID(source_id), version, final_attempt=_final_attempt(ctx))


async def crawl_source(ctx, *, source_id: str, version: int) -> None:
    from app.services.ingest.pipeline import crawl_website

    await crawl_website(uuid.UUID(source_id), version, final_attempt=_final_attempt(ctx))


async def remove_source(ctx, *, source_id: str) -> None:
    from app.services.ingest.pipeline import delete_source

    await delete_source(uuid.UUID(source_id))


async def sync_websites(ctx) -> int:
    """Queues every website whose periodic re-sync is due (every 15 minutes)."""
    from app.jobs import enqueue
    from app.services.ingest.pipeline import due_websites

    due = await due_websites()
    for source_id, version in due:
        await enqueue("crawl_source", key=f"crawl:{source_id}:{version}", job_timeout=3600, source_id=str(source_id), version=version)
    return len(due)


async def handoff_check(ctx, *, conversation_id: str, escalated_at: str) -> str:
    """Nobody replied to a handoff in time: offer the matching form, or the contact form."""
    from app.services.forms_ai import handoff_timeout

    async with SessionLocal() as db:
        return await handoff_timeout(db, uuid.UUID(conversation_id), datetime.fromisoformat(escalated_at))


async def sweep_handoffs(ctx) -> int:
    """Every minute: runs handoff checks that were lost (for example, while no worker was running).

    Returns how many conversations got their form offer or automatic ticket.
    """
    from sqlalchemy import select

    from app.models import Chatbot, Conversation
    from app.models.base import utcnow
    from app.services.forms_ai import handoff_timeout

    now = utcnow()
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Conversation.id, Conversation.escalated_at, Chatbot.escalation)
                .join(Chatbot, Chatbot.id == Conversation.chatbot_id)
                .where(
                    Conversation.status == "escalated",
                    Conversation.channel == "widget",
                    Conversation.escalated_at > now - timedelta(days=2),
                    Conversation.escalated_at < now - timedelta(seconds=60),
                )
                .limit(500)
            )
        ).all()
    offered = 0
    for conv_id, escalated_at, esc in rows:
        esc = esc or {}
        if not esc.get("ticket_fallback", True) or escalated_at > now - timedelta(seconds=int(esc.get("wait_seconds", 120)) + 30):
            continue
        async with SessionLocal() as db:
            offered += (await handoff_timeout(db, conv_id, escalated_at)) in ("offered", "ticket")
    return offered


async def poll_mailboxes(ctx) -> int:
    """Every minute: queues a fetch for each active mailbox (keyed, so a mailbox is never queued twice)."""
    from app.services.mail.fetch import poll_due

    return await poll_due()


async def fetch_mailbox(ctx, *, account_id: str) -> str:
    from app.services.mail.fetch import fetch_mailbox as run

    return await run(uuid.UUID(account_id), final_attempt=_final_attempt(ctx))


async def send_email_message(ctx, *, email_id: str) -> str:
    from app.services.mail.outbound import send

    async with SessionLocal() as db:
        return await send(db, uuid.UUID(email_id), final_attempt=_final_attempt(ctx))


FUNCTIONS = [
    maintenance,
    ingest_source,
    crawl_source,
    remove_source,
    sync_websites,
    handoff_check,
    sweep_handoffs,
    poll_mailboxes,
    fetch_mailbox,
    send_email_message,
]
CRON = [
    ("maintenance", "*/5 * * * *"),
    ("sync_websites", "*/15 * * * *"),
    ("sweep_handoffs", "* * * * *"),
    ("poll_mailboxes", "* * * * *"),
]
