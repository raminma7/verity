import pytest

from app.core.errors import ApiError
from app.services.forms import normalize_fields, sanitize_prefill, validate_values
from tests.phase3 import RETURN_FIELDS, VALID_RETURN, category, form, owner

FIELDS = normalize_fields(RETURN_FIELDS)


def test_every_field_type_accepts_valid_values():
    clean, errors = validate_values(FIELDS, VALID_RETURN)
    assert errors == {}
    assert clean["email"] == "jane@example.com" and clean["quantity"] == 1 and clean["consent"] is True
    assert clean["items"] == ["Shoes"] and clean["purchased_on"] == "2026-10-01"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("order_number", "12", "format"),
        ("order_number", "", "required"),
        ("email", "not-an-email", "valid email"),
        ("reason", "Bored", "options"),
        ("items", ["Shoes", "Shirt", "Hat"], "at most 2"),
        ("items", ["Socks"], "from the options"),
        ("quantity", 0, "1 or more"),
        ("quantity", 2.5, "whole number"),
        ("quantity", "lots", "number"),
        ("purchased_on", "2019-05-01", "or later"),
        ("purchased_on", "yesterday", "date"),
        ("details", "x" * 501, "at most 500"),
        ("consent", False, "Tick"),
    ],
)
def test_invalid_values_report_per_field(key, value, message):
    _, errors = validate_values(FIELDS, {**VALID_RETURN, key: value})
    assert list(errors) == [key] and message.lower() in errors[key].lower()


def test_optional_fields_may_be_empty_and_unknown_keys_are_ignored():
    values = {k: v for k, v in VALID_RETURN.items() if k in ("order_number", "email", "reason", "consent")}
    clean, errors = validate_values(FIELDS, {**values, "hacker": "<script>"})
    assert errors == {} and "hacker" not in clean and "items" not in clean


def test_slow_patterns_time_out_instead_of_hanging():
    fields = normalize_fields([{"key": "x", "label": "X", "type": "text", "validation": {"pattern": "(a+)+$", "max_length": 2000}}])
    _, errors = validate_values(fields, {"x": "a" * 40 + "!"})
    assert "x" in errors


def test_prefill_keeps_only_valid_values_and_never_ticks_consent():
    out = sanitize_prefill(FIELDS, {"order_number": "12345", "email": "bad", "reason": "Nope", "consent": True, "quantity": "3"})
    assert out == {"order_number": "12345", "quantity": 3}


@pytest.mark.parametrize(
    "fields",
    [
        [{"key": "Bad Key", "label": "X", "type": "text"}],
        [{"key": "a", "label": "A", "type": "text"}, {"key": "a", "label": "B", "type": "text"}],
        [{"key": "a", "label": "", "type": "text"}],
        [{"key": "a", "label": "A", "type": "rating"}],
        [{"key": "a", "label": "A", "type": "select", "options": []}],
        [{"key": "a", "label": "A", "type": "text", "validation": {"pattern": "("}}],
        [{"key": "a", "label": "A", "type": "text", "validation": {"min_length": 10, "max_length": 5}}],
        [{"key": "a", "label": "A", "type": "number", "validation": {"min": 5, "max": 1}}],
        [{"key": "a", "label": "A", "type": "email", "validation": {"pattern": ".*"}}],
        [{"key": "a", "label": "A", "type": "date", "validation": {"min": "soon"}}],
        [{"key": "a", "label": "A", "type": "multiselect", "options": ["x"], "validation": {"max_selected": 3}}],
        [{"key": f"f{i}", "label": "F", "type": "text"} for i in range(31)],
    ],
)
def test_invalid_definitions_are_refused(fields):
    with pytest.raises(ApiError) as e:
        normalize_fields(fields)
    assert e.value.detail["code"] == "invalid_form_definition"


async def test_builder_api_versions_fields_and_links_chatbots(client):
    await owner(client)
    cat = await category(client)
    bot = (await client.post("/api/chatbots", json={"name": "Help"})).json()
    f = await form(client, cat["id"], chatbot_ids=[bot["id"]])
    assert f["version"] == 1 and f["chatbot_ids"] == [bot["id"]] and f["category"] == "Returns"
    r = await client.patch(f"/api/forms/{f['id']}", json={"name": "Returns & exchanges"})
    assert r.json()["version"] == 1
    r = await client.patch(f"/api/forms/{f['id']}", json={"fields": RETURN_FIELDS[:3]})
    assert r.json()["version"] == 2 and len(r.json()["fields"]) == 3
    r = await client.patch(f"/api/forms/{f['id']}", json={"fields": [{"key": "x", "label": "X", "type": "nope"}]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_form_definition"
    r = await client.patch(f"/api/forms/{f['id']}", json={"chatbot_ids": []})
    assert r.json()["chatbot_ids"] == []
    r = await client.post("/api/forms", json={"name": "Empty", "category_id": cat["id"], "status": "active"})
    assert r.status_code == 422
    r = await client.post(f"/api/forms/{f['id']}/archive")
    assert r.json()["status"] == "archived"
    assert f["id"] not in [x["id"] for x in (await client.get("/api/forms")).json()]
    assert (await client.patch(f"/api/forms/{f['id']}", json={"name": "z"})).json()["detail"]["code"] == "form_archived"
    assert (await client.post(f"/api/forms/{f['id']}/restore")).json()["status"] == "draft"


async def test_contact_form_always_exists_and_keeps_email(client):
    await owner(client)
    forms = (await client.get("/api/forms")).json()
    contact = next(x for x in forms if x["is_contact"])
    assert contact["category"] == "General" and contact["status"] == "active"
    assert [x["key"] for x in contact["fields"]] == ["name", "email", "message"]
    r = await client.patch(f"/api/forms/{contact['id']}", json={"fields": [{"key": "message", "label": "Message", "type": "textarea"}]})
    assert r.json()["detail"]["code"] == "contact_form_needs_email"
    assert (await client.post(f"/api/forms/{contact['id']}/archive")).json()["detail"]["code"] == "contact_form_required"
    assert (await client.patch(f"/api/forms/{contact['id']}", json={"status": "draft"})).json()["detail"]["code"] == "contact_form_required"
    # Calling the list again doesn't create a second one.
    assert sum(x["is_contact"] for x in (await client.get("/api/forms")).json()) == 1
