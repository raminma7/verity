"""Generation providers. Claude in production (streaming, native citations); a deterministic fake for tests.

Knowledge passages go to the model as `search_result` blocks with citations enabled, so Claude returns
text blocks whose citations point at the exact passages they rely on (`search_result_index`). We map those
back to chunks; we never parse citation markers out of free text.
"""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class Passage:
    """One retrieved passage, as sent to the model."""

    source: str  # URL, or "file name, page 3"
    title: str
    text: str


@dataclass
class CitedSpan:
    text: str
    passages: list[int] = field(default_factory=list)  # indices into the request's passages
    cited_text: list[str] = field(default_factory=list)


@dataclass
class Generation:
    spans: list[CitedSpan]
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str | None
    refused: bool = False

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class Delta:
    text: str


class LLM(Protocol):
    def stream(
        self, *, model: str, system: str, history: list[dict], question: str, passages: list[Passage], max_tokens: int
    ) -> AsyncIterator[Delta | Generation]: ...


def _user_content(question: str, passages: list[Passage]) -> list[dict]:
    blocks: list[dict] = [
        {
            "type": "search_result",
            "source": p.source,
            "title": p.title or p.source,
            "content": [{"type": "text", "text": p.text}],
            "citations": {"enabled": True},
        }
        for p in passages
    ]
    blocks.append({"type": "text", "text": question})
    return blocks


class AnthropicLLM:
    # Fallback models are chosen server-side by refusal category. Haiku has no server-side fallback.
    FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")

    def __init__(self):
        import anthropic

        s = get_settings()
        # Behind Verity's egress proxy the key may be injected; elsewhere it comes from VERITY_ANTHROPIC_API_KEY.
        self.client = anthropic.AsyncAnthropic(
            api_key=s.anthropic_api_key or "proxy-injected", timeout=s.llm_timeout_seconds, max_retries=2
        )
        self._anthropic = anthropic

    async def stream(
        self, *, model: str, system: str, history: list[dict], question: str, passages: list[Passage], max_tokens: int
    ) -> AsyncIterator[Delta | Generation]:
        a = self._anthropic
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            # The system prompt is stable per chatbot, so it caches across conversations.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [*history, {"role": "user", "content": _user_content(question, passages)}],
            # Support answers are short and grounded: low effort keeps latency and cost down.
            "output_config": {"effort": "low"},
        }
        if model in self.FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            async with self.client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "text" and event.text:
                        yield Delta(event.text)
                final = await stream.get_final_message()
        except a.RateLimitError as e:
            raise LLMError("llm_rate_limited", "The AI provider is rate limiting requests.", retryable=True) from e
        except a.APIStatusError as e:
            raise LLMError("llm_unavailable", f"The AI provider returned {e.status_code}.", retryable=e.status_code >= 500) from e
        except a.APIConnectionError as e:
            raise LLMError("llm_unavailable", "Couldn't reach the AI provider.", retryable=True) from e

        spans: list[CitedSpan] = []
        for block in final.content:
            if block.type != "text":
                continue
            span = CitedSpan(block.text)
            for c in block.citations or []:
                idx = getattr(c, "search_result_index", None)
                if idx is not None and 0 <= idx < len(passages) and idx not in span.passages:
                    span.passages.append(idx)
                    span.cited_text.append(getattr(c, "cited_text", ""))
            spans.append(span)
        usage = final.usage
        yield Generation(
            spans=spans,
            input_tokens=(usage.input_tokens or 0) + (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0),
            output_tokens=usage.output_tokens or 0,
            model=final.model,
            stop_reason=final.stop_reason,
            refused=final.stop_reason == "refusal",
        )


class FakeLLM:
    """Answers with the first sentence of the most relevant passages and cites them. No network."""

    model_suffix = ""

    async def stream(
        self, *, model: str, system: str, history: list[dict], question: str, passages: list[Passage], max_tokens: int
    ) -> AsyncIterator[Delta | Generation]:
        if "[[fail]]" in question:
            raise LLMError("llm_unavailable", "Simulated provider outage.", retryable=True)
        spans: list[CitedSpan] = []
        if not passages:
            spans.append(CitedSpan("I couldn't find this in the knowledge base."))
        else:
            spans.append(CitedSpan("Here is what the documentation says. "))
            for i, p in enumerate(passages[:2]):
                sentence = re.split(r"(?<=[.!?])\s", p.text.strip(), maxsplit=1)[0][:300]
                spans.append(CitedSpan(sentence + " ", [i], [sentence]))
        for s in spans:
            for word in re.findall(r"\S+\s*", s.text):
                yield Delta(word)
        prompt = len(system) + len(question) + sum(len(p.text) for p in passages) + sum(len(str(m["content"])) for m in history)
        out = sum(len(s.text) for s in spans)
        yield Generation(spans, max(1, prompt // 4), max(1, out // 4), model, "end_turn")


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = FakeLLM() if get_settings().llm_provider == "fake" else AnthropicLLM()
    return _llm


def set_llm(llm: LLM | None) -> None:
    global _llm
    _llm = llm
