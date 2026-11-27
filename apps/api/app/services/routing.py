"""Ticket routing (Phase 5A): who a new ticket goes to.

Called from tickets.create(), so every source is routed the same way: email, chatbot forms, conversations
(through conversations.escalate()) and manual tickets. No AI is involved, so routing keeps working when the
token allowance is used up.

    1. The first enabled rule (by position) whose conditions all match decides: a team, or one person.
    2. Otherwise the team that handles the ticket's category, then the workspace's default team.
    3. Inside the team: members who can work tickets, are available and under their cap; those whose skills
       include the category first; then the fewest open tickets; ties go to whoever was assigned longest ago.
    4. Nobody qualifies: the ticket waits in the team's queue, unassigned.

Routed tickets stay New until the assignee opens or answers them. The workspace is locked for the
transaction, so tickets created at the same moment see each other's assignments and are balanced.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemberRouting, Membership, RoutingRule, Team, Ticket, User, Workspace, team_members
from app.models.base import utcnow
from app.services.audit import jsonable

OPEN = ("new", "open", "pending")
CONDITION_KEYS = ("sources", "category_ids", "priorities", "keywords", "requester_domains")


def routing_settings(ws: Workspace) -> dict:
    return {"enabled": False, "default_team_id": None, **((ws.settings or {}).get("routing") or {})}


@dataclass
class Decision:
    team: Team | None = None
    assignee: User | None = None
    rule: RoutingRule | None = None
    reason: str = ""


def matches(rule: RoutingRule, t: Ticket) -> bool:
    c = rule.conditions or {}
    if c.get("sources") and t.source not in c["sources"]:
        return False
    if c.get("category_ids") and str(t.category_id) not in [str(x) for x in c["category_ids"]]:
        return False
    if c.get("priorities") and t.priority not in c["priorities"]:
        return False
    if c.get("keywords"):
        hay = f"{t.subject}\n{t.description}".lower()
        if not any(k.lower() in hay for k in c["keywords"] if k.strip()):
            return False
    if c.get("requester_domains"):
        domain = (t.requester_email or "").rpartition("@")[2].lower()
        if not domain or not any(
            domain == d.lower().lstrip("@") or domain.endswith("." + d.lower().lstrip("@")) for d in c["requester_domains"]
        ):
            return False
    return True


async def candidates(db: AsyncSession, workspace_id: uuid.UUID, user_ids: list[uuid.UUID] | None) -> list[dict]:
    """Members who can be given tickets now, with their open-ticket count and routing profile."""
    q = select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == workspace_id)
    if user_ids is not None:
        if not user_ids:
            return []
        q = q.where(Membership.user_id.in_(user_ids))
    rows = [(m, u) for m, u in (await db.execute(q)).all() if "tickets.manage" in m.role.permissions and u.status == "active"]
    if not rows:
        return []
    ids = [u.id for _, u in rows]
    profiles = {
        p.user_id: p
        for p in (
            await db.execute(select(MemberRouting).where(MemberRouting.workspace_id == workspace_id, MemberRouting.user_id.in_(ids)))
        ).scalars()
    }
    counts = dict(
        (
            await db.execute(
                select(Ticket.assignee_id, func.count())
                .where(Ticket.workspace_id == workspace_id, Ticket.assignee_id.in_(ids), Ticket.status.in_(OPEN))
                .group_by(Ticket.assignee_id)
            )
        ).all()
    )
    out = []
    for _, u in rows:
        p = profiles.get(u.id)
        out.append(
            {
                "user": u,
                "open": counts.get(u.id, 0),
                "available": p is None or p.availability == "available",
                "max_open": p.max_open if p else None,
                "skills": [str(s) for s in (p.skills if p else [])],
                "last_assigned_at": p.last_assigned_at if p else None,
            }
        )
    return out


def _eligible(c: dict) -> bool:
    return c["available"] and (c["max_open"] is None or c["open"] < c["max_open"])


def pick(cands: list[dict], category_id) -> dict | None:
    ok = [c for c in cands if _eligible(c)]
    if not ok:
        return None
    cat = str(category_id) if category_id else None

    def key(c):
        skilled = 0 if cat and cat in c["skills"] else 1
        last = c["last_assigned_at"].timestamp() if c["last_assigned_at"] else 0.0
        return (skilled, c["open"], last, str(c["user"].id))

    return min(ok, key=key)


async def team_members_of(db: AsyncSession, team: Team) -> list[uuid.UUID]:
    return list((await db.execute(select(team_members.c.user_id).where(team_members.c.team_id == team.id))).scalars())


async def _team(db: AsyncSession, workspace_id: uuid.UUID, team_id) -> Team | None:
    if not team_id:
        return None
    try:
        tid = uuid.UUID(str(team_id))
    except ValueError:
        return None
    return (await db.execute(select(Team).where(Team.id == tid, Team.workspace_id == workspace_id))).scalar_one_or_none()


async def decide(db: AsyncSession, ws: Workspace, t: Ticket, *, use_rules: bool = True) -> Decision:
    settings = routing_settings(ws)
    team: Team | None = await _team(db, ws.id, t.team_id)  # a team chosen when the ticket was created
    rule_used: RoutingRule | None = None
    why = ""
    if team is None and use_rules:
        rules = (
            await db.execute(
                select(RoutingRule)
                .where(RoutingRule.workspace_id == ws.id, RoutingRule.enabled.is_(True))
                .order_by(RoutingRule.position, RoutingRule.created_at)
            )
        ).scalars()
        for rule in rules:
            if not matches(rule, t):
                continue
            action = rule.action or {}
            if action.get("assignee_id"):
                try:
                    uid = uuid.UUID(str(action["assignee_id"]))
                except ValueError:
                    continue
                cands = await candidates(db, ws.id, [uid])
                if cands and _eligible(cands[0]):
                    return Decision(assignee=cands[0]["user"], rule=rule, reason=f"rule “{rule.name}”")
                continue  # that person can't take it now: try the next rule
            team = await _team(db, ws.id, action.get("team_id"))
            if team is not None:
                rule_used, why = rule, f"rule “{rule.name}”"
                break
    if team is None and t.category_id:
        for candidate in (await db.execute(select(Team).where(Team.workspace_id == ws.id).order_by(Team.created_at))).scalars():
            if str(t.category_id) in [str(x) for x in candidate.category_ids or []]:
                team, why = candidate, "category"
                break
    if team is None:
        team = await _team(db, ws.id, settings.get("default_team_id"))
        why = "default team" if team else ""
    if team is None:
        return Decision(reason="no team matched")
    chosen = pick(await candidates(db, ws.id, await team_members_of(db, team)), t.category_id)
    if chosen is None:
        return Decision(team=team, rule=rule_used, reason=f"{why} · nobody in {team.name} is available")
    return Decision(team=team, assignee=chosen["user"], rule=rule_used, reason=f"{why} · least busy in {team.name} · {chosen['open']} open")


async def lock(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"routing:{workspace_id}"})


async def route(db: AsyncSession, t: Ticket, *, force: bool = False, actor_id: uuid.UUID | None = None) -> Decision | None:
    """Routes an unassigned ticket. Returns the decision, or None when routing is off (and not forced)."""
    from app.services import tickets as tk

    ws = await db.get(Workspace, t.workspace_id)
    if not force and not routing_settings(ws)["enabled"] and t.team_id is None:
        return None
    await lock(db, ws.id)
    d = await decide(db, ws, t, use_rules=True)
    now = utcnow()
    if d.team is not None:
        t.team_id = d.team.id
    if d.assignee is not None:
        t.assignee_id = d.assignee.id
        t.assigned_at = now
        await db.execute(
            pg_insert(MemberRouting)
            .values(id=uuid.uuid4(), workspace_id=ws.id, user_id=d.assignee.id, availability="available", skills=[], last_assigned_at=now)
            .on_conflict_do_update(index_elements=["workspace_id", "user_id"], set_={"last_assigned_at": now})
        )
    tk.add_event(
        db,
        t,
        "routed",
        author_id=actor_id,
        **jsonable(
            {
                "team_id": d.team.id if d.team else None,
                "team": d.team.name if d.team else None,
                "assignee_id": d.assignee.id if d.assignee else None,
                "assignee": d.assignee.full_name if d.assignee else None,
                "rule_id": d.rule.id if d.rule else None,
                "rule": d.rule.name if d.rule else None,
                "reason": d.reason,
            }
        ),
    )
    if d.assignee is not None and d.assignee.id != actor_id:
        tk.notify_assignee(db, t, d.assignee.id)
    return d
