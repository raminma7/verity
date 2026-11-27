from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.deps import Db, WorkspaceContext, require
from app.routers import (
    admin,
    analytics,
    auth,
    billing,
    chatbots,
    email,
    forms,
    knowledge,
    me,
    privacy,
    routing,
    tickets,
    tools,
    usage,
    widget,
    workspaces,
)
from app.services import email as mail
from app.services import payments, quota

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    s = get_settings()
    payments.check_configuration()
    app = FastAPI(title="Verity API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def origin_check(request: Request, call_next):
        # Session cookies are SameSite=Lax; this also refuses cross-site writes from browsers that send Origin.
        origin = request.headers.get("origin")
        if request.method in UNSAFE and origin and origin not in s.allowed_origins:
            return JSONResponse({"detail": {"code": "bad_origin", "message": "Cross-site request refused."}}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        field = str(first.get("loc", ["", ""])[-1])
        msg = str(first.get("msg", "Invalid input.")).removeprefix("Value error, ")
        if field == "email":
            msg = "Enter a valid email address."
        return JSONResponse(
            {
                "detail": {
                    "code": "invalid_input",
                    "message": msg,
                    "field": field,
                    "errors": [{"loc": e["loc"], "msg": e["msg"]} for e in errors],
                }
            },
            status_code=422,
        )

    api = APIRouter(prefix="/api")
    for r in (
        auth.router,
        me.router,
        workspaces.router,
        billing.router,
        usage.router,
        admin.router,
        analytics.router,
        knowledge.router,
        chatbots.router,
        forms.router,
        tickets.router,
        routing.router,
        email.router,
        tools.router,
        widget.router,
        privacy.router,
    ):
        api.include_router(r)

    @api.get("/health")
    async def health():
        return {"status": "ok"}

    if s.is_dev:
        dev = APIRouter(prefix="/dev", tags=["dev"])

        @dev.get("/outbox")
        async def outbox(to: str | None = None):
            items = [m for m in mail.OUTBOX if to is None or m.to == to.lower()]
            return [{"to": m.to, "subject": m.subject, "text": m.text, "links": m.links, "headers": m.headers} for m in items[-20:]]

        @dev.post("/mailbox/{address}/deliver")
        async def deliver_mail(address: str, request: Request, fetch: bool = True):
            """Fake mail transport only: puts a raw message in a mailbox, then fetches it (like new mail arriving)."""
            from sqlalchemy import select

            from app.core.db import SessionLocal
            from app.models import EmailAccount
            from app.services.mail.fetch import queue_fetch
            from app.services.mail.transport import FakeTransport

            if s.mail_transport != "fake":
                return JSONResponse({"detail": {"code": "fake_transport_only", "message": "Set VERITY_MAIL_TRANSPORT=fake."}}, 409)
            uid = await FakeTransport.deliver(address, await request.body())
            if fetch:
                async with SessionLocal() as db:
                    ids = (
                        (
                            await db.execute(
                                select(EmailAccount.id).where(EmailAccount.address == address.lower(), EmailAccount.status == "active")
                            )
                        )
                        .scalars()
                        .all()
                    )
                for i in ids:
                    await queue_fetch(i)
            return {"uid": uid}

        @dev.get("/mailbox/{address}/sent")
        async def sent_mail(address: str):
            from email import policy
            from email.parser import Parser

            from app.services.mail.transport import FakeTransport

            out = []
            for raw in (await FakeTransport.sent(address))[-50:]:
                m = Parser(policy=policy.default).parsestr(raw)
                body = m.get_body(("plain",))
                out.append(
                    {
                        "to": str(m["To"]),
                        "subject": str(m["Subject"]),
                        "message_id": str(m["Message-ID"]),
                        "in_reply_to": str(m["In-Reply-To"] or ""),
                        "references": str(m["References"] or ""),
                        "auto_submitted": str(m["Auto-Submitted"] or ""),
                        "text": body.get_content() if body is not None else "",
                    }
                )
            return out

        class SimIn(BaseModel):
            model: str = "claude-haiku-5-5"
            operation: str = "chat_response"
            input_tokens: int = Field(default=1200, ge=0, le=1_000_000)
            output_tokens: int = Field(default=400, ge=0, le=1_000_000)
            count: int = Field(default=1, ge=1, le=200)

        @dev.post("/simulate-usage")
        async def simulate(body: SimIn, db: Db, ctx: WorkspaceContext = require("usage.read", subscription=True)):
            """Runs the real reserve → settle path without calling a model, to exercise the quota UI."""
            n = 0
            for _ in range(body.count):
                res = await quota.reserve(
                    db,
                    ctx.workspace.id,
                    model=body.model,
                    operation=body.operation,
                    est_input=body.input_tokens,
                    est_output=body.output_tokens,
                )
                await quota.settle(
                    db,
                    res.id,
                    input_tokens=body.input_tokens,
                    output_tokens=body.output_tokens,
                    kind="embedding" if body.operation == "embedding" else "llm",
                )
                n += 1
            return {"settled": n}

        @dev.post("/billing/renew")
        async def dev_renew(db: Db, ctx: WorkspaceContext = require("billing.manage")):
            """Test provider only: ends the current period now and runs the renewal job for this workspace, so a
            renewal (or, with the declining test card, a failed one) can be tried without waiting a month."""

            from sqlalchemy import update

            from app.models import Subscription, TokenLedger
            from app.models.base import utcnow
            from app.services.subscriptions import renew_due

            sub = ctx.subscription
            if sub is None or sub.provider != "mock":
                return JSONResponse({"detail": {"code": "mock_only", "message": "Only test subscriptions renew here."}}, 409)
            now = utcnow()
            await db.execute(
                update(Subscription).where(Subscription.id == sub.id, Subscription.status == "active").values(current_period_end=now)
            )
            await db.execute(
                update(TokenLedger)
                .where(TokenLedger.workspace_id == sub.workspace_id, TokenLedger.is_current.is_(True), TokenLedger.period_end > now)
                .values(period_end=now)
            )
            await db.execute(
                update(Subscription).where(Subscription.id == sub.id, Subscription.status == "past_due").values(next_retry_at=now)
            )
            await db.commit()
            return await renew_due(db, workspace_id=ctx.workspace.id)

        api.include_router(dev)

    app.include_router(api)
    return app


app = create_app()
