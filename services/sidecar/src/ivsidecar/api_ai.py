"""Automatic tags and search: model download and the on/off switch (T2).

GET  /v1/ai            status: models installed, download progress, switch, how far tagging is
POST /v1/ai:install    download the models (about 0.9 GB) in the background; switches AI on when done
PUT  /v1/settings/ai   {enabled, max_tags}; switching on queues every photo, off drops queued AI jobs
GET  /v1/ai/groups     "Sorted by AI": the most common AI tags, as virtual groups (T3)
"""

from __future__ import annotations

import logging
import sqlite3
import threading

from fastapi import APIRouter, HTTPException

from . import ai_models, handlers, settings
from . import library as lib
from . import schemas as S
from .events import bus
from .jobqueue import JobQueue

log = logging.getLogger("ivsidecar.ai")


class Download:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"  # idle | downloading | failed | done
        self.done = 0
        self.error: str | None = None
        self.thread: threading.Thread | None = None


def build_router(conn: sqlite3.Connection, db_lock: threading.RLock, queue: JobQueue | None = None) -> APIRouter:
    r = APIRouter(prefix="/v1")
    library_dir = lib.library_dir(conn)
    dl = Download()

    def switch_on() -> int:
        with db_lock, lib.tx(conn):
            s = settings.load(conn, "ai")
            settings.save(conn, "ai", s.model_copy(update={"enabled": True}))
            n = handlers.enqueue_ai_backfill(conn)
        if queue is not None:
            queue.wake()
        bus.changed("ai")
        return n

    def status() -> dict:
        with db_lock:
            s = settings.load(conn, "ai")
            jobs = dict(
                conn.execute("SELECT status, count(*) FROM jobs WHERE stage = 'classify' GROUP BY status").fetchall()
            )
            tagged = conn.execute("SELECT count(DISTINCT asset_id) FROM asset_tags WHERE origin = 'ai'").fetchone()[0]
        with dl.lock:
            d = {"state": dl.state, "done_bytes": dl.done, "total_bytes": ai_models.TOTAL_BYTES, "error": dl.error}
        return {
            "installed": ai_models.installed(library_dir),
            "enabled": s.enabled,
            "max_tags": s.max_tags,
            "download": d,
            "photos_done": jobs.get("succeeded", 0),
            "photos_waiting": jobs.get("queued", 0) + jobs.get("running", 0),
            "photos_failed": jobs.get("failed", 0) + jobs.get("dead", 0),
            "photos_with_ai_tags": tagged,
        }

    def run_download() -> None:
        def progress(done: int, total: int) -> None:
            with dl.lock:
                dl.done = done
            bus.task_progress("ai_models", done, total)

        try:
            ai_models.download(library_dir, progress)
            with dl.lock:
                dl.state, dl.error = "done", None
            n = switch_on()
            log.info("AI models installed; %d photos queued for tagging", n)
        except Exception as e:  # noqa: BLE001 - shown in the UI with a Retry button
            log.warning("AI model download failed: %s", e)
            with dl.lock:
                dl.state, dl.error = "failed", str(e)[:500]
        bus.changed("ai")

    @r.get("/ai")
    def get_ai() -> S.AiStatus:
        return status()

    @r.post("/ai:install", status_code=202)
    def install() -> S.AiStatus:
        if ai_models.installed(library_dir):
            with db_lock:
                enabled = settings.load(conn, "ai").enabled
            if not enabled:
                switch_on()
            return status()
        with dl.lock:
            if dl.state != "downloading":
                dl.state, dl.error = "downloading", None
                dl.thread = threading.Thread(target=run_download, daemon=True, name="iv-ai-download")
                dl.thread.start()
        return status()

    @r.get("/ai/groups")
    def ai_groups() -> list[S.FacetTag]:
        with db_lock:
            return lib.ai_groups(conn)

    @r.get("/settings/ai")
    def get_ai_settings() -> settings.AiOptions:
        with db_lock:
            return settings.load(conn, "ai")

    @r.put("/settings/ai")
    def put_ai_settings(body: settings.AiOptions) -> settings.AiOptions:
        if body.enabled and not ai_models.installed(library_dir):
            raise HTTPException(status_code=409, detail="download the AI models first")
        with db_lock, lib.tx(conn):
            was = settings.load(conn, "ai").enabled
            settings.save(conn, "ai", body)
            if body.enabled and not was:
                handlers.enqueue_ai_backfill(conn)
            elif was and not body.enabled:
                handlers.cancel_queued_ai(conn)
        if queue is not None:
            queue.wake()
        bus.changed("ai")
        return body

    r.state = {"download": dl}  # for tests
    return r
