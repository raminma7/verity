"""Fetching mailboxes on the job queue: keyed per mailbox, locked, retried, and visible on the account."""

import asyncio

from saq import Worker
from sqlalchemy import text, update

from app.core.db import SessionLocal, engine
from app.jobs import tasks
from app.models import EmailAccount, Subscription
from app.services.mail import transport as tr
from app.services.mail.fetch import fetch_mailbox, poll_due
from app.services.mail.transport import FakeTransport, TransportError
from tests.mail import SUPPORT, connect, mail, owner, sent, tickets
from tests.test_jobs import real_queue  # noqa: F401  (fixture)


async def test_polling_queues_each_active_mailbox_once(real_queue, client):  # noqa: F811
    await owner(client)
    a = await connect(client)
    b = await connect(client, "paused@acmehelp.com")
    await client.patch(f"/api/email-accounts/{b['id']}", json={"status": "paused"})
    assert await poll_due() == 1
    assert await poll_due() == 1  # queued again: the key keeps one job per mailbox
    jobs = [j for j in (await real_queue.info(jobs=True))["jobs"] if j["function"] == "fetch_mailbox"]
    assert len(jobs) == 1 and a["id"] in str(jobs[0]["kwargs"])


async def test_a_real_worker_fetches_new_mail_and_sends_the_first_reply(real_queue, client):  # noqa: F811
    await owner(client)
    a = await connect(client)
    await FakeTransport.deliver(SUPPORT, mail()[0])
    worker = Worker(real_queue, functions=[tasks.fetch_mailbox, tasks.send_email_message], concurrency=2, dequeue_timeout=0.1)
    task = asyncio.create_task(worker.start())
    try:
        await poll_due()
        for _ in range(100):
            if await tickets(client) and await sent():
                break
            await asyncio.sleep(0.1)
    finally:
        await worker.stop()
        task.cancel()
    assert len(await tickets(client)) == 1 and len(await sent()) == 1
    acct = (await client.get(f"/api/email-accounts/{a['id']}")).json()
    assert acct["fetch_status"] == "ok" and acct["last_success_at"]


async def test_a_mailbox_already_being_fetched_is_not_fetched_again(client):
    await owner(client)
    a = await connect(client)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"), {"k": f"mailbox:{a['id']}"})
        await conn.commit()
        assert await fetch_mailbox(a["id"]) == "busy"
        await conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": f"mailbox:{a['id']}"})
        await conn.commit()
    assert (await fetch_mailbox(a["id"])).startswith("ok")


async def test_transient_failures_retry_and_show_on_the_account(client):
    await owner(client)
    a = await connect(client)
    real = tr.get_transport()
    calls = {"n": 0}

    class Flaky(FakeTransport):
        async def fetch(self, acct, state, limit):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TransportError("email_connect_failed", "Couldn't connect to imap.acme.test:993.", retryable=True)
            return await super().fetch(acct, state, limit)

    tr.set_transport(Flaky())
    try:
        await FakeTransport.deliver(SUPPORT, mail()[0])
        try:
            await fetch_mailbox(a["id"], final_attempt=False)
            raise AssertionError("expected a retry")
        except TransportError as e:
            assert e.code == "email_connect_failed"
        acct = (await client.get(f"/api/email-accounts/{a['id']}")).json()
        assert acct["fetch_status"] == "error" and acct["last_error_code"] == "email_connect_failed" and acct["status"] == "active"
        assert (await fetch_mailbox(a["id"], final_attempt=False)).startswith("ok:1")
    finally:
        tr.set_transport(real)
    acct = (await client.get(f"/api/email-accounts/{a['id']}")).json()
    assert acct["fetch_status"] == "ok" and acct["last_error_code"] is None
    assert len(await tickets(client)) == 1


async def test_without_an_active_subscription_mailboxes_are_not_fetched(client):
    await owner(client)
    await connect(client)
    async with SessionLocal() as db:
        await db.execute(update(Subscription).values(status="canceled"))
        await db.commit()
    assert await poll_due() == 0


async def test_disconnected_or_paused_mailboxes_are_skipped(client):
    await owner(client)
    a = await connect(client)
    async with SessionLocal() as db:
        await db.execute(update(EmailAccount).values(status="paused"))
        await db.commit()
    assert await fetch_mailbox(a["id"]) == "skipped"
