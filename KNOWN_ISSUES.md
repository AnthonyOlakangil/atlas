# KNOWN ISSUES

Unresolved issues, honestly logged (BUILD_PROMPT §11.5). Severity: `high` / `medium` / `low`.

- **[medium] Live MCP smoke test not yet run.** `tests/test_mcp_live.py` needs the operator to complete the Robinhood MCP OAuth handshake in a browser and export `RH_MCP_ACCESS_TOKEN` (RUNBOOK §1). The offline suite fully covers the wrapper; the live fetch is the remaining Phase 2 gate item. Run: `RH_MCP_ACCESS_TOKEN=... uv run pytest -m live tests/test_mcp_live.py`.
- **[medium] Live Gemini smoke test not yet run.** `tests/test_llm_live.py` needs `GEMINI_API_KEY` in `.env` (operator-entered only — RUNBOOK §1). Until then, cycles run with the offline FakeLLM narrative. Run: `uv run pytest -m live tests/test_llm_live.py`.
- **[medium] Phase 10 dogfood pending.** Five real trading days with live keys; log per day in `DOGFOOD.md`. Cannot be compressed into a build session.
- **[low] Reddit/YouTube sentiment sources need operator credentials** (PRAW script app, YouTube channel whitelist). The sentiment agent degrades gracefully (empty report) until then.
- **[low] Sector Heat colors by unrealized P&L %, not day change** — needs per-ticker prior-close tracking (v2; captioned honestly in the UI).
- **[low] `_live_price_lookup` in the scheduler fetches per-ticker history from yfinance daily** — fine at this portfolio size; batch if the book grows past ~20 names.
