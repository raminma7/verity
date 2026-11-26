import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.models.base import utcnow


def audit(
    db: AsyncSession,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    ip: str | None = None,
    **data,
) -> None:
    """Adds an audit row to the current transaction; it commits with the change it records."""
    db.add(
        AuditLog(
            action=action,
            actor_id=actor_id,
            workspace_id=workspace_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            data={k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in data.items()},
            ip=ip,
            created_at=utcnow(),
        )
    )
