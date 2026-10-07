"""Lever 1: an agent with no input does not spend a request.

The free tier allows 20 requests/day for gemini-3.5-flash and a cycle costs 7.
The sentiment analyst was spending one of those every cycle to reason about zero
posts, emitting "[SPECULATION] Crowd lean and retail sentiment are completely
unknown" — which reads like a finding but is the absence of data.

Absence of data and absence of signal are different claims. A skipped agent says
"not collected", never speculates, and costs nothing.
"""

from __future__ import annotations

from atlas.ingest.macro import MacroContext
from atlas.ingest.market import MarketSnapshot
from atlas.ingest.news import NewsDigest
from atlas.ingest.social import SentimentReport
from atlas.research.agents.runners import (
    run_macro_agent,
    run_market_agent,
    run_news_agent,
    run_sentiment_agent,
)


class ExplodingLLM:
    """Any call is a spent request. Calling it at all fails the test."""

    def complete(self, *a: object, **k: object) -> object:
        raise AssertionError("an agent with empty input must not spend a request")


def test_sentiment_with_zero_posts_skips_without_calling_the_llm() -> None:
    view = run_sentiment_agent(SentimentReport(posts_analyzed=0), ExplodingLLM())
    assert view.key_findings == [], "a skip must not manufacture findings"
    assert view.confidence == 0.0


def test_skip_says_not_collected_rather_than_speculating() -> None:
    view = run_sentiment_agent(SentimentReport(posts_analyzed=0), ExplodingLLM())
    text = (view.summary + " ".join(view.anomalies)).lower()
    assert "not collected" in text
    assert "speculation" not in text, "absence of data is not a speculative finding"
    assert "unknown" not in view.summary.lower()


def test_skip_is_logged_to_anomalies_with_its_reason() -> None:
    view = run_sentiment_agent(SentimentReport(posts_analyzed=0), ExplodingLLM())
    assert any("sentiment" in a.lower() and "skip" in a.lower() for a in view.anomalies)


def test_news_with_zero_items_skips_too() -> None:
    """Generalized, not special-cased to sentiment."""
    view = run_news_agent(NewsDigest(items=[]), ExplodingLLM())
    assert view.key_findings == []
    assert "not collected" in view.summary.lower()


def test_macro_with_no_series_skips() -> None:
    """Before FRED_API_KEY landed, macro ran every cycle on an empty series dict."""
    view = run_macro_agent(MacroContext(), ExplodingLLM())
    assert view.key_findings == []
    assert "not collected" in view.summary.lower()


def test_market_agent_with_no_quotes_skips() -> None:
    view = run_market_agent(MarketSnapshot(), ExplodingLLM())
    assert view.key_findings == []


def test_a_populated_agent_still_calls_the_llm() -> None:
    """The gate must open the moment a source is configured — no code change."""
    called: list[str] = []

    class RecordingLLM:
        def complete(self, messages: object, schema: type, **k: object) -> object:
            called.append("yes")
            return schema(agent="sentiment-analyst", summary="real", key_findings=[],
                          confidence=0.5, injection_flags=[], anomalies=[])

    run_sentiment_agent(
        SentimentReport(posts_analyzed=5, ticker_mentions={"NVDA": 2}, bullish_ratio=0.6),
        RecordingLLM(),
    )
    assert called == ["yes"]
