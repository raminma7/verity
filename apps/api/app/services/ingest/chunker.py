"""Split sections into overlapping, retrieval-sized chunks.

Structure first: paragraphs are kept whole when they fit, long ones split at sentences, then at words.
Small neighbouring sections (short PDF pages) are merged so chunks aren't fragments; the chunk records
the range it spans (page and page_end). Headings are prefixed to their chunks so a passage still says
what it's about when it's retrieved on its own.
"""

import math
import re
from dataclasses import dataclass, field

from app.services.ingest.parsers import Section

MAX_TOKENS = 500
OVERLAP_TOKENS = 80
MIN_TOKENS = 120


def estimate_tokens(text: str) -> int:
    """About four characters per token for English prose; the embedding API reports the exact figure."""
    return max(1, math.ceil(len(text) / 4))


@dataclass
class Chunk:
    content: str
    token_count: int
    meta: dict = field(default_factory=dict)


_SENT = re.compile(r"(?<=[.!?。！？])\s+")


def _units(text: str, max_tokens: int) -> list[tuple[str, bool]]:
    """(piece, starts_paragraph) pairs, each piece within max_tokens."""
    out: list[tuple[str, bool]] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if estimate_tokens(para) <= max_tokens:
            out.append((para, True))
            continue
        first = True
        for sent in _SENT.split(para):
            sent = sent.strip()
            if not sent:
                continue
            pieces = [sent]
            if estimate_tokens(sent) > max_tokens:
                pieces, buf = [], []
                for w in sent.split():
                    if buf and estimate_tokens(" ".join([*buf, w])) > max_tokens:
                        pieces.append(" ".join(buf))
                        buf = []
                    buf.append(w[: max_tokens * 4])
                if buf:
                    pieces.append(" ".join(buf))
            for piece in pieces:
                out.append((piece, first))
                first = False
    return out


def _join(units: list[tuple[str, bool]]) -> str:
    text = ""
    for i, (piece, starts) in enumerate(units):
        text += piece if i == 0 else ("\n\n" if starts else " ") + piece
    return text


def _merge_small(sections: list[Section]) -> list[Section]:
    """Merge consecutive small sections that share a heading/sheet, keeping the page range."""
    merged: list[Section] = []
    for s in sections:
        prev = merged[-1] if merged else None
        same_context = prev is not None and all(prev.meta.get(k) == s.meta.get(k) for k in ("heading", "sheet"))
        if (
            prev is not None
            and same_context
            and estimate_tokens(prev.text) < MIN_TOKENS
            and estimate_tokens(prev.text + s.text) <= MAX_TOKENS
            and "rows" not in s.meta
        ):
            meta = dict(prev.meta)
            if "page" in s.meta:
                meta["page_end"] = s.meta.get("page_end", s.meta["page"])
            merged[-1] = Section(prev.text + "\n\n" + s.text, meta)
        else:
            merged.append(Section(s.text, dict(s.meta)))
    return merged


def chunk_sections(sections: list[Section], *, max_tokens: int = MAX_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in _merge_small(sections):
        prefix = f"{section.meta['heading']}\n\n" if section.meta.get("heading") else ""
        budget = max_tokens - estimate_tokens(prefix) if prefix else max_tokens
        units = _units(section.text, budget)
        current: list[tuple[str, bool]] = []
        for unit in units:
            if current and estimate_tokens(_join([*current, unit])) > budget:
                body = _join(current)
                chunks.append(Chunk(prefix + body, estimate_tokens(prefix + body), dict(section.meta)))
                tail: list[tuple[str, bool]] = []
                for u in reversed(current):  # carry the last ~overlap tokens into the next chunk
                    if estimate_tokens(_join([u, *tail])) > overlap:
                        break
                    tail.insert(0, u)
                if estimate_tokens(_join([*tail, unit])) > budget:
                    tail = []
                current = tail
            current.append(unit)
        if current:
            body = _join(current)
            chunks.append(Chunk(prefix + body, estimate_tokens(prefix + body), dict(section.meta)))
    return chunks
