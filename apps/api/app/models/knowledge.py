"""Knowledge base: sources (uploaded files and crawled websites), crawled pages, and embedded chunks.

Every row carries workspace_id, and every query filters on it: chunks are only ever searched inside one
workspace, so retrieval can't leak another tenant's text.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Computed, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

EMBEDDING_DIM = 1024  # Voyage voyage-4 family

FILE_TYPES = ("pdf", "docx", "txt", "csv", "xlsx")
SOURCE_STATUSES = ("queued", "processing", "ready", "failed", "deleting")


class KnowledgeSource(IdMixin, TimestampMixin, Base):
    """One uploaded document or one crawled website."""

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "domain"),
        Index("ix_knowledge_sources_ws_kind", "workspace_id", "kind"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # file | website
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(500))
    # Last processing run, step by step, for the PipelineTrace: [{name, detail, ms, state}]
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    # Bumped on every reprocess; a job whose version is stale stops without writing.
    version: Mapped[int] = mapped_column(Integer, default=1)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Files
    file_type: Mapped[str | None] = mapped_column(String(10))
    storage_key: Mapped[str | None] = mapped_column(String(200))
    mime: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))

    # Websites
    root_url: Mapped[str | None] = mapped_column(String(2000))
    domain: Mapped[str | None] = mapped_column(String(253))  # NULL for files, so the unique constraint ignores them
    include_paths: Mapped[list] = mapped_column(JSONB, default=list)
    exclude_paths: Mapped[list] = mapped_column(JSONB, default=list)
    max_depth: Mapped[int] = mapped_column(Integer, default=3)
    sync_interval: Mapped[str] = mapped_column(String(10), default="weekly")  # off | daily | weekly
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    notice: Mapped[str | None] = mapped_column(String(60))  # e.g. page_limit_reached


class KnowledgePage(IdMixin, Base):
    """A crawled page of a website source. content_hash lets a re-sync skip unchanged pages."""

    __tablename__ = "knowledge_pages"
    __table_args__ = (UniqueConstraint("source_id", "url"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(String(500), default="")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready | failed | skipped
    error_code: Mapped[str | None] = mapped_column(String(60))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(IdMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_ws_source", "workspace_id", "source_id"),
        Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"))
    page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_pages.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    # Where the passage came from, for citations: {page, sheet, rows, heading, url, title}
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
