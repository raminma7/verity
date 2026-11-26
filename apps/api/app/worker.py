"""Background jobs: subscription renewals, stale token holds and audit retention.

    python -m app.worker

Phase 2 moves ingestion and crawling onto a proper queue; these periodic jobs stay here.
"""

import asyncio
import logging

from app.core.db import SessionLocal
from app.routers.admin import purge_audit
from app.services import quota
from app.services.subscriptions import renew_due

log = logging.getLogger("verity.worker")
INTERVAL = 300


async def tick() -> None:
    async with SessionLocal() as db:
        renewed = await renew_due(db)
        released = await quota.release_stale(db)
        purged = await purge_audit(db)
    log.info("renewals=%s released=%s purged=%s", renewed, released, purged)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            await tick()
        except Exception:
            log.exception("worker tick failed")
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
