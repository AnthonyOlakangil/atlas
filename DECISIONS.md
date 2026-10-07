# DECISIONS

Every assumption and deviation, logged as required by BUILD_PROMPT §0/§12.

## Task 3 — bounded marketable-limit buffer (2026-08-16)

- **A marketable limit is legitimate slippage protection, not a defect.** `sizing.py` prices buy limits 0.5% above market (`limit_buffer_pct`) so the order fills; 86.58 × 1.005 = $87.01 is exactly the figure the 2026-08-16 brief showed. It fills like a market order but *caps* the worst fill. Rejecting marketable limits outright would reject every whole-share buy Atlas can generate and push the book toward **uncapped** market orders — strictly worse. The operator proposed that rejection rule, then withdrew it on this reasoning.
- **The rule that catches the real failure is a bounded buffer, `MARKETABLE_BUFFER_PCT = 1%`.** At or under 1% through the spread the cap is real protection and passes with disclosure. Beyond 1% the cap protects nothing meaningful and almost certainly reflects a sizing error or a stale quote, so it is rejected (`excessive_marketable_buffer`). Mirrored for sells (limit at or below the bid). The ±10% fat-finger guard remains the outer bound for the **passive** direction only — above market the tighter 1% bound binds first.
- **No `reject_marketable_limit` config flag.** It was deliberately deleted rather than left switched off: a flag that would break every whole-share buy is a footgun, not a feature.
- **Disclosure must not imply price improvement.** The old brief said "only fills at $87.01 or better" while quoting $86.58 — true, but it reads as patience the order does not have. Required substance now: fills immediately at or near the current ask/bid; the limit caps slippage by N%; it does not obtain a better price. Every marketable-limit ticket also carries a §2.1a layman's block, because the market / marketable-limit / passive-limit distinction is exactly the kind of mechanism that section exists to unpack.
- **Held vs non-held degradation split (operator-endorsed).** Task 2.5 asked for both a hard gate on unquoted holdings *and* single-symbol degradation. Those conflict, and the split is by ownership: a **held** position with no usable quote aborts the cycle, a **non-held** universe symbol degrades to a §9 anomaly and the cycle continues. Degrading a holding would reintroduce the silent omission the gate exists to prevent.
- **Pre-cutoff briefs are date-exempt from the number-hygiene scan, not deleted.** `FORMATTER_LANDED = 2026-08-16`; briefs dated earlier were rendered by code that interpolated raw floats and will always carry artifacts. Each is a record of what Atlas actually produced at that stage — deleting history to green a test would destroy the evidence the fix was needed.
- **503 is now retried like 429.** `llm.py` retried only on 429, so a Gemini 503 — whose own body says "Spikes in demand are usually temporary" — killed an agent instantly. That cost market-data and news-analyst on 2026-08-16 and shipped a brief on partial research. `RETRYABLE_STATUS = {429, 500, 502, 503, 504}` with the existing exponential backoff + jitter; persistent 5xx raises `LLMUnavailable` (an outage) rather than the quota-flavoured 429 message. Non-429 4xx is still never retried — it will not succeed.

## Task 2 — GLDM provenance: independent reasoning, not exemplar leakage (2026-08-16)

Verdict recorded per the operator's instruction to note the finding if the reasoning proved independent.

- **The reference brief is never loaded by any code.** `grep -rn "reference-2026\|reference_brief" atlas/ tests/` returns zero hits. `briefs/reference-2026-07-briefing.md` is a file a human reads; it is in no prompt payload, no agent input, and no synthesis step. Leakage from it is structurally impossible, not merely unobserved.
- **GLDM originates upstream**, in `runs/2026-08-16/bundle.json` as a `sector-analyst` trade_idea carrying its own thesis, catalyst, risk, bear case, falsifier and confidence (0.85). It does not first materialise at synthesis.
- **The synthesizer cannot author a position.** Tickets are assembled in code from `assessment.approved` ← risk agent ← `bundle.sector.trade_ideas`; the LLM at synthesis writes only the TL;DR and teaching narrative. There is no path from an exemplar to a ticket.
- **GLDM's real entry points are two declared config sources**, both legitimate: `operator.yaml` diversifier `seed_examples`, injected as `SLEEVES:{...}`, and the then-hardcoded `orchestrator.py:DEFAULT_WATCHLIST` (since replaced by `build_watchlist()`), which caused a live quote to be fetched and injected as `QUOTES:{...}`. The prompt explicitly frames seeds as "STARTING POINTS, not a whitelist" (§2.0).
- **The thesis is grounded in current state**, not copied: "diversifier underweight at 8.8% versus a 10-15% band" is this cycle's computed drift, independently reconfirmed at 8.93% after BRK.B landed.
- **Same test on the other two tickets — both clean.** VOO cites live core drift (19.5% vs 50–60%). **ANET is the strongest evidence of independent reasoning: it appeared in no watchlist, no seed_examples list, and nowhere in the reference brief's ticket content**, so the analyst could only have surfaced it from `CURRENT_PORTFOLIO`. (An earlier draft of this note added that it "had no quote" — that was wrong: `run_cycle` topped up held tickers, so ANET was priced. It had no *indicators*, which is the separate watchlist defect fixed the same day.)

Blast radius: zero. Bug 3 (the limit-above-market ticket) remains real and unfixed, but its cause is structural validation, not contaminated retrieval.

## Wave A — P0 bug fixes (2026-08-16)

- **Day change is a value-weighted per-position price return, never total-vs-total** (`atlas/portfolio/performance.py`). Root cause: [synthesis.py] computed `(total - prev.total_value) / prev.total_value` off the `snapshots` table, which stores `total_value` *including cash* and a `positions` JSON that no consumer ever read. That cannot distinguish a price move from a deposit or a composition change — hence "+94.86%" when portfolio.yaml went from 4 holdings to 12.
  - **Cash flows are excluded structurally, not by adjustment.** Only positions enter the sum, so a deposit cannot appear as return. Manual mode has no deposit ledger (portfolio.yaml simply states `settled_cash`), so flows genuinely cannot be separated at the total level — computing per-position sidesteps the problem instead of approximating it. Time-weighted return was therefore unnecessary.
  - **Share-count changes are immune:** the return is a price ratio weighted by prior value, so buying more of a held name is 0%, not +100%.
  - **Composition mismatch degrades loudly** with the specific added/removed tickers and the prior date.
  - **±25% tripwire** raises `ImplausibleReturnError` and halts the render rather than printing.
  - **Snapshot format changed** to `{ticker: {shares, price, value}}`; `position_map()` is shared by the write site and the diff so they cannot drift. Pre-fix rows degrade loudly as "incompatible format" rather than lying.
  - **Stale snapshots invalidated, not backfilled** — both existing rows stored value-only with no per-position price, so a backfill was impossible. Deleted; the next cycle establishes a clean baseline.
  - **Other windows are contaminated too (reported, not yet fixed):** `viz/charts.py` `performance_series` bases cumulative return on `rows[0]` — the first snapshot ever — and `drawdown_series` shares the shape. The dashboard Performance curve, its SPY comparison, and TrackRecord drawdown all read the same table. Logged in KNOWN_ISSUES.
- **One formatter module** (`atlas/core/format.py`) owns money/percent/shares/bps. Round at the boundary only; every entry coerces through `str()` into `Decimal` so float binary artifacts (`$713.6099853515625`) cannot survive. `ROUND_HALF_UP`, not banker's rounding, so a basis ending in 5 never loses a cent. A nonzero weight below 0.01% renders `<0.01%` so a small real holding is never displayed out of existence.
- **`BRK.B` silently priced at 0 — Yahoo spells class shares `BRK-B`.** Worse than a missing quote: the position stayed in the book, contributed $0.00 to total value and rendered 0.0%, so it was held but invisible in the sleeve table. `atlas/ingest/symbols.py` translates at the single yfinance entry point. Book total corrected $4,379.71 → $4,384.92.
- **BRK.B basis is a Robinhood gift-stock promo credit ($5.10 for 0.010345 sh), not a cash purchase.** Flagged here because it may warrant different tax and track-record treatment than a market fill — the track record scores against cost basis, and a promotional credit is not an investment decision Atlas made.
- **Requirement logged: `portfolio.yaml` load must fail loudly on a holding with no resolvable sleeve.** Today `state.py` falls back to `infer_sleeve()` and then to the string `"unassigned"`, which `sizing.py` rejects as an unknown sleeve — so a mislabelled holding disappears from band math silently. Every concentration warning in every brief would be wrong. Not yet implemented; tracked in KNOWN_ISSUES as the next portfolio-layer change.

## v2 Part 0 — resume + stabilize (2026-08-16)

Defaults taken per BUILD_PROMPT_V2 §8 ("do not ask, pick, log, move on").

- **`ruff check .` replaces `ruff check atlas tests` in CI.** v2 §0.2 lints the whole repo; the narrower CI scope meant `migrations/` had never been linted and carried 4 `I001` import-order errors from Alembic's generated template. Fixed the 4 files (import reordering only, no behavior change) and widened CI so new revisions can't regress.
- **ESLint configured for the first time.** v2 §6 requires `eslint` clean, but the frontend had no config, no devDependency, and no CI step — `npx eslint .` had never been runnable. Added flat config (`eslint.config.js`: `@eslint/js` + `typescript-eslint` + `react-hooks` + `react-refresh`), a `lint` script pinned to `--max-warnings 0`, and a CI step.
- **`annotate()` extracted from `views/BriefReader.tsx` to `lib/annotate.tsx`.** It was the sole `react-refresh/only-export-components` warning (a component file exporting a non-component). It is also the term-wrapping primitive §2.4's Legend sidebar builds on, so it belongs in `lib/` regardless. Existing test coverage moved with it.
- **`BUILD_PROMPT(2).md` renamed `BUILD_PROMPT_V2.md`** and added to `EXPECTED_ROOT` in `tests/test_repo_hygiene.py`. The parenthesised name was a browser-download artifact; the clean-root hygiene test correctly rejected an unlisted root file.
- **Part 0.3 Test A hardened to byte-identical serialization.** `test_offline_bundle_valid_and_deterministic` compared `model_dump()` dicts; equal dicts can still serialize differently (float repr, key ordering). Now also asserts `model_dump_json()` equality, which is the v2 §0.3 bar. Passes.
- **Every `portfolio.yaml` holding carries an explicit `sleeve:` key.** Seed-example inference in `state.py` only matches tickers listed in `operator.yaml`, which would leave DIA, ANET, NLR, NFLX and F `unassigned` — and `sizing.py` rejects an unknown sleeve, silently dropping 5 of 12 holdings from all band math.
- **Operator answers to the three Part 7 open questions (2026-08-16):** (1) **FRED** — operator is registering a free FRED API key themselves; `FRED_API_KEY` goes in `.env`, never into chat or a committed file. Macro ingestion is built against it with a graceful-degradation path so the section renders "unavailable — FRED not configured" until the key lands. (2) **BRK.B is held, 0.010345 shares** — added to the book; **total cost basis for the lot is still outstanding** and BRK.B is deliberately *not* in `portfolio.yaml` until the operator supplies it, because guessing a basis would violate §2.1 rule 8 and produce a false P&L. (3) **`settled_cash: 2001.04` is confirmed** against actual Robinhood buying power — no discrepancy, no unreconciled flag needed in §9.
- **NFLX and F assigned to `diversifier`.** The four-sleeve taxonomy has no home for a single-name non-AI equity: neither is AI-capex (so not `satellite_ai_semis`), neither is broad-market ballast (not `core`), and neither is a can-fully-lose bet (not `speculative`). `diversifier` — "offset tech concentration" — is the least-wrong fit for both. Flagged to the operator; a fifth sleeve may be the real answer in v2.

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
