"""Text in photos (K2): read, edit and revert a photo's OCR text, and OCR progress.

The OCR result itself is written by the job queue (handlers.store_ocr). Here the user's side:
an edit becomes the current text (origin 'user') and is never overwritten by a later OCR run;
"Use the read text again" drops the edit. Both keep the search indexes in step.
"""

from __future__ import annotations

import sqlite3

from . import handlers
from .cards import refresh_card
from .entities import get_type, list_entities, refresh_entities
from .library import Invalid, NotFound, _exists, audit

MAX_TEXT = 100_000


def _job(conn: sqlite3.Connection, asset_id: int) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT status, last_error FROM jobs WHERE asset_id = ? AND stage = 'ocr' ORDER BY id DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def get_text(conn: sqlite3.Connection, asset_id: int) -> dict:
    _exists(conn, "assets", asset_id, "photo")
    status, error = _job(conn, asset_id)
    state = {
        None: "not_read",
        "queued": "waiting",
        "running": "reading",
        "succeeded": "done",
        "skipped": "done",
        "failed": "failed",
        "dead": "failed",
    }[status]
    cur = conn.execute(
        "SELECT origin, full_text, engine, engine_version, confidence, created_at FROM extracted_text "
        "WHERE asset_id = ? AND is_current = 1",
        (asset_id,),
    ).fetchone()
    ai = conn.execute(
        "SELECT id FROM extracted_text WHERE asset_id = ? AND origin = 'ai' ORDER BY id DESC LIMIT 1", (asset_id,)
    ).fetchone()
    lines = []
    if ai:
        lines = [
            {"line_no": n, "text": t, "x": x, "y": y, "w": w, "h": h, "confidence": c}
            for n, t, x, y, w, h, c in conn.execute(
                "SELECT line_no, text, x, y, w, h, confidence FROM text_regions WHERE extracted_text_id = ? "
                "ORDER BY line_no",
                (ai[0],),
            )
        ]
    return {
        "state": state,
        "error": error if state == "failed" else None,
        "text": cur[1] if cur else "",
        "origin": cur[0] if cur else None,
        "engine": f"{cur[2]} {cur[3]}".strip() if cur and cur[0] == "ai" else None,
        "confidence": cur[4] if cur else None,
        "updated_at": cur[5] if cur else None,
        "lines": lines,
        "entities": list_entities(conn, asset_id),
        "type": get_type(conn, asset_id),
    }


def set_text(conn: sqlite3.Connection, asset_id: int, text: str) -> None:
    """Your version of the photo's text. Later OCR runs keep it."""
    _exists(conn, "assets", asset_id, "photo")
    text = text.replace("\r\n", "\n").strip()
    if len(text) > MAX_TEXT:
        raise Invalid(f"text is longer than {MAX_TEXT} characters")
    cur = handlers.current_text(conn, asset_id)
    before = cur[2] if cur else None
    if cur is not None and cur[1] == "user":
        conn.execute("UPDATE extracted_text SET full_text = ? WHERE id = ?", (text, cur[0]))
    else:
        conn.execute("UPDATE extracted_text SET is_current = 0 WHERE asset_id = ?", (asset_id,))
        conn.execute(
            "INSERT INTO extracted_text (asset_id, engine, engine_version, full_text, origin, is_current) "
            "VALUES (?, 'user', '', ?, 'user', 1)",
            (asset_id, text),
        )
    handlers.set_search_text(conn, asset_id, text)
    refresh_entities(conn, asset_id)
    refresh_card(conn, asset_id)
    audit(conn, "text.edit", "asset", asset_id, {"len": len(before or "")}, {"len": len(text)})


def revert_text(conn: sqlite3.Connection, asset_id: int) -> None:
    """Drop your edit; the text read from the photo (if any) is shown and searched again."""
    _exists(conn, "assets", asset_id, "photo")
    n = conn.execute("DELETE FROM extracted_text WHERE asset_id = ? AND origin = 'user'", (asset_id,)).rowcount
    if not n:
        raise NotFound("this photo's text has not been edited")
    ai = conn.execute(
        "SELECT id, full_text FROM extracted_text WHERE asset_id = ? AND origin = 'ai' ORDER BY id DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    if ai:
        conn.execute("UPDATE extracted_text SET is_current = 1 WHERE id = ?", (ai[0],))
    handlers.set_search_text(conn, asset_id, ai[1] if ai else "")
    refresh_entities(conn, asset_id)
    refresh_card(conn, asset_id)
    audit(conn, "text.revert", "asset", asset_id)


def read_again(conn: sqlite3.Connection, asset_id: int) -> None:
    """Run OCR on this photo again, before everything else waiting (priority 10)."""
    _exists(conn, "assets", asset_id, "photo")
    row = conn.execute(
        "SELECT 1 FROM assets a JOIN derivatives d ON d.asset_id = a.id AND d.kind = 'thumb_512' "
        "WHERE a.id = ? AND a.kind = 'image'",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise Invalid("text can be read only from photos that have been processed")
    handlers.enqueue(conn, "ocr", asset_id=asset_id, priority=10, requeue=True)


def status(conn: sqlite3.Connection) -> dict:
    jobs = dict(conn.execute("SELECT status, count(*) FROM jobs WHERE stage = 'ocr' GROUP BY status").fetchall())
    with_text = conn.execute("SELECT count(*) FROM extracted_text WHERE is_current = 1 AND full_text != ''").fetchone()[
        0
    ]
    edited = conn.execute("SELECT count(*) FROM extracted_text WHERE origin = 'user'").fetchone()[0]
    return {
        "enabled": handlers.ocr_enabled(conn),
        "photos_done": jobs.get("succeeded", 0) + jobs.get("skipped", 0),
        "photos_waiting": jobs.get("queued", 0) + jobs.get("running", 0),
        "photos_failed": jobs.get("failed", 0) + jobs.get("dead", 0),
        "photos_with_text": with_text,
        "photos_edited": edited,
    }
