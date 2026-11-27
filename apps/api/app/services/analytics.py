"""Support analytics for a workspace (owners and admins) and platform reports for system administrators.

Ranges are local calendar days in the viewer's time zone: the last 7, 30 or 90 days including today, or a custom
range of up to a year. Buckets are local days, or ISO weeks (starting Monday) above 90 days. Every series has
one point per bucket, zeros included, and every report carries the same-length period before it for deltas.

Everything is computed from what the product already records (tickets and their timeline, conversations and
messages, API tool calls, incoming email, metered usage, invoices), at query time: changing an SLA target
recomputes its report. Money is reported per currency and never added across currencies.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.base import utcnow

PRESETS = {"7d": 7, "30d": 30, "90d": 90}
MAX_DAYS = 366
WEEKLY_ABOVE = 90
OPEN_STATUSES = ("new", "open", "pending")

# Wall-clock minutes. Workspaces change them under Settings → Tickets.
SLA_DEFAULTS = {
    "urgent": {"first_response_minutes": 60, "resolution_minutes": 8 * 60},
    "high": {"first_response_minutes": 4 * 60, "resolution_minutes": 24 * 60},
    "normal": {"first_response_minutes": 8 * 60, "resolution_minutes": 3 * 24 * 60},
    "low": {"first_response_minutes": 24 * 60, "resolution_minutes": 7 * 24 * 60},
}
PRIORITIES = ("urgent", "high", "normal", "low")


def sla_targets(settings: dict | None) -> dict:
    saved = (settings or {}).get("sla") or {}
    return {p: {**SLA_DEFAULTS[p], **(saved.get(p) or {})} for p in PRIORITIES}


def check_sla_targets(body: dict) -> dict:
    out = {}
    for p in PRIORITIES:
        t = body.get(p) or {}
        frt, res = t.get("first_response_minutes"), t.get("resolution_minutes")
        for name, v in (("first_response_minutes", frt), ("resolution_minutes", res)):
            if not isinstance(v, int) or isinstance(v, bool) or not 5 <= v <= 60 * 24 * 90:
                raise ApiError(422, "invalid_sla", "Targets are whole minutes between 5 minutes and 90 days.", field=f"{p}.{name}")
        if res < frt:
            raise ApiError(
                422, "invalid_sla", "A resolution target can't be shorter than its first-response target.", field=f"{p}.resolution_minutes"
            )
        out[p] = {"first_response_minutes": frt, "resolution_minutes": res}
    return out


@dataclass
class Range:
    tz: str
    first: date  # first local day (inclusive)
    last: date  # last local day (inclusive)
    start: datetime  # UTC
    end: datetime  # UTC, exclusive
    bucket: str  # day | week
    preset: str

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def previous(self) -> "Range":
        last = self.first - timedelta(days=1)
        first = last - timedelta(days=self.days - 1)
        return make_range(self.tz, first, last, self.preset, bucket=self.bucket)

    def labels(self) -> list[date]:
        d = self.first if self.bucket == "day" else self.first - timedelta(days=self.first.weekday())
        step = timedelta(days=1 if self.bucket == "day" else 7)
        out = []
        while d <= self.last:
            out.append(d)
            d += step
        return out

    def out(self) -> dict:
        return {
            "preset": self.preset,
            "from": self.first.isoformat(),
            "to": self.last.isoformat(),
            "days": self.days,
            "tz": self.tz,
            "bucket": self.bucket,
        }

    def params(self) -> dict:
        return {"start": self.start, "end": self.end, "tz": self.tz, "bucket": self.bucket}


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def make_range(tz: str, first: date, last: date, preset: str, *, bucket: str | None = None) -> Range:
    z = _zone(tz)
    start = datetime(first.year, first.month, first.day, tzinfo=z)
    after = last + timedelta(days=1)
    end = datetime(after.year, after.month, after.day, tzinfo=z)
    days = (last - first).days + 1
    return Range(
        tz=z.key,
        first=first,
        last=last,
        start=start.astimezone(ZoneInfo("UTC")),
        end=end.astimezone(ZoneInfo("UTC")),
        bucket=bucket or ("week" if days > WEEKLY_ABOVE else "day"),
        preset=preset,
    )


def parse_range(preset: str, tz: str | None, start: str | None = None, end: str | None = None, *, now: datetime | None = None) -> Range:
    z = _zone(tz)
    today = (now or utcnow()).astimezone(z).date()
    if preset in PRESETS:
        return make_range(z.key, today - timedelta(days=PRESETS[preset] - 1), today, preset)
    if preset != "custom":
        raise ApiError(422, "invalid_range", "Choose 7, 30 or 90 days, or a custom range.", field="range")
    try:
        first, last = date.fromisoformat(start or ""), date.fromisoformat(end or "")
    except ValueError:
        raise ApiError(422, "invalid_range", "Give the custom range's first and last day (YYYY-MM-DD).", field="from") from None
    if first > last:
        raise ApiError(422, "invalid_range", "The range starts after it ends.", field="from")
    if last > today:
        raise ApiError(422, "invalid_range", "The range can't end in the future.", field="to")
    if (last - first).days + 1 > MAX_DAYS:
        raise ApiError(422, "invalid_range", "A custom range covers at most one year.", field="from")
    return make_range(z.key, first, last, "custom")


# ---- helpers ----------------------------------------------------------------------------------------------

BUCKET = "(date_trunc(:bucket, ({col} AT TIME ZONE :tz)))::date"


def _b(col: str) -> str:
    return BUCKET.format(col=col)


def _series(rng: Range, rows, keys: list[str] | None = None, *, missing=0) -> list[dict]:
    """rows: (bucket date, key, value) → one point per bucket with a value per key (`missing` when there is none:
    0 for counts, None for medians, where an empty bucket has no value rather than a zero)."""
    by: dict[date, dict] = {d: {} for d in rng.labels()}
    found = []
    for d, k, v in rows:
        if d in by:
            by[d][k] = (by[d].get(k) or 0) + (v or 0)
            if k not in found:
                found.append(k)
    keys = keys or found
    return [{"date": d.isoformat(), **{k: _num(vals[k]) if k in vals else missing for k in keys}} for d, vals in by.items()]


def _num(v):
    if v is None:
        return 0
    if isinstance(v, float):
        return round(v, 2)
    return int(v) if hasattr(v, "__int__") and not isinstance(v, bool) else v


def _minutes(seconds) -> float | None:
    return None if seconds is None else round(float(seconds) / 60, 1)


async def _all(db: AsyncSession, sql: str, params: dict):
    return (await db.execute(text(sql), params)).all()


async def _one(db: AsyncSession, sql: str, params: dict):
    return (await db.execute(text(sql), params)).one()


def _rate(n, d) -> float | None:
    return round(n * 100 / d, 1) if d else None


# ---- tickets ------------------------------------------------------------------------------------------------

REOPEN_EVENTS = """(kind = 'reopened' OR (kind = 'status' AND data->>'from' IN ('resolved', 'closed') AND data->>'to' = 'open'))"""
RESOLVE_EVENTS = """(kind = 'status' AND data->>'to' IN ('resolved', 'closed') AND data->>'from' NOT IN ('resolved', 'closed'))"""


async def _ticket_totals(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    p = {"ws": ws, **rng.params()}
    created = await _one(
        db,
        "SELECT count(*), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM first_response_at - created_at)) "
        "FILTER (WHERE first_response_at IS NOT NULL), "
        "percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM first_response_at - created_at)) "
        "FILTER (WHERE first_response_at IS NOT NULL), "
        "count(*) FILTER (WHERE first_response_at IS NOT NULL) "
        "FROM tickets WHERE workspace_id = :ws AND created_at >= :start AND created_at < :end",
        p,
    )
    resolved = await _one(
        db,
        "SELECT count(*), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM resolved_at - created_at)), "
        "percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM resolved_at - created_at)) "
        "FROM tickets WHERE workspace_id = :ws AND resolved_at >= :start AND resolved_at < :end",
        p,
    )
    events = await _one(
        db,
        f"SELECT count(*) FILTER (WHERE {REOPEN_EVENTS}), count(*) FILTER (WHERE {RESOLVE_EVENTS}) "
        "FROM ticket_events WHERE workspace_id = :ws AND kind IN ('reopened', 'status') AND created_at >= :start AND created_at < :end",
        p,
    )
    return {
        "created": int(created[0]),
        "responded": int(created[3]),
        "first_response_median_minutes": _minutes(created[1]),
        "first_response_p90_minutes": _minutes(created[2]),
        "resolved": int(resolved[0]),
        "resolution_median_minutes": _minutes(resolved[1]),
        "resolution_p90_minutes": _minutes(resolved[2]),
        "reopened": int(events[0]),
        "resolutions": int(events[1]),
        "reopen_rate": _rate(int(events[0]), int(events[1])),
    }


async def tickets(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    p = {"ws": ws, **rng.params()}
    by_source = await _all(
        db,
        f"SELECT {_b('created_at')} AS d, source, count(*) FROM tickets "
        "WHERE workspace_id = :ws AND created_at >= :start AND created_at < :end GROUP BY 1, 2",
        p,
    )
    cats = await _all(
        db,
        "SELECT coalesce(c.name, 'No category'), count(*) FROM tickets t LEFT JOIN ticket_categories c ON c.id = t.category_id "
        "WHERE t.workspace_id = :ws AND t.created_at >= :start AND t.created_at < :end GROUP BY 1 ORDER BY 2 DESC, 1",
        p,
    )
    top = [c for c, _ in cats[:6]]
    by_category = await _all(
        db,
        f"SELECT {_b('t.created_at')} AS d, coalesce(c.name, 'No category'), count(*) FROM tickets t "
        "LEFT JOIN ticket_categories c ON c.id = t.category_id "
        "WHERE t.workspace_id = :ws AND t.created_at >= :start AND t.created_at < :end GROUP BY 1, 2",
        p,
    )
    by_category = [(d, k if k in top else "Other", n) for d, k, n in by_category]
    times = await _all(
        db,
        f"SELECT {_b('created_at')} AS d, 'first_response', "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM first_response_at - created_at)) / 60 "
        "FROM tickets WHERE workspace_id = :ws AND created_at >= :start AND created_at < :end AND first_response_at IS "
        "NOT NULL GROUP BY 1 "
        f"UNION ALL SELECT {_b('resolved_at')} AS d, 'resolution', "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM resolved_at - created_at)) / 60 "
        "FROM tickets WHERE workspace_id = :ws AND resolved_at >= :start AND resolved_at < :end GROUP BY 1",
        p,
    )
    reopen_series = await _all(
        db,
        f"SELECT {_b('created_at')} AS d, CASE WHEN {REOPEN_EVENTS} THEN 'reopened' ELSE 'resolved' END, count(*) "
        f"FROM ticket_events WHERE workspace_id = :ws AND created_at >= :start AND created_at < :end AND ({REOPEN_EVENTS} "
        f"OR {RESOLVE_EVENTS}) "
        "GROUP BY 1, 2",
        p,
    )
    sources = ["email", "chatbot_form", "conversation", "manual"]
    return {
        "range": rng.out(),
        "totals": await _ticket_totals(db, ws, rng),
        "previous": await _ticket_totals(db, ws, rng.previous()),
        "by_source": _series(rng, by_source, sources),
        "source_totals": {s: sum(n for _, k, n in by_source if k == s) for s in sources},
        "by_category": _series(rng, by_category, top + (["Other"] if len(cats) > 6 else [])),
        "category_totals": [{"name": c, "count": int(n)} for c, n in cats],
        "response_times": _series(rng, times, ["first_response", "resolution"], missing=None),
        "reopens": _series(rng, reopen_series, ["resolved", "reopened"]),
    }


# ---- SLA ----------------------------------------------------------------------------------------------------


def _targets_cte(targets: dict) -> tuple[str, dict]:
    rows, params = [], {}
    for i, p in enumerate(PRIORITIES):
        rows.append(f"(:p{i}, CAST(:f{i} AS int), CAST(:r{i} AS int))")
        params |= {f"p{i}": p, f"f{i}": targets[p]["first_response_minutes"], f"r{i}": targets[p]["resolution_minutes"]}
    return "targets(priority, frt, res) AS (VALUES " + ", ".join(rows) + ")", params


async def sla(db: AsyncSession, ws: uuid.UUID, rng: Range, settings: dict | None) -> dict:
    targets = sla_targets(settings)
    cte, tp = _targets_cte(targets)
    now = utcnow()
    p = {"ws": ws, "now": now, **rng.params(), **tp}
    # Response: met (answered in time), breached (answered late, or unanswered past the target), pending (still in time).
    rows = await _all(
        db,
        f"WITH {cte} SELECT t.priority, count(*), "
        "count(*) FILTER (WHERE t.first_response_at IS NOT NULL AND t.first_response_at - t.created_at <= make_interval(mins => g.frt)), "
        "count(*) FILTER (WHERE coalesce(t.first_response_at, :now) - t.created_at > make_interval(mins => g.frt)), "
        "count(*) FILTER (WHERE t.resolved_at IS NOT NULL AND t.resolved_at - t.created_at <= make_interval(mins => g.res)), "
        "count(*) FILTER (WHERE coalesce(t.resolved_at, :now) - t.created_at > make_interval(mins => g.res)) "
        "FROM tickets t JOIN targets g ON g.priority = t.priority "
        "WHERE t.workspace_id = :ws AND t.created_at >= :start AND t.created_at < :end GROUP BY t.priority",
        p,
    )
    found = {r[0]: r for r in rows}
    per = []
    for pr in PRIORITIES:
        r = found.get(pr)
        total, frt_met, frt_breached, res_met, res_breached = (int(x) for x in (r[1:] if r else (0, 0, 0, 0, 0)))
        per.append(
            {
                "priority": pr,
                "targets": targets[pr],
                "tickets": total,
                "response_met": frt_met,
                "response_breached": frt_breached,
                "response_pending": total - frt_met - frt_breached,
                "resolution_met": res_met,
                "resolution_breached": res_breached,
                "resolution_pending": total - res_met - res_breached,
                "response_met_rate": _rate(frt_met, frt_met + frt_breached),
                "resolution_met_rate": _rate(res_met, res_met + res_breached),
            }
        )
    series = await _all(
        db,
        f"WITH {cte} SELECT {_b('t.created_at')} AS d, k, count(*) FROM tickets t JOIN targets g ON g.priority = t.priority, "
        "LATERAL (VALUES ('response_breached', coalesce(t.first_response_at, :now) - t.created_at > make_interval(mins => g.frt)), "
        "('resolution_breached', coalesce(t.resolved_at, :now) - t.created_at > make_interval(mins => g.res))) AS b(k, hit) "
        "WHERE t.workspace_id = :ws AND t.created_at >= :start AND t.created_at < :end AND b.hit GROUP BY 1, 2",
        p,
    )
    breaching = await _all(
        db,
        f"WITH {cte} SELECT t.id, t.number, t.subject, t.priority, t.status, t.created_at, "
        "CASE WHEN t.first_response_at IS NULL AND :now - t.created_at > make_interval(mins => g.frt) THEN 'first_response' "
        "ELSE 'resolution' END AS target, "
        "extract(epoch FROM :now - t.created_at) / 60 - CASE WHEN t.first_response_at IS NULL "
        "AND :now - t.created_at > make_interval(mins => g.frt) THEN g.frt ELSE g.res END AS overdue, u.full_name "
        "FROM tickets t JOIN targets g ON g.priority = t.priority LEFT JOIN users u ON u.id = t.assignee_id "
        "WHERE t.workspace_id = :ws AND t.status IN ('new', 'open', 'pending') AND ("
        "(t.first_response_at IS NULL AND :now - t.created_at > make_interval(mins => g.frt)) "
        "OR :now - t.created_at > make_interval(mins => g.res)) ORDER BY overdue DESC LIMIT 10",
        p,
    )
    total_breached = sum(x["response_breached"] for x in per) + sum(x["resolution_breached"] for x in per)
    return {
        "range": rng.out(),
        "targets": targets,
        "by_priority": per,
        "breaches": total_breached,
        "series": _series(rng, series, ["response_breached", "resolution_breached"]),
        "breaching_now": [
            {
                "id": r[0],
                "number": r[1],
                "subject": r[2],
                "priority": r[3],
                "status": r[4],
                "created_at": r[5],
                "target": r[6],
                "overdue_minutes": round(float(r[7]), 1),
                "assignee": r[8],
            }
            for r in breaching
        ],
    }


# ---- agents -------------------------------------------------------------------------------------------------


async def agents(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    p = {"ws": ws, **rng.params()}
    members = await _all(
        db,
        "SELECT u.id, u.full_name, u.email, r.name, coalesce(mr.availability, 'available'), mr.max_open "
        "FROM memberships m JOIN users u ON u.id = m.user_id JOIN roles r ON r.id = m.role_id "
        "LEFT JOIN member_routing mr ON mr.workspace_id = m.workspace_id AND mr.user_id = m.user_id "
        "WHERE m.workspace_id = :ws AND r.permissions ? 'tickets.manage' ORDER BY u.full_name",
        p,
    )
    open_now = dict(
        await _all(
            db,
            "SELECT assignee_id, count(*) FROM tickets WHERE workspace_id = :ws AND status IN ('new', 'open', 'pending') "
            "AND assignee_id IS NOT NULL GROUP BY 1",
            p,
        )
    )
    assigned = dict(
        await _all(
            db,
            "SELECT uid, count(*) FROM ("
            "SELECT (data->>'to')::uuid AS uid FROM ticket_events WHERE workspace_id = :ws AND kind = 'assignment' "
            "AND data->>'to' IS NOT NULL AND created_at >= :start AND created_at < :end "
            "UNION ALL SELECT (data->>'assignee_id')::uuid FROM ticket_events WHERE workspace_id = :ws AND kind = 'routed' "
            "AND data->>'assignee_id' IS NOT NULL AND created_at >= :start AND created_at < :end "
            "UNION ALL SELECT t.assignee_id FROM tickets t WHERE t.workspace_id = :ws AND t.created_at >= :start AND t.created_at < :end "
            "AND t.assignee_id IS NOT NULL AND t.assigned_at <= t.created_at + interval '2 seconds' AND NOT EXISTS ("
            "SELECT 1 FROM ticket_events e WHERE e.ticket_id = t.id AND e.kind IN ('assignment', 'routed'))) x GROUP BY uid",
            p,
        )
    )
    replies = dict(
        await _all(
            db,
            "SELECT author_id, count(*) FROM ticket_events WHERE workspace_id = :ws AND kind = 'agent_reply' "
            "AND created_at >= :start AND created_at < :end AND author_id IS NOT NULL GROUP BY 1",
            p,
        )
    )
    chat_replies = dict(
        await _all(
            db,
            "SELECT author_id, count(*) FROM messages WHERE workspace_id = :ws AND role = 'agent' "
            "AND created_at >= :start AND created_at < :end AND author_id IS NOT NULL GROUP BY 1",
            p,
        )
    )
    resolved = dict(
        await _all(
            db,
            f"SELECT author_id, count(*) FROM ticket_events WHERE workspace_id = :ws AND {RESOLVE_EVENTS} "
            "AND created_at >= :start AND created_at < :end AND author_id IS NOT NULL GROUP BY 1",
            p,
        )
    )
    frt = dict(
        await _all(
            db,
            "SELECT assignee_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM first_response_at - created_at)) "
            "FROM tickets WHERE workspace_id = :ws AND first_response_at >= :start AND first_response_at < :end "
            "AND assignee_id IS NOT NULL GROUP BY 1",
            p,
        )
    )
    unassigned = await db.scalar(
        text("SELECT count(*) FROM tickets WHERE workspace_id = :ws AND status IN ('new', 'open', 'pending') AND assignee_id IS NULL"), p
    )
    return {
        "range": rng.out(),
        "unassigned_open": int(unassigned or 0),
        "members": [
            {
                "id": uid,
                "name": name,
                "email": email,
                "role": role,
                "availability": avail,
                "max_open": max_open,
                "open_now": int(open_now.get(uid, 0)),
                "assigned": int(assigned.get(uid, 0)),
                "replies": int(replies.get(uid, 0)),
                "chat_replies": int(chat_replies.get(uid, 0)),
                "resolved": int(resolved.get(uid, 0)),
                "first_response_median_minutes": _minutes(frt.get(uid)),
            }
            for uid, name, email, role, avail, max_open in members
        ],
    }


# ---- chatbot ------------------------------------------------------------------------------------------------

# A widget conversation in which the visitor wrote at least once is one of:
#   handed_off  — it went to a person (escalated) or became a ticket;
#   deflected   — an answer from the knowledge base or an API, and no person or ticket was needed;
#   unanswered  — neither (no answer found, AI unavailable, the visitor left).
CONV_OUTCOME = """
CASE
  WHEN c.escalated_at IS NOT NULL OR EXISTS (SELECT 1 FROM tickets t WHERE t.conversation_id = c.id) THEN 'handed_off'
  WHEN EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.role = 'agent') THEN 'handed_off'
  WHEN EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.role = 'assistant' AND m.data->>'outcome' = 'answered')
    THEN 'deflected'
  ELSE 'unanswered'
END"""
CONV_WHERE = (
    "c.workspace_id = :ws AND c.channel = 'widget' AND c.created_at >= :start AND c.created_at < :end "
    "AND EXISTS (SELECT 1 FROM messages v WHERE v.conversation_id = c.id AND v.role = 'visitor')"
)


async def _chat_totals(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    rows = dict(
        await _all(
            db, f"SELECT {CONV_OUTCOME} AS o, count(*) FROM conversations c WHERE {CONV_WHERE} GROUP BY 1", {"ws": ws, **rng.params()}
        )
    )
    total = sum(rows.values())
    out = {k: int(rows.get(k, 0)) for k in ("deflected", "handed_off", "unanswered")}
    return {
        "conversations": int(total),
        **out,
        "deflection_rate": _rate(out["deflected"], total),
        "handoff_rate": _rate(out["handed_off"], total),
    }


async def chatbot(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    p = {"ws": ws, **rng.params()}
    series = await _all(
        db, f"SELECT {_b('c.created_at')} AS d, {CONV_OUTCOME} AS o, count(*) FROM conversations c WHERE {CONV_WHERE} GROUP BY 1, 2", p
    )
    per_bot = await _all(
        db,
        f"SELECT b.id, b.name, {CONV_OUTCOME} AS o, count(*) FROM conversations c JOIN chatbots b ON b.id = c.chatbot_id "
        f"WHERE {CONV_WHERE} GROUP BY 1, 2, 3",
        p,
    )
    bots: dict = {}
    for bid, name, o, n in per_bot:
        b = bots.setdefault(bid, {"id": bid, "name": name, "deflected": 0, "handed_off": 0, "unanswered": 0})
        b[o] += int(n)
    for b in bots.values():
        total = b["deflected"] + b["handed_off"] + b["unanswered"]
        b["conversations"] = total
        b["deflection_rate"] = _rate(b["deflected"], total)
        b["handoff_rate"] = _rate(b["handed_off"], total)
    return {
        "range": rng.out(),
        "totals": await _chat_totals(db, ws, rng),
        "previous": await _chat_totals(db, ws, rng.previous()),
        "series": _series(rng, series, ["deflected", "handed_off", "unanswered"]),
        "chatbots": sorted(bots.values(), key=lambda b: -b["conversations"]),
    }


# ---- API tool calls -----------------------------------------------------------------------------------------


async def tools(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    p = {"ws": ws, **rng.params()}
    w = "workspace_id = :ws AND created_at >= :start AND created_at < :end"
    series = await _all(
        db,
        f"SELECT {_b('created_at')} AS d, CASE WHEN status = 'ok' THEN 'ok' ELSE 'failed' END, count(*) FROM "
        f"api_tool_calls WHERE {w} GROUP BY 1, 2",
        p,
    )
    statuses = await _all(db, f"SELECT status, count(*) FROM api_tool_calls WHERE {w} GROUP BY 1 ORDER BY 2 DESC", p)
    per_tool = await _all(
        db,
        "SELECT tool_name, count(*), count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE origin = 'test'), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE latency_ms > 0), "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE latency_ms > 0) "
        f"FROM api_tool_calls WHERE {w} GROUP BY 1 ORDER BY 2 DESC",
        p,
    )
    totals = await _one(
        db,
        "SELECT count(*), count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE origin = 'chat'), count(*) "
        "FILTER (WHERE origin = 'test'), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE latency_ms > 0), "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE latency_ms > 0) "
        f"FROM api_tool_calls WHERE {w}",
        p,
    )
    return {
        "range": rng.out(),
        "totals": {
            "calls": int(totals[0]),
            "ok": int(totals[1]),
            "from_chats": int(totals[2]),
            "tests": int(totals[3]),
            "success_rate": _rate(int(totals[1]), int(totals[0])),
            "latency_p50_ms": None if totals[4] is None else round(float(totals[4])),
            "latency_p95_ms": None if totals[5] is None else round(float(totals[5])),
        },
        "series": _series(rng, series, ["ok", "failed"]),
        "by_status": [{"status": s, "count": int(n)} for s, n in statuses],
        "tools": [
            {
                "name": name,
                "calls": int(n),
                "ok": int(ok),
                "tests": int(tests),
                "success_rate": _rate(int(ok), int(n)),
                "latency_p50_ms": None if p50 is None else round(float(p50)),
                "latency_p95_ms": None if p95 is None else round(float(p95)),
            }
            for name, n, ok, tests, p50, p95 in per_tool
        ],
    }


# ---- email --------------------------------------------------------------------------------------------------

MAIL_KIND = """
CASE
  WHEN status IN ('ticketed', 'appended') THEN 'legitimate'
  WHEN status IN ('spam', 'deleted') THEN 'spam'
  WHEN status = 'promotion' THEN 'promotion'
  WHEN status = 'rate_limited' THEN 'held'
  WHEN status IN ('loop', 'bounce', 'auto_reply') THEN 'automatic'
  WHEN status = 'failed' THEN 'failed'
  ELSE 'processing'
END"""


async def email(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    p = {"ws": ws, **rng.params()}
    w = "workspace_id = :ws AND direction = 'in' AND received_at >= :start AND received_at < :end"
    keys = ["legitimate", "spam", "promotion", "held", "automatic", "failed"]
    series = await _all(db, f"SELECT {_b('received_at')} AS d, {MAIL_KIND}, count(*) FROM email_messages WHERE {w} GROUP BY 1, 2", p)
    totals = dict(await _all(db, f"SELECT {MAIL_KIND}, count(*) FROM email_messages WHERE {w} GROUP BY 1", p))
    extra = await _one(
        db,
        "SELECT count(*) FILTER (WHERE verdict->>'reviewed' = 'not_spam'), count(*) FILTER (WHERE verdict->>'decided_by' = 'ai') "
        f"FROM email_messages WHERE {w}",
        p,
    )
    total = sum(int(v) for v in totals.values())
    unwanted = int(totals.get("spam", 0)) + int(totals.get("promotion", 0))
    return {
        "range": rng.out(),
        "totals": {
            "received": total,
            **{k: int(totals.get(k, 0)) for k in keys},
            "marked_not_spam": int(extra[0]),
            "decided_by_ai": int(extra[1]),
            "spam_rate": _rate(unwanted, total),
        },
        "series": _series(rng, series, keys),
    }


# ---- AI usage -----------------------------------------------------------------------------------------------


async def ai(db: AsyncSession, ws: uuid.UUID, rng: Range) -> dict:
    p = {"ws": ws, **rng.params()}
    w = "workspace_id = :ws AND created_at >= :start AND created_at < :end"
    series = await _all(db, f"SELECT {_b('created_at')} AS d, operation, sum(weighted_tokens) FROM usage_events WHERE {w} GROUP BY 1, 2", p)
    ops = await _all(
        db,
        f"SELECT operation, sum(input_tokens), sum(output_tokens), sum(weighted_tokens), count(*) FROM usage_events WHERE {w} "
        "GROUP BY 1 ORDER BY 4 DESC",
        p,
    )
    models = await _all(
        db,
        f"SELECT model, sum(input_tokens), sum(output_tokens), sum(weighted_tokens), count(*) FROM usage_events WHERE {w} "
        "GROUP BY 1 ORDER BY 4 DESC",
        p,
    )
    prev = await _one(db, f"SELECT coalesce(sum(weighted_tokens), 0) FROM usage_events WHERE {w}", {"ws": ws, **rng.previous().params()})

    def rows(rs):
        return [
            {"name": n, "input_tokens": int(i), "output_tokens": int(o), "weighted_tokens": int(wt), "calls": int(c)}
            for n, i, o, wt, c in rs
        ]

    return {
        "range": rng.out(),
        "totals": {
            "weighted_tokens": sum(int(r[3]) for r in ops),
            "input_tokens": sum(int(r[1]) for r in ops),
            "output_tokens": sum(int(r[2]) for r in ops),
            "calls": sum(int(r[4]) for r in ops),
        },
        "previous": {"weighted_tokens": int(prev[0])},
        "series": _series(rng, series, [r[0] for r in ops]),
        "by_feature": rows(ops),
        "by_model": rows(models),
    }


# ---- Platform (system administrators) -----------------------------------------------------------------------


async def platform_usage(db: AsyncSession, rng: Range) -> dict:
    p = rng.params()
    rows = await _all(
        db,
        "SELECT w.id, w.name, w.status, s.terms->>'plan_name', s.status, "
        "(SELECT coalesce(sum(weighted_tokens), 0) FROM usage_events u WHERE u.workspace_id = w.id AND u.created_at >= "
        ":start AND u.created_at < :end), "
        "(SELECT coalesce(sum(provider_cost_usd), 0) FROM usage_events u WHERE u.workspace_id = w.id AND u.created_at >= "
        ":start AND u.created_at < :end), "
        "(SELECT count(*) FROM tickets t WHERE t.workspace_id = w.id AND t.created_at >= :start AND t.created_at < :end), "
        "(SELECT count(*) FROM conversations c WHERE c.workspace_id = w.id AND c.channel = 'widget' AND c.created_at >= "
        ":start AND c.created_at < :end), "
        "(SELECT count(*) FROM memberships m WHERE m.workspace_id = w.id) "
        "FROM workspaces w LEFT JOIN subscriptions s ON s.workspace_id = w.id ORDER BY 6 DESC, 8 DESC, w.created_at DESC LIMIT 25",
        p,
    )
    series = await _all(
        db,
        f"SELECT {_b('created_at')} AS d, 'weighted', sum(weighted_tokens) FROM usage_events WHERE created_at >= :start "
        "AND created_at < :end GROUP BY 1",
        p,
    )
    active = await db.scalar(
        text("SELECT count(DISTINCT workspace_id) FROM usage_events WHERE created_at >= :start AND created_at < :end"), p
    )
    return {
        "range": rng.out(),
        "active_workspaces": int(active or 0),
        "series": _series(rng, series, ["weighted"]),
        "workspaces": [
            {
                "id": r[0],
                "name": r[1],
                "status": r[2],
                "plan": r[3],
                "subscription_status": r[4],
                "weighted_tokens": int(r[5]),
                "provider_cost_usd": round(float(r[6]), 4),
                "tickets": int(r[7]),
                "conversations": int(r[8]),
                "members": int(r[9]),
            }
            for r in rows
        ],
    }


NET = "(p.subtotal_cents - CASE WHEN p.amount_cents > 0 THEN p.refunded_cents * p.subtotal_cents / p.amount_cents ELSE 0 END)"


async def _revenue(db: AsyncSession, rng: Range) -> dict:
    rows = await _all(
        db,
        f"SELECT p.currency, sum({NET}) FROM payments p WHERE p.status IN ('succeeded', 'refunded') AND p.created_at >= "
        ":start AND p.created_at < :end GROUP BY 1",
        rng.params(),
    )
    return {c: int(v) for c, v in rows}


async def platform_costs(db: AsyncSession, rng: Range) -> dict:
    p = rng.params()
    w = "created_at >= :start AND created_at < :end"
    series = await _all(db, f"SELECT {_b('created_at')} AS d, 'cost', sum(provider_cost_usd) FROM usage_events WHERE {w} GROUP BY 1", p)
    models = await _all(
        db,
        f"SELECT model, sum(provider_cost_usd), sum(input_tokens), sum(output_tokens) FROM usage_events WHERE {w} GROUP "
        "BY 1 ORDER BY 2 DESC",
        p,
    )
    ops = await _all(db, f"SELECT operation, sum(provider_cost_usd) FROM usage_events WHERE {w} GROUP BY 1 ORDER BY 2 DESC", p)
    total = sum(float(r[1] or 0) for r in models)
    prev = await db.scalar(text(f"SELECT coalesce(sum(provider_cost_usd), 0) FROM usage_events WHERE {w}"), rng.previous().params())
    revenue = await _revenue(db, rng)
    usd = revenue.get("USD")
    return {
        "range": rng.out(),
        "total_usd": round(total, 2),
        "previous_usd": round(float(prev or 0), 2),
        "series": [{**pt, "cost": round(float(pt["cost"]), 4)} for pt in _series(rng, series, ["cost"])],
        "by_model": [
            {"name": m, "cost_usd": round(float(c or 0), 4), "input_tokens": int(i), "output_tokens": int(o)} for m, c, i, o in models
        ],
        "by_feature": [{"name": o, "cost_usd": round(float(c or 0), 4)} for o, c in ops],
        "revenue": revenue,
        # Provider costs are in USD, so a margin exists only against USD revenue.
        "usd_margin_rate": None if not usd else round((usd / 100 - total) * 100 / (usd / 100), 1),
    }


async def platform_subscriptions(db: AsyncSession, rng: Range) -> dict:
    p = rng.params()
    now_counts = dict(
        await _all(
            db,
            "SELECT CASE WHEN status = 'active' AND current_period_end + interval '48 hours' > now() THEN 'active' "
            "WHEN status = 'past_due' THEN 'past_due' ELSE 'ended' END, count(*) FROM subscriptions GROUP BY 1",
            {},
        )
    )
    by_plan = await _all(
        db,
        "SELECT terms->>'plan_name', terms->>'currency', count(*) FROM subscriptions WHERE status IN ('active', "
        "'past_due') GROUP BY 1, 2 ORDER BY 3 DESC",
        {},
    )
    series = await _all(
        db,
        f"SELECT {_b('created_at')} AS d, 'new', count(*) FROM payments WHERE kind = 'subscription' AND status IN "
        "('succeeded', 'refunded') "
        "AND created_at >= :start AND created_at < :end GROUP BY 1 "
        f"UNION ALL SELECT {_b('created_at')} AS d, 'canceled', count(*) FROM audit_logs WHERE action = 'subscription.canceled' "
        "AND created_at >= :start AND created_at < :end GROUP BY 1 "
        f"UNION ALL SELECT {_b('created_at')} AS d, 'upgraded', count(*) FROM audit_logs WHERE action = 'subscription.upgraded' "
        "AND created_at >= :start AND created_at < :end GROUP BY 1",
        p,
    )
    s = _series(rng, series, ["new", "upgraded", "canceled"])
    return {
        "range": rng.out(),
        "now": {k: int(now_counts.get(k, 0)) for k in ("active", "past_due", "ended")},
        "by_plan": [{"plan": n, "currency": c, "count": int(k)} for n, c, k in by_plan],
        "series": s,
        "totals": {k: sum(pt[k] for pt in s) for k in ("new", "upgraded", "canceled")},
    }


async def platform_revenue(db: AsyncSession, rng: Range) -> dict:
    """MRR at the end of each bucket (the monthly value of the latest paid subscription invoice covering that
    moment, per workspace) and revenue (net of tax and refunds) per bucket, both per currency."""
    labels = rng.labels()
    step = timedelta(days=1 if rng.bucket == "day" else 7)
    z = _zone(rng.tz)
    points = []
    now = utcnow()
    for d in labels:
        nxt = d + step
        moment = min(datetime(nxt.year, nxt.month, nxt.day, tzinfo=z).astimezone(ZoneInfo("UTC")), now)
        points.append(moment)
    mrr_rows = await _all(
        db,
        "SELECT m.i, x.currency, sum(x.mrr_cents) FROM unnest(CAST(:moments AS timestamptz[])) WITH ORDINALITY AS m(t, i) "
        "CROSS JOIN LATERAL (SELECT DISTINCT ON (p.workspace_id) p.workspace_id, p.currency, p.mrr_cents FROM payments p "
        "WHERE p.kind IN ('subscription', 'renewal', 'upgrade') AND p.status = 'succeeded' "
        "AND p.period_start <= m.t AND p.period_end > m.t ORDER BY p.workspace_id, p.period_start DESC, p.created_at DESC) x "
        "GROUP BY 1, 2",
        {"moments": points},
    )
    rev = await _all(
        db,
        f"SELECT {_b('p.created_at')} AS d, p.currency, sum({NET}) FROM payments p WHERE p.status IN ('succeeded', 'refunded') "
        "AND p.created_at >= :start AND p.created_at < :end GROUP BY 1, 2",
        rng.params(),
    )
    by_kind = await _all(
        db,
        f"SELECT p.currency, p.kind, sum({NET}), count(*) FROM payments p WHERE p.status IN ('succeeded', 'refunded') "
        "AND p.created_at >= :start AND p.created_at < :end GROUP BY 1, 2 ORDER BY 1, 3 DESC",
        rng.params(),
    )
    current = await _all(
        db,
        "SELECT terms->>'currency', sum(CASE WHEN terms->>'billing_cycle' = 'year' THEN (terms->>'price_cents')::int / 12 "
        "ELSE (terms->>'price_cents')::int END), count(*) FROM subscriptions "
        "WHERE (status = 'active' AND current_period_end + interval '48 hours' > now()) OR status = 'past_due' GROUP BY 1",
        {},
    )
    # Every currency in the report appears in every series (zero where it has nothing), never summed together.
    currencies = sorted({c for _, c, _ in mrr_rows} | {c for _, c, _ in rev} | {c for c, _, _ in current})
    mrr_series = _series(rng, [(labels[int(i) - 1], c, v) for i, c, v in mrr_rows], currencies)
    return {
        "range": rng.out(),
        "currencies": currencies,
        "mrr_now": [{"currency": c, "mrr_cents": int(v), "subscriptions": int(n)} for c, v, n in current],
        "mrr_series": mrr_series,
        "revenue": await _revenue(db, rng),
        "revenue_previous": await _revenue(db, rng.previous()),
        "revenue_series": _series(rng, rev, currencies),
        "by_kind": [{"currency": c, "kind": k, "net_cents": int(v), "invoices": int(n)} for c, k, v, n in by_kind],
    }


async def platform_failed_payments(db: AsyncSession, rng: Range) -> dict:
    p = rng.params()
    series = await _all(
        db,
        f"SELECT {_b('created_at')} AS d, CASE WHEN status = 'failed' THEN 'failed' ELSE 'recovered' END, count(*) FROM payments "
        "WHERE kind = 'renewal' AND created_at >= :start AND created_at < :end AND (status = 'failed' OR (status = "
        "'succeeded' AND attempt_count > 1)) "
        "GROUP BY 1, 2",
        p,
    )
    at_risk = await _all(
        db,
        "SELECT currency, sum(amount_cents), count(*) FROM payments WHERE status = 'failed' AND created_at >= :start AND "
        "created_at < :end GROUP BY 1",
        p,
    )
    past_due = await _all(
        db,
        "SELECT w.id, w.name, s.terms->>'plan_name', s.past_due_since, s.next_retry_at, s.retry_count, "
        "(SELECT p.amount_cents FROM payments p WHERE p.workspace_id = w.id AND p.status = 'failed' ORDER BY p.created_at DESC LIMIT 1), "
        "(SELECT p.currency FROM payments p WHERE p.workspace_id = w.id AND p.status = 'failed' ORDER BY p.created_at DESC LIMIT 1) "
        "FROM subscriptions s JOIN workspaces w ON w.id = s.workspace_id WHERE s.status = 'past_due' ORDER BY s.past_due_since",
        {},
    )
    s = _series(rng, series, ["failed", "recovered"])
    return {
        "range": rng.out(),
        "series": s,
        "totals": {"failed": sum(pt["failed"] for pt in s), "recovered": sum(pt["recovered"] for pt in s)},
        "at_risk": [{"currency": c, "amount_cents": int(a), "invoices": int(n)} for c, a, n in at_risk],
        "past_due": [
            {
                "workspace_id": r[0],
                "workspace": r[1],
                "plan": r[2],
                "since": r[3],
                "next_retry_at": r[4],
                "retries": int(r[5] or 0),
                "amount_cents": r[6],
                "currency": r[7],
            }
            for r in past_due
        ],
    }
