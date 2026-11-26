"""Ingestion jobs: parse → chunk → embed → index, for uploaded files and crawled websites.

Each run records its steps (for the PipelineTrace) and its outcome on the source row. New chunks replace
old ones in one transaction under a row lock that also re-checks the source's version, so retrieval never
sees a half-indexed document and a stale run (superseded by a reprocess) can't overwrite a newer one.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import KnowledgeChunk, KnowledgePage, KnowledgeSource
from app.models.base import utcnow
from app.services.ai.embeddings import BATCH, EmbeddingError, get_embedder
from app.services.ai.metered import Meter, estimate
from app.services.crawler import CrawlConfig, Crawler, CrawlError, Fetcher
from app.services.ingest.chunker import Chunk, chunk_sections
from app.services.ingest.parsers import ParseError, Section, parse
from app.services.quota import QuotaExhausted
from app.services.storage import get_storage
from app.services.subscriptions import effective_limits

log = logging.getLogger("verity.ingest")

SYNC_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


class Stale(Exception):
    """The source was reprocessed or deleted while this run was working."""


@dataclass
class Trace:
    steps: list

    def __init__(self):
        self.steps = []
        self._t = time.perf_counter()

    def done(self, name: str, detail: str = "") -> None:
        now = time.perf_counter()
        self.steps.append({"name": name, "detail": detail, "ms": int((now - self._t) * 1000), "state": "done"})
        self._t = now

    def fail(self, name: str, detail: str) -> None:
        self.steps.append({"name": name, "detail": detail, "ms": int((time.perf_counter() - self._t) * 1000), "state": "error"})


async def embed_texts(db: AsyncSession, workspace_id: uuid.UUID, texts: list[str]) -> tuple[list[list[float]], int]:
    """Embeds in batches; each batch reserves and settles its own tokens through the quota."""
    emb = get_embedder()
    vectors, tokens = [], 0
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        est = estimate(sum(len(t) for t in batch))
        async with Meter(db, workspace_id, model=emb.model, operation="embedding", kind="embedding", est_input=est, est_output=0) as m:
            out = await emb.embed(batch, "document")
            await m.settle(out.tokens or est, 0)
        vectors += out.vectors
        tokens += out.tokens or est
    return vectors, tokens


async def _lock_current(db: AsyncSession, source_id: uuid.UUID, version: int) -> KnowledgeSource:
    src = (await db.execute(select(KnowledgeSource).where(KnowledgeSource.id == source_id).with_for_update())).scalar_one_or_none()
    if src is None or src.version != version or src.status == "deleting":
        raise Stale()
    return src


def _rows(src: KnowledgeSource, chunks: list[Chunk], vectors: list[list[float]], page_id: uuid.UUID | None, start: int = 0):
    now, model = utcnow(), get_embedder().model
    return [
        KnowledgeChunk(
            workspace_id=src.workspace_id,
            source_id=src.id,
            page_id=page_id,
            ordinal=start + i,
            content=c.content,
            token_count=c.token_count,
            meta=c.meta,
            embedding=v,
            embedding_model=model,
            created_at=now,
        )
        for i, (c, v) in enumerate(zip(chunks, vectors, strict=True))
    ]


async def _fail(source_id: uuid.UUID, version: int, code: str, message: str, trace: Trace) -> None:
    async with SessionLocal() as db:
        try:
            src = await _lock_current(db, source_id, version)
        except Stale:
            return
        src.status, src.error_code, src.error_message, src.steps = "failed", code, message[:500], trace.steps
        src.processed_at = utcnow()
        await db.commit()


async def _start(source_id: uuid.UUID, version: int) -> KnowledgeSource | None:
    async with SessionLocal() as db:
        try:
            src = await _lock_current(db, source_id, version)
        except Stale:
            return None
        src.status, src.error_code, src.error_message, src.notice = "processing", None, None, None
        src.steps = []
        await db.commit()
        return src


async def ingest_file(source_id: uuid.UUID, version: int, *, final_attempt: bool = True) -> None:
    src = await _start(source_id, version)
    if src is None:
        return
    trace = Trace()
    try:
        data = await get_storage().get(src.storage_key)
        sections = await asyncio.to_thread(parse, src.file_type, data)
        where = f"{len(sections)} pages" if src.file_type == "pdf" else f"{len(sections)} sections"
        trace.done("Parse", f"{src.file_type.upper()} · {where}")
        chunks = chunk_sections(sections)
        if not chunks:
            raise ParseError("empty_document", "This document has no text.")
        avg = sum(c.token_count for c in chunks) // len(chunks)
        trace.done("Chunk", f"{len(chunks)} chunks · ~{avg} tokens each")
        async with SessionLocal() as db:
            vectors, tokens = await embed_texts(db, src.workspace_id, [c.content for c in chunks])
            trace.done("Embed", f"{get_embedder().model} · {tokens:,} tokens")
            locked = await _lock_current(db, source_id, version)
            await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id))
            db.add_all(_rows(locked, chunks, vectors, None))
            trace.done("Index", f"{len(chunks)} chunks searchable")
            locked.status, locked.chunk_count, locked.token_count = "ready", len(chunks), tokens
            locked.steps, locked.processed_at = trace.steps, utcnow()
            await db.commit()
    except Stale:
        log.info("source %s v%s superseded", source_id, version)
    except ParseError as e:
        trace.fail("Parse", e.message)
        await _fail(source_id, version, e.code, e.message, trace)
    except QuotaExhausted:
        trace.fail("Embed", "AI token allowance used up")
        await _fail(
            source_id, version, "quota_exhausted", "The workspace's AI token allowance is used up. Reprocess when it renews.", trace
        )
    except EmbeddingError as e:
        if e.retryable and not final_attempt:
            raise
        trace.fail("Embed", str(e))
        await _fail(source_id, version, e.code, "The embedding service is unavailable. Try reprocessing in a few minutes.", trace)
    except Exception:
        if not final_attempt:
            raise
        log.exception("ingest %s failed", source_id)
        trace.fail("Index", "Unexpected error")
        await _fail(source_id, version, "processing_failed", "Something went wrong while processing this document.", trace)


async def page_budget(db: AsyncSession, src: KnowledgeSource) -> int:
    """How many pages this website may hold under the plan's crawled_pages limit (-1 = unlimited)."""
    limit = int((await effective_limits(db, src.workspace_id)).get("crawled_pages", 0))
    if limit < 0:
        return -1
    others = (
        await db.execute(
            select(func.count(KnowledgePage.id))
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgePage.source_id)
            .where(
                KnowledgePage.workspace_id == src.workspace_id,
                KnowledgePage.source_id != src.id,
                KnowledgePage.status == "ready",
                KnowledgeSource.status != "deleting",
            )
        )
    ).scalar_one()
    return max(0, limit - others)


async def crawl_website(source_id: uuid.UUID, version: int, *, final_attempt: bool = True, fetcher: Fetcher | None = None) -> None:
    src = await _start(source_id, version)
    if src is None:
        return
    trace = Trace()
    s = get_settings()
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    stats = {"fetched": 0, "new": 0, "changed": 0, "unchanged": 0, "failed": 0, "tokens": 0}
    limit_hit = False
    try:
        async with SessionLocal() as db:
            budget = await page_budget(db, src)
            existing = {p.url: p for p in (await db.execute(select(KnowledgePage).where(KnowledgePage.source_id == source_id))).scalars()}
        max_pages = s.crawl_max_pages_per_run if budget < 0 else min(s.crawl_max_pages_per_run, budget + 20)
        cfg = CrawlConfig(
            root_url=src.root_url,
            domain=src.domain,
            max_depth=src.max_depth,
            max_pages=max_pages,
            include=src.include_paths,
            exclude=src.exclude_paths,
        )
        seen: set[str] = set()
        indexed = 0
        async for page in Crawler(cfg, fetcher).run():
            stats["fetched"] += 1
            seen.add(page.url)
            async with SessionLocal() as db:
                row = existing.get(page.url)
                if page.error_code or not page.text:
                    stats["failed"] += page.error_code not in ("noindex", "no_content", "not_html")
                    await _record_page(
                        db, src, version, row, page, status="skipped" if page.error_code in ("noindex", "not_html") else "failed"
                    )
                    continue
                if budget >= 0 and indexed >= budget:
                    limit_hit = True
                    break
                indexed += 1
                if row is not None and row.content_hash == page.content_hash and row.status == "ready":
                    stats["unchanged"] += 1
                    row.fetched_at = utcnow()
                    await _lock_current(db, source_id, version)
                    await db.merge(row)
                    await db.commit()
                    continue
                chunks = chunk_sections([Section(page.text, {"url": page.url, "title": page.title})])
                vectors, tokens = await embed_texts(db, src.workspace_id, [c.content for c in chunks])
                stats["tokens"] += tokens
                stats["changed" if row else "new"] += 1
                await _record_page(db, src, version, row, page, status="ready", chunks=chunks, vectors=vectors)
        if stats["fetched"] == 0:
            raise CrawlError("no_pages", "Verity couldn't fetch any page from this site.")
        trace.done("Crawl", f"{stats['fetched']} pages fetched · {stats['failed']} failed")
        trace.done(
            "Embed", f"{stats['new']} new · {stats['changed']} changed · {stats['unchanged']} unchanged · {stats['tokens']:,} tokens"
        )
        async with SessionLocal() as db:
            locked = await _lock_current(db, source_id, version)
            if not limit_hit and stats["fetched"] < max_pages:
                # A complete crawl: pages that are gone from the site leave the knowledge base too.
                gone = [p.id for url, p in existing.items() if url not in seen]
                if gone:
                    await db.execute(delete(KnowledgePage).where(KnowledgePage.id.in_(gone)))
            counts = (
                await db.execute(
                    select(func.count(KnowledgeChunk.id), func.coalesce(func.sum(KnowledgeChunk.token_count), 0)).where(
                        KnowledgeChunk.source_id == source_id
                    )
                )
            ).one()
            pages = (
                await db.execute(
                    select(func.count(KnowledgePage.id)).where(KnowledgePage.source_id == source_id, KnowledgePage.status == "ready")
                )
            ).scalar_one()
            trace.done("Index", f"{pages} pages · {counts[0]} chunks searchable")
            locked.status = "ready" if pages else "failed"
            if not pages and limit_hit:
                locked.error_code = "page_limit_reached"
                locked.error_message = "Your plan's crawled-page limit is reached. Upgrade or remove another website to add pages."
            elif not pages:
                locked.error_code, locked.error_message = "no_content", "No readable pages were found on this site."
            locked.chunk_count, locked.token_count, locked.page_count = counts[0], int(counts[1]), pages
            locked.notice = "page_limit_reached" if limit_hit else None
            now = utcnow()
            locked.last_synced_at, locked.processed_at, locked.steps = now, now, trace.steps
            interval = SYNC_INTERVALS.get(locked.sync_interval)
            locked.next_sync_at = now + interval if interval else None
            await db.commit()
    except Stale:
        log.info("website %s v%s superseded", source_id, version)
    except CrawlError as e:
        trace.fail("Crawl", e.message)
        await _fail(source_id, version, e.code, e.message, trace)
    except QuotaExhausted:
        trace.fail("Embed", "AI token allowance used up")
        await _fail(source_id, version, "quota_exhausted", "The workspace's AI token allowance is used up. Re-sync when it renews.", trace)
    except EmbeddingError as e:
        if e.retryable and not final_attempt:
            raise
        trace.fail("Embed", str(e))
        await _fail(source_id, version, e.code, "The embedding service is unavailable. Try again in a few minutes.", trace)
    except Exception:
        if not final_attempt:
            raise
        log.exception("crawl %s failed", source_id)
        trace.fail("Crawl", "Unexpected error")
        await _fail(source_id, version, "processing_failed", "Something went wrong while crawling this site.", trace)
    finally:
        if own_fetcher:
            await fetcher.aclose()


async def _record_page(db, src, version, row, page, *, status, chunks=None, vectors=None) -> None:
    await _lock_current(db, src.id, version)
    if row is None:
        row = KnowledgePage(workspace_id=src.workspace_id, source_id=src.id, url=page.url)
        db.add(row)
        await db.flush()
    else:
        row = await db.merge(row)
    row.title, row.http_status, row.status, row.error_code = page.title[:500], page.status or None, status, page.error_code
    row.fetched_at = utcnow()
    if status == "ready" and chunks is not None:
        row.content_hash = page.content_hash
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.page_id == row.id))
        db.add_all(_rows(src, chunks, vectors, row.id))
        row.chunk_count = len(chunks)
    elif status != "ready":
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.page_id == row.id))
        row.chunk_count, row.content_hash = 0, None
    await db.commit()


async def delete_source(source_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        src = await db.get(KnowledgeSource, source_id)
        if src is None:
            return
        key = src.storage_key
        await db.delete(src)  # chunks and pages cascade
        await db.commit()
    if key:
        await get_storage().delete(key)


async def due_websites(limit: int = 50) -> list[tuple[uuid.UUID, int]]:
    """Websites whose periodic re-sync is due; bumps their version so the re-sync is a fresh run."""
    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(KnowledgeSource)
                    .where(
                        KnowledgeSource.kind == "website",
                        KnowledgeSource.status.in_(("ready", "failed")),
                        KnowledgeSource.next_sync_at.is_not(None),
                        KnowledgeSource.next_sync_at <= utcnow(),
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        out = []
        for src in rows:
            src.version += 1
            src.status = "queued"
            src.next_sync_at = None
            out.append((src.id, src.version))
        await db.commit()
    return out


async def mark_queued(db: AsyncSession, src: KnowledgeSource) -> int:
    """Bumps the version and queues the source for (re)processing. Returns the new version."""
    await db.execute(
        update(KnowledgeSource)
        .where(KnowledgeSource.id == src.id)
        .values(version=KnowledgeSource.version + 1, status="queued", error_code=None, error_message=None)
    )
    await db.flush()
    await db.refresh(src)
    return src.version
