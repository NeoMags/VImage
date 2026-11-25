"""B19 routes: backups, restore and export (doc 04 §6)."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import backups, settings
from . import export as export_mod
from . import library as lib
from . import schemas as S
from .events import bus
from .jobqueue import JobQueue
from .scanner import Scanner
from .watcher import FolderWatcher

log = logging.getLogger(__name__)
DRAIN_S = 60.0  # how long a restore waits for running jobs to finish


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    path: str | None = Field(default=None, min_length=1, max_length=4096)


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "csv"]
    path: str = Field(min_length=1, max_length=4096)
    include_hidden: bool = False


class LibraryState:
    """Engine-wide facts the backup routes share with /v1/health."""

    def __init__(self, needs_restore: str | None = None):
        self.needs_restore = needs_restore


def build_router(
    conn: sqlite3.Connection,
    db_lock: threading.RLock,
    state: LibraryState,
    queue: JobQueue | None = None,
    scanner: Scanner | None = None,
    watcher: FolderWatcher | None = None,
) -> APIRouter:
    r = APIRouter(prefix="/v1")
    library_dir = lib.library_dir(conn)
    busy = threading.Lock()  # one backup, restore or export at a time

    @contextmanager
    def exclusive():
        if not busy.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="a backup, restore or export is already running")
        try:
            yield
        finally:
            busy.release()

    @r.get("/backups")
    def list_backups() -> S.Backups:
        return {
            "backups": backups.list_backups(library_dir),
            "folder": str(backups.backup_dir(library_dir)),
            "needs_restore": state.needs_restore,
        }

    @r.post("/backups", status_code=201)
    def back_up_now() -> S.BackupMade:
        with exclusive():
            try:
                return backups.make_backup(library_dir, "manual")
            except backups.BackupError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    @r.get("/settings/backup")
    def get_backup_settings() -> settings.BackupOptions:
        return settings.load(conn, "backup")

    @r.put("/settings/backup")
    def put_backup_settings(body: settings.BackupOptions) -> settings.BackupOptions:
        if body.copy_to is not None:
            folder = Path(body.copy_to)
            if not folder.is_absolute() or not folder.is_dir():
                raise HTTPException(status_code=422, detail=f"folder not found: {body.copy_to}")
            if backups.inside_a_source(conn, folder):
                raise HTTPException(
                    status_code=422, detail="the backup copy folder cannot be inside a photo source folder"
                )
        settings.save(conn, "backup", body)
        return body

    @r.post("/backups:restore")
    def restore(body: RestoreBody) -> S.Restored:
        with exclusive():
            try:
                file = backups.resolve(library_dir, body.name, body.path)
                version = backups.validate(file)
            except backups.BackupError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
            keep = {g: settings.load(conn, g) for g in ("backup", "queue", "resources", "scan")}
            # 1. Quiet everything that writes: no new jobs, no scans, no watches.
            if queue:
                queue.set_paused(True)
                deadline = time.monotonic() + DRAIN_S
                while queue.state()["busy"] and time.monotonic() < deadline:
                    time.sleep(0.1)
                if queue.state()["busy"]:
                    queue.set_paused(keep["queue"].paused)
                    raise HTTPException(status_code=409, detail="processing did not stop in time; try again")
            if watcher:
                watcher.unwatch_all()
            if scanner:
                for sid in scanner.running():
                    scanner.cancel(sid)
            try:
                # 2. A safety copy of what is about to be replaced (skipped for an empty, fresh library).
                pre = backups.make_backup(library_dir, "pre-restore")
                # 3. Replace the contents in place, migrate, and keep this PC's settings.
                with db_lock:
                    backups.restore_into(conn, file, library_dir)
                    for g, v in keep.items():
                        settings.save(conn, g, v)
                    if queue:
                        queue.recover()
                    repaired = backups.repair_derivatives(conn, library_dir)
                state.needs_restore = None
                log.info("restored %s (schema %d); safety copy %s", file, version, pre["backup"]["name"])
            except backups.BackupError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e
            finally:
                # 4. Resume: watch what the database lists now, reconcile, process.
                if watcher:
                    watcher.rewatch_all()
                if queue:
                    queue.set_paused(keep["queue"].paused)
                bus.library_restored()
            return {
                "restored_from": file.name,
                "schema_version": version,
                "pre_restore": pre["backup"]["name"],
                "thumbnails_requeued": repaired,
            }

    @r.post("/export")
    def export(body: ExportBody) -> S.Exported:
        with exclusive():
            try:
                path = export_mod.check_destination(conn, Path(body.path), body.format)
            except export_mod.ExportError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
            # Its own connection: a long read must not hold the shared one (WAL: a consistent snapshot).
            ro = sqlite3.connect(f"{(library_dir / 'library.db').resolve().as_uri()}?mode=ro", uri=True)
            try:
                ro.execute("BEGIN")
                return export_mod.export(
                    ro, path, body.format, body.include_hidden, progress=lambda d, t: bus.task_progress("export", d, t)
                )
            except OSError as e:
                raise HTTPException(status_code=409, detail=f"could not write the export: {e}") from e
            finally:
                ro.close()

    return r
