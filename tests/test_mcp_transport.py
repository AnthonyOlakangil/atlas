"""HTTPMCPTransport protocol handling, mocked with respx (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from atlas.execution.transport import HTTPMCPTransport, MCPToolError

URL = "https://agent.robinhood.com/mcp/trading"


def _rpc_result(payload: object) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


@respx.mock
def test_initialize_handshake_then_tool_call() -> None:
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"mcp-session-id": "s-1"}),
        httpx.Response(202),  # notifications/initialized
        httpx.Response(200, json=_rpc_result({"equity": "250.00"})),
    ]
    transport = HTTPMCPTransport(URL, access_token="tok")
    result = transport.call_tool("get_portfolio", {})
    assert result == {"equity": "250.00"}

    init_req = json.loads(route.calls[0].request.content)
    assert init_req["method"] == "initialize"
    assert route.calls[0].request.headers["authorization"] == "Bearer tok"
    tool_req = json.loads(route.calls[2].request.content)
    assert tool_req["params"] == {"name": "get_portfolio", "arguments": {}}
    assert route.calls[2].request.headers["mcp-session-id"] == "s-1"


@respx.mock
def test_sse_framed_response_parsed() -> None:
    sse = "event: message\ndata: " + json.dumps(_rpc_result({"ok": True})) + "\n\n"
    respx.post(URL).side_effect = [
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}}),
        httpx.Response(202),
        httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"}),
    ]
    transport = HTTPMCPTransport(URL)
    assert transport.call_tool("get_accounts", {}) == {"ok": True}


@respx.mock
def test_tool_error_raises() -> None:
    respx.post(URL).side_effect = [
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}}),
        httpx.Response(202),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "denied"}}),
    ]
    transport = HTTPMCPTransport(URL)
    with pytest.raises(MCPToolError, match="denied"):
        transport.call_tool("place_equity_order", {"symbol": "VOO"})
