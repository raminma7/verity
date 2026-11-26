import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import KnowledgeChunk, KnowledgePage, KnowledgeSource, Subscription, UsageEvent
from app.services import crawler as crawler_mod
from app.services.ingest import pipeline
from app.services.quota import current_ledger
from tests.conftest import new_client, signup, subscribe
from tests.files import make_blank_pdf, make_docx, make_pdf, make_xlsx
from tests.test_crawler import handler, public_resolver
from tests.test_workspaces import add_member


@pytest.fixture(autouse=True)
def fake_web(monkeypatch):
    """Crawls hit the in-memory test site instead of the network."""

    def make_fetcher():
        f = crawler_mod.Fetcher(transport=httpx.MockTransport(handler), resolve=public_resolver)
        f.allow_private = False
        return f

    monkeypatch.setattr(pipeline, "Fetcher", make_fetcher)


async def owner(client, email="kb@example.com"):
    await signup(client, email)
    await subscribe(client)
    return client.headers["X-Workspace-Id"]


async def set_limits(ws_id, **limits):
    async with SessionLocal() as db:
        sub = (await db.execute(select(Subscription).where(Subscription.workspace_id == ws_id))).scalar_one()
        sub.terms = {**sub.terms, "limits": {**sub.terms["limits"], **limits}}
        await db.commit()


async def upload(c, name, data):
    return await c.post("/api/knowledge/files", files={"file": (name, data)})


PDF = make_pdf(["Refunds are processed within five business days of approval.", "Shipping is free for orders over 50 euros."])


async def test_upload_is_parsed_chunked_embedded_and_metered(client):
    ws = await owner(client)
    r = await upload(client, "policies.pdf", PDF)
    assert r.status_code == 201, r.text
    src = (await client.get(f"/api/knowledge/sources/{r.json()['id']}")).json()
    assert src["status"] == "ready", src
    assert src["file_type"] == "pdf" and src["chunk_count"] >= 1
    assert [s["name"] for s in src["steps"]] == ["Parse", "Chunk", "Embed", "Index"]
    chunks = (await client.get(f"/api/knowledge/sources/{src['id']}/chunks")).json()
    assert chunks["items"][0]["meta"]["page"] == 1
    async with SessionLocal() as db:
        ev = (await db.execute(select(UsageEvent))).scalars().all()
        assert [e.kind for e in ev] == ["embedding"]
        assert (await current_ledger(db, ws)).reserved == 0
    audit = (await client.get("/api/workspace/audit-logs")).json()
    assert any(a["action"] == "knowledge.source_added" for a in audit)


async def test_every_supported_type(client):
    await owner(client)
    files = {
        "a.docx": make_docx([("h1", "Billing"), ("p", "Invoices are sent monthly.")]),
        "b.xlsx": make_xlsx({"Prices": [["Plan", "Price"], ["Starter", 29]]}),
        "c.csv": b"Plan,Price\nGrowth,99\n",
        "d.txt": b"Support is open Monday to Friday.",
    }
    for name, data in files.items():
        r = await upload(client, name, data)
        assert r.status_code == 201, r.text
    rows = (await client.get("/api/knowledge/sources")).json()
    assert {s["file_type"]: s["status"] for s in rows} == {"docx": "ready", "xlsx": "ready", "csv": "ready", "txt": "ready"}


async def test_bad_files_are_refused_or_explained(client):
    await owner(client)
    r = await upload(client, "virus.exe", b"MZ")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unsupported_type"
    r = await upload(client, "fake.pdf", b"not a pdf")
    assert r.json()["detail"]["code"] == "type_mismatch"
    r = await upload(client, "scan.pdf", make_blank_pdf())
    assert r.status_code == 201
    src = (await client.get(f"/api/knowledge/sources/{r.json()['id']}")).json()
    assert src["status"] == "failed" and src["error_code"] == "no_text_layer"
    assert "scanned" in src["error_message"]
    r = await upload(client, "again.pdf", make_blank_pdf())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_document"


async def test_document_limit_holds_under_parallel_uploads(client):
    ws = await owner(client)
    await set_limits(ws, documents=3)
    results = await asyncio.gather(*(upload(client, f"doc{i}.txt", f"Document number {i} text.".encode()) for i in range(8)))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 3 and codes.count(402) == 5
    assert all(r.json()["detail"]["limit"] == "documents" for r in results if r.status_code == 402)
    # Deleting one frees a slot.
    first = next(r for r in results if r.status_code == 201).json()["id"]
    assert (await client.delete(f"/api/knowledge/sources/{first}")).status_code == 202
    assert (await upload(client, "next.txt", b"Another document.")).status_code == 201


async def test_storage_limit(client):
    ws = await owner(client)
    await set_limits(ws, storage_mb=1)
    assert (await upload(client, "big.txt", b"a " * 400_000)).status_code == 201
    r = await upload(client, "big2.txt", b"b " * 400_000)
    assert r.status_code == 402 and r.json()["detail"]["limit"] == "storage_mb"


async def test_quota_exhausted_marks_document_failed(client):
    ws = await owner(client)
    async with SessionLocal() as db:
        ledger = await current_ledger(db, ws)
        ledger.consumed = ledger.allowance
        await db.commit()
    r = await upload(client, "a.txt", b"Some text to embed.")
    src = (await client.get(f"/api/knowledge/sources/{r.json()['id']}")).json()
    assert src["status"] == "failed" and src["error_code"] == "quota_exhausted"


async def test_reprocess_and_delete(client):
    await owner(client)
    sid = (await upload(client, "p.pdf", PDF)).json()["id"]
    r = await client.post(f"/api/knowledge/sources/{sid}/reprocess")
    assert r.status_code == 202 and r.json()["status"] == "ready"
    async with SessionLocal() as db:
        src = await db.get(KnowledgeSource, sid)
        assert src.version == 2
        n = (await db.execute(select(func.count()).where(KnowledgeChunk.source_id == src.id))).scalar_one()
        assert n == src.chunk_count  # replaced, not duplicated
    assert (await client.delete(f"/api/knowledge/sources/{sid}")).status_code == 202
    assert (await client.get(f"/api/knowledge/sources/{sid}")).status_code == 404
    async with SessionLocal() as db:
        assert (await db.execute(select(func.count()).select_from(KnowledgeChunk))).scalar_one() == 0


async def test_stale_run_does_not_overwrite_newer_version(client):
    await owner(client)
    sid = (await upload(client, "p.pdf", PDF)).json()["id"]
    async with SessionLocal() as db:
        src = await db.get(KnowledgeSource, sid)
        src.version = 5
        src.chunk_count = 999
        await db.commit()
    await pipeline.ingest_file(src.id, 1)  # an old job arriving late
    async with SessionLocal() as db:
        assert (await db.get(KnowledgeSource, sid)).chunk_count == 999


async def test_website_crawl_resync_and_limits(client):
    ws = await owner(client)
    r = await client.post("/api/knowledge/websites", json={"url": "docs.example.com", "max_depth": 2})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    src = (await client.get(f"/api/knowledge/sources/{sid}")).json()
    assert src["status"] == "ready", src
    assert src["domain"] == "docs.example.com" and src["page_count"] >= 2 and src["next_sync_at"]
    pages = (await client.get(f"/api/knowledge/sources/{sid}/pages")).json()
    assert any(p["status"] == "skipped" and p["error_code"] == "noindex" for p in pages)
    async with SessionLocal() as db:
        first_tokens = (await db.execute(select(func.sum(UsageEvent.input_tokens)))).scalar_one()
    # Re-sync: nothing changed, so nothing is re-embedded.
    await client.post(f"/api/knowledge/sources/{sid}/reprocess")
    async with SessionLocal() as db:
        assert (await db.execute(select(func.sum(UsageEvent.input_tokens)))).scalar_one() == first_tokens
    # Same domain twice is refused; the domain limit holds.
    r = await client.post("/api/knowledge/websites", json={"url": "https://www.docs.example.com/other"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_domain"
    await set_limits(ws, domains=1)
    r = await client.post("/api/knowledge/websites", json={"url": "https://help.example.net"})
    assert r.status_code == 402 and r.json()["detail"]["limit"] == "domains"


async def test_crawled_pages_limit_caps_the_crawl(client):
    ws = await owner(client)
    await set_limits(ws, crawled_pages=2)
    sid = (await client.post("/api/knowledge/websites", json={"url": "https://docs.example.com", "max_depth": 3})).json()["id"]
    src = (await client.get(f"/api/knowledge/sources/{sid}")).json()
    assert src["status"] == "ready" and src["page_count"] == 2 and src["notice"] == "page_limit_reached"
    stats = (await client.get("/api/knowledge/stats")).json()
    assert stats["usage"]["crawled_pages"] == 2 and stats["limits"]["crawled_pages"] == 2
    r = await client.post("/api/knowledge/websites", json={"url": "https://help.example.net"})
    assert r.status_code == 402 and r.json()["detail"]["limit"] == "crawled_pages"


async def test_private_address_website_fails_with_reason(client):
    await owner(client)
    sid = (await client.post("/api/knowledge/websites", json={"url": "https://evil.example.com"})).json()["id"]
    src = (await client.get(f"/api/knowledge/sources/{sid}")).json()
    assert src["status"] == "failed" and src["error_code"] == "blocked_address"


async def test_periodic_resync_queues_due_websites(client):
    await owner(client)
    sid = (await client.post("/api/knowledge/websites", json={"url": "https://docs.example.com", "max_depth": 1})).json()["id"]
    async with SessionLocal() as db:
        src = await db.get(KnowledgeSource, sid)
        src.next_sync_at = src.last_synced_at
        await db.commit()
    from app.jobs.tasks import sync_websites

    assert await sync_websites({}) == 1
    src = (await client.get(f"/api/knowledge/sources/{sid}")).json()
    assert src["status"] == "ready" and src["next_sync_at"] > src["last_synced_at"]


async def test_roles(client):
    await owner(client)
    sid = (await upload(client, "p.pdf", PDF)).json()["id"]
    async with new_client() as viewer, new_client() as agent:
        await add_member(client, viewer, "viewer@example.com", "viewer")
        await add_member(client, agent, "agent@example.com", "agent")
        for member in (viewer, agent):
            assert (await member.get("/api/knowledge/sources")).status_code == 200
            assert (await member.get(f"/api/knowledge/sources/{sid}/chunks")).status_code == 200
            assert (await upload(member, "x.txt", b"x")).status_code == 403
            assert (await member.post(f"/api/knowledge/sources/{sid}/reprocess")).status_code == 403
            assert (await member.delete(f"/api/knowledge/sources/{sid}")).status_code == 403


async def test_needs_subscription(client):
    await signup(client, "nosub@example.com")
    r = await client.get("/api/knowledge/sources")
    assert r.status_code == 402 and r.json()["detail"]["code"] == "subscription_required"


async def test_tenant_isolation(client):
    await owner(client, "a@example.com")
    sid = (await upload(client, "secret.pdf", PDF)).json()["id"]
    wid = (await client.post("/api/knowledge/websites", json={"url": "https://docs.example.com", "max_depth": 1})).json()["id"]
    async with new_client() as other:
        await owner(other, "b@example.com")
        assert (await other.get("/api/knowledge/sources")).json() == []
        for path in (f"/api/knowledge/sources/{sid}", f"/api/knowledge/sources/{sid}/chunks", f"/api/knowledge/sources/{wid}/pages"):
            assert (await other.get(path)).status_code == 404
        assert (await other.post(f"/api/knowledge/sources/{sid}/reprocess")).status_code == 404
        assert (await other.patch(f"/api/knowledge/sources/{wid}", json={"name": "mine"})).status_code == 404
        assert (await other.delete(f"/api/knowledge/sources/{sid}")).status_code == 404
        # B may add the same file and domain to its own workspace.
        assert (await upload(other, "secret.pdf", PDF)).status_code == 201
        assert (await other.post("/api/knowledge/websites", json={"url": "https://docs.example.com"})).status_code == 201
        stats = (await other.get("/api/knowledge/stats")).json()
        assert stats["usage"]["documents"] == 1
    async with SessionLocal() as db:
        assert (await db.get(KnowledgeSource, sid)).status == "ready"
        assert (await db.execute(select(func.count()).select_from(KnowledgePage))).scalar_one() >= 2
