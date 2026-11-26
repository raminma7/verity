"""AnthropicLLM.structured(): the request it sends and how it reads the answer (no network: a stub client)."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.services.ai.llm import AnthropicLLM, LLMError

SCHEMA = {"type": "object", "properties": {"form_id": {"type": "string"}}, "required": ["form_id"], "additionalProperties": False}


def usage(i=120, o=12):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=30)


class Stub:
    def __init__(self, result):
        self.result, self.kwargs = result, None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def llm_with(result) -> tuple[AnthropicLLM, Stub]:
    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm._anthropic = anthropic
    llm.client = Stub(result)
    return llm, llm.client


async def call(llm, model="claude-opus-5-5"):
    return await llm.structured(model=model, system="sys", prompt="<conversation>hi</conversation>", schema=SCHEMA, max_tokens=2048)


async def test_request_uses_structured_outputs_low_effort_and_fallbacks():
    msg = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"form_id": "form_1"}')], usage=usage(), model="claude-opus-5-5", stop_reason="end_turn"
    )
    llm, stub = llm_with(msg)
    out = await call(llm)
    assert out.data == {"form_id": "form_1"} and out.input_tokens == 150 and out.output_tokens == 12 and not out.refused
    k = stub.kwargs
    assert k["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert k["system"][0]["cache_control"] == {"type": "ephemeral"} and k["messages"][0]["role"] == "user"
    # Haiku has no server-side fallback.
    llm, stub = llm_with(msg)
    await call(llm, "claude-haiku-5-5")
    assert "fallbacks" not in stub.kwargs


async def test_refusal_and_truncated_json():
    refused = SimpleNamespace(content=[], usage=usage(), model="claude-opus-5-5", stop_reason="refusal")
    out = await call(llm_with(refused)[0])
    assert out.refused and out.data == {}
    cut = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"form_id": "fo')], usage=usage(), model="m", stop_reason="max_tokens"
    )
    with pytest.raises(LLMError) as e:
        await call(llm_with(cut)[0])
    assert e.value.code == "llm_bad_output"


async def test_provider_errors_map_to_llm_errors():
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    limited = anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=req), body=None)
    with pytest.raises(LLMError) as e:
        await call(llm_with(limited)[0])
    assert e.value.code == "llm_rate_limited" and e.value.retryable
