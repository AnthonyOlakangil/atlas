# Atlas

A personal, always-on market research and portfolio management system for a single operator. Atlas runs a research cycle every trading day, produces an institutional-quality brief, generates order tickets sized to the operator's allocation framework, tracks performance against SPY, and executes **operator-approved** orders via the Robinhood Trading MCP.

> **Safety model, up front:**
> - **Approval gate** — Atlas never places an order the operator hasn't explicitly approved. The research pipeline ends at a `PENDING_APPROVAL` ticket; the only path to a live order is the separate `atlas execute --ticket-id <id>` command, which runs `review_equity_order` first.
> - **Paper-first** — Atlas starts in `PAPER` mode. `LIVE` is unreachable until 30 consecutive trading days of paper operation are logged and the track-record report renders.
> - **Quarantine** — all scraped content is data, never instructions. Analysis LLM calls run in a read-only tool context that is structurally incapable of placing orders.
> - Atlas is not a licensed financial advisor. All investment decisions and their consequences are the operator's.

## Setup

```bash
# 1. Toolchain: uv (https://astral.sh/uv), Node >= 18
uv python install 3.12
uv sync                      # installs Python deps into .venv

# 2. Secrets
cp .env.example .env         # then edit .env:
#    GEMINI_API_KEY=...      # free key from https://aistudio.google.com
#    (optional) FRED_API_KEY, REDDIT_*, YOUTUBE_API_KEY

# 3. Database
uv run alembic upgrade head

# 4. Robinhood MCP auth (optional until you want live data)
#    See RUNBOOK.md — OAuth handshake completed in your browser; Atlas never sees your password.

# 5. Frontend
cd frontend && npm install
```

## Daily operation

```bash
uv run atlas cycle           # run the full research cycle now (scheduler runs it 06:30 PT)
uv run atlas brief           # print today's brief path
uv run atlas tickets         # list proposed tickets
uv run atlas execute --ticket-id <id>   # the ONLY way to execute an approved ticket
uv run atlas report --track-record      # hit rate, alpha vs SPY, drawdown
uv run atlas serve           # FastAPI backend on 127.0.0.1:8000
cd frontend && npm run dev   # dashboard on localhost:5173
```

## What's tracked vs. generated

- **Tracked in git:** source, `briefs/` (the operator's record), config, migrations.
- **Generated, gitignored:** `.env`, `atlas.db` (paper book + state), `runs/<date>/` debug logs, caches, `node_modules/`, build output.

## Architecture

```
ingest  →  research (5 parallel analysts + risk)  →  synthesis  →  critic  →  brief + PENDING_APPROVAL tickets
                                                                                        │
                                                     operator approval → atlas execute ─┘ (only path to orders)
```

- `atlas/research/` **cannot import** `atlas/execution/` — enforced by `tests/test_architecture.py` in CI.
- SQLite via SQLAlchemy 2.0. Postgres migration path: change the DB URL in `.env`, rerun Alembic (documented, not built).
- LLM: Gemini free tier by default, behind a provider-agnostic `LLMClient` protocol (Ollama/Anthropic swappable via `LLM_PROVIDER`).

See `RUNBOOK.md` for operations, failure recovery, and the free-tier privacy caveat. See `DECISIONS.md` for every assumption made during the build.
