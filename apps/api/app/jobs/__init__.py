"""Background jobs on a Redis queue (SAQ).

Request handlers call `enqueue(...)`; the worker (`python -m app.worker`) runs the job functions registered
in `app.jobs.tasks`. Jobs open their own database session and record their outcome on the row they work
on, so a failure is visible to the customer as a status, not just a log line.

With VERITY_QUEUE=inline (tests), jobs run immediately in the calling task instead of going through Redis;
jobs scheduled for later (`scheduled=`) wait in DELAYED until a test runs them with `run_delayed()`.
"""

import logging

from saq import Queue

from app.core.config import get_settings

log = logging.getLogger("verity.jobs")
QUEUE_NAME = "verity"

_queue: Queue | None = None
DELAYED: list[tuple[str, dict]] = []


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue.from_url(get_settings().redis_url, name=QUEUE_NAME)
    return _queue


def set_queue(queue: Queue | None) -> None:
    """Tests swap in a queue bound to a test Redis."""
    global _queue
    _queue = queue


async def enqueue(
    function: str, *, key: str | None = None, job_timeout: int = 600, retries: int = 3, scheduled: int | None = None, **kwargs
) -> None:
    """Queues `function(**kwargs)`. A job with the same `key` that is still queued or running is not added twice.

    `scheduled`: run no earlier than this Unix time.
    """
    if get_settings().queue == "inline":
        if scheduled is not None:
            DELAYED.append((function, kwargs))
            return
        from app.jobs.tasks import FUNCTIONS

        fn = dict((f.__name__, f) for f in FUNCTIONS)[function]
        try:
            await fn({}, **kwargs)
        except Exception:  # jobs record their own failures; inline mode must not break the request
            log.exception("inline job %s failed", function)
        return
    await get_queue().enqueue(
        function,
        key=key,
        timeout=job_timeout,
        retries=retries,
        retry_delay=5.0,
        retry_backoff=True,
        **({"scheduled": scheduled} if scheduled is not None else {}),
        **kwargs,
    )


async def run_delayed() -> int:
    """Inline mode: runs the jobs that were scheduled for later, as if their time had come."""
    from app.jobs.tasks import FUNCTIONS

    by_name = {f.__name__: f for f in FUNCTIONS}
    n = 0
    while DELAYED:
        function, kwargs = DELAYED.pop(0)
        await by_name[function]({}, **kwargs)
        n += 1
    return n
