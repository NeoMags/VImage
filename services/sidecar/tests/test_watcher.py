"""B7: live watching, reconcile, unplugged drives, edits, removing a folder (doc 05 §2-3)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ivsidecar import db, scanner
from ivsidecar.app import create_app
from ivsidecar.jobqueue import JobQueue
from ivsidecar.watcher import FolderWatcher

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _wait(cond, timeout: float = 30, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _jpeg(path: Path, seed: int, size: int = 40) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 256, (size, size, 3), dtype=np.uint8)).save(path, quality=85)
    return path


def _touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _locs(conn) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in conn.execute("SELECT rel_path, id, file_key, state FROM file_locations")}


def _idle(sc: scanner.Scanner, w: FolderWatcher | None = None) -> bool:
    if sc.running():
        return False
    if w is None:
        return True
    with w._cv:
        return not w._pending and not w._full


@pytest.fixture()
def lib_src(tmp_path):
    lib, src = tmp_path / "Library", tmp_path / "Photos"
    src.mkdir()
    conn = db.open_library(lib)
    sid = scanner.add_source(conn, str(src), lib)
    sc = scanner.Scanner(lib)
    watchers: list[FolderWatcher] = []

    def make_watcher(**kw) -> FolderWatcher:
        kw = {"debounce_s": 0.3, "young_s": 0.0, **kw}
        w = FolderWatcher(lib, sc, **kw)
        watchers.append(w)
        return w

    yield lib, src, conn, sid, sc, make_watcher
    for w in watchers:
        w.stop()
    sc.stop()
    conn.close()


def _full_scan(sc, sid):
    sc.start(sid)
    _wait(lambda: not sc.running(), what="full scan")


def _drain(conn, timeout_s: float = 120) -> None:
    def idle():
        return (
            conn.execute(
                "SELECT count(*) FROM jobs WHERE status = 'running' OR (status = 'queued' AND run_after <= "
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
            ).fetchone()[0]
            == 0
        )

    _wait(idle, timeout_s, "job queue to drain")


# --------------------------------------------------------------------------- live watching
def test_new_changed_deleted_files_are_picked_up_live(lib_src):
    lib, src, conn, sid, sc, make_watcher = lib_src
    _touch(src / "a.jpg")
    _touch(src / "sub/b.jpg")
    w = make_watcher()
    w.start()
    _wait(lambda: _idle(sc, w) and len(_locs(conn)) == 2, what="startup reconcile")
    key_a = _locs(conn)["a.jpg"][1]

    _touch(src / "new/deep/c.jpg")  # folder created with a file in it
    os.rename(src / "a.jpg", src / "sub/a_moved.jpg")  # move between folders keeps identity
    (src / "sub/b.jpg").unlink()
    _wait(
        lambda: (
            {k: v[2] for k, v in _locs(conn).items()}
            == {"sub/a_moved.jpg": "present", "sub/b.jpg": "missing", "new/deep/c.jpg": "present"}
        ),
        what="watch batch",
    )
    assert _locs(conn)["sub/a_moved.jpg"][1] == key_a
    assert w.batches >= 1
    kinds = {r[0] for r in conn.execute("SELECT kind FROM scan_runs")}
    assert kinds == {"full", "watch_batch"}

    # a whole folder moved out of the source: everything under it is missing
    os.rename(src / "new", src.parent / "elsewhere")
    _wait(lambda: _locs(conn)["new/deep/c.jpg"][2] == "missing", what="folder moved out")


def test_hidden_and_excluded_folders_are_ignored_by_batches(lib_src):
    lib, src, conn, sid, sc, make_watcher = lib_src
    w = make_watcher()
    w.start()
    _wait(lambda: _idle(sc, w), what="startup")
    _touch(src / ".hidden/x.jpg")
    _touch(src / "$RECYCLE.BIN/y.jpg")
    _touch(src / "ok/z.jpg")
    _wait(lambda: "ok/z.jpg" in _locs(conn), what="batch")
    assert set(_locs(conn)) == {"ok/z.jpg"}


def test_library_inside_source_does_not_trigger_batches(tmp_path):
    root = tmp_path / "Pictures"
    lib = root / "ImageVault"
    root.mkdir()
    conn = db.open_library(lib)
    sid = scanner.add_source(conn, str(root), lib)
    sc = scanner.Scanner(lib)
    w = FolderWatcher(lib, sc, debounce_s=0.2, young_s=0)
    w.start()
    try:
        _wait(lambda: _idle(sc, w), what="startup")
        n_runs = conn.execute("SELECT count(*) FROM scan_runs").fetchone()[0]
        for i in range(20):  # database and thumbnail writes inside the library
            _touch(lib / "thumbs" / f"{i}.webp")
            conn.execute("INSERT INTO settings (key, value) VALUES (?, '{}')", (f"k{i}",))
        time.sleep(1.0)
        assert conn.execute("SELECT count(*) FROM scan_runs").fetchone()[0] == n_runs
        assert w.batches == 0
        assert sid
    finally:
        w.stop()
        sc.stop()


def test_files_still_being_written_are_deferred(lib_src):
    lib, src, conn, sid, sc, make_watcher = lib_src
    w = make_watcher(debounce_s=0.2, young_s=1.5)
    w.start()
    _wait(lambda: _idle(sc, w), what="startup")
    _touch(src / "copying.jpg")
    time.sleep(0.9)  # flushed once already, but the file is younger than 1.5 s
    assert "copying.jpg" not in _locs(conn)
    _wait(lambda: "copying.jpg" in _locs(conn), timeout=10, what="deferred file")


def test_rename_storm_10k_keeps_identity(lib_src):
    """Plan B7 acceptance: renaming 10,000 files loses nothing and creates no duplicates."""
    lib, src, conn, sid, sc, make_watcher = lib_src
    n = int(os.environ.get("IV_STORM_N", "10000"))
    for i in range(n):
        _touch(src / f"d{i // 100:03d}" / f"f{i:05d}.jpg", b"")
    _full_scan(sc, sid)
    before = {v[0]: v[1] for v in _locs(conn).values()}  # location id -> file key
    assert len(before) == n
    w = make_watcher()
    w.start(reconcile_now=False)
    t0 = time.monotonic()
    for i in range(n):
        d = src / f"d{i // 100:03d}"
        os.rename(d / f"f{i:05d}.jpg", d / f"renamed_{i:05d}.jpg")
    t_rename = time.monotonic() - t0

    def done():
        if not _idle(sc, w):
            return False
        locs = _locs(conn)
        return len(locs) == n and all(
            Path(k).name.startswith("renamed_") and v[2] == "present" for k, v in locs.items()
        )

    _wait(done, timeout=180, what="rename storm to settle")
    after = {v[0]: v[1] for v in _locs(conn).values()}
    assert after == before  # same rows, same file keys: tags would stay attached
    print(f"\n{n} renames in {t_rename:.1f}s, settled after {time.monotonic() - t0:.1f}s, batches={w.batches}")


# --------------------------------------------------------------------------- reconcile
def test_changes_made_while_the_app_was_closed(lib_src):
    lib, src, conn, sid, sc, make_watcher = lib_src
    _touch(src / "keep.jpg")
    _touch(src / "gone.jpg")
    _full_scan(sc, sid)
    # app closed: no watcher running
    (src / "gone.jpg").unlink()
    _touch(src / "added/new.jpg")
    w = make_watcher()
    w.start()  # startup reconcile
    _wait(
        lambda: (
            _idle(sc, w)
            and {k: v[2] for k, v in _locs(conn).items()}
            == {"keep.jpg": "present", "gone.jpg": "missing", "added/new.jpg": "present"}
        ),
        what="startup reconcile",
    )
    assert sid in w.watched()


def test_events_lost_triggers_full_scan(lib_src):
    from watchdog.events import EventsLostEvent

    lib, src, conn, sid, sc, make_watcher = lib_src
    w = make_watcher()
    w.start(reconcile_now=False)
    _touch(src / "x.jpg")  # pretend its event was lost
    w._on_event(sid, str(src), EventsLostEvent(str(src)))
    _wait(lambda: "x.jpg" in _locs(conn), what="full scan after lost events")
    assert conn.execute("SELECT count(*) FROM scan_runs WHERE kind = 'full'").fetchone()[0] >= 1


# --------------------------------------------------------------------------- unplugged drives
def test_unplug_and_replug(tmp_path, monkeypatch):
    drive = tmp_path / "Drive"
    src = drive / "Photos"
    _touch(src / "a.jpg")
    lib = tmp_path / "Library"
    conn = db.open_library(lib)
    sid = scanner.add_source(conn, str(src), lib)
    sc = scanner.Scanner(lib)
    w = FolderWatcher(lib, sc, debounce_s=0.2, young_s=0)
    try:
        w.start()
        _wait(lambda: _idle(sc, w) and "a.jpg" in _locs(conn), what="first scan")

        # A real unplug invalidates the watch handle; Windows refuses to rename a folder that has
        # a watched folder inside it, so the simulation drops the watch first.
        w._unschedule(sid)
        os.rename(drive, tmp_path / "Drive_unplugged")  # drive removed
        w.check_sources()
        assert conn.execute("SELECT status FROM sources").fetchone()[0] == "unreachable"
        assert _locs(conn)["a.jpg"][2] == "unreachable"  # not missing: nothing was deleted
        assert sid not in w.watched()
        with pytest.raises(scanner.SourceError, match="cannot be reached"):
            sc.start(sid)

        os.rename(tmp_path / "Drive_unplugged", drive)  # plugged back in, one file added elsewhere
        _touch(src / "b.jpg")
        w.check_sources()
        assert conn.execute("SELECT status FROM sources").fetchone()[0] == "active"
        assert sid in w.watched()
        _wait(lambda: _idle(sc, w) and "b.jpg" in _locs(conn), what="rescan after replug")
        assert _locs(conn)["a.jpg"][2] == "present"

        # comes back under another drive letter: found again by its volume id
        w._unschedule(sid)
        os.rename(drive, tmp_path / "E_drive")
        moved = tmp_path / "E_drive" / "Photos"
        monkeypatch.setattr(scanner, "candidate_roots", lambda root: [str(moved)])
        w.check_sources()
        row = conn.execute("SELECT status, root_path FROM sources").fetchone()
        assert row == ("active", str(moved))
        _touch(moved / "c.jpg")
        _wait(lambda: "c.jpg" in _locs(conn), what="watching the new path")
    finally:
        w.stop()
        sc.stop()


def test_root_deleted_event_triggers_reachability_check(lib_src):
    lib, src, conn, sid, sc, make_watcher = lib_src
    w = make_watcher(reach_every_s=3600)
    w.start(reconcile_now=False)
    import shutil

    _touch(src / "a.jpg")
    shutil.rmtree(src)  # Windows and Linux both report the watched folder itself as deleted
    _wait(
        lambda: conn.execute("SELECT status FROM sources").fetchone()[0] == "unreachable",
        timeout=10,
        what="unreachable after root deletion",
    )


# --------------------------------------------------------------------------- edits keep identity
def _run_queue(lib, conn):
    q = JobQueue(lib)
    q.start()
    try:
        _drain(conn)
    finally:
        q.stop()


def _tag(conn, asset_id: int, name: str) -> None:
    tid = conn.execute(
        "INSERT INTO tags (name, slug) VALUES (?, ?) ON CONFLICT (slug) DO UPDATE SET name = name RETURNING id",
        (name, name),
    ).fetchone()[0]
    conn.execute("INSERT INTO asset_tags (asset_id, tag_id, origin) VALUES (?, ?, 'user')", (asset_id, tid))


def _asset_of(conn, rel):
    return conn.execute("SELECT asset_id FROM file_locations WHERE rel_path = ?", (rel,)).fetchone()[0]


def _rewrite(path: Path, seed: int) -> None:
    ino = path.stat().st_ino
    st = path.stat()
    _jpeg(path, seed)  # rewrites in place (same file id)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert path.stat().st_ino == ino


def test_edits_keep_asset_and_tags(lib_src):
    lib, src, conn, sid, sc, _ = lib_src
    _jpeg(src / "solo.jpg", 1)
    _jpeg(src / "shared.jpg", 2)
    (src / "shared_copy.jpg").write_bytes((src / "shared.jpg").read_bytes())
    _jpeg(src / "other.jpg", 3)
    _full_scan(sc, sid)
    _run_queue(lib, conn)
    solo, shared, other = _asset_of(conn, "solo.jpg"), _asset_of(conn, "shared.jpg"), _asset_of(conn, "other.jpg")
    assert _asset_of(conn, "shared_copy.jpg") == shared
    _tag(conn, solo, "beach")
    _tag(conn, shared, "family")
    old_sha = conn.execute("SELECT sha256 FROM assets WHERE id = ?", (solo,)).fetchone()[0]
    old_thumb = conn.execute("SELECT phash FROM assets WHERE id = ?", (solo,)).fetchone()[0]

    # 1. edited in place, content lives nowhere else: same asset, new content, tags kept
    _rewrite(src / "solo.jpg", 11)
    # 2. one of two identical files edited: new asset for it, tags copied; the other keeps the old
    _rewrite(src / "shared_copy.jpg", 12)
    _full_scan(sc, sid)
    _run_queue(lib, conn)

    assert _asset_of(conn, "solo.jpg") == solo
    row = conn.execute("SELECT sha256, status, phash FROM assets WHERE id = ?", (solo,)).fetchone()
    assert row[0] != old_sha and row[1] == "ready" and row[2] != old_thumb
    assert conn.execute("SELECT sha256 FROM asset_versions WHERE asset_id = ?", (solo,)).fetchall() == [(old_sha,)]
    assert [
        r[0]
        for r in conn.execute(
            "SELECT t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id WHERE asset_id = ?", (solo,)
        )
    ] == ["beach"]

    new_copy = _asset_of(conn, "shared_copy.jpg")
    assert new_copy not in (shared, solo) and _asset_of(conn, "shared.jpg") == shared
    for aid in (shared, new_copy):
        tags = [
            r[0]
            for r in conn.execute(
                "SELECT t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id WHERE asset_id = ?", (aid,)
            )
        ]
        assert tags == ["family"]
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (new_copy,)).fetchone()[0] == "ready"

    # 3. edited to content that is already in the library: linked to that asset (duplicate)
    (src / "solo.jpg").write_bytes((src / "other.jpg").read_bytes())
    st = (src / "solo.jpg").stat()
    os.utime(src / "solo.jpg", ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    _full_scan(sc, sid)
    _run_queue(lib, conn)
    assert _asset_of(conn, "solo.jpg") == other
    assert conn.execute("SELECT count(*) FROM asset_tags WHERE asset_id = ?", (solo,)).fetchone()[0] == 1  # kept


# --------------------------------------------------------------------------- removing a folder
def test_remove_folder_api(tmp_path):
    lib = tmp_path / "Library"
    a, b = tmp_path / "A", tmp_path / "B"
    _jpeg(a / "only_a.jpg", 1)
    _jpeg(a / "both.jpg", 2)
    b.mkdir()
    (b / "both.jpg").write_bytes((a / "both.jpg").read_bytes())
    conn = db.open_library(lib)
    sc = scanner.Scanner(lib)
    w = FolderWatcher(lib, sc, debounce_s=0.2, young_s=0)
    client = TestClient(create_app(conn, TOKEN, time.time(), None, sc, w))
    try:
        sid_a = client.post("/v1/sources", json={"path": str(a)}, headers=AUTH).json()["id"]
        sid_b = client.post("/v1/sources", json={"path": str(b)}, headers=AUTH).json()["id"]
        _wait(lambda: not sc.running(), what="scans")
        _run_queue(lib, conn)
        only_a = _asset_of(conn, "only_a.jpg")
        both = conn.execute("SELECT asset_id FROM file_locations WHERE source_id = ?", (sid_b,)).fetchone()[0]
        _tag(conn, only_a, "gone-with-folder")
        thumbs = [lib / r[0] for r in conn.execute("SELECT rel_path FROM derivatives WHERE asset_id = ?", (only_a,))]
        assert thumbs and all(p.exists() for p in thumbs)
        assert {s["id"]: s["watching"] for s in client.get("/v1/sources", headers=AUTH).json()} == {
            sid_a: True,
            sid_b: True,
        }

        r = client.get(f"/v1/sources/{sid_a}/removal", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["files"] == 2 and r.json()["assets_only_here"] == 1
        assert client.delete(f"/v1/sources/{sid_a}?confirm=0", headers=AUTH).status_code == 409
        assert sid_a in w.watched()  # not removed: still watched
        assert client.delete(f"/v1/sources/{sid_a}", headers=AUTH).status_code == 422  # confirm required

        r = client.delete(f"/v1/sources/{sid_a}?confirm=1", headers=AUTH)
        assert r.status_code == 200, r.text
        assert r.json()["assets_deleted"] == 1 and r.json()["thumbnails_deleted"] == len(thumbs)
        assert [s["id"] for s in client.get("/v1/sources", headers=AUTH).json()] == [sid_b]
        assert {r[0] for r in conn.execute("SELECT id FROM assets")} == {both}
        assert conn.execute("SELECT count(*) FROM asset_tags").fetchone()[0] == 0
        assert not any(p.exists() for p in thumbs)
        assert (a / "only_a.jpg").exists() and (a / "both.jpg").exists()  # the folder is never touched
        assert sid_a not in w.watched()
        assert conn.execute("SELECT count(*) FROM audit_log WHERE entity = 'source'").fetchone()[0] == 1
        assert client.get(f"/v1/sources/{sid_a}/removal", headers=AUTH).status_code == 404
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        w.stop()
        sc.stop()
