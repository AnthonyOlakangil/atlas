"""§4.1 structure check: clean root, .gitignore covers secrets/caches/artifacts, .env untracked."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EXPECTED_ROOT = {
    ".claude",
    ".env",
    ".env.example",
    ".git",
    ".github",
    ".gitignore",
    ".python-version",
    ".venv",
    "BUILD_PROMPT.md",
    "BUILD_PROMPT_V2.md",
    "DECISIONS.md",
    "DOGFOOD.md",
    "KNOWN_ISSUES.md",
    "LICENSE",
    "README.md",
    "RUNBOOK.md",
    "alembic.ini",
    "atlas",
    "atlas.db",
    "briefs",
    "frontend",
    "migrations",
    "pyproject.toml",
    "runs",
    "tests",
    "uv.lock",
}

REQUIRED_GITIGNORE_PATTERNS = [
    ".env",
    "atlas/config/portfolio.yaml",  # real holdings — never committed
    "__pycache__/",
    "*.pyc",
    ".venv/",
    "node_modules/",
    "dist/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".DS_Store",
    ".vscode/",
    ".idea/",
    "*.db",
    "runs/",
]


def test_no_unexpected_files_at_root() -> None:
    candidates = {p.name for p in REPO.iterdir()} - EXPECTED_ROOT
    unexpected = []
    for name in candidates:
        # gitignored files (caches, local artifacts) can never be committed — that's fine
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", name], cwd=REPO
        ).returncode == 0
        if not ignored:
            unexpected.append(name)
    assert not unexpected, f"Unexpected non-ignored files at repo root: {sorted(unexpected)}"


def test_gitignore_covers_required_patterns() -> None:
    gitignore = (REPO / ".gitignore").read_text().splitlines()
    entries = {line.strip() for line in gitignore if line.strip() and not line.startswith("#")}
    missing = [p for p in REQUIRED_GITIGNORE_PATTERNS if p not in entries]
    assert not missing, f".gitignore missing required patterns: {missing}"


def test_env_never_tracked() -> None:
    out = subprocess.run(
        ["git", "ls-files", ".env"], cwd=REPO, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "", ".env must never be tracked by git"


def test_env_example_is_tracked_or_present() -> None:
    assert (REPO / ".env.example").exists(), ".env.example must ship with the repo"
    content = (REPO / ".env.example").read_text()
    assert "GEMINI_API_KEY=" in content
    # The example must never contain an actual key
    for line in content.splitlines():
        if line.startswith("GEMINI_API_KEY="):
            assert line.strip() == "GEMINI_API_KEY=", ".env.example must ship an EMPTY key"
