# CLAUDE CODE — ATLAS v2: RESUME, STABILIZE, THEN REBUILD THE BRIEF ENGINE

Paste everything below into Claude Code from the repo root.

---

## PART 0 — RESUME PROTOCOL (DO THIS FIRST — DO NOT SKIP TO PART 2)

You worked on this repo before. You do not remember it. The operator does not remember exactly where you stopped either. **Reconstruct state from the repo, not from assumption.** Do not write a single line of new feature code until Part 0 is fully green.

### 0.1 Reconstruct

Run and report, in this order:

```bash
git log --oneline -40
git status
git branch -a
cat DECISIONS.md
cat KNOWN_ISSUES.md
cat DOGFOOD.md 2>/dev/null || echo "NO DOGFOOD.md — Phase 10 never ran"
ls -la briefs/ runs/ 2>/dev/null
ls -la atlas/config/
```

Then produce a **STATE REPORT** at the top of your response containing:

- Last commit, date, and what it did
- Highest build phase with a **verified passing gate** (do not trust a checkbox — verify the gate's test actually exists and passes)
- Every item in `KNOWN_ISSUES.md` with severity, plus anything you find that isn't logged
- Whether Phase 10 (dogfood, 5 consecutive real briefs) ever ran
- Any uncommitted work-in-progress and whether it's salvageable or should be reverted
- `BROKER_MODE` currently set to `manual` or `connected`

### 0.2 Stabilize

```bash
uv run pytest -v
npm test
ruff check .
mypy atlas/execution --strict
npx eslint .
```

Rules:

- **Red suite = stop.** Fix before proceeding. No new features on a red suite.
- Every fix follows the debug protocol: reproduce in a failing test → fix → confirm green. No fix without a regression test.
- If a test is failing because it is a *bad test*, say so explicitly and justify before changing it. Do not delete tests to reach green.
- Clear or explicitly re-triage every P0/P1 in `KNOWN_ISSUES.md`. P2s may stay logged with a one-line rationale.

### 0.3 Prove it works end-to-end (two live-fire tests)

Before building anything new, demonstrate the existing pipeline still functions:

**Test A — offline determinism.** Run the full cycle against cached fixtures using `FakeLLMClient` + `FakeMCPClient`. Same input must produce byte-identical `ResearchBundle` twice. Report pass/fail.

**Test B — live manual-mode brief.** With `BROKER_MODE=manual`, run one real cycle against the real `portfolio.yaml` (updated per Part 7) and the real Gemini key. Generate an actual brief into `briefs/`. Then run the `critic` against it. Report: the critic's verdict, wall-clock runtime, token count, and every source that failed or degraded.

**If Test B produces a brief the critic rejects, that is your first bug.** Fix it. The v2 spec in Part 2 is worthless bolted onto a pipeline that can't ship a v1 brief.

Post a **GO/NO-GO** line before continuing. If NO-GO, stop and report what's blocking.

---

## PART 1 — WHAT WE'RE BUILDING AND WHY

The operator has been getting market briefs manually in chat for a month. Those briefs are now the real quality bar — higher and more specific than what the current synthesizer produces. The reference file `briefs/reference-2026-07-briefing.md` remains the gold standard for *structure and depth*. Part 2 below adds what the manual briefs proved matters and the reference file under-specifies: **exact numbers, exact dates, exact times, retrospective accountability, and a forward calendar that goes out fourteen days with nothing hand-waved.**

The failure mode you are engineering against is not "the brief is wrong." It is **"the brief is vague."** A brief that says "semis were weak last week" is a failure. A brief that says "SOX closed Friday down 20% from its June 11 peak, with SMH −3.1% on the week against SPY +1.4%" is the product.

Second failure mode: **the brief asserting portfolio state that isn't true.** In past sessions the operator had to correct a brief that described an already-filled ANET order as still pending, and another that listed BITB as a holding when it was only planned. Position state comes from `portfolio.yaml` and the fills ledger. Nothing else. If a ticket was proposed but never stamped via `atlas filled`, it is **proposed, not held** — render it in a separate "Proposed / Not Executed" block and never fold it into holdings math.

---

## PART 2 — THE BRIEF v2 SPECIFICATION

Replace the current synthesizer output contract with this. Every section is required. A section with nothing to report says so explicitly — it is never silently dropped.

### 2.1 Global rules for every number in the brief

These are enforced by the critic, not by good intentions.

1. **No naked adjectives.** "Semis rallied" is rejected. "SMH +2.8% to $573.60" is accepted. Any directional claim carries the magnitude.
2. **Every price carries a timestamp and a staleness flag.** Format: `$573.60 (close 08/15)` or `$573.60 (delayed 15m, 09:42 PT)`.
3. **Every event carries a date, and a time when the time matters.** "CPI Wednesday" is rejected. "July CPI — Wed Aug 12, 05:30 PT release" is accepted. Earnings always carry BMO/AMC.
4. **Every claim carries a source and a tag:** `[FACT]` / `[CONSENSUS]` / `[SPECULATION]`. Consensus figures name whose consensus and as-of when.
5. **Every percentage move states its window and its base.** "+15%" is rejected. "+15% from the $166.00 fill (Jul 25) to $188.67 (close 08/11)" is accepted.
6. **Adjusted vs. GAAP is always disambiguated.** If a company reports adjusted EBITDA/EPS, label it adjusted and give the GAAP figure alongside, or state explicitly that GAAP wasn't retrieved. The operator got burned on this with MP Materials — Robinhood surfaces adjusted by default and the gap matters.
7. **Confidence is numeric when low.** Not "unclear" — "low confidence: 2 sources, both secondary, no primary filing."
8. **Never round away the operator's actual fills.** Share counts to 6dp, cost basis to the cent.

### 2.1a The layman's-terms rule

Atlas is written for an operator who is **actively building financial literacy while managing real money.** Density and precision are non-negotiable — but density that can't be parsed is worthless. Every dense passage gets unpacked.

**The mechanic.** After any block containing jargon, a multi-step causal chain, or a number whose significance isn't self-evident, insert a plain-language unpacking block:

> **So in layman's terms:** ...

Rendered as a visually distinct callout (indent + left border in the dashboard, blockquote in the markdown), never as an inline aside.

**Frequency.** At minimum:

- One per §2 (Since Last Brief), unpacking the day's dominant move
- One per §5 Key Finding that involves a transmission mechanism — how a rate move reaches a stock price, how a tariff reaches a margin, how capex guidance reaches a supplier's revenue
- One per proposed ticket in §7, restating the thesis with no jargon at all
- One per §6 "What to watch for" item
- Anywhere the operator would reasonably have to re-read a sentence

Roughly six to twelve per brief. Under four means the brief is under-explaining; over twenty means the primary prose is too dense and should be rewritten rather than annotated.

**Style rules for these blocks:**

- Two to four sentences. Never longer.
- Zero jargon inside the block. If a term is unavoidable, it isn't a layman's block — fix the sentence above it instead.
- **Explain the mechanism, not just the outcome.** Not "rates went up so tech fell." Instead: "Higher rates mean a dollar of profit ten years from now is worth less today. Companies like NVDA are valued mostly on profits that far out, so they take the biggest hit. Banks like the ones in XLF actually earn more when rates rise, which is why XLF held up."
- **Tie it to this book, by ticker, with the operator's real position.** Abstract examples are rejected.
- Analogies are welcome. Condescension is not. The operator understands trailing vs. forward P/E, sandbagging, funding rates, and GAAP reconciliation — do not re-explain those. Explain the *new* thing.
- Never soften the conclusion to make it friendlier. If the read is "your semis sleeve is 12 points over band and that's the single biggest risk in the book," the layman's block says exactly that in simpler words.

**Critic enforcement:** reject any brief with fewer than four layman's blocks, any block containing an undefined abbreviation, any block that restates the outcome without the mechanism, and any block that runs past four sentences.

### 2.1b Advisor posture

Atlas produces advisor-grade output: it takes positions, ranks options, and states what it would do. It does not hedge every sentence into uselessness. Specifically:

- **Lead with the recommendation, then justify it.** "Trim SMH by $60" before the paragraph explaining why.
- **Rank explicitly when there are multiple options.** Best to worst, with the deciding factor named.
- **Quantify the downside every time.** Every recommendation carries what the operator loses if it's wrong, in dollars against the actual position size.
- **Say "I don't know" numerically**, not vaguely — "low confidence, 2 secondary sources, no primary filing."
- **Correct prior positions out loud** when new evidence arrives. §3 exists for this. A prior BITB-correlation claim was wrong and got corrected in-session; that's the standard.
- **"Do nothing" is a real recommendation** and should appear often. An advisor who recommends action daily is selling activity, not judgment.
- The §13 disclosure stands. Advisor-grade output is not licensed advice, and the brief says so every time.

### 2.2 Section order and contents

**§1 — TL;DR**
Three to five bullets. Each bullet: what changed → what it means for *this* book → what to do (or explicitly, "no action"). Written last, after everything else.

**§2 — Since Last Brief: What Actually Happened**
Retrospective, covering the gap since the previous brief (one day, or the full weekend on a Monday). This section is new and is the highest-value addition.

- **Tape recap** — SPY/QQQ/DIA/SOXX/RUT close, point and % change, plus the week's range if this is a Monday brief. Flag record closes explicitly with the prior record and its date. Note streaks ("best week since Apr 17").
- **Rates, vol, commodities, crypto** — 10Y and 2Y yields with bp change, curve spread, VIX level and change, WTI/Brent, gold, BTC/ETH. BTC gets ETF net flow and the Fear & Greed reading when available.
- **Every holding, individually** — ticker, close, day %, week %, position P&L vs. actual cost basis in both $ and %. No holding is skipped because it was quiet; "F: $14.31, +0.4% d/d, +0.9% vs. $14.16 avg cost — no news" is a valid line and the operator wants it.
- **Events that resolved** — earnings that printed, macro data that released, Fed speakers, policy actions. Give the number, the consensus it was measured against, and the market's reaction, not just the headline.

**§3 — Correction Ledger**
Everything the prior brief got wrong, stated plainly. Compare yesterday's brief against what actually happened:

- Predictions made and how they resolved
- Any factual error caught (by the critic, by the operator, or by you on re-read)
- Any thesis that new information now contradicts

If the prior brief was clean, say "No corrections." Never quietly drop a busted call. The operator has caught reasoning errors before — for example, two CPI scenarios that both assumed a jobs number that had already been released and was therefore fixed. That class of error is what this section exists to surface.

**§4 — Portfolio Status**

- Total value, settled cash, unsettled cash with the T+1 release date
- Day / week / MTD / YTD change in $ and %, each vs. SPY over the identical window
- **Sleeve table:** sleeve, current %, target band, drift, in-band Y/N. The AI/semis sleeve has run above its 20–25% band for most of this portfolio's life — when it's over, the brief states by how much and what specifically would bring it back, every single day, without softening it.
- **Look-through concentration:** the true AI-capex exposure across SMH, NVDA, AVGO, MU, ANET, and the AI-weighted portion of VOO. Report as a single number. Any proposed add must state its effect on it.
- **Proposed / Not Executed** — tickets proposed but never stamped via `atlas filled`. Explicitly excluded from all holdings math.

**§5 — Key Findings**
Numbered, each tagged and sourced with a timestamp. Must cover, every day:

1. Index levels and valuation context (forward P/E, CAPE, earnings growth tracking)
2. Fed and macro — current target range, the exact dissent count and names, market-implied odds for the next two meetings with the source of the odds, and the next three dated data releases
3. Each sector the operator holds — semis, financials, healthcare, nuclear/uranium, autos, streaming, materials
4. Anything with a live dated catalyst inside 14 days
5. Policy and regulatory items, subject to the two-lean sourcing rule in §7.2 of the original build spec
6. Anything trending in social/retail channels that touches a holding — **with the mechanism verified.** The operator brought a viral rare-earths clip and an AXON claim; in both cases the real value was checking whether the mechanism was actually what the narrative claimed. It usually isn't. Report the claim, the verified mechanism, and the gap between them.

**§6 — The Week Ahead / Next 14 Days**
A dated table, not prose. One row per event:

| Date | Time (PT) | Event | Type | Relevance to book | Positions affected |
|---|---|---|---|---|---|

Populate from:

- **Earnings** — every holding, plus every major name whose print moves a holding's sector. BMO/AMC marked. Include consensus EPS/revenue and the implied move from options when retrievable.
- **Macro** — CPI, PPI, PCE, NFP, jobless claims, retail sales, GDP, ISM, consumer sentiment. Exact release times.
- **Fed** — FOMC dates, minutes releases, Powell/Warsh/governor speeches, Jackson Hole and similar.
- **Structural** — OpEx, triple witching, quarterly index rebalances, ETF reconstitutions, Treasury auctions, T+1 settlement dates for the operator's own pending cash.
- **Company-specific** — investor days, product launches, lockup expirations, dividend ex-dates and pay dates for holdings (F and VOO both pay; DRIP is on for VOO), analyst days, court dates and regulatory deadlines.

Then a short prose block: **"What to watch for"** — for the two or three highest-impact events, state the specific observable that would change the portfolio stance, and what the response would be. Not "watch CPI" — "if core CPI prints above 0.4% m/m, September hike odds move through 70% and the case for adding to XLF strengthens while the semis sleeve gets a second reason to be trimmed."

**§7 — Today's Proposed Trades**
Keep the existing per-ticket contract from the original build spec in full (thesis, catalyst, risk, bear case, falsifier, horizon, sizing rationale, exit plan, Robinhood how-to, learning note, portfolio impact). Add two required fields:

- **Sleeve delta** — the exact before/after sleeve percentages this trade produces
- **Correlation note** — what this position is already correlated with in the book. GOOG and AMZN, currently on the operator's shortlist, deepen AI-capex exposure rather than diversifying it. Any ticket for either must say so in this field, unprompted.

**The hard rule stands and gets stricter:** if there's no good setup, the brief says "No trades today" and explains why. Atlas is not rewarded for activity. "Hold and do nothing" is frequently the correct output. The critic rejects any brief that proposes a trade whose stated catalyst is more than 14 days out with no time-sensitivity argument.

**§8 — Robinhood Execution Notes**
Platform mechanics that constrain the day's proposals:

- Fractional shares are **market orders only**; limit orders require whole shares. A limit-order recommendation on a sub-$100 position the operator can only afford fractionally is an invalid ticket — catch it at generation, not review.
- PDT rule status and remaining day-trade count at current account size
- Settled vs. unsettled cash and what's actually spendable today
- GFV risk on any proposed sell of recently-purchased shares

**§9 — Anomalies & Data Health**
Injection flags, failed or degraded sources, stale caches with age, contradictions between sources, and any figure that couldn't be verified against a primary source.

**§10 — Track Record**
Rolling hit rate at +1/+5/+20d, alpha vs. SPY, paper-book P&L, and the current open-ticket list with days held.

**§11 — Teaching Corner**
One or two concepts, drawn from what actually appears in today's brief, explained in three sentences or fewer, **always using the operator's real positions as the example.** Terms already taught (tracked in the DB) are not re-taught; they get a hover-tooltip definition instead. Already covered: expense ratio, beta, bid-ask spread, limit vs. market, GTC, DCA, core-satellite, PDT, capex, hyperscaler, hawkish/dovish, margin, shorting, options basics, futures, perpetuals, funding rate, depeg, guidance, trailing vs. forward P/E, sandbagging, EBITDA, GAAP vs. adjusted, PPA, NdPr, DRIP, T+1 settlement.

**§12 — Legend**
Every abbreviation, acronym, ticker, and term of art appearing anywhere in today's brief — not only new ones. In markdown output this renders as a sorted table at the end; in the dashboard it becomes the persistent sidebar specified in §2.4. Three columns: term, expansion, one-line plain-English definition. Tickers additionally carry the operator's current position or "not held / watchlist."

The rule is total coverage. If `SOX`, `BMO`, `bp`, `m/m`, `NdPr`, `OpEx`, `GFV`, or `T+1` appears in the brief, it appears in the legend — even if the operator has seen it fifty times. The legend is a reference surface, not a teaching queue; §11 handles what's new.

**§13 — Disclosure**
Atlas is not a licensed financial advisor; this is research output, not advice.

### 2.4 The Legend sidebar (dashboard)

The Brief Reader gets a persistent right-hand sidebar. This is a first-class feature, not a nice-to-have.

**Behavior:**

- **Always visible** on desktop, docked right, independently scrollable, collapsible to a thin rail. Below ~1100px it becomes a bottom sheet triggered by a floating button.
- **Scoped to the current brief by default**, with a toggle for the full corpus dictionary across every brief ever generated.
- **Search box at the top.** Fuzzy, case-insensitive, matches term, expansion, and definition body. Filters live as the operator types. Keyboard shortcut `/` focuses it; `Esc` clears.
- **Grouped and collapsible** by category: Tickers & Funds, Macro & Fed, Accounting & Filings, Market Structure, Order Mechanics, Options & Derivatives, Crypto, Units & Notation.
- **Bidirectional linking.** Clicking a legend entry scrolls the brief to and highlights the first occurrence of that term. Clicking a term in the brief scrolls the sidebar to and highlights that entry.
- **Sorting toggle:** alphabetical, or by first appearance in the brief.

**Hover definitions in the brief body:**

- Every term present in the dictionary is auto-wrapped at render time and marked with a subtle dotted underline. **Underlines only — no color change, no bold.** Green and red are reserved strictly for performance and must never be spent on typography.
- Hover (desktop) or tap (touch) opens a tooltip: term, expansion, one-line definition, and a "more" link that expands the sidebar entry.
- Tooltips are keyboard-accessible via focus, dismiss on `Esc`, and never obscure the number the operator is reading — flip position when near a viewport edge.
- Terms already marked as known still get the underline and tooltip. Reference is always available; the teaching queue is separate.
- **Match on longest span first** so `forward P/E` wins over `P/E`, and never wrap a term inside a code block, a table header, or an already-wrapped span.

**Dictionary source:** a `Term` table — term, aliases, expansion, plain definition, category, first-seen date, times-seen count, `known` flag. Seeded with everything already covered in prior sessions (listed in §11) plus full standard market vocabulary. Extended automatically: at synthesis time, extract every acronym, all-caps token, and known term of art from the brief, diff against the table, and **fail the render if any extracted term has no dictionary entry.** A brief containing an undefined abbreviation does not ship. The critic enforces this too.

Definitions are written to the same standard as the layman's blocks: plain, mechanism-first, one line, no jargon inside the definition itself.

### 2.3 Style

Direct, dense, structured. No filler, no throat-clearing, no closing pleasantries. Tables over prose wherever the content is tabular. Show arithmetic on every sizing and return calculation. Bottom line first in each section. Never bury the number in a sentence — lead with it.

---

## PART 3 — WHAT THE DATA LAYER MUST SUPPORT

Part 2 is unachievable on the current ingestion layer. Audit and extend:

1. **Earnings calendar** — forward 14 days minimum, per-ticker, with BMO/AMC, consensus EPS and revenue, and options-implied move where available. Must cover holdings plus a configurable sector-peer set.
2. **Macro calendar** — the full release schedule with exact times, consensus, and prior. Not just "CPI is Wednesday."
3. **Fed data** — current target range, dissent record from the last statement, and market-implied probabilities for the next two meetings with the source named.
4. **Corporate actions** — ex-dividend and pay dates for every holding, splits, index adds/drops.
5. **Structural calendar** — OpEx, witching, rebalances, auctions. Mostly derivable from a rules engine rather than scraped; build it that way.
6. **Per-holding cost basis and fill history** — from `portfolio.yaml` plus the fills ledger, so §2 can report P&L against real basis rather than an estimate.
7. **Prior-brief store** — §3 (Correction Ledger) requires reading yesterday's brief and its predictions. If predictions aren't currently persisted as structured records, add a `Prediction` table: ticket/claim, made-on date, resolution date, resolved outcome, scored Y/N.
8. **Term dictionary** — the `Term` table specified in §2.4, powering §11, §12, the sidebar, and hover tooltips. A tracking table may already exist; if so, migrate and extend it rather than duplicating. Seed it fully before Phase 12 — the render-fails-on-undefined-term rule is unusable against an empty dictionary. Ship the seed as a versioned data file, not an ad-hoc migration.

Every new source: rate-limited, cached at the tier already defined (24h news, 7d reference, 5m quotes), `respx`-mocked in tests, with a graceful-degradation test. A missing calendar source degrades the section to "unavailable — source X failed at HH:MM"; it never silently produces an empty week.

---

## PART 4 — SUBAGENT PLAN

Run these in parallel where dependencies allow. Each returns structured output; you integrate.

| Subagent | Scope | Deliverable |
|---|---|---|
| `repo-archaeologist` | Part 0.1 only | STATE REPORT, issue triage, GO/NO-GO recommendation |
| `test-medic` | Part 0.2 | Green suite, regression test per fix, updated `KNOWN_ISSUES.md` |
| `calendar-engineer` | Part 3 items 1–5 | Calendar ingestion modules + tests + degradation tests |
| `portfolio-engineer` | Part 3 items 6–7, Part 7 | Cost-basis reporting, `Prediction` table, migration, sleeve/look-through math |
| `synthesizer-v2` | Part 2 | New brief template, output contract, section renderers, layman's-block generation |
| `lexicographer` | §2.4 dictionary | Seeded `Term` table, category taxonomy, extraction pass, undefined-term render gate |
| `critic` | Part 2 enforcement | Extended rejection rules per §2.1, §2.1a, §2.4; must catch ≥5 seeded defects in a deliberately vague brief |
| `dashboard-engineer` | Brief Reader, Legend sidebar, Calendar view | Vitest green, renders v2 brief with all sections, working search + hover tooltips |
| `qa-engineer` | After every phase | Adversarial review; findings logged, not waved off |

**Constraint that does not bend:** any subagent touching untrusted ingested content gets a read-only tool registry and cannot reach `execution/`. Enforce structurally in code, verified by the existing `test_architecture.py` import-linter rules. Extend those rules to cover any new module.

---

## PART 5 — PHASES

Each phase: passing tests → `qa-engineer` review → demo artifact → `DECISIONS.md` entry. Do not advance on red.

**Phase 11 — Data layer.** Part 3 in full. Gate: each new source has a passing integration test and a graceful-degradation test; a calendar query for the next 14 days returns dated, timed, populated rows for the operator's actual holdings.

**Phase 12 — Brief v2.** Part 2 in full, including the layman's-terms rule (§2.1a) and the `Term` dictionary with its extraction pass (§2.4). Gate: generate a v2 brief from cached fixtures with every section populated and ≥4 layman's blocks; every extracted term resolves to a dictionary entry; the extended critic passes it; the critic rejects a deliberately vague variant on at least 5 seeded defects, one of which is an undefined abbreviation and one of which is a layman's block that states an outcome without its mechanism.

**Phase 13 — Correction ledger.** `Prediction` persistence and scoring. Gate: replay 10 days of fixture briefs and produce a correct correction ledger on day 10 that catches ≥2 seeded busted predictions.

**Phase 14 — Dashboard.** Brief Reader renders all v2 sections as collapsible cards; Legend sidebar per §2.4; new Calendar view. Gate: Vitest green, renders against a real generated brief, and specifically: sidebar search filters live, clicking a legend entry scrolls and highlights the term in the brief, hovering a term in the brief opens its tooltip, every underlined term resolves, and the layout holds at 1440px, 1100px, and 390px widths.

**Phase 15 — Dogfood v2.** Five consecutive real trading-day cycles. Read every brief critically. Gate: 5 briefs in `briefs/`, each critic-passed, plus `DOGFOOD_V2.md` recording what broke and what you fixed. **Also record wall-clock runtime and Gemini token/request count per cycle against the free-tier ceiling** — if a v2 cycle exceeds the daily quota, report it and propose cuts rather than silently degrading.

---

## PART 6 — DEFINITION OF DONE

- [ ] `uv run pytest` and `npm test` green; `ruff`, `mypy --strict` on `execution/`, `eslint` clean
- [ ] `KNOWN_ISSUES.md` has zero P0/P1
- [ ] Five critic-passed v2 briefs in `briefs/`
- [ ] `DOGFOOD_V2.md` written
- [ ] `DECISIONS.md` updated for every default taken
- [ ] `RUNBOOK.md` updated with the new sources, their failure modes, and the v2 cycle runtime
- [ ] Architecture tests prove research/ still cannot reach execution/
- [ ] Every shipped brief carries ≥4 layman's blocks and zero undefined abbreviations
- [ ] Legend sidebar renders, searches, and cross-links against a real brief on desktop and mobile widths
- [ ] `portfolio.yaml` matches Part 7 and the brief's holdings math reconciles against it to the cent

---

## PART 7 — CURRENT PORTFOLIO STATE

Replace `atlas/config/portfolio.yaml` with this. `cost_basis` is total dollars for the lot, not per share. Gitignored — do not commit.

```yaml
holdings:
  # Core
  - {ticker: VOO,  shares: 1.049410,  cost_basis: 719.39}
  - {ticker: DIA,  shares: 0.194089,  cost_basis: 100.00}

  # AI / Semis
  - {ticker: SMH,  shares: 0.477360,  cost_basis: 272.50}
  - {ticker: NVDA, shares: 0.366158,  cost_basis: 75.00}
  - {ticker: ANET, shares: 2.000000,  cost_basis: 330.00}
  - {ticker: AVGO, shares: 0.466046,  cost_basis: 184.00}
  - {ticker: MU,   shares: 0.201043,  cost_basis: 184.96}

  # Diversifiers
  - {ticker: XLV,  shares: 0.200852,  cost_basis: 32.50}
  - {ticker: XLF,  shares: 1.692047,  cost_basis: 95.00}
  - {ticker: NLR,  shares: 0.454917,  cost_basis: 50.00}

  # Other
  - {ticker: NFLX, shares: 0.709421,  cost_basis: 50.00}
  - {ticker: F,    shares: 10.105932, cost_basis: 151.50}

settled_cash: 2001.04
unsettled_cash: 0.00
```

**Three things to resolve with the operator before Phase 15, not silently:**

1. **BRK.B is missing.** A prior session recorded ~0.010345 shares. It does not appear in the recent transaction log. Ask; do not guess a number into the file.
2. **`settled_cash` is computed, not observed.** Derived as $2,000 baseline + $2,000 deposit − $1,998.96 in buys. Reconcile against actual Robinhood buying power on first run and flag any discrepancy loudly.
3. **Planned but not held:** GOOG, AMZN, SGOV, BITB, additional XLF and BRK.B. These live in a watchlist, never in holdings. BITB was incorrectly treated as a holding in a past brief — do not repeat that.

---

## PART 8 — DECISION DEFAULTS

Do not ask. Pick, log in `DECISIONS.md`, move on.

| Question | Default |
|---|---|
| LLM | `gemini-3-flash` for all passes; provider swappable via config flag |
| Missing calendar data | Degrade the section with a named-source failure notice; never fabricate, never leave blank |
| Brief length | Whatever completeness requires. Density is the constraint, not word count. |
| Conflicting sources | Report both, name both, state which is primary, do not adjudicate |
| Brief on a market holiday | Generate a shortened version: calendar + portfolio status only |
| Weekend brief | Monday's brief covers the full weekend gap in §2 |
| New API key needed | **Pause and ask.** Never into chat, never hardcoded, never a placeholder treated as real. |

---

## PART 9 — HOW TO REPORT BACK

After Part 0, post the STATE REPORT and the GO/NO-GO, then continue autonomously. After each phase, post: what shipped, test counts before/after, what `qa-engineer` found, what you deferred and why. At the end, post one v2 brief in full so the operator can judge the output directly.

Do not declare a phase complete on a red suite. Do not ship a critic-rejected brief. Log every unresolved issue with severity rather than swallowing it.
