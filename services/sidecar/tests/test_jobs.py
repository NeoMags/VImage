"""B5: job queue (doc 06 §3): migration 0002, idempotent enqueue, stages 1-4 end to end,
retries, crash handling, pause, API, and kill -9 recovery of the real engine."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

import httpx
import jobhelpers
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ivsidecar import db, handlers, settings
from ivsidecar.app import create_app
from ivsidecar.jobqueue import JobQueue
from ivsidecar.probes import find_exiftool
from ivsidecar.scanner import volume_info

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}

if not find_exiftool() and os.environ.get("IV_REQUIRE_EXIFTOOL") != "1":
    pytest.skip("exiftool not installed", allow_module_level=True)


# --------------------------------------------------------------------------- helpers
def _jpeg(path: Path, seed: int, size: int = 48) -> None:
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 256, (size, size, 3), dtype=np.uint8)).save(path, quality=85)


def _library(tmp_path: Path, threads: int = 2) -> tuple[Path, Path, sqlite3.Connection]:
    lib, src = tmp_path / "Library", tmp_path / "Photos"
    src.mkdir()
    conn = db.open_library(lib)
    settings.save(conn, "resources", settings.Resources(threads=threads))
    # OCR (K2) is on by default; queue tests switch it off so they don't wait for it (test_text.py covers it)
    settings.save(conn, "ocr", settings.OcrOptions(enabled=False))
    conn.execute(
        "INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('s1', 'local_folder', 'Photos', ?)",
        (str(src),),
    )
    return lib, src, conn


def _register_all(conn: sqlite3.Connection, src: Path) -> None:
    # the same file keys the scanner computes, so the engine's startup scan (B7) recognises them
    vol = volume_info(src).volume_id
    for p in sorted(src.iterdir()):
        st = p.stat()
        handlers.register_file(conn, 1, p.name, f"{vol}:{st.st_ino:x}", st.st_size, str(st.st_mtime_ns))


def _drain(conn: sqlite3.Connection, timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        busy = conn.execute(
            "SELECT count(*) FROM jobs WHERE status = 'running' OR (status = 'queued' AND run_after <= "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        ).fetchone()[0]
        if busy == 0:
            return
        time.sleep(0.2)
    raise AssertionError(
        f"queue did not drain: {conn.execute("SELECT stage, status, last_error FROM jobs WHERE status NOT IN ('succeeded')").fetchall()}"
    )


def _job(conn, name: str, stage: str = "identify"):
    return conn.execute(
        "SELECT j.status, j.attempts, j.crashes, j.last_error, j.run_after FROM jobs j "
        "JOIN file_locations fl ON fl.id = j.location_id WHERE fl.rel_path = ? AND j.stage = ?",
        (name, stage),
    ).fetchone()


# --------------------------------------------------------------------------- schema
def test_migration_0002_upgrades_existing_library(tmp_path):
    """A v1 library keeps its rows; jobs gain location_id; a backup is taken first."""
    lib = tmp_path / "lib"
    lib.mkdir()
    conn = db.connect(lib / "library.db")
    sql = resources.files("ivsidecar.migrations").joinpath("0001_initial.sql").read_text(encoding="utf-8")
    conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
    conn.execute("INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('s', 'local_folder', 'x', '/x')")
    conn.execute("INSERT INTO assets (uid, sha256, mime_type, byte_size) VALUES ('a', zeroblob(32), 'image/jpeg', 1)")
    conn.execute(
        "INSERT INTO file_locations (source_id, asset_id, file_key, rel_path, size_bytes, mtime) "
        "VALUES (1, 1, 'k', 'a.jpg', 1, 't')"
    )
    conn.execute("INSERT INTO jobs (asset_id, stage, pipeline_version) VALUES (1, 'thumbnail', '1')")
    conn.close()

    conn = db.open_library(lib)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.latest_version() >= 2
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT rel_path, asset_id FROM file_locations").fetchall() == [("a.jpg", 1)]
    assert conn.execute("SELECT asset_id, location_id, stage FROM jobs").fetchall() == [(1, None, "thumbnail")]
    assert list((lib / "backups").glob("library-v1-*.db"))
    # deleting the asset still cascades to its location and jobs (foreign keys intact after the rebuild)
    conn.execute("DELETE FROM assets")
    assert conn.execute("SELECT count(*) FROM file_locations").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_enqueue_is_idempotent_and_owner_is_exclusive(tmp_path):
    _lib, _src, conn = _library(tmp_path)
    loc = handlers.register_file(conn, 1, "a.jpg", "k1", 10, "t")
    assert handlers.register_file(conn, 1, "a.jpg", "k1", 10, "t") == loc  # same file seen again
    handlers.enqueue(conn, "identify", location_id=loc)
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):  # a job belongs to an asset or a location, never both
        conn.execute(
            "INSERT INTO jobs (asset_id, location_id, stage, pipeline_version) VALUES (NULL, NULL, 'hash', '1')"
        )
    with pytest.raises(ValueError):
        handlers.enqueue(conn, "hash")
    # requeue resets a finished job (content changed) instead of adding a second one
    conn.execute("UPDATE jobs SET status = 'succeeded', attempts = 3")
    handlers.enqueue(conn, "identify", location_id=loc, requeue=True)
    assert conn.execute("SELECT status, attempts FROM jobs").fetchall() == [("queued", 0)]


def test_claim_order_priority_then_age(tmp_path):
    lib, _src, conn = _library(tmp_path)
    a = handlers.register_file(conn, 1, "a", "ka", 1, "t")
    b = handlers.register_file(conn, 1, "b", "kb", 1, "t", priority=10)  # user is looking at it
    q = JobQueue(lib)
    assert q._claim()["location_id"] == b
    assert q._claim()["location_id"] == a
    assert q._claim() is None


# --------------------------------------------------------------------------- end to end
def test_stages_end_to_end(tmp_path):
    lib, src, conn = _library(tmp_path)
    for i in range(4):
        _jpeg(src / f"img{i}.jpg", i)
    (src / "copy_of_img0.jpg").write_bytes((src / "img0.jpg").read_bytes())
    Image.new("RGB", (20, 10), "red").save(src / "red.png")
    (src / "corrupt.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    (src / "notes.txt").write_text("not an image")
    _register_all(conn, src)

    q = JobQueue(lib)
    q.start()
    try:
        _drain(conn)
    finally:
        q.stop()

    status = dict(
        conn.execute(
            "SELECT fl.rel_path, a.status FROM file_locations fl JOIN assets a ON a.id = fl.asset_id"
        ).fetchall()
    )
    assert {k: status[k] for k in ("img0.jpg", "img3.jpg", "red.png", "copy_of_img0.jpg")} == dict.fromkeys(
        ("img0.jpg", "img3.jpg", "red.png", "copy_of_img0.jpg"), "ready"
    )
    assert status["corrupt.jpg"] == "failed"
    # unsupported: identified, not hashed, no asset; the location keeps the type
    assert "notes.txt" not in status
    assert _job(conn, "notes.txt")[0] == "skipped" and _job(conn, "notes.txt", "hash") is None
    assert conn.execute("SELECT mime_type FROM file_locations WHERE rel_path = 'notes.txt'").fetchone()[0]
    # exact duplicate: two locations, one asset, processed once
    assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 6
    dup = conn.execute(
        "SELECT count(DISTINCT asset_id) FROM file_locations WHERE rel_path IN ('img0.jpg', 'copy_of_img0.jpg')"
    ).fetchone()[0]
    assert dup == 1
    assert sorted(str(_job(conn, n, "hash")[3]) for n in ("img0.jpg", "copy_of_img0.jpg")) == ["None", "duplicate"]
    # outputs: derivatives on disk, pHash in the vector table, metadata row
    ready = conn.execute("SELECT id, width, height FROM assets WHERE status = 'ready'").fetchall()
    assert len(ready) == 5
    for aid, w, h in ready:
        assert w and h
        for (rel,) in conn.execute("SELECT rel_path FROM derivatives WHERE asset_id = ?", (aid,)):
            assert (lib / rel).is_file()
        assert conn.execute("SELECT count(*) FROM vec_phash WHERE rowid = ?", (aid,)).fetchone()[0] == 1
        assert conn.execute("SELECT extractor FROM asset_metadata WHERE asset_id = ?", (aid,)).fetchone()
    # no duplicate jobs anywhere
    assert (
        conn.execute(
            "SELECT count(*) FROM (SELECT 1 FROM jobs GROUP BY asset_id, location_id, stage HAVING count(*) > 1)"
        ).fetchone()[0]
        == 0
    )
    # sources untouched
    assert sorted(p.name for p in src.iterdir()) == sorted(
        ["img0.jpg", "img1.jpg", "img2.jpg", "img3.jpg", "copy_of_img0.jpg", "red.png", "corrupt.jpg", "notes.txt"]
    )


def test_crashing_file_fails_after_two_crashes_and_queue_continues(tmp_path):
    lib, src, conn = _library(tmp_path, threads=1)
    _jpeg(src / "a_crash.jpg", 1)
    _jpeg(src / "b_ok.jpg", 2)
    _register_all(conn, src)
    q = JobQueue(lib, stage_fn=jobhelpers.crash_on_bad)
    q.start()
    try:
        _drain(conn)
    finally:
        q.stop()
    status, attempts, crashes, err, _ = _job(conn, "a_crash.jpg")
    assert (status, crashes) == ("failed", 2) and "crashed 2 times" in err
    assert conn.execute("SELECT count(*) FROM assets WHERE status = 'ready'").fetchone()[0] == 1


def test_retry_backoff_then_dead(tmp_path):
    lib, src, conn = _library(tmp_path, threads=1)
    _jpeg(src / "flaky.jpg", 1)
    _register_all(conn, src)
    conn.execute("UPDATE jobs SET max_attempts = 2")
    q = JobQueue(lib, stage_fn=jobhelpers.flaky)
    q.start()
    try:
        _drain(conn)  # first failure: re-queued ~30 s in the future
        status, attempts, _c, err, run_after = _job(conn, "flaky.jpg")
        assert (status, attempts) == ("queued", 1) and "locked" in err
        later = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now','+25 seconds')").fetchone()[0]
        assert run_after > later
        conn.execute("UPDATE jobs SET run_after = strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        q.wake()
        time.sleep(0.5)
        _drain(conn)
    finally:
        q.stop()
    assert _job(conn, "flaky.jpg")[:2] == ("dead", 2)


def test_hung_worker_is_killed(tmp_path):
    lib, src, conn = _library(tmp_path, threads=1)
    _jpeg(src / "hang.jpg", 1)
    _register_all(conn, src)
    q = JobQueue(lib, stage_fn=jobhelpers.hang, job_timeout_s=1.5)
    q.start()
    try:
        _drain(conn, timeout_s=60)
    finally:
        q.stop()
    status, _a, crashes, err, _ = _job(conn, "hang.jpg")
    assert (status, crashes) == ("failed", 2) and "timed out" in err


def test_pause_resume_and_api(tmp_path):
    lib, src, conn = _library(tmp_path)
    _jpeg(src / "a.jpg", 1)
    q = JobQueue(lib)
    client = TestClient(create_app(conn, TOKEN, time.time(), q))
    assert client.get("/v1/jobs/stats").status_code == 401
    assert client.post("/v1/jobs/pause").status_code == 401
    assert client.post("/v1/jobs/pause", headers=AUTH).json() == {"paused": True}
    _register_all(conn, src)
    q.start()
    try:
        time.sleep(1.5)
        stats = client.get("/v1/jobs/stats", headers=AUTH).json()
        assert stats["jobs"] == {"queued": 1} and stats["queue"]["paused"] is True
        assert JobQueue(lib).paused is True  # survives a restart
        client.post("/v1/jobs/resume", headers=AUTH)
        _drain(conn)
        stats = client.get("/v1/jobs/stats", headers=AUTH).json()
        assert stats["assets"] == {"ready": 1} and stats["jobs"] == {"succeeded": 4}
        assert client.post("/v1/jobs/retry", headers=AUTH).json() == {"requeued": 0}
    finally:
        q.stop()


def test_crash_recovery_requeues_running(tmp_path):
    lib, _src, conn = _library(tmp_path)
    handlers.register_file(conn, 1, "a", "ka", 1, "t")
    conn.execute("UPDATE jobs SET status = 'running'")
    assert JobQueue(lib).recover() == 1
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "queued"


# --------------------------------------------------------------------------- kill -9
def _start_engine(lib: Path) -> tuple[subprocess.Popen, int]:
    env = {**os.environ, "IV_TOKEN": TOKEN}
    env.pop("IV_PARENT_PID", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "ivsidecar", "--library", str(lib)], stdout=subprocess.PIPE, env=env, text=True
    )
    line = proc.stdout.readline()
    ready = json.loads(line)
    assert ready["event"] == "ready", line
    return proc, ready["port"]


def _stats(port: int) -> dict:
    return httpx.get(f"http://127.0.0.1:{port}/v1/jobs/stats", headers=AUTH, timeout=10).json()


@pytest.mark.skipif(os.environ.get("IV_SKIP_SLOW") == "1", reason="slow")
def test_kill_9_mid_batch_recovers(tmp_path):
    """Plan B5 acceptance: kill -9 halfway through 500 files -> every asset ready, no duplicate jobs."""
    n = 500
    lib, src, conn = _library(tmp_path, threads=4)
    for i in range(n):
        _jpeg(src / f"p{i:03d}.jpg", 1000 + i, size=32)
    _register_all(conn, src)

    proc, port = _start_engine(lib)
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            ready = _stats(port)["assets"].get("ready", 0)
            if ready >= n // 3:
                break
            time.sleep(0.2)
        assert ready < n, "batch finished before the kill; make it bigger"
    finally:
        proc.kill()  # SIGKILL on POSIX, TerminateProcess on Windows: no cleanup runs
        proc.wait(10)
    running = conn.execute("SELECT count(*) FROM jobs WHERE status = 'running'").fetchone()[0]

    proc, port = _start_engine(lib)
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            s = _stats(port)
            if s["assets"].get("ready", 0) == n and not s["jobs"].get("queued") and not s["jobs"].get("running"):
                break
            time.sleep(0.5)
    finally:
        proc.terminate()
        proc.wait(10)
    assert s["assets"] == {"ready": n}, (s, running)
    assert s["jobs"] == {"succeeded": 4 * n}
    dups = conn.execute(
        "SELECT count(*) FROM (SELECT 1 FROM jobs GROUP BY asset_id, location_id, stage HAVING count(*) > 1)"
    ).fetchone()[0]
    assert dups == 0
    assert conn.execute("SELECT count(*) FROM derivatives").fetchone()[0] == 2 * n


def test_selftest_cli():
    """The packaged-build check (`ivsidecar --selftest-queue`) passes from source too."""
    res = subprocess.run(
        [sys.executable, "-m", "ivsidecar", "--selftest-queue"], capture_output=True, text=True, timeout=120
    )
    assert res.stdout.strip(), res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert res.returncode == 0 and out["ok"], out
