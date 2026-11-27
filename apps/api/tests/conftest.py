import os

os.environ.setdefault("VERITY_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/verity_test")
os.environ["VERITY_ENV"] = "test"
os.environ["VERITY_EMAIL_BACKEND"] = "memory"
os.environ["VERITY_QUEUE"] = "inline"
os.environ["VERITY_LLM_PROVIDER"] = "fake"
os.environ["VERITY_EMBEDDING_PROVIDER"] = "fake"
os.environ["VERITY_MAIL_TRANSPORT"] = "fake"
os.environ.setdefault("VERITY_STORAGE_DIR", "/tmp/verity-test-storage")
os.environ["VERITY_CRAWL_ALLOW_PRIVATE"] = "true"
os.environ["VERITY_ADMIN_EMAIL"] = "root@example.org"
os.environ["VERITY_ADMIN_PASSWORD"] = "Admin-pass-123"

import re  # noqa: E402

import fakeredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import jobs  # noqa: E402
from app.core import ratelimit  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services import email as mail  # noqa: E402

KEEP = {"roles", "plans", "model_multipliers"}
PASSWORD = "Correct-horse-42"


@pytest.fixture(scope="session", autouse=True)
async def database():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await seed()
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean(database):
    ratelimit.set_redis(fakeredis.FakeAsyncRedis(decode_responses=True))
    mail.OUTBOX.clear()
    jobs.DELAYED.clear()
    yield
    tables = [t.name for t in Base.metadata.sorted_tables if t.name not in KEEP]
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))
        await conn.execute(text("DELETE FROM roles WHERE workspace_id IS NOT NULL"))
    await seed()  # recreate the admin user


@pytest.fixture
async def db():
    async with SessionLocal() as session:
        yield session


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client():
    async with new_client() as c:
        yield c


def last_token(to: str, path: str) -> str:
    for m in reversed(mail.OUTBOX):
        if m.to == to:
            for link in m.links:
                if path in link:
                    found = re.search(r"(?:token=|/invite/)([A-Za-z0-9_\-]+)", link)
                    if found:
                        return found.group(1)
    raise AssertionError(f"no {path} email for {to}")


async def signup(c: httpx.AsyncClient, email: str, name: str = "Ada Lovelace") -> dict:
    r = await c.post("/api/auth/register", json={"full_name": name, "email": email, "password": PASSWORD})
    assert r.status_code == 202, r.text
    r = await c.post("/api/auth/verify-email", json={"token": last_token(email, "/verify-email")})
    assert r.status_code == 200, r.text
    me = (await c.get("/api/me")).json()
    ws_id = me["workspaces"][0]["id"]
    c.headers["X-Workspace-Id"] = ws_id
    return me


async def subscribe(c: httpx.AsyncClient, plan_key: str = "starter") -> dict:
    plans = (await c.get("/api/plans")).json()
    plan = next(p for p in plans if p["key"] == plan_key)
    r = await c.post("/api/billing/checkout", json={"plan_id": plan["id"]})
    assert r.status_code == 201, r.text
    if r.json().get("scheduled"):
        return r.json()
    r = await c.post(f"/api/billing/checkout/{r.json()['checkout_id']}/confirm")
    assert r.status_code == 200, r.text
    return plan


async def login(c: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await c.post("/api/auth/login", json={"email": email, "password": password})
