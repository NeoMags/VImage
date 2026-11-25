"""Library export (B19): JSON (everything per photo) and CSV (one row per photo).

Streamed in chunks of photos, so memory stays flat however big the library is. Every value
that can come from the AI keeps its origin, so AI output stays labelled (PRD rule 3).
Written to a temporary file next to the destination, then renamed: no half-written export.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, backups

CHUNK = 1000
FORMATS = ("json", "csv")
CSV_COLUMNS = (
    "uid", "path", "date", "title", "title_origin", "kind", "mime_type", "width", "height", "byte_size",
    "tags", "collections", "favorite", "hidden", "reviewed", "notes", "sha256",
)  # fmt: skip


class ExportError(RuntimeError):
    pass


def _chunks(conn: sqlite3.Connection, include_hidden: bool) -> Iterator[list[int]]:
    last = 0
    hidden = "" if include_hidden else "AND is_hidden = 0"
    while True:
        ids = [
            r[0]
            for r in conn.execute(f"SELECT id FROM assets WHERE id > ? {hidden} ORDER BY id LIMIT ?", (last, CHUNK))
        ]
        if not ids:
            return
        yield ids
        last = ids[-1]


def _group(conn: sqlite3.Connection, sql: str, ids: list[int]) -> dict[int, list[tuple]]:
    out: dict[int, list[tuple]] = defaultdict(list)
    marks = ",".join("?" * len(ids))
    for row in conn.execute(sql.format(marks=marks), ids):
        out[row[0]].append(row[1:])
    return out


def records(conn: sqlite3.Connection, include_hidden: bool) -> Iterator[dict]:
    """One dict per photo, in id order."""
    for ids in _chunks(conn, include_hidden):
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            "SELECT id, uid, kind, status, mime_type, byte_size, width, height, duration_ms, sha256, taken_at, "
            "taken_at_origin, file_modified_at, title, title_origin, is_favorite, is_reviewed, is_hidden, "
            f"indexed_at, updated_at FROM assets WHERE id IN ({marks}) ORDER BY id",
            ids,
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        md_cur = conn.execute(f"SELECT * FROM asset_metadata WHERE asset_id IN ({marks})", ids)
        md_cols = [c[0] for c in md_cur.description]
        metadata = {
            r[0]: {k: v for k, v in zip(md_cols, r, strict=True) if k not in ("asset_id", "raw_exif")}
            for r in md_cur.fetchall()
        }
        locations = _group(
            conn,
            "SELECT l.asset_id, s.root_path, l.rel_path, l.state, s.display_name FROM file_locations l "
            "JOIN sources s ON s.id = l.source_id WHERE l.asset_id IN ({marks}) ORDER BY l.id",
            ids,
        )
        tags = _group(
            conn,
            "SELECT x.asset_id, t.name, x.origin, x.confidence, x.confirmed_at FROM asset_tags x "
            "JOIN tags t ON t.id = x.tag_id WHERE x.asset_id IN ({marks}) AND x.rejected = 0 ORDER BY t.name",
            ids,
        )
        colls = _group(
            conn,
            "SELECT x.asset_id, c.uid, c.name FROM collection_assets x JOIN collections c ON c.id = x.collection_id "
            "WHERE x.asset_id IN ({marks}) ORDER BY c.name",
            ids,
        )
        notes = _group(
            conn,
            "SELECT asset_id, body, created_at, updated_at FROM notes WHERE asset_id IN ({marks}) ORDER BY id",
            ids,
        )
        for r in rows:
            aid = r.pop("id")
            yield {
                "uid": r["uid"],
                "kind": r["kind"],
                "status": r["status"],
                "mime_type": r["mime_type"],
                "byte_size": r["byte_size"],
                "width": r["width"],
                "height": r["height"],
                "duration_ms": r["duration_ms"],
                "sha256": r["sha256"].hex() if r["sha256"] else None,
                "taken_at": {"value": r["taken_at"], "origin": r["taken_at_origin"]},
                "file_modified_at": r["file_modified_at"],
                "title": {"value": r["title"], "origin": r["title_origin"]},
                "favorite": bool(r["is_favorite"]),
                "reviewed": bool(r["is_reviewed"]),
                "hidden": bool(r["is_hidden"]),
                "added_at": r["indexed_at"],
                "updated_at": r["updated_at"],
                "locations": [
                    {"path": str(Path(root) / rel), "state": state, "source": name}
                    for root, rel, state, name in locations.get(aid, [])
                ],
                "metadata": metadata.get(aid),
                "tags": [
                    {"name": n, "origin": o, "confidence": c, "confirmed": confirmed is not None}
                    for n, o, c, confirmed in tags.get(aid, [])
                ],
                "collections": [{"uid": u, "name": n} for u, n in colls.get(aid, [])],
                "notes": [{"body": b, "created_at": c, "updated_at": u} for b, c, u in notes.get(aid, [])],
            }


def _csv_row(rec: dict) -> dict:
    present = next((loc["path"] for loc in rec["locations"] if loc["state"] == "present"), None)
    return {
        "uid": rec["uid"],
        "path": present or (rec["locations"][0]["path"] if rec["locations"] else ""),
        "date": rec["taken_at"]["value"] or rec["file_modified_at"] or "",
        "title": rec["title"]["value"] or "",
        "title_origin": rec["title"]["origin"] or "",
        "kind": rec["kind"],
        "mime_type": rec["mime_type"],
        "width": rec["width"] if rec["width"] is not None else "",
        "height": rec["height"] if rec["height"] is not None else "",
        "byte_size": rec["byte_size"],
        # AI tags keep their label in the cell: "beach; sunset (AI)"
        "tags": "; ".join(
            t["name"] + (" (AI)" if t["origin"] == "ai" and not t["confirmed"] else "") for t in rec["tags"]
        ),
        "collections": "; ".join(c["name"] for c in rec["collections"]),
        "favorite": int(rec["favorite"]),
        "hidden": int(rec["hidden"]),
        "reviewed": int(rec["reviewed"]),
        "notes": len(rec["notes"]),
        "sha256": rec["sha256"] or "",
    }


def check_destination(conn: sqlite3.Connection, path: Path, fmt: str) -> Path:
    if fmt not in FORMATS:
        raise ExportError(f"unknown format {fmt!r}")
    if not path.is_absolute():
        raise ExportError("the export path must be a full path")
    if path.suffix.lower() != f".{fmt}":
        path = path.with_name(path.name + f".{fmt}")
    if not path.parent.is_dir():
        raise ExportError(f"folder not found: {path.parent}")
    if backups.inside_a_source(conn, path.parent):
        raise ExportError("exports cannot be saved inside a photo source folder (the app never writes there)")
    return path


def export(
    conn: sqlite3.Connection,
    path: Path,
    fmt: str,
    include_hidden: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Writes the export. Returns {path, photos, bytes}. `progress(done, total)` every chunk."""
    path = check_destination(conn, path, fmt)
    hidden = "" if include_hidden else "WHERE is_hidden = 0"
    total = conn.execute(f"SELECT count(*) FROM assets {hidden}").fetchone()[0]
    tmp = path.with_name(path.name + ".partial")
    n = 0
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            if fmt == "json":
                head = {
                    "format": "imagevault-export",
                    "version": 1,
                    "app_version": __version__,
                    "exported_at": datetime.now(UTC).isoformat(),
                    "include_hidden": include_hidden,
                    "photos": total,
                }
                f.write(json.dumps(head, ensure_ascii=False)[:-1] + ', "items": [\n')
                for rec in records(conn, include_hidden):
                    f.write((",\n" if n else "") + json.dumps(rec, ensure_ascii=False))
                    n += 1
                    if progress and n % CHUNK == 0:
                        progress(n, total)
                f.write("\n]}\n")
            else:
                f.write("﻿")  # BOM: Excel on Windows then reads the file as UTF-8
                w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                w.writeheader()
                for rec in records(conn, include_hidden):
                    w.writerow(_csv_row(rec))
                    n += 1
                    if progress and n % CHUNK == 0:
                        progress(n, total)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if progress:
        progress(n, total)
    return {"path": str(path), "photos": n, "bytes": path.stat().st_size, "format": fmt}
