"""Connected mailboxes and every email that goes in or out (Phase 4).

An `EmailAccount` is a mailbox the workspace connected (IMAP to fetch, SMTP to send). Its password is
Fernet-encrypted at rest (`secret_enc`) and never leaves the server. Every message fetched or sent is an
`EmailMessage` row: the unique (account, Message-ID) index is what guarantees a message is processed once,
and the stored Message-IDs are what later replies are threaded against.

    incoming: received ──▶ ticketed | appended | spam | promotion | rate_limited | loop | bounce | auto_reply | failed
              spam/promotion/rate_limited ──(not spam)──▶ ticketed;  ──(delete)──▶ deleted
    outgoing: queued ──▶ sent | failed ──(retry)──▶ queued
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

ACCOUNT_STATUSES = ("active", "paused", "error", "disconnected")
REVIEW_STATUSES = ("spam", "promotion", "rate_limited")


class EmailAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "email_accounts"
    __table_args__ = (
        Index(
            "uq_email_accounts_ws_address",
            "workspace_id",
            text("lower(address)"),
            unique=True,
            postgresql_where=text("status <> 'disconnected'"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    address: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(20), default="organization")  # personal | organization
    transport: Mapped[str] = mapped_column(String(20), default="imap")
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_security: Mapped[str] = mapped_column(String(10), default="ssl")  # ssl | starttls
    smtp_host: Mapped[str] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)
    smtp_security: Mapped[str] = mapped_column(String(10), default="ssl")
    username: Mapped[str] = mapped_column(String(320))
    # The app password, Fernet-encrypted. Wiped on disconnect. Never returned by the API.
    secret_enc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    fetch_status: Mapped[str] = mapped_column(String(20), default="idle")  # idle | queued | fetching | ok | error
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(String(500))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    fetch_state: Mapped[dict] = mapped_column(JSONB, default=dict)  # {uidvalidity, last_uid}
    # None: use the workspace's automatic-reply setting. Otherwise {mode, subject, body}.
    auto_reply: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailMessage(IdMixin, TimestampMixin, Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        Index("uq_email_messages_account_mid", "account_id", "message_id", unique=True),
        Index("ix_email_messages_ws_mid", "workspace_id", "message_id"),
        Index("ix_email_messages_ws_status", "workspace_id", "status", "received_at"),
        Index("ix_email_messages_ws_direction_received", "workspace_id", "direction", "received_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    # Null for mail sent through the platform sender (no account connected).
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    message_id: Mapped[str] = mapped_column(String(998))
    in_reply_to: Mapped[str | None] = mapped_column(String(998))
    references: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    from_addr: Mapped[str] = mapped_column(String(320), default="")
    from_name: Mapped[str] = mapped_column(String(200), default="")
    to: Mapped[list] = mapped_column(JSONB, default=list)
    cc: Mapped[list] = mapped_column(JSONB, default=list)
    subject: Mapped[str] = mapped_column(String(998), default="")
    text_body: Mapped[str] = mapped_column(Text, default="")
    html_body: Mapped[str | None] = mapped_column(Text)  # sanitized
    headers: Mapped[dict] = mapped_column(JSONB, default=dict)  # a safe subset, for review and loop checks
    uid: Mapped[int | None] = mapped_column(BigInteger)
    size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))
    verdict: Mapped[dict] = mapped_column(JSONB, default=dict)
    kind_out: Mapped[str | None] = mapped_column(String(30))  # auto_reply | agent_reply | chat_reply | form_confirmation
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    ticket_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ticket_events.id", ondelete="SET NULL"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailAttachment(IdMixin, TimestampMixin, Base):
    __tablename__ = "email_attachments"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    email_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("email_messages.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    declared_mime: Mapped[str] = mapped_column(String(120), default="")
    detected_mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_key: Mapped[str | None] = mapped_column(String(200))
    # Why the file wasn't kept: blocked_type | too_large | too_many | type_mismatch | archive_too_large | storage_full
    blocked_reason: Mapped[str | None] = mapped_column(String(40))
