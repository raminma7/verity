"""A trained spam model, so spam never needs an AI call.

Two layers, both plain Python (no ML library at runtime):

1. A base logistic-regression model trained offline (`scripts/train_spam.py`) on a public corpus and shipped as
   sparse weights (`spam_base.json`): hashed word features from the subject and body plus a few structural ones
   (HTML, links, capitals, exclamation marks, money amounts). Only hashed numbers ship; no message text.
2. A per-workspace Naive Bayes layer that learns from the team's own reviews: every "Not spam", every "Delete"
   in the Spam section and every "Move to Spam" on a ticket updates counts in `spam_features`. Its log-odds are
   added to the base score, weighted by how much the workspace has taught it, so each workspace's model adapts
   to its own mail and never to anyone else's.

`score()` gives p(spam). The ingest pipeline decides: ≥ SPAM_AT spam; ≤ HAM_AT legitimate; in between, the mail
is treated as legitimate and marked "possible spam" (a lost customer request costs more than a spam ticket).
"""

import json
import math
import re
import uuid
import zlib
from functools import lru_cache
from html import unescape
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SpamModelStats
from app.models.base import utcnow

DIM = 1 << 20
SPAM_AT = 0.95  # at or above: spam, no AI call
HAM_AT = 0.35  # at or below: legitimate
WEAK_RULE_SPAM_AT = 0.6  # a weak spam rule hit lowers the bar
LEARN_FULL_WEIGHT_AT = 50  # reviewed messages before the workspace layer counts fully
MAX_WORKSPACE_SHIFT = 6.0  # the workspace layer can move the log-odds at most this far
MAX_FEATURES = 400
WEIGHTS = Path(__file__).with_name("spam_base.json")

_TOKEN = re.compile(r"[a-z0-9$€£']{2,24}|!")
_TAG = re.compile(r"<[^>]+>")
_LINK = re.compile(r"https?://|www\.", re.I)
_IMG = re.compile(r"<img\b", re.I)
_MONEY = re.compile(r"[$€£]\s?\d|\d+\s?(usd|eur|dollars)\b", re.I)


def _bucket(n: float, edges: tuple) -> int:
    return sum(1 for e in edges if n >= e)


def features(subject: str, text_body: str, html: str | None, from_addr: str = "") -> set[str]:
    """The feature names for one message. Identical in training and at runtime."""
    body = text_body or ""
    if not body.strip() and html:
        body = unescape(_TAG.sub(" ", html))
    body = body[:20000]
    out: set[str] = set()
    subj = (subject or "")[:300]
    for t in _TOKEN.findall(subj.lower()):
        out.add("s:" + t)
    words = _TOKEN.findall(body.lower())
    for t in words:
        out.add("b:" + t)
    letters = [c for c in subj + body if c.isalpha()]
    caps = sum(1 for c in letters if c.isupper()) / len(letters) if letters else 0
    out.add(f"x:caps:{_bucket(caps, (0.1, 0.25, 0.5))}")
    out.add(f"x:excl:{min(3, (subj + body).count('!') // 2)}")
    out.add(f"x:links:{_bucket(len(_LINK.findall(body + (html or ''))), (1, 3, 8))}")
    out.add(f"x:img:{min(3, len(_IMG.findall(html or '')))}")
    if html:
        out.add("x:html")
        if not (text_body or "").strip():
            out.add("x:html_only")
    if _MONEY.search(subj + " " + body):
        out.add("x:money")
    local = (from_addr or "").split("@")[0]
    if sum(c.isdigit() for c in local) >= 3:
        out.add("x:from_digits")
    if subj and subj.upper() == subj and any(c.isalpha() for c in subj):
        out.add("x:subj_caps")
    return out


def hashed(names: set[str]) -> list[int]:
    return sorted({zlib.crc32(n.encode()) % DIM for n in names})


@lru_cache(maxsize=1)
def base_model() -> tuple[float, dict[int, float]]:
    data = json.loads(WEIGHTS.read_text())
    assert data["dim"] == DIM, "spam_base.json was trained with another feature size"
    return float(data["bias"]), {int(k): float(v) for k, v in data["w"].items()}


MIN_KNOWN_WORDS = 4  # fewer words the model knows than this: too little to call it spam


def base_logit(idx: list[int]) -> float:
    bias, w = base_model()
    return bias + sum(w.get(i, 0.0) for i in idx)


def known_words(names: set[str]) -> int:
    _, w = base_model()
    return sum(1 for n in names if n[:2] in ("s:", "b:") and (zlib.crc32(n.encode()) % DIM) in w)


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-40.0, min(40.0, z))))


async def workspace_shift(db: AsyncSession, workspace_id: uuid.UUID, idx: list[int]) -> float:
    """Naive Bayes log-odds from the workspace's reviewed mail, weighted by how much it has taught the model."""
    stats = await db.get(SpamModelStats, workspace_id)
    if stats is None or stats.spam_docs + stats.ham_docs == 0:
        return 0.0
    rows = (
        await db.execute(
            text("SELECT spam, ham FROM spam_features WHERE workspace_id = :ws AND feature = ANY(:f)"),
            {"ws": workspace_id, "f": idx[: MAX_FEATURES * 4]},
        )
    ).all()
    s_docs, h_docs = stats.spam_docs, stats.ham_docs
    llr = math.log((s_docs + 1) / (h_docs + 1))
    for spam, ham in rows:
        llr += math.log((spam + 1) / (s_docs + 2)) - math.log((ham + 1) / (h_docs + 2))
    weight = min(1.0, (s_docs + h_docs) / LEARN_FULL_WEIGHT_AT)
    return max(-MAX_WORKSPACE_SHIFT, min(MAX_WORKSPACE_SHIFT, llr * weight))


async def score(db: AsyncSession, workspace_id: uuid.UUID, subject: str, text_body: str, html: str | None, from_addr: str) -> dict:
    names = features(subject, text_body, html, from_addr)
    idx = hashed(names)
    base = base_logit(idx)
    shift = await workspace_shift(db, workspace_id, idx)
    p = sigmoid(base + shift)
    known = known_words(names)
    if known < MIN_KNOWN_WORDS and shift <= 0:
        p = min(p, HAM_AT)  # a two-word "Help, please" isn't spam just because the model hasn't seen enough
    return {"p_spam": round(p, 4), "base": round(base, 3), "workspace": round(shift, 3), "known_words": known}


async def learn(
    db: AsyncSession, workspace_id: uuid.UUID, *, spam: bool, subject: str, text_body: str, html: str | None, from_addr: str
) -> None:
    """Teaches the workspace's layer one reviewed message. The caller commits."""
    idx = hashed(features(subject, text_body, html, from_addr))[:MAX_FEATURES]
    col = "spam" if spam else "ham"
    await db.execute(
        text(
            f"INSERT INTO spam_features (workspace_id, feature, spam, ham) "  # noqa: S608 - col is one of two literals
            f"SELECT :ws, f, {1 if spam else 0}, {0 if spam else 1} FROM unnest(CAST(:f AS bigint[])) AS f "
            f"ON CONFLICT (workspace_id, feature) DO UPDATE SET {col} = spam_features.{col} + 1"
        ),
        {"ws": workspace_id, "f": idx},
    )
    await db.execute(
        text(
            f"INSERT INTO spam_model_stats (workspace_id, spam_docs, ham_docs, updated_at) "  # noqa: S608
            f"VALUES (:ws, {1 if spam else 0}, {0 if spam else 1}, :now) "
            f"ON CONFLICT (workspace_id) DO UPDATE SET {col}_docs = spam_model_stats.{col}_docs + 1, updated_at = :now"
        ),
        {"ws": workspace_id, "now": utcnow()},
    )


async def stats(db: AsyncSession, workspace_id: uuid.UUID) -> dict:
    s = (await db.execute(select(SpamModelStats).where(SpamModelStats.workspace_id == workspace_id))).scalar_one_or_none()
    return {"spam_reviewed": s.spam_docs if s else 0, "legitimate_reviewed": s.ham_docs if s else 0}
