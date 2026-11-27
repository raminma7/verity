"""Support analytics for workspace owners and admins (`analytics.read`), SLA targets, and platform reports for
system administrators. Ranges are calendar days in the viewer's time zone (see `services/analytics`)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.core.deps import Db, SystemAdmin, WorkspaceContext, client_ip, require
from app.services import analytics as an
from app.services.audit import audit

router = APIRouter(tags=["analytics"])


class RangeIn:
    """?range=7d|30d|90d|custom&from=YYYY-MM-DD&to=YYYY-MM-DD (custom ranges are local days, up to a year)."""

    def __init__(
        self,
        preset: str = Query("30d", alias="range", pattern=r"^(7d|30d|90d|custom)$"),
        start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"),
    ):
        self.preset, self.start, self.end = preset, start, end

    def for_user(self, user) -> an.Range:
        return an.parse_range(self.preset, user.timezone, self.start, self.end)


R = Annotated[RangeIn, Depends()]


@router.get("/analytics/tickets")
async def tickets(db: Db, r: R, ctx: WorkspaceContext = require("analytics.read")):
    return await an.tickets(db, ctx.workspace.id, r.for_user(ctx.user))


@router.get("/analytics/sla")
async def sla(db: Db, r: R, ctx: WorkspaceContext = require("analytics.read")):
    return await an.sla(db, ctx.workspace.id, r.for_user(ctx.user), ctx.workspace.settings)


@router.get("/analytics/agents")
async def agents(db: Db, r: R, ctx: WorkspaceContext = require("analytics.read")):
    return await an.agents(db, ctx.workspace.id, r.for_user(ctx.user))


@router.get("/analytics/chatbot")
async def chatbot(db: Db, r: R, ctx: WorkspaceContext = require("analytics.read")):
    return await an.chatbot(db, ctx.workspace.id, r.for_user(ctx.user))


@router.get("/analytics/tools")
async def tools(db: Db, r: R, ctx: WorkspaceContext = require("analytics.read")):
    return await an.tools(db, ctx.workspace.id, r.for_user(ctx.user))


@router.get("/analytics/email")
async def email(db: Db, r: R, ctx: WorkspaceContext = require("analytics.read")):
    return await an.email(db, ctx.workspace.id, r.for_user(ctx.user))


@router.get("/analytics/ai")
async def ai(db: Db, r: R, ctx: WorkspaceContext = require("analytics.read")):
    return await an.ai(db, ctx.workspace.id, r.for_user(ctx.user))


# ---- SLA targets (Settings → Tickets) ----------------------------------------------------------------------


@router.get("/sla-targets")
async def get_sla_targets(ctx: WorkspaceContext = require("tickets.read")):
    return {"targets": an.sla_targets(ctx.workspace.settings), "defaults": an.SLA_DEFAULTS, "can_edit": ctx.can("tickets.configure")}


class SlaIn(BaseModel):
    targets: dict[str, dict[str, int]]


@router.put("/sla-targets")
async def put_sla_targets(body: SlaIn, request: Request, db: Db, ctx: WorkspaceContext = require("tickets.configure")):
    targets = an.check_sla_targets(body.targets)
    ws = ctx.workspace
    ws.settings = {**(ws.settings or {}), "sla": targets}
    audit(
        db,
        "tickets.sla_updated",
        actor_id=ctx.user.id,
        workspace_id=ws.id,
        target_type="workspace",
        target_id=ws.id,
        ip=client_ip(request),
        targets=targets,
    )
    await db.commit()
    return {"targets": targets}


# ---- Platform reports (system administrators) ----------------------------------------------------------------


@router.get("/admin/analytics/usage")
async def platform_usage(admin: SystemAdmin, db: Db, r: R):
    return await an.platform_usage(db, r.for_user(admin))


@router.get("/admin/analytics/costs")
async def platform_costs(admin: SystemAdmin, db: Db, r: R):
    return await an.platform_costs(db, r.for_user(admin))


@router.get("/admin/analytics/subscriptions")
async def platform_subscriptions(admin: SystemAdmin, db: Db, r: R):
    return await an.platform_subscriptions(db, r.for_user(admin))


@router.get("/admin/analytics/revenue")
async def platform_revenue(admin: SystemAdmin, db: Db, r: R):
    return await an.platform_revenue(db, r.for_user(admin))


@router.get("/admin/analytics/failed-payments")
async def platform_failed_payments(admin: SystemAdmin, db: Db, r: R):
    return await an.platform_failed_payments(db, r.for_user(admin))
