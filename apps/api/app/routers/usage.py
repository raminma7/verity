from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import cast, func, select
from sqlalchemy.types import Date

from app.core.deps import Db, WorkspaceContext, require
from app.models import ModelMultiplier, UsageEvent
from app.models.base import utcnow
from app.services.quota import available, current_ledger

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/summary")
async def summary(db: Db, ctx: WorkspaceContext = require("usage.read")):
    ledger = await current_ledger(db, ctx.workspace.id)
    if ledger is None:
        return {"active": False}
    raw = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.weighted_tokens).filter(UsageEvent.kind == "embedding"), 0),
            ).where(UsageEvent.ledger_id == ledger.id)
        )
    ).one()
    total = ledger.allowance + ledger.bonus
    return {
        "active": True,
        "period_start": ledger.period_start,
        "period_end": ledger.period_end,
        "allowance": ledger.allowance,
        "bonus": ledger.bonus,
        "total_allowance": total,
        "consumed_weighted": ledger.consumed,
        "reserved": ledger.reserved,
        "remaining": available(ledger),
        "percent_used": round(ledger.consumed * 100 / total, 1) if total else 0,
        "actual_input_tokens": int(raw[0]),
        "actual_output_tokens": int(raw[1]),
        "embedding_weighted": int(raw[2]),
        "ai_available": available(ledger) > 0 and ctx.workspace.status == "active",
    }


@router.get("/daily")
async def daily(db: Db, days: int = 30, ctx: WorkspaceContext = require("usage.read")):
    days = max(1, min(days, 90))
    since = utcnow() - timedelta(days=days)
    day = cast(UsageEvent.created_at, Date)
    rows = (
        await db.execute(
            select(
                day,
                func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens),
                func.sum(UsageEvent.weighted_tokens),
            )
            .where(UsageEvent.workspace_id == ctx.workspace.id, UsageEvent.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
    ).all()
    return [{"date": d.isoformat(), "actual": int(a or 0), "weighted": int(w or 0)} for d, a, w in rows]


@router.get("/breakdown")
async def breakdown(db: Db, ctx: WorkspaceContext = require("usage.read")):
    ledger = await current_ledger(db, ctx.workspace.id)
    if ledger is None:
        return {"by_operation": [], "by_model": []}

    async def group(col):
        rows = (
            await db.execute(
                select(
                    col,
                    func.sum(UsageEvent.input_tokens),
                    func.sum(UsageEvent.output_tokens),
                    func.sum(UsageEvent.weighted_tokens),
                    func.count(),
                )
                .where(UsageEvent.ledger_id == ledger.id)
                .group_by(col)
                .order_by(func.sum(UsageEvent.weighted_tokens).desc())
            )
        ).all()
        return [
            {"key": k, "input_tokens": int(i or 0), "output_tokens": int(o or 0), "weighted": int(w or 0), "requests": n}
            for k, i, o, w, n in rows
        ]

    return {"by_operation": await group(UsageEvent.operation), "by_model": await group(UsageEvent.model)}


@router.get("/multipliers")
async def multipliers(db: Db, _: WorkspaceContext = require("usage.read")):
    """Published so customers can see exactly how weighted tokens are calculated."""
    rows = (await db.execute(select(ModelMultiplier).order_by(ModelMultiplier.model, ModelMultiplier.operation))).scalars()
    return [
        {"model": m.model, "operation": m.operation, "input_multiplier": m.input_multiplier, "output_multiplier": m.output_multiplier}
        for m in rows
    ]
