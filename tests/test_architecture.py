"""§1.2 gate: nothing under atlas/research/ may import atlas/execution/.

Pure-AST import scan run in CI on every push; a violation fails the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESEARCH = REPO / "atlas" / "research"
FORBIDDEN = "atlas.execution"


def find_violations(package_root: Path) -> list[tuple[str, str]]:
    """Return (file, offending_import) pairs for imports of atlas.execution."""
    violations: list[tuple[str, str]] = []
    for py in sorted(package_root.rglob("*.py")):
        label = str(py.relative_to(REPO)) if py.is_relative_to(REPO) else str(py)
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == FORBIDDEN or alias.name.startswith(FORBIDDEN + "."):
                        violations.append((label, alias.name))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == FORBIDDEN or mod.startswith(FORBIDDEN + "."):
                    violations.append((label, mod))
                # relative escape hatch: from ..execution import x
                if node.level > 0 and (mod == "execution" or mod.startswith("execution.")):
                    violations.append((label, "." * node.level + mod))
    return violations


def test_research_never_imports_execution() -> None:
    violations = find_violations(RESEARCH)
    assert not violations, (
        f"atlas/research must never import atlas/execution (§1.2). Violations: {violations}"
    )


def test_research_never_mentions_execution_even_dynamically() -> None:
    """Blocks importlib/`__import__` workarounds: the string may not appear at all."""
    offenders = [
        str(py.relative_to(REPO))
        for py in sorted(RESEARCH.rglob("*.py"))
        if FORBIDDEN in py.read_text()
    ]
    assert not offenders, (
        f"atlas/research files may not even mention {FORBIDDEN!r}: {offenders}"
    )


def test_scanner_actually_catches_violations(tmp_path: Path) -> None:
    """Meta-test: prove the scanner would fail the build on a real violation."""
    bad = tmp_path / "sneaky.py"
    bad.write_text(
        "import atlas.execution\n"
        "from atlas.execution.mcp_client import MCPClient\n"
        "from ..execution import approval\n"
    )
    found = {imp for _, imp in find_violations(tmp_path)}
    assert found == {"atlas.execution", "atlas.execution.mcp_client", "..execution"}
