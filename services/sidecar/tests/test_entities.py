"""K3: entities found in text (linked to their boxes) and the screenshot / scan / photo type."""

from __future__ import annotations

import os

import jobhelpers
import pytest
from PIL import Image
from test_jobs import _drain, _library, _register_all

from ivsidecar import entities as E
from ivsidecar import library as lib_mod
from ivsidecar import settings
from ivsidecar import text as T
from ivsidecar.jobqueue import JobQueue
from ivsidecar.probes import find_exiftool


def kinds(line):
    return [(k, n) for k, _, n in E.extract(line)]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Visit https://Example.com/Path?a=1.", [("url", "https://example.com/Path?a=1")]),
        (
            "docs at www.python.org/3/ and github.com/neo",
            [("url", "https://www.python.org/3/"), ("url", "https://github.com/neo")],
        ),
        ("mail Neo.Smith+x@Mail.Example.co.uk now", [("email", "neo.smith+x@mail.example.co.uk")]),
        ("Call +91 98765 43210 today", [("phone", "+919876543210")]),
        ("Tel (020) 7946 0958", [("phone", "02079460958")]),
        ("Due 2026-11-26", [("date", "2026-11-26")]),
        ("Paid on 26/11/2026", [("date", "2026-11-26")]),
        ("Paid on 11/26/2026", [("date", "2026-11-26")]),
        ("Paid on 05/04/2026", [("date", "2026-04-05")]),  # day first when both fit
        ("Meeting Nov 3, 2026 and 4th December 2026", [("date", "2026-11-03"), ("date", "2026-12-04")]),
        ("Total: ₹1,250.00", [("amount", "INR 1250.00")]),
        ("Total $42.50 incl. tax", [("amount", "USD 42.50")]),
        ("Rs. 499 only", [("amount", "INR 499")]),
        ("Price 19.99 EUR", [("amount", "EUR 19.99")]),
        ("$ pip install rapidocr", [("command", "pip install rapidocr")]),
        ("PS C:\\Users\\neo> winget install exiftool", [("command", "winget install exiftool")]),
        ("git commit -m 'fix'", [("command", "git commit -m 'fix'")]),
        ("Error 0x80070005: Access is denied", [("error_message", "error 0x80070005: access is denied")]),
        ("404 Not Found", [("error_message", "404 not found")]),
        ("Version 1.2.3 released", []),
        ("Hello world, nothing here", []),
        ("2026-11-26", [("date", "2026-11-26")]),  # a date is not a phone number
    ],
)
def test_extract(line, expected):
    assert kinds(line) == expected


def test_detect_type_rules(tmp_path):
    lib, src, conn = _library(tmp_path)

    def asset(name, w, h, mime="image/png", make=None, lines=0, cover=0.0):
        aid = conn.execute(
            "INSERT INTO assets (uid, sha256, kind, status, mime_type, byte_size, width, height) "
            "VALUES (?, randomblob(32), 'image', 'ready', ?, 1, ?, ?) RETURNING id",
            (name, mime, w, h),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO file_locations (source_id, file_key, rel_path, size_bytes, mtime, asset_id) "
            "VALUES (1, ?, ?, 1, '0', ?)",
            (name, name, aid),
        )
        if make:
            conn.execute(
                "INSERT INTO asset_metadata (asset_id, camera_make, extractor) VALUES (?, ?, 'test')", (aid, make)
            )
        if lines:
            tid = conn.execute(
                "INSERT INTO extracted_text (asset_id, engine, engine_version, full_text) VALUES (?, 'x', '1', 't') "
                "RETURNING id",
                (aid,),
            ).fetchone()[0]
            for i in range(lines):
                conn.execute(
                    "INSERT INTO text_regions (extracted_text_id, line_no, text, x, y, w, h) VALUES (?, ?, 't', 0, 0, ?, 0.1)",
                    (tid, i, cover / lines / 0.1),
                )
        return aid

    cases = {
        asset("Screenshot 2026-01-01.jpg", 800, 600, "image/jpeg"): ("screenshot", 0.95),
        asset("docs/scan_0001.jpg", 2480, 3508, "image/jpeg"): ("scan", 0.9),
        asset("IMG_1.jpg", 4032, 3024, "image/jpeg", make="Apple"): ("photo", 0.9),
        asset("x.png", 1170, 2532): ("screenshot", 0.8),
        asset("y.png", 700, 500, lines=1, cover=0.01): ("screenshot", 0.7),
        asset("page.jpg", 1000, 1414, "image/jpeg", lines=20, cover=0.3): ("scan", 0.6),
        asset("wide.jpg", 1000, 600, "image/jpeg", lines=5, cover=0.2): ("screenshot", 0.6),
        asset("dog.jpg", 1000, 600, "image/jpeg"): ("photo", 0.5),
        asset("art.gif", 300, 300, "image/gif"): None,
    }
    for aid, want in cases.items():
        assert E.detect_type(conn, aid) == want, aid
    # your choice wins and survives a new guess; clearing it hands it back
    dog = list(cases)[-2]
    E.classify(conn, dog)
    assert E.get_type(conn, dog) == {"value": "photo", "origin": "ai", "confidence": 0.5}
    with lib_mod.tx(conn):
        E.set_type(conn, dog, "scan")
    assert E.classify(conn, dog) is None and E.get_type(conn, dog)["value"] == "scan"
    with pytest.raises(lib_mod.Invalid):
        E.set_type(conn, dog, "drawing")
    with lib_mod.tx(conn):
        E.set_type(conn, dog, None)
    assert E.get_type(conn, dog)["origin"] == "ai"
    # the type filter and its facet
    for aid in cases:
        E.classify(conn, aid)
    page = lib_mod.list_assets(conn, type=["scan"])
    assert {i["id"] for i in page["items"]} == {list(cases)[1], list(cases)[5]}
    f = lib_mod.asset_facets(conn, type=["scan"])
    assert {x["value"]: x["count"] for x in f["type"]} == {"screenshot": 4, "photo": 2, "scan": 2}
    assert f["total"] == 2


@pytest.mark.skipif(not find_exiftool() and os.environ.get("IV_REQUIRE_EXIFTOOL") != "1", reason="no exiftool")
def test_entities_point_at_their_boxes_and_follow_your_edits(tmp_path):
    lib, src, conn = _library(tmp_path)
    settings.save(conn, "ocr", settings.OcrOptions(enabled=True))
    Image.new("RGB", (64, 48), "white").save(src / "receipt.png")
    _register_all(conn, src)
    q = JobQueue(lib, stage_fn=jobhelpers.fake_ocr)
    q.start()
    try:
        _drain(conn)
    finally:
        q.stop()
    aid = conn.execute("SELECT asset_id FROM file_locations").fetchone()[0]
    t = T.get_text(conn, aid)
    assert [(e["type"], e["normalized"], e["line_no"]) for e in t["entities"]] == [("phone", "+155501001234", 1)]
    assert t["type"] == {"value": "screenshot", "origin": "ai", "confidence": 0.7}
    with lib_mod.tx(conn):
        T.set_text(conn, aid, "Call +1 555 0100 1234\nmail a@b.com")
    ents = {(e["type"], e["line_no"]) for e in T.get_text(conn, aid)["entities"]}
    assert ents == {("phone", 1), ("email", None)}  # the unchanged line still points at its box
    with lib_mod.tx(conn):
        T.revert_text(conn, aid)
    assert [e["type"] for e in T.get_text(conn, aid)["entities"]] == ["phone"]
