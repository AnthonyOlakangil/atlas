"""MCP transport layer: how tool calls reach the Robinhood Trading MCP.

`HTTPMCPTransport` speaks MCP streamable-HTTP JSON-RPC (initialize →
notifications/initialized → tools/call). Auth is a bearer token obtained by
the operator's browser OAuth handshake (RUNBOOK §1) — Atlas never handles the
Robinhood password.
"""

from __future__ import annotations

import itertools
import json
from typing import Any, Protocol

import httpx


class MCPTransportError(RuntimeError):
    """Transport-level failure (network, protocol, non-2xx)."""


class MCPToolError(RuntimeError):
    """The MCP server executed the call and returned an error result."""


class MCPTransport(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class HTTPMCPTransport:
    """Minimal MCP streamable-HTTP client over httpx."""

    def __init__(self, url: str, access_token: str = "", timeout: float = 30.0) -> None:
        self._url = url
        self._timeout = timeout
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._client = httpx.Client(headers=headers, timeout=timeout)
        self._session_id: str | None = None
        self._ids = itertools.count(1)

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers: dict[str, str] = {}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        try:
            resp = self._client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"MCP request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise MCPTransportError(f"MCP HTTP {resp.status_code}: {resp.text[:500]}")
        return resp

    @staticmethod
    def _parse_body(resp: httpx.Response) -> dict[str, Any]:
        """Handle both plain-JSON and SSE-framed responses."""
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    parsed: dict[str, Any] = json.loads(line[5:].strip())
                    return parsed
            raise MCPTransportError("SSE response contained no data frame")
        body: dict[str, Any] = resp.json()
        return body

    def _ensure_session(self) -> None:
        if self._session_id is not None:
            return
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "atlas", "version": "0.1.0"},
                },
            }
        )
        self._session_id = resp.headers.get("mcp-session-id", "")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self._ensure_session()
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        body = self._parse_body(resp)
        if "error" in body:
            raise MCPToolError(f"{name}: {body['error']}")
        result: dict[str, Any] = body.get("result", {})
        if result.get("isError"):
            raise MCPToolError(f"{name}: {result.get('content')}")
        content = result.get("content", [])
        # MCP tools return a content list; RH tools return one JSON text block
        if isinstance(content, list) and content and content[0].get("type") == "text":
            try:
                return json.loads(content[0]["text"])
            except (json.JSONDecodeError, KeyError):
                return content[0].get("text")
        return result
