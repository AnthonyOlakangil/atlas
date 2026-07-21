# DECISIONS

Every assumption and deviation, logged as required by BUILD_PROMPT §0/§12.

## Phase P — Preflight (2026-07-18)

- **Python 3.12 via `uv python install 3.12`**, not Homebrew. System python3 is 3.9.6; uv manages toolchains user-space with no sudo and no system changes, which satisfies the "safe, user-space installers" rule more cleanly than `brew install python@3.12`. Node 26, npm 11, git 2.50, brew 6 already present — nothing else installed.

## First live run — bugs found and fixed (2026-07-20)

Ran a real cycle against live Gemini + live prices + the manual portfolio.yaml. It surfaced defects that the offline suite hadn't covered (all prior sizing/critic tests used buys); each is now fixed with a regression test.

- **Model: `gemini-3-flash` (BUILD_PROMPT §3.1) does not exist in the live API — it 404s.** Switched the default to **`gemini-3.5-flash`** (current stable, still free-tier), confirmed working with structured output. Updated settings default, GeminiClient default, `.env.example`, and tests.
- **Critic false-positive on trims/sells.** The `cash_violation` check compared `sized_dollars` against settled cash for *every* approved idea — but for a SELL/TRIM that value is proceeds *received*, not cash *spent*. It rejected a legitimate SMH trim twice. Guarded the check to `action == "BUY"`.
- **Trim overshoot — risk agent clamped buys to bands but not trims.** A TRIM could push a sleeve below its band floor (observed: satellite 28.3% → 3.5%). Now a TRIM is clamped so the sleeve lands no lower than `band_lo`; if the sleeve is already at/below the floor the trim is skipped as a watch item. A full **SELL** (thesis exit) is deliberately *not* band-clamped — it may take a sleeve to zero. Sell shares now round DOWN so a clamp can never be crossed by rounding.
- **Sell proceeds shown as settled cash.** `_portfolio_impact` added proceeds to settled cash immediately, contradicting the T+1 rule and the ticket's own cash-check line. The cash sentence is now action-aware: a sell reports "settled cash $X unchanged (proceeds settle T+1)".
- **LLM-hallucinated retrieval timestamps.** Models stamped synthesized findings with training-era dates (2023). Retrieval time is a fact Atlas knows, so every claim's `retrieved_at` is now overwritten in code with the real cycle retrieval time — the LLM has no say over it.
- **Brief dated in UTC rolled a day ahead every evening Pacific.** The cycle's `as_of` (and thus the brief date/filename) now uses `America/Los_Angeles` per §12.
- **CLI dumped a full traceback** on known operational errors (missing key, gate refusals, bad portfolio.yaml, broker/network failures). A `main()` wrapper now prints a one-line `Error: …` and exits 1 for those; genuine bugs still surface their traceback.

## Change request: BROKER_MODE (2026-07-20)

- **`BROKER_MODE=manual` is the new default; `connected` preserves the full MCP path unchanged** (the pre-flag test suite is pinned to connected in conftest as the proof it stays intact).
- **portfolio.yaml lives at `atlas/config/portfolio.yaml`** — the operator's request said `config/`, and this repo's config dir is `atlas/config/` (operator.yaml, sources.yaml). Gitignored; `portfolio.example.yaml` ships. `cost_basis` is defined as TOTAL dollars paid per lot, matching the internal Position/PositionView semantics.
- **One state interface:** `load_portfolio_state()` dispatches manual (yaml) vs connected (ledger). Sizing/drift/settled-cash logic operate on either unchanged. Manual mode has no order history, so its GFV ledger is empty — the broker enforces GFVs for hand-placed trades; documented in RUNBOOK.
- **Manual holdings get sleeves by explicit `sleeve:` key, else seed-example inference, else `unassigned`** — inference is a labeling default only, never a gate (§2.0 intact).
- **`atlas filled --ticket-id --price` added:** manual mode's bookkeeping counterpart to execution — stamps an APPROVED ticket EXECUTED with the operator's real fill so +1/+5/+20d scoring and the track record still function without a broker link. It respects the approval gate and cannot double-record.
- **Robinhood how-to extracted to `core/howto.py`** and served on each ticket via the API, so the dashboard's manual-mode "place this yourself" panel and the brief can never drift apart.

## Phases 6–9 (2026-07-18/19)

- **Brief = deterministic template + LLM narrative.** The §8 structure (sections, ticket fields, citations, cash arithmetic) is assembled in code from structured data; the LLM writes only the TL;DR/teaching narrative. Structure can't be hallucinated away, and the critic's structural checks are enforceable in code.
- **v1 exit-plan policy:** satellite/speculative buys get app-managed stop (-8%) and target (+20%); core/diversifier holdings are band-rebalanced with no stop (the falsifier defines the exit). Stops propose exit tickets — they never auto-execute; the approval gate holds everywhere.
- **Scheduler lives at `atlas/scheduler.py`** (top level, not in `research/`): the stop monitor needs the execution package, and research may never import it. The CLI wires the entrypoint.
- **Sector Heat treemap colors by unrealized P&L %,** not day change — per-ticker prior closes aren't tracked yet; the caption says so. Day-change coloring is a v2 item.
- **Frontend built in-thread** after the dispatched viz-builder subagent failed on a session limit before creating any files. Same deliverables: 8 views, Vitest suite, dark theme, no localStorage.
- **Interactive-confirm design proven in anger:** with the MCP unauthenticated (401), review-failure surfaces as a warning and blocks non-interactive execution until the operator confirms — observed live during the Phase 8 smoke.

## Phase 3 — Portfolio state (2026-07-18)

- **The single-position cap (25%) applies to single-issuer positions, not diversified funds.** Read literally, a 25% per-ticker cap contradicts the operator's own framework (core sleeve = 50–60% in one broad ETF). Resolution: diversified funds are governed by their sleeve band; single names by the cap. Callers pass `is_diversified_fund`; default is the conservative False.
- **`broad_market` is exempt from the sector-concentration cap** — a total-market fund is not a sector bet; sector caps exist to stop stacking one theme (e.g. semis across sleeves), which is enforced cross-sleeve via a best-effort SECTOR_MAP. Unknown tickers skip the sector check with a review flag rather than being rejected (open universe, §2.0).
- **`CashEvent` model added** (deposits/withdrawals) — the ledger needs cash inflows; spec's model list omitted it. Settled/unsettled cash and GFVs are derived by chronologically replaying deposits + fills (`derive_ledger`), mirroring how live mode will replay MCP order history (§5 gap 5).
- **GFV definition implemented:** a buy funded (even partly) by unsettled sale proceeds, where that position is sold before the funding settles. `would_cause_gfv()` hard-blocks such proposals before they become tickets (§2.1).

## Phase 0 — Foundation (2026-07-18)

- **Architecture rule enforced with a pure-AST pytest check** (`tests/test_architecture.py`) instead of the `import-linter` package. Same guarantee (build fails on `atlas.research`/`atlas.agents` importing `atlas.execution`), one fewer dependency, and the rule lives next to the other tests. CI runs it on every push.
- **GeminiClient implemented over httpx REST** (`generativelanguage.googleapis.com`) rather than the `google-genai` SDK: keeps the provider layer thin, uniform with the Ollama/Anthropic stubs, and mockable with `respx` like every other HTTP source. Swappable per §3.1 either way.
- **Alembic lives at root** (`alembic.ini` + `migrations/`) — a deliberate addition to the §4 tree; migrations are project-level, not package-level.
- **`KNOWN_ISSUES.md` added at root** per §11.5 (not in the §4 tree, but required by the self-verification loop).
- **DB is SQLite at `./atlas.db`** (gitignored). Postgres path: swap the SQLAlchemy URL and rerun Alembic; documented in README, not built (§12 default).
- **Timezone America/Los_Angeles everywhere**, market hours computed in ET and converted (§12 default).
- **Backtesting: out of scope for v1** — forward-tested paper trading only (§12 default).
- **Dashboard auth: none, binds 127.0.0.1 only** (§12 default).
- **Notifications: brief written to `briefs/` + local desktop notification** via `osascript` on macOS (§12 default).
- **Max 3 tickets/day, $10 minimum ticket** (§12 defaults).
- **Options: data + analysis only; no options ticket generation in v1** (§12 default).
