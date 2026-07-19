"""LLM layer gate: limiter ≤8/min, 429 backoff, repair retry, loud missing key."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from atlas.core.llm import (
    GEMINI_BASE,
    GeminiClient,
    LLMSchemaError,
    LLMUnavailable,
    MissingAPIKeyError,
    TokenBucketLimiter,
    build_llm_client,
)
from atlas.core.settings import Settings
from atlas.research.agents.schemas import AnalystView

URL = f"{GEMINI_BASE}/gemini-3-flash:generateContent"

VALID_VIEW = {
    "agent": "market-data",
    "summary": "ok",
    "key_findings": [],
    "confidence": 0.5,
    "injection_flags": [],
    "anomalies": [],
}


def _gemini_response(text: str) -> dict[str, object]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
    }


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_limiter_caps_at_8_per_rolling_minute() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(rate_per_minute=8, clock=clock, sleep=clock.sleep)
    for _ in range(8):
        assert limiter.acquire() == 0.0  # first 8 pass immediately
    waited = limiter.acquire()  # 9th must wait for the window to roll
    assert waited > 0
    assert clock.slept, "9th call should have slept"


def test_missing_key_fails_loudly_with_instructions() -> None:
    with pytest.raises(MissingAPIKeyError) as exc:
        GeminiClient(api_key="")
    msg = str(exc.value)
    assert ".env" in msg and "aistudio.google.com" in msg  # one-liner, not a stack trace


def _client(tmp_path: Path, **kw: object) -> GeminiClient:
    return GeminiClient(
        api_key="test-key",
        limiter=TokenBucketLimiter(rate_per_minute=1000),
        usage_log_path=tmp_path / "usage.json",
        sleep=lambda _: None,
        **kw,  # type: ignore[arg-type]
    )


@respx.mock
def test_structured_output_validates_and_logs_usage(tmp_path: Path) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_gemini_response(json.dumps(VALID_VIEW)))
    )
    view = _client(tmp_path).complete(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        AnalystView,
        agent="market-data",
    )
    assert view.summary == "ok"
    body = json.loads(route.calls[0].request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in body["generationConfig"]
    assert body["systemInstruction"]["parts"][0]["text"] == "s"

    usage = json.loads((tmp_path / "usage.json").read_text())
    assert usage[0]["prompt_tokens"] == 100 and usage[0]["agent"] == "market-data"


@respx.mock
def test_schema_invalid_gets_one_repair_retry(tmp_path: Path) -> None:
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(200, json=_gemini_response('{"not": "valid"}')),
        httpx.Response(200, json=_gemini_response(json.dumps(VALID_VIEW))),
    ]
    view = _client(tmp_path).complete([{"role": "user", "content": "u"}], AnalystView)
    assert view.summary == "ok"
    assert route.call_count == 2
    repair_body = json.loads(route.calls[1].request.content)
    assert "failed schema validation" in json.dumps(repair_body)


@respx.mock
def test_schema_invalid_twice_raises(tmp_path: Path) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=_gemini_response("garbage")))
    with pytest.raises(LLMSchemaError):
        _client(tmp_path).complete([{"role": "user", "content": "u"}], AnalystView)


@respx.mock
def test_429_backs_off_with_jitter_then_succeeds(tmp_path: Path) -> None:
    slept: list[float] = []
    client = GeminiClient(
        api_key="k",
        limiter=TokenBucketLimiter(rate_per_minute=1000),
        usage_log_path=tmp_path / "usage.json",
        sleep=slept.append,
    )
    respx.post(URL).side_effect = [
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(200, json=_gemini_response(json.dumps(VALID_VIEW))),
    ]
    view = client.complete([{"role": "user", "content": "u"}], AnalystView)
    assert view.summary == "ok"
    assert len(slept) == 2
    assert slept[0] >= 2 and slept[1] >= 4  # exponential
    assert slept[0] != slept[1]  # jitter — never identical immediate retries


@respx.mock
def test_persistent_429_raises_unavailable_with_quota_hint(tmp_path: Path) -> None:
    respx.post(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(LLMUnavailable, match="midnight Pacific"):
        _client(tmp_path).complete([{"role": "user", "content": "u"}], AnalystView)


def test_factory_selects_provider() -> None:
    with pytest.raises(MissingAPIKeyError):
        build_llm_client(Settings(_env_file=None, gemini_api_key="", llm_provider="gemini"))
    client = build_llm_client(Settings(_env_file=None, gemini_api_key="k", llm_provider="gemini"))
    assert isinstance(client, GeminiClient)
    ollama = build_llm_client(Settings(_env_file=None, llm_provider="ollama"))
    assert type(ollama).__name__ == "OllamaClient"
