import uuid
from datetime import timedelta

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import Ctx, Db, VerifiedUser, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, forbidden, not_found
from app.core.security import hash_token, new_token
from app.models import AuditLog, Invitation, Membership, Role, User, Workspace
from app.models.base import utcnow
from app.schemas import InviteIn, RoleChangeIn, RoleIn, WorkspaceIn, WorkspaceUpdateIn
from app.services import email as mail
from app.services.audit import audit
from app.services.permissions import PERMISSIONS
from app.services.quota import current_ledger, total_available
from app.services.subscriptions import effective_limits
from app.services.workspaces import create_workspace, roles_for, seat_count, slugify

router = APIRouter(tags=["workspaces"])


def _role(r: Role) -> dict:
    return {
        "id": r.id,
        "key": r.key,
        "name": r.name,
        "description": r.description,
        "permissions": r.permissions,
        "is_system": r.is_system,
        "rank": r.rank,
    }


def _limit_ok(limit: int, used: int) -> bool:
    return limit < 0 or used < limit


@router.get("/permissions")
async def permission_catalogue(_: VerifiedUser):
    return [{"key": k, "description": v} for k, v in PERMISSIONS.items()]


@router.post("/workspaces", status_code=201)
async def new_workspace(body: WorkspaceIn, request: Request, user: VerifiedUser, db: Db):
    ws = await create_workspace(db, user, body.name.strip())
    audit(db, "workspace.created", actor_id=user.id, workspace_id=ws.id, target_type="workspace", target_id=ws.id, ip=client_ip(request))
    await db.commit()
    return {"id": ws.id, "name": ws.name, "slug": ws.slug}


@router.get("/workspace")
async def current_workspace(ctx: Ctx, db: Db):
    sub = ctx.subscription
    ledger = await current_ledger(db, ctx.workspace.id) if ctx.subscribed else None
    return {
        "workspace": {
            "id": ctx.workspace.id,
            "name": ctx.workspace.name,
            "slug": ctx.workspace.slug,
            "status": ctx.workspace.status,
            "settings": ctx.workspace.settings,
            "created_at": ctx.workspace.created_at,
        },
        "role": _role(ctx.membership.role),
        "permissions": sorted(ctx.permissions),
        "subscription": None
        if sub is None
        else {
            "status": sub.status if ctx.subscribed or sub.status != "active" else "expired",
            "active": ctx.subscribed,
            "plan_id": sub.plan_id,
            "plan_name": sub.terms["plan_name"],
            "renews_at": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
        },
        "limits": await effective_limits(db, ctx.workspace.id),
        "usage": {"seats": await seat_count(db, ctx.workspace.id)},
        "ai_available": bool(ledger) and await total_available(db, ctx.workspace.id, ledger) > 0,
    }


@router.patch("/workspace")
async def update_workspace(body: WorkspaceUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = require("workspace.update")):
    ws = ctx.workspace
    if body.name is not None:
        ws.name = body.name.strip()
    if body.settings is not None:
        allowed = {"reopen_on_reply"}
        ws.settings = {**ws.settings, **{k: v for k, v in body.settings.items() if k in allowed}}
    audit(
        db, "workspace.updated", actor_id=ctx.user.id, workspace_id=ws.id, target_type="workspace", target_id=ws.id, ip=client_ip(request)
    )
    await db.commit()
    return {"id": ws.id, "name": ws.name, "settings": ws.settings}


# ---- Members ------------------------------------------------------------------------------------


@router.get("/workspace/members")
async def members(db: Db, ctx: WorkspaceContext = require("members.read")):
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == ctx.workspace.id)
            .order_by(Membership.created_at)
        )
    ).all()
    invites = (
        await db.execute(
            select(Invitation)
            .where(
                Invitation.workspace_id == ctx.workspace.id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > utcnow(),
            )
            .order_by(Invitation.created_at.desc())
        )
    ).scalars()
    return {
        "members": [
            {
                "id": m.id,
                "user_id": u.id,
                "full_name": u.full_name,
                "email": u.email,
                "status": u.status,
                "role": _role(m.role),
                "is_owner": u.id == ctx.workspace.owner_id,
                "joined_at": m.created_at,
            }
            for m, u in rows
        ],
        "invitations": [
            {"id": i.id, "email": i.email, "role": _role(i.role), "expires_at": i.expires_at, "created_at": i.created_at} for i in invites
        ],
    }


def _can_grant(ctx: WorkspaceContext, role: Role) -> None:
    if role.key == "owner":
        raise forbidden("Use ownership transfer to make someone the owner.", "owner_via_transfer")
    is_owner = ctx.user.id == ctx.workspace.owner_id
    if not is_owner and role.rank >= ctx.membership.role.rank:
        raise forbidden("You can only grant roles below your own.", "role_escalation")
    if not is_owner and any(p not in ctx.permissions for p in role.permissions):
        raise forbidden("You can't grant permissions you don't have.", "role_escalation")


async def _workspace_role(db, ctx: WorkspaceContext, role_id: uuid.UUID) -> Role:
    role = await db.get(Role, role_id)
    if role is None or (role.workspace_id is not None and role.workspace_id != ctx.workspace.id):
        raise not_found("Role")
    return role


@router.post("/workspace/invitations", status_code=201)
async def invite(body: InviteIn, request: Request, db: Db, ctx: WorkspaceContext = require("members.manage", subscription=True)):
    email = body.email.lower()
    role = await _workspace_role(db, ctx, body.role_id)
    _can_grant(ctx, role)
    limits = await effective_limits(db, ctx.workspace.id)
    if not _limit_ok(limits["members"], await seat_count(db, ctx.workspace.id)):
        raise ApiError(402, "limit_reached", "Your plan's member limit is reached. Upgrade to add more people.", limit="members")
    already = (
        await db.execute(
            select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == ctx.workspace.id, User.email == email)
        )
    ).first()
    if already:
        raise ApiError(409, "already_member", "That person is already a member.")
    # Replace any open invitation to the same address.
    for old in (
        await db.execute(
            select(Invitation).where(
                Invitation.workspace_id == ctx.workspace.id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
        )
    ).scalars():
        old.revoked_at = utcnow()
    token = new_token()
    inv = Invitation(
        workspace_id=ctx.workspace.id,
        email=email,
        role_id=role.id,
        token_hash=hash_token(token),
        invited_by=ctx.user.id,
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(days=get_settings().invite_ttl_days),
    )
    db.add(inv)
    audit(
        db,
        "member.invited",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="invitation",
        target_id=email,
        role=role.key,
        ip=client_ip(request),
    )
    await db.commit()
    await mail.send_invitation(email, ctx.user.full_name, ctx.workspace.name, role.name, token)
    return {"id": inv.id, "email": email}


@router.delete("/workspace/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("members.manage")):
    inv = await db.get(Invitation, invitation_id)
    if inv is None or inv.workspace_id != ctx.workspace.id:
        raise not_found("Invitation")
    inv.revoked_at = utcnow()
    audit(db, "member.invite_revoked", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, target_type="invitation", target_id=inv.email)
    await db.commit()


async def _member(db, ctx: WorkspaceContext, membership_id: uuid.UUID) -> Membership:
    m = await db.get(Membership, membership_id)
    if m is None or m.workspace_id != ctx.workspace.id:
        raise not_found("Member")
    return m


def _can_manage(ctx: WorkspaceContext, target: Membership) -> None:
    if target.user_id == ctx.workspace.owner_id:
        raise forbidden("The workspace owner can't be changed here.", "owner_protected")
    if ctx.user.id != ctx.workspace.owner_id and target.role.rank >= ctx.membership.role.rank:
        raise forbidden("You can only manage members below your own role.", "role_escalation")


@router.patch("/workspace/members/{membership_id}")
async def change_role(
    membership_id: uuid.UUID, body: RoleChangeIn, request: Request, db: Db, ctx: WorkspaceContext = require("members.manage")
):
    target = await _member(db, ctx, membership_id)
    _can_manage(ctx, target)
    role = await _workspace_role(db, ctx, body.role_id)
    _can_grant(ctx, role)
    old = target.role.key
    target.role_id = role.id
    audit(
        db,
        "member.role_changed",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="user",
        target_id=target.user_id,
        old=old,
        new=role.key,
        ip=client_ip(request),
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/workspace/members/{membership_id}", status_code=204)
async def remove_member(membership_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = require("members.manage")):
    target = await _member(db, ctx, membership_id)
    _can_manage(ctx, target)
    await db.delete(target)
    audit(
        db,
        "member.removed",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="user",
        target_id=target.user_id,
        ip=client_ip(request),
    )
    await db.commit()


@router.post("/workspace/leave", status_code=204)
async def leave(ctx: Ctx, db: Db):
    if ctx.user.id == ctx.workspace.owner_id:
        raise ApiError(409, "owner_cannot_leave", "Transfer ownership before leaving this workspace.")
    await db.delete(ctx.membership)
    if ctx.user.last_workspace_id == ctx.workspace.id:
        ctx.user.last_workspace_id = None
    audit(db, "member.left", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, target_type="user", target_id=ctx.user.id)
    await db.commit()


@router.post("/workspace/transfer-ownership/{membership_id}")
async def transfer_ownership(membership_id: uuid.UUID, request: Request, db: Db, ctx: Ctx):
    if ctx.user.id != ctx.workspace.owner_id:
        raise forbidden("Only the owner can transfer ownership.")
    target = await _member(db, ctx, membership_id)
    if target.user_id == ctx.user.id:
        raise ApiError(400, "same_user", "You already own this workspace.")
    roles = {r.key: r for r in await roles_for(db, ctx.workspace.id) if r.is_system}
    target.role_id = roles["owner"].id
    ctx.membership.role_id = roles["admin"].id
    ctx.workspace.owner_id = target.user_id
    audit(
        db,
        "workspace.ownership_transferred",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="user",
        target_id=target.user_id,
        ip=client_ip(request),
    )
    await db.commit()
    return {"status": "ok"}


# ---- Roles --------------------------------------------------------------------------------------


@router.get("/workspace/roles")
async def list_roles(db: Db, ctx: WorkspaceContext = require("members.read")):
    return [_role(r) for r in await roles_for(db, ctx.workspace.id)]


@router.post("/workspace/roles", status_code=201)
async def create_role(body: RoleIn, db: Db, ctx: WorkspaceContext = require("members.manage", subscription=True)):
    unknown = [p for p in body.permissions if p not in PERMISSIONS]
    if unknown:
        raise ApiError(422, "unknown_permission", f"Unknown permissions: {', '.join(unknown)}")
    if ctx.user.id != ctx.workspace.owner_id and any(p not in ctx.permissions for p in body.permissions):
        raise forbidden("You can't create a role with permissions you don't have.", "role_escalation")
    key = "custom-" + slugify(body.name)
    exists = (await db.execute(select(Role.id).where(Role.workspace_id == ctx.workspace.id, Role.key == key))).first()
    if exists:
        raise ApiError(409, "role_exists", "A role with that name already exists.")
    role = Role(
        workspace_id=ctx.workspace.id,
        key=key,
        name=body.name,
        description=body.description,
        permissions=sorted(set(body.permissions)),
        is_system=False,
        rank=min(ctx.membership.role.rank - 1, 79),
    )
    db.add(role)
    audit(db, "role.created", actor_id=ctx.user.id, workspace_id=ctx.workspace.id, target_type="role", target_id=key)
    await db.commit()
    return _role(role)


@router.delete("/workspace/roles/{role_id}", status_code=204)
async def delete_role(role_id: uuid.UUID, db: Db, ctx: WorkspaceContext = require("members.manage")):
    role = await db.get(Role, role_id)
    if role is None or role.workspace_id != ctx.workspace.id:
        raise not_found("Role")
    in_use = (await db.execute(select(Membership.id).where(Membership.role_id == role.id))).first()
    if in_use:
        raise ApiError(409, "role_in_use", "Move members off this role before deleting it.")
    await db.delete(role)
    await db.commit()


# ---- Invitations (invitee side) -----------------------------------------------------------------


async def _open_invite(db, token: str) -> Invitation:
    inv = (await db.execute(select(Invitation).where(Invitation.token_hash == hash_token(token)))).scalar_one_or_none()
    if inv is None or inv.revoked_at or inv.accepted_at:
        raise ApiError(400, "invalid_token", "This invitation is no longer valid.")
    if inv.expires_at <= utcnow():
        raise ApiError(400, "expired_token", "This invitation has expired. Ask for a new one.")
    return inv


@router.get("/invitations/{token}")
async def preview_invitation(token: str, db: Db):
    inv = await _open_invite(db, token)
    ws = await db.get(Workspace, inv.workspace_id)
    inviter = await db.get(User, inv.invited_by)
    return {"workspace": ws.name, "role": inv.role.name, "email": inv.email, "invited_by": inviter.full_name}


@router.post("/invitations/{token}/accept")
async def accept_invitation(token: str, request: Request, user: VerifiedUser, db: Db):
    inv = await _open_invite(db, token)
    if inv.email != user.email:
        raise forbidden("This invitation was sent to a different email address.", "email_mismatch")
    ws = await db.get(Workspace, inv.workspace_id)
    if ws.status != "active":
        raise ApiError(403, "workspace_suspended", "This workspace is suspended.")
    exists = (
        await db.execute(select(Membership).where(Membership.workspace_id == ws.id, Membership.user_id == user.id))
    ).scalar_one_or_none()
    if exists is None:
        db.add(Membership(workspace_id=ws.id, user_id=user.id, role_id=inv.role_id))
    inv.accepted_at = utcnow()
    user.last_workspace_id = ws.id
    audit(
        db,
        "member.joined",
        actor_id=user.id,
        workspace_id=ws.id,
        target_type="user",
        target_id=user.id,
        role=inv.role.key,
        ip=client_ip(request),
    )
    await db.commit()
    return {"workspace_id": ws.id}


# ---- Audit log ----------------------------------------------------------------------------------


@router.get("/workspace/audit-logs")
async def workspace_audit(db: Db, limit: int = 50, ctx: WorkspaceContext = require("audit.read")):
    rows = (
        await db.execute(
            select(AuditLog, User.full_name)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .where(AuditLog.workspace_id == ctx.workspace.id)
            .order_by(AuditLog.created_at.desc())
            .limit(min(limit, 200))
        )
    ).all()
    return [
        {
            "id": a.id,
            "action": a.action,
            "actor": name,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "data": a.data,
            "ip": a.ip,
            "created_at": a.created_at,
        }
        for a, name in rows
    ]
