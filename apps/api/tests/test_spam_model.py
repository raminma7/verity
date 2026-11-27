"""Phase 6 spam model: the shipped base model on examples written for this project, per-workspace learning
(isolated per workspace), the thresholds, "possible spam" tickets and "Move to Spam", and no AI calls for spam."""

import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import EmailMessage, SpamModelStats, Ticket
from app.services.mail import spam_model
from tests.conftest import new_client
from tests.mail import connect, deliver, mail, owner, tickets
from tests.test_email_ingest import usage_ops
from tests.test_phase3_access import team

EXAMPLES = json.loads((Path(__file__).parent / "fixtures" / "spam_examples.json").read_text())


def p_base(e) -> float:
    return spam_model.sigmoid(
        spam_model.base_logit(spam_model.hashed(spam_model.features(e["subject"], e["body"], e.get("html"), e["from"])))
    )


def test_base_model_on_examples_it_never_saw():
    spam = [p_base(e) for e in EXAMPLES["spam"]]
    ham = [p_base(e) for e in EXAMPLES["legitimate"]]
    assert all(p >= spam_model.SPAM_AT for p in spam), spam  # every spam example is caught without AI
    assert all(p < spam_model.SPAM_AT for p in ham), ham  # no customer email is called spam
    assert sum(p <= spam_model.HAM_AT for p in ham) >= len(ham) * 2 / 3  # most aren't even "possible spam"


def test_features_hold_no_text_and_the_weights_no_messages():
    names = spam_model.features("Hello", "My order 123 is late", None, "a@b.example")
    assert all(n.startswith(("s:", "b:", "x:")) for n in names)
    raw = spam_model.WEIGHTS.read_text()
    data = json.loads(raw)
    assert data["dim"] == spam_model.DIM and all(k.isdigit() for k in data["w"])  # hashed indexes only
    assert "lottery" not in raw and len(raw) < 1_000_000


async def test_too_little_text_is_never_spam(client, db):
    await owner(client)
    ws = uuid.UUID(client.headers["X-Workspace-Id"])
    s = await spam_model.score(db, ws, "Help", "Please help", None, "jane@example.org")
    assert s["known_words"] < spam_model.MIN_KNOWN_WORDS and s["p_spam"] <= spam_model.HAM_AT


async def test_each_workspace_learns_from_its_own_reviews(client, db):
    await owner(client)
    ws = uuid.UUID(client.headers["X-Workspace-Id"])
    msg = ("Quarterly partner update", "Here are this quarter's partner programme numbers and the new referral terms for resellers.")
    before = (await spam_model.score(db, ws, *msg, None, "partners@vendor.example"))["p_spam"]
    for i in range(30):
        await spam_model.learn(db, ws, spam=True, subject=f"{msg[0]} {i}", text_body=msg[1], html=None, from_addr="partners@vendor.example")
    for i in range(30):
        await spam_model.learn(
            db,
            ws,
            spam=False,
            subject=f"Order {i} late",
            text_body="My order hasn't arrived, can you check?",
            html=None,
            from_addr="c@x.example",
        )
    await db.commit()
    after = (await spam_model.score(db, ws, *msg, None, "partners@vendor.example"))["p_spam"]
    assert after > before and after >= spam_model.SPAM_AT
    assert (await spam_model.stats(db, ws)) == {"spam_reviewed": 30, "legitimate_reviewed": 30}
    async with new_client() as other:
        other.headers["X-Forwarded-For"] = "198.51.100.20"
        await owner(other, "other@example.com")
        ws2 = uuid.UUID(other.headers["X-Workspace-Id"])
        assert (await spam_model.score(db, ws2, *msg, None, "partners@vendor.example"))["p_spam"] == before  # not learned there


async def test_possible_spam_becomes_a_ticket_and_move_to_spam_teaches_the_model(client, db):
    await owner(client, plan="growth")
    ws = uuid.UUID(client.headers["X-Workspace-Id"])
    clients = await team(client)
    a = await connect(client)
    e = EXAMPLES["legitimate"][1]  # a billing question the base model isn't sure about
    await deliver(client, a["id"], mail(e["subject"], e["body"], sender=e["from"])[0])
    [t] = await tickets(client)
    detail = (await client.get(f"/api/tickets/{t['id']}")).json()
    assert detail["possible_spam"] is True
    assert (await clients["viewer"].post(f"/api/tickets/{t['id']}/move-to-spam")).status_code == 403
    r = await clients["agent"].post(f"/api/tickets/{t['id']}/move-to-spam")
    assert r.status_code == 200 and r.json()["status"] == "closed"
    async with SessionLocal() as s:
        m = (await s.execute(select(EmailMessage).where(EmailMessage.direction == "in"))).scalar_one()
        assert m.status == "spam" and m.verdict["reviewed"] == "spam"
        assert (await s.get(SpamModelStats, ws)).spam_docs == 1
        assert (await s.get(Ticket, uuid.UUID(t["id"]))).status == "closed"
    spam_items = (await client.get("/api/email/spam")).json()["items"]
    assert [x["subject"] for x in spam_items] == [e["subject"]]
    # "Not spam" brings it back and teaches the other way.
    r = await client.post(f"/api/email/messages/{spam_items[0]['id']}/not-spam", json={})
    assert r.status_code == 200
    async with SessionLocal() as s:
        assert (await s.get(SpamModelStats, ws)).ham_docs == 1
    # Manual tickets can't be moved to Spam.
    manual = (await client.post("/api/tickets", json={"subject": "Internal"})).json()
    assert (await client.post(f"/api/tickets/{manual['id']}/move-to-spam")).json()["detail"]["code"] == "not_email"
    for c in clients.values():
        if c is not client:
            await c.aclose()


async def test_deleting_spam_teaches_the_model(client):
    await owner(client)
    ws = uuid.UUID(client.headers["X-Workspace-Id"])
    a = await connect(client)
    e = EXAMPLES["spam"][2]
    await deliver(client, a["id"], mail(e["subject"], e["body"], sender=e["from"])[0])
    [m] = (await client.get("/api/email/spam")).json()["items"]
    assert m["verdict"]["decided_by"] == "model"
    assert (await client.delete(f"/api/email/messages/{m['id']}")).status_code == 204
    async with SessionLocal() as s:
        assert (await s.get(SpamModelStats, ws)).spam_docs == 1
    assert await usage_ops() == []
