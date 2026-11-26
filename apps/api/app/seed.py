"""Idempotent seed: system roles, default plans, model multipliers, and (optionally) the first system admin.

python -m app.seed
"""

import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import ModelMultiplier, Plan, Role, User
from app.models.base import utcnow
from app.services.permissions import DEFAULT_ROLES

PLANS = [
    {
        "key": "starter",
        "name": "Starter",
        "description": "For small teams answering their first questions with AI.",
        "price_cents": 2900,
        "token_allowance": 1_000_000,
        "limits": {
            "documents": 100,
            "domains": 10,
            "crawled_pages": 1000,
            "chatbot_domains": 1,
            "email_accounts": 3,
            "members": 3,
            "storage_mb": 1024,
        },
        "features": ["1 chatbot domain", "100 documents", "10 crawled domains", "3 email accounts", "3 members"],
        "sort_order": 10,
    },
    {
        "key": "growth",
        "name": "Growth",
        "description": "For support teams that run most conversations through Verity.",
        "price_cents": 9900,
        "token_allowance": 5_000_000,
        "limits": {
            "documents": 500,
            "domains": 25,
            "crawled_pages": 10000,
            "chatbot_domains": 3,
            "email_accounts": 10,
            "members": 15,
            "storage_mb": 10240,
        },
        "features": ["3 chatbot domains", "500 documents", "25 crawled domains", "10 email accounts", "15 members"],
        "sort_order": 20,
    },
    {
        "key": "scale",
        "name": "Scale",
        "description": "For large support organisations with high volume.",
        "price_cents": 29900,
        "token_allowance": 20_000_000,
        "limits": {
            "documents": 2500,
            "domains": 100,
            "crawled_pages": 100000,
            "chatbot_domains": 10,
            "email_accounts": 30,
            "members": -1,
            "storage_mb": 51200,
        },
        "features": ["10 chatbot domains", "2,500 documents", "100 crawled domains", "30 email accounts", "Unlimited members"],
        "sort_order": 30,
    },
]

MULTIPLIERS = [
    # model, operation, input weight, output weight, $ in /1M, $ out /1M
    # Weights are what customers are charged in weighted tokens; admins tune them (and the provider cost
    # columns, which only feed admin cost reports) under Admin → Model weights.
    ("claude-haiku-5-5", "*", 1.0, 5.0, 1.0, 5.0),
    ("claude-sonnet-5-5", "*", 3.0, 15.0, 3.0, 15.0),
    ("claude-opus-5-5", "*", 5.0, 25.0, 5.0, 25.0),
    ("text-embedding", "embedding", 0.1, 0.0, 0.1, 0.0),
    ("voyage-4", "embedding", 0.1, 0.0, 0.1, 0.0),
    ("fake-embedding", "embedding", 0.1, 0.0, 0.0, 0.0),
]


async def seed() -> None:
    s = get_settings()
    async with SessionLocal() as db:
        for r in DEFAULT_ROLES:
            role = (await db.execute(select(Role).where(Role.workspace_id.is_(None), Role.key == r["key"]))).scalar_one_or_none()
            if role is None:
                role = Role(workspace_id=None, key=r["key"], is_system=True)
                db.add(role)
            role.name, role.description, role.permissions, role.rank = r["name"], r["description"], r["permissions"], r["rank"]
        for p in PLANS:
            if not (await db.execute(select(Plan.id).where(Plan.key == p["key"]))).first():
                db.add(Plan(currency="USD", billing_cycle="month", **p))
        for model, op, wi, wo, ci, co in MULTIPLIERS:
            if not (
                await db.execute(select(ModelMultiplier.id).where(ModelMultiplier.model == model, ModelMultiplier.operation == op))
            ).first():
                db.add(
                    ModelMultiplier(
                        model=model, operation=op, input_multiplier=wi, output_multiplier=wo, input_cost_per_m=ci, output_cost_per_m=co
                    )
                )
        if s.admin_email and s.admin_password:
            email = s.admin_email.lower()
            admin = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if admin is None:
                db.add(
                    User(
                        full_name="System Administrator",
                        email=email,
                        password_hash=hash_password(s.admin_password),
                        email_verified_at=utcnow(),
                        is_system_admin=True,
                    )
                )
            else:
                admin.is_system_admin = True
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
    print("Seeded roles, plans and multipliers.")
