"""Every AI call goes through the workspace token quota: reserve the estimate → call → settle the real cost.

    async with Meter(db, ws_id, model=..., operation=..., kind=..., est_input=..., est_output=...) as meter:
        result = await call()
        await meter.settle(result.input_tokens, result.output_tokens)

`reserve` raises QuotaExhausted before any provider call is made. Leaving the block without settling
(an exception, a cancelled stream, a client that disconnects) releases the hold, so a failed call costs
nothing and a hold can't leak.
"""

import math
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent
from app.services import quota


def estimate(text_chars: int) -> int:
    return max(1, math.ceil(text_chars / 4))


class Meter:
    def __init__(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        model: str,
        operation: str,
        kind: str,
        est_input: int,
        est_output: int,
    ):
        self.db, self.workspace_id = db, workspace_id
        self.model, self.operation, self.kind = model, operation, kind
        self.est_input, self.est_output = est_input, est_output
        self.reservation_id: uuid.UUID | None = None
        self.event: UsageEvent | None = None

    async def __aenter__(self) -> "Meter":
        res = await quota.reserve(
            self.db, self.workspace_id, model=self.model, operation=self.operation, est_input=self.est_input, est_output=self.est_output
        )
        self.reservation_id = res.id
        return self

    async def settle(self, input_tokens: int, output_tokens: int, *, model: str | None = None) -> UsageEvent | None:
        if self.reservation_id is None or self.event is not None:
            return self.event
        if model and model != self.model:
            # A server-side fallback answered on another model; bill the model that actually ran.
            await quota.retarget(self.db, self.reservation_id, model)
        self.event = await quota.settle(
            self.db, self.reservation_id, input_tokens=input_tokens, output_tokens=output_tokens, kind=self.kind
        )
        return self.event

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self.reservation_id is not None and self.event is None:
            try:
                await self.db.rollback()
            finally:
                await quota.release(self.db, self.reservation_id)
        return False
