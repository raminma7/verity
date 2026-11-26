"""The background worker: a SAQ worker on Redis.

    python -m app.worker

Runs queued jobs (document ingestion, website crawls, deletions) and cron jobs (website re-sync,
subscription renewals, stale token holds, audit retention). Run as many workers as you like: queued jobs
are handed to one worker each, and cron jobs are de-duplicated across workers.
"""

import asyncio
import logging

from saq import CronJob, Worker

from app.jobs import get_queue
from app.jobs.tasks import CRON, FUNCTIONS


def build_worker(**kwargs) -> Worker:
    by_name = {f.__name__: f for f in FUNCTIONS}
    cron = [CronJob(by_name[name], cron=expr, timeout=1800) for name, expr in CRON]
    return Worker(get_queue(), functions=FUNCTIONS, cron_jobs=cron, concurrency=kwargs.pop("concurrency", 4), **kwargs)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = build_worker()
    await worker.queue.connect()
    await worker.start()  # stops cleanly on SIGINT / SIGTERM


if __name__ == "__main__":
    asyncio.run(main())
