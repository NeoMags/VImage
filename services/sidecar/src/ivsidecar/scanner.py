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
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import db, events, handlers

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


def candidate_roots(root_path: str) -> list[str]:
    """Where a source on a removable drive may be now: the same folder under other drive letters."""
    if sys.platform != "win32" or len(root_path) < 2 or root_path[1] != ":":
        return []
    import ctypes

    mask = ctypes.windll.kernel32.GetLogicalDrives()
    here = root_path[0].upper()
    return [f"{chr(65 + i)}{root_path[1:]}" for i in range(26) if mask >> i & 1 and chr(65 + i) != here]


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

    def _progress(self, status: str, kind: str) -> None:
        c = self.counts
        events.bus.scan_progress(
            self.source_id,
            run_id=self.run_id,
            kind=kind,
            status=status,
            files_seen=c.seen,
            files_new=c.new,
            files_changed=c.changed,
            files_missing=c.missing,
        )

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
                self._progress("running", "full")
        self._mark_missing()
        self._finish("succeeded")
        return "succeeded"

    # -- watch batches (B7)
    def run_batch(self, dirs: dict[str, bool], min_age_s: float = 2.0) -> dict[str, bool]:
        """Re-list only the folders the watcher reported, as one 'watch_batch' run.

        `dirs` maps a folder (relative, "" = root) to recursive: True for a folder that was
        created or moved in (its whole subtree is new to us), False when only its direct files
        changed. A folder that no longer exists marks everything under it missing.
        Files modified less than `min_age_s` ago may still be being written (a copy in
        progress); their folder is returned to be looked at again, and nothing in it is marked
        missing yet. Returns those folders.
        """
        deferred: dict[str, bool] = {}
        for rel_dir, recursive in sorted(dirs.items()):
            if self.shutdown.is_set() or self.cancel.is_set():
                deferred.update({k: v for k, v in dirs.items() if k >= rel_dir})
                break
            if not self._dir_allowed(rel_dir):
                continue
            path = self.root / rel_dir if rel_dir else self.root
            if not path.is_dir():
                self._mark_missing_under(rel_dir, direct_only=False)
                continue
            stack, young_any = [rel_dir], False
            while stack:
                d = stack.pop()
                try:
                    subdirs, files = self._list(d)
                except OSError as e:
                    log.warning("watch: cannot list %s: %s", d, e)
                    young_any = True  # try again later rather than marking its files missing
                    continue
                cutoff = time.time() - min_age_s
                ready = [f for f in files if f[2].st_mtime <= cutoff]
                self._register(ready)
                if len(ready) < len(files):
                    young_any = True
                    if not recursive:
                        deferred[d] = False
                else:
                    self._mark_missing_under(d, direct_only=True)
                if recursive:
                    stack.extend(subdirs)
            if recursive:
                if young_any:
                    deferred[rel_dir] = True
                else:
                    self._mark_missing_under(rel_dir, direct_only=False)
        self.conn.execute(
            "UPDATE scan_runs SET files_seen = ?, files_new = ?, files_changed = ?, files_missing = ? WHERE id = ?",
            (self.counts.seen, self.counts.new, self.counts.changed, self.counts.missing, self.run_id),
        )
        self._finish("succeeded" if not self.shutdown.is_set() else "cancelled", update_source=False)
        self._progress("succeeded" if not self.shutdown.is_set() else "cancelled", "watch_batch")
        if self.on_batch and (self.counts.new or self.counts.changed):
            self.on_batch()
        return deferred

    def _dir_allowed(self, rel_dir: str) -> bool:
        """Same exclusions as the full walk, applied to every folder on the way down."""
        if not rel_dir:
            return True
        parts = rel_dir.split("/")
        cur = self.root
        for i, name in enumerate(parts):
            if name.startswith(".") or name.casefold() in SKIP_DIRS:
                return False
            cur = cur / name
            rel = "/".join(parts[: i + 1])
            if any(fnmatch.fnmatchcase(rel.casefold(), g.casefold()) for g in self.exclude):
                return False
            try:
                st = cur.lstat()
            except OSError:
                return True  # gone: allowed, so that its files are marked missing
            attrs = getattr(st, "st_file_attributes", 0)
            if stat_mod.S_ISLNK(st.st_mode) or attrs & (
                FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_REPARSE_POINT
            ):
                return False
            if cur.resolve() == self.library_dir:
                return False
        return True

    def _mark_missing_under(self, rel_dir: str, direct_only: bool) -> None:
        prefix = f"{rel_dir}/" if rel_dir else ""
        sql = (
            "UPDATE file_locations SET state = 'missing' WHERE source_id = ? AND state = 'present' "
            "AND seen_run_id IS NOT ? AND substr(rel_path, 1, ?) = ?"
        )
        if direct_only:
            sql += " AND instr(substr(rel_path, ? + 1), '/') = 0"
            args = (self.source_id, self.run_id, len(prefix), prefix, len(prefix))
        else:
            args = (self.source_id, self.run_id, len(prefix), prefix)
        self.counts.missing += self.conn.execute(sql, args).rowcount

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
                    "SELECT id, size_bytes, mtime, rel_path, state, asset_id FROM file_locations "
                    "WHERE source_id = ? AND file_key = ?",
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
                    moved = row[3] != rel
                    if moved:
                        self.counts.moved += 1
                    c.execute(
                        f"UPDATE file_locations SET rel_path = ?, state = 'present', last_seen_at = {NOW}, seen_run_id = ? "
                        "WHERE id = ?",
                        (rel, self.run_id, row[0]),
                    )
                    if moved and row[5] is not None:
                        handlers.refresh_fts(c, [row[5]])  # file name is searchable
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

    def _finish(self, status: str, error: str | None = None, update_source: bool = True) -> None:
        if update_source:  # a full scan (watch batches report their own progress)
            self._progress(status, "full")
        self.conn.execute(
            f"UPDATE scan_runs SET status = ?, last_error = ?, finished_at = {NOW} WHERE id = ?",
            (status, error, self.run_id),
        )
        if not update_source:
            return
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
        self._source_locks: dict[int, threading.Lock] = {}

    def source_lock(self, source_id: int) -> threading.Lock:
        """Held while anything walks this source, so a watch batch and a full scan never overlap."""
        with self._lock:
            return self._source_locks.setdefault(source_id, threading.Lock())

    def _conn(self) -> sqlite3.Connection:
        return db.connect(self.library_dir / "library.db")

    def resume_interrupted(self) -> list[int]:
        conn = self._conn()
        try:
            ids = [r[0] for r in conn.execute("SELECT DISTINCT source_id FROM scan_runs WHERE status = 'running'")]
        finally:
            conn.close()
        started = []
        for sid in ids:
            try:
                self.start(sid)
                started.append(sid)
            except SourceError as e:
                log.warning("not resuming scan of source %d: %s", sid, e)
        return started

    def start(self, source_id: int, include_online_only: bool = False) -> int:
        """Start (or resume) a full scan. Returns the scan_runs id."""
        if self.check_reachable(source_id) != "active":
            raise SourceError("the folder cannot be reached (drive unplugged or renamed?)")
        with self._lock:
            if source_id in self._active and self._active[source_id][0].is_alive():
                raise SourceError("a scan of this folder is already running")
            conn = self._conn()
            try:
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

    # -- reachability (doc 05 §3: unplugged drives are unreachable, never "deleted")
    def check_reachable(self, source_id: int) -> str:
        """Returns the source's status after checking that its folder is there.

        An active source whose folder is gone becomes 'unreachable' and its files too (not
        missing: nothing was deleted). On Windows a removable drive may come back under another
        letter; it is found again by its volume serial and root_path is updated. When the
        folder is back the source is 'active' again; the caller then runs a full scan, which
        sorts out what changed meanwhile.
        """
        conn = self._conn()
        try:
            row = conn.execute("SELECT root_path, volume_id, status FROM sources WHERE id = ?", (source_id,)).fetchone()
            if row is None or row[2] not in ("active", "unreachable"):
                raise SourceError("no such folder")
            root_path, volume_id, status = row
            found = root_path if Path(root_path).is_dir() else self._refind(root_path, volume_id)
            if found is None:
                if status == "active":
                    log.warning("source %d unreachable: %s", source_id, root_path)
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("UPDATE sources SET status = 'unreachable' WHERE id = ?", (source_id,))
                    conn.execute(
                        "UPDATE file_locations SET state = 'unreachable' WHERE source_id = ? AND state = 'present'",
                        (source_id,),
                    )
                    conn.execute("COMMIT")
                    events.bus.source_status(source_id, "unreachable")
                    events.bus.asset_updated_all()
                return "unreachable"
            if status == "unreachable" or found != root_path:
                log.info("source %d reachable again at %s", source_id, found)
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE sources SET status = 'active', root_path = ?, last_error = NULL WHERE id = ?",
                    (found, source_id),
                )
                conn.execute(
                    "UPDATE file_locations SET state = 'present' WHERE source_id = ? AND state = 'unreachable'",
                    (source_id,),
                )
                conn.execute("COMMIT")
                events.bus.source_status(source_id, "active")
                events.bus.asset_updated_all()
            return "active"
        finally:
            conn.close()

    @staticmethod
    def _refind(root_path: str, volume_id: str | None) -> str | None:
        if not volume_id:
            return None
        for cand in candidate_roots(root_path):
            try:
                if Path(cand).is_dir() and volume_info(Path(cand)).volume_id == volume_id:
                    return cand
            except OSError:
                continue
        return None

    def run_batch(self, source_id: int, dirs: dict[str, bool], min_age_s: float = 2.0) -> dict[str, bool]:
        """One watch batch (see Scan.run_batch). Waits while a full scan of the source runs."""
        with self.source_lock(source_id):
            conn = self._conn()
            try:
                run_id = conn.execute(
                    "INSERT INTO scan_runs (source_id, kind) VALUES (?, 'watch_batch') RETURNING id", (source_id,)
                ).fetchone()[0]
                scan = Scan(conn, source_id, run_id, self.library_dir, False, None, self.on_batch, self._shutdown)
                scan.include_online_only = self._include_online_only(conn)
                deferred = scan.run_batch(dirs, min_age_s)
                log.info("watch batch %d of source %d: %d folders, %s", run_id, source_id, len(dirs), scan.counts)
                return deferred
            finally:
                conn.close()

    @staticmethod
    def _include_online_only(conn: sqlite3.Connection) -> bool:
        from . import settings

        return settings.load(conn, "scan").include_online_only

    def _run(self, source_id: int, run_id: int, include_online_only: bool, cancel: threading.Event) -> None:
        conn = self._conn()
        lock = self.source_lock(source_id)
        lock.acquire()
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
            lock.release()
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

    def forget(self, source_id: int) -> None:
        """Cancel and wait for any scan of a source that is being removed."""
        self.cancel(source_id)
        with self.source_lock(source_id):  # a watch batch in progress finishes first
            pass

    def running(self) -> list[int]:
        with self._lock:
            return [sid for sid, (t, _e) in self._active.items() if t.is_alive()]


# --------------------------------------------------------------------------- removing a source
_ONLY_HERE = (
    "SELECT DISTINCT fl.asset_id FROM file_locations fl WHERE fl.source_id = ? AND fl.asset_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM file_locations o WHERE o.asset_id = fl.asset_id AND o.source_id != ?)"
)


def removal_preview(conn: sqlite3.Connection, source_id: int) -> dict:
    """What removing a folder deletes from the library (shown in the confirmation)."""
    row = conn.execute(
        "SELECT display_name, root_path FROM sources WHERE id = ? AND status != 'removed'", (source_id,)
    ).fetchone()
    if row is None:
        raise SourceError("no such folder")
    files = conn.execute("SELECT count(*) FROM file_locations WHERE source_id = ?", (source_id,)).fetchone()[0]
    only = conn.execute(f"SELECT count(*) FROM ({_ONLY_HERE})", (source_id, source_id)).fetchone()[0]
    return {"id": source_id, "display_name": row[0], "root_path": row[1], "files": files, "assets_only_here": only}


def remove_source(conn: sqlite3.Connection, source_id: int, library_dir: Path, confirm: int) -> dict:
    """Removes a folder from the library (B7 decision 4). The folder and its files are not
    touched. Photos found only in this folder are deleted from the library with their tags,
    notes and thumbnails; photos that also live in another folder stay.

    `confirm` must equal the assets_only_here count the user was shown, so a count that went
    stale (a scan added more meanwhile) is not deleted by surprise.
    """
    preview = removal_preview(conn, source_id)
    if confirm != preview["assets_only_here"]:
        raise SourceError(
            f"the folder now has {preview['assets_only_here']} photos found only there (you confirmed {confirm})"
        )
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = [r[0] for r in conn.execute(_ONLY_HERE, (source_id, source_id))]
        touched = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT asset_id FROM file_locations WHERE source_id = ? AND asset_id IS NOT NULL",
                (source_id,),
            )
        ]
        thumbs: list[str] = []
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" for _ in chunk)
            thumbs += [
                r[0] for r in conn.execute(f"SELECT rel_path FROM derivatives WHERE asset_id IN ({marks})", chunk)
            ]
            conn.execute(f"DELETE FROM assets WHERE id IN ({marks})", chunk)  # cascades tags, notes, jobs...
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))  # cascades locations, scan runs
        handlers.refresh_fts(conn, set(touched) - set(ids))  # still in other folders: drop these paths
        conn.execute(
            "INSERT INTO audit_log (action, entity, entity_id, before) VALUES ('remove', 'source', ?, ?)",
            (
                str(source_id),
                json.dumps({"root_path": preview["root_path"], "files": preview["files"], "assets_deleted": len(ids)}),
            ),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    lib = library_dir.resolve()
    removed_files = 0
    for rel in thumbs:  # after the commit: a crash here leaves stray cache files, never broken rows
        p = (lib / rel).resolve()
        if p.is_relative_to(lib):
            try:
                p.unlink()
                removed_files += 1
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning("could not delete thumbnail %s: %s", p, e)
    log.info("removed source %d: %d photos deleted from the library", source_id, len(ids))
    events.bus.source_status(source_id, "removed")
    events.bus.asset_updated_all()
    return {"removed": source_id, "assets_deleted": len(ids), "thumbnails_deleted": removed_files}
