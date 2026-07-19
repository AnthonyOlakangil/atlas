"""Structured logging: human-readable console + JSON-lines per-run files under runs/<date>/."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from atlas.core.settings import RUNS_DIR, get_settings


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = extra
        return json.dumps(payload, default=str)


def run_dir(day: str | None = None) -> Path:
    tz = ZoneInfo(get_settings().timezone)
    d = day or datetime.now(tz).strftime("%Y-%m-%d")
    p = RUNS_DIR / d
    p.mkdir(parents=True, exist_ok=True)
    return p


def setup_logging(level: int = logging.INFO, to_run_file: bool = True) -> logging.Logger:
    root = logging.getLogger("atlas")
    if root.handlers:  # already configured
        return root
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(console)

    if to_run_file:
        fh = logging.FileHandler(run_dir() / "atlas.jsonl")
        fh.setFormatter(JsonLineFormatter())
        root.addHandler(fh)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"atlas.{name}")
