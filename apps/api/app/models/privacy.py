import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class DataExport(IdMixin, Base):
    """A data export: the whole workspace (owners) or one person's own data. Built by a job, downloaded once."""

    __tablename__ = "data_exports"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # workspace | personal
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | ready | downloaded | expired | failed
    storage_key: Mapped[str | None] = mapped_column(String(300))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpamModelStats(Base):
    """Per-workspace evidence for the spam model's learning layer: how many reviewed messages of each class."""

    __tablename__ = "spam_model_stats"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    spam_docs: Mapped[int] = mapped_column(Integer, default=0)
    ham_docs: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpamFeature(Base):
    """Per-workspace counts of a hashed feature in spam and in legitimate mail (a Naive Bayes layer)."""

    __tablename__ = "spam_features"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    feature: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    spam: Mapped[int] = mapped_column(Integer, default=0)
    ham: Mapped[int] = mapped_column(Integer, default=0)
