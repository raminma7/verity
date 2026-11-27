"""Checking a tool definition and describing it to the model.

The host is fixed by the URL template; parameters only fill path segments (percent-encoded), the query string
or a JSON body, so nothing the model or a customer says can point a call somewhere else.
"""

import re
from urllib.parse import urlsplit

import regex

from app.core.errors import ApiError
from app.core.ssrf import ip_blocked, is_ip
from app.models.integrations import PARAM_LOCATIONS, PARAM_SOURCES, PARAM_TYPES

NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
FORBIDDEN_HEADERS = {
    "host",
    "content-length",
    "content-type",
    "transfer-encoding",
    "connection",
    "cookie",
    "authorization",
    "proxy-authorization",
    "te",
    "upgrade",
    "expect",
}
MAX_PARAMS = 20
MAX_HEADERS = 10


def _bad(code: str, message: str, field: str) -> ApiError:
    return ApiError(422, code, message, field=field)


def forbidden_header(name: str) -> bool:
    n = name.lower()
    return n in FORBIDDEN_HEADERS or n.startswith(("proxy-", "x-forwarded-", "sec-"))


def check_url(url: str) -> tuple[str, list[str]]:
    """The template's host and its path placeholders, in order."""
    try:
        parts = urlsplit(url.strip())
        _ = parts.port
    except ValueError:
        raise _bad("tool_invalid_url", "Enter a valid address.", "url_template") from None
    if parts.scheme != "https" or not parts.hostname:
        raise _bad("tool_invalid_url", "The address must start with https://.", "url_template")
    if parts.username or parts.password or parts.fragment:
        raise _bad("tool_invalid_url", "The address can't contain credentials or a #fragment.", "url_template")
    if "{" in parts.netloc or "{" in parts.query or "}" in parts.netloc or "}" in parts.query:
        raise _bad("tool_invalid_url", "Placeholders are only allowed in the path, not the host or query.", "url_template")
    host = parts.hostname
    if is_ip(host) and ip_blocked(host):
        raise _bad("blocked_address", "This address points to a private network, which Verity doesn't connect to.", "url_template")
    if "." not in host or host.endswith((".local", ".internal", ".localhost")) or host == "localhost":
        raise _bad("blocked_address", "Enter a public address. Verity doesn't connect to private networks.", "url_template")
    keys = PLACEHOLDER.findall(parts.path)
    for k in keys:
        if not KEY.match(k):
            raise _bad("tool_invalid_url", f"“{{{k}}}” isn't a valid placeholder. Use lowercase letters, digits and _.", "url_template")
    if len(set(keys)) != len(keys):
        raise _bad("tool_invalid_url", "Each placeholder may appear only once.", "url_template")
    return host, keys


def check_parameters(params: list[dict], *, method: str, placeholders: list[str]) -> list[dict]:
    if len(params) > MAX_PARAMS:
        raise _bad("tool_invalid_parameters", f"A tool can have at most {MAX_PARAMS} parameters.", "parameters")
    out, seen = [], set()
    for i, p in enumerate(params):
        f = f"parameters.{i}"
        key = str(p.get("key", ""))
        if not KEY.match(key):
            raise _bad("tool_invalid_parameters", "Parameter keys use lowercase letters, digits and _.", f"{f}.key")
        if key in seen:
            raise _bad("tool_invalid_parameters", f"Two parameters are called “{key}”.", f"{f}.key")
        seen.add(key)
        ptype = p.get("type", "string")
        loc = p.get("location", "query")
        source = p.get("source", "ai")
        if ptype not in PARAM_TYPES or loc not in PARAM_LOCATIONS or source not in PARAM_SOURCES:
            raise _bad("tool_invalid_parameters", "Unknown parameter type, location or source.", f)
        if loc == "body" and method != "POST":
            raise _bad("tool_invalid_parameters", "Body parameters need the POST method.", f"{f}.location")
        if source != "ai" and ptype != "string":
            raise _bad("tool_invalid_parameters", "Values from the conversation are text.", f"{f}.type")
        enum = p.get("enum") or []
        if len(enum) > 50:
            raise _bad("tool_invalid_parameters", "At most 50 choices.", f"{f}.enum")
        if enum and (source != "ai" or ptype == "boolean"):
            raise _bad("tool_invalid_parameters", "Choices are only for values the AI picks.", f"{f}.enum")
        enum = [_coerce_enum(v, ptype, f) for v in enum]
        pattern = (p.get("pattern") or "").strip() or None
        if pattern:
            if ptype != "string" or len(pattern) > 200:
                raise _bad("tool_invalid_parameters", "Patterns are for text values (200 characters at most).", f"{f}.pattern")
            try:
                regex.compile(pattern)
            except regex.error:
                raise _bad("tool_invalid_parameters", "This pattern isn't a valid regular expression.", f"{f}.pattern") from None
        max_length = p.get("max_length")
        if max_length is not None and not (isinstance(max_length, int) and 1 <= max_length <= 1000):
            raise _bad("tool_invalid_parameters", "The maximum length is between 1 and 1,000.", f"{f}.max_length")
        out.append(
            {
                "key": key,
                "label": str(p.get("label") or key)[:80],
                "type": ptype,
                "description": str(p.get("description") or "")[:300],
                "required": bool(p.get("required", False)) or loc == "path",
                "location": loc,
                "source": source,
                "enum": enum,
                "pattern": pattern,
                "max_length": max_length,
            }
        )
    path_keys = [p["key"] for p in out if p["location"] == "path"]
    if sorted(path_keys) != sorted(placeholders):
        raise _bad(
            "tool_url_params_mismatch",
            "Every {placeholder} in the address needs a path parameter with the same key, and every path parameter a placeholder.",
            "parameters",
        )
    return out


def _coerce_enum(v, ptype: str, field: str):
    try:
        if ptype == "integer":
            return int(v)
        if ptype == "number":
            return float(v)
    except (TypeError, ValueError):
        raise _bad("tool_invalid_parameters", "A choice doesn't match the parameter's type.", f"{field}.enum") from None
    return str(v)[:200]


def check_headers(headers: dict, *, auth_type: str, auth_header: str | None) -> dict:
    if len(headers) > MAX_HEADERS:
        raise _bad("tool_header_not_allowed", f"At most {MAX_HEADERS} headers.", "headers")
    out = {}
    for name, value in headers.items():
        if not HEADER_NAME.match(name) or forbidden_header(name):
            raise _bad("tool_header_not_allowed", f"The header “{name}” can't be set here.", "headers")
        if auth_type == "api_key" and auth_header and name.lower() == auth_header.lower():
            raise _bad("tool_header_not_allowed", "The key header is set by the authentication settings.", "headers")
        value = str(value)
        if len(value) > 500 or any(c in value for c in "\r\n\0"):
            raise _bad("tool_header_not_allowed", f"The value of “{name}” isn't allowed.", "headers")
        out[name] = value
    return out


def check_auth(auth_type: str, auth_header: str | None) -> str | None:
    if auth_type == "api_key":
        h = (auth_header or "").strip()
        if not HEADER_NAME.match(h) or forbidden_header(h):
            raise _bad("tool_header_not_allowed", "Enter the header the API reads the key from, such as X-Api-Key.", "auth_header")
        return h
    return None


def input_schema(params: list[dict]) -> dict:
    """The JSON schema the model fills. Only values the AI picks; Verity fills the rest from the conversation."""
    props, required = {}, []
    for p in params:
        if p["source"] != "ai":
            continue
        s: dict = {"type": p["type"]}
        desc = p["description"] or p["label"]
        if p["max_length"]:
            desc += f" (at most {p['max_length']} characters)"
        s["description"] = desc
        if p["enum"]:
            s["enum"] = p["enum"]
        props[p["key"]] = s
        required.append(p["key"])
    # strict tools need every property listed as required; optional ones accept null.
    for p in params:
        if p["source"] == "ai" and not p["required"]:
            props[p["key"]] = {"anyOf": [props[p["key"]], {"type": "null"}], "description": props[p["key"]]["description"]}
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def claude_tool(tool) -> dict:
    return {
        "name": tool.name,
        "description": (tool.description or tool.display_name)[:1500],
        "input_schema": input_schema(tool.parameters),
        "strict": True,
    }
