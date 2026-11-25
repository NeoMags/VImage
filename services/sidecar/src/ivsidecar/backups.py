"""Library backups and restore (B19, doc 05 §5).

A backup is the library database only (tags, notes, collections, titles, flags, sources,
metadata, job history). Thumbnails and previews are rebuilt from the originals, so a restore
ends with `repair_derivatives`, which re-queues the thumbnail stage where files are missing.

File names in <library>/backups (the kind is part of the name, so nothing else is needed):
    library-auto-v<N>-<stamp>.db        automatic, at most one a day, the last `keep` are kept
    library-manual-v<N>-<stamp>.db      "Back up now"; never pruned
    library-pre-restore-v<N>-<stamp>.db made just before a restore; never pruned
    library-v<N>-<stamp>.db             made before a migration (db.migrate); never pruned
    damaged-<stamp>.db                  a library.db that failed its check at start; never pruned
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import db, handlers, settings

log = logging.getLogger(__name__)

KINDS = ("auto", "manual", "pre-restore", "pre-migration", "damaged")
AUTO_EVERY = timedelta(hours=24)
_NAME = re.compile(
    r"^(?:library-(?:(?P<kind>auto|manual|pre-restore)-)?v(?P<ver>\d+)|damaged)-(?P<stamp>\d{8}T\d{6}Z)(?:-\d+)?\.db$"
)


class BackupError(RuntimeError):
    """A backup could not be made, or a file cannot be restored (the message says why)."""


def backup_dir(library_dir: Path) -> Path:
    return library_dir / "backups"


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def parse_name(name: str) -> dict | None:
    m = _NAME.match(name)
    if not m:
        return None
    if name.startswith("damaged-"):
        kind = "damaged"
    else:
        kind = m["kind"] or "pre-migration"
    created = datetime.strptime(m["stamp"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return {"kind": kind, "created_at": created.isoformat(), "schema_version": int(m["ver"]) if m["ver"] else None}


def list_backups(library_dir: Path) -> list[dict]:
    """Newest first. Files whose names we do not recognise are left alone and not listed."""
    d = backup_dir(library_dir)
    out = []
    if d.is_dir():
        for p in d.iterdir():
            info = parse_name(p.name)
            if info is None or not p.is_file():
                continue
            out.append({"name": p.name, "size": p.stat().st_size, **info})
    out.sort(key=lambda b: (b["created_at"], b["name"]), reverse=True)
    return out


def inside_a_source(conn: sqlite3.Connection, path: Path) -> bool:
    """True if `path` is a source folder or inside one (B20: the engine never writes there)."""
    target = os.path.normcase(os.path.realpath(path))
    for (root,) in conn.execute("SELECT root_path FROM sources WHERE status != 'removed'"):
        r = os.path.normcase(os.path.realpath(root))
        if target == r or target.startswith(r.rstrip(os.sep) + os.sep):
            return True
    return False


def _unique(d: Path, base: str) -> Path:
    p = d / f"{base}.db"
    n = 1
    while p.exists():
        p = d / f"{base}-{n}.db"
        n += 1
    return p


def make_backup(library_dir: Path, kind: str = "manual", now: datetime | None = None) -> dict:
    """Snapshots library.db with SQLite's online backup API (safe while the app runs).

    The copy is written under a temporary name, checked with integrity_check, then renamed,
    so a half-written backup never appears. Then old automatic backups are pruned and, if a
    second-copy folder is set, the file is copied there too (a failure there is reported in
    `copy_error`, the backup itself still counts).
    """
    if kind not in ("auto", "manual", "pre-restore"):
        raise ValueError(kind)
    d = backup_dir(library_dir)
    d.mkdir(parents=True, exist_ok=True)
    src = db.connect(library_dir / "library.db")
    try:
        version = src.execute("PRAGMA user_version").fetchone()[0]
        final = _unique(d, f"library-{kind}-v{version}-{_stamp(now)}")
        tmp = final.with_name(final.name + ".partial")
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
                dst.execute("PRAGMA journal_mode = DELETE")  # one self-contained file
            finally:
                dst.close()
            db.check_file(tmp)
            os.replace(tmp, final)
        except (sqlite3.Error, db.Damaged, OSError) as e:
            tmp.unlink(missing_ok=True)
            raise BackupError(f"backup failed: {e}") from e
        opts = settings.load(src, "backup")
        pruned = prune(library_dir, opts.keep) if kind == "auto" else []
        copy_error = None
        if opts.copy_to:
            try:
                copy_out(src, final, Path(opts.copy_to))
            except (OSError, BackupError) as e:
                copy_error = str(e)
                log.warning("second copy of %s failed: %s", final.name, e)
    finally:
        src.close()
    info = {"name": final.name, "size": final.stat().st_size, **(parse_name(final.name) or {})}
    log.info("backup %s made (%d bytes, pruned %d)", final.name, info["size"], len(pruned))
    return {"backup": info, "pruned": pruned, "copy_error": copy_error}


def copy_out(conn: sqlite3.Connection, file: Path, folder: Path) -> Path:
    """Copies a backup to the second-copy folder (temp name, then rename)."""
    if not folder.is_dir():
        raise BackupError(f"the backup copy folder is not available: {folder}")
    if inside_a_source(conn, folder):
        raise BackupError("the backup copy folder is inside a photo source folder")
    target = folder / file.name
    tmp = folder / (file.name + ".partial")
    shutil.copyfile(file, tmp)
    os.replace(tmp, target)
    return target


def prune(library_dir: Path, keep: int) -> list[str]:
    """Deletes automatic backups beyond the newest `keep`. Other kinds are never pruned."""
    autos = [b for b in list_backups(library_dir) if b["kind"] == "auto"]
    gone = []
    for b in autos[keep:]:
        try:
            (backup_dir(library_dir) / b["name"]).unlink()
            gone.append(b["name"])
        except OSError as e:
            log.warning("could not prune %s: %s", b["name"], e)
    return gone


def auto_backup_if_due(library_dir: Path, now: datetime | None = None) -> dict | None:
    """At start: an automatic backup if the last one is older than a day and there is
    something worth keeping (an empty library is not backed up)."""
    now = now or datetime.now(UTC)
    autos = [b for b in list_backups(library_dir) if b["kind"] == "auto"]
    if autos and now - datetime.fromisoformat(autos[0]["created_at"]) < AUTO_EVERY:
        return None
    conn = db.connect(library_dir / "library.db")
    try:
        empty = conn.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM sources) AND NOT EXISTS (SELECT 1 FROM assets)"
        ).fetchone()[0]
    finally:
        conn.close()
    if empty:
        return None
    return make_backup(library_dir, "auto", now)


def resolve(library_dir: Path, name: str | None, path: str | None) -> Path:
    """The file a restore reads: a backup by name, or any .db file the user picked."""
    if (name is None) == (path is None):
        raise BackupError("give either a backup name or a file path")
    if name is not None:
        if parse_name(name) is None or Path(name).name != name:
            raise BackupError("no such backup")
        p = backup_dir(library_dir) / name
    else:
        p = Path(path)  # type: ignore[arg-type]
    if not p.is_file():
        raise BackupError(f"file not found: {p}")
    return p


def validate(file: Path) -> int:
    """Checks a file before a restore. Returns its schema version."""
    try:
        version = db.check_file(file)
    except db.Damaged as e:
        raise BackupError(str(e)) from e
    if version > db.latest_version():
        raise BackupError(
            f"this backup is from a newer version of ImageVault (schema {version}, this app reads up to "
            f"{db.latest_version()}); update the app first"
        )
    if version == 0:
        raise BackupError("this file is not an ImageVault library backup")
    src = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        src.close()
    if not {"assets", "sources", "file_locations", "tags", "notes"} <= tables:
        raise BackupError("this file is not an ImageVault library backup")
    return version


def restore_into(conn: sqlite3.Connection, file: Path, library_dir: Path) -> None:
    """Replaces the live database's contents with `file`, in place, through the online backup
    API: every open connection (queue, scanner, watcher) sees the restored data on its next
    query, so nothing has to be reopened. Then brings the schema up to date.
    The caller has paused everything that writes and made a pre-restore backup."""
    src = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
    try:
        src.backup(conn)
    finally:
        src.close()
    db.migrate(conn, library_dir, backup_first=False)
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise BackupError("the restored library failed its integrity check")


def repair_derivatives(conn: sqlite3.Connection, library_dir: Path) -> int:
    """After a restore (e.g. on a clean install) thumbnails may be missing: drop rows whose file
    is gone and queue the thumbnail stage again for those assets. Returns how many assets."""
    missing: set[int] = set()
    gone: list[tuple[int, str]] = []
    for aid, kind, rel in conn.execute("SELECT asset_id, kind, rel_path FROM derivatives").fetchall():
        if not (library_dir / rel).is_file():
            gone.append((aid, kind))
            missing.add(aid)
    if not missing:
        return 0
    conn.execute("BEGIN")
    try:
        conn.executemany("DELETE FROM derivatives WHERE asset_id = ? AND kind = ?", gone)
        for aid in missing:
            handlers.enqueue(conn, "thumbnail", asset_id=aid, requeue=True)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(missing)
