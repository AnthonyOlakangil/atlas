"""Role-scoped tool registries — structural enforcement of §1.1.

Any LLM call that processes untrusted content runs against a
ReadOnlyToolRegistry, which is *incapable* of holding a state-mutating tool:
registration raises, by class invariant, before the tool ever exists in the
registry. This is enforcement in code, not in a system prompt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Name prefixes that mutate broker state. Defense in depth: even a tool
# mislabeled as non-mutating is rejected by name.
MUTATING_PREFIXES = (
    "place_",
    "cancel_",
    "create_",
    "update_",
    "add_",
    "remove_",
    "follow_",
    "unfollow_",
    "execute_",
)


class MutatingToolError(RuntimeError):
    """Raised when a mutating tool is offered to a read-only context."""


class UnknownToolError(KeyError):
    """Raised when a tool name is called that was never registered."""


@dataclass(frozen=True)
class Tool:
    name: str
    fn: Callable[..., Any]
    description: str = ""
    mutating: bool = False


class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, /, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise UnknownToolError(name)
        return self._tools[name].fn(**kwargs)


class ReadOnlyToolRegistry(ToolRegistry):
    """Structurally incapable of holding an order-placement or state-mutating tool."""

    def register(self, tool: Tool) -> None:
        if tool.mutating or tool.name.startswith(MUTATING_PREFIXES):
            raise MutatingToolError(
                f"Tool {tool.name!r} mutates state and can never enter a read-only "
                f"(analysis) context. Analysis agents cannot place orders — §1.1."
            )
        super().register(tool)


def build_analysis_registry(tools: tuple[Tool, ...] = ()) -> ReadOnlyToolRegistry:
    """The only registry constructor the research/analysis path may use."""
    return ReadOnlyToolRegistry(tools)
