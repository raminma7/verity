"""Data retention settings, deleting a workspace or an account (with a 30-day grace period), and data exports."""

import uuid

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core import ratelimit
from app.core.config import get_settings
from app.core.deps import Db, VerifiedUser, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.core.security import hash_token, verify_password
from app.models import DataExport, Membership, User, Workspace
from app.models.base import utcnow
from app.services import email as mail
from app.services import privacy
from app.services.audit import audit
from app.services.storage import get_storage

router = APIRouter(tags=["privacy"])


# ---- Retention --------------------------------------------------------------------------------------------


@router.get("/workspace/retention")
async def get_retention(db: Db, ctx: WorkspaceContext = require("workspace.read")):
    caps = await privacy.retention_caps(db)
    own = (ctx.workspace.settings or {}).get("retention") or {}
    return {
        "retention": {k: own.get(k) for k in privacy.RETENTION_KEYS},
        "caps": caps,
        "effective": privacy.effective_retention(ctx.workspace.settings, caps),
        "kinds": [{"key": k, "label": privacy.RETENTION_WORDS[k]} for k in privacy.RETENTION_KEYS],
        "can_edit": ctx.can("workspace.update"),
    }


class RetentionIn(BaseModel):
    retention: dict[str, int | None]


@router.put("/workspace/retention")
async def put_retention(body: RetentionIn, request: Request, db: Db, ctx: WorkspaceContext = require("workspace.update")):
    values = privacy.clean_retention(body.retention)
    caps = await privacy.retention_caps(db)
    for k, v in values.items():
        if caps.get(k) is not None and (v is None or v > caps[k]):
            raise ApiError(422, "retention_over_max", f"The platform keeps this for at most {caps[k]} days.", field=k, max_days=caps[k])
    ws = ctx.workspace
    ws.settings = {**(ws.settings or {}), "retention": values}
    audit(
        db,
        "workspace.retention_updated",
        actor_id=ctx.user.id,
        workspace_id=ws.id,
        target_type="workspace",
        target_id=ws.id,
        ip=client_ip(request),
        **values,
    )
    await db.commit()
    return await get_retention(db, ctx)


# ---- Deleting a workspace ---------------------------------------------------------------------------------


class DeleteWorkspaceIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    confirm_name: str = Field(min_length=1, max_length=200)


def _owner(ctx: WorkspaceContext) -> None:
    if ctx.workspace.owner_id != ctx.user.id:
        raise ApiError(403, "owner_only", "Only the workspace owner can do this.")


@router.post("/workspace/delete")
async def delete_workspace(body: DeleteWorkspaceIn, request: Request, db: Db, ctx: WorkspaceContext = require("workspace.delete")):
    _owner(ctx)
    if not verify_password(body.password, ctx.user.password_hash):
        raise ApiError(403, "wrong_password", "That password isn't right.", field="password")
    if body.confirm_name.strip() != ctx.workspace.name:
        raise ApiError(422, "name_mismatch", "Type the workspace's name exactly to confirm.", field="confirm_name")
    ws = (await db.execute(select(Workspace).where(Workspace.id == ctx.workspace.id).with_for_update())).scalar_one()
    erase_on = await privacy.request_workspace_deletion(db, ws, ctx.user, ip=client_ip(request))
    await db.commit()
    for address, locale in await privacy.workspace_member_emails(db, ws.id):
        await mail.send_workspace_deletion_scheduled(address, ws.name, erase_on, address == ctx.user.email, locale=locale)
    return {"erase_on": erase_on}


@router.post("/workspaces/{workspace_id}/restore")
async def restore_workspace(workspace_id: uuid.UUID, user: VerifiedUser, db: Db):
    ws = (await db.execute(select(Workspace).where(Workspace.id == workspace_id).with_for_update())).scalar_one_or_none()
    member = (await db.execute(select(Membership.id).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id))).first()
    if ws is None or member is None:
        raise ApiError(404, "workspace_not_found", "Workspace not found.")
    if ws.owner_id != user.id:
        raise ApiError(403, "owner_only", "Only the workspace owner can do this.")
    if ws.deletion_requested_at is None:
        raise ApiError(409, "not_scheduled", "This workspace isn't scheduled for deletion.")
    await privacy.restore_workspace(db, ws, user)
    await db.commit()
    return {
        "status": "active",
        "note": "The workspace is back. Its subscription ended when it was closed: choose a plan to turn features on again.",
    }


# ---- Deleting an account ----------------------------------------------------------------------------------


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/me/delete")
async def delete_account(body: DeleteAccountIn, request: Request, response: Response, user: VerifiedUser, db: Db):
    if not verify_password(body.password, user.password_hash):
        raise ApiError(403, "wrong_password", "That password isn't right.", field="password")
    if user.is_system_admin:
        raise ApiError(409, "admin_account", "System administrator accounts can't be deleted here.")
    owned, _ = await privacy.owned_workspaces(db, user)
    if owned:
        raise ApiError(
            409,
            "owner_must_transfer",
            "You own workspaces that other people use. Transfer ownership or delete them first.",
            workspaces=[w.name for w in owned],
        )
    erase_on = await privacy.request_account_deletion(db, user, ip=client_ip(request))
    await db.commit()
    await mail.send_account_deletion_scheduled(user.email, erase_on, locale=user.locale)
    response.delete_cookie(get_settings().session_cookie, path="/")
    return {"erase_on": erase_on}


# ---- Exports ----------------------------------------------------------------------------------------------


def _export(e: DataExport) -> dict:
    return {
        "id": e.id,
        "kind": e.kind,
        "status": e.status,
        "size": e.size,
        "created_at": e.created_at,
        "ready_at": e.ready_at,
        "expires_at": e.expires_at,
        "workspace_id": e.workspace_id,
    }


async def _queue_export(db, user: User, workspace_id: uuid.UUID | None, kind: str) -> DataExport:
    from app.jobs import enqueue

    await ratelimit.hit(f"export:{user.id}", "3/86400")
    ex = DataExport(id=uuid.uuid4(), workspace_id=workspace_id, user_id=user.id, kind=kind, status="queued", created_at=utcnow())
    db.add(ex)
    audit(db, f"privacy.export_{kind}", actor_id=user.id, workspace_id=workspace_id, target_type="export", target_id=ex.id)
    await db.commit()
    await enqueue("build_export", key=f"export:{ex.id}", job_timeout=1800, export_id=str(ex.id))
    await db.refresh(ex)
    return ex


@router.post("/workspace/exports", status_code=202)
async def export_workspace(db: Db, ctx: WorkspaceContext = require("workspace.delete")):
    _owner(ctx)
    return _export(await _queue_export(db, ctx.user, ctx.workspace.id, "workspace"))


@router.post("/me/exports", status_code=202)
async def export_me(user: VerifiedUser, db: Db):
    return _export(await _queue_export(db, user, None, "personal"))


@router.get("/exports")
async def my_exports(user: VerifiedUser, db: Db):
    rows = (
        await db.execute(select(DataExport).where(DataExport.user_id == user.id).order_by(DataExport.created_at.desc()).limit(20))
    ).scalars()
    return [_export(e) for e in rows]


@router.get("/exports/{token}/download")
async def download_export(token: str, user: VerifiedUser, db: Db):
    ex = (await db.execute(select(DataExport).where(DataExport.token_hash == hash_token(token)).with_for_update())).scalar_one_or_none()
    if ex is None or ex.user_id != user.id:
        raise not_found("Export")
    if ex.status != "ready" or (ex.expires_at and ex.expires_at < utcnow()):
        raise ApiError(410, "export_gone", "This download link has been used or has expired. Ask for a new export.")
    data = await get_storage().get(ex.storage_key)
    ex.status = "downloaded"
    audit(db, "privacy.export_downloaded", actor_id=user.id, workspace_id=ex.workspace_id, target_type="export", target_id=ex.id)
    await db.commit()
    name = f"verity-{ex.kind}-export-{ex.created_at:%Y%m%d}.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})
