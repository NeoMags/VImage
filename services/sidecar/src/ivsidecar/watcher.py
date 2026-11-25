"""Live folder watching and periodic reconcile (doc 05 §2-3, B7).

The watcher only says *which folders* to look at again; the scanner decides what changed by
listing them (Scan.run_batch), with the same identity, exclusion and "never open unchanged
files" rules as a full scan. So a missed, doubled or reordered event costs a re-list, never a
wrong library.

* Events are collected per source and flushed 2 s after the last one (a copy of 500 photos is
  one batch, not 500).
* The OS dropped events (watchdog EventsLostEvent, Windows buffer overflow) or a batch of more
  than 500 folders: a full scan of the source instead.
* Every 30 s each source's folder is checked: an unplugged drive makes the source
  'unreachable'; when it is back (possibly under another drive letter) the watch is re-created
  and a full scan runs.
* A full scan of every source at startup (changes made while the app was closed) and every 6 h
  (anything the watcher missed).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from watchdog.events import EventsLostEvent, FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from .scanner import Scanner, SourceError

log = logging.getLogger("ivsidecar.watcher")

IGNORED_EVENTS = {"opened", "closed", "closed_no_write", "attrib"}


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: FolderWatcher, source_id: int, root: str):
        self.watcher, self.source_id, self.root = watcher, source_id, root

    def on_any_event(self, event: FileSystemEvent) -> None:
        try:
            self.watcher._on_event(self.source_id, self.root, event)
        except Exception:  # noqa: BLE001 - never kill the observer thread
            log.exception("watch event handling failed: %r", event)


class FolderWatcher:
    def __init__(
        self,
        library_dir: Path,
        scanner: Scanner,
        debounce_s: float = 2.0,
        max_dirs: int = 500,
        young_s: float = 2.0,
        young_give_up_s: float = 60.0,
        reach_every_s: float = 30.0,
        reconcile_every_s: float = 6 * 3600,
    ):
        self.library_dir = library_dir.resolve()
        self.scanner = scanner
        self.debounce_s = debounce_s
        self.max_dirs = max_dirs
        self.young_s = young_s
        self.young_give_up_s = young_give_up_s
        self.reach_every_s = reach_every_s
        self.reconcile_every_s = reconcile_every_s
        self._observer = Observer()
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._watches: dict[int, tuple[object, str]] = {}  # source id -> (ObservedWatch, root)
        self._pending: dict[int, dict[str, bool]] = {}  # source id -> {rel dir: recursive}
        self._last_event: dict[int, float] = {}
        self._first_deferred: dict[tuple[int, str], float] = {}
        self._full: set[int] = set()  # needs a full scan (events lost / too many folders)
        self._lost: set[int] = set()  # root folder deleted or unmounted: watch is dead
        self._threads: list[threading.Thread] = []
        self.batches = 0  # for tests and stats

    # ------------------------------------------------------------------ lifecycle
    def start(self, reconcile_now: bool = True) -> None:
        self._observer.start()
        for sid in self._source_ids():
            if self.scanner.check_reachable(sid) == "active":
                self.add(sid)
        for target, name in ((self._flush_loop, "iv-watch-flush"), (self._housekeeping_loop, "iv-watch-house")):
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            self._threads.append(t)
        if reconcile_now:
            self.reconcile_all()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        for t in self._threads:
            t.join(10)
        try:
            self._observer.stop()
            self._observer.join(10)
        except Exception:  # noqa: BLE001
            log.exception("stopping the folder observer failed")

    def _conn(self):
        return db.connect(self.library_dir / "library.db")

    def _source_ids(self, statuses: tuple[str, ...] = ("active", "unreachable")) -> list[int]:
        conn = self._conn()
        try:
            marks = ",".join("?" for _ in statuses)
            return [
                r[0]
                for r in conn.execute(
                    f"SELECT id FROM sources WHERE watch = 1 AND status IN ({marks}) ORDER BY id", statuses
                )
            ]
        finally:
            conn.close()

    # ------------------------------------------------------------------ watches
    def add(self, source_id: int) -> bool:
        """Watch a source's folder (no-op if already watched at the same path)."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT root_path, status, watch FROM sources WHERE id = ?", (source_id,)).fetchone()
        finally:
            conn.close()
        if row is None or row[1] != "active" or not row[2]:
            return False
        root = row[0]
        with self._cv:
            cur = self._watches.get(source_id)
            if cur and cur[1] == root and source_id not in self._lost:
                return True
        self._unschedule(source_id)
        try:
            watch = self._observer.schedule(_Handler(self, source_id, root), root, recursive=True)
        except OSError as e:
            log.warning("cannot watch source %d (%s): %s", source_id, root, e)
            return False
        with self._cv:
            self._watches[source_id] = (watch, root)
            self._lost.discard(source_id)
        log.info("watching source %d: %s", source_id, root)
        return True

    def remove(self, source_id: int) -> None:
        """Stop watching and drop anything pending (folder removed from the library)."""
        self._unschedule(source_id)
        with self._cv:
            self._pending.pop(source_id, None)
            self._last_event.pop(source_id, None)
            self._full.discard(source_id)
            self._lost.discard(source_id)
            for k in [k for k in self._first_deferred if k[0] == source_id]:
                del self._first_deferred[k]

    def _unschedule(self, source_id: int) -> None:
        with self._cv:
            cur = self._watches.pop(source_id, None)
        if cur:
            try:
                self._observer.unschedule(cur[0])
            except Exception:  # noqa: BLE001 - an emitter that already died on its own
                log.debug("unschedule of source %d: emitter already gone", source_id)

    def unwatch_all(self) -> None:
        """Before a restore: drop every watch (the restored database may list other sources)."""
        for sid in self.watched():
            self.remove(sid)

    def rewatch_all(self) -> None:
        """After a restore: watch the sources the database now lists, then reconcile them."""
        for sid in self._source_ids():
            if self.scanner.check_reachable(sid) == "active":
                self.add(sid)
        self.reconcile_all()

    def watched(self) -> list[int]:
        with self._cv:
            return sorted(self._watches)

    # ------------------------------------------------------------------ events
    def _rel(self, root: str, path: str | bytes) -> str | None:
        p = os.fsdecode(path)
        try:
            if Path(p).resolve().is_relative_to(self.library_dir):
                return None  # our own database and thumbnails, when the library sits inside a source
        except OSError:
            pass
        rel = os.path.relpath(p, root)
        if rel == os.curdir:
            return ""
        if rel.startswith(os.pardir):
            return None
        return rel.replace(os.sep, "/")

    def _on_event(self, source_id: int, root: str, event: FileSystemEvent) -> None:
        if isinstance(event, EventsLostEvent):
            log.warning("source %d: the OS dropped file events; a full scan will follow", source_id)
            self._mark(source_id, full=True)
            return
        if event.event_type in IGNORED_EVENTS:
            return
        if event.is_directory and event.event_type == "modified":
            return  # a folder's own timestamp; the file events say what changed
        targets: dict[str, bool] = {}
        paths = [event.src_path] + ([event.dest_path] if event.event_type == "moved" and event.dest_path else [])
        for path in paths:
            rel = self._rel(root, path)
            if rel is None:
                continue
            if event.is_directory:
                if rel == "" and event.event_type in ("deleted", "moved"):
                    with self._cv:
                        self._lost.add(source_id)  # root gone: reachability check, below
                        self._cv.notify_all()
                    continue
                targets[rel] = True  # created, deleted or moved folder: its whole subtree
            else:
                parent = rel.rpartition("/")[0]
                targets[parent] = targets.get(parent, False)
        if targets:
            self._mark(source_id, targets)

    def _mark(self, source_id: int, dirs: dict[str, bool] | None = None, full: bool = False) -> None:
        with self._cv:
            if full:
                self._full.add(source_id)
            pending = self._pending.setdefault(source_id, {})
            for d, rec in (dirs or {}).items():
                pending[d] = pending.get(d, False) or rec
            self._last_event[source_id] = time.monotonic()
            self._cv.notify_all()

    # ------------------------------------------------------------------ flushing
    def _due(self) -> list[int]:
        now = time.monotonic()
        return [
            sid
            for sid, t in self._last_event.items()
            if now - t >= self.debounce_s and (self._pending.get(sid) or sid in self._full)
        ]

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                self._cv.wait(timeout=min(0.5, self.debounce_s))
                due = self._due()
            for sid in due:
                if self._stop.is_set():
                    return
                try:
                    self.flush(sid)
                except Exception:  # noqa: BLE001
                    log.exception("watch batch of source %d failed", sid)
                    with self._cv:  # try again after the next debounce
                        self._last_event[sid] = time.monotonic()

    def flush(self, source_id: int) -> str:
        """Process what is pending for one source now. Returns what was done (for tests/logs)."""
        if source_id in self.scanner.running():
            with self._cv:  # a full scan is walking it; look at these folders once it ends
                self._last_event[source_id] = time.monotonic()
            return "waiting"
        with self._cv:
            dirs = self._pending.pop(source_id, {})
            full = source_id in self._full
            self._full.discard(source_id)
            self._last_event.pop(source_id, None)
        if full or len(dirs) > self.max_dirs:
            try:
                self.scanner.start(source_id, include_online_only=self._include_online_only())
            except SourceError as e:
                log.warning("full scan of source %d not started: %s", source_id, e)
                if "already running" in str(e):
                    self._mark(source_id, full=True)
            return "full"
        if not dirs:
            return "nothing"
        now = time.monotonic()
        with self._cv:
            forced = any(now - self._first_deferred.get((source_id, d), now) >= self.young_give_up_s for d in dirs)
        deferred = self.scanner.run_batch(source_id, dirs, min_age_s=0.0 if forced else self.young_s)
        self.batches += 1
        with self._cv:
            for d in dirs:
                if d not in deferred:
                    self._first_deferred.pop((source_id, d), None)
            for d in deferred:
                self._first_deferred.setdefault((source_id, d), now)
        if deferred:
            self._mark(source_id, deferred)
        return "batch"

    def _include_online_only(self) -> bool:
        conn = self._conn()
        try:
            return Scanner._include_online_only(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------------ reachability + reconcile
    def _housekeeping_loop(self) -> None:
        next_reach = time.monotonic() + self.reach_every_s
        next_reconcile = time.monotonic() + self.reconcile_every_s
        while not self._stop.is_set():
            with self._cv:
                self._cv.wait(timeout=max(0.0, min(next_reach, next_reconcile) - time.monotonic()))
                lost = bool(self._lost)
            if self._stop.is_set():
                return
            now = time.monotonic()
            try:
                if lost or now >= next_reach:
                    self.check_sources()
                    next_reach = now + self.reach_every_s
                if now >= next_reconcile:
                    self.reconcile_all()
                    next_reconcile = now + self.reconcile_every_s
            except Exception:  # noqa: BLE001
                log.exception("watch housekeeping failed")

    def check_sources(self) -> None:
        """Unplugged drives become unreachable; returning ones are watched and rescanned."""
        for sid in self._source_ids():
            try:
                status = self.scanner.check_reachable(sid)
            except SourceError:
                continue
            with self._cv:
                cur = self._watches.get(sid)
                dead = sid in self._lost
            if status != "active":
                if cur:
                    log.info("source %d unreachable: watch stopped", sid)
                    self._unschedule(sid)
                with self._cv:
                    self._lost.discard(sid)
                continue
            root = self._root(sid)
            if cur is None or dead or cur[1] != root:
                if self.add(sid):
                    self._start_full(sid)  # changes made while it was away or unwatched

    def _root(self, source_id: int) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT root_path FROM sources WHERE id = ?", (source_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _start_full(self, source_id: int) -> None:
        try:
            self.scanner.start(source_id, include_online_only=self._include_online_only())
        except SourceError as e:
            log.info("full scan of source %d not started: %s", source_id, e)

    def reconcile_all(self) -> None:
        """Full scan of every active, watched source (skips any already scanning)."""
        running = set(self.scanner.running())
        for sid in self._source_ids(("active",)):
            if sid not in running:
                self._start_full(sid)
