"""Duplicate finder routes (D1). The logic lives in duplicates.py.

GET  /v1/duplicates?tab=copies|similar   open groups, newest first, suggested keeper first
GET  /v1/duplicates:summary              counts for the sidebar badge, and whether a scan runs
POST /v1/duplicates/{id}/resolve         {keep?: [ids]} keep those (default: suggested), hide the rest
POST /v1/duplicates/{id}/dismiss         "Not duplicates", remembered
POST /v1/duplicates:resolve-all          {tab} keep the suggested photo in every open group
POST /v1/duplicates:rescan               rebuild open groups in the background (202)
GET/PUT /v1/settings/duplicates          thresholds

Nothing here deletes or moves a file: "hide" is VImage's own flag, undone from the Hidden view.
"""

from __future__ import annotations

import logging
import sqlite3
import threading

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import duplicates as dup
from . import library as lib
from . import schemas as S
from . import settings
from .events import bus

log = logging.getLogger("ivsidecar.duplicates")


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keep: list[int] | None = Field(default=None, min_length=1, max_length=500)


class ResolveAllBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tab: S.DuplicateTab


def build_router(conn: sqlite3.Connection, db_lock: threading.RLock) -> APIRouter:
    r = APIRouter(prefix="/v1", tags=["duplicates"])
    scan = {"thread": None, "error": None}
    scan_lock = threading.Lock()

    def write(fn, *args):
        with db_lock:
            try:
                with lib.tx(conn):
                    out = fn(conn, *args)
            except LookupError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e
            except dup.GroupError as e:
                raise HTTPException(status_code=409, detail=str(e)) from e
            except lib.Invalid as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        bus.changed("duplicates")
        return out

    def scanning() -> bool:
        t = scan["thread"]
        return t is not None and t.is_alive()

    @r.get("/duplicates")
    def list_groups(
        tab: S.DuplicateTab = "copies",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> S.DuplicateGroups:
        with db_lock:
            return dup.list_groups(conn, tab, offset, limit)

    @r.get("/duplicates:summary")
    def summary() -> S.DuplicateSummary:
        with db_lock:
            out = dup.summary(conn)
        return {**out, "scanning": scanning(), "scan_error": scan["error"]}

    @r.post("/duplicates/{group_id}/resolve")
    def resolve(group_id: int, body: ResolveBody | None = None) -> S.DuplicateResolved:
        out = write(dup.resolve, group_id, body.keep if body else None)
        bus.asset_updated(*out["kept"], *out["hidden"])
        return out

    @r.post("/duplicates/{group_id}/dismiss")
    def dismiss(group_id: int) -> S.DuplicateDismissed:
        return write(dup.dismiss, group_id)

    @r.post("/duplicates:resolve-all")
    def resolve_all(body: ResolveAllBody) -> S.DuplicatesResolvedAll:
        out = write(dup.resolve_all, body.tab)
        bus.asset_updated_all()
        return out

    def run_scan() -> None:
        try:
            res = dup.rescan(conn, db_lock, lambda d, t: bus.task_progress("duplicates", d, t))
            log.info("duplicate scan: %s", res)
            scan["error"] = None
        except Exception as e:  # noqa: BLE001 - shown in the UI
            log.exception("duplicate scan failed")
            scan["error"] = str(e)[:500]
        bus.changed("duplicates")

    @r.post("/duplicates:rescan", status_code=202)
    def rescan() -> S.DuplicateSummary:
        with scan_lock:
            if not scanning():
                scan["thread"] = threading.Thread(target=run_scan, daemon=True, name="iv-duplicates")
                scan["thread"].start()
        return summary()

    @r.get("/settings/duplicates")
    def get_settings() -> settings.DuplicateOptions:
        with db_lock:
            return settings.load(conn, "duplicates")

    @r.put("/settings/duplicates")
    def put_settings(body: settings.DuplicateOptions) -> settings.DuplicateOptions:
        with db_lock:
            settings.save(conn, "duplicates", body)
        bus.changed("duplicates")
        return body

    r.state = scan  # for tests
    return r
