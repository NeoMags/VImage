"""B19: backups, restore and export (doc 04 §6, PRD F14)."""

from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import time
import tracemalloc
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_library import AUTH, TOKEN, _seed

from ivsidecar import backups, db, settings
from ivsidecar import export as export_mod
from ivsidecar.api_backup import LibraryState
from ivsidecar.app import create_app


def _client(conn, state=None):
    return TestClient(create_app(conn, TOKEN, time.time(), library_state=state))


@pytest.fixture()
def lib(tmp_path):
    library = tmp_path / "Library"
    conn = db.open_library(library)
    _seed(conn, 30, root=str(tmp_path / "Photos"))
    client = _client(conn)
    yield library, conn, client
    conn.close()


def ok(r, code=200):
    assert r.status_code in (code, 204), r.text
    return r.json() if r.status_code != 204 else None


def user_data(client, asset_id=1) -> dict:
    a = ok(client.get(f"/v1/assets/{asset_id}", headers=AUTH))
    return {
        "tags": sorted(t["name"] for t in a["tags"]),
        "notes": [n["body"] for n in a["notes"]],
        "collections": sorted(c["name"] for c in a["collections"]),
        "title": a["title"]["value"],
    }


def edit(client, asset_id=1):
    ok(client.post(f"/v1/assets/{asset_id}/tags", headers=AUTH, json={"names": ["porch", "facade"]}))
    ok(client.post(f"/v1/assets/{asset_id}/notes", headers=AUTH, json={"body": "railing 1.1 m"}), 201)
    coll = ok(client.post("/v1/collections", headers=AUTH, json={"name": "Site visit"}), 201)
    ok(client.post(f"/v1/collections/{coll['id']}/assets", headers=AUTH, json={"ids": [asset_id]}))
    ok(client.patch(f"/v1/assets/{asset_id}", headers=AUTH, json={"title": "Front porch"}))


# ------------------------------------------------------------------------------------- backups
def test_back_up_now_makes_an_intact_copy_and_lists_it(lib):
    library, _conn, client = lib
    made = ok(client.post("/v1/backups", headers=AUTH), 201)
    b = made["backup"]
    assert b["kind"] == "manual" and b["schema_version"] == db.latest_version() and b["size"] > 0
    assert made["pruned"] == [] and made["copy_error"] is None
    path = library / "backups" / b["name"]
    assert db.check_file(path) == db.latest_version()
    assert not list((library / "backups").glob("*.partial"))
    listed = ok(client.get("/v1/backups", headers=AUTH))
    assert [x["name"] for x in listed["backups"]] == [b["name"]]
    assert listed["needs_restore"] is None and listed["folder"] == str(library / "backups")


def test_automatic_backups_are_daily_and_only_the_last_seven_are_kept(lib):
    library, _conn, _client = lib
    t0 = datetime(2026, 1, 1, 9, tzinfo=UTC)
    manual = backups.make_backup(library, "manual", now=t0 - timedelta(days=30))["backup"]["name"]
    made = [backups.auto_backup_if_due(library, now=t0 + timedelta(days=i)) for i in range(10)]
    assert all(made)
    assert backups.auto_backup_if_due(library, now=t0 + timedelta(days=9, hours=23)) is None  # not due yet
    autos = [b for b in backups.list_backups(library) if b["kind"] == "auto"]
    assert len(autos) == 7
    assert autos[0]["created_at"].startswith("2026-01-10")
    assert manual in {b["name"] for b in backups.list_backups(library)}  # manual ones are never pruned
    assert made[-1]["pruned"] == [made[2]["backup"]["name"]]


def test_an_empty_library_is_not_backed_up(tmp_path):
    library = tmp_path / "Library"
    db.open_library(library).close()
    assert backups.auto_backup_if_due(library) is None
    assert backups.list_backups(library) == []


def test_second_copy_goes_to_the_chosen_folder_never_into_a_source(lib, tmp_path):
    library, conn, client = lib
    hdd = tmp_path / "BackupHDD"
    hdd.mkdir()
    ok(client.put("/v1/settings/backup", headers=AUTH, json={"keep": 3, "copy_to": str(hdd)}))
    name = ok(client.post("/v1/backups", headers=AUTH), 201)["backup"]["name"]
    assert (hdd / name).is_file() and not list(hdd.glob("*.partial"))
    (tmp_path / "Photos" / "sub").mkdir(parents=True)
    r = client.put("/v1/settings/backup", headers=AUTH, json={"keep": 3, "copy_to": str(tmp_path / "Photos" / "sub")})
    assert r.status_code == 422 and "source" in r.text
    assert client.put("/v1/settings/backup", headers=AUTH, json={"copy_to": str(tmp_path / "nope")}).status_code == 422
    assert client.put("/v1/settings/backup", headers=AUTH, json={"keep": 0}).status_code == 422
    # The folder disappears (drive unplugged): the backup still counts, the copy error is reported.
    shutil.rmtree(hdd)
    made = ok(client.post("/v1/backups", headers=AUTH), 201)
    assert made["copy_error"] and "not available" in made["copy_error"]
    assert settings.load(conn, "backup").keep == 3


# ------------------------------------------------------------------------------------- restore
def test_restore_brings_back_tags_notes_collections_and_keeps_a_safety_copy(lib):
    library, conn, client = lib
    edit(client)
    before = user_data(client)
    name = ok(client.post("/v1/backups", headers=AUTH), 201)["backup"]["name"]

    # Change everything after the backup.
    a = ok(client.get("/v1/assets/1", headers=AUTH))
    for t in a["tags"]:
        ok(client.delete(f"/v1/assets/1/tags/{t['id']}", headers=AUTH))
    ok(client.patch(f"/v1/assets/1/notes/{a['notes'][0]['id']}", headers=AUTH, json={"body": "changed"}))
    ok(client.delete(f"/v1/collections/{a['collections'][0]['id']}", headers=AUTH))
    ok(client.patch("/v1/assets/1", headers=AUTH, json={"title": "Something else"}))
    ok(client.put("/v1/settings/backup", headers=AUTH, json={"keep": 4}))  # a setting made after the backup
    assert user_data(client) != before

    out = ok(client.post("/v1/backups:restore", headers=AUTH, json={"name": name}))
    assert out["restored_from"] == name and out["schema_version"] == db.latest_version()
    assert user_data(client) == before
    kinds = {b["name"]: b["kind"] for b in backups.list_backups(library)}
    assert kinds[out["pre_restore"]] == "pre-restore"
    assert settings.load(conn, "backup").keep == 4  # this PC's settings survive a restore
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # The safety copy undoes the restore.
    ok(client.post("/v1/backups:restore", headers=AUTH, json={"name": out["pre_restore"]}))
    assert user_data(client)["title"] == "Something else"


def test_restore_from_a_file_on_a_clean_install_reproduces_the_library(lib, tmp_path):
    """PRD F14 acceptance: restore on a clean install reproduces tags/notes/collections."""
    library, _conn, client = lib
    edit(client, 5)
    before = user_data(client, 5)
    name = ok(client.post("/v1/backups", headers=AUTH), 201)["backup"]["name"]
    outside = tmp_path / "usb" / "my library backup.db"
    outside.parent.mkdir()
    shutil.copy(library / "backups" / name, outside)

    fresh = db.open_library(tmp_path / "NewPC" / "Library")
    new = _client(fresh)
    out = ok(new.post("/v1/backups:restore", headers=AUTH, json={"path": str(outside)}))
    assert user_data(new, 5) == before
    assert ok(new.get("/v1/assets", headers=AUTH, params={"limit": 100}))["items"].__len__() == 30
    # No thumbnail files on the new PC: their rows are dropped and the stage queued again.
    assert out["thumbnails_requeued"] == 30
    assert fresh.execute("SELECT count(*) FROM derivatives").fetchone()[0] == 0
    assert (
        fresh.execute("SELECT count(*) FROM jobs WHERE stage = 'thumbnail' AND status = 'queued'").fetchone()[0] == 30
    )
    fresh.close()


def test_restore_refuses_files_it_cannot_trust(lib, tmp_path):
    library, _conn, client = lib
    name = ok(client.post("/v1/backups", headers=AUTH), 201)["backup"]["name"]
    good = library / "backups" / name

    corrupt = tmp_path / "corrupt.db"
    data = bytearray(good.read_bytes())
    assert len(data) > 4096 * 8
    data[4096 * 2 : 4096 * 6] = b"\xff" * (4096 * 4)  # pages 3-6: broken b-tree pages
    corrupt.write_bytes(bytes(data))
    newer = tmp_path / "newer.db"
    shutil.copy(good, newer)
    with sqlite3.connect(newer) as c:
        c.execute("PRAGMA user_version = 999")
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as c:
        c.execute("CREATE TABLE x (y)")
        c.execute("PRAGMA user_version = 3")
    text = tmp_path / "notes.txt"
    text.write_text("hello")

    cases = [
        ({"path": str(corrupt)}, ""),
        ({"path": str(newer)}, "newer version"),
        ({"path": str(other)}, "not an ImageVault"),
        ({"path": str(text)}, ""),
        ({"path": str(tmp_path / "missing.db")}, "not found"),
        ({"name": "../library.db"}, "no such backup"),
        ({"name": name, "path": str(good)}, "either"),
        ({}, "either"),
    ]
    edit(client)
    before = user_data(client)
    for body, words in cases:
        r = client.post("/v1/backups:restore", headers=AUTH, json=body)
        assert r.status_code == 422, (body, r.text)
        assert words in r.text, (body, r.text)
    assert user_data(client) == before  # nothing was touched


def test_a_damaged_library_is_set_aside_and_offered_for_restore(tmp_path):
    library = tmp_path / "Library"
    conn = db.open_library(library)
    _seed(conn, 5)
    backups.make_backup(library, "manual")
    conn.close()
    with open(library / "library.db", "r+b") as f:  # trash the file's header and first pages
        f.write(b"\x00" * 8192)
    with pytest.raises(db.Damaged):
        db.open_library(library)
    damaged = db.quarantine(library)
    assert (library / "backups" / damaged).is_file()
    conn = db.open_library(library)  # what __main__ does next: start empty
    client = _client(conn, LibraryState(damaged))
    assert ok(client.get("/v1/health", headers=AUTH))["needs_restore"] == damaged
    listed = ok(client.get("/v1/backups", headers=AUTH))
    assert listed["needs_restore"] == damaged
    assert {b["kind"] for b in listed["backups"]} == {"manual", "damaged"}
    manual = next(b["name"] for b in listed["backups"] if b["kind"] == "manual")
    ok(client.post("/v1/backups:restore", headers=AUTH, json={"name": manual}))
    assert ok(client.get("/v1/health", headers=AUTH))["needs_restore"] is None
    assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 5
    conn.close()


# ------------------------------------------------------------------------------------- export
def _ai_tag(conn, asset_id, name):
    tid = conn.execute("INSERT INTO tags (name, slug) VALUES (?, ?) RETURNING id", (name, name)).fetchone()[0]
    conn.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, origin, confidence) VALUES (?, ?, 'ai', 0.8)", (asset_id, tid)
    )


def test_json_export_has_everything_and_labels_ai_output(lib, tmp_path):
    _library, conn, client = lib
    edit(client, 2)
    _ai_tag(conn, 2, "staircase")
    ok(client.post("/v1/assets/3/hide", headers=AUTH))
    out = ok(client.post("/v1/export", headers=AUTH, json={"format": "json", "path": str(tmp_path / "lib")}))
    assert out["path"].endswith("lib.json") and out["photos"] == 29 and out["format"] == "json"
    data = json.loads((tmp_path / "lib.json").read_text(encoding="utf-8"))
    assert data["format"] == "imagevault-export" and data["photos"] == 29 and len(data["items"]) == 29
    rec = next(r for r in data["items"] if r["uid"] == "u1")  # asset 2
    assert rec["title"] == {"value": "Front porch", "origin": "user"}
    assert {(t["name"], t["origin"]) for t in rec["tags"]} == {
        ("facade", "user"),
        ("porch", "user"),
        ("staircase", "ai"),
    }
    assert [n["body"] for n in rec["notes"]] == ["railing 1.1 m"]
    assert [c["name"] for c in rec["collections"]] == ["Site visit"]
    assert rec["locations"][0]["path"].endswith("img000001.jpg")
    assert "u2" not in {r["uid"] for r in data["items"]}  # hidden left out
    out = ok(
        client.post(
            "/v1/export",
            headers=AUTH,
            json={"format": "json", "path": str(tmp_path / "all.json"), "include_hidden": True},
        )
    )
    assert out["photos"] == 30


def test_csv_export_opens_in_excel_and_marks_ai_tags(lib, tmp_path):
    _library, conn, client = lib
    edit(client, 2)
    _ai_tag(conn, 2, "staircase")
    ok(client.post("/v1/export", headers=AUTH, json={"format": "csv", "path": str(tmp_path / "lib.csv")}))
    raw = (tmp_path / "lib.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert len(rows) == 30 and list(rows[0]) == list(export_mod.CSV_COLUMNS)
    row = next(r for r in rows if r["uid"] == "u1")
    assert row["tags"] == "facade; porch; staircase (AI)"
    assert row["collections"] == "Site visit" and row["notes"] == "1" and row["title"] == "Front porch"


def test_export_never_writes_into_a_source_folder(lib, tmp_path):
    _library, _conn, client = lib
    (tmp_path / "Photos").mkdir()
    for path, words in [
        (tmp_path / "Photos" / "x.json", "source folder"),
        ("relative.json", "full path"),
        (tmp_path / "missing" / "x.json", "folder not found"),
    ]:
        r = client.post("/v1/export", headers=AUTH, json={"format": "json", "path": str(path)})
        assert r.status_code == 422 and words in r.text, r.text
    assert list((tmp_path / "Photos").iterdir()) == []


def test_export_memory_stays_flat_on_a_big_library(tmp_path):
    conn = db.open_library(tmp_path / "Library")
    _seed(conn, 20_000)
    tracemalloc.start()
    out = export_mod.export(conn, tmp_path / "big.json", "json")
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    conn.close()
    assert out["photos"] == 20_000
    assert peak < 40 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"  # chunks of 1000, not the whole library


def test_the_engine_starts_on_a_damaged_library_instead_of_stopping(tmp_path):
    """__main__: the real start path sets the damaged file aside and reports it."""
    import json as _json
    import os
    import subprocess
    import sys

    import httpx

    library = tmp_path / "Library"
    library.mkdir()
    (library / "library.db").write_bytes(b"this is not a database" * 500)
    env = {**os.environ, "IV_TOKEN": TOKEN}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ivsidecar", "--library", str(library)], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        ready = _json.loads(proc.stdout.readline())  # type: ignore[union-attr]
        assert ready["event"] == "ready"
        health = httpx.get(f"http://127.0.0.1:{ready['port']}/v1/health", headers=AUTH, timeout=30).json()
        assert health["needs_restore"].startswith("damaged-")
        assert (library / "backups" / health["needs_restore"]).read_bytes().startswith(b"this is not a database")
    finally:
        proc.terminate()
        proc.wait(20)
