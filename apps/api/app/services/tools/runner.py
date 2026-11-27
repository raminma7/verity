"""Running an API tool: check the arguments, fill context values, rate limit, call through the guard, log.

The credential is decrypted only here, at the moment of the call, and is never logged, traced or returned.
Whatever the API answers is untrusted data: callers pass it to the model framed as data, never as
instructions.
"""

import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

import regex
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.core.ssrf import OutboundError
from app.models import ApiTool, ApiToolCall, Conversation
from app.models.base import utcnow
from app.services.tools.definitions import PLACEHOLDER
from app.services.tools.transport import get_client

log = logging.getLogger("verity.tools")
PATTERN_TIMEOUT = 0.05
DEFAULT_MAX_LENGTH = 200

STATUS_WORDS = {
    "ok": "Succeeded",
    "http_error": "API error",
    "timeout": "Timed out",
    "blocked": "Blocked",
    "rate_limited": "Rate limited",
    "invalid_args": "Invalid input",
    "too_large": "Response too large",
    "failed": "Failed",
}
_OUTBOUND_STATUS = {"blocked_address": "blocked", "dns_failed": "failed", "timeout": "timeout", "too_large": "too_large"}


class ArgError(Exception):
    pass


@dataclass
class ToolOutcome:
    status: str
    http_status: int | None = None
    latency_ms: int = 0
    body: str = ""
    response_bytes: int = 0
    error_code: str | None = None
    error: str | None = None
    request_line: str = ""  # "GET shop.example.com/orders/{order_id}", never the secret or the values
    args: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def word(self) -> str:
        return STATUS_WORDS.get(self.status, self.status)


def context_values(conv: Conversation | None) -> dict:
    if conv is None:
        return {}
    return {"visitor_email": conv.visitor_email, "visitor_name": conv.visitor_name}


def available_for(tool: ApiTool, conv: Conversation | None) -> bool:
    """A tool whose required context value (e.g. the visitor's email) is unknown isn't offered at all."""
    ctx = context_values(conv)
    return all(ctx.get(p["source"]) for p in tool.parameters if p["source"] != "ai" and p["required"])


def check_value(p: dict, v):
    t = p["type"]
    if t == "boolean":
        if not isinstance(v, bool):
            raise ArgError(f"{p['key']} must be true or false")
        return v
    if t == "integer":
        if isinstance(v, bool) or not isinstance(v, int):
            if isinstance(v, str) and regex.fullmatch(r"-?\d{1,18}", v):
                v = int(v)
            else:
                raise ArgError(f"{p['key']} must be a whole number")
    elif t == "number":
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise ArgError(f"{p['key']} must be a number")
    else:
        if not isinstance(v, str | int | float) or isinstance(v, bool):
            raise ArgError(f"{p['key']} must be text")
        v = str(v).strip()
        if not v:
            raise ArgError(f"{p['key']} is empty")
        if len(v) > (p.get("max_length") or DEFAULT_MAX_LENGTH):
            raise ArgError(f"{p['key']} is too long")
        if any(c in v for c in "\r\n\0"):
            raise ArgError(f"{p['key']} contains control characters")
        if p.get("pattern"):
            try:
                ok = regex.fullmatch(p["pattern"], v, timeout=PATTERN_TIMEOUT) is not None
            except TimeoutError:
                ok = False
            if not ok:
                raise ArgError(f"{p['key']} doesn't have the expected format")
    if p.get("enum") and v not in p["enum"]:
        raise ArgError(f"{p['key']} must be one of the listed choices")
    return v


def build_request(tool: ApiTool, args: dict, conv: Conversation | None) -> tuple[str, dict, dict | None, dict]:
    """(url, query, json body, the values used). Raises ArgError."""
    args = args if isinstance(args, dict) else {}
    ctx = context_values(conv)
    values: dict = {}
    for p in tool.parameters:
        raw = args.get(p["key"]) if p["source"] == "ai" else ctx.get(p["source"])
        if raw is None or raw == "":
            if p["required"]:
                raise ArgError(f"{p['key']} is required")
            continue
        values[p["key"]] = check_value(p, raw)
    path_vals = {}
    for p in tool.parameters:
        if p["location"] == "path":
            s = str(values[p["key"]])
            if s in (".", ".."):
                raise ArgError(f"{p['key']} isn't a valid value")
            path_vals[p["key"]] = quote(s, safe="")
    parts = urlsplit(tool.url_template)
    path = PLACEHOLDER.sub(lambda m: path_vals[m.group(1)], parts.path)
    url = parts._replace(path=path).geturl()
    query = {p["key"]: _q(values[p["key"]]) for p in tool.parameters if p["location"] == "query" and p["key"] in values}
    body = {p["key"]: values[p["key"]] for p in tool.parameters if p["location"] == "body" and p["key"] in values}
    return url, query, (body if tool.method == "POST" else None), values


def _q(v) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def request_line(tool: ApiTool) -> str:
    parts = urlsplit(tool.url_template)
    return f"{tool.method} {parts.hostname}{parts.path}"


def auth_headers(tool: ApiTool) -> dict:
    if tool.auth_type == "none" or not tool.secret_enc:
        return {}
    secret = decrypt_secret(tool.secret_enc)
    if tool.auth_type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    return {tool.auth_header or "X-Api-Key": secret}


async def _limited(tool: ApiTool, *, conv: Conversation | None, origin: str, actor_id) -> bool:
    s = get_settings()
    if origin == "test":
        return not await ratelimit.allow(f"tool:test:{actor_id}", s.rl_tool_test)
    checks = [(f"tool:{tool.id}", f"{max(1, tool.rate_limit_per_minute)}/60"), (f"tool:ws:{tool.workspace_id}", s.rl_tool_workspace)]
    if conv is not None:
        checks.append((f"tool:conv:{conv.id}", s.rl_tool_conversation))
    for key, rule in checks:
        if not await ratelimit.allow(key, rule):
            return True
    return False


async def run(
    db: AsyncSession,
    tool: ApiTool,
    args: dict,
    *,
    conversation: Conversation | None = None,
    origin: str = "chat",
    actor_id: uuid.UUID | None = None,
) -> ToolOutcome:
    """Calls the tool and logs the call (added to the session; the caller commits)."""
    out = ToolOutcome(status="failed", request_line=request_line(tool))
    try:
        url, query, body, values = build_request(tool, args, conversation)
        out.args = values
    except ArgError as e:
        out.status, out.error_code, out.error = "invalid_args", "invalid_args", str(e)
        out.args = {k: v for k, v in (args or {}).items() if isinstance(k, str)} if isinstance(args, dict) else {}
        _log(db, tool, out, conversation, origin, actor_id)
        return out
    if await _limited(tool, conv=conversation, origin=origin, actor_id=actor_id):
        out.status, out.error_code, out.error = "rate_limited", "rate_limited", "Too many calls to this tool right now."
        _log(db, tool, out, conversation, origin, actor_id)
        return out
    headers = dict(tool.headers or {})
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        headers.update(auth_headers(tool))
        resp = await get_client().request(
            tool.method,
            url,
            headers=headers,
            params=query or None,
            json=body,
            timeout=float(min(max(tool.timeout_seconds, 1), 15)),
            max_bytes=min(max(tool.max_response_kb, 1), 256) * 1024,
        )
    except OutboundError as e:
        out.status = _OUTBOUND_STATUS.get(e.code, "failed")
        out.error_code, out.error, out.http_status = e.code, e.message, e.http_status
        _log(db, tool, out, conversation, origin, actor_id)
        return out
    except Exception:  # noqa: BLE001 - a broken API must never break the answer
        log.exception("tool %s failed", tool.id)
        out.status, out.error_code, out.error = "failed", "failed", "The call failed."
        _log(db, tool, out, conversation, origin, actor_id)
        return out
    out.http_status, out.latency_ms, out.response_bytes = resp.status, resp.latency_ms, len(resp.body)
    text = resp.body.decode("utf-8", errors="replace")
    if resp.content_type.endswith("json"):
        with contextlib.suppress(ValueError):
            text = json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
    out.body = text
    if 200 <= resp.status < 300:
        out.status = "ok"
    else:
        out.status, out.error_code, out.error = "http_error", "http_error", f"The API answered {resp.status}."
    _log(db, tool, out, conversation, origin, actor_id)
    return out


def _log(db: AsyncSession, tool: ApiTool, out: ToolOutcome, conv, origin: str, actor_id) -> None:
    db.add(
        ApiToolCall(
            workspace_id=tool.workspace_id,
            tool_id=tool.id,
            tool_name=tool.name,
            conversation_id=conv.id if conv is not None else None,
            origin=origin,
            actor_id=actor_id,
            status=out.status,
            http_status=out.http_status,
            latency_ms=out.latency_ms,
            response_bytes=out.response_bytes,
            error_code=out.error_code,
            args={k: v for k, v in out.args.items()}  # values only; headers and secrets are never logged
            if out.args
            else {},
            created_at=utcnow(),
        )
    )


def call_out(c: ApiToolCall) -> dict:
    return {
        "id": c.id,
        "tool_id": c.tool_id,
        "tool_name": c.tool_name,
        "conversation_id": c.conversation_id,
        "origin": c.origin,
        "status": c.status,
        "status_word": STATUS_WORDS.get(c.status, c.status),
        "http_status": c.http_status,
        "latency_ms": c.latency_ms,
        "response_bytes": c.response_bytes,
        "error_code": c.error_code,
        "args": c.args,
        "created_at": c.created_at,
    }
