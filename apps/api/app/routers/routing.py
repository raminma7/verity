"""Teams, routing rules, routing settings and members' availability (Phase 5A).

Teams, rules, settings and other members' profiles: tickets.configure (Owner and Administrator). Your own
availability: tickets.manage. Re-running auto-assign on a ticket: tickets.manage. Every query filters on
ctx.workspace.id; teams, rules, members and categories from another workspace answer 404.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.models import MemberRouting, Membership, RoutingRule, Team, Ticket, TicketCategory, User, team_members
from app.models.ticketing import TICKET_PRIORITIES, TICKET_SOURCES
from app.services import routing
from app.services import tickets as svc
from app.services.audit import audit

router = APIRouter(tags=["routing"])

CONFIGURE = require("tickets.configure", subscription=True)
WORK = require("tickets.manage", subscription=True)
READ = require("tickets.read", subscription=True)


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    member_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)


class TeamUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    category_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


class MembersIn(BaseModel):
    member_ids: list[uuid.UUID] = Field(max_length=500)


class ConditionsIn(BaseModel):
    sources: list[Literal["chatbot_form", "conversation", "manual", "email"]] = Field(default_factory=list)
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    priorities: list[Literal["low", "normal", "high", "urgent"]] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    requester_domains: list[str] = Field(default_factory=list, max_length=50)


class ActionIn(BaseModel):
    team_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    conditions: ConditionsIn = Field(default_factory=ConditionsIn)
    action: ActionIn


class RuleUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    conditions: ConditionsIn | None = None
    action: ActionIn | None = None


class OrderIn(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=500)


class SettingsIn(BaseModel):
    enabled: bool
    default_team_id: uuid.UUID | None = None


class ProfileIn(BaseModel):
    availability: Literal["available", "away"] | None = None
    max_open: int | None = Field(default=None, ge=1, le=1000)
    clear_max_open: bool = False
    skills: list[uuid.UUID] | None = Field(default=None, max_length=100)


class AvailabilityIn(BaseModel):
    availability: Literal["available", "away"]


# ---- Helpers -------------------------------------------------------------------------------------


async def _team(db: AsyncSession, ctx: WorkspaceContext, team_id: uuid.UUID) -> Team:
    t = (await db.execute(select(Team).where(Team.id == team_id, Team.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if t is None:
        raise not_found("Team")
    return t


async def _rule(db: AsyncSession, ctx: WorkspaceContext, rule_id: uuid.UUID) -> RoutingRule:
    r = (
        await db.execute(select(RoutingRule).where(RoutingRule.id == rule_id, RoutingRule.workspace_id == ctx.workspace.id))
    ).scalar_one_or_none()
    if r is None:
        raise not_found("Rule")
    return r


async def _check_categories(db: AsyncSession, ws_id: uuid.UUID, ids: list[uuid.UUID], field: str) -> list[str]:
    ids = list(dict.fromkeys(ids))
    if ids:
        found = (
            await db.execute(
                select(func.count()).select_from(TicketCategory).where(TicketCategory.workspace_id == ws_id, TicketCategory.id.in_(ids))
            )
        ).scalar_one()
        if found != len(ids):
            raise ApiError(404, "category_not_found", "Category not found.", field=field)
    return [str(i) for i in ids]


async def _check_members(db: AsyncSession, ws_id: uuid.UUID, ids: list[uuid.UUID], field: str = "member_ids") -> list[uuid.UUID]:
    """Team members must be members whose role can work tickets."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    rows = (await db.execute(select(Membership).where(Membership.workspace_id == ws_id, Membership.user_id.in_(ids)))).scalars().all()
    if len(rows) != len(ids):
        raise ApiError(404, "member_not_found", "That person isn't a member of this workspace.", field=field)
    if any("tickets.manage" not in m.role.permissions for m in rows):
        raise ApiError(422, "member_cannot_work_tickets", "Only members whose role can work tickets can join a team.", field=field)
    return ids


async def _team_out(db: AsyncSession, t: Team) -> dict:
    members = (
        await db.execute(
            select(User.id, User.full_name, User.email)
            .join(team_members, team_members.c.user_id == User.id)
            .where(team_members.c.team_id == t.id)
            .order_by(User.full_name)
        )
    ).all()
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "category_ids": t.category_ids,
        "members": [{"id": m.id, "name": m.full_name, "email": m.email} for m in members],
        "created_at": t.created_at,
    }


def _rule_out(r: RoutingRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "position": r.position,
        "enabled": r.enabled,
        "conditions": r.conditions,
        "action": r.action,
        "created_at": r.created_at,
    }


async def _clean_rule(db: AsyncSession, ctx: WorkspaceContext, conditions: ConditionsIn, action: ActionIn) -> tuple[dict, dict]:
    ws_id = ctx.workspace.id
    if bool(action.team_id) == bool(action.assignee_id):
        raise ApiError(422, "rule_action_required", "Choose a team or one person for this rule.", field="action")
    if action.team_id:
        await _team(db, ctx, action.team_id)
        act = {"team_id": str(action.team_id)}
    else:
        await svc.check_assignee(db, ws_id, action.assignee_id)
        act = {"assignee_id": str(action.assignee_id)}
    cond = {
        "sources": [s for s in dict.fromkeys(conditions.sources) if s in TICKET_SOURCES],
        "category_ids": await _check_categories(db, ws_id, conditions.category_ids, "conditions.category_ids"),
        "priorities": [p for p in dict.fromkeys(conditions.priorities) if p in TICKET_PRIORITIES],
        "keywords": [k.strip()[:80] for k in conditions.keywords if k.strip()],
        "requester_domains": [d.strip().lower().lstrip("@")[:255] for d in conditions.requester_domains if d.strip()],
    }
    return cond, act


async def _set_members(db: AsyncSession, team: Team, ids: list[uuid.UUID]) -> None:
    await db.execute(delete(team_members).where(team_members.c.team_id == team.id))
    if ids:
        await db.execute(insert(team_members), [{"team_id": team.id, "user_id": i} for i in ids])


# ---- Teams ---------------------------------------------------------------------------------------


@router.get("/teams")
async def list_teams(db: Db, ctx: WorkspaceContext = READ):
    rows = (await db.execute(select(Team).where(Team.workspace_id == ctx.workspace.id).order_by(Team.name))).scalars().all()
    return {"items": [await _team_out(db, t) for t in rows]}


@router.post("/teams", status_code=201)
async def create_team(body: TeamIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    ws_id = ctx.workspace.id
    name = " ".join(body.name.split())
    if (await db.execute(select(Team.id).where(Team.workspace_id == ws_id, func.lower(Team.name) == name.lower()))).first():
        raise ApiError(409, "team_name_taken", "Another team already has this name.", field="name")
    cats = await _check_categories(db, ws_id, body.category_ids, "category_ids")
    members = await _check_members(db, ws_id, body.member_ids or [])
    t = Team(workspace_id=ws_id, name=name, description=body.description.strip(), category_ids=cats)
    db.add(t)
    await db.flush()
    await _set_members(db, t, members)
    audit(
        db, "team.created", actor_id=ctx.user.id, workspace_id=ws_id, target_type="team", target_id=t.id, ip=client_ip(request), name=name
    )
    await db.commit()
    return await _team_out(db, t)


@router.patch("/teams/{team_id}")
async def update_team(team_id: uuid.UUID, body: TeamUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    t = await _team(db, ctx, team_id)
    if body.name is not None:
        name = " ".join(body.name.split())
        clash = (
            await db.execute(
                select(Team.id).where(Team.workspace_id == ctx.workspace.id, func.lower(Team.name) == name.lower(), Team.id != t.id)
            )
        ).first()
        if clash:
            raise ApiError(409, "team_name_taken", "Another team already has this name.", field="name")
        t.name = name
    if body.description is not None:
        t.description = body.description.strip()
    if body.category_ids is not None:
        t.category_ids = await _check_categories(db, ctx.workspace.id, body.category_ids, "category_ids")
    audit(
        db,
        "team.updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="team",
        target_id=t.id,
        ip=client_ip(request),
        fields=sorted(body.model_dump(exclude_none=True)),
    )
    await db.commit()
    return await _team_out(db, t)


@router.put("/teams/{team_id}/members")
async def set_team_members(team_id: uuid.UUID, body: MembersIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    t = await _team(db, ctx, team_id)
    ids = await _check_members(db, ctx.workspace.id, body.member_ids)
    await _set_members(db, t, ids)
    audit(
        db,
        "team.members_updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="team",
        target_id=t.id,
        ip=client_ip(request),
        member_ids=ids,
    )
    await db.commit()
    return await _team_out(db, t)


@router.delete("/teams/{team_id}", status_code=204)
async def delete_team(team_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    t = await _team(db, ctx, team_id)
    used = (
        (
            await db.execute(
                select(RoutingRule.name).where(
                    RoutingRule.workspace_id == ctx.workspace.id, RoutingRule.action["team_id"].astext == str(t.id)
                )
            )
        )
        .scalars()
        .first()
    )
    if used:
        raise ApiError(409, "team_in_use", f"The rule “{used}” sends tickets to this team. Change or delete it first.")
    ws = ctx.workspace
    settings = routing.routing_settings(ws)
    if settings.get("default_team_id") == str(t.id):
        ws.settings = {**(ws.settings or {}), "routing": {**settings, "default_team_id": None}}
    audit(
        db, "team.deleted", actor_id=ctx.user.id, workspace_id=ws.id, target_type="team", target_id=t.id, ip=client_ip(request), name=t.name
    )
    await db.delete(t)
    await db.commit()


# ---- Rules ---------------------------------------------------------------------------------------


@router.get("/routing/rules")
async def list_rules(db: Db, ctx: WorkspaceContext = CONFIGURE):
    rows = (
        await db.execute(
            select(RoutingRule).where(RoutingRule.workspace_id == ctx.workspace.id).order_by(RoutingRule.position, RoutingRule.created_at)
        )
    ).scalars()
    return {"items": [_rule_out(r) for r in rows]}


@router.post("/routing/rules", status_code=201)
async def create_rule(body: RuleIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    cond, act = await _clean_rule(db, ctx, body.conditions, body.action)
    last = (await db.execute(select(func.max(RoutingRule.position)).where(RoutingRule.workspace_id == ctx.workspace.id))).scalar_one()
    r = RoutingRule(
        workspace_id=ctx.workspace.id, name=body.name.strip(), enabled=body.enabled, conditions=cond, action=act, position=(last or 0) + 1
    )
    db.add(r)
    await db.flush()
    audit(
        db,
        "routing_rule.created",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="routing_rule",
        target_id=r.id,
        ip=client_ip(request),
    )
    await db.commit()
    return _rule_out(r)


@router.patch("/routing/rules/{rule_id}")
async def update_rule(rule_id: uuid.UUID, body: RuleUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    r = await _rule(db, ctx, rule_id)
    if body.conditions is not None or body.action is not None:
        cond_in = body.conditions or ConditionsIn(**{k: v for k, v in (r.conditions or {}).items() if k in ConditionsIn.model_fields})
        act_in = body.action or ActionIn(**(r.action or {}))
        r.conditions, r.action = await _clean_rule(db, ctx, cond_in, act_in)
    if body.name is not None:
        r.name = body.name.strip()
    if body.enabled is not None:
        r.enabled = body.enabled
    audit(
        db,
        "routing_rule.updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="routing_rule",
        target_id=r.id,
        ip=client_ip(request),
        fields=sorted(body.model_dump(exclude_none=True)),
    )
    await db.commit()
    return _rule_out(r)


@router.put("/routing/rules/order")
async def order_rules(body: OrderIn, db: Db, ctx: WorkspaceContext = CONFIGURE):
    rows = {r.id: r for r in (await db.execute(select(RoutingRule).where(RoutingRule.workspace_id == ctx.workspace.id))).scalars()}
    if set(body.ids) != set(rows) or len(body.ids) != len(rows):
        raise ApiError(422, "invalid_order", "Send every rule exactly once.", field="ids")
    for i, rid in enumerate(body.ids):
        rows[rid].position = i + 1
    audit(
        db,
        "routing_rule.reordered",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="workspace",
        target_id=ctx.workspace.id,
    )
    await db.commit()
    return {"items": [_rule_out(rows[i]) for i in body.ids]}


@router.delete("/routing/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    r = await _rule(db, ctx, rule_id)
    audit(
        db,
        "routing_rule.deleted",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="routing_rule",
        target_id=r.id,
        ip=client_ip(request),
        name=r.name,
    )
    await db.delete(r)
    await db.commit()


# ---- Settings and members ------------------------------------------------------------------------


@router.get("/routing/settings")
async def get_settings_(db: Db, ctx: WorkspaceContext = CONFIGURE):
    return routing.routing_settings(ctx.workspace)


@router.put("/routing/settings")
async def put_settings(body: SettingsIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    if body.default_team_id:
        await _team(db, ctx, body.default_team_id)
    ws = ctx.workspace
    value = {"enabled": body.enabled, "default_team_id": str(body.default_team_id) if body.default_team_id else None}
    ws.settings = {**(ws.settings or {}), "routing": value}
    audit(
        db,
        "routing.settings_updated",
        actor_id=ctx.user.id,
        workspace_id=ws.id,
        target_type="workspace",
        target_id=ws.id,
        ip=client_ip(request),
        **value,
    )
    await db.commit()
    return value


async def _profiles(db: AsyncSession, ctx: WorkspaceContext) -> list[dict]:
    ws_id = ctx.workspace.id
    cands = {c["user"].id: c for c in await routing.candidates(db, ws_id, None)}
    teams: dict = {}
    for team_id, user_id, name in (
        await db.execute(
            select(team_members.c.team_id, team_members.c.user_id, Team.name)
            .join(Team, Team.id == team_members.c.team_id)
            .where(Team.workspace_id == ws_id)
        )
    ).all():
        teams.setdefault(user_id, []).append({"id": team_id, "name": name})
    out = []
    for c in sorted(cands.values(), key=lambda c: c["user"].full_name.lower()):
        u = c["user"]
        out.append(
            {
                "user_id": u.id,
                "name": u.full_name,
                "email": u.email,
                "availability": "available" if c["available"] else "away",
                "max_open": c["max_open"],
                "skills": c["skills"],
                "open_tickets": c["open"],
                "teams": teams.get(u.id, []),
                "last_assigned_at": c["last_assigned_at"],
            }
        )
    return out


@router.get("/routing/members")
async def list_profiles(db: Db, ctx: WorkspaceContext = READ):
    return {"items": await _profiles(db, ctx)}


async def _save_profile(db: AsyncSession, ws_id: uuid.UUID, user_id: uuid.UUID, values: dict) -> None:
    await db.execute(
        pg_insert(MemberRouting)
        .values(id=uuid.uuid4(), workspace_id=ws_id, user_id=user_id, **{"availability": "available", "skills": [], **values})
        .on_conflict_do_update(index_elements=["workspace_id", "user_id"], set_=values)
    )


@router.put("/routing/members/{user_id}")
async def put_profile(user_id: uuid.UUID, body: ProfileIn, request: Request, db: Db, ctx: WorkspaceContext = CONFIGURE):
    await _check_members(db, ctx.workspace.id, [user_id], field="user_id")
    values: dict = {}
    if body.availability is not None:
        values["availability"] = body.availability
    if body.clear_max_open:
        values["max_open"] = None
    elif body.max_open is not None:
        values["max_open"] = body.max_open
    if body.skills is not None:
        values["skills"] = await _check_categories(db, ctx.workspace.id, body.skills, "skills")
    if values:
        await _save_profile(db, ctx.workspace.id, user_id, values)
        audit(
            db,
            "member.routing_updated",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="user",
            target_id=user_id,
            ip=client_ip(request),
            **values,
        )
        await db.commit()
    return next(p for p in await _profiles(db, ctx) if p["user_id"] == user_id)


@router.get("/routing/me")
async def get_me(db: Db, ctx: WorkspaceContext = WORK):
    p = (
        await db.execute(select(MemberRouting).where(MemberRouting.workspace_id == ctx.workspace.id, MemberRouting.user_id == ctx.user.id))
    ).scalar_one_or_none()
    return {"availability": p.availability if p else "available"}


@router.put("/routing/me")
async def put_me(body: AvailabilityIn, request: Request, db: Db, ctx: WorkspaceContext = WORK):
    await _save_profile(db, ctx.workspace.id, ctx.user.id, {"availability": body.availability})
    audit(
        db,
        "member.availability_changed",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="user",
        target_id=ctx.user.id,
        ip=client_ip(request),
        availability=body.availability,
    )
    await db.commit()
    return {"availability": body.availability}


@router.post("/tickets/{ticket_id}/route")
async def route_ticket(ticket_id: uuid.UUID, db: Db, ctx: WorkspaceContext = WORK):
    """Auto-assign: runs routing for an unassigned ticket now, even when automatic routing is off."""
    t = (await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if t is None:
        raise not_found("Ticket")
    if t.assignee_id is not None:
        raise ApiError(409, "ticket_assigned", "This ticket already has an assignee. Unassign it first.")
    d = await routing.route(db, t, force=True, actor_id=ctx.user.id)
    audit(
        db,
        "ticket.routed",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="ticket",
        target_id=t.id,
        assignee_id=t.assignee_id,
    )
    await db.commit()
    return {"assignee_id": t.assignee_id, "team_id": t.team_id, "reason": d.reason if d else ""}
