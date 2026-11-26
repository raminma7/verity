from tests.phase3 import category, form, owner


async def test_create_rename_and_unique_names(client):
    await owner(client)
    a = await category(client, "Returns")
    b = await category(client, "Billing")
    assert (a["position"], b["position"]) == (1, 2)
    r = await client.post("/api/ticket-categories", json={"name": "  returns "})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "category_name_taken"
    r = await client.patch(f"/api/ticket-categories/{b['id']}", json={"name": "Payments", "description": "Invoices and cards"})
    assert r.status_code == 200 and r.json()["name"] == "Payments"
    r = await client.patch(f"/api/ticket-categories/{b['id']}", json={"name": "Returns"})
    assert r.json()["detail"]["code"] == "category_name_taken"
    assert (await client.post("/api/ticket-categories", json={"name": ""})).status_code == 422


async def test_order_must_list_every_active_category_once(client):
    await owner(client)
    a, b, c = [await category(client, n) for n in ("A", "B", "C")]
    r = await client.put("/api/ticket-categories/order", json={"ids": [c["id"], a["id"], b["id"]]})
    assert r.status_code == 200
    listed = (await client.get("/api/ticket-categories")).json()
    assert [x["name"] for x in listed] == ["C", "A", "B"]
    for bad in ([a["id"], b["id"]], [a["id"], a["id"], b["id"], c["id"]]):
        r = await client.put("/api/ticket-categories/order", json={"ids": bad})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_order"


async def test_archive_restore_and_in_use(client):
    await owner(client)
    a = await category(client, "Returns")
    b = await category(client, "Old")
    f = await form(client, a["id"])
    r = await client.post(f"/api/ticket-categories/{a['id']}/archive")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "category_in_use" and r.json()["detail"]["forms"] == ["Return request"]
    r = await client.post(f"/api/ticket-categories/{b['id']}/archive")
    assert r.status_code == 200 and r.json()["archived"]
    assert [x["name"] for x in (await client.get("/api/ticket-categories")).json()] == ["Returns"]
    assert len((await client.get("/api/ticket-categories?include_archived=true")).json()) == 2
    # Archived categories can't receive new tickets or forms.
    r = await client.post("/api/tickets", json={"subject": "x", "category_id": b["id"]})
    assert r.json()["detail"]["code"] == "category_archived"
    r = await client.patch(f"/api/forms/{f['id']}", json={"category_id": b["id"]})
    assert r.json()["detail"]["code"] == "category_archived"
    # A new category may take the archived one's name; restoring then conflicts.
    await category(client, "Old")
    r = await client.post(f"/api/ticket-categories/{b['id']}/restore")
    assert r.json()["detail"]["code"] == "category_name_taken"


async def test_category_changes_are_audited(client):
    await owner(client)
    a = await category(client, "Returns")
    await client.patch(f"/api/ticket-categories/{a['id']}", json={"name": "Refunds"})
    log = (await client.get("/api/workspace/audit-logs")).json()
    actions = [e["action"] for e in log]
    assert "ticket_category.created" in actions and "ticket_category.updated" in actions
