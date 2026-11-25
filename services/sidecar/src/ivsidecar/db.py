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
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode = WAL")
    # NORMAL is the recommended level for WAL: no fsync per commit, still safe against a crash or
    # kill of the app; only a power cut can lose the last commits, which the job queue redoes.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def open_library(library_dir: Path) -> sqlite3.Connection:
    """Open (creating if needed) library.db and bring it to the latest schema version."""
    library_dir.mkdir(parents=True, exist_ok=True)
    db_path = library_dir / "library.db"
    existed = db_path.exists()
    conn = connect(db_path)
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        conn.close()
        raise RuntimeError(f"library database failed integrity check: {db_path}")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(v, sql) for v, sql in _migrations() if v > current]
    if pending and existed and current > 0:
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
    return conn
