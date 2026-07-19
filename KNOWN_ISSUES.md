# KNOWN ISSUES

Unresolved issues, honestly logged (BUILD_PROMPT §11.5). Severity: `high` / `medium` / `low`.

- **[medium] Live MCP smoke test not yet run.** `tests/test_mcp_live.py` needs the operator to complete the Robinhood MCP OAuth handshake in a browser and export `RH_MCP_ACCESS_TOKEN` (RUNBOOK §1). The offline suite fully covers the wrapper; the live fetch is the remaining Phase 2 gate item. Run: `RH_MCP_ACCESS_TOKEN=... uv run pytest -m live tests/test_mcp_live.py`.
