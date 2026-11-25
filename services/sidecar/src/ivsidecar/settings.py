"""Typed settings stored as JSON in the `settings` table (one row per group).

Only known groups exist; unknown keys are rejected by the models, so a bad UI request
cannot store arbitrary data.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _default_threads() -> int:
    # Leave two cores for the UI and the rest of Windows.
    return max(1, (os.cpu_count() or 2) - 2)


class Resources(BaseModel):
    """How much of the PC background processing may use (doc 04: /settings/resources)."""

    model_config = ConfigDict(extra="forbid")

    threads: int = Field(default_factory=_default_threads, ge=1, le=64)
    max_ram_mb: int = Field(default=4096, ge=512, le=262_144)
    only_when_idle: bool = False
    only_on_ac: bool = False
    gpu: Literal["auto", "directml", "cpu"] = "auto"


GROUPS: dict[str, type[BaseModel]] = {"resources": Resources}


def load(conn: sqlite3.Connection, group: str) -> BaseModel:
    model = GROUPS[group]
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (group,)).fetchone()
    if row is None:
        return model()
    try:
        return model.model_validate(json.loads(row[0]))
    except ValueError:
        # Stored value no longer valid (e.g. after a model change): fall back to defaults
        # rather than refusing to start. The next save overwrites it.
        return model()


def save(conn: sqlite3.Connection, group: str, value: BaseModel) -> None:
    # Single upsert statement: atomic on its own (the connection runs in autocommit mode).
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (group, value.model_dump_json()),
    )
