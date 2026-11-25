"""Entry point: `ivsidecar --library DIR [--port 0]`, token from env IV_TOKEN.

If IV_PARENT_PID is set, exits when that process ends. Prints one JSON line `{"event": "ready", "port": N}` on stdout once listening, so the
desktop shell can read the port. Binds to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from . import __version__, db, logs
from .app import create_app
from .jobqueue import JobQueue
from .scanner import Scanner
from .watchdog import watch_parent
from .watcher import FolderWatcher

log = logging.getLogger("ivsidecar")


def default_library_dir() -> Path:
    # Kept apart from the per-user install folder (%LOCALAPPDATA%\\ImageVault) so program files
    # and library data never mix. The desktop shell passes --library explicitly; this default
    # only matters when the sidecar is run on its own.
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "ImageVault" / "Library"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ivsidecar")
    parser.add_argument("--library", type=Path, default=Path(os.environ.get("IV_LIBRARY_DIR") or default_library_dir()))
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    parser.add_argument("--selftest-queue", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.selftest_queue:  # CI check of the packaged build; no token or library needed
        from watchdog.observers import Observer

        from .jobqueue import selftest

        result = selftest()
        result["observer"] = Observer.__name__  # the packaged build must not fall back to polling
        print(json.dumps(result), flush=True)
        return 0 if result["ok"] else 1

    token = os.environ.get("IV_TOKEN", "")
    if len(token) < 16:
        print(json.dumps({"event": "error", "error": "IV_TOKEN env var missing or shorter than 16 chars"}), flush=True)
        return 2

    started = time.time()
    logs.setup(args.library / "logs")
    log.info("starting ivsidecar %s, library %s", __version__, args.library)
    parent = os.environ.get("IV_PARENT_PID")
    if parent and parent.isdigit():
        watch_parent(int(parent))
    t_db = time.time()
    conn = db.open_library(args.library)
    db_ms = round((time.time() - t_db) * 1000)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(128)  # accept connections before announcing readiness
    port = sock.getsockname()[1]

    queue = JobQueue(args.library)
    scanner = Scanner(args.library, on_batch=queue.wake)
    watcher = FolderWatcher(args.library, scanner)
    server: uvicorn.Server | None = None

    def request_exit() -> None:  # POST /v1/shutdown: uvicorn finishes requests, then the finally below runs
        log.info("shutdown requested by the desktop shell")
        if server is not None:
            server.should_exit = True

    config = uvicorn.Config(
        create_app(conn, token, started, queue, scanner, watcher, on_shutdown=request_exit),
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    print(
        json.dumps(
            {"event": "ready", "port": port, "startup_ms": round((time.time() - started) * 1000), "db_ms": db_ms}
        ),
        flush=True,
    )
    queue.start()  # after "ready": jobs left over from last time must not delay the window
    scanner.resume_interrupted()
    # watch every folder, then a full scan of each (changes made while the app was closed). In a
    # thread: setting up watches can take a while on big trees and must not delay the API.
    threading.Thread(target=watcher.start, daemon=True, name="iv-watch-start").start()
    try:
        server.run(sockets=[sock])
    finally:
        watcher.stop()
        scanner.stop()
        queue.stop()
        conn.close()  # Windows keeps an open database file locked
        log.info("stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
