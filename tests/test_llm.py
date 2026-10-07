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
    LLMError,
    LLMSchemaError,
    LLMUnavailable,
    MissingAPIKeyError,
    TokenBucketLimiter,
    build_llm_client,
)
from atlas.core.settings import Settings
from atlas.research.agents.schemas import AnalystView

URL = f"{GEMINI_BASE}/gemini-3.5-flash:generateContent"

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


_RPD_BODY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota. limit: 20, model: gemini-3.5-flash",
        "status": "RESOURCE_EXHAUSTED",
        "details": [{
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{
                "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                "quotaValue": "20",
            }],
        }],
    }
}

_RPM_BODY = {
    "error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "details": [{
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}],
        }],
    }
}


@respx.mock
def test_per_day_quota_fails_fast_without_burning_minutes_on_backoff(tmp_path: Path) -> None:
    """A daily quota does not replenish in 60s. Backing off against it stalled a
    cycle for 277 seconds and still failed — 28 pointless waits."""
    slept: list[float] = []
    route = respx.post(URL).mock(return_value=httpx.Response(429, json=_RPD_BODY))
    with pytest.raises(LLMUnavailable) as exc:
        GeminiClient(
            api_key="k", limiter=TokenBucketLimiter(rate_per_minute=1000),
            usage_log_path=tmp_path / "usage.json", sleep=slept.append,
        ).complete([{"role": "user", "content": "u"}], AnalystView)
    assert route.call_count == 1, "a per-day quota must not be retried"
    assert not slept, "no backoff against a quota that resets at midnight"
    assert "midnight" in str(exc.value).lower()
    assert "20" in str(exc.value), "surface the actual daily allowance"


@respx.mock
def test_per_minute_quota_still_backs_off(tmp_path: Path) -> None:
    slept: list[float] = []
    respx.post(URL).side_effect = [
        httpx.Response(429, json=_RPM_BODY),
        httpx.Response(200, json=_gemini_response(json.dumps(VALID_VIEW))),
    ]
    client = GeminiClient(
        api_key="k", limiter=TokenBucketLimiter(rate_per_minute=1000),
        usage_log_path=tmp_path / "usage.json", sleep=slept.append,
    )
    assert client.complete([{"role": "user", "content": "u"}], AnalystView).summary == "ok"
    assert len(slept) == 1


@respx.mock
def test_429_without_a_parseable_quota_id_backs_off_conservatively(tmp_path: Path) -> None:
    """Unknown shape: assume transient rather than fail the cycle outright."""
    slept: list[float] = []
    respx.post(URL).side_effect = [
        httpx.Response(429, text="nope"),
        httpx.Response(200, json=_gemini_response(json.dumps(VALID_VIEW))),
    ]
    client = GeminiClient(
        api_key="k", limiter=TokenBucketLimiter(rate_per_minute=1000),
        usage_log_path=tmp_path / "usage.json", sleep=slept.append,
    )
    assert client.complete([{"role": "user", "content": "u"}], AnalystView).summary == "ok"
    assert len(slept) == 1


@respx.mock
def test_503_is_retried_like_429(tmp_path: Path) -> None:
    """Gemini's own 503 body says "Spikes in demand are usually temporary. Please
    try again later." It was not retried, so a transient blip killed an agent for
    the entire cycle — that is what cost market-data and news-analyst on
    2026-08-16, producing a brief on partial research."""
    slept: list[float] = []
    client = GeminiClient(
        api_key="k",
        limiter=TokenBucketLimiter(rate_per_minute=1000),
        usage_log_path=tmp_path / "usage.json",
        sleep=slept.append,
    )
    respx.post(URL).side_effect = [
        httpx.Response(503),
        httpx.Response(200, json=_gemini_response(json.dumps(VALID_VIEW))),
    ]
    assert client.complete([{"role": "user", "content": "u"}], AnalystView).summary == "ok"
    assert len(slept) == 1


@respx.mock
def test_persistent_503_raises_unavailable_not_a_bare_http_error(tmp_path: Path) -> None:
    respx.post(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LLMUnavailable, match="503"):
        GeminiClient(
            api_key="k",
            limiter=TokenBucketLimiter(rate_per_minute=1000),
            usage_log_path=tmp_path / "usage.json",
            sleep=lambda _: None,
        ).complete([{"role": "user", "content": "u"}], AnalystView)


@respx.mock
def test_a_400_is_not_retried_because_it_will_never_succeed(tmp_path: Path) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(LLMError):
        GeminiClient(
            api_key="k",
            limiter=TokenBucketLimiter(rate_per_minute=1000),
            usage_log_path=tmp_path / "usage.json",
            sleep=lambda _: None,
        ).complete([{"role": "user", "content": "u"}], AnalystView)
    assert route.call_count == 1


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
