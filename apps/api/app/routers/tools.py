"""External API tools (Phase 5A): define them, test them, and link them to chatbots.

Tools: integrations.manage. Linking tools to a chatbot: chatbots.manage (reading the links: chatbots.read).
Every query filters on ctx.workspace.id; IDs from another workspace answer 404. The credential is encrypted
before it is stored, never returned (only `has_secret`), and every change is audit-logged without it.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.core.security import encrypt_secret
from app.models import ApiTool, ApiToolCall, Chatbot, chatbot_tools
from app.models.base import utcnow
from app.services.audit import audit
from app.services.subscriptions import effective_limits
from app.services.tools import definitions as defs
from app.services.tools import runner

router = APIRouter(tags=["tools"])

MANAGE = require("integrations.manage", subscription=True)
BOTS_READ = require("chatbots.read", subscription=True)
BOTS_MANAGE = require("chatbots.manage", subscription=True)


class ParameterIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=80)
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = Field(default="", max_length=300)
    required: bool = False
    location: Literal["path", "query", "body"] = "query"
    source: Literal["ai", "visitor_email", "visitor_name"] = "ai"
    enum: list[str | int | float] = Field(default_factory=list, max_length=50)
    pattern: str | None = Field(default=None, max_length=200)
    max_length: int | None = None


class ToolIn(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    display_name: str = Field(default="", max_length=80)
    description: str = Field(min_length=10, max_length=1500)
    method: Literal["GET", "POST"] = "GET"
    url_template: str = Field(min_length=10, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    parameters: list[ParameterIn] = Field(default_factory=list, max_length=20)
    auth_type: Literal["none", "api_key", "bearer"] = "none"
    auth_header: str | None = Field(default=None, max_length=80)
    secret: str | None = Field(default=None, min_length=1, max_length=4000)
    timeout_seconds: int = Field(default=8, ge=1, le=15)
    max_response_kb: int = Field(default=32, ge=1, le=256)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=600)


class ToolUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=64)
    display_name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, min_length=10, max_length=1500)
    method: Literal["GET", "POST"] | None = None
    url_template: str | None = Field(default=None, min_length=10, max_length=2000)
    headers: dict[str, str] | None = None
    parameters: list[ParameterIn] | None = Field(default=None, max_length=20)
    auth_type: Literal["none", "api_key", "bearer"] | None = None
    auth_header: str | None = Field(default=None, max_length=80)
    timeout_seconds: int | None = Field(default=None, ge=1, le=15)
    max_response_kb: int | None = Field(default=None, ge=1, le=256)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=600)
    status: Literal["draft", "active", "disabled"] | None = None


class SecretIn(BaseModel):
    secret: str = Field(min_length=1, max_length=4000)


class TestIn(BaseModel):
    args: dict = Field(default_factory=dict)
    visitor_email: str | None = Field(default=None, max_length=320)
    visitor_name: str | None = Field(default=None, max_length=120)


class ChatbotToolsIn(BaseModel):
    tool_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


def tool_out(t: ApiTool, chatbots: list[dict] | None = None) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "display_name": t.display_name,
        "description": t.description,
        "method": t.method,
        "url_template": t.url_template,
        "host": defs.check_url(t.url_template)[0] if t.url_template else None,
        "headers": t.headers,
        "parameters": t.parameters,
        "auth_type": t.auth_type,
        "auth_header": t.auth_header,
        "has_secret": bool(t.secret_enc),
        "timeout_seconds": t.timeout_seconds,
        "max_response_kb": t.max_response_kb,
        "rate_limit_per_minute": t.rate_limit_per_minute,
        "status": t.status,
        "last_test_at": t.last_test_at,
        "last_test_status": t.last_test_status,
        "last_test_word": runner.STATUS_WORDS.get(t.last_test_status) if t.last_test_status else None,
        "chatbots": chatbots if chatbots is not None else None,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


async def _tool(db: AsyncSession, ctx: WorkspaceContext, tool_id: uuid.UUID) -> ApiTool:
    t = (await db.execute(select(ApiTool).where(ApiTool.id == tool_id, ApiTool.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if t is None:
        raise not_found("API tool")
    return t


async def _lock(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"tools:{workspace_id}"})


async def _count(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    return (await db.execute(select(func.count()).select_from(ApiTool).where(ApiTool.workspace_id == workspace_id))).scalar_one()


async def _limit(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    return (await effective_limits(db, workspace_id)).get("api_tools", 0)


def _check(name, method, url_template, parameters, headers, auth_type, auth_header) -> tuple[list[dict], dict, str | None]:
    if not defs.NAME.match(name):
        raise ApiError(422, "tool_invalid_name", "Use lowercase letters, digits and _ (for example order_status).", field="name")
    _host, placeholders = defs.check_url(url_template)
    params = defs.check_parameters(parameters, method=method, placeholders=placeholders)
    auth_header = defs.check_auth(auth_type, auth_header)
    hdrs = defs.check_headers(headers, auth_type=auth_type, auth_header=auth_header)
    return params, hdrs, auth_header


async def _name_free(db: AsyncSession, ws_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> None:
    q = select(ApiTool.id).where(ApiTool.workspace_id == ws_id, ApiTool.name == name)
    if exclude:
        q = q.where(ApiTool.id != exclude)
    if (await db.execute(q)).first():
        raise ApiError(409, "tool_name_taken", "Another tool already has this name.", field="name")


async def _linked(db: AsyncSession, tool_id: uuid.UUID) -> list[dict]:
    rows = (
        await db.execute(
            select(Chatbot.id, Chatbot.name)
            .join(chatbot_tools, chatbot_tools.c.chatbot_id == Chatbot.id)
            .where(chatbot_tools.c.tool_id == tool_id)
        )
    ).all()
    return [{"id": r.id, "name": r.name} for r in rows]


@router.get("/tools")
async def list_tools(db: Db, ctx: WorkspaceContext = MANAGE):
    rows = (await db.execute(select(ApiTool).where(ApiTool.workspace_id == ctx.workspace.id).order_by(ApiTool.created_at))).scalars().all()
    return {
        "items": [tool_out(t, await _linked(db, t.id)) for t in rows],
        "limit": await _limit(db, ctx.workspace.id),
        "used": len(rows),
    }


@router.post("/tools", status_code=201)
async def create_tool(body: ToolIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    ws_id = ctx.workspace.id
    name = body.name.strip()
    params, headers, auth_header = _check(
        name,
        body.method,
        body.url_template.strip(),
        [p.model_dump() for p in body.parameters],
        body.headers,
        body.auth_type,
        body.auth_header,
    )
    if body.auth_type != "none" and not body.secret:
        raise ApiError(422, "tool_secret_required", "Enter the key or token the API expects.", field="secret")
    await _lock(db, ws_id)
    limit = await _limit(db, ws_id)
    if limit >= 0 and await _count(db, ws_id) >= limit:
        raise ApiError(
            402, "limit_reached", "Your plan's API tool limit is reached. Upgrade or delete a tool to add another.", limit="api_tools"
        )
    await _name_free(db, ws_id, name)
    t = ApiTool(
        workspace_id=ws_id,
        name=name,
        display_name=body.display_name.strip() or name.replace("_", " ").capitalize(),
        description=body.description.strip(),
        method=body.method,
        url_template=body.url_template.strip(),
        headers=headers,
        parameters=params,
        auth_type=body.auth_type,
        auth_header=auth_header,
        secret_enc=encrypt_secret(body.secret) if body.secret and body.auth_type != "none" else None,
        timeout_seconds=body.timeout_seconds,
        max_response_kb=body.max_response_kb,
        rate_limit_per_minute=body.rate_limit_per_minute,
        status="draft",
        created_by=ctx.user.id,
    )
    db.add(t)
    await db.flush()
    audit(
        db,
        "api_tool.created",
        actor_id=ctx.user.id,
        workspace_id=ws_id,
        target_type="api_tool",
        target_id=t.id,
        ip=client_ip(request),
        name=t.name,
        host=defs.check_url(t.url_template)[0],
        auth_type=t.auth_type,
    )
    await db.commit()
    await db.refresh(t)
    return tool_out(t, [])


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: uuid.UUID, db: Db, ctx: WorkspaceContext = MANAGE):
    t = await _tool(db, ctx, tool_id)
    return tool_out(t, await _linked(db, t.id))


@router.patch("/tools/{tool_id}")
async def update_tool(tool_id: uuid.UUID, body: ToolUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    t = await _tool(db, ctx, tool_id)
    changes = body.model_dump(exclude_unset=True)
    name = (body.name or t.name).strip()
    method = body.method or t.method
    url = (body.url_template or t.url_template).strip()
    auth_type = body.auth_type or t.auth_type
    auth_header = body.auth_header if "auth_header" in changes else t.auth_header
    params_in = [p.model_dump() for p in body.parameters] if body.parameters is not None else t.parameters
    headers_in = body.headers if body.headers is not None else t.headers
    params, headers, auth_header = _check(name, method, url, params_in, headers_in, auth_type, auth_header)
    if name != t.name:
        await _name_free(db, ctx.workspace.id, name, exclude=t.id)
    status = body.status or t.status
    if auth_type != "none" and not t.secret_enc and status == "active":
        raise ApiError(422, "tool_secret_required", "Save the key or token before turning the tool on.", field="secret")
    before = t.status
    t.name, t.method, t.url_template, t.parameters, t.headers = name, method, url, params, headers
    t.auth_type, t.auth_header = auth_type, auth_header
    if auth_type == "none":
        t.secret_enc = None
    for f in ("display_name", "description", "timeout_seconds", "max_response_kb", "rate_limit_per_minute"):
        if changes.get(f) is not None:
            setattr(t, f, changes[f].strip() if isinstance(changes[f], str) else changes[f])
    t.status = status
    if status != "active" and before == "active":
        # A tool that is off is no longer offered anywhere.
        await db.execute(delete(chatbot_tools).where(chatbot_tools.c.tool_id == t.id))
    audit(
        db,
        "api_tool.updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="api_tool",
        target_id=t.id,
        ip=client_ip(request),
        fields=sorted(changes),
    )
    if status != before:
        audit(
            db,
            "api_tool.status_changed",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="api_tool",
            target_id=t.id,
            status=status,
        )
    await db.commit()
    await db.refresh(t)
    return tool_out(t, await _linked(db, t.id))


@router.put("/tools/{tool_id}/secret")
async def set_secret(tool_id: uuid.UUID, body: SecretIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    t = await _tool(db, ctx, tool_id)
    if t.auth_type == "none":
        raise ApiError(422, "tool_no_auth", "Choose an authentication type first.", field="auth_type")
    t.secret_enc = encrypt_secret(body.secret)
    audit(
        db,
        "api_tool.secret_changed",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="api_tool",
        target_id=t.id,
        ip=client_ip(request),
    )
    await db.commit()
    return {"has_secret": True}


@router.delete("/tools/{tool_id}", status_code=204)
async def delete_tool(tool_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    t = await _tool(db, ctx, tool_id)
    t.secret_enc = None
    audit(
        db,
        "api_tool.deleted",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="api_tool",
        target_id=t.id,
        ip=client_ip(request),
        name=t.name,
    )
    await db.delete(t)
    await db.commit()


@router.post("/tools/{tool_id}/test")
async def test_tool(tool_id: uuid.UUID, body: TestIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    """Runs the real guarded call with sample values. Uses no AI tokens."""
    from app.models import Conversation

    t = await _tool(db, ctx, tool_id)
    if t.auth_type != "none" and not t.secret_enc:
        raise ApiError(422, "tool_secret_required", "Save the key or token first.", field="secret")
    # Stands in for the conversation's context values; never added to the session, so never stored.
    sample = Conversation(visitor_email=body.visitor_email, visitor_name=body.visitor_name)
    out = await runner.run(db, t, body.args, conversation=sample, origin="test", actor_id=ctx.user.id)
    t.last_test_at = utcnow()
    t.last_test_status = out.status
    audit(
        db,
        "api_tool.tested",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="api_tool",
        target_id=t.id,
        ip=client_ip(request),
        status=out.status,
        http_status=out.http_status,
    )
    await db.commit()
    return {
        "status": out.status,
        "status_word": out.word,
        "http_status": out.http_status,
        "latency_ms": out.latency_ms,
        "response_bytes": out.response_bytes,
        "error_code": out.error_code,
        "error": out.error,
        "request": out.request_line,
        "preview": out.body[:4000],
        "truncated": len(out.body) > 4000,
    }


@router.get("/tools/{tool_id}/calls")
async def tool_calls(tool_id: uuid.UUID, db: Db, limit: int = 50, ctx: WorkspaceContext = MANAGE):
    t = await _tool(db, ctx, tool_id)
    rows = (
        await db.execute(
            select(ApiToolCall)
            .where(ApiToolCall.workspace_id == ctx.workspace.id, ApiToolCall.tool_id == t.id)
            .order_by(ApiToolCall.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
    ).scalars()
    return {"items": [runner.call_out(c) for c in rows]}


# ---- Chatbots ↔ tools ---------------------------------------------------------------------------


async def _bot(db: AsyncSession, ctx: WorkspaceContext, bot_id: uuid.UUID) -> Chatbot:
    b = (await db.execute(select(Chatbot).where(Chatbot.id == bot_id, Chatbot.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if b is None:
        raise not_found("Chatbot")
    return b


async def _bot_tools(db: AsyncSession, ctx: WorkspaceContext, b: Chatbot) -> dict:
    linked = set((await db.execute(select(chatbot_tools.c.tool_id).where(chatbot_tools.c.chatbot_id == b.id))).scalars())
    rows = (
        await db.execute(
            select(ApiTool).where(ApiTool.workspace_id == ctx.workspace.id, ApiTool.status == "active").order_by(ApiTool.display_name)
        )
    ).scalars()
    return {
        "items": [
            {"id": t.id, "name": t.name, "display_name": t.display_name, "description": t.description, "enabled": t.id in linked}
            for t in rows
        ]
    }


@router.get("/chatbots/{bot_id}/tools")
async def get_bot_tools(bot_id: uuid.UUID, db: Db, ctx: WorkspaceContext = BOTS_READ):
    return await _bot_tools(db, ctx, await _bot(db, ctx, bot_id))


@router.put("/chatbots/{bot_id}/tools")
async def set_bot_tools(bot_id: uuid.UUID, body: ChatbotToolsIn, request: Request, db: Db, ctx: WorkspaceContext = BOTS_MANAGE):
    b = await _bot(db, ctx, bot_id)
    ids = list(dict.fromkeys(body.tool_ids))
    if ids:
        found = (await db.execute(select(ApiTool).where(ApiTool.id.in_(ids), ApiTool.workspace_id == ctx.workspace.id))).scalars().all()
        if len(found) != len(ids):
            raise ApiError(404, "tool_not_found", "API tool not found.", field="tool_ids")
        if any(t.status != "active" for t in found):
            raise ApiError(422, "tool_not_active", "Turn the tool on before adding it to a chatbot.", field="tool_ids")
    await db.execute(delete(chatbot_tools).where(chatbot_tools.c.chatbot_id == b.id))
    if ids:
        await db.execute(insert(chatbot_tools), [{"chatbot_id": b.id, "tool_id": i} for i in ids])
    audit(
        db,
        "chatbot.tools_updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="chatbot",
        target_id=b.id,
        ip=client_ip(request),
        tool_ids=ids,
    )
    await db.commit()
    return await _bot_tools(db, ctx, b)
