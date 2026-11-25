"""Sources and the resumable folder scanner (doc 05 §1-2, B6).

A source is a folder the user adds. A scan walks it and registers only files that are new or
changed; unchanged files are recognised from directory metadata alone and are never opened.
Nothing in a source is ever written, renamed or deleted.

File identity (`file_key`):
* NTFS/ReFS (and POSIX filesystems): volume serial + file id. Survives rename and move within
  the volume, so tags stay attached.
* exFAT/FAT32, network shares, or a file id of 0: "path:" + relative path (doc 05 §1).

Resume: the walk is depth-first over names in sorted order, so the traversal order is fixed and
one value, the last completed folder (`scan_runs.resume_token`), says exactly what is done. A
restarted scan skips folders that come before the token. (The plan said breadth-first; sorted
depth-first gives the same result with a single-path token instead of a stored queue.)
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import sqlite3
import stat as stat_mod
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import db, handlers

log = logging.getLogger("ivsidecar.scanner")

NOW = handlers.NOW

# Windows file attributes (winnt.h)
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
ONLINE_ONLY = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

SKIP_DIRS = {"$recycle.bin", "system volume information", "__macosx", "@eadir", ".thumbnails"}
SKIP_FILE_GLOBS = ("thumbs.db", "desktop.ini", ".ds_store", "~$*", "*.tmp", "*.part", "*.crdownload", "*.partial")
FILE_ID_FILESYSTEMS = {"NTFS", "REFS"}


class SourceError(ValueError):
    """A folder that cannot be added (shown to the user as is)."""


# --------------------------------------------------------------------------- volumes
def _win_volume(path: Path) -> tuple[str | None, str | None]:
    """(filesystem name, volume serial) for the volume holding `path`."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    root = ctypes.create_unicode_buffer(1024)
    if not k32.GetVolumePathNameW(str(path), root, 1024):
        return None, None
    fs = ctypes.create_unicode_buffer(64)
    serial = wintypes.DWORD()
    ok = k32.GetVolumeInformationW(root.value, None, 0, ctypes.byref(serial), None, None, fs, 64)
    return (fs.value.upper(), f"{serial.value:08x}") if ok else (None, None)


@dataclass
class VolumeInfo:
    volume_id: str | None
    use_file_ids: bool
    kind: str  # sources.kind


def volume_info(root: Path) -> VolumeInfo:
    st = root.stat()
    if sys.platform == "win32":
        unc = str(root).startswith("\\\\")
        fs, serial = _win_volume(root)
        use_ids = not unc and fs in FILE_ID_FILESYSTEMS
        return VolumeInfo(serial or f"{st.st_dev:x}", use_ids, "network_share" if unc else "local_folder")
    return VolumeInfo(f"{st.st_dev:x}", True, "local_folder")


# --------------------------------------------------------------------------- sources
def add_source(conn: sqlite3.Connection, path: str, library_dir: Path, display_name: str | None = None) -> int:
    if not path or "\x00" in path:
        raise SourceError("enter a folder path")
    root = Path(path).expanduser()
    if not root.is_absolute():
        raise SourceError("the folder path must be absolute")
    if not root.is_dir():
        raise SourceError(f"folder not found: {root}")
    try:
        with os.scandir(root):
            pass
    except OSError as e:
        raise SourceError(f"folder cannot be read: {e.strerror or e}") from e
    root = root.resolve()
    lib = library_dir.resolve()
    if root.is_relative_to(lib):  # includes root == lib. (A source containing the library is fine:
        raise SourceError("that is ImageVault's own library folder")  # the walk skips the library.)
    key = os.path.normcase(str(root))
    for (existing,) in conn.execute("SELECT root_path FROM sources WHERE status != 'removed'"):
        other = os.path.normcase(existing)
        if other == key:
            raise SourceError("that folder is already in the library")
        if Path(key).is_relative_to(other) or Path(other).is_relative_to(key):
            raise SourceError(f"that folder overlaps a folder already in the library: {existing}")
    vi = volume_info(root)
    return conn.execute(
        "INSERT INTO sources (uid, kind, display_name, root_path, volume_id) VALUES (?, ?, ?, ?, ?) RETURNING id",
        (str(uuid.uuid4()), vi.kind, display_name or root.name or str(root), str(root), vi.volume_id),
    ).fetchone()[0]


# --------------------------------------------------------------------------- walking
def _order_key(rel_dir: str) -> tuple[str, ...]:
    return tuple(rel_dir.split("/")) if rel_dir else ()


def _excluded(name: str, rel: str, globs: list[str]) -> bool:
    low = name.casefold()
    if any(fnmatch.fnmatchcase(low, g) for g in SKIP_FILE_GLOBS):
        return True
    rel_low = rel.casefold()
    return any(fnmatch.fnmatchcase(rel_low, g.casefold()) or fnmatch.fnmatchcase(low, g.casefold()) for g in globs)


def is_online_only(st: os.stat_result) -> bool:
    """Cloud placeholder (OneDrive/Google Drive "online-only"): reading it would download it."""
    return bool(getattr(st, "st_file_attributes", 0) & ONLINE_ONLY)


def _hidden(entry: os.DirEntry, st: os.stat_result) -> bool:
    if entry.name.startswith("."):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))


@dataclass
class ScanCounts:
    seen: int = 0
    new: int = 0
    changed: int = 0
    moved: int = 0
    skipped_online_only: int = 0
    missing: int = 0


class Scan:
    """One scan run of one source. `run()` walks until done or cancelled; safe to re-run."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        source_id: int,
        run_id: int,
        library_dir: Path,
        include_online_only: bool = False,
        cancel: threading.Event | None = None,
        on_batch=None,
        shutdown: threading.Event | None = None,
    ):
        self.conn = conn
        self.source_id = source_id
        self.run_id = run_id
        self.library_dir = library_dir.resolve()
        self.include_online_only = include_online_only
        self.cancel = cancel or threading.Event()
        self.shutdown = shutdown or threading.Event()  # engine stopping: leave the run 'running' to resume
        self.on_batch = on_batch  # called after each folder's files are committed (wakes the queue)
        src = conn.execute(
            "SELECT root_path, include_globs, exclude_globs, volume_id FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        self.root = Path(src[0])
        self.include = json.loads(src[1])
        self.exclude = json.loads(src[2])
        self.vol = volume_info(self.root)
        token = conn.execute("SELECT resume_token FROM scan_runs WHERE id = ?", (run_id,)).fetchone()[0]
        self.token = _order_key(token) if token is not None else None
        self.counts = ScanCounts()

    # -- identity
    def _file_key(self, entry: os.DirEntry, rel: str) -> str:
        if self.vol.use_file_ids:
            ino = entry.inode()  # Windows: file id via a metadata-only open (no data read)
            if ino:
                return f"{self.vol.volume_id}:{ino:x}"
        return "path:" + (rel.casefold() if sys.platform == "win32" else rel)

    # -- walk
    def run(self) -> str:
        """Returns the final scan_runs.status."""
        stack: list[str] = [""]
        while stack:
            if self.shutdown.is_set():
                return "running"
            if self.cancel.is_set():
                self._finish("cancelled")
                return "cancelled"
            rel_dir = stack.pop()
            key = _order_key(rel_dir)
            done_before = self.token is not None and key <= self.token
            on_token_path = self.token is not None and self.token[: len(key)] == key
            if done_before and not on_token_path:
                continue  # whole subtree finished in an earlier attempt
            try:
                subdirs, files = self._list(rel_dir)
            except OSError as e:
                if rel_dir == "":
                    self._finish("failed", f"source folder cannot be read: {e}")
                    return "failed"
                log.warning("skipping unreadable folder %s: %s", rel_dir, e)
                subdirs, files = [], []
            if not done_before:
                self._register(files)
            # push in reverse so the smallest name is processed next (sorted pre-order)
            stack.extend(reversed(subdirs))
            if not done_before:
                self.conn.execute(
                    "UPDATE scan_runs SET resume_token = ?, files_seen = files_seen + ?, files_new = files_new + ?, "
                    "files_changed = files_changed + ? WHERE id = ?",
                    (rel_dir, len(files), self._batch_new, self._batch_changed, self.run_id),
                )
                if self.on_batch and (self._batch_new or self._batch_changed):
                    self.on_batch()
        self._mark_missing()
        self._finish("succeeded")
        return "succeeded"

    def _list(self, rel_dir: str) -> tuple[list[str], list[tuple[str, os.DirEntry, os.stat_result]]]:
        path = self.root / rel_dir if rel_dir else self.root
        subdirs, files = [], []
        with os.scandir(path) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat_mod.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                # symlinks and junctions are not followed (loops, other volumes). OneDrive files are
                # reparse points too, so for regular files only directories are excluded here.
                if stat_mod.S_ISDIR(st.st_mode) or stat_mod.S_ISLNK(st.st_mode):
                    continue
            if _hidden(e, st):
                continue
            if stat_mod.S_ISDIR(st.st_mode):
                full = (path / e.name).resolve()
                if e.name.casefold() in SKIP_DIRS or full == self.library_dir:
                    continue
                if any(fnmatch.fnmatchcase(rel.casefold(), g.casefold()) for g in self.exclude):
                    continue
                subdirs.append(rel)
            elif stat_mod.S_ISREG(st.st_mode):
                if _excluded(e.name, rel, self.exclude):
                    continue
                if self.include and not any(fnmatch.fnmatchcase(rel.casefold(), g.casefold()) for g in self.include):
                    continue
                files.append((rel, e, st))
        return subdirs, files

    def _register(self, files: list[tuple[str, os.DirEntry, os.stat_result]]) -> None:
        self._batch_new = self._batch_changed = 0
        if not files:
            return
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            for rel, entry, st in files:
                if is_online_only(st) and not self.include_online_only:
                    # Reading it would make OneDrive/Google Drive download it (decision 3). A file
                    # already in the library that went online-only is still present, not missing.
                    self.counts.skipped_online_only += 1
                    c.execute(
                        f"UPDATE file_locations SET last_seen_at = {NOW}, seen_run_id = ? WHERE source_id = ? AND rel_path = ?",
                        (self.run_id, self.source_id, rel),
                    )
                    continue
                try:
                    fkey = self._file_key(entry, rel)
                except OSError:
                    continue
                mtime = str(st.st_mtime_ns)
                row = c.execute(
                    "SELECT id, size_bytes, mtime, rel_path, state FROM file_locations WHERE source_id = ? AND file_key = ?",
                    (self.source_id, fkey),
                ).fetchone()
                self.counts.seen += 1
                if row is None:
                    handlers.register_file(c, self.source_id, rel, fkey, st.st_size, mtime, run_id=self.run_id)
                    self._batch_new += 1
                    self.counts.new += 1
                elif row[1] != st.st_size or row[2] != mtime:
                    handlers.register_file(
                        c, self.source_id, rel, fkey, st.st_size, mtime, changed=True, run_id=self.run_id
                    )
                    self._batch_changed += 1
                    self.counts.changed += 1
                else:  # unchanged content: never read; only path/presence bookkeeping
                    if row[3] != rel:
                        self.counts.moved += 1
                    c.execute(
                        f"UPDATE file_locations SET rel_path = ?, state = 'present', last_seen_at = {NOW}, seen_run_id = ? "
                        "WHERE id = ?",
                        (rel, self.run_id, row[0]),
                    )
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def _mark_missing(self) -> None:
        """Locations this run did not see are gone (doc 05 §3: kept, marked missing)."""
        n = self.conn.execute(
            "UPDATE file_locations SET state = 'missing' WHERE source_id = ? AND state = 'present' "
            "AND seen_run_id IS NOT ?",
            (self.source_id, self.run_id),
        ).rowcount
        self.counts.missing = n
        self.conn.execute("UPDATE scan_runs SET files_missing = ? WHERE id = ?", (n, self.run_id))

    def _finish(self, status: str, error: str | None = None) -> None:
        self.conn.execute(
            f"UPDATE scan_runs SET status = ?, last_error = ?, finished_at = {NOW} WHERE id = ?",
            (status, error, self.run_id),
        )
        if status == "succeeded":
            self.conn.execute(
                f"UPDATE sources SET last_scan_at = {NOW}, last_error = NULL WHERE id = ?", (self.source_id,)
            )
        elif error:
            self.conn.execute("UPDATE sources SET last_error = ? WHERE id = ?", (error, self.source_id))


# --------------------------------------------------------------------------- manager
class Scanner:
    """Runs scans in background threads, one per source; resumes interrupted runs at startup."""

    def __init__(self, library_dir: Path, on_batch=None):
        self.library_dir = library_dir
        self.on_batch = on_batch
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._active: dict[int, tuple[threading.Thread, threading.Event]] = {}

    def _conn(self) -> sqlite3.Connection:
        return db.connect(self.library_dir / "library.db")

    def resume_interrupted(self) -> list[int]:
        conn = self._conn()
        try:
            ids = [r[0] for r in conn.execute("SELECT DISTINCT source_id FROM scan_runs WHERE status = 'running'")]
        finally:
            conn.close()
        for sid in ids:
            self.start(sid)
        return ids

    def start(self, source_id: int, include_online_only: bool = False) -> int:
        """Start (or resume) a full scan. Returns the scan_runs id."""
        with self._lock:
            if source_id in self._active and self._active[source_id][0].is_alive():
                raise SourceError("a scan of this folder is already running")
            conn = self._conn()
            try:
                if (
                    conn.execute("SELECT 1 FROM sources WHERE id = ? AND status = 'active'", (source_id,)).fetchone()
                    is None
                ):
                    raise SourceError("no such folder")
                last = conn.execute(
                    "SELECT id, status FROM scan_runs WHERE source_id = ? AND kind = 'full' ORDER BY id DESC LIMIT 1",
                    (source_id,),
                ).fetchone()
                if last and last[1] in ("running", "cancelled"):  # resume where it stopped
                    run_id = last[0]
                    conn.execute(
                        "UPDATE scan_runs SET status = 'running', finished_at = NULL, last_error = NULL WHERE id = ?",
                        (run_id,),
                    )
                else:
                    run_id = conn.execute(
                        "INSERT INTO scan_runs (source_id, kind) VALUES (?, 'full') RETURNING id", (source_id,)
                    ).fetchone()[0]
            finally:
                conn.close()
            cancel = threading.Event()
            t = threading.Thread(
                target=self._run,
                args=(source_id, run_id, include_online_only, cancel),
                daemon=True,
                name=f"iv-scan-{source_id}",
            )
            self._active[source_id] = (t, cancel)
            t.start()
            return run_id

    def _run(self, source_id: int, run_id: int, include_online_only: bool, cancel: threading.Event) -> None:
        conn = self._conn()
        try:
            scan = Scan(
                conn, source_id, run_id, self.library_dir, include_online_only, cancel, self.on_batch, self._shutdown
            )
            status = scan.run()
            log.info("scan %d of source %d %s: %s", run_id, source_id, status, scan.counts)
        except Exception as e:  # noqa: BLE001
            log.exception("scan %d failed", run_id)
            conn.execute(
                f"UPDATE scan_runs SET status = 'failed', last_error = ?, finished_at = {NOW} WHERE id = ?",
                (f"{type(e).__name__}: {e}"[:1000], run_id),
            )
        finally:
            conn.close()
            if self.on_batch:
                self.on_batch()

    def cancel(self, source_id: int) -> bool:
        with self._lock:
            active = self._active.get(source_id)
        if not active or not active[0].is_alive():
            return False
        active[1].set()
        active[0].join(30)
        return True

    def stop(self) -> None:
        """Engine shutdown: stop walking; runs stay 'running' and resume at next start."""
        self._shutdown.set()
        with self._lock:
            active = list(self._active.values())
        for t, _ev in active:
            t.join(10)

    def running(self) -> list[int]:
        with self._lock:
            return [sid for sid, (t, _e) in self._active.items() if t.is_alive()]
