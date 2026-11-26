import re
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invitation, Membership, Role, User, Workspace
from app.models.base import utcnow


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return s or "workspace"


async def system_role(db: AsyncSession, key: str) -> Role:
    return (await db.execute(select(Role).where(Role.workspace_id.is_(None), Role.key == key))).scalar_one()


async def unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug = base
    while (await db.execute(select(Workspace.id).where(Workspace.slug == slug))).first():
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


async def create_workspace(db: AsyncSession, owner: User, name: str) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), name=name, slug=await unique_slug(db, name), owner_id=owner.id)
    db.add(ws)
    owner_role = await system_role(db, "owner")
    db.add(Membership(workspace_id=ws.id, user_id=owner.id, role_id=owner_role.id))
    owner.last_workspace_id = ws.id
    return ws


def default_workspace_name(full_name: str) -> str:
    first = full_name.strip().split(" ")[0] if full_name.strip() else "My"
    return f"{first}'s workspace"


async def roles_for(db: AsyncSession, workspace_id: uuid.UUID) -> list[Role]:
    q = select(Role).where((Role.workspace_id.is_(None)) | (Role.workspace_id == workspace_id)).order_by(Role.rank.desc())
    return list((await db.execute(q)).scalars())


async def seat_count(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    """Members plus open invitations; both count toward the member limit."""
    members = await db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id))
    invites = await db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.workspace_id == workspace_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > utcnow(),
        )
    )
    return int(members or 0) + int(invites or 0)
