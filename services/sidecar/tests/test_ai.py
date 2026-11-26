"""T2: AI tag + embedding stages through the real job queue (models faked), the model
downloader, and the /v1/ai routes. Real CLIP/RAM++ inference: test_ai_models.py."""

from __future__ import annotations

import hashlib
import io
import os
import time

import jobhelpers
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_jobs import AUTH, TOKEN, _drain, _library, _register_all

from ivsidecar import ai_models, handlers, settings
from ivsidecar import library as lib_mod
from ivsidecar.app import create_app
from ivsidecar.jobqueue import JobQueue
from ivsidecar.probes import find_exiftool

if not find_exiftool() and os.environ.get("IV_REQUIRE_EXIFTOOL") != "1":
    pytest.skip("exiftool not installed", allow_module_level=True)


def _photos(src, colours=("red", "green", "blue")):
    for c in colours:
        Image.new("RGB", (64, 48), c).save(src / f"{c}.png")


def _run(lib, conn, threads=2):
    q = JobQueue(lib, stage_fn=jobhelpers.fake_ai)
    q.start()
    try:
        _drain(conn)
    finally:
        q.stop()


def _tags(conn, name):
    return dict(
        conn.execute(
            "SELECT t.name, x.origin || ':' || x.rejected FROM asset_tags x JOIN tags t ON t.id = x.tag_id "
            "JOIN file_locations fl ON fl.asset_id = x.asset_id WHERE fl.rel_path = ?",
            (name,),
        ).fetchall()
    )


def _asset(conn, name):
    return conn.execute("SELECT asset_id FROM file_locations WHERE rel_path = ?", (name,)).fetchone()[0]


def test_ai_off_by_default_then_backfill_tags_and_embeds_every_photo(tmp_path):
    lib, src, conn = _library(tmp_path)
    _photos(src)
    _register_all(conn, src)
    _run(lib, conn)
    assert conn.execute("SELECT count(*) FROM jobs WHERE stage IN ('embed', 'classify')").fetchone()[0] == 0

    settings.save(conn, "ai", settings.AiOptions(enabled=True))
    assert handlers.enqueue_ai_backfill(conn) == 3
    assert handlers.enqueue_ai_backfill(conn) == 3  # idempotent: same jobs, nothing added
    assert conn.execute("SELECT count(*) FROM jobs WHERE stage IN ('embed', 'classify')").fetchone()[0] == 6
    _run(lib, conn)

    assert _tags(conn, "red.png") == {"red thing": "ai:0", "object": "ai:0"}
    assert _tags(conn, "blue.png")["blue thing"] == "ai:0"
    conf = conn.execute("SELECT confidence FROM asset_tags x JOIN tags t ON t.id = x.tag_id WHERE t.name = 'red thing'")
    assert conf.fetchone()[0] == pytest.approx(0.9)
    # one embedding per photo, in the vector index, with the model recorded
    assert conn.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM vec_clip_b32").fetchone()[0] == 3
    assert conn.execute(
        "SELECT name, dims FROM embedding_models WHERE is_active = 1 AND modality = 'image'"
    ).fetchall() == [(ai_models.CLIP_NAME, 512)]
    # AI tags are searchable like any tag
    rid = _asset(conn, "red.png")
    assert conn.execute(
        "SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'tags:thing' AND rowid = ?", (rid,)
    ).fetchone()
    # a nearest-neighbour query works on the stored vectors
    near = conn.execute(
        "SELECT rowid FROM vec_clip_b32 WHERE embedding MATCH (SELECT embedding FROM vec_clip_b32 WHERE rowid = "
        "(SELECT id FROM embeddings WHERE asset_id = ?)) AND k = 1",
        (rid,),
    ).fetchone()[0]
    assert conn.execute("SELECT asset_id FROM embeddings WHERE id = ?", (near,)).fetchone()[0] == rid


def test_new_photos_are_tagged_automatically_once_ai_is_on(tmp_path):
    lib, src, conn = _library(tmp_path)
    settings.save(conn, "ai", settings.AiOptions(enabled=True, max_tags=1))
    _photos(src, ("green",))
    _register_all(conn, src)
    _run(lib, conn)
    assert _tags(conn, "green.png") == {"green thing": "ai:0"}  # max_tags respected
    # AI jobs queue after the photo itself is ready (lower priority than stages 1-4)
    prio = dict(conn.execute("SELECT stage, priority FROM jobs WHERE asset_id IS NOT NULL").fetchall())
    assert prio["classify"] == prio["thumbnail"] + handlers.AI_PRIORITY_OFFSET


def test_removed_ai_tags_never_return_and_your_tags_are_kept(tmp_path):
    lib, src, conn = _library(tmp_path)
    settings.save(conn, "ai", settings.AiOptions(enabled=True))
    _photos(src, ("red",))
    _register_all(conn, src)
    _run(lib, conn)
    aid = _asset(conn, "red.png")
    # you remove "object" (rejected), confirm "red thing" and add your own tag
    with lib_mod.tx(conn):
        (obj,) = lib_mod.get_or_create_tags(conn, ["object"])
        conn.execute("UPDATE asset_tags SET rejected = 1 WHERE asset_id = ? AND tag_id = ?", (aid, obj))
        (mine,) = lib_mod.get_or_create_tags(conn, ["holiday"])
        conn.execute("INSERT INTO asset_tags (asset_id, tag_id, origin) VALUES (?, ?, 'user')", (aid, mine))
    # the model runs again (e.g. a newer model) and suggests the same tags
    with lib_mod.tx(conn):
        handlers.apply_ai_tags(conn, aid, [("red thing", 0.95), ("object", 0.9)])
    assert _tags(conn, "red.png") == {"red thing": "ai:0", "object": "ai:1", "holiday": "user:0"}
    assert conn.execute(
        "SELECT confidence FROM asset_tags x JOIN tags t ON t.id = x.tag_id WHERE t.name = 'red thing'"
    ).fetchone()[0] == pytest.approx(0.95)


def test_switching_ai_off_drops_only_queued_ai_jobs(tmp_path):
    lib, src, conn = _library(tmp_path)
    _photos(src)
    _register_all(conn, src)
    _run(lib, conn)
    settings.save(conn, "ai", settings.AiOptions(enabled=True))
    handlers.enqueue_ai_backfill(conn)
    assert handlers.cancel_queued_ai(conn) == 6
    assert conn.execute("SELECT count(*) FROM jobs WHERE stage IN ('embed', 'classify')").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'succeeded'").fetchone()[0] > 0


def test_ai_jobs_only_run_in_the_first_slot(tmp_path):
    lib, src, conn = _library(tmp_path, threads=3)
    settings.save(conn, "ai", settings.AiOptions(enabled=True))
    _photos(src)
    _register_all(conn, src)
    q = JobQueue(lib, stage_fn=jobhelpers.fake_ai)
    # a slot other than 0 never claims an AI job, even when it is the only one waiting
    _run(lib, conn)
    handlers.enqueue_ai(conn, _asset(conn, "red.png"), 1)
    assert q._claim(1) is None
    job = q._claim(0)
    assert job["stage"] in ("embed", "classify")


# --------------------------------------------------------------------------- downloader
def _fake_files(tmp_path, monkeypatch, payloads: dict[str, bytes]):
    files = tuple(
        ai_models.ModelFile(name, f"https://example.invalid/{name}", len(b), hashlib.sha256(b).hexdigest())
        for name, b in payloads.items()
    )
    monkeypatch.setattr(ai_models, "FILES", files)
    monkeypatch.setattr(ai_models, "TOTAL_BYTES", sum(f.size for f in files))
    monkeypatch.delenv("IV_MODELS_DIR", raising=False)
    return files


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status


def test_download_verifies_resumes_and_rejects_bad_files(tmp_path, monkeypatch):
    data = {"clip/a.onnx": b"A" * 3000, "ram/b.npy": b"B" * 5000}
    _fake_files(tmp_path, monkeypatch, data)
    lib = tmp_path / "Library"
    calls = []

    def opener(url, start):
        name = url.split("example.invalid/")[1]
        calls.append((name, start))
        body = data[name]
        return _Resp(body[start:], 206) if start else _Resp(body)

    # a half-finished earlier download resumes where it stopped
    part = lib / "models" / "ram" / "b.npy.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"B" * 2000)
    seen = []
    ai_models.download(lib, lambda d, t: seen.append((d, t)), opener=opener)
    assert ("ram/b.npy", 2000) in calls
    assert ai_models.installed(lib)
    assert seen[-1] == (8000, 8000)
    assert not part.exists()
    # nothing is fetched again once verified
    calls.clear()
    ai_models.download(lib, opener=opener)
    assert calls == []

    # a corrupted file is noticed and replaced
    (lib / "models" / "clip" / "a.onnx").write_bytes(b"X" * 3000)
    (lib / "models" / "clip" / "a.onnx.ok").unlink()
    assert not ai_models.installed(lib)
    ai_models.download(lib, opener=opener)
    assert ai_models.installed(lib)

    # a download that does not match its checksum is discarded, never used
    def bad(url, start):
        return _Resp(b"Z" * 3000)

    (lib / "models" / "clip" / "a.onnx").unlink()
    with pytest.raises(ai_models.DownloadError, match="checksum"):
        ai_models.download(lib, opener=bad)
    assert not (lib / "models" / "clip" / "a.onnx").exists()
    assert not (lib / "models" / "clip" / "a.onnx.part").exists()


def test_network_error_keeps_the_partial_file(tmp_path, monkeypatch):
    _fake_files(tmp_path, monkeypatch, {"clip/a.onnx": b"A" * 3000})
    lib = tmp_path / "Library"

    class Broken(_Resp):
        def read(self, n=-1):
            if self.tell() >= 1000:
                raise ConnectionResetError("connection reset")
            return super().read(1000)

    with pytest.raises(ai_models.DownloadError, match="connection reset"):
        ai_models.download(lib, opener=lambda u, s: Broken(b"A" * 3000))
    assert (lib / "models" / "clip" / "a.onnx.part").stat().st_size == 1000


# --------------------------------------------------------------------------- API
def test_ai_routes_install_switch_and_status(tmp_path, monkeypatch):
    data = {"clip/a.onnx": b"A" * 100}
    _fake_files(tmp_path, monkeypatch, data)
    monkeypatch.setattr(ai_models, "_open", lambda url, start: _Resp(data["clip/a.onnx"]))
    lib, src, conn = _library(tmp_path)
    _photos(src, ("red", "blue"))
    _register_all(conn, src)
    _run(lib, conn)
    app = create_app(conn, TOKEN, time.time())
    with TestClient(app) as c:
        st = c.get("/v1/ai", headers=AUTH).json()
        assert st["installed"] is False and st["enabled"] is False and st["download"]["state"] == "idle"
        r = c.put("/v1/settings/ai", json={"enabled": True, "max_tags": 8}, headers=AUTH)
        assert r.status_code == 409 and "download" in r.json()["detail"]

        assert c.post("/v1/ai:install", headers=AUTH).status_code == 202
        deadline = time.monotonic() + 10
        while (
            c.get("/v1/ai", headers=AUTH).json()["download"]["state"] == "downloading" and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        st = c.get("/v1/ai", headers=AUTH).json()
        assert st["installed"] and st["enabled"] and st["download"]["state"] == "done"
        assert st["photos_waiting"] == 2  # every existing photo queued for tags

        r = c.put("/v1/settings/ai", json={"enabled": False, "max_tags": 5}, headers=AUTH)
        assert r.status_code == 200
        st = c.get("/v1/ai", headers=AUTH).json()
        assert st["enabled"] is False and st["max_tags"] == 5 and st["photos_waiting"] == 0
        assert c.put("/v1/settings/ai", json={"enabled": True, "max_tags": 50}, headers=AUTH).status_code == 422


# --------------------------------------------------------------------------- T3: review and groups
def test_ai_tags_have_their_own_facet_section_until_accepted(tmp_path):
    lib, src, conn = _library(tmp_path)
    settings.save(conn, "ai", settings.AiOptions(enabled=True))
    _photos(src)
    _register_all(conn, src)
    _run(lib, conn)
    f = lib_mod.asset_facets(conn)
    assert f["tags"] == []
    ai = {t["name"]: t["count"] for t in f["ai_tags"]}
    assert ai == {"object": 3, "red thing": 1, "green thing": 1, "blue thing": 1}
    # accepting the AI tags on one photo makes them yours everywhere; counts still cover every photo
    rid = _asset(conn, "red.png")
    with lib_mod.tx(conn):
        out = lib_mod.bulk(conn, [rid], {"confirm_ai_tags": True})
    assert out == {"photos": 1, "ai_tags_confirmed": 2}
    f = lib_mod.asset_facets(conn)
    assert {t["name"]: t["count"] for t in f["tags"]} == {"object": 3, "red thing": 1}
    assert {t["name"] for t in f["ai_tags"]} == {"green thing", "blue thing"}
    # a removed AI tag leaves both sections and the group
    with lib_mod.tx(conn):
        lib_mod.bulk(conn, [_asset(conn, "blue.png")], {"remove_tags": ["blue thing"]})
    f = lib_mod.asset_facets(conn)
    assert {t["name"] for t in f["ai_tags"]} == {"green thing"}
    groups = {g["name"]: g["count"] for g in lib_mod.ai_groups(conn)}
    assert groups == {"object": 3, "red thing": 1, "green thing": 1}


def test_ai_groups_route_and_bulk_accept_route(tmp_path):
    lib, src, conn = _library(tmp_path)
    settings.save(conn, "ai", settings.AiOptions(enabled=True))
    _photos(src, ("red", "blue"))
    _register_all(conn, src)
    _run(lib, conn)
    app = create_app(conn, TOKEN, time.time())
    with TestClient(app) as c:
        groups = c.get("/v1/ai/groups", headers=AUTH).json()
        assert groups[0] == {"id": groups[0]["id"], "name": "object", "count": 2}
        rid = _asset(conn, "red.png")
        r = c.post("/v1/assets:bulk", json={"ids": [rid], "confirm_ai_tags": True}, headers=AUTH)
        assert r.status_code == 200 and r.json() == {"photos": 1, "ai_tags_confirmed": 2}
        tags = {t["name"]: t for t in c.get(f"/v1/assets/{rid}", headers=AUTH).json()["tags"]}
        assert tags["red thing"]["origin"] == "ai" and tags["red thing"]["confirmed"] is True
        # hidden photos do not count in groups
        c.post(f"/v1/assets/{rid}/hide", headers=AUTH)
        assert {g["name"]: g["count"] for g in c.get("/v1/ai/groups", headers=AUTH).json()} == {
            "object": 1,
            "blue thing": 1,
        }
