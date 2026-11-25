"""Engine side of stages 1-4: build each job's input and write its result (doc 06 §1).

Runs in the engine process only, inside the queue's write transaction, so a stage's result,
the job's status and the next job are committed together: a crash leaves either all of it
or none of it.

Order per file: identify (location) -> hash (location) -> asset created, or matched to an
existing asset = exact duplicate, no further work -> metadata (asset) -> thumbnail (asset)
-> asset 'ready'.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from .stages import PIPELINE_VERSION

LOCATION_STAGES = ("identify", "hash")
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


class MissingFile(Exception):
    """The job's file has no present location any more (deleted or moved before processing)."""


def enqueue(
    conn: sqlite3.Connection,
    stage: str,
    *,
    asset_id: int | None = None,
    location_id: int | None = None,
    priority: int = 100,
    requeue: bool = False,
) -> None:
    """Idempotent: a second call for the same (owner, stage, version) adds nothing.

    requeue=True resets an existing job to run again (used when a file's content changed).
    """
    if (asset_id is None) == (location_id is None):
        raise ValueError("exactly one of asset_id / location_id")
    owner = "asset_id" if asset_id is not None else "location_id"
    action = (
        f"DO UPDATE SET status='queued', attempts=0, crashes=0, last_error=NULL, run_after={NOW}, "
        "priority=min(priority, excluded.priority), started_at=NULL, finished_at=NULL"
        if requeue
        else "DO NOTHING"
    )
    conn.execute(
        f"INSERT INTO jobs ({owner}, stage, priority, pipeline_version) VALUES (?, ?, ?, ?) "
        f"ON CONFLICT ({owner}, stage, pipeline_version) WHERE {owner} IS NOT NULL {action}",
        (asset_id if asset_id is not None else location_id, stage, priority, PIPELINE_VERSION),
    )


def register_file(
    conn: sqlite3.Connection,
    source_id: int,
    rel_path: str,
    file_key: str,
    size_bytes: int,
    mtime: str,
    priority: int = 100,
    changed: bool = False,
    run_id: int | None = None,
) -> int:
    """Record a found (or changed) file and queue stage 1 for it. Returns the location id.

    changed=True: same file_key, new size/mtime: stage 1 runs again (and so does hash).
    """
    row = conn.execute(
        "INSERT INTO file_locations (source_id, file_key, rel_path, size_bytes, mtime, seen_run_id) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source_id, file_key) DO UPDATE SET rel_path=excluded.rel_path, state='present', "
        f"size_bytes=excluded.size_bytes, mtime=excluded.mtime, last_seen_at={NOW}, seen_run_id=excluded.seen_run_id "
        "RETURNING id",
        (source_id, file_key, rel_path, size_bytes, mtime, run_id),
    ).fetchone()
    enqueue(conn, "identify", location_id=row[0], priority=priority, requeue=changed)
    return row[0]


def _location_path(conn: sqlite3.Connection, location_id: int) -> Path:
    row = conn.execute(
        "SELECT s.root_path, fl.rel_path FROM file_locations fl JOIN sources s ON s.id = fl.source_id "
        "WHERE fl.id = ? AND fl.state = 'present'",
        (location_id,),
    ).fetchone()
    if row is None:
        raise MissingFile(f"location {location_id} is not present")
    return Path(row[0]) / row[1]


def _asset_path(conn: sqlite3.Connection, asset_id: int) -> Path:
    row = conn.execute(
        "SELECT s.root_path, fl.rel_path FROM file_locations fl JOIN sources s ON s.id = fl.source_id "
        "WHERE fl.asset_id = ? AND fl.state = 'present' AND s.status = 'active' ORDER BY fl.id LIMIT 1",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise MissingFile(f"asset {asset_id} has no present file")
    return Path(row[0]) / row[1]


def build_payload(conn: sqlite3.Connection, job: sqlite3.Row, max_pixels: int | None = None) -> dict:
    if job["location_id"] is not None:
        return {"path": str(_location_path(conn, job["location_id"]))}
    payload = {"path": str(_asset_path(conn, job["asset_id"]))}
    if job["stage"] == "thumbnail":
        uid = conn.execute("SELECT uid FROM assets WHERE id = ?", (job["asset_id"],)).fetchone()[0]
        o = conn.execute("SELECT orientation FROM asset_metadata WHERE asset_id = ?", (job["asset_id"],)).fetchone()
        payload.update(uid=uid, orientation=o[0] if o else None, max_pixels=max_pixels)
    return payload


# --------------------------------------------------------------------------- results


def apply_result(conn: sqlite3.Connection, job: sqlite3.Row, data: dict | None) -> tuple[str, str | None]:
    """Write a successful stage result and queue the next stage.

    Returns the job's final status ('succeeded' or 'skipped') and a note to keep on the job.
    """
    stage, prio = job["stage"], job["priority"]
    if stage == "identify":
        conn.execute("UPDATE file_locations SET mime_type = ? WHERE id = ?", (data["mime"], job["location_id"]))
        if not data["supported"]:
            # Not hashed: reading a large unsupported file (archive, PDF, PSD) in full would be
            # wasted work. The location keeps its mime type, so the UI can list these files.
            return "skipped", f"unsupported: {data['mime']}"
        # requeue: when identify runs again because the file changed, the hash must run again too
        enqueue(conn, "hash", location_id=job["location_id"], priority=prio, requeue=True)
        return "succeeded", None
    if stage == "hash":
        return "succeeded", _apply_hash(conn, job, data)
    if stage == "metadata":
        return "succeeded", _apply_metadata(conn, job, data)
    if stage == "thumbnail":
        _apply_thumbnail(conn, job, data)
        return "succeeded", None
    raise ValueError(f"no result handler for stage {stage}")


def _apply_hash(conn: sqlite3.Connection, job: sqlite3.Row, d: dict) -> str | None:
    loc = job["location_id"]
    existing = conn.execute("SELECT id FROM assets WHERE sha256 = ?", (d["sha256"],)).fetchone()
    if existing:  # exact duplicate: one more place where this content lives, nothing to reprocess
        conn.execute(
            "UPDATE file_locations SET asset_id = ?, mime_type = ? WHERE id = ?", (existing[0], d["mime"], loc)
        )
        return "duplicate"
    status = "pending" if d["supported"] else "unsupported"
    error = None if d["supported"] else f"unsupported: {d['mime']}"
    asset_id = conn.execute(
        "INSERT INTO assets (uid, sha256, kind, status, mime_type, byte_size, error, file_modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT mtime FROM file_locations WHERE id = ?)) RETURNING id",
        (str(uuid.uuid4()), d["sha256"], d["kind"], status, d["mime"], d["size"], error, loc),
    ).fetchone()[0]
    conn.execute("UPDATE file_locations SET asset_id = ?, mime_type = ? WHERE id = ?", (asset_id, d["mime"], loc))
    if d["supported"]:
        enqueue(conn, "metadata", asset_id=asset_id, priority=job["priority"])
    return None


_MD_COLS = (
    "camera_make camera_model lens iso exposure_s f_number focal_length_mm orientation color_profile "
    "gps_lat gps_lon gps_alt_m raw_exif extractor"
).split()


def _apply_metadata(conn: sqlite3.Connection, job: sqlite3.Row, d: dict) -> str | None:
    aid = job["asset_id"]
    cols = ", ".join(_MD_COLS)
    marks = ", ".join("?" for _ in _MD_COLS)
    conn.execute(
        f"INSERT OR REPLACE INTO asset_metadata (asset_id, {cols}) VALUES (?, {marks})",
        (aid, *(d.get(c) for c in _MD_COLS)),
    )
    # Never overwrite a date or size the user (or later AI) set; source values fill gaps only.
    conn.execute(
        "UPDATE assets SET status = 'processing', width = coalesce(?, width), height = coalesce(?, height), "
        "duration_ms = coalesce(?, duration_ms), "
        "taken_at = CASE WHEN taken_at_origin IS NULL OR taken_at_origin = 'source' THEN ? ELSE taken_at END, "
        "taken_at_origin = CASE WHEN taken_at_origin IS NULL OR taken_at_origin = 'source' "
        "  THEN CASE WHEN ? IS NULL THEN NULL ELSE 'source' END ELSE taken_at_origin END, "
        f"updated_at = {NOW} WHERE id = ?",
        (d.get("width"), d.get("height"), d.get("duration_ms"), d.get("taken_at"), d.get("taken_at"), aid),
    )
    enqueue(conn, "thumbnail", asset_id=aid, priority=job["priority"])
    return d.get("warning")


def _apply_thumbnail(conn: sqlite3.Connection, job: sqlite3.Row, d: dict | None) -> None:
    aid = job["asset_id"]
    if d is not None:  # None = video: metadata only for now (decision 2)
        for der in d["derivatives"]:
            conn.execute(
                "INSERT OR REPLACE INTO derivatives (asset_id, kind, rel_path, width, height, byte_size) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (aid, der["kind"], der["rel_path"], der["width"], der["height"], der["byte_size"]),
            )
        conn.execute("DELETE FROM vec_phash WHERE rowid = ?", (aid,))
        conn.execute("INSERT INTO vec_phash (rowid, phash) VALUES (?, vec_bit(?))", (aid, d["phash"]))
        conn.execute(
            "UPDATE assets SET phash = ?, width = coalesce(width, ?), height = coalesce(height, ?) WHERE id = ?",
            (d["phash"], d["width"], d["height"], aid),
        )
    conn.execute(f"UPDATE assets SET status = 'ready', error = NULL, updated_at = {NOW} WHERE id = ?", (aid,))


def apply_failure(conn: sqlite3.Connection, job: sqlite3.Row, error: str) -> None:
    """A job gave up (failed or dead): show it on the asset so the UI can offer Retry."""
    if job["asset_id"] is not None:
        conn.execute(
            f"UPDATE assets SET status = 'failed', error = ?, updated_at = {NOW} WHERE id = ?",
            (f"{job['stage']}: {error}"[:1000], job["asset_id"]),
        )


def stats(conn: sqlite3.Connection) -> dict:
    by_stage: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for stage, status, n in conn.execute("SELECT stage, status, count(*) FROM jobs GROUP BY stage, status"):
        by_stage.setdefault(stage, {})[status] = n
        totals[status] = totals.get(status, 0) + n
    assets = dict(conn.execute("SELECT status, count(*) FROM assets GROUP BY status").fetchall())
    return {"jobs": totals, "by_stage": by_stage, "assets": assets}
