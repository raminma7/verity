"""Every AI call settles through the quota; failures release the hold."""

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import TokenReservation, UsageEvent
from app.services.ai.embeddings import get_embedder
from app.services.ai.llm import Generation, LLMError, Passage, get_llm
from app.services.ai.metered import Meter
from app.services.quota import QuotaExhausted, current_ledger
from tests.conftest import signup, subscribe


async def workspace(client) -> str:
    await signup(client, "meter@example.com")
    await subscribe(client)
    return client.headers["X-Workspace-Id"]


async def test_embedding_call_settles_as_embedding(client):
    ws = await workspace(client)
    async with SessionLocal() as db:
        emb = get_embedder()
        async with Meter(db, ws, model=emb.model, operation="embedding", kind="embedding", est_input=50, est_output=0) as m:
            out = await emb.embed(["Refunds take five days."], "document")
            await m.settle(out.tokens, 0)
        ev = (await db.execute(select(UsageEvent))).scalar_one()
        assert ev.kind == "embedding" and ev.operation == "embedding" and ev.input_tokens == out.tokens
        ledger = await current_ledger(db, ws)
        assert ledger.reserved == 0 and ledger.consumed == ev.weighted_tokens


async def test_failed_call_releases_and_costs_nothing(client):
    ws = await workspace(client)
    async with SessionLocal() as db:
        try:
            async with Meter(db, ws, model="claude-opus-5-5", operation="chat_response", kind="llm", est_input=1000, est_output=2048):
                async for _ in get_llm().stream(
                    model="claude-opus-5-5", system="s", history=[], question="[[fail]]", passages=[], max_tokens=2048
                ):
                    pass
        except LLMError:
            pass
        ledger = await current_ledger(db, ws)
        assert ledger.reserved == 0 and ledger.consumed == 0
        assert (await db.execute(select(TokenReservation.status))).scalar_one() == "released"
        assert (await db.execute(select(UsageEvent))).first() is None


async def test_generation_settles_real_usage(client):
    ws = await workspace(client)
    async with SessionLocal() as db:
        async with Meter(db, ws, model="claude-opus-5-5", operation="chat_response", kind="llm", est_input=500, est_output=2048) as m:
            final = None
            async for ev in get_llm().stream(
                model="claude-opus-5-5",
                system="s",
                history=[],
                question="refunds?",
                passages=[Passage("a.pdf", "a", "Refunds take five days.")],
                max_tokens=2048,
            ):
                if isinstance(ev, Generation):
                    final = ev
            await m.settle(final.input_tokens, final.output_tokens)
        ev = (await db.execute(select(UsageEvent))).scalar_one()
        assert (ev.input_tokens, ev.output_tokens) == (final.input_tokens, final.output_tokens)
        assert ev.weighted_tokens == final.input_tokens * 5 + final.output_tokens * 25
        assert final.spans[1].passages == [0]


async def test_exhausted_quota_blocks_before_any_call(client):
    ws = await workspace(client)
    async with SessionLocal() as db:
        ledger = await current_ledger(db, ws)
        ledger.consumed = ledger.allowance
        await db.commit()
        called = False
        try:
            async with Meter(db, ws, model="claude-opus-5-5", operation="chat_response", kind="llm", est_input=10, est_output=10):
                called = True
        except QuotaExhausted:
            pass
        assert not called
