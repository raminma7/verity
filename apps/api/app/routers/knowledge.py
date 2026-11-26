"""Knowledge base: upload documents, add websites, watch processing, reprocess and delete.

Plan limits (documents, domains, storage_mb; crawled_pages in the crawler) are checked on the backend
under a per-workspace advisory lock, so parallel requests can't slip past them.
"""

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.jobs import enqueue
from app.models import KnowledgeChunk, KnowledgePage, KnowledgeSource
from app.services.audit import audit
from app.services.crawler import CrawlError, normalize_root, site_host
from app.services.ingest.parsers import MIME, ParseError, detect_type
from app.services.ingest.pipeline import mark_queued
from app.services.storage import get_storage
from app.services.subscriptions import effective_limits

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

READ = require("knowledge.read", subscription=True)
MANAGE = require("knowledge.manage", subscription=True)


def _limit_reached(key: str, message: str) -> ApiError:
    return ApiError(402, "limit_reached", message, limit=key)


async def lock_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"knowledge:{workspace_id}"})


async def usage(db: AsyncSession, workspace_id: uuid.UUID) -> dict:
    live = (KnowledgeSource.workspace_id == workspace_id) & (KnowledgeSource.status != "deleting")
    docs, storage = (
        await db.execute(
            select(func.count(KnowledgeSource.id), func.coalesce(func.sum(KnowledgeSource.size_bytes), 0)).where(
                live, KnowledgeSource.kind == "file"
            )
        )
    ).one()
    domains = (await db.execute(select(func.count(KnowledgeSource.id)).where(live, KnowledgeSource.kind == "website"))).scalar_one()
    pages = (
        await db.execute(
            select(func.count(KnowledgePage.id))
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgePage.source_id)
            .where(live, KnowledgePage.status == "ready")
        )
    ).scalar_one()
    return {"documents": docs, "domains": domains, "crawled_pages": pages, "storage_bytes": int(storage)}


def _ok(limit: int, used: int, adding: int = 1) -> bool:
    return limit < 0 or used + adding <= limit


def source_out(s: KnowledgeSource) -> dict:
    out = {
        "id": s.id,
        "kind": s.kind,
        "name": s.name,
        "status": s.status,
        "error_code": s.error_code,
        "error_message": s.error_message,
        "notice": s.notice,
        "chunk_count": s.chunk_count,
        "token_count": s.token_count,
        "steps": s.steps,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "processed_at": s.processed_at,
    }
    if s.kind == "file":
        out |= {"file_type": s.file_type, "size_bytes": s.size_bytes, "mime": s.mime}
    else:
        out |= {
            "root_url": s.root_url,
            "domain": s.domain,
            "include_paths": s.include_paths,
            "exclude_paths": s.exclude_paths,
            "max_depth": s.max_depth,
            "sync_interval": s.sync_interval,
            "last_synced_at": s.last_synced_at,
            "next_sync_at": s.next_sync_at,
            "page_count": s.page_count,
        }
    return out


async def _get(db: AsyncSession, ctx: WorkspaceContext, source_id: uuid.UUID) -> KnowledgeSource:
    src = (
        await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.id == source_id, KnowledgeSource.workspace_id == ctx.workspace.id, KnowledgeSource.status != "deleting"
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise not_found("Source")
    return src


async def queue_processing(src: KnowledgeSource) -> None:
    if src.kind == "file":
        await enqueue("ingest_source", key=f"ingest:{src.id}:{src.version}", job_timeout=1800, source_id=str(src.id), version=src.version)
    else:
        await enqueue("crawl_source", key=f"crawl:{src.id}:{src.version}", job_timeout=3600, source_id=str(src.id), version=src.version)


# ---- Reading ------------------------------------------------------------------------------------


@router.get("/stats")
async def stats(db: Db, ctx: WorkspaceContext = READ):
    used = await usage(db, ctx.workspace.id)
    limits = await effective_limits(db, ctx.workspace.id)
    by_status = dict(
        (
            await db.execute(
                select(KnowledgeSource.status, func.count())
                .where(KnowledgeSource.workspace_id == ctx.workspace.id, KnowledgeSource.status != "deleting")
                .group_by(KnowledgeSource.status)
            )
        ).all()
    )
    chunks = (await db.execute(select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.workspace_id == ctx.workspace.id))).scalar_one()
    return {
        "usage": used,
        "limits": {k: limits.get(k, 0) for k in ("documents", "domains", "crawled_pages", "storage_mb")},
        "by_status": by_status,
        "chunks": chunks,
        "max_upload_mb": get_settings().max_upload_mb,
    }


@router.get("/sources")
async def list_sources(db: Db, kind: str | None = None, status: str | None = None, q: str | None = None, ctx: WorkspaceContext = READ):
    query = select(KnowledgeSource).where(KnowledgeSource.workspace_id == ctx.workspace.id, KnowledgeSource.status != "deleting")
    if kind in ("file", "website"):
        query = query.where(KnowledgeSource.kind == kind)
    if status in ("queued", "processing", "ready", "failed"):
        query = query.where(KnowledgeSource.status == status)
    if q:
        like = f"%{q.strip()[:100].replace('%', r'\%').replace('_', r'\_')}%"
        query = query.where(or_(KnowledgeSource.name.ilike(like), KnowledgeSource.root_url.ilike(like)))
    rows = (await db.execute(query.order_by(KnowledgeSource.created_at.desc()).limit(500))).scalars().all()
    return [source_out(s) for s in rows]


@router.get("/sources/{source_id}")
async def get_source(source_id: uuid.UUID, db: Db, ctx: WorkspaceContext = READ):
    return source_out(await _get(db, ctx, source_id))


@router.get("/sources/{source_id}/chunks")
async def source_chunks(source_id: uuid.UUID, db: Db, offset: int = 0, limit: int = 50, ctx: WorkspaceContext = READ):
    src = await _get(db, ctx, source_id)
    limit = max(1, min(limit, 200))
    rows = (
        await db.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.ordinal, KnowledgeChunk.content, KnowledgeChunk.token_count, KnowledgeChunk.meta)
            .where(KnowledgeChunk.source_id == src.id, KnowledgeChunk.workspace_id == ctx.workspace.id)
            .order_by(KnowledgeChunk.page_id.nulls_first(), KnowledgeChunk.ordinal)
            .offset(max(0, offset))
            .limit(limit)
        )
    ).all()
    ids = [str(r.id) for r in rows]
    cited: dict[str, int] = {}
    if ids:
        cited = dict(
            (
                await db.execute(
                    text(
                        "SELECT c->>'chunk_id', count(*) FROM messages m, jsonb_array_elements(m.citations) c "
                        "WHERE m.workspace_id = :ws AND c->>'chunk_id' = ANY(:ids) GROUP BY 1"
                    ),
                    {"ws": ctx.workspace.id, "ids": ids},
                )
            ).all()
        )
    return {
        "total": src.chunk_count,
        "items": [
            {
                "id": r.id,
                "ordinal": r.ordinal,
                "content": r.content,
                "token_count": r.token_count,
                "meta": r.meta,
                "cited": cited.get(str(r.id), 0),
            }
            for r in rows
        ],
    }


@router.get("/sources/{source_id}/pages")
async def source_pages(source_id: uuid.UUID, db: Db, ctx: WorkspaceContext = READ):
    src = await _get(db, ctx, source_id)
    rows = (
        await db.execute(
            select(KnowledgePage)
            .where(KnowledgePage.source_id == src.id, KnowledgePage.workspace_id == ctx.workspace.id)
            .order_by(KnowledgePage.url)
            .limit(5000)
        )
    ).scalars()
    return [
        {
            "id": p.id,
            "url": p.url,
            "title": p.title,
            "status": p.status,
            "error_code": p.error_code,
            "http_status": p.http_status,
            "chunk_count": p.chunk_count,
            "fetched_at": p.fetched_at,
        }
        for p in rows
    ]


# ---- Adding -------------------------------------------------------------------------------------


@router.post("/files", status_code=201)
async def upload(request: Request, db: Db, file: Annotated[UploadFile, File()], ctx: WorkspaceContext = MANAGE):
    s = get_settings()
    max_bytes = s.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(413, "file_too_large", f"Files can be up to {s.max_upload_mb} MB.")
    name = (file.filename or "document").replace("\\", "/").rsplit("/", 1)[-1].strip()[:300] or "document"
    try:
        file_type = detect_type(name, data)
    except ParseError as e:
        raise ApiError(422, e.code, e.message) from None
    digest = hashlib.sha256(data).hexdigest()

    await lock_workspace(db, ctx.workspace.id)
    dup = (
        await db.execute(
            select(KnowledgeSource.id, KnowledgeSource.name).where(
                KnowledgeSource.workspace_id == ctx.workspace.id, KnowledgeSource.sha256 == digest, KnowledgeSource.status != "deleting"
            )
        )
    ).first()
    if dup:
        raise ApiError(409, "duplicate_document", f"This file is already in the knowledge base as “{dup.name}”.", source_id=str(dup.id))
    limits = await effective_limits(db, ctx.workspace.id)
    used = await usage(db, ctx.workspace.id)
    if not _ok(limits["documents"], used["documents"]):
        raise _limit_reached("documents", "Your plan's document limit is reached. Upgrade or remove documents to add more.")
    if limits["storage_mb"] >= 0 and used["storage_bytes"] + len(data) > limits["storage_mb"] * 1024 * 1024:
        raise _limit_reached("storage_mb", "Your plan's storage is full. Upgrade or remove documents to add more.")

    key = await get_storage().put(ctx.workspace.id, data, suffix=f".{file_type}")
    src = KnowledgeSource(
        workspace_id=ctx.workspace.id,
        kind="file",
        name=name,
        status="queued",
        file_type=file_type,
        storage_key=key,
        mime=MIME[file_type],
        size_bytes=len(data),
        sha256=digest,
        created_by=ctx.user.id,
        steps=[],
        include_paths=[],
        exclude_paths=[],
    )
    db.add(src)
    await db.flush()
    audit(
        db,
        "knowledge.source_added",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="knowledge_source",
        target_id=src.id,
        ip=client_ip(request),
        kind="file",
        name=name,
        size_bytes=len(data),
    )
    await db.commit()
    await queue_processing(src)
    await db.refresh(src)
    return source_out(src)


def clean_paths(v: list[str]) -> list[str]:
    out = []
    for p in v:
        p = p.strip()
        if not p:
            continue
        if not p.startswith("/") or len(p) > 300:
            raise ValueError("Paths start with / (for example /docs).")
        out.append(p)
    return out


class WebsiteIn(BaseModel):
    url: str = Field(min_length=3, max_length=2000)
    name: str | None = Field(default=None, max_length=300)
    max_depth: int = Field(default=3, ge=0, le=10)
    include_paths: list[str] = Field(default_factory=list, max_length=20)
    exclude_paths: list[str] = Field(default_factory=list, max_length=50)
    sync_interval: str = Field(default="weekly", pattern="^(off|daily|weekly)$")

    @field_validator("include_paths", "exclude_paths")
    @classmethod
    def paths(cls, v: list[str]) -> list[str]:
        return clean_paths(v)


class WebsiteUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    include_paths: list[str] | None = Field(default=None, max_length=20)
    exclude_paths: list[str] | None = Field(default=None, max_length=50)
    sync_interval: str | None = Field(default=None, pattern="^(off|daily|weekly)$")

    @field_validator("include_paths", "exclude_paths")
    @classmethod
    def paths(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else clean_paths(v)


@router.post("/websites", status_code=201)
async def add_website(body: WebsiteIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    try:
        root = normalize_root(body.url)
    except CrawlError as e:
        raise ApiError(422, e.code, e.message, field="url") from None
    domain = site_host(root)
    await lock_workspace(db, ctx.workspace.id)
    existing = (
        await db.execute(
            select(KnowledgeSource.id, KnowledgeSource.status).where(
                KnowledgeSource.workspace_id == ctx.workspace.id, KnowledgeSource.domain == domain
            )
        )
    ).first()
    if existing and existing.status != "deleting":
        raise ApiError(409, "duplicate_domain", f"{domain} is already in the knowledge base.", source_id=str(existing.id))
    if existing:
        raise ApiError(409, "domain_being_removed", f"{domain} is still being removed. Try again in a minute.")
    limits = await effective_limits(db, ctx.workspace.id)
    used = await usage(db, ctx.workspace.id)
    if not _ok(limits["domains"], used["domains"]):
        raise _limit_reached("domains", "Your plan's website limit is reached. Upgrade or remove a website to add another.")
    if limits["crawled_pages"] >= 0 and used["crawled_pages"] >= limits["crawled_pages"]:
        raise _limit_reached("crawled_pages", "Your plan's crawled-page limit is reached. Upgrade or remove a website to add another.")
    src = KnowledgeSource(
        workspace_id=ctx.workspace.id,
        kind="website",
        name=(body.name or domain).strip(),
        status="queued",
        root_url=root,
        domain=domain,
        max_depth=body.max_depth,
        include_paths=body.include_paths,
        exclude_paths=body.exclude_paths,
        sync_interval=body.sync_interval,
        created_by=ctx.user.id,
        steps=[],
    )
    db.add(src)
    await db.flush()
    audit(
        db,
        "knowledge.source_added",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="knowledge_source",
        target_id=src.id,
        ip=client_ip(request),
        kind="website",
        url=root,
    )
    await db.commit()
    await queue_processing(src)
    await db.refresh(src)
    return source_out(src)


# ---- Changing -----------------------------------------------------------------------------------


@router.patch("/sources/{source_id}")
async def update_source(source_id: uuid.UUID, body: WebsiteUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    src = await _get(db, ctx, source_id)
    changes = body.model_dump(exclude_none=True)
    if src.kind == "file" and set(changes) - {"name"}:
        raise ApiError(422, "invalid_input", "Only a document's name can be changed.")
    for k, v in changes.items():
        setattr(src, k, v.strip() if isinstance(v, str) and k == "name" else v)
    if "sync_interval" in changes and src.kind == "website":
        from app.services.ingest.pipeline import SYNC_INTERVALS

        interval = SYNC_INTERVALS.get(src.sync_interval)
        src.next_sync_at = (src.last_synced_at + interval) if interval and src.last_synced_at else None
    audit(
        db,
        "knowledge.source_updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="knowledge_source",
        target_id=src.id,
        ip=client_ip(request),
        changes=sorted(changes),
    )
    await db.commit()
    await db.refresh(src)
    return source_out(src)


@router.post("/sources/{source_id}/reprocess", status_code=202)
async def reprocess(source_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    src = await _get(db, ctx, source_id)
    await mark_queued(db, src)
    audit(
        db,
        "knowledge.source_reprocessed",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="knowledge_source",
        target_id=src.id,
        ip=client_ip(request),
    )
    await db.commit()
    await queue_processing(src)
    await db.refresh(src)
    return source_out(src)


@router.delete("/sources/{source_id}", status_code=202)
async def remove(source_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    src = await _get(db, ctx, source_id)
    # Out of retrieval and limits at once; the job removes the file, pages and chunks.
    src.status = "deleting"
    src.version += 1
    audit(
        db,
        "knowledge.source_deleted",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="knowledge_source",
        target_id=src.id,
        ip=client_ip(request),
        kind=src.kind,
        name=src.name,
    )
    await db.commit()
    await enqueue("remove_source", key=f"remove:{src.id}", source_id=str(src.id))
    return {"id": src.id, "status": "deleting"}
