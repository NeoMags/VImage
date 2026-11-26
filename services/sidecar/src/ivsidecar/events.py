"""Live updates for the UI (B8b): an in-process event bus behind `WS /v1/events`.

Any thread (queue slots, scanners, API handlers) calls `bus.publish(...)`; it only records
what changed and costs next to nothing when no window is connected. Every 250 ms the
collected changes go out as ONE message per connected window, coalesced:

    {"type": "batch", "events": [
        {"type": "asset.updated", "ids": [3, 9]},          # or {"all": true} past 1000 ids
        {"type": "job.updated", "count": 42},              # jobs that finished since the last batch
        {"type": "scan.progress", "source_id": 1, ...},     # latest counters per source
        {"type": "source.status", "source_id": 1, "status": "unreachable"},
        {"type": "task.progress", "task": "export", "done": 3000, "total": 100000},
        {"type": "tags.updated"}, {"type": "collections.updated"}, {"type": "ai.updated"},
        {"type": "library.restored"}]}                    # a backup replaced the library: refetch all

A large scan therefore sends at most 4 messages a second, not one per file. Events are hints:
the UI refetches what it shows, and after a reconnect it refetches everything, because the
database (not this stream) is the truth. Nothing is persisted or replayed.
"""

from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger("ivsidecar.events")

FLUSH_S = 0.25
MAX_IDS = 1000
QUEUE_MAX = 64  # batches a slow window may fall behind before it is told to refetch everything


class EventBus:
    def __init__(self, flush_s: float = FLUSH_S):
        self.flush_s = flush_s
        self._lock = threading.Lock()
        self._subs: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._next = 0
        self._reset()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent = 0  # batches delivered (all subscribers), for tests

    def _reset(self) -> None:
        self._assets: set[int] = set()
        self._assets_all = False
        self._jobs = 0
        self._scans: dict[int, dict] = {}
        self._sources: dict[int, str] = {}
        self._flags: set[str] = set()
        self._tasks: dict[str, dict] = {}

    # ------------------------------------------------------------------ publishing (any thread)
    def _active(self) -> bool:
        return bool(self._subs)

    def asset_updated(self, *ids) -> None:
        if not self._active():
            return
        with self._lock:
            if not self._assets_all:
                self._assets.update(int(i) for i in ids if i is not None)
                if len(self._assets) > MAX_IDS:
                    self._assets_all, self._assets = True, set()
        self._wake.set()

    def asset_updated_all(self) -> None:
        if not self._active():
            return
        with self._lock:
            self._assets_all, self._assets = True, set()
        self._wake.set()

    def job_updated(self, n: int = 1) -> None:
        if not self._active():
            return
        with self._lock:
            self._jobs += n
        self._wake.set()

    def scan_progress(self, source_id: int, **counters) -> None:
        if not self._active():
            return
        with self._lock:
            self._scans[source_id] = {"source_id": source_id, **counters}
        self._wake.set()

    def source_status(self, source_id: int, status: str) -> None:
        if not self._active():
            return
        with self._lock:
            self._sources[source_id] = status
        self._wake.set()

    def task_progress(self, task: str, done: int, total: int) -> None:
        """A long request (task: 'export') is this far along; latest counters only."""
        if not self._active():
            return
        with self._lock:
            self._tasks[task] = {"task": task, "done": done, "total": total}
        self._wake.set()

    def library_restored(self) -> None:
        if not self._active():
            return
        with self._lock:
            self._flags.add("library.restored")
        self._wake.set()

    def changed(self, what: str) -> None:
        """what: 'tags', 'collections', 'ai' (model download finished, AI switched on/off) or 'ocr'
        (reading text switched on/off, or queued again)."""
        if not self._active():
            return
        with self._lock:
            self._flags.add(f"{what}.updated")
        self._wake.set()

    # ------------------------------------------------------------------ subscribers
    def subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[int, asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            self._next += 1
            sid = self._next
            self._subs[sid] = (loop, q)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._flusher, daemon=True, name="iv-events")
                self._thread.start()
        return sid, q

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subs.pop(sid, None)

    def hello(self) -> dict:
        """First message on every connection."""
        return {"type": "hello", "flush_ms": int(self.flush_s * 1000)}

    def subscribers(self) -> int:
        return len(self._subs)

    # ------------------------------------------------------------------ flushing
    def _take(self) -> list[dict]:
        with self._lock:
            events: list[dict] = []
            if self._assets_all:
                events.append({"type": "asset.updated", "all": True})
            elif self._assets:
                events.append({"type": "asset.updated", "ids": sorted(self._assets)})
            if self._jobs:
                events.append({"type": "job.updated", "count": self._jobs})
            events += [{"type": "scan.progress", **v} for v in self._scans.values()]
            events += [{"type": "source.status", "source_id": k, "status": v} for k, v in self._sources.items()]
            events += [{"type": "task.progress", **v} for v in self._tasks.values()]
            events += [{"type": f} for f in sorted(self._flags)]
            self._reset()
            return events

    def flush(self) -> int:
        """Send what was collected (normally called by the flusher thread). Returns subscribers reached."""
        events = self._take()
        if not events:
            return 0
        msg = {"type": "batch", "events": events}
        with self._lock:
            subs = list(self._subs.values())
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, msg)
            except RuntimeError:  # its event loop has closed; the socket handler unsubscribes
                continue
        self.sent += len(subs)
        return len(subs)

    def _flusher(self) -> None:
        while True:
            self._wake.wait()
            with self._lock:
                if not self._subs:
                    self._wake.clear()
                    self._reset()
                    if not self._subs:
                        self._thread = None
                        return
            threading.Event().wait(self.flush_s)  # collect for one interval, then send once
            self._wake.clear()
            try:
                self.flush()
            except Exception:  # noqa: BLE001
                log.exception("event flush failed")


def _offer(q: asyncio.Queue, msg: dict) -> None:
    if q.full():  # a window that cannot keep up: drop its backlog, tell it to refetch everything
        while not q.empty():
            q.get_nowait()
        msg = {"type": "batch", "events": [{"type": "resync"}]}
    q.put_nowait(msg)


bus = EventBus()
