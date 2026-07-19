"""Live smoke test (§Phase 2 gate): fetches the real portfolio.

Opt-in: requires the operator's OAuth token and `-m live`:
    RH_MCP_ACCESS_TOKEN=... uv run pytest -m live tests/test_mcp_live.py
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.environ.get("RH_MCP_ACCESS_TOKEN"),
    reason="requires operator's Robinhood MCP OAuth token (see RUNBOOK §1)",
)
def test_live_smoke_fetch_real_portfolio() -> None:
    from atlas.core.settings import get_settings
    from atlas.execution.transport import HTTPMCPTransport

    settings = get_settings()
    transport = HTTPMCPTransport(
        settings.rh_mcp_url, access_token=os.environ["RH_MCP_ACCESS_TOKEN"]
    )
    accounts = transport.call_tool("get_accounts", {})
    assert accounts, "expected at least one account"
    portfolio = transport.call_tool("get_portfolio", {})
    assert portfolio
