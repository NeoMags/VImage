"""S1: Smart Collections. A saved rule (the gallery filters), evaluated live: list, months,
facets and counts agree with the same filters set by hand; new photos appear on their own."""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pytest
from test_library import AUTH, _get, _seed, api  # noqa: F401 - api is a fixture

from ivsidecar import library as lib


def _new(client, name, filters, **extra):
    return client.post(
        "/v1/collections", headers=AUTH, json={"name": name, "rule": {"v": 1, "filters": filters}, **extra}
    )


def _rule(c) -> dict:
    """The rule without the empty filters the API lists as null."""
    return {"v": c["rule"]["v"], "filters": {k: v for k, v in c["rule"]["filters"].items() if v is not None}}


def _ids(client, **params):
    return [a["id"] for a in _get(client, "/v1/assets", limit=500, **params)["items"]]


def test_smart_collection_matches_its_filters_and_updates_itself(api):  # noqa: F811
    conn, client = api
    _seed(conn, 50)
    r = _new(client, "Screenshots", {"kind": ["screenshot"]})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    [c] = [c for c in _get(client, "/v1/collections") if c["id"] == cid]
    assert c["kind"] == "smart" and _rule(c) == {"v": 1, "filters": {"kind": ["screenshot"]}} and c["count"] == 5

    assert _ids(client, collection=cid) == _ids(client, kind="screenshot")
    assert _get(client, "/v1/assets:months", collection=cid)["total"] == 5
    facets = _get(client, "/v1/assets:facets", collection=cid)
    assert {k["value"]: k["count"] for k in facets["kind"]} == {"screenshot": 5}
    # a smart collection has no hand-made order: position sort falls back to date
    assert _ids(client, collection=cid, sort="position") == _ids(client, collection=cid)
    # more filters on top narrow it (AND)
    fav = _ids(client, collection=cid)[0]
    with lib.tx(conn):
        lib.patch_asset(conn, fav, {"is_favorite": True})
    assert _ids(client, collection=cid, favorite="true") == [fav]

    _seed(conn, 10, start=50)  # 50 and ... : i % 10 == 0 is a screenshot
    assert len(_ids(client, collection=cid)) == 6
    assert [c["count"] for c in _get(client, "/v1/collections") if c["id"] == cid] == [6]


def test_rules_with_dates_tags_collections_and_relative_dates(api):  # noqa: F811
    conn, client = api
    _seed(conn, 30)
    with lib.tx(conn):
        tag = lib.get_or_create_tags(conn, ["beach"])[0]
        lib.add_tags(conn, [1, 2, 3], [tag])
    trip = client.post("/v1/collections", headers=AUTH, json={"name": "Trip"}).json()["id"]
    client.post(f"/v1/collections/{trip}/assets", headers=AUTH, json={"ids": [2, 3, 4]})

    beach_trip = _new(client, "Beach on the trip", {"tag": [tag], "collection": trip}).json()["id"]
    assert sorted(_ids(client, collection=beach_trip)) == [2, 3]

    jan = _new(client, "Early Jan", {"from": "2024-01-01", "to": "2024-01-05"}).json()["id"]
    assert sorted(_ids(client, collection=jan)) == sorted(_ids(client, **{"from": "2024-01-01", "to": "2024-01-05"}))
    rule = _rule(next(c for c in _get(client, "/v1/collections") if c["id"] == jan))
    assert rule == {"v": 1, "filters": {"from": "2024-01-01", "to": "2024-01-05"}}

    recent = _new(client, "Last 30 days", {"recent_days": 30}).json()["id"]
    assert _ids(client, collection=recent) == []
    today = date.today().isoformat() + "T09:00:00"
    conn.execute("UPDATE assets SET taken_at = ? WHERE id = 7", (today,))
    assert _ids(client, collection=recent) == [7]
    assert _ids(client, recent_days=30) == [7]
    old = (date.today() - timedelta(days=40)).isoformat() + "T09:00:00"
    conn.execute("UPDATE assets SET taken_at = ? WHERE id = 7", (old,))
    assert _ids(client, collection=recent) == []


def test_rule_validation_and_editing(api):  # noqa: F811
    conn, client = api
    _seed(conn, 20)
    assert _new(client, "Empty", {}).status_code == 422
    assert _new(client, "Unknown", {"colour": "red"}).status_code == 422
    assert (
        client.post(
            "/v1/collections", headers=AUTH, json={"name": "x", "rule": {"v": 2, "filters": {"favorite": True}}}
        ).status_code
        == 422
    )
    assert _new(client, "Missing tag", {"tag": [999]}).status_code == 422
    smart = _new(client, "Images", {"kind": ["image"]}).json()["id"]
    r = _new(client, "Loop", {"collection": smart})
    assert r.status_code == 422 and "another smart collection" in r.text

    # photos cannot be put in by hand; the rule can be changed and the count follows
    assert client.post(f"/v1/collections/{smart}/assets", headers=AUTH, json={"ids": [1]}).status_code == 409
    r = client.patch(
        f"/v1/collections/{smart}", headers=AUTH, json={"rule": {"v": 1, "filters": {"kind": ["screenshot"]}}}
    )
    assert r.status_code == 200 and r.json()["count"] == 2 and _rule(r.json())["filters"] == {"kind": ["screenshot"]}
    assert client.patch(f"/v1/collections/{smart}", headers=AUTH, json={"name": "Shots"}).json()["rule"] is not None
    manual = client.post("/v1/collections", headers=AUTH, json={"name": "Mine"}).json()["id"]
    r = client.patch(f"/v1/collections/{manual}", headers=AUTH, json={"rule": {"v": 1, "filters": {"favorite": True}}})
    assert r.status_code == 409
    assert next(c for c in _get(client, "/v1/collections") if c["id"] == manual)["rule"] is None

    # deleting a smart collection never touches photos, nor a manual collection it names
    in_mine = _new(client, "Fav in Mine", {"collection": manual, "favorite": True}).json()["id"]
    assert client.delete(f"/v1/collections/{in_mine}", headers=AUTH).status_code == 204
    assert conn.execute("SELECT count(*) FROM assets").fetchone() == (20,)


@pytest.mark.skipif(os.environ.get("IV_SKIP_SLOW") == "1", reason="slow")
def test_smart_collection_is_as_fast_as_the_same_filters_100k(api):  # noqa: F811
    conn, client = api
    n = int(os.environ.get("IV_LIB_N", "100000"))
    _seed(conn, n)
    conn.execute("ANALYZE")
    cid = _new(client, "Big screenshots", {"kind": ["screenshot"], "min_bytes": 1500}).json()["id"]

    def best(fn, reps=3):
        out = float("inf")
        for _ in range(reps):
            t = time.perf_counter()
            fn()
            out = min(out, time.perf_counter() - t)
        return out * 1000

    plain = {"kind": "screenshot", "min_bytes": 1500}
    for path in ("/v1/assets", "/v1/assets:months", "/v1/assets:facets"):
        a = best(lambda: _get(client, path, **plain))  # noqa: B023
        b = best(lambda: _get(client, path, collection=cid))  # noqa: B023
        print(f"\n{path}: filters {a:.0f} ms, smart collection {b:.0f} ms")
        assert b < a * 1.5 + 20
    lst = best(lambda: _get(client, "/v1/collections"))
    print(f"collection list with a smart count: {lst:.0f} ms")
    assert lst < (600 if os.name == "nt" else 300)
