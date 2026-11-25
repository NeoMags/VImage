"""Write the API contract for the UI without starting a server (B9).

    uv run python -m ivsidecar.openapi ../../packages/shared-types

writes `openapi.json` (HTTP routes) and `events.sample.json` (one real message of every kind
from `WS /v1/events`). `packages/shared-types` generates TypeScript from the first and tests its
message parser against the second. CI regenerates both and fails when the committed copies
differ, so the UI never builds against a stale contract.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from . import db, events
from .app import create_app


def spec() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.open_library(Path(tmp) / "Library")
        try:
            return create_app(conn, "x" * 32, 0.0).openapi()
        finally:
            conn.close()


def render() -> str:
    return json.dumps(spec(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def events_sample() -> str:
    b = events.EventBus()
    b._subs[0] = (None, None)  # counts as connected, so publishing records changes; nothing is sent
    b.asset_updated(3, 1)
    b.job_updated(2)
    b.scan_progress(
        1, run_id=7, kind="full", status="running", files_seen=10, files_new=9, files_changed=1, files_missing=0
    )
    b.source_status(2, "unreachable")
    b.changed("tags")
    b.changed("collections")
    msgs = [b.hello(), {"type": "batch", "events": b._take()}]
    b.asset_updated_all()
    msgs.append({"type": "batch", "events": b._take()})
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    q.put_nowait({})
    events._offer(q, {"type": "batch", "events": []})  # a window that fell behind gets "resync"
    msgs.append(q.get_nowait())
    return json.dumps(msgs, indent=2) + "\n"


FILES = {"openapi.json": render, "events.sample.json": events_sample}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m ivsidecar.openapi <packages/shared-types>", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    for name, make in FILES.items():
        (out / name).write_text(make(), encoding="utf-8", newline="\n")
        print(f"wrote {out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
