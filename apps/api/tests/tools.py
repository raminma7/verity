"""Helpers for the API tool tests."""

ORDER_TOOL = {
    "name": "order_status",
    "display_name": "Order status",
    "description": "Look up the shipping status of a customer's order by its order number.",
    "method": "GET",
    "url_template": "https://shop.example.com/orders/{order_id}",
    "parameters": [
        {
            "key": "order_id",
            "label": "Order number",
            "type": "string",
            "description": "The order number",
            "location": "path",
            "pattern": r"[0-9]{5,8}",
        }
    ],
    "auth_type": "bearer",
    "secret": "sk_live_SUPERSECRET_123",
}


async def make_tool(c, activate: bool = True, **over) -> dict:
    body = {**ORDER_TOOL, **over}
    r = await c.post("/api/tools", json=body)
    assert r.status_code == 201, r.text
    t = r.json()
    if activate:
        r = await c.patch(f"/api/tools/{t['id']}", json={"status": "active"})
        assert r.status_code == 200, r.text
        t = r.json()
    return t
