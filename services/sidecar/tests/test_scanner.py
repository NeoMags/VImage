"""B6: sources and the resumable scanner (doc 05 §1-3)."""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ivsidecar import db, scanner, settings
from ivsidecar.app import create_app

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def env(tmp_path):
    lib, src = tmp_path / "Library", tmp_path / "Photos"
    src.mkdir()
    return lib, src, db.open_library(lib)


def _touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _scan(conn, lib, sid, **kw) -> scanner.Scan:
    run_id = conn.execute("INSERT INTO scan_runs (source_id, kind) VALUES (?, 'full') RETURNING id", (sid,)).fetchone()[
        0
    ]
    s = scanner.Scan(conn, sid, run_id, lib, **kw)
    assert s.run() == "succeeded"
    return s


def _locs(conn) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in conn.execute("SELECT rel_path, id, file_key, state FROM file_locations")}


# --------------------------------------------------------------------------- adding folders
def test_add_source_validation(env, tmp_path):
    lib, src, conn = env
    with pytest.raises(scanner.SourceError, match="not found"):
        scanner.add_source(conn, str(tmp_path / "nope"), lib)
    with pytest.raises(scanner.SourceError, match="absolute"):
        scanner.add_source(conn, "relative/dir", lib)
    with pytest.raises(scanner.SourceError, match="library"):
        scanner.add_source(conn, str(lib), lib)
    sid = scanner.add_source(conn, str(src), lib)
    row = conn.execute("SELECT kind, display_name, volume_id FROM sources WHERE id = ?", (sid,)).fetchone()
    assert row[0] == "local_folder" and row[1] == "Photos" and row[2]
    with pytest.raises(scanner.SourceError, match="already"):
        scanner.add_source(conn, str(src), lib)
    (src / "sub").mkdir()
    with pytest.raises(scanner.SourceError, match="overlaps"):
        scanner.add_source(conn, str(src / "sub"), lib)
    with pytest.raises(scanner.SourceError, match="overlaps"):
        scanner.add_source(conn, str(tmp_path), lib)


# --------------------------------------------------------------------------- walking
def test_scan_registers_files_and_applies_exclusions(env):
    lib, src, conn = env
    for rel in [
        "a.jpg",
        "b/c.png",
        "b/d/e.heic",
        "Thumbs.db",
        "desktop.ini",
        "x.tmp",
        "~$doc.jpg",
        ".hidden.jpg",
        ".git/obj.jpg",
        "$RECYCLE.BIN/old.jpg",
        "skip/me.jpg",
        "raw/f.NEF",
    ]:
        _touch(src / rel)
    if sys.platform != "win32":
        (src / "loop").symlink_to(src, target_is_directory=True)  # not followed
    sid = scanner.add_source(conn, str(src), lib)
    conn.execute("UPDATE sources SET exclude_globs = ? WHERE id = ?", (json.dumps(["skip/*", "*.nef"]), sid))
    s = _scan(conn, lib, sid)
    assert sorted(_locs(conn)) == ["a.jpg", "b/c.png", "b/d/e.heic"]
    assert s.counts.new == 3
    assert conn.execute("SELECT count(*) FROM jobs WHERE stage = 'identify' AND status = 'queued'").fetchone()[0] == 3
    run = conn.execute("SELECT status, files_seen, files_new, resume_token FROM scan_runs").fetchone()
    assert run[:3] == ("succeeded", 3, 3)


def test_library_inside_source_is_skipped(tmp_path):
    src = tmp_path / "Pictures"
    lib = src / "ImageVault"
    conn = db.open_library(lib)
    _touch(src / "a.jpg")
    _touch(lib / "thumbs" / "ab" / "cd" / "x_512.webp")
    sid = scanner.add_source(conn, str(src), lib)
    _scan(conn, lib, sid)
    assert sorted(_locs(conn)) == ["a.jpg"]


def test_unchanged_rescan_reads_no_files(env, monkeypatch):
    lib, src, conn = env
    for i in range(30):
        _touch(src / f"d{i % 3}" / f"{i}.jpg", bytes([i]) * 10)
    sid = scanner.add_source(conn, str(src), lib)
    _scan(conn, lib, sid)
    conn.execute("UPDATE jobs SET status = 'succeeded'")

    def no_read(*a, **k):
        raise AssertionError(f"a source file was opened: {a[:1]}")

    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open", lambda f, *a, **k: no_read(f) if str(src) in str(f) else real_open(f, *a, **k)
    )
    real_os_open = os.open
    monkeypatch.setattr(os, "open", lambda f, *a, **k: no_read(f) if str(src) in str(f) else real_os_open(f, *a, **k))
    s = _scan(conn, lib, sid)
    assert (s.counts.seen, s.counts.new, s.counts.changed, s.counts.missing) == (30, 0, 0, 0)
    assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'queued'").fetchone()[0] == 0


def test_changed_renamed_and_deleted(env):
    lib, src, conn = env
    a = _touch(src / "a.jpg", b"one")
    _touch(src / "b.jpg", b"two")
    _touch(src / "c.jpg", b"three")
    sid = scanner.add_source(conn, str(src), lib)
    _scan(conn, lib, sid)
    before = _locs(conn)
    conn.execute("UPDATE jobs SET status = 'succeeded'")

    a.write_bytes(b"one, edited")  # content change: size differs
    (src / "sub").mkdir()
    os.replace(src / "b.jpg", src / "sub" / "renamed.jpg")  # rename + move on the same volume
    (src / "c.jpg").unlink()
    s = _scan(conn, lib, sid)
    after = _locs(conn)

    assert s.counts.changed == 1
    assert (
        conn.execute(
            "SELECT j.status FROM jobs j JOIN file_locations fl ON fl.id = j.location_id WHERE fl.rel_path = 'a.jpg'"
        ).fetchone()[0]
        == "queued"
    )
    # same file id -> same location row (tags would survive); only the path changed
    assert after["sub/renamed.jpg"][:2] == before["b.jpg"][:2] and "b.jpg" not in after
    assert s.counts.moved == 1 and s.counts.new == 0
    assert after["c.jpg"][2] == "missing" and s.counts.missing == 1


def test_online_only_files_are_skipped(env, monkeypatch):
    lib, src, conn = env
    _touch(src / "local.jpg")
    _touch(src / "cloud.jpg")
    monkeypatch.setattr(scanner, "is_online_only", lambda st: st.st_size == 5)
    (src / "cloud.jpg").write_bytes(b"cloud")
    sid = scanner.add_source(conn, str(src), lib)
    s = _scan(conn, lib, sid)
    assert sorted(_locs(conn)) == ["local.jpg"] and s.counts.skipped_online_only == 1
    s = _scan(conn, lib, sid, include_online_only=True)
    assert sorted(_locs(conn)) == ["cloud.jpg", "local.jpg"]


class _Crash(Exception):
    pass


def test_resume_after_interruption(env):
    lib, src, conn = env
    for d in range(20):
        for f in range(5):
            _touch(src / f"dir{d:02d}" / f"{f}.jpg")
    sid = scanner.add_source(conn, str(src), lib)
    run_id = conn.execute("INSERT INTO scan_runs (source_id, kind) VALUES (?, 'full') RETURNING id", (sid,)).fetchone()[
        0
    ]

    listed: list[str] = []

    class Crashy(scanner.Scan):
        def _list(self, rel_dir):
            listed.append(rel_dir)
            if len(listed) == 9:  # killed while in the 9th folder
                raise _Crash
            return super()._list(rel_dir)

    with pytest.raises(_Crash):
        Crashy(conn, sid, run_id, lib).run()
    assert conn.execute("SELECT status, resume_token FROM scan_runs").fetchone() == ("running", "dir06")
    listed.clear()

    class Counting(scanner.Scan):
        def _list(self, rel_dir):
            listed.append(rel_dir)
            return super()._list(rel_dir)

    assert Counting(conn, sid, run_id, lib).run() == "succeeded"
    # the root and the last finished folder are listed again only to find their subfolders (their
    # files are not re-registered); folders finished before it are skipped entirely
    assert listed[:3] == ["", "dir06", "dir07"]
    assert conn.execute("SELECT count(*) FROM file_locations").fetchone()[0] == 100
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 100
    assert conn.execute("SELECT files_seen, status FROM scan_runs").fetchone() == (100, "succeeded")


def test_cancel_then_resume(env):
    lib, src, conn = env
    for d in range(5):
        _touch(src / f"d{d}" / "x.jpg")
    sid = scanner.add_source(conn, str(src), lib)
    run_id = conn.execute("INSERT INTO scan_runs (source_id, kind) VALUES (?, 'full') RETURNING id", (sid,)).fetchone()[
        0
    ]
    s = scanner.Scan(conn, sid, run_id, lib)
    s.cancel.set()
    assert s.run() == "cancelled"
    mgr = scanner.Scanner(lib)
    assert mgr.start(sid) == run_id  # a cancelled scan resumes the same run
    for _ in range(100):
        if not mgr.running():
            break
        time.sleep(0.05)
    assert conn.execute("SELECT status FROM scan_runs WHERE id = ?", (run_id,)).fetchone()[0] == "succeeded"
    assert conn.execute("SELECT count(*) FROM file_locations").fetchone()[0] == 5


# --------------------------------------------------------------------------- API
def test_sources_api(env):
    lib, src, conn = env
    _touch(src / "a.jpg")
    mgr = scanner.Scanner(lib)
    client = TestClient(create_app(conn, TOKEN, time.time(), None, mgr))
    assert client.post("/v1/sources", json={"path": str(src)}).status_code == 401
    r = client.post("/v1/sources", json={"path": str(src / "nope")}, headers=AUTH)
    assert r.status_code == 400 and "not found" in r.json()["detail"]
    r = client.post("/v1/sources", json={"path": str(src), "display_name": "My photos"}, headers=AUTH)
    assert r.status_code == 201
    sid = r.json()["id"]
    for _ in range(100):
        if not mgr.running():
            break
        time.sleep(0.05)
    body = client.get("/v1/sources", headers=AUTH).json()
    assert body[0]["display_name"] == "My photos" and body[0]["files"] == {"present": 1}
    assert body[0]["last_scan"]["status"] == "succeeded"
    assert client.post(f"/v1/sources/{sid}/scan", headers=AUTH).status_code == 200
    assert client.post("/v1/sources/999/scan", headers=AUTH).status_code == 409
    assert client.get("/v1/settings/scan", headers=AUTH).json() == {"include_online_only": False}
    mgr.stop()


# --------------------------------------------------------------------------- kill -9, large tree
def _start_engine(lib: Path):
    env = {**os.environ, "IV_TOKEN": TOKEN}
    env.pop("IV_PARENT_PID", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "ivsidecar", "--library", str(lib)], stdout=subprocess.PIPE, env=env, text=True
    )
    ready = json.loads(proc.stdout.readline())
    return proc, f"http://127.0.0.1:{ready['port']}"


def test_kill_9_mid_scan_resumes(tmp_path):
    """Plan B6 acceptance (scaled by IV_SCAN_N, default 20k; CI runs 100k): a scan killed
    partway resumes from the last folder and ends with every file registered exactly once."""
    n = int(os.environ.get("IV_SCAN_N", "20000"))
    per_dir = 100
    lib, src = tmp_path / "Library", tmp_path / "Photos"
    t0 = time.perf_counter()
    for i in range(n):
        d = src / f"y{i // (per_dir * 50):03d}" / f"m{(i // per_dir) % 50:02d}"
        if i % per_dir == 0:
            d.mkdir(parents=True)
        (d / f"IMG_{i:06d}.jpg").write_bytes(b"")
    gen_s = time.perf_counter() - t0
    conn = db.open_library(lib)
    settings.save(conn, "queue", settings.QueueState(paused=True))  # measure the scan alone

    proc, base = _start_engine(lib)
    try:
        t1 = time.perf_counter()
        sid = httpx.post(f"{base}/v1/sources", json={"path": str(src)}, headers=AUTH, timeout=30).json()["id"]
        while True:
            seen = conn.execute("SELECT files_seen FROM scan_runs").fetchone()[0]
            if seen >= n // 3:
                break
            assert time.perf_counter() - t1 < 300
            time.sleep(0.05)
    finally:
        proc.kill()
        proc.wait(10)
    token = conn.execute("SELECT status, resume_token FROM scan_runs").fetchone()
    assert token[0] == "running" and token[1], token

    proc, base = _start_engine(lib)  # resumes interrupted scans at startup
    try:
        t2 = time.perf_counter()
        while conn.execute("SELECT status FROM scan_runs").fetchone()[0] == "running":
            assert time.perf_counter() - t2 < 600
            time.sleep(0.1)
        resume_s = time.perf_counter() - t2
        run = conn.execute("SELECT status, files_seen FROM scan_runs").fetchone()
        assert run[0] == "succeeded" and run[1] <= n  # resumed: files from finished folders not counted again
        assert conn.execute("SELECT count(*), count(DISTINCT file_key) FROM file_locations").fetchone() == (n, n)
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == n

        # second scan of the unchanged tree: nothing new, nothing queued
        t3 = time.perf_counter()
        httpx.post(f"{base}/v1/sources/{sid}/scan", headers=AUTH, timeout=30)
        while conn.execute("SELECT status FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "running":
            time.sleep(0.1)
        rescan_s = time.perf_counter() - t3
        last = conn.execute(
            "SELECT status, files_seen, files_new, files_changed FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert last == ("succeeded", n, 0, 0)
    finally:
        proc.terminate()
        proc.wait(10)
    print(
        f"\nscan N={n}: generate {gen_s:.1f} s, resume {resume_s:.1f} s, unchanged rescan {rescan_s:.1f} s "
        f"({n / rescan_s:,.0f} files/s)"
    )
