"""The form builder. Forms are read with chatbots.read and changed with chatbots.manage.

A form maps to one ticket category and is offered by the chatbots linked to it. Changing its fields bumps
its version; tickets keep the fields they were submitted with.
"""

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, WorkspaceContext, client_ip, require
from app.core.errors import ApiError, not_found
from app.models import Chatbot, Form, TicketCategory, chatbot_forms
from app.services import forms as fsvc
from app.services.audit import audit
from app.services.tickets import check_category

router = APIRouter(tags=["forms"])

READ = require("chatbots.read", subscription=True)
MANAGE = require("chatbots.manage", subscription=True)


class FormIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    category_id: uuid.UUID
    activation_condition: str = Field(default="", max_length=2000)
    status: str = Field(default="draft", pattern="^(draft|active)$")
    fields: list[dict] = Field(default_factory=list, max_length=fsvc.MAX_FIELDS)
    submit_label: str = Field(default="Send", min_length=1, max_length=40)
    success_message: str = Field(default="", max_length=500)
    chatbot_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class FormUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    category_id: uuid.UUID | None = None
    activation_condition: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(draft|active)$")
    fields: list[dict] | None = Field(default=None, max_length=fsvc.MAX_FIELDS)
    submit_label: str | None = Field(default=None, min_length=1, max_length=40)
    success_message: str | None = Field(default=None, max_length=500)
    chatbot_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


async def chatbot_ids_of(db: AsyncSession, form_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict = {i: [] for i in form_ids}
    if form_ids:
        for fid, bid in (
            await db.execute(select(chatbot_forms.c.form_id, chatbot_forms.c.chatbot_id).where(chatbot_forms.c.form_id.in_(form_ids)))
        ).all():
            out[fid].append(bid)
    return out


def form_out(f: Form, chatbot_ids: list | None = None, category: str | None = None) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "description": f.description,
        "category_id": f.category_id,
        "category": category,
        "activation_condition": f.activation_condition,
        "status": f.status,
        "fields": f.fields,
        "version": f.version,
        "submit_label": f.submit_label,
        "success_message": f.success_message,
        "is_contact": f.is_contact,
        "chatbot_ids": chatbot_ids or [],
        "created_at": f.created_at,
        "updated_at": f.updated_at,
    }


async def _form(db: AsyncSession, ctx: WorkspaceContext, form_id: uuid.UUID) -> Form:
    f = (await db.execute(select(Form).where(Form.id == form_id, Form.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if f is None:
        raise not_found("Form")
    return f


async def _set_chatbots(db: AsyncSession, ctx: WorkspaceContext, f: Form, ids: list[uuid.UUID]) -> None:
    ids = list(dict.fromkeys(ids))
    if ids:
        found = set((await db.execute(select(Chatbot.id).where(Chatbot.id.in_(ids), Chatbot.workspace_id == ctx.workspace.id))).scalars())
        if found != set(ids):
            raise ApiError(404, "chatbot_not_found", "Chatbot not found.", field="chatbot_ids")
    await db.execute(delete(chatbot_forms).where(chatbot_forms.c.form_id == f.id))
    if ids:
        await db.execute(insert(chatbot_forms), [{"form_id": f.id, "chatbot_id": b} for b in ids])


def _contact_keeps_email(fields: list[dict]) -> None:
    if not any(x["type"] == "email" and x["required"] for x in fields):
        raise ApiError(422, "contact_form_needs_email", "The contact form must keep a required email field, so the team can reply.")


async def _out(db: AsyncSession, f: Form) -> dict:
    ids = await chatbot_ids_of(db, [f.id])
    cat = (await db.execute(select(TicketCategory.name).where(TicketCategory.id == f.category_id))).scalar_one_or_none()
    return form_out(f, ids[f.id], cat)


@router.get("/forms")
async def list_forms(db: Db, include_archived: bool = False, ctx: WorkspaceContext = READ):
    # The contact form always exists, so the handoff fallback is visible and editable.
    await fsvc.contact_form(db, ctx.workspace.id)
    await db.commit()
    q = select(Form).where(Form.workspace_id == ctx.workspace.id)
    if not include_archived:
        q = q.where(Form.status != "archived")
    rows = (await db.execute(q.order_by(Form.is_contact.desc(), Form.name))).scalars().all()
    ids = await chatbot_ids_of(db, [f.id for f in rows])
    cats = dict(
        (await db.execute(select(TicketCategory.id, TicketCategory.name).where(TicketCategory.workspace_id == ctx.workspace.id))).all()
    )
    return [form_out(f, ids[f.id], cats.get(f.category_id)) for f in rows]


@router.post("/forms", status_code=201)
async def create_form(body: FormIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    await check_category(db, ctx.workspace.id, body.category_id)
    fields = fsvc.normalize_fields(body.fields)
    if body.status == "active" and not fields:
        raise ApiError(422, "invalid_form_definition", "Add at least one field before activating the form.")
    f = Form(
        workspace_id=ctx.workspace.id,
        name=" ".join(body.name.split()),
        description=body.description.strip(),
        category_id=body.category_id,
        activation_condition=body.activation_condition.strip(),
        status=body.status,
        fields=fields,
        submit_label=body.submit_label.strip(),
        success_message=body.success_message.strip(),
    )
    db.add(f)
    await db.flush()
    await _set_chatbots(db, ctx, f, body.chatbot_ids)
    audit(
        db,
        "form.created",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="form",
        target_id=f.id,
        ip=client_ip(request),
        name=f.name,
    )
    await db.commit()
    return await _out(db, f)


@router.get("/forms/{form_id}")
async def get_form(form_id: uuid.UUID, db: Db, ctx: WorkspaceContext = READ):
    return await _out(db, await _form(db, ctx, form_id))


@router.patch("/forms/{form_id}")
async def update_form(form_id: uuid.UUID, body: FormUpdateIn, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    f = await _form(db, ctx, form_id)
    if f.status == "archived":
        raise ApiError(409, "form_archived", "This form is archived. Restore it to edit.")
    changed: list[str] = []
    if body.name is not None:
        f.name = " ".join(body.name.split())
        changed.append("name")
    if body.description is not None:
        f.description = body.description.strip()
        changed.append("description")
    if body.category_id is not None and body.category_id != f.category_id:
        await check_category(db, ctx.workspace.id, body.category_id)
        f.category_id = body.category_id
        changed.append("category")
    if body.activation_condition is not None:
        f.activation_condition = body.activation_condition.strip()
        changed.append("activation_condition")
    if body.fields is not None:
        fields = fsvc.normalize_fields(body.fields)
        if f.is_contact:
            _contact_keeps_email(fields)
        if fields != f.fields:
            f.fields = fields
            f.version += 1
            changed.append("fields")
    if body.status is not None:
        if f.is_contact and body.status != "active":
            raise ApiError(422, "contact_form_required", "The contact form stays active: it's the fallback when nobody answers a handoff.")
        f.status = body.status
        changed.append("status")
    if f.status == "active" and not f.fields:
        raise ApiError(422, "invalid_form_definition", "Add at least one field before activating the form.")
    if body.submit_label is not None:
        f.submit_label = body.submit_label.strip()
        changed.append("submit_label")
    if body.success_message is not None:
        f.success_message = body.success_message.strip()
        changed.append("success_message")
    if body.chatbot_ids is not None:
        await _set_chatbots(db, ctx, f, body.chatbot_ids)
        changed.append("chatbots")
    audit(
        db,
        "form.updated",
        actor_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        target_type="form",
        target_id=f.id,
        ip=client_ip(request),
        changed=changed,
        version=f.version,
    )
    await db.commit()
    return await _out(db, f)


@router.post("/forms/{form_id}/archive")
async def archive_form(form_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    f = await _form(db, ctx, form_id)
    if f.is_contact:
        raise ApiError(422, "contact_form_required", "The contact form can't be archived: it's the fallback when nobody answers a handoff.")
    if f.status != "archived":
        f.status = "archived"
        await db.execute(delete(chatbot_forms).where(chatbot_forms.c.form_id == f.id))
        audit(
            db,
            "form.archived",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="form",
            target_id=f.id,
            ip=client_ip(request),
        )
        await db.commit()
    return await _out(db, f)


@router.post("/forms/{form_id}/restore")
async def restore_form(form_id: uuid.UUID, request: Request, db: Db, ctx: WorkspaceContext = MANAGE):
    f = await _form(db, ctx, form_id)
    if f.status == "archived":
        await check_category(db, ctx.workspace.id, f.category_id)
        f.status = "draft"
        audit(
            db,
            "form.restored",
            actor_id=ctx.user.id,
            workspace_id=ctx.workspace.id,
            target_type="form",
            target_id=f.id,
            ip=client_ip(request),
        )
        await db.commit()
    return await _out(db, f)
