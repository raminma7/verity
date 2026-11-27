"""Fetching a mailbox in the background, one mailbox at a time.

`poll_due()` (every minute) queues `fetch_mailbox` for each active mailbox of a subscribed workspace, keyed
`mailbox:{id}` so the queue never holds the same mailbox twice. The job also takes a Postgres advisory lock on
its own connection, so even a manual "Fetch now" racing the schedule, or a second worker, can't fetch one
mailbox twice at once. Progress (the last UID) is saved after every message, so a crash re-fetches at most
one message, and the Message-ID index makes that harmless.

The account shows what happened: fetching, the last successful fetch, and a stable error code.
"""

import logging
import uuid

from sqlalchemy import select, text, update

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.models import EmailAccount, Membership, Notification, Subscription, User
from app.models import EmailMessage as EmailRow
from app.models.base import utcnow
from app.services.mail.accounts import config_for
from app.services.mail.ingest import process_incoming
from app.services.mail.transport import TransportError, get_transport
from app.services.subscriptions import is_active

log = logging.getLogger("verity.mail")

MAX_BATCHES = 10


async def poll_due() -> int:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(EmailAccount, Subscription)
                .outerjoin(Subscription, Subscription.workspace_id == EmailAccount.workspace_id)
                .where(EmailAccount.status == "active", EmailAccount.secret_enc.is_not(None))
            )
        ).all()
    n = 0
    for acct, sub in rows:
        if not is_active(sub):
            continue
        await queue_fetch(acct.id)
        n += 1
    return n


async def queue_fetch(account_id: uuid.UUID) -> None:
    from app.jobs import enqueue

    await enqueue("fetch_mailbox", key=f"mailbox:{account_id}", job_timeout=900, retries=3, account_id=str(account_id))


async def _notify_auth_failed(db, acct: EmailAccount) -> None:
    rows = (
        await db.execute(
            select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == acct.workspace_id)
        )
    ).all()
    for m, u in rows:
        if "integrations.manage" in m.role.permissions and u.status == "active":
            db.add(
                Notification(
                    workspace_id=acct.workspace_id,
                    user_id=u.id,
                    kind="email_account_error",
                    title=f"{acct.address} needs a new app password",
                    body="Verity couldn't sign in to this mailbox, so new email isn't arriving. Update its app password.",
                    data={"email_account_id": str(acct.id)},
                    created_at=utcnow(),
                )
            )


async def fetch_mailbox(account_id: uuid.UUID, *, final_attempt: bool = True) -> str:
    async with engine.connect() as lock_conn:
        key = f"mailbox:{account_id}"
        got = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": key})).scalar()
        await lock_conn.commit()
        if not got:
            return "busy"
        try:
            return await _fetch(account_id, final_attempt=final_attempt)
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": key})
            await lock_conn.commit()


async def _fetch(account_id: uuid.UUID, *, final_attempt: bool) -> str:
    async with SessionLocal() as db:
        acct = await db.get(EmailAccount, account_id)
        if acct is None or acct.status != "active" or not acct.secret_enc:
            return "skipped"
        acct.fetch_status, acct.last_fetch_at = "fetching", utcnow()
        await db.commit()
        processed = 0
        try:
            cfg = config_for(acct)
            for _ in range(MAX_BATCHES):
                result = await get_transport().fetch(cfg, dict(acct.fetch_state or {}), get_settings().mail_fetch_batch)
                if not result.messages:
                    await db.refresh(acct)
                    acct.fetch_state = result.state
                    await db.commit()
                for raw in result.messages:
                    try:
                        await process_incoming(db, acct, raw)
                    except TransportError:
                        raise
                    except Exception:  # noqa: BLE001 - one bad message must not stall the mailbox
                        log.exception("message uid=%s on mailbox %s failed", raw.uid, account_id)
                        await db.rollback()
                        await db.execute(
                            update(EmailRow)
                            .where(EmailRow.account_id == account_id, EmailRow.uid == raw.uid, EmailRow.status == "processing")
                            .values(status="failed", error="Something went wrong while processing this message.")
                        )
                    await db.refresh(acct)
                    acct.fetch_state = {**result.state, "last_uid": raw.uid}
                    await db.commit()
                    processed += 1
                if not result.more:
                    break
        except TransportError as e:
            await db.rollback()
            await db.refresh(acct)
            acct.fetch_status = "error"
            acct.last_error_code, acct.last_error = e.code, e.message[:500]
            acct.consecutive_failures += 1
            if e.code == "email_auth_failed":
                acct.status = "error"
                await _notify_auth_failed(db, acct)
            await db.commit()
            if e.retryable and not final_attempt:
                raise
            return e.code
        except Exception:
            log.exception("fetching mailbox %s failed", account_id)
            await db.rollback()
            await db.refresh(acct)
            acct.fetch_status = "error"
            acct.last_error_code, acct.last_error = (
                "email_fetch_failed",
                "Something went wrong while reading new mail. Verity will try again.",
            )
            acct.consecutive_failures += 1
            await db.commit()
            if not final_attempt:
                raise
            return "email_fetch_failed"
        await db.refresh(acct)
        acct.fetch_status, acct.last_success_at = "ok", utcnow()
        acct.last_error_code = acct.last_error = None
        acct.consecutive_failures = 0
        await db.commit()
        return f"ok:{processed}"
