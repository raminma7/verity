"""Attachments on incoming mail, guarded like knowledge-base uploads.

A file is kept only if its name has an allowed extension and its content agrees with it (checked by its
bytes, not by the sender's Content-Type). Executables and scripts are refused whatever they are called, and
archives that would expand far beyond their size are refused. Kept files count against the plan's storage;
when storage is full the attachment is listed but not kept. Files are served only as downloads.
"""

import hashlib
import io
import re
import uuid
import zipfile
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import EmailAttachment
from app.services.ingest.parsers import MAX_UNZIPPED
from app.services.mail.parse import Attachment
from app.services.storage import get_storage
from app.services.subscriptions import effective_limits

ALLOWED = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "txt": "text/plain",
    "csv": "text/csv",
    "log": "text/plain",
    "ics": "text/calendar",
    "eml": "message/rfc822",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "zip": "application/zip",
}
EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"#!")


@dataclass
class Checked:
    filename: str
    declared: str
    detected: str
    data: bytes
    blocked: str | None = None


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f\"<>:|?*]", "", name).strip(" .") or "attachment"
    return name[:200]


def _magic_ok(ext: str, data: bytes) -> bool:
    head = data[:16]
    if ext == "pdf":
        return data[:1024].lstrip().startswith(b"%PDF-")
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in ("doc", "xls"):
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext in ("docx", "xlsx", "pptx", "zip"):
        return head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06")
    # Text types: must decode as text and carry no NUL bytes.
    if b"\x00" in data[:65536]:
        return False
    try:
        data[:65536].decode("utf-8")
    except UnicodeDecodeError:
        try:
            data[:65536].decode("latin-1")
        except UnicodeDecodeError:
            return False
    return True


def check(att: Attachment) -> Checked:
    s = get_settings()
    name = safe_filename(att.filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    c = Checked(name, att.content_type[:120], "application/octet-stream", att.data)
    if att.data.startswith(EXECUTABLE_MAGIC) or ext not in ALLOWED:
        c.blocked = "blocked_type"
    elif len(att.data) > s.mail_max_attachment_mb * 1024 * 1024:
        c.blocked = "too_large"
    elif not att.data or not _magic_ok(ext, att.data):
        c.blocked = "type_mismatch"
    else:
        c.detected = ALLOWED[ext]
        if ext in ("docx", "xlsx", "pptx", "zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(att.data)) as z:
                    infos = z.infolist()
                    if sum(i.file_size for i in infos) > MAX_UNZIPPED or len(infos) > 10000:
                        c.blocked = "archive_too_large"
                    elif any(
                        i.filename.lower().rsplit(".", 1)[-1] in ("exe", "dll", "scr", "bat", "cmd", "js", "vbs", "msi", "ps1", "jar")
                        for i in infos
                    ):
                        c.blocked = "blocked_type"
            except zipfile.BadZipFile:
                c.blocked = "type_mismatch"
    return c


async def store(db: AsyncSession, workspace_id: uuid.UUID, message_id: uuid.UUID, atts: list[Attachment]) -> list[EmailAttachment]:
    """Checks every attachment and keeps the ones that pass, within the plan's storage."""
    from app.routers.knowledge import usage as storage_usage

    s = get_settings()
    out: list[EmailAttachment] = []
    limit_mb = (await effective_limits(db, workspace_id)).get("storage_mb", 0)
    used = (await storage_usage(db, workspace_id))["storage_bytes"]
    for i, att in enumerate(atts):
        c = check(att)
        if i >= s.mail_max_attachments:
            c.blocked = c.blocked or "too_many"
        if c.blocked is None and limit_mb >= 0 and used + len(c.data) > limit_mb * 1024 * 1024:
            c.blocked = "storage_full"
        key = None
        if c.blocked is None:
            ext = "." + c.filename.rsplit(".", 1)[-1].lower() if "." in c.filename else ""
            key = await get_storage().put(workspace_id, c.data, suffix=ext)
            used += len(c.data)
        row = EmailAttachment(
            workspace_id=workspace_id,
            email_message_id=message_id,
            filename=c.filename,
            declared_mime=c.declared,
            detected_mime=c.detected,
            size=len(c.data),
            sha256=hashlib.sha256(c.data).hexdigest(),
            storage_key=key,
            blocked_reason=c.blocked,
        )
        db.add(row)
        out.append(row)
    return out


def attachment_out(a: EmailAttachment) -> dict:
    return {
        "id": a.id,
        "filename": a.filename,
        "mime": a.detected_mime,
        "size": a.size,
        "available": a.storage_key is not None,
        "blocked_reason": a.blocked_reason,
    }
