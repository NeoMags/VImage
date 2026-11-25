"""B8a: library data API (doc 04 §2): browse, edit, tags, collections, notes, bulk."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import time
from importlib import resources

import pytest
from fastapi.testclient import TestClient

from ivsidecar import db, handlers
from ivsidecar.app import create_app

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _seed(conn, n: int, root: str = "/photos", start: int = 0) -> None:
    """n synthetic assets with one present location and a thumbnail each (fast, no files)."""
    if conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('s1', 'local_folder', 'P', ?)", (root,)
        )
    conn.execute("BEGIN")
    for i in range(start, start + n):
        aid = conn.execute(
            "INSERT INTO assets (uid, sha256, kind, status, mime_type, byte_size, width, height, taken_at, "
            "taken_at_origin, file_modified_at) VALUES (?, ?, ?, 'ready', 'image/jpeg', ?, 100, 80, ?, ?, ?) RETURNING id",
            (
                f"u{i}",
                i.to_bytes(32, "big"),
                "screenshot" if i % 10 == 0 else "image",
                1000 + i,
                None if i % 7 == 0 else f"2024-01-{1 + i % 28:02d}T10:{i % 60:02d}:00",
                None if i % 7 == 0 else "source",
                f"2023-06-{1 + i % 28:02d}T08:00:00",
            ),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO file_locations (source_id, asset_id, file_key, rel_path, size_bytes, mtime) "
            "VALUES (1, ?, ?, ?, 1, '1')",
            (aid, f"k{i}", f"dir{i // 100}/img{i:06d}.jpg"),
        )
        conn.execute(
            "INSERT INTO derivatives (asset_id, kind, rel_path, width, height, byte_size) "
            "VALUES (?, 'thumb_256', ?, 256, 200, 10)",
            (aid, f"thumbs/{i}.webp"),
        )
    conn.execute("COMMIT")
    handlers.refresh_fts(conn, range(1, start + n + 1))


@pytest.fixture()
def api(tmp_path):
    conn = db.open_library(tmp_path / "Library")
    client = TestClient(create_app(conn, TOKEN, time.time()))
    yield conn, client
    conn.close()


def _get(client, url, **params):
    r = client.get(url, headers=AUTH, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _walk(client, **params) -> list[int]:
    ids, cursor = [], None
    while True:
        page = _get(client, "/v1/assets", **params, **({"cursor": cursor} if cursor else {}))
        ids += [a["id"] for a in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            return ids


# --------------------------------------------------------------------------- migration
def test_migration_0004_converts_mtime_and_fills_search_index(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    conn = db.connect(lib / "library.db")
    for v in ("0001_initial.sql", "0002_job_locations.sql", "0003_scan_seen.sql"):
        sql = resources.files("ivsidecar.migrations").joinpath(v).read_text(encoding="utf-8")
        conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
    conn.execute("PRAGMA user_version = 3")
    conn.execute("INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('s', 'local_folder', 'x', '/x')")
    ns = 1_700_000_000 * 10**9
    conn.execute(
        "INSERT INTO assets (uid, sha256, mime_type, byte_size, title, file_modified_at) "
        "VALUES ('a', zeroblob(32), 'image/jpeg', 1, 'Beach day', ?)",
        (str(ns),),
    )
    conn.execute(
        "INSERT INTO file_locations (source_id, asset_id, file_key, rel_path, size_bytes, mtime) "
        "VALUES (1, 1, 'k', 'Holidays/beach.jpg', 1, ?)",
        (str(ns),),
    )
    conn.close()
    conn = db.open_library(lib)
    mod, sort_date = conn.execute("SELECT file_modified_at, sort_date FROM assets").fetchone()
    assert mod == time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(1_700_000_000)) == sort_date
    assert conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'holidays'").fetchall() == [(1,)]
    assert conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'beach day'").fetchall() == [(1,)]
    assert list((lib / "backups").glob("library-v3-*.db"))


# --------------------------------------------------------------------------- listing
def test_auth_required(api):
    conn, client = api
    assert client.get("/v1/assets").status_code == 401
    assert client.get("/v1/tags").status_code == 401


def test_list_sort_filters_and_keyset_pagination(api):
    conn, client = api
    _seed(conn, 250)
    all_ids = _walk(client, limit=60)
    assert len(all_ids) == 250 and len(set(all_ids)) == 250
    page = _get(client, "/v1/assets", limit=3)
    a = page["items"][0]
    assert set(a) >= {"id", "uid", "kind", "date", "thumb_path", "availability", "is_favorite"}
    assert a["availability"] == "present" and a["thumb_path"].startswith("thumbs/")
    assert a["file_name"].startswith("img") and a["file_name"].endswith(".jpg")  # rel_path dirNN/imgNNN.jpg
    dates = [x["date"] for x in _get(client, "/v1/assets", limit=500)["items"]]
    assert dates == sorted(dates, reverse=True)
    # no capture date -> file modified date (decision 4)
    no_taken = conn.execute("SELECT id, file_modified_at FROM assets WHERE taken_at IS NULL LIMIT 1").fetchone()
    assert _get(client, f"/v1/assets/{no_taken[0]}")["date"] == no_taken[1]

    asc = _walk(client, sort="size", order="asc", limit=100)
    sizes = dict(conn.execute("SELECT id, byte_size FROM assets").fetchall())
    assert [sizes[i] for i in asc] == sorted(sizes.values())
    shots = _walk(client, kind="screenshot")
    assert len(shots) == 25
    assert len(_walk(client, **{"from": "2024-01-01", "to": "2024-01-01"})) == len(
        conn.execute("SELECT id FROM assets WHERE sort_date LIKE '2024-01-01%'").fetchall()
    )

    # a cursor from one sort cannot be used with another
    cur = _get(client, "/v1/assets", limit=10)["next_cursor"]
    assert client.get("/v1/assets", headers=AUTH, params={"cursor": cur, "sort": "size"}).status_code == 422
    assert client.get("/v1/assets", headers=AUTH, params={"cursor": "garbage!!"}).status_code == 422


def test_month_counts_match_the_list_for_every_filter(api):
    conn, client = api
    _seed(conn, 120)
    conn.execute("UPDATE assets SET taken_at = '2019-05-02T10:00:00' WHERE id <= 10")
    conn.execute("UPDATE assets SET is_favorite = 1 WHERE id % 3 = 0")
    conn.execute("UPDATE assets SET is_hidden = 1 WHERE id = 5")
    for params in (
        {},
        {"kind": "screenshot"},
        {"favorite": "true"},
        {"hidden": "true"},
        {"from": "2024-01-01", "to": "2024-01-31"},
        {"availability": "present"},
    ):
        out = _get(client, "/v1/assets:months", **params)
        ids = _walk(client, **params)
        assert out["total"] == len(ids) == sum(m["count"] for m in out["months"]), params
        months = [m["month"] for m in out["months"]]
        assert months == sorted(months, reverse=True)  # newest first
        for m in out["months"]:  # each month's count = what the list returns for that month
            y, mo = map(int, m["month"].split("-"))
            end = f"{y + mo // 12}-{mo % 12 + 1:02d}-01"
            last = (dt.date.fromisoformat(end) - dt.timedelta(days=1)).isoformat()
            got = _walk(
                client,
                **{
                    **params,
                    "from": max(params.get("from", ""), f"{m['month']}-01"),
                    "to": min(params.get("to", "9999"), last),
                },
            )
            assert len(got) == m["count"], (params, m)
    out = _get(client, "/v1/assets:months")
    assert {m["month"] for m in out["months"]} == {"2019-05", "2024-01", "2023-06"}
    assert client.get("/v1/assets:months", headers=AUTH, params={"availability": "nope"}).status_code == 422
    assert client.get("/v1/assets:months").status_code == 401


def test_pagination_is_stable_while_photos_arrive(api):
    conn, client = api
    _seed(conn, 200)
    seen, cursor, added = [], None, 200
    while True:
        page = _get(client, "/v1/assets", limit=25, **({"cursor": cursor} if cursor else {}))
        seen += [a["id"] for a in page["items"]]
        _seed(conn, 5, start=added)  # new photos arrive between pages
        added += 5
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen))  # never twice
    assert set(range(1, 201)) <= set(seen)  # every photo that existed at the start, once


def test_availability_missing_and_unreachable(api):
    conn, client = api
    _seed(conn, 3)
    conn.execute("UPDATE file_locations SET state = 'missing' WHERE asset_id = 2")
    conn.execute("UPDATE file_locations SET state = 'unreachable' WHERE asset_id = 3")
    assert _walk(client, availability="missing") == [2]  # kept, with a badge (decision 2)
    assert _walk(client, availability="unreachable") == [3]
    assert _get(client, "/v1/assets/2")["availability"] == "missing"


# --------------------------------------------------------------------------- detail and edits
def test_detail_patch_hide_and_audit(api, tmp_path):
    conn, client = api
    _seed(conn, 2, root=str(tmp_path))
    assert client.get("/v1/assets/999", headers=AUTH).status_code == 404
    d = _get(client, "/v1/assets/1")
    assert d["title"] == {"value": None, "origin": None}
    assert _get(client, "/v1/assets/2")["taken_at"]["origin"] == "source"
    assert d["locations"][0]["path"] == str(tmp_path / "dir0/img000000.jpg")
    assert d["tags"] == [] and d["notes"] == [] and d["versions"] == []

    r = client.patch(
        "/v1/assets/1",
        headers=AUTH,
        json={"title": "Sunset", "taken_at": "2020-05-04T18:30:00+02:00", "is_favorite": True},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == {"value": "Sunset", "origin": "user"}
    assert d["taken_at"] == {"value": "2020-05-04T18:30:00+02:00", "origin": "user"}
    assert d["is_favorite"] is True
    assert client.patch("/v1/assets/1", headers=AUTH, json={"taken_at": "yesterday"}).status_code == 422
    assert client.patch("/v1/assets/1", headers=AUTH, json={"bogus": 1}).status_code == 422
    assert conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'sunset'").fetchall() == [(1,)]

    # a user date survives a later metadata run (it only fills source dates)
    job = {"asset_id": 1, "priority": 100, "stage": "metadata"}
    conn.execute("BEGIN")
    handlers._apply_metadata(conn, job, {"taken_at": "2001-01-01T00:00:00", "extractor": "t"})
    conn.execute("COMMIT")
    assert _get(client, "/v1/assets/1")["taken_at"]["value"] == "2020-05-04T18:30:00+02:00"
    # clearing the date asks for the file's own date again
    client.patch("/v1/assets/1", headers=AUTH, json={"taken_at": None})
    assert conn.execute("SELECT taken_at_origin FROM assets WHERE id = 1").fetchone()[0] is None
    assert conn.execute("SELECT status FROM jobs WHERE asset_id = 1 AND stage = 'metadata'").fetchone()[0] == "queued"

    assert client.post("/v1/assets/1/hide", headers=AUTH).json()["is_hidden"] is True
    assert _walk(client) == [2]
    assert _walk(client, hidden=True) == [1]
    client.post("/v1/assets/1/unhide", headers=AUTH)
    assert sorted(_walk(client)) == [1, 2]
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE entity = 'asset' ORDER BY id")]
    assert actions == ["update", "update", "hide", "unhide"]


def test_reveal_only_returns_known_present_files(api, tmp_path):
    conn, client = api
    _seed(conn, 2, root=str(tmp_path))
    f = tmp_path / "dir0/img000000.jpg"
    f.parent.mkdir()
    f.write_bytes(b"x")
    assert client.post("/v1/assets/1/reveal", headers=AUTH).json() == {"path": str(f)}
    assert client.post("/v1/assets/2/reveal", headers=AUTH).status_code == 409  # file not there
    assert client.post("/v1/assets/9/reveal", headers=AUTH).status_code == 404


# --------------------------------------------------------------------------- tags
def test_tags_lifecycle_and_rejection(api):
    conn, client = api
    _seed(conn, 3)
    r = client.post("/v1/tags", headers=AUTH, json={"name": "Café  Paris"})
    assert r.status_code == 201
    cafe = r.json()["id"]
    assert r.json()["slug"] == "cafe-paris"
    assert client.post("/v1/tags", headers=AUTH, json={"name": "cafe paris"}).status_code == 409
    client.post("/v1/assets/1/tags", headers=AUTH, json={"names": ["Café Paris", "Beach"]})
    beach = conn.execute("SELECT id FROM tags WHERE slug = 'beach'").fetchone()[0]
    assert [t["name"] for t in _get(client, "/v1/assets/1")["tags"]] == ["Beach", "Café Paris"]  # spaces collapsed
    assert _walk(client, tag=[cafe, beach]) == [1]
    assert {t["slug"]: t["count"] for t in _get(client, "/v1/tags", q="CAF")} == {"cafe-paris": 1}
    assert conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'beach'").fetchall() == [(1,)]

    # an AI tag the user removes is remembered as rejected (decision 3); re-adding makes it theirs
    conn.execute("INSERT INTO asset_tags (asset_id, tag_id, origin, confidence) VALUES (2, ?, 'ai', 0.8)", (beach,))
    assert client.delete(f"/v1/assets/2/tags/{beach}", headers=AUTH).status_code == 204
    assert conn.execute("SELECT rejected FROM asset_tags WHERE asset_id = 2").fetchone()[0] == 1
    assert _get(client, "/v1/assets/2")["tags"] == []
    client.post("/v1/assets/2/tags", headers=AUTH, json={"names": ["beach"]})
    assert conn.execute("SELECT origin, rejected FROM asset_tags WHERE asset_id = 2").fetchone() == ("user", 0)
    # a user's own tag is simply deleted
    client.delete(f"/v1/assets/1/tags/{beach}", headers=AUTH)
    assert (
        conn.execute("SELECT count(*) FROM asset_tags WHERE asset_id = 1 AND tag_id = ?", (beach,)).fetchone()[0] == 0
    )

    # rename, conflict on rename, merge, delete
    assert client.patch(f"/v1/tags/{beach}", headers=AUTH, json={"name": "Seaside"}).json()["slug"] == "seaside"
    assert client.patch(f"/v1/tags/{beach}", headers=AUTH, json={"name": "cafe paris"}).status_code == 409
    assert conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'seaside'").fetchall() == [(2,)]
    r = client.post(f"/v1/tags/{beach}/merge", headers=AUTH, json={"into": cafe})
    assert r.json() == {"photos_moved": 1}
    assert sorted(_walk(client, tag=[cafe])) == [1, 2]
    assert client.delete(f"/v1/tags/{cafe}", headers=AUTH).json() == {"photos_untagged": 2}
    assert conn.execute("SELECT count(*) FROM asset_tags").fetchone()[0] == 0
    assert client.delete("/v1/tags/999", headers=AUTH).status_code == 404


# --------------------------------------------------------------------------- collections
def test_collections_nesting_order_and_delete(api):
    conn, client = api
    _seed(conn, 5)
    trips = client.post("/v1/collections", headers=AUTH, json={"name": "Trips"}).json()["id"]
    italy = client.post("/v1/collections", headers=AUTH, json={"name": "Italy", "parent_id": trips}).json()["id"]
    rome = client.post("/v1/collections", headers=AUTH, json={"name": "Rome", "parent_id": italy}).json()["id"]
    assert client.patch(f"/v1/collections/{trips}", headers=AUTH, json={"parent_id": rome}).status_code == 409
    assert client.patch(f"/v1/collections/{trips}", headers=AUTH, json={"parent_id": trips}).status_code == 409
    assert client.post("/v1/collections", headers=AUTH, json={"name": "x", "parent_id": 99}).status_code == 404

    r = client.post(f"/v1/collections/{italy}/assets", headers=AUTH, json={"ids": [3, 1, 5]})
    assert r.json() == {"added": 3}
    assert client.post(f"/v1/collections/{italy}/assets", headers=AUTH, json={"ids": [1]}).json() == {"added": 0}
    assert client.post(f"/v1/collections/{italy}/assets", headers=AUTH, json={"ids": [42]}).status_code == 404
    assert _walk(client, collection=italy, sort="position", order="asc") == [3, 1, 5]
    # drag photo 5 between 3 and 1
    r = client.put(f"/v1/collections/{italy}/assets/5/position", headers=AUTH, json={"position": 1.5})
    assert r.status_code == 204
    assert _walk(client, collection=italy, sort="position", order="asc") == [3, 5, 1]
    assert client.get("/v1/assets", headers=AUTH, params={"sort": "position"}).status_code == 422
    counts = {c["name"]: c["count"] for c in _get(client, "/v1/collections")}
    assert counts == {"Trips": 0, "Italy": 3, "Rome": 0}
    assert [c["name"] for c in _get(client, "/v1/assets/1")["collections"]] == ["Italy"]

    client.request("DELETE", f"/v1/collections/{italy}/assets", headers=AUTH, json={"ids": [3]})
    assert _walk(client, collection=italy, sort="position", order="asc") == [5, 1]
    assert client.patch(f"/v1/collections/{rome}", headers=AUTH, json={"name": "Roma"}).json()["name"] == "Roma"
    assert client.delete(f"/v1/collections/{trips}", headers=AUTH).status_code == 204
    assert _get(client, "/v1/collections") == []  # nested ones go with it
    assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 5  # photos never deleted


# --------------------------------------------------------------------------- notes
def test_notes(api):
    conn, client = api
    _seed(conn, 1)
    r = client.post("/v1/assets/1/notes", headers=AUTH, json={"body": "Receipt for the blue sofa"})
    assert r.status_code == 201
    nid = r.json()["id"]
    assert conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'sofa'").fetchall() == [(1,)]
    assert (
        client.patch(f"/v1/assets/1/notes/{nid}", headers=AUTH, json={"body": "Red chair"}).json()[0]["body"]
        == "Red chair"
    )
    assert conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'sofa'").fetchall() == []
    assert client.patch(f"/v1/assets/2/notes/{nid}", headers=AUTH, json={"body": "x"}).status_code == 404
    assert client.post("/v1/assets/1/notes", headers=AUTH, json={"body": ""}).status_code == 422
    assert client.post("/v1/assets/1/notes", headers=AUTH, json={"body": "x" * 20001}).status_code == 422
    assert client.delete(f"/v1/assets/1/notes/{nid}", headers=AUTH).status_code == 204
    assert _get(client, "/v1/assets/1/notes") == []


# --------------------------------------------------------------------------- bulk
def test_bulk_is_all_or_nothing_and_fast(api):
    conn, client = api
    _seed(conn, 5000)
    coll = client.post("/v1/collections", headers=AUTH, json={"name": "All"}).json()["id"]
    ids = list(range(1, 5001))
    t0 = time.perf_counter()
    r = client.post(
        "/v1/assets:bulk",
        headers=AUTH,
        json={"ids": ids, "add_tags": ["Archive", "2024"], "add_to_collection": coll, "set_favorite": True},
    )
    took = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    assert r.json() == {"photos": 5000, "tags_added": 2, "added_to_collection": 5000, "set_favorite": 5000}
    print(f"\nbulk 5000 photos x (2 tags, collection, favourite): {took * 1000:.0f} ms")
    assert took < 5  # plan target is 1 s in the container; the hard limit here catches regressions only
    assert conn.execute("SELECT count(*) FROM asset_tags").fetchone()[0] == 10000

    # one unknown id: nothing changes
    before = conn.execute("SELECT count(*) FROM asset_tags").fetchone()[0]
    r = client.post("/v1/assets:bulk", headers=AUTH, json={"ids": [1, 2, 99999], "add_tags": ["New"], "hide": True})
    assert r.status_code == 404
    assert conn.execute("SELECT count(*) FROM asset_tags").fetchone()[0] == before
    assert conn.execute("SELECT count(*) FROM tags WHERE slug = 'new'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM assets WHERE is_hidden = 1").fetchone()[0] == 0
    assert client.post("/v1/assets:bulk", headers=AUTH, json={"ids": list(range(1, 5002))}).status_code == 422
    assert client.post("/v1/assets:bulk", headers=AUTH, json={"ids": [1]}).status_code == 422  # no change

    r = client.post("/v1/assets:bulk", headers=AUTH, json={"ids": [1, 2], "remove_tags": ["archive"], "hide": True})
    assert r.json()["hidden_changed"] == 2
    assert conn.execute("SELECT count(*) FROM asset_tags WHERE asset_id IN (1, 2)").fetchone()[0] == 2


# --------------------------------------------------------------------------- performance
@pytest.mark.skipif(os.environ.get("IV_SKIP_SLOW") == "1", reason="slow")
def test_100k_list_and_detail_timings(api):
    conn, client = api
    n = int(os.environ.get("IV_LIB_N", "100000"))
    _seed(conn, n)
    conn.execute("ANALYZE")

    def timed(fn, reps=5):
        best = float("inf")
        for _ in range(reps):
            t0 = time.perf_counter()
            out = fn()
            best = min(best, time.perf_counter() - t0)
        return best * 1000, out

    first_ms, page = timed(lambda: _get(client, "/v1/assets", limit=60))
    cursor = page["next_cursor"]
    for _ in range(499):  # jump to page 500 by keyset (same cost per page)
        cursor = _get(client, "/v1/assets", limit=60, cursor=cursor)["next_cursor"]
    deep_ms, _ = timed(lambda: _get(client, "/v1/assets", limit=60, cursor=cursor))
    filt_ms, _ = timed(lambda: _get(client, "/v1/assets", limit=60, kind="screenshot"))
    detail_ms, _ = timed(lambda: _get(client, f"/v1/assets/{n // 2}"))
    print(
        f"\n{n} photos: first page {first_ms:.1f} ms, page 500 {deep_ms:.1f} ms, "
        f"screenshots filter {filt_ms:.1f} ms, detail {detail_ms:.1f} ms (HTTP round trip incl.)"
    )
    assert max(first_ms, deep_ms, filt_ms) < 250 and detail_ms < 250  # regression guard; targets in the plan


def test_cursor_is_opaque_but_tamper_safe(api):
    conn, client = api
    _seed(conn, 5)
    bad = base64.urlsafe_b64encode(json.dumps(["date", "desc", "x'); DROP TABLE assets;--", "1"]).encode()).decode()
    assert client.get("/v1/assets", headers=AUTH, params={"cursor": bad}).status_code == 422
    assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 5
