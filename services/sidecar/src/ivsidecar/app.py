"""Localhost API (doc 04). Every route requires the per-launch bearer token."""

from __future__ import annotations

import hmac
import os
import platform
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, api_status, handlers, probes, schemas, settings
from . import scanner as scan_mod
from .api_library import build_router
from .jobqueue import JobQueue
from .watcher import FolderWatcher

# Origins used by Tauri 2 webviews (Windows uses http://tauri.localhost).
TAURI_ORIGINS = ["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"]


def dev_origins() -> list[str]:
    """IV_DEV_ORIGINS, honoured only outside the packaged exe (see allowed_origins)."""
    if getattr(sys, "frozen", False):
        return []
    return [o.strip() for o in os.environ.get("IV_DEV_ORIGINS", "").split(",") if o.strip()]


def allowed_origins() -> list[str]:
    """Pages allowed to call the API: the packaged app's webview only. A development run (not the
    packaged exe) also accepts IV_DEV_ORIGINS, e.g. the Vite dev server used by `tauri dev` and the
    UI's end-to-end test. The packaged build ignores it, so it cannot be widened on a user's PC."""
    return TAURI_ORIGINS + dev_origins()


class NewSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    display_name: str | None = Field(default=None, max_length=200)


def create_app(
    conn: sqlite3.Connection,
    token: str,
    started_at: float,
    queue: JobQueue | None = None,
    scanner: scan_mod.Scanner | None = None,
    watcher: FolderWatcher | None = None,
    on_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="ImageVault sidecar", version=__version__, docs_url=None, redoc_url=None, openapi_url="/v1/openapi.json"
    )
    origins = allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def require_token(authorization: str = Header(default="")) -> None:
        expected = f"Bearer {token}"
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing token")

    db_lock = threading.RLock()  # one shared connection: request threads take turns (api_library)
    api_status.install_error_handlers(app)
    app.include_router(build_router(conn, db_lock), dependencies=[Depends(require_token)])
    status_routes, ws_routes = api_status.build_router(conn, db_lock, token, origins, queue)
    app.include_router(status_routes, dependencies=[Depends(require_token)])
    app.include_router(ws_routes)  # checks the token itself (subprotocol)
    if dev_origins():
        _install_dev_thumbs(app, conn, token)

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    def health() -> schemas.Health:
        with db_lock:
            jobs = handlers.stats(conn)["jobs"]
        return {
            "status": "ok",
            "version": __version__,
            "db_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "queue": {
                "queued": jobs.get("queued", 0),
                "running": jobs.get("running", 0),
                "failed": jobs.get("failed", 0) + jobs.get("dead", 0),
            },
            "uptime_s": round(time.time() - started_at, 1),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "db": probes.sqlite_info(conn),
            "vips": probes.vips_info(),
            "heif": probes.heif_info(),
            "stages": probes.stages_info(),
            "exiftool": probes.exiftool_info(),
        }

    @app.post("/v1/shutdown", dependencies=[Depends(require_token)])
    def shutdown(tasks: BackgroundTasks) -> schemas.ShuttingDown:
        """Clean quit (B10): stop taking jobs, let running ones finish, close the database, exit.
        The shell waits up to 5 s for the process to end, then kills it."""
        if on_shutdown is None:
            raise HTTPException(status_code=503, detail="shutdown is not available in this mode")
        tasks.add_task(on_shutdown)  # after the reply is sent
        return schemas.ShuttingDown(stopping=True)

    @app.get("/v1/settings/resources", dependencies=[Depends(require_token)])
    def get_resources() -> settings.Resources:
        return settings.load(conn, "resources")

    @app.put("/v1/settings/resources", dependencies=[Depends(require_token)])
    def put_resources(body: settings.Resources) -> settings.Resources:
        settings.save(conn, "resources", body)
        if queue:
            queue.wake()
        return body

    def need_queue() -> JobQueue:
        if queue is None:
            raise HTTPException(status_code=503, detail="job queue not running")
        return queue

    @app.get("/v1/jobs/stats", dependencies=[Depends(require_token)])
    def job_stats() -> schemas.JobStats:
        out = handlers.stats(conn)
        out["queue"] = queue.state() if queue else None
        return out

    @app.post("/v1/jobs/pause", dependencies=[Depends(require_token)])
    def pause_jobs() -> schemas.Paused:
        need_queue().set_paused(True)
        return {"paused": True}

    @app.post("/v1/jobs/resume", dependencies=[Depends(require_token)])
    def resume_jobs() -> schemas.Paused:
        need_queue().set_paused(False)
        return {"paused": False}

    @app.post("/v1/jobs/retry", dependencies=[Depends(require_token)])
    def retry_jobs() -> schemas.Requeued:
        return {"requeued": need_queue().retry_failed()}

    # ---------------------------------------------------------------- sources (B6)
    def need_scanner() -> scan_mod.Scanner:
        if scanner is None:
            raise HTTPException(status_code=503, detail="scanner not running")
        return scanner

    def start_scan(source_id: int) -> int:
        opts = settings.load(conn, "scan")
        try:
            return need_scanner().start(source_id, include_online_only=opts.include_online_only)
        except scan_mod.SourceError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    run_keys = (
        "id",
        "kind",
        "status",
        "files_seen",
        "files_new",
        "files_changed",
        "files_missing",
        "started_at",
        "finished_at",
        "last_error",
    )

    @app.get("/v1/sources", dependencies=[Depends(require_token)])
    def list_sources() -> list[schemas.Source]:
        running = set(scanner.running()) if scanner else set()
        watching = set(watcher.watched()) if watcher else set()
        out = []
        for sid, uid, kind, name, root, status, last_scan, last_error in conn.execute(
            "SELECT id, uid, kind, display_name, root_path, status, last_scan_at, last_error FROM sources "
            "WHERE status != 'removed' ORDER BY id"
        ).fetchall():
            files = dict(
                conn.execute(
                    "SELECT state, count(*) FROM file_locations WHERE source_id = ? GROUP BY state", (sid,)
                ).fetchall()
            )
            run = conn.execute(
                f"SELECT {', '.join(run_keys)} FROM scan_runs WHERE source_id = ? ORDER BY id DESC LIMIT 1", (sid,)
            ).fetchone()
            out.append(
                {
                    "id": sid,
                    "uid": uid,
                    "kind": kind,
                    "display_name": name,
                    "root_path": root,
                    "status": status,
                    "last_scan_at": last_scan,
                    "last_error": last_error,
                    "files": files,
                    "scanning": sid in running,
                    "watching": sid in watching,
                    "last_scan": dict(zip(run_keys, run, strict=True)) if run else None,
                }
            )
        return out

    @app.post("/v1/sources", status_code=201, dependencies=[Depends(require_token)])
    def add_source(body: NewSource) -> schemas.SourceAdded:
        try:
            sid = scan_mod.add_source(conn, body.path, need_scanner().library_dir, body.display_name)
        except scan_mod.SourceError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        run_id = start_scan(sid)
        if watcher:
            watcher.add(sid)
        return {"id": sid, "scan_run_id": run_id}

    @app.post("/v1/sources/{source_id}/scan", dependencies=[Depends(require_token)])
    def scan_source(source_id: int) -> schemas.ScanStarted:
        return {"scan_run_id": start_scan(source_id)}

    @app.post("/v1/sources/{source_id}/scan/cancel", dependencies=[Depends(require_token)])
    def cancel_scan(source_id: int) -> schemas.ScanCancelled:
        return {"cancelled": need_scanner().cancel(source_id)}

    @app.get("/v1/sources/{source_id}/removal", dependencies=[Depends(require_token)])
    def removal_preview(source_id: int) -> schemas.RemovalPreview:
        try:
            return scan_mod.removal_preview(conn, source_id)
        except scan_mod.SourceError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.delete("/v1/sources/{source_id}", dependencies=[Depends(require_token)])
    def remove_source(source_id: int, confirm: int = Query(ge=0)) -> schemas.SourceRemoved:
        """confirm = the assets_only_here count from GET .../removal that the user agreed to."""
        sc = need_scanner()
        try:
            scan_mod.removal_preview(conn, source_id)
        except scan_mod.SourceError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if watcher:
            watcher.remove(source_id)
        sc.forget(source_id)
        try:
            with db_lock:
                return scan_mod.remove_source(conn, source_id, sc.library_dir, confirm)
        except scan_mod.SourceError as e:
            if watcher:  # not removed after all: keep watching it
                watcher.add(source_id)
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/v1/settings/scan", dependencies=[Depends(require_token)])
    def get_scan_options() -> settings.ScanOptions:
        return settings.load(conn, "scan")

    @app.put("/v1/settings/scan", dependencies=[Depends(require_token)])
    def put_scan_options(body: settings.ScanOptions) -> settings.ScanOptions:
        settings.save(conn, "scan", body)
        return body

    return app


def _install_dev_thumbs(app: FastAPI, conn: sqlite3.Connection, token: str) -> None:
    """Development only (never in the packaged exe): serves thumbnails over HTTP so the UI can show them
    in a plain browser, where the Tauri asset protocol does not exist. <img> cannot send a header, so the
    token travels as ?t=. Only files inside <library>/thumbs are served."""
    library_dir = Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent
    thumbs = (library_dir / "thumbs").resolve()

    @app.get("/dev/thumbs/{rel_path:path}", include_in_schema=False)
    def dev_thumb(rel_path: str, t: str = "") -> FileResponse:
        if not hmac.compare_digest(t.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing token")
        path = (library_dir / rel_path).resolve()
        if not path.is_relative_to(thumbs) or not path.is_file():
            raise HTTPException(status_code=404, detail="no such thumbnail")
        return FileResponse(path, media_type="image/webp")
