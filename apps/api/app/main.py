from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.deps import Db, WorkspaceContext, require
from app.routers import admin, auth, billing, me, usage, workspaces
from app.services import email as mail
from app.services import quota

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    s = get_settings()
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
    for r in (auth.router, me.router, workspaces.router, billing.router, usage.router, admin.router):
        api.include_router(r)

    @api.get("/health")
    async def health():
        return {"status": "ok"}

    if s.is_dev:
        dev = APIRouter(prefix="/dev", tags=["dev"])

        @dev.get("/outbox")
        async def outbox(to: str | None = None):
            items = [m for m in mail.OUTBOX if to is None or m.to == to.lower()]
            return [{"to": m.to, "subject": m.subject, "text": m.text, "links": m.links} for m in items[-20:]]

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

        api.include_router(dev)

    app.include_router(api)
    return app


app = create_app()
