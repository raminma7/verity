"""The Redis job queue: a real SAQ worker against a real Redis (skipped when none is running)."""

import asyncio
import uuid

import pytest
from redis.asyncio import Redis
from saq import Queue, Worker

from app.core.config import get_settings
from app.jobs import enqueue, set_queue

RAN: list[dict] = []


async def record(ctx, *, value: str) -> str:
    RAN.append({"value": value, "attempt": ctx["job"].attempts})
    if value == "flaky" and ctx["job"].attempts < 2:
        raise RuntimeError("transient")
    return value


async def redis_up() -> bool:
    try:
        r = Redis.from_url(get_settings().redis_url)
        await r.ping()
        await r.aclose()
        return True
    except Exception:
        return False


@pytest.fixture
async def real_queue(monkeypatch):
    if not await redis_up():
        pytest.skip("Redis is not running")
    queue = Queue.from_url(get_settings().redis_url, name=f"verity-test-{uuid.uuid4().hex[:8]}")
    await queue.connect()
    set_queue(queue)
    monkeypatch.setattr(get_settings(), "queue", "saq")
    RAN.clear()
    yield queue
    set_queue(None)
    await queue.disconnect()


async def run_until(queue: Queue, done, wait_s: float = 10.0):
    worker = Worker(queue, functions=[record], concurrency=2, dequeue_timeout=0.1)
    task = asyncio.create_task(worker.start())
    try:
        for _ in range(int(wait_s / 0.05)):
            if done():
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"jobs did not finish: {RAN}")
    finally:
        await worker.stop()
        task.cancel()


async def test_jobs_run_on_a_real_worker_and_retry(real_queue):
    await enqueue("record", key="a", value="hello")
    await enqueue("record", key="b", value="flaky")
    await run_until(real_queue, lambda: {"hello", "flaky"} <= {r["value"] for r in RAN} and sum(r["value"] == "flaky" for r in RAN) >= 2)
    flaky = [r for r in RAN if r["value"] == "flaky"]
    assert len(flaky) == 2  # failed once, retried once, then succeeded


async def test_same_key_is_queued_once(real_queue):
    await enqueue("record", key="same", value="one")
    await enqueue("record", key="same", value="two")  # still queued: dropped
    await run_until(real_queue, lambda: len(RAN) >= 1)
    await asyncio.sleep(0.3)
    assert [r["value"] for r in RAN] == ["one"]
