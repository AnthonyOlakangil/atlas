# DOGFOOD — 5 consecutive real daily cycles (Phase 10)

Phase 10 requires five *real* trading days with live keys, so it runs on the calendar, not in a build session. This file logs each day; the template is at the bottom.

## Prerequisites (operator, one-time — see RUNBOOK §1)
1. `GEMINI_API_KEY` in `.env` (aistudio.google.com, free).
2. Robinhood MCP OAuth completed in browser; token in `.env` as `RH_MCP_ACCESS_TOKEN`.
3. Start the loop: `uv run atlas schedule` (or run `uv run atlas cycle` manually each morning).

## Day 0 — 2026-07-19 (build-day dry run, FakeLLM — does not count toward the 5)
- Full cycle ran against **live market data** (yfinance quotes; MCP unauthenticated) with the offline FakeLLM narrative.
- Brief `briefs/2026-07-19.md` generated, critic PASS round 1. Two tickets proposed (VOO $140 core, GLDM $30 diversifier); QQQM idea was sized against live prices.
- Ticket #1 approved + executed through the gate: VOO 0.2049 sh @ $683.17 = $139.98, T+1 settles 2026-07-20. The unauthenticated MCP review correctly surfaced a warning and demanded interactive confirmation before proceeding.
- What broke: nothing blocking. Noted: brief still shows fixture-flavored TL;DR until the real Gemini key lands (expected).

---

## Template (copy per real day)

### Day N — YYYY-MM-DD
- **Cycle ran:** (time, preopen/postclose, any job failures in `runs/<date>/`)
- **Brief quality vs reference bar:** (specific gaps: citations, teaching, portfolio-awareness)
- **Critic:** (pass/reject, rounds, what it caught)
- **Tickets:** (proposed/approved/rejected/executed, and why)
- **What broke:**
- **What was fixed (commit):**
