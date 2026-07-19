# DECISIONS

Every assumption and deviation, logged as required by BUILD_PROMPT §0/§12.

## Phase P — Preflight (2026-07-18)

- **Python 3.12 via `uv python install 3.12`**, not Homebrew. System python3 is 3.9.6; uv manages toolchains user-space with no sudo and no system changes, which satisfies the "safe, user-space installers" rule more cleanly than `brew install python@3.12`. Node 26, npm 11, git 2.50, brew 6 already present — nothing else installed.

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
