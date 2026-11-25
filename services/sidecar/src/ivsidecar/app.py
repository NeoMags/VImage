"""Localhost API (doc 04). Every route requires the per-launch bearer token."""

from __future__ import annotations

import hmac
import platform
import sqlite3
import sys
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, probes, settings

# Origins used by Tauri 2 webviews (Windows uses http://tauri.localhost).
TAURI_ORIGINS = ["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"]


def create_app(conn: sqlite3.Connection, token: str, started_at: float) -> FastAPI:
    app = FastAPI(
        title="ImageVault sidecar", version=__version__, docs_url=None, redoc_url=None, openapi_url="/v1/openapi.json"
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=TAURI_ORIGINS,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def require_token(authorization: str = Header(default="")) -> None:
        expected = f"Bearer {token}"
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing token")

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
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

    @app.get("/v1/settings/resources", dependencies=[Depends(require_token)])
    def get_resources() -> settings.Resources:
        return settings.load(conn, "resources")

    @app.put("/v1/settings/resources", dependencies=[Depends(require_token)])
    def put_resources(body: settings.Resources) -> settings.Resources:
        settings.save(conn, "resources", body)
        return body

    return app
