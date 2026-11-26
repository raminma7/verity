"""Embedding providers. Voyage AI in production; a deterministic fake for tests, CI and e2e."""

import asyncio
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import get_settings
from app.models.knowledge import EMBEDDING_DIM

InputType = Literal["document", "query"]
BATCH = 64


class EmbeddingError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class Embeddings:
    vectors: list[list[float]]
    tokens: int


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str], input_type: InputType) -> Embeddings: ...


class VoyageEmbedder:
    def __init__(self, model: str, api_key: str | None, url: str):
        self.model = model
        self.url = url
        # Behind Verity's egress proxy the key is injected into the request; elsewhere it comes from
        # VERITY_VOYAGE_API_KEY. It is never logged or returned.
        self.headers = {"Authorization": f"Bearer {api_key or 'proxy-injected'}", "Content-Type": "application/json"}

    async def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        # Ingestion runs in the background and can wait out a rate limit; a customer's question can't.
        attempts = 3 if input_type == "document" else 1
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            for attempt in range(attempts):
                try:
                    r = await client.post(
                        self.url,
                        headers=self.headers,
                        json={"input": texts, "model": self.model, "input_type": input_type, "output_dimension": EMBEDDING_DIM},
                    )
                except httpx.HTTPError as e:
                    raise EmbeddingError("embedding_unavailable", f"Voyage request failed: {type(e).__name__}", retryable=True) from None
                if r.status_code == 429 and attempt + 1 < attempts:
                    try:
                        wait = float(r.headers.get("retry-after", ""))
                    except ValueError:
                        wait = 5.0 * (attempt + 1)
                    await asyncio.sleep(min(max(wait, 1.0), 30.0))
                    continue
                break
        if r.status_code == 429:
            raise EmbeddingError("embedding_rate_limited", "Voyage rate limit reached", retryable=True)
        if r.status_code >= 500:
            raise EmbeddingError("embedding_unavailable", f"Voyage returned {r.status_code}", retryable=True)
        if r.status_code >= 400:
            raise EmbeddingError("embedding_failed", f"Voyage returned {r.status_code}")
        body = r.json()
        data = sorted(body["data"], key=lambda d: d["index"])
        return Embeddings([d["embedding"] for d in data], int(body.get("usage", {}).get("total_tokens", 0)))


class FakeEmbedder:
    """Hashed bag-of-words vectors: texts that share words are close, so retrieval behaves sensibly in tests."""

    model = "fake-embedding"

    async def embed(self, texts: list[str], input_type: InputType) -> Embeddings:
        vectors, tokens = [], 0
        for t in texts:
            v = [0.0] * EMBEDDING_DIM
            words = re.findall(r"\w+", t.lower())
            tokens += max(1, len(t) // 4)
            for w in words:
                if len(w) < 3:
                    continue
                h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=4).digest(), "big")
                v[h % EMBEDDING_DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v] if any(v) else [1.0 / math.sqrt(EMBEDDING_DIM)] * EMBEDDING_DIM)
        return Embeddings(vectors, tokens)


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        s = get_settings()
        _embedder = FakeEmbedder() if s.embedding_provider == "fake" else VoyageEmbedder(s.embedding_model, s.voyage_api_key, s.voyage_url)
    return _embedder


def set_embedder(e: Embedder | None) -> None:
    global _embedder
    _embedder = e
