"""Runtime settings (.env) and operator configuration (operator.yaml)."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent
CONFIG_DIR = PACKAGE_ROOT / "config"
BRIEFS_DIR = REPO_ROOT / "briefs"
RUNS_DIR = REPO_ROOT / "runs"


class SleeveConfig(BaseModel):
    target_pct: tuple[float, float]
    thesis: str
    seed_examples: list[str]
    note: str = ""


class RiskConfig(BaseModel):
    tolerance: str
    drawdown_ceiling_comfort: float
    max_single_position_pct: float
    max_sector_concentration_pct: float
    horizon: str


class OperatorConfig(BaseModel):
    """Parsed operator.yaml — Atlas's objective function.

    The sleeve seed_examples are starting points, never a whitelist (§2.0):
    nothing in this config object may be used to gate instrument selection.
    """

    operator: dict[str, Any]
    objective: dict[str, Any]
    risk: RiskConfig
    sleeves: dict[str, SleeveConfig]
    universe: str
    # Candidates to fetch but not hold. Distinct from sleeve seed_examples, which
    # are illustrative only (§2.0) and must never drive data acquisition.
    watchlist: list[str] = []
    preferences: list[str]

    def risk_decimal(self, name: str) -> Decimal:
        """Risk cap as a Decimal (yaml floats never touch money math directly)."""
        return Decimal(str(getattr(self.risk, name)))

    def declared_sleeves(self) -> frozenset[str]:
        """The single source of truth for valid sleeve names.

        Callers validate against this rather than a hardcoded literal, so adding
        a sleeve to operator.yaml is the only edit required. A sleeve declared
        without a `target_pct` band never reaches `sleeves` (see `load`), so it
        is correctly absent here too — a sleeve with no band cannot be drift-checked.
        """
        return frozenset(self.sleeves)

    @classmethod
    def load(cls, path: Path | None = None) -> OperatorConfig:
        raw = yaml.safe_load((path or CONFIG_DIR / "operator.yaml").read_text())
        alloc = raw["allocation_framework"]
        sleeves = {
            name: SleeveConfig(**cfg)
            for name, cfg in alloc.items()
            if isinstance(cfg, dict) and "target_pct" in cfg
        }
        return cls(
            operator=raw["operator"],
            objective=raw["objective"],
            risk=RiskConfig(**raw["risk"]),
            sleeves=sleeves,
            universe=alloc.get("universe", "open"),
            watchlist=list(raw.get("watchlist") or []),
            preferences=raw["preferences"],
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    llm_provider: Literal["gemini", "ollama", "anthropic", "fake"] = "gemini"
    llm_model: str = "gemini-3.5-flash"

    atlas_mode: Literal["PAPER", "LIVE"] = "PAPER"
    # manual (default): no broker link — portfolio.yaml is the book, execution is inert.
    # connected: the Robinhood MCP path (OAuth, live reads, gated execution).
    broker_mode: Literal["manual", "connected"] = "manual"
    atlas_db_path: str = "atlas.db"

    fred_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "atlas-research/0.1 (personal research agent)"
    youtube_api_key: str = ""
    rh_mcp_url: str = "https://agent.robinhood.com/mcp/trading"
    rh_mcp_access_token: str = ""

    timezone: str = "America/Los_Angeles"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.atlas_db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_operator_config() -> OperatorConfig:
    return OperatorConfig.load()


def load_sources(path: Path | None = None) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load((path or CONFIG_DIR / "sources.yaml").read_text())
    return raw["sources"]
