"""Job functions run by the worker. Each takes SAQ's `ctx` first and keyword arguments after."""

import logging

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


FUNCTIONS = [maintenance]
CRON = [("maintenance", "*/5 * * * *")]
