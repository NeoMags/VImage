"""Smart Collections (S1): a saved set of gallery filters, evaluated every time it is opened.

The rule is stored in `collections.rule` as JSON `{"v": 1, "filters": {...}}`, with the same
names the UI and `GET /v1/assets` use (`from`/`to` for dates). Nothing is stored per photo, so
new photos that match appear on their own and counts are always current.

A rule may name a manual collection, never a smart one (no loops). "recent_days" is a relative
date ("last 30 days") worked out on each query.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AssetKind = Literal["image", "screenshot", "video", "pdf", "drawing", "document", "other"]
AssetStatus = Literal["pending", "processing", "ready", "failed", "unsupported"]


class SmartFilters(BaseModel):
    """The gallery filters a Smart Collection keeps (all must match; several tags must all match)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: list[AssetKind] | None = Field(default=None, max_length=10)
    status: list[AssetStatus] | None = Field(default=None, max_length=10)
    mime: list[str] | None = Field(default=None, max_length=50)
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")
    recent_days: int | None = Field(default=None, ge=1, le=36500)
    source: int | None = None
    tag: list[int] | None = Field(default=None, max_length=50)
    collection: int | None = None
    """A manual collection only."""
    favorite: bool | None = None
    reviewed: bool | None = None
    availability: Literal["present", "missing", "unreachable"] | None = None
    min_w: int | None = Field(default=None, ge=1)
    min_h: int | None = Field(default=None, ge=1)
    max_w: int | None = Field(default=None, ge=1)
    max_h: int | None = Field(default=None, ge=1)
    min_bytes: int | None = Field(default=None, ge=0)
    max_bytes: int | None = Field(default=None, ge=0)
    multi_location: bool | None = None


class SmartRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    filters: SmartFilters


def check(conn: sqlite3.Connection, rule: SmartRule) -> str:
    """Validates references and returns the JSON to store. Raises ValueError with a message for people."""
    f = rule.filters
    if not f.model_dump(exclude_none=True):
        raise ValueError("a smart collection needs at least one filter")
    if f.collection is not None:
        row = conn.execute("SELECT kind FROM collections WHERE id = ?", (f.collection,)).fetchone()
        if row is None:
            raise ValueError("the collection in this rule no longer exists")
        if row[0] != "manual":
            raise ValueError("a smart collection cannot use another smart collection")
    for t in f.tag or []:
        if conn.execute("SELECT 1 FROM tags WHERE id = ?", (t,)).fetchone() is None:
            raise ValueError("a tag in this rule no longer exists")
    return json.dumps(rule.model_dump(mode="json", by_alias=True, exclude_none=True), separators=(",", ":"))


def load(raw: str | None) -> dict | None:
    """Stored JSON -> the rule as the API returns it. A rule that no longer validates (should not
    happen) comes back as None rather than breaking the collection list."""
    if raw is None:
        return None
    try:
        return SmartRule.model_validate_json(raw).model_dump(mode="json", by_alias=True, exclude_none=True)
    except ValueError:
        return None


def to_filters(raw: str) -> dict:
    """Stored JSON -> keyword arguments for library._asset_filters (python names)."""
    f = SmartRule.model_validate_json(raw).filters.model_dump(exclude_none=True)
    return f
