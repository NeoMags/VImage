"""Entry point: `ivsidecar --library DIR [--port 0]`, token from env IV_TOKEN.

If IV_PARENT_PID is set, exits when that process ends. Prints one JSON line `{"event": "ready", "port": N}` on stdout once listening, so the
desktop shell can read the port. Binds to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

import uvicorn

from . import db
from .app import create_app
from .watchdog import watch_parent


def default_library_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "ImageVault"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ivsidecar")
    parser.add_argument("--library", type=Path, default=Path(os.environ.get("IV_LIBRARY_DIR") or default_library_dir()))
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    args = parser.parse_args(argv)

    token = os.environ.get("IV_TOKEN", "")
    if len(token) < 16:
        print(json.dumps({"event": "error", "error": "IV_TOKEN env var missing or shorter than 16 chars"}), flush=True)
        return 2

    started = time.time()
    parent = os.environ.get("IV_PARENT_PID")
    if parent and parent.isdigit():
        watch_parent(int(parent))
    conn = db.open_library(args.library)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(128)  # accept connections before announcing readiness
    port = sock.getsockname()[1]

    config = uvicorn.Config(create_app(conn, token, started), log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    print(json.dumps({"event": "ready", "port": port, "startup_ms": round((time.time() - started) * 1000)}), flush=True)
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    sys.exit(main())
