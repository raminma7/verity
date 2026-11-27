"""Shared helpers for the Phase 3 tests (categories, forms, tickets)."""

from tests.conftest import signup, subscribe

RETURN_FIELDS = [
    {"key": "order_number", "label": "Order number", "type": "text", "required": True, "validation": {"pattern": r"[0-9]{5,8}"}},
    {"key": "email", "label": "Email", "type": "email", "required": True},
    {"key": "reason", "label": "Reason", "type": "select", "required": True, "options": ["Damaged", "Wrong size", "Changed my mind"]},
    {"key": "items", "label": "Items", "type": "multiselect", "options": ["Shoes", "Shirt", "Hat"], "validation": {"max_selected": 2}},
    {"key": "quantity", "label": "Quantity", "type": "number", "validation": {"min": 1, "max": 10, "integer": True}},
    {"key": "purchased_on", "label": "Purchase date", "type": "date", "validation": {"min": "2020-01-01"}},
    {"key": "details", "label": "Details", "type": "textarea", "validation": {"max_length": 500}},
    {"key": "consent", "label": "I agree to the return policy", "type": "checkbox", "required": True},
]

VALID_RETURN = {
    "order_number": "12345",
    "email": "Jane@Example.com",
    "reason": "Damaged",
    "items": ["Shoes"],
    "quantity": 1,
    "purchased_on": "2026-10-01",
    "details": "The sole came off.",
    "consent": True,
}


async def owner(client, email="owner3@example.com") -> dict:
    me = await signup(client, email)
    await subscribe(client)
    return me


async def category(client, name="Returns") -> dict:
    r = await client.post("/api/ticket-categories", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def form(client, category_id, **over) -> dict:
    body = {
        "name": "Return request",
        "category_id": category_id,
        "activation_condition": "The customer wants to return or exchange an order.",
        "status": "active",
        "fields": RETURN_FIELDS,
        **over,
    }
    r = await client.post("/api/forms", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def ticket(client, **over) -> dict:
    r = await client.post("/api/tickets", json={"subject": "Printer on fire", **over})
    assert r.status_code == 201, r.text
    return r.json()
