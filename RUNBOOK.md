# RUNBOOK

Operations manual for Atlas. Written for the operator; assumes the README setup is done.

## 1. Keys and auth

### Gemini API key (required for research cycles)
1. Go to https://aistudio.google.com → "Get API key" → create a free key.
2. Put it in `.env` as `GEMINI_API_KEY=...`. Never paste it into a chat, a commit, or anywhere else.
3. If the key is missing, Atlas fails with a one-line message naming this file — not a stack trace.

**Privacy caveat (read this):** the Gemini **free tier permits Google to use your prompts for model training** and excludes the paid tier's data-privacy guarantee. Atlas sends market analysis *and* portfolio-state context (positions, balances) to the LLM. Mitigation: the LLM layer is provider-agnostic — you can route portfolio-state-heavy calls to a local Ollama model (`LLM_PROVIDER=ollama`) while public-market analysis stays on Gemini. This is a config choice, not a rewrite.

**Quota:** free tier ≈ 10 requests/min and ≈ 1,500/day. Atlas caps itself at ≤ 8/min. The daily quota resets at **midnight Pacific** (not rolling) — heavy debugging days can exhaust it; it comes back at midnight.

### Broker mode: manual (default) vs connected

`BROKER_MODE=manual` (the default) runs Atlas with **no broker link at all**:
- Your holdings live in `atlas/config/portfolio.yaml` (copy `portfolio.example.yaml`; `cost_basis` is TOTAL dollars paid per lot). **Keep it current by hand after every trade** — it is the book Atlas reasons against, and it is gitignored because it holds your real positions.
- Market data comes from yfinance. No OAuth, no account access.
- `atlas execute` is **inert** — it tells you the broker isn't connected and points to the ticket's "Robinhood how-to". You place the trade yourself in the app, then record it so scoring works: `uv run atlas filled --ticket-id <id> --price <your fill>` … and update portfolio.yaml.
- Sizing, sleeve drift, and the settled-cash rule all run on the yaml numbers. GFV tracking is on you and your broker in manual mode — Atlas has no order history to derive it from.

`BROKER_MODE=connected` enables the Robinhood MCP path below.

### Robinhood MCP (connected mode only)
- URL: `https://agent.robinhood.com/mcp/trading`.
- Auth is an **OAuth handshake you complete in your browser** — Atlas never sees or stores your Robinhood password.
- The MCP has **read access to ALL your Robinhood accounts** but **trade access ONLY to the Agentic account** (a separate account from your primary brokerage). Atlas labels which account every figure comes from.

### Reddit (optional, sentiment only)
Create a "script" app at https://www.reddit.com/prefs/apps → put client id/secret in `.env`. Read-only.

## 2. Daily operations

- The scheduler runs the full cycle at **06:30 PT** (pre-open) and a light update at **13:15 PT** (post-close). Run manually anytime: `uv run atlas cycle`.
- Output lands in `briefs/YYYY-MM-DD.md` and the dashboard. Raw per-stage output is in `runs/<date>/` for debugging.
- Approve/reject tickets from the dashboard's Ticket Review view or the CLI. **Approval never fires an order from the browser** — it marks the ticket approved; execution is always `uv run atlas execute --ticket-id <id>`.

## 3. Stops are app-managed — Atlas must be running

Robinhood has **no bracket/OCO orders**. Atlas persists stop/target levels on each ticket and monitors them with a scheduled job, proposing the exit order when a level triggers.

> **⚠️ Stops are only enforced while Atlas is running.** If the machine is asleep or the scheduler is stopped, nothing is watching your stop levels. This is a hard platform limitation, not a bug.

## 4. Failure recovery

| Symptom | Action |
|---|---|
| A source is down / blocking | The cycle degrades gracefully and flags it under Anomalies in the brief. No action needed; check `runs/<date>/`. |
| Gemini 429s persist | The limiter backs off with jitter; if the daily quota is hit, wait for midnight PT reset. |
| `place_equity_order` fails | Never auto-retried. The error is surfaced and the ticket stays `PENDING_APPROVAL`. Re-run `atlas execute` when resolved. |
| DB corrupt / wrong | `atlas.db` is disposable in paper mode: delete it and rerun `uv run alembic upgrade head`. Track record restarts. |
| Kill the agent | `uv run atlas stop` or plain Ctrl-C / `kill` of the scheduler process. Nothing trades while it's down (see §3 caveat about stops). |

## 5. Modes

- `ATLAS_MODE=PAPER` (default): `place_*` calls are intercepted and written to the paper book. No real orders possible.
- `ATLAS_MODE=LIVE`: unreachable until 30 consecutive paper trading days are logged AND `atlas report --track-record` renders. The code checks the trade log; setting the env var early just gets you an explanatory refusal.
