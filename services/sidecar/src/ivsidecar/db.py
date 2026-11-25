"""Library database: open, load sqlite-vec, apply migrations, back up before upgrading."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import sqlite_vec


def _migrations() -> list[tuple[int, str]]:
    files = resources.files("ivsidecar.migrations")
    out = []
    for f in files.iterdir():
        if f.name.endswith(".sql"):
            out.append((int(f.name.split("_", 1)[0]), f.read_text(encoding="utf-8")))
    return sorted(out)


def latest_version() -> int:
    return _migrations()[-1][0]


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode = WAL")
        # NORMAL is the recommended level for WAL: no fsync per commit, still safe against a crash or
        # kill of the app; only a power cut can lose the last commits, which the job queue redoes.
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
    except BaseException:
        conn.close()  # e.g. not a database: never leave the file open (Windows locks it)
        raise
    return conn


class Damaged(RuntimeError):
    """library.db cannot be opened or fails PRAGMA integrity_check."""


def check_file(path: Path) -> int:
    """Opens a database file read-only and checks it. Returns its schema version (user_version).
    Raises Damaged if it is not a readable, intact SQLite database."""
    try:
        src = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise Damaged(f"cannot open {path.name}: {e}") from e
    try:
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Damaged(f"{path.name} failed its integrity check")
        return src.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as e:
        raise Damaged(f"{path.name} is not a readable library database: {e}") from e
    finally:
        src.close()


def open_library(library_dir: Path) -> sqlite3.Connection:
    """Open (creating if needed) library.db and bring it to the latest schema version."""
    library_dir.mkdir(parents=True, exist_ok=True)
    db_path = library_dir / "library.db"
    conn = None
    try:
        conn = connect(db_path)
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError as e:
        if conn is not None:
            conn.close()  # Windows: an open handle would stop quarantine() from moving the file
        raise Damaged(f"library database cannot be opened: {db_path}: {e}") from e
    if not ok:
        conn.close()
        raise Damaged(f"library database failed integrity check: {db_path}")
    migrate(conn, library_dir)
    return conn


def quarantine(library_dir: Path) -> str:
    """Moves a damaged library.db (and its WAL/SHM files) to backups/damaged-<stamp>.db, never
    deleting it. Returns the new file name. The caller then opens a fresh, empty library."""
    backup_dir = library_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    name = f"damaged-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
    db_path = library_dir / "library.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.replace(backup_dir / (name + suffix))
    return name


def migrate(conn: sqlite3.Connection, library_dir: Path, backup_first: bool = True) -> None:
    """Applies pending migrations. Backs up first when an existing library is upgraded."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(v, sql) for v, sql in _migrations() if v > current]
    if pending and backup_first and current > 0:
        backup_dir = library_dir / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        with sqlite3.connect(backup_dir / f"library-v{current}-{stamp}.db") as dst:
            conn.backup(dst)
    if pending:
        # Table rebuilds (DROP + RENAME) must run with foreign keys off, or dropping a parent
        # table would cascade. The pragma cannot change inside a transaction, so it is set
        # around them, and violations are checked before each commit.
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            for version, sql in pending:
                conn.executescript("BEGIN;\n" + sql)
                bad = conn.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    conn.execute("ROLLBACK")
                    raise RuntimeError(f"migration {version} left foreign key violations: {bad[:5]}")
                conn.execute(f"PRAGMA user_version = {version}")
                conn.execute("COMMIT")
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
