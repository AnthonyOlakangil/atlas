"""LLM provider layer (§3.1): one protocol, many backends. Gemini is default.

Agents depend on `LLMClient`, never on a provider. Swap providers by changing
LLM_PROVIDER in .env — no agent code changes.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from atlas.core.budget import (
    FREE_TIER_RPD,
    PACIFIC,
    record_quota_exhaustion,
)
from atlas.core.logging import get_logger, run_dir
from atlas.core.settings import Settings, get_settings

log = get_logger("core.llm")

T = TypeVar("T", bound=BaseModel)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
FREE_TIER_RPM_CAP = 8  # self-imposed, under the ~10 RPM free-tier ceiling
MAX_429_RETRIES = 5

# Transient upstream conditions worth another attempt. 503 in particular is what
# Gemini returns under load ("Spikes in demand are usually temporary") — not
# retrying it meant one blip killed an agent for a whole cycle and the brief
# shipped on partial research. 4xx other than 429 will never succeed on retry.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _daily_quota_violation(resp: httpx.Response) -> str | None:
    """Return the daily allowance if this 429 is a per-DAY quota, else None.

    Google distinguishes the two in `QuotaFailure.violations[].quotaId`:
    `GenerateRequestsPerDayPerProjectPerModel-FreeTier` vs the per-minute form.
    They need opposite handling — a per-minute quota clears in under a minute,
    a per-day one does not clear until midnight Pacific. An unparseable body is
    treated as transient, because assuming exhaustion would fail a whole cycle
    on a malformed error.
    """
    try:
        violations = resp.json()["error"]["details"]
    except (ValueError, KeyError, TypeError):
        return None
    for detail in violations:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations", []):
            if "PerDay" in str(violation.get("quotaId", "")):
                return str(violation.get("quotaValue", "unknown"))
    return None


class LLMError(RuntimeError):
    pass


class MissingAPIKeyError(LLMError):
    pass


class LLMSchemaError(LLMError):
    """Response failed schema validation even after one repair attempt."""


class LLMUnavailable(LLMError):
    """Persistent 429/5xx — surface in the run log, don't crash the cycle."""


class LLMClient(Protocol):
    def complete(
        self, messages: list[dict[str, str]], schema: type[T], *, agent: str = ""
    ) -> T: ...


class TokenBucketLimiter:
    """Thread-safe rolling-minute limiter shared by all agents (§3.1).

    Five analysts run in parallel but are staggered under the cap here rather
    than firing at once.
    """

    def __init__(
        self,
        rate_per_minute: int = FREE_TIER_RPM_CAP,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._rate = rate_per_minute
        self._clock = clock
        self._sleep = sleep
        self._stamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a slot is free; return seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._stamps = [s for s in self._stamps if now - s < 60]
                if len(self._stamps) < self._rate:
                    self._stamps.append(now)
                    return waited
                wait = 60 - (now - self._stamps[0]) + 0.01
            waited += wait
            self._sleep(wait)


_shared_limiter: TokenBucketLimiter | None = None


def shared_limiter() -> TokenBucketLimiter:
    global _shared_limiter
    if _shared_limiter is None:
        _shared_limiter = TokenBucketLimiter()
    return _shared_limiter


def _log_usage(path: Path, entry: dict[str, Any]) -> None:
    """Append one call's token counts to runs/<date>/usage.json (§11.6)."""
    entries: list[dict[str, Any]] = []
    if path.exists():
        try:
            entries = json.loads(path.read_text())
        except json.JSONDecodeError:
            entries = []
    entries.append(entry)
    path.write_text(json.dumps(entries, indent=1))


class GeminiClient:
    """Gemini REST client: native JSON/structured-output mode, 429 backoff + jitter."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash",
        limiter: TokenBucketLimiter | None = None,
        usage_log_path: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise MissingAPIKeyError(
                "GEMINI_API_KEY is missing — edit the .env file in the repo root and set "
                "GEMINI_API_KEY=<your key> (free key: https://aistudio.google.com)"
            )
        self._key = api_key
        self._model = model
        self._limiter = limiter or shared_limiter()
        self._usage_path = usage_log_path
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout)

    def _usage_file(self) -> Path:
        return self._usage_path or (run_dir() / "usage.json")

    def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{GEMINI_BASE}/{self._model}:generateContent"
        for attempt in range(1, MAX_429_RETRIES + 1):
            self._limiter.acquire()
            try:
                resp = self._client.post(url, json=body, headers={"x-goog-api-key": self._key})
            except httpx.HTTPError as exc:
                raise LLMUnavailable(f"network error calling Gemini: {exc}") from exc
            if resp.status_code == 200:
                return dict(resp.json())
            if resp.status_code == 429:
                daily = _daily_quota_violation(resp)
                if daily is not None:
                    # Ground truth beats the local counter: usage.json logs only
                    # successful calls, so it under-reports after any failure.
                    # Record the reconciliation so preflight refuses the next
                    # cycle instead of starting one that cannot finish.
                    try:
                        record_quota_exhaustion(
                            self._usage_file(),
                            now=datetime.now(PACIFIC),
                            limit=int(daily) if daily.isdigit() else FREE_TIER_RPD,
                        )
                    except Exception as exc:  # noqa: BLE001 - never mask the 429
                        log.warning(f"could not reconcile quota state: {exc}")
                    # A per-day quota does not replenish inside a backoff window.
                    # Retrying it burned 277s across 28 sleeps and still failed.
                    raise LLMUnavailable(
                        f"daily free-tier quota exhausted ({daily} requests/day for "
                        f"{self._model}) — resets midnight Pacific. Not retried: a "
                        "per-day quota cannot recover within a cycle. See RUNBOOK §1."
                    )
            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_429_RETRIES:
                # exponential backoff + jitter — NEVER an immediate retry (§3.1)
                delay = min(2**attempt, 30) + random.uniform(0, 1.5)
                log.warning(
                    f"gemini {resp.status_code} — backing off",
                    extra={"data": {"attempt": attempt, "delay_s": round(delay, 1)}},
                )
                self._sleep(delay)
                continue
            if resp.status_code == 429:
                raise LLMUnavailable(
                    "persistent 429 from Gemini — free-tier quota likely exhausted "
                    "(resets midnight Pacific; see RUNBOOK §1)"
                )
            if resp.status_code in RETRYABLE_STATUS:
                raise LLMUnavailable(
                    f"persistent {resp.status_code} from Gemini after "
                    f"{MAX_429_RETRIES} attempts — upstream outage, not a quota problem"
                )
            raise LLMError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
        raise LLMUnavailable("unreachable")  # pragma: no cover

    @staticmethod
    def _split_messages(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, Any]]]:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
            if m["role"] != "system"
        ]
        return "\n\n".join(system_parts), contents

    def complete(self, messages: list[dict[str, str]], schema: type[T], *, agent: str = "") -> T:
        system, contents = self._split_messages(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema.model_json_schema(),
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        last_error = ""
        for attempt in ("first", "repair"):
            payload = self._request(body)
            usage = payload.get("usageMetadata", {})
            _log_usage(
                self._usage_file(),
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "model": self._model,
                    "agent": agent,
                    "prompt_tokens": usage.get("promptTokenCount"),
                    "completion_tokens": usage.get("candidatesTokenCount"),
                    "attempt": attempt,
                },
            )
            try:
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as exc:
                raise LLMError(f"malformed Gemini response: {payload}") from exc
            try:
                return schema.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:500]
                body["contents"] = [
                    *contents,
                    {"role": "model", "parts": [{"text": text[:2000]}]},
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "Your previous response failed schema validation: "
                                    f"{last_error}\nReturn ONLY valid JSON matching the schema."
                                )
                            }
                        ],
                    },
                ]
        raise LLMSchemaError(f"schema-invalid after repair retry: {last_error}")


class OllamaClient:
    """Local-model stub against the same protocol (privacy mitigation, RUNBOOK §1)."""

    def __init__(self, model: str = "llama3.1", base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._url = f"{base_url}/api/chat"
        self._client = httpx.Client(timeout=300)

    def complete(self, messages: list[dict[str, str]], schema: type[T], *, agent: str = "") -> T:
        resp = self._client.post(
            self._url,
            json={
                "model": self._model,
                "messages": messages,
                "stream": False,
                "format": schema.model_json_schema(),
            },
        )
        if resp.status_code != 200:
            raise LLMUnavailable(f"Ollama HTTP {resp.status_code} — is `ollama serve` running?")
        return schema.model_validate(json.loads(resp.json()["message"]["content"]))


class AnthropicClient:
    """Anthropic stub against the same protocol (config-flag swap, §3.1)."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5") -> None:
        if not api_key:
            raise MissingAPIKeyError("ANTHROPIC_API_KEY missing — set it in .env")
        self._key = api_key
        self._model = model
        self._client = httpx.Client(timeout=120)

    def complete(self, messages: list[dict[str, str]], schema: type[T], *, agent: str = "") -> T:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        user_msgs = [m for m in messages if m["role"] != "system"]
        schema_hint = json.dumps(schema.model_json_schema())
        resp = self._client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"},
            json={
                "model": self._model,
                "max_tokens": 4096,
                "system": f"{system}\n\nRespond ONLY with JSON matching this schema: {schema_hint}",
                "messages": user_msgs,
            },
        )
        if resp.status_code != 200:
            raise LLMUnavailable(f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}")
        return schema.model_validate(json.loads(resp.json()["content"][0]["text"]))


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    s = settings or get_settings()
    if s.llm_provider == "gemini":
        return GeminiClient(api_key=s.gemini_api_key, model=s.llm_model)
    if s.llm_provider == "ollama":
        return OllamaClient(model=s.llm_model)
    if s.llm_provider == "anthropic":
        import os

        return AnthropicClient(api_key=os.environ.get("ANTHROPIC_API_KEY", ""), model=s.llm_model)
    raise LLMError(f"unknown LLM_PROVIDER {s.llm_provider!r}")
