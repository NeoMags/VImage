"""Engine status routes (B8b, doc 04 §1-2): error format, jobs, stats, live events."""

from __future__ import annotations

import asyncio
import hmac
import sqlite3
import threading

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, handlers
from .events import bus
from .jobqueue import JobQueue

# ---------------------------------------------------------------------------- errors
CODES = {
    400: "validation_failed",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media",
    422: "validation_failed",
    503: "busy",
}
TITLES = {
    "validation_failed": "Invalid request",
    "unauthorized": "Missing or invalid token",
    "forbidden": "Not allowed",
    "not_found": "Not found",
    "method_not_allowed": "Method not allowed",
    "conflict": "Conflict",
    "unsupported_media": "Unsupported media",
    "busy": "Engine busy",
    "internal": "Internal error",
}


def problem(status: int, detail, code: str | None = None, **extra) -> JSONResponse:
    code = code or CODES.get(status, "internal" if status >= 500 else "error")
    body = {
        "type": f"https://imagevault.local/problems/{code}",
        "title": TITLES.get(code, "Error"),
        "status": status,
        "code": code,
        "detail": detail,
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    """Every error, from every route, as doc 04 §1 problem+json: {type, title, status, code, detail}."""

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_req: Request, exc: StarletteHTTPException):
        resp = problem(exc.status_code, exc.detail)
        for k, v in (exc.headers or {}).items():
            resp.headers[k] = v
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_error(_req: Request, exc: RequestValidationError):
        errors = [{"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg", "")} for e in exc.errors()]
        summary = "; ".join(f"{'.'.join(e['loc'][1:]) or e['loc'][0]}: {e['msg']}" for e in errors[:5])
        return problem(422, summary or "invalid request", errors=errors)

    @app.exception_handler(Exception)
    async def unexpected(_req: Request, exc: Exception):  # never leak a stack trace to the UI
        import logging

        logging.getLogger("ivsidecar.api").exception("unhandled error")
        return problem(500, f"{type(exc).__name__}", code="internal")


# ---------------------------------------------------------------------------- routes
JOB_KEYS = (
    "id stage status priority attempts crashes asset_id location_id path last_error run_after started_at finished_at"
).split()


def build_router(
    conn: sqlite3.Connection, lock: threading.RLock, token: str, origins: list[str], queue: JobQueue | None
) -> tuple[APIRouter, APIRouter]:
    """Returns (routes that need the bearer header, the WebSocket route that checks it itself)."""
    r = APIRouter(prefix="/v1")

    @r.get("/jobs")
    def list_jobs(
        status: str | None = Query(None, pattern="^(queued|running|succeeded|failed|skipped|dead)$"),
        stage: str | None = Query(None, max_length=20),
        limit: int = Query(100, ge=1, le=500),
        before_id: int | None = Query(None, ge=1, description="paging: the last id of the previous page"),
    ) -> dict:
        where, args = [], []
        if status:
            where.append("j.status = ?")
            args.append(status)
        if stage:
            where.append("j.stage = ?")
            args.append(stage)
        if before_id:
            where.append("j.id < ?")
            args.append(before_id)
        sql = (
            "SELECT j.id, j.stage, j.status, j.priority, j.attempts, j.crashes, j.asset_id, j.location_id, "
            "coalesce(l.rel_path, (SELECT rel_path FROM file_locations x WHERE x.asset_id = j.asset_id "
            " ORDER BY x.state != 'present', x.id LIMIT 1)), "
            "j.last_error, j.run_after, j.started_at, j.finished_at FROM jobs j "
            "LEFT JOIN file_locations l ON l.id = j.location_id"
            + (f" WHERE {' AND '.join(where)}" if where else "")
            + " ORDER BY j.id DESC LIMIT ?"
        )
        with lock:
            rows = conn.execute(sql, (*args, limit + 1)).fetchall()
        items = [dict(zip(JOB_KEYS, row, strict=True)) for row in rows[:limit]]
        return {"items": items, "next_before_id": items[-1]["id"] if len(rows) > limit else None}

    @r.post("/jobs/{job_id}/retry")
    def retry_job(job_id: int) -> dict:
        with lock:
            row = conn.execute("SELECT status, asset_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"job {job_id} not found")
            if row[0] not in ("failed", "dead", "skipped"):
                raise HTTPException(
                    status_code=409, detail=f"job {job_id} is {row[0]}; only failed jobs can be retried"
                )
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE jobs SET status = 'queued', attempts = 0, crashes = 0, last_error = NULL, "
                    f"run_after = {handlers.NOW}, started_at = NULL, finished_at = NULL WHERE id = ?",
                    (job_id,),
                )
                if row[1] is not None:
                    conn.execute(
                        "UPDATE assets SET status = 'processing', error = NULL WHERE id = ? AND status = 'failed'",
                        (row[1],),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        if queue:
            queue.wake()
        bus.job_updated()
        if row[1] is not None:
            bus.asset_updated(row[1])
        return {"id": job_id, "status": "queued"}

    @r.get("/stats")
    def stats() -> dict:
        with lock:
            return library_stats(conn, queue)

    ws = APIRouter(prefix="/v1")

    @ws.websocket("/events")
    async def events_socket(sock: WebSocket):
        # Browsers cannot set an Authorization header on a WebSocket: the token comes as the
        # subprotocol "bearer.<token>" next to "ivsidecar" (doc 04 §1, B8 plan).
        offered = [p.strip() for p in sock.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
        ok = any(p.startswith("bearer.") and hmac.compare_digest(p[7:].encode(), token.encode()) for p in offered)
        origin = sock.headers.get("origin")
        if not ok or (origin is not None and origin not in origins) or "ivsidecar" not in offered:
            await sock.close(code=1008)  # before accept: the handshake is refused (HTTP 403)
            return
        await sock.accept(subprotocol="ivsidecar")
        sid, q = bus.subscribe(asyncio.get_running_loop())
        try:
            await sock.send_json({"type": "hello", "flush_ms": int(bus.flush_s * 1000)})
            receiver = asyncio.create_task(sock.receive_text())  # notices the window closing
            while True:
                getter = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    getter.cancel()
                    try:
                        receiver.result()
                    except WebSocketDisconnect:
                        return
                    receiver = asyncio.create_task(sock.receive_text())  # client messages are ignored
                    if getter.cancelled():
                        continue
                if getter in done:
                    await sock.send_json(getter.result())
        except WebSocketDisconnect:
            return
        finally:
            bus.unsubscribe(sid)

    return r, ws


def library_stats(conn: sqlite3.Connection, queue: JobQueue | None) -> dict:
    by_status = dict(conn.execute("SELECT status, count(*) FROM assets WHERE is_hidden = 0 GROUP BY status").fetchall())
    by_kind = dict(conn.execute("SELECT kind, count(*) FROM assets WHERE is_hidden = 0 GROUP BY kind").fetchall())
    present = conn.execute(
        "SELECT count(*) FROM assets a WHERE EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.id "
        "AND l.state = 'present')"
    ).fetchone()[0]
    unreachable = conn.execute(
        "SELECT count(*) FROM assets a WHERE NOT EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.id "
        "AND l.state = 'present') AND EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.id "
        "AND l.state = 'unreachable')"
    ).fetchone()[0]
    total = conn.execute("SELECT count(*) FROM assets").fetchone()[0]
    sources = dict(conn.execute("SELECT status, count(*) FROM sources GROUP BY status").fetchall())
    last_scan = conn.execute("SELECT max(finished_at) FROM scan_runs WHERE status = 'succeeded'").fetchone()[0]
    return {
        "photos": {
            "total": total,
            "by_status": by_status,
            "by_kind": by_kind,
            "hidden": conn.execute("SELECT count(*) FROM assets WHERE is_hidden = 1").fetchone()[0],
            "favorites": conn.execute("SELECT count(*) FROM assets WHERE is_favorite = 1").fetchone()[0],
            "present": present,
            "unreachable": unreachable,
            "missing": total - present - unreachable,
        },
        "files_unsupported": conn.execute(
            "SELECT count(*) FROM jobs WHERE stage = 'identify' AND status = 'skipped'"
        ).fetchone()[0],
        "tags": conn.execute("SELECT count(*) FROM tags").fetchone()[0],
        "collections": conn.execute("SELECT count(*) FROM collections").fetchone()[0],
        "sources": sources,
        "last_scan_at": last_scan,
        "jobs": handlers.stats(conn)["jobs"],
        "queue": queue.state() if queue else None,
        "db_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "db_latest": db.latest_version(),
    }
