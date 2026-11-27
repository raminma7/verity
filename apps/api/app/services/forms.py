"""Chatbot forms: field definitions, value validation and the built-in contact form.

Field definitions are checked when a form is saved; submitted values are checked again on the server when a
visitor submits, whatever the widget did. The same value check runs on AI prefill, so a value the model
invents in the wrong shape is dropped instead of shown.

Patterns run through the `regex` package with a timeout, so a slow pattern can't stall a request.
"""

import math
import uuid
from datetime import date

import regex
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import FIELD_TYPES, Form, TicketCategory

MAX_FIELDS = 30
MAX_OPTIONS = 50
PATTERN_TIMEOUT = 0.05  # seconds
KEY = regex.compile(r"^[a-z][a-z0-9_]{0,39}$")
DEFAULT_MAX = {"text": 500, "textarea": 4000}
HARD_MAX = {"text": 2000, "textarea": 10000}
VALIDATION_KEYS = {
    "text": {"min_length", "max_length", "pattern"},
    "textarea": {"min_length", "max_length"},
    "email": set(),
    "number": {"min", "max", "integer"},
    "select": set(),
    "multiselect": {"min_selected", "max_selected"},
    "date": {"min", "max"},
    "checkbox": set(),
}
DEFAULT_CATEGORY = "General"


def _bad(message: str, field: str | None = None) -> ApiError:
    return ApiError(422, "invalid_form_definition", message, **({"field_key": field} if field else {}))


def _iso(v, label: str, key: str) -> str:
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise _bad(f"{label}: use a date like 2026-01-31.", key) from None


def _num(v, label: str, key: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise _bad(f"{label}: limits must be numbers.", key)
    return v


def normalize_fields(raw: list) -> list[dict]:
    """Checks and normalizes field definitions. Raises invalid_form_definition with the offending key."""
    if not isinstance(raw, list):
        raise _bad("Fields must be a list.")
    if len(raw) > MAX_FIELDS:
        raise _bad(f"A form can have at most {MAX_FIELDS} fields.")
    out: list[dict] = []
    keys: set[str] = set()
    for i, f in enumerate(raw):
        if not isinstance(f, dict):
            raise _bad(f"Field {i + 1} isn't valid.")
        key = str(f.get("key", "")).strip()
        label = " ".join(str(f.get("label", "")).split())[:120]
        ftype = f.get("type")
        if not KEY.match(key):
            raise _bad(f"Field {i + 1}: the key must start with a letter and use only a–z, 0–9 and _ (max 40).", key or None)
        if key in keys:
            raise _bad(f"Two fields use the key “{key}”. Keys must be unique.", key)
        keys.add(key)
        if not label:
            raise _bad(f"Field “{key}” needs a label.", key)
        if ftype not in FIELD_TYPES:
            raise _bad(f"Field “{label}”: choose one of {', '.join(FIELD_TYPES)}.", key)
        field = {
            "key": key,
            "label": label,
            "type": ftype,
            "required": bool(f.get("required", False)),
            "help": " ".join(str(f.get("help") or "").split())[:300],
            "placeholder": " ".join(str(f.get("placeholder") or "").split())[:120],
            "options": [],
            "validation": {},
        }
        if ftype in ("select", "multiselect"):
            opts = []
            for o in f.get("options") or []:
                o = " ".join(str(o).split())[:120]
                if o and o not in opts:
                    opts.append(o)
            if not opts:
                raise _bad(f"Field “{label}” needs at least one option.", key)
            if len(opts) > MAX_OPTIONS:
                raise _bad(f"Field “{label}” can have at most {MAX_OPTIONS} options.", key)
            field["options"] = opts
        v_in = f.get("validation") or {}
        if not isinstance(v_in, dict):
            raise _bad(f"Field “{label}”: validation must be an object.", key)
        unknown = set(v_in) - VALIDATION_KEYS[ftype]
        if unknown:
            raise _bad(f"Field “{label}”: {', '.join(sorted(unknown))} doesn't apply to {ftype} fields.", key)
        v: dict = {}
        if ftype in ("text", "textarea"):
            hard = HARD_MAX[ftype]
            lo = int(_num(v_in.get("min_length", 0), label, key))
            hi = int(_num(v_in.get("max_length", DEFAULT_MAX[ftype]), label, key))
            if not 0 <= lo <= hi <= hard:
                raise _bad(f"Field “{label}”: lengths must be between 0 and {hard}, minimum first.", key)
            v["min_length"], v["max_length"] = lo, hi
            if v_in.get("pattern"):
                p = str(v_in["pattern"])
                if len(p) > 200:
                    raise _bad(f"Field “{label}”: the pattern is too long (max 200 characters).", key)
                try:
                    regex.compile(p)
                except regex.error:
                    raise _bad(f"Field “{label}”: the pattern isn't a valid regular expression.", key) from None
                v["pattern"] = p
        elif ftype == "number":
            if v_in.get("integer"):
                v["integer"] = True
            for b in ("min", "max"):
                if v_in.get(b) is not None:
                    v[b] = _num(v_in[b], label, key)
            if "min" in v and "max" in v and v["min"] > v["max"]:
                raise _bad(f"Field “{label}”: the minimum is above the maximum.", key)
        elif ftype == "multiselect":
            for b in ("min_selected", "max_selected"):
                if v_in.get(b) is not None:
                    n = int(_num(v_in[b], label, key))
                    if not 0 <= n <= len(field["options"]):
                        raise _bad(f"Field “{label}”: {b.replace('_', ' ')} must be between 0 and the number of options.", key)
                    v[b] = n
            if v.get("min_selected", 0) > v.get("max_selected", len(field["options"])):
                raise _bad(f"Field “{label}”: the minimum selection is above the maximum.", key)
        elif ftype == "date":
            for b in ("min", "max"):
                if v_in.get(b):
                    v[b] = _iso(v_in[b], label, key)
            if "min" in v and "max" in v and v["min"] > v["max"]:
                raise _bad(f"Field “{label}”: the earliest date is after the latest.", key)
        field["validation"] = v
        out.append(field)
    return out


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def check_value(field: dict, v):
    """Returns the clean value, or raises ValueError with a message for the visitor."""
    t, rules = field["type"], field.get("validation") or {}
    if t in ("text", "textarea"):
        if not isinstance(v, str | int | float) or isinstance(v, bool):
            raise ValueError("Enter text.")
        s = str(v).strip() if t == "text" else str(v).strip("\n ")
        if t == "text":
            s = " ".join(s.split())
        hi = rules.get("max_length", DEFAULT_MAX[t])
        if len(s) < rules.get("min_length", 0):
            raise ValueError(f"Use at least {rules['min_length']} characters.")
        if len(s) > hi:
            raise ValueError(f"Use at most {hi} characters.")
        if rules.get("pattern"):
            try:
                ok = regex.fullmatch(rules["pattern"], s, timeout=PATTERN_TIMEOUT) is not None
            except TimeoutError:
                ok = False
            if not ok:
                raise ValueError("This doesn't look right. Check the format.")
        return s
    if t == "email":
        try:
            return validate_email(str(v).strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise ValueError("Enter a valid email address.") from None
    if t == "number":
        if isinstance(v, bool):
            raise ValueError("Enter a number.")
        try:
            n = float(str(v).strip().replace(",", ".")) if isinstance(v, str) else float(v)
        except (TypeError, ValueError):
            raise ValueError("Enter a number.") from None
        if not math.isfinite(n):
            raise ValueError("Enter a number.")
        if rules.get("integer"):
            if n != int(n):
                raise ValueError("Enter a whole number.")
            n = int(n)
        if "min" in rules and n < rules["min"]:
            raise ValueError(f"Enter {rules['min']:g} or more.")
        if "max" in rules and n > rules["max"]:
            raise ValueError(f"Enter {rules['max']:g} or less.")
        return int(n) if float(n).is_integer() else n
    if t == "select":
        if v not in field["options"]:
            raise ValueError("Choose one of the options.")
        return v
    if t == "multiselect":
        if not isinstance(v, list) or any(x not in field["options"] for x in v):
            raise ValueError("Choose from the options.")
        picked = [o for o in field["options"] if o in v]
        if len(picked) < rules.get("min_selected", 0):
            raise ValueError(f"Choose at least {rules['min_selected']}.")
        if len(picked) > rules.get("max_selected", len(field["options"])):
            raise ValueError(f"Choose at most {rules['max_selected']}.")
        return picked
    if t == "date":
        try:
            d = date.fromisoformat(str(v).strip()).isoformat()
        except ValueError:
            raise ValueError("Enter a date.") from None
        if rules.get("min") and d < rules["min"]:
            raise ValueError(f"Choose {rules['min']} or later.")
        if rules.get("max") and d > rules["max"]:
            raise ValueError(f"Choose {rules['max']} or earlier.")
        return d
    if t == "checkbox":
        if not isinstance(v, bool):
            raise ValueError("Tick or untick this box.")
        return v
    raise ValueError("Unsupported field.")


def validate_values(fields: list[dict], values: dict) -> tuple[dict, dict[str, str]]:
    """Clean values for every field, and an error per field key. Unknown keys are ignored."""
    clean: dict = {}
    errors: dict[str, str] = {}
    values = values if isinstance(values, dict) else {}
    for f in fields:
        v = values.get(f["key"])
        if f["type"] == "checkbox":
            v = bool(v) if v is not None else False
            if f["required"] and not v:
                errors[f["key"]] = "Tick this box to continue."
                continue
            clean[f["key"]] = v
            continue
        if _empty(v):
            if f["required"]:
                errors[f["key"]] = "This field is required."
            continue
        try:
            clean[f["key"]] = check_value(f, v)
        except ValueError as e:
            errors[f["key"]] = str(e)
    return clean, errors


def sanitize_prefill(fields: list[dict], values: dict) -> dict:
    """Keeps only prefill values that pass their field's check; never fills a checkbox (consent is the customer's)."""
    out = {}
    for f in fields:
        v = (values or {}).get(f["key"])
        if f["type"] == "checkbox" or _empty(v):
            continue
        try:
            out[f["key"]] = check_value(f, v)
        except ValueError:
            continue
    return out


def public_fields(fields: list[dict]) -> list[dict]:
    return [{k: f.get(k) for k in ("key", "label", "type", "required", "help", "placeholder", "options", "validation")} for f in fields]


def display_value(field: dict | None, v) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return "" if v is None else str(v)


# ---- Defaults -----------------------------------------------------------------------------------

CONTACT_FIELDS = [
    {"key": "name", "label": "Your name", "type": "text", "required": False},
    {"key": "email", "label": "Your email", "type": "email", "required": True, "help": "We’ll reply to this address."},
    {"key": "message", "label": "How can we help?", "type": "textarea", "required": True},
]


async def default_category(db: AsyncSession, workspace_id: uuid.UUID) -> TicketCategory:
    """The workspace's "General" category, created the first time it's needed (or the first active one)."""
    rows = (
        (
            await db.execute(
                select(TicketCategory)
                .where(TicketCategory.workspace_id == workspace_id, TicketCategory.archived_at.is_(None))
                .order_by(TicketCategory.position, TicketCategory.created_at)
            )
        )
        .scalars()
        .all()
    )
    for c in rows:
        if c.name.lower() == DEFAULT_CATEGORY.lower():
            return c
    if rows:
        return rows[0]
    c = TicketCategory(workspace_id=workspace_id, name=DEFAULT_CATEGORY, description="Anything that doesn't fit elsewhere.", position=0)
    db.add(c)
    await db.flush()
    return c


async def contact_form(db: AsyncSession, workspace_id: uuid.UUID) -> Form:
    """The built-in "Contact the team" form: offered when nobody answers a handoff and no other form fits."""
    f = (await db.execute(select(Form).where(Form.workspace_id == workspace_id, Form.is_contact.is_(True)))).scalar_one_or_none()
    if f is not None:
        return f
    cat = await default_category(db, workspace_id)
    f = Form(
        workspace_id=workspace_id,
        name="Contact the team",
        description="Leave your email and we’ll open a ticket. Someone from the team will get back to you.",
        category_id=cat.id,
        status="active",
        fields=normalize_fields(CONTACT_FIELDS),
        submit_label="Send",
        success_message="",
        is_contact=True,
    )
    db.add(f)
    await db.flush()
    return f
