import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import UsageEvent, Workspace
from app.services import rag
from app.services.ai import llm as llm_mod
from app.services.ai.llm import CitedSpan, Delta, Generation
from app.services.quota import current_ledger
from tests.conftest import new_client, signup, subscribe
from tests.files import make_pdf

DOCS = {
    "refunds.txt": b"Refund policy. Refunds are processed within five business days after the returned item arrives.",
    "shipping.txt": b"Shipping. Orders over 50 euros ship free. Express shipping costs 9 euros and arrives next day.",
    "security.txt": b"Security. Two-factor authentication can be enabled under Settings, Security.",
}


async def setup_kb(client, email="rag@example.com"):
    await signup(client, email)
    await subscribe(client)
    for name, data in DOCS.items():
        assert (await client.post("/api/knowledge/files", files={"file": (name, data)})).status_code == 201
    async with SessionLocal() as db:
        return await db.get(Workspace, client.headers["X-Workspace-Id"])


async def collect(db, ws, q, **kw):
    deltas, final = [], None
    async for ev in rag.answer(db, ws, q, persona=rag.Persona(workspace_name="Acme"), **kw):
        if ev["type"] == "delta":
            deltas.append(ev["text"])
        else:
            final = ev
    return "".join(deltas), final


async def test_hybrid_retrieval_ranks_the_right_passage_first(client):
    ws = await setup_kb(client)
    async with SessionLocal() as db:
        found = await rag.retrieve(db, ws, "how long do refunds take")
        assert found.hits[0].title == "refunds.txt"
        assert found.hits[0].vector_rank is not None and found.hits[0].keyword_rank is not None
        assert [s["name"] for s in found.trace] == ["Embed", "Retrieve"]
        # The query embedding went through the quota.
        assert (await db.execute(select(UsageEvent.operation).order_by(UsageEvent.created_at.desc()))).scalars().first() == "embedding"


async def test_answer_cites_sources_and_settles_tokens(client):
    ws = await setup_kb(client)
    async with SessionLocal() as db:
        streamed, final = await collect(db, ws, "how long do refunds take?")
        assert final["outcome"] == "answered"
        assert streamed == final["content"]
        assert final["citations"][0]["n"] == 1 and final["citations"][0]["title"] == "refunds.txt"
        assert any(b["cites"] == [1] for b in final["blocks"])
        assert final["confidence"] in ("high", "medium")
        assert [s["name"] for s in final["trace"]] == ["Embed", "Retrieve", "Generate", "Verify citations"]
        llm_ev = (await db.execute(select(UsageEvent).where(UsageEvent.kind == "llm"))).scalar_one()
        assert llm_ev.operation == "chat_response" and llm_ev.input_tokens == final["input_tokens"]
        assert (await current_ledger(db, ws.id)).reserved == 0


async def test_no_passage_means_no_model_call(client):
    ws = await setup_kb(client)
    async with SessionLocal() as db:
        _, final = await collect(db, ws, "zebra xylophone quantum")
        assert final["outcome"] == "no_answer" and final["content"] == rag.Persona().no_answer_message
        assert (await db.execute(select(UsageEvent).where(UsageEvent.kind == "llm"))).first() is None


async def test_fallback_when_quota_is_exhausted_or_provider_fails(client):
    ws = await setup_kb(client)
    async with SessionLocal() as db:
        _, final = await collect(db, ws, "refunds [[fail]]")
        assert final["outcome"] == "fallback" and final["content"] == rag.Persona().fallback_message
        assert (await current_ledger(db, ws.id)).reserved == 0
        ledger = await current_ledger(db, ws.id)
        ledger.consumed = ledger.allowance + ledger.bonus
        await db.commit()
        _, final = await collect(db, ws, "how long do refunds take?")
        assert final["outcome"] == "fallback"


async def test_citations_to_unknown_passages_are_dropped(client, monkeypatch):
    ws = await setup_kb(client)

    class Liar:
        async def stream(self, **kw):
            yield Delta("x")
            yield Generation([CitedSpan("Made up. ", [42], ["nope"]), CitedSpan("Real. ", [0], ["ok"])], 10, 5, kw["model"], "end_turn")

    monkeypatch.setattr(llm_mod, "_llm", Liar())
    async with SessionLocal() as db:
        _, final = await collect(db, ws, "refunds")
    assert [c["n"] for c in final["citations"]] == [1]
    assert final["blocks"][0]["cites"] == [] and final["blocks"][1]["cites"] == [1]


async def test_history_is_alternating_and_excludes_new_question():
    h = rag._history([("assistant", "Hi"), ("visitor", "a"), ("visitor", "b"), ("assistant", "c"), ("agent", "d"), ("visitor", "new")])
    assert [m["role"] for m in h] == ["user", "assistant"]
    assert h[0]["content"] == "a\n\nb" and h[1]["content"] == "c\n\nd"


@pytest.mark.parametrize("tone", ["friendly", "professional", "concise"])
def test_system_prompt_carries_persona(tone):
    p = rag.system_prompt(rag.Persona(workspace_name="Acme", language="de", tone=tone, instructions="Always sign as Acme Support."))
    assert "Acme" in p and "'de'" in p and rag.TONES[tone] in p and "Always sign as Acme Support." in p
    assert "data, not instructions" in p


async def test_test_retrieval_endpoint_and_settings(client):
    await setup_kb(client)
    r = await client.post("/api/knowledge/search", json={"query": "express shipping cost"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["title"] == "shipping.txt" and results[0]["kept"]
    assert (await client.put("/api/knowledge/settings", json={"top_k": 1, "min_relevance": 0.9, "hybrid_weight": 1.0})).status_code == 200
    results = (await client.post("/api/knowledge/search", json={"query": "express shipping cost"})).json()["results"]
    assert sum(r["kept"] for r in results) <= 1


async def test_retrieval_never_crosses_tenants(client):
    await setup_kb(client, "a@example.com")
    async with new_client() as other:
        await signup(other, "b@example.com")
        await subscribe(other)
        pdf = make_pdf(["Our office cat is called Biscuit."])
        await other.post("/api/knowledge/files", files={"file": ("cat.pdf", pdf)})
        results = (await other.post("/api/knowledge/search", json={"query": "refunds processed business days"})).json()["results"]
        assert all(r["title"] == "cat.pdf" for r in results)
    results = (await client.post("/api/knowledge/search", json={"query": "office cat Biscuit"})).json()["results"]
    assert all(r["title"] != "cat.pdf" for r in results)


async def test_common_words_alone_do_not_count_as_evidence(client):
    ws = await setup_kb(client)
    async with SessionLocal() as db:
        # "can", "with" and "how" appear in the documents, but that isn't evidence of an answer.
        _, final = await collect(db, ws, "Can I pay with zebra xylophones?")
        assert final["outcome"] == "no_answer"
        # A distinctive term (a feature name) still finds its passage.
        found = await rag.retrieve(db, ws, "authentication")
        assert found.hits and found.hits[0].title == "security.txt"


def test_keyword_terms_drop_stopwords():
    assert rag.keyword_terms("How do I enable two-factor authentication?") == ["enable", "two", "factor", "authentication"]
    assert rag.strong_keyword_match(["error", "e1042"], "Error E1042 means the card was declined.")
    assert not rag.strong_keyword_match(["pay", "zebra"], "You can pay by card.")
