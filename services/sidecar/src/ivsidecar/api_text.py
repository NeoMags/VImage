"""Text in photos (K2).

GET    /v1/ocr                          progress: read, waiting, failed, with text, edited
POST   /v1/ocr:rerun                    read every photo again (your edits are kept)
GET    /v1/settings/ocr, PUT            {enabled}; on queues every unread photo, off drops queued OCR
GET    /v1/assets/{id}/text             the text, where it came from, and its line boxes
PUT    /v1/assets/{id}/text             {text}: your version; later OCR runs keep it
DELETE /v1/assets/{id}/text/edit        drop your edit, use the read text again
POST   /v1/assets/{id}/text:rerun       read this photo again now
"""

from __future__ import annotations

import sqlite3
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import handlers, settings
from . import library as lib
from . import schemas as S
from . import text as T
from .events import bus
from .jobqueue import JobQueue


class TextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=T.MAX_TEXT)


def build_router(conn: sqlite3.Connection, db_lock: threading.RLock, queue: JobQueue | None = None) -> APIRouter:
    r = APIRouter(prefix="/v1")

    def run(fn, *args, write: bool = False, asset: int | None = None):
        with db_lock:
            try:
                if write:
                    with lib.tx(conn):
                        out = fn(conn, *args)
                else:
                    out = fn(conn, *args)
            except lib.NotFound as e:
                raise HTTPException(status_code=404, detail=str(e)) from e
            except lib.Invalid as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        if asset is not None:
            bus.asset_updated(asset)
        return out

    def wake() -> None:
        if queue is not None:
            queue.wake()
        bus.changed("ocr")

    @r.get("/ocr")
    def ocr_status() -> S.OcrStatus:
        return run(T.status)

    @r.post("/ocr:rerun", status_code=202)
    def ocr_rerun() -> S.OcrStatus:
        """Read the text of every photo again (e.g. after an OCR update). Text you edited is kept."""
        if not run(handlers.ocr_enabled):
            raise HTTPException(status_code=409, detail="reading text is switched off in Settings")
        run(handlers.enqueue_ocr_backfill, True, write=True)
        wake()
        return run(T.status)

    @r.get("/settings/ocr")
    def get_ocr_settings() -> settings.OcrOptions:
        return run(settings.load, "ocr")

    @r.put("/settings/ocr")
    def put_ocr_settings(body: settings.OcrOptions) -> settings.OcrOptions:
        def go(c):
            was = settings.load(c, "ocr").enabled
            settings.save(c, "ocr", body)
            if body.enabled and not was:
                handlers.enqueue_ocr_backfill(c)
            elif was and not body.enabled:
                handlers.cancel_queued_ocr(c)

        run(go, write=True)
        wake()
        return body

    @r.get("/assets/{asset_id}/text")
    def get_text(asset_id: int) -> S.AssetText:
        return run(T.get_text, asset_id)

    @r.put("/assets/{asset_id}/text")
    def put_text(asset_id: int, body: TextBody) -> S.AssetText:
        run(T.set_text, asset_id, body.text, write=True, asset=asset_id)
        return run(T.get_text, asset_id)

    @r.delete("/assets/{asset_id}/text/edit")
    def revert(asset_id: int) -> S.AssetText:
        run(T.revert_text, asset_id, write=True, asset=asset_id)
        return run(T.get_text, asset_id)

    @r.post("/assets/{asset_id}/text:rerun", status_code=202)
    def rerun(asset_id: int) -> S.AssetText:
        run(T.read_again, asset_id, write=True, asset=asset_id)
        wake()
        return run(T.get_text, asset_id)

    return r
