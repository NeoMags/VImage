"""K2: the OCR stage through the real job queue (engine faked, except one real run), the text
indexes, your edits, the on/off switch and the /v1 text routes."""

from __future__ import annotations

import os
import time

import jobhelpers
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from test_jobs import AUTH, TOKEN, _drain, _library, _register_all

from ivsidecar import handlers, settings
from ivsidecar import library as lib_mod
from ivsidecar import text as T
from ivsidecar.app import create_app
from ivsidecar.jobqueue import JobQueue
from ivsidecar.probes import find_exiftool

if not find_exiftool() and os.environ.get("IV_REQUIRE_EXIFTOOL") != "1":
    pytest.skip("exiftool not installed", allow_module_level=True)


def _photos(src, names=("shot1.png", "holiday.jpg", "blank.png")):
    for i, n in enumerate(names):
        Image.new("RGB", (64, 48), (40 * i, 90, 160)).save(src / n)


def _run(lib, conn, fn=jobhelpers.fake_ocr, timeout_s=120):
    q = JobQueue(lib, stage_fn=fn)
    q.start()
    try:
        _drain(conn, timeout_s)
    finally:
        q.stop()


def _on(conn):
    settings.save(conn, "ocr", settings.OcrOptions(enabled=True))


def _asset(conn, name):
    return conn.execute("SELECT asset_id FROM file_locations WHERE rel_path = ?", (name,)).fetchone()[0]


def _fts(conn, query):
    return {r[0] for r in conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?", (query,))}


def _trigram(conn, needle):
    return {r[0] for r in conn.execute("SELECT rowid FROM ocr_trigram WHERE ocr_text LIKE ?", (f"%{needle}%",))}


def test_new_photos_are_read_and_indexed_screenshots_first(tmp_path):
    lib, src, conn = _library(tmp_path)
    _on(conn)
    _photos(src)
    _register_all(conn, src)
    _run(lib, conn)
    shot, hol, blank = (_asset(conn, n) for n in ("shot1.png", "holiday.jpg", "blank.png"))
    t = T.get_text(conn, shot)
    assert t["state"] == "done" and t["origin"] == "ai" and t["text"] == "Invoice shot1\nCall +1 555 0100 1234"
    assert [ln["text"] for ln in t["lines"]] == ["Invoice shot1", "Call +1 555 0100 1234"]
    assert t["lines"][0]["x"] == pytest.approx(0.1) and t["engine"] == "fake 1"
    # word index (column filter) and exact substrings, e.g. part of a phone number
    assert _fts(conn, "ocr_text:invoice") == {shot, hol}
    assert _fts(conn, "ocr_text:shot1") == {shot}
    assert _trigram(conn, "0100 12") == {shot, hol}
    # a photo without text: done, nothing indexed
    assert T.get_text(conn, blank)["text"] == "" and T.get_text(conn, blank)["state"] == "done"
    assert blank not in _trigram(conn, "Call")
    # PNG first: priority after thumbnail + 20, other images + 30
    prio = dict(conn.execute("SELECT asset_id, priority FROM jobs WHERE stage = 'ocr'").fetchall())
    thumb = conn.execute("SELECT priority FROM jobs WHERE stage = 'thumbnail' AND asset_id = ?", (shot,)).fetchone()[0]
    assert prio[shot] == thumb + handlers.OCR_FIRST_OFFSET and prio[hol] == thumb + handlers.OCR_PRIORITY_OFFSET
    assert T.status(conn) == {
        "enabled": True,
        "photos_done": 3,
        "photos_waiting": 0,
        "photos_failed": 0,
        "photos_with_text": 2,
        "photos_edited": 0,
    }


def test_your_edit_survives_a_new_ocr_run_and_can_be_reverted(tmp_path):
    lib, src, conn = _library(tmp_path)
    _on(conn)
    _photos(src, ("shot1.png",))
    _register_all(conn, src)
    _run(lib, conn)
    aid = _asset(conn, "shot1.png")
    with lib_mod.tx(conn):
        T.set_text(conn, aid, "Receipt from Café Zürich\r\ntotal 42")
    t = T.get_text(conn, aid)
    assert t["origin"] == "user" and t["text"] == "Receipt from Café Zürich\ntotal 42" and len(t["lines"]) == 2
    assert _fts(conn, "ocr_text:zurich") == {aid}  # diacritics folded
    assert _fts(conn, "ocr_text:invoice") == set()  # the read text is no longer searched
    # OCR runs again (Read again): your text stays current, the new boxes are kept
    with lib_mod.tx(conn):
        T.read_again(conn, aid)
    assert conn.execute("SELECT priority FROM jobs WHERE stage = 'ocr' AND asset_id = ?", (aid,)).fetchone()[0] == 10
    _run(lib, conn)
    t = T.get_text(conn, aid)
    assert t["origin"] == "user" and t["text"].startswith("Receipt") and len(t["lines"]) == 2
    assert conn.execute("SELECT last_error FROM jobs WHERE stage = 'ocr'").fetchone()[0] == "kept your edited text"
    assert conn.execute("SELECT count(*) FROM extracted_text WHERE asset_id = ?", (aid,)).fetchone()[0] == 2
    # editing again updates your row, no new one
    with lib_mod.tx(conn):
        T.set_text(conn, aid, "Receipt v2")
    assert conn.execute("SELECT count(*) FROM extracted_text WHERE asset_id = ?", (aid,)).fetchone()[0] == 2
    # revert: the read text is shown and searched again
    with lib_mod.tx(conn):
        T.revert_text(conn, aid)
    t = T.get_text(conn, aid)
    assert t["origin"] == "ai" and t["text"].startswith("Invoice")
    assert _fts(conn, "ocr_text:invoice") == {aid} and _fts(conn, "ocr_text:receipt") == set()
    with pytest.raises(lib_mod.NotFound):
        T.revert_text(conn, aid)
    # other columns of the word index are untouched by text changes (and vice versa)
    handlers.refresh_fts(conn, [aid])
    assert _fts(conn, "ocr_text:invoice") == {aid} and _fts(conn, "filename:shot1") == {aid}
    edits = conn.execute("SELECT action FROM audit_log WHERE action LIKE 'text.%' ORDER BY id").fetchall()
    assert [a for (a,) in edits] == ["text.edit", "text.edit", "text.revert"]


def test_ocr_failure_shows_on_the_job_not_on_the_photo(tmp_path, monkeypatch):
    monkeypatch.setattr("ivsidecar.jobqueue.BACKOFF_S", (0, 0, 0, 0))
    lib, src, conn = _library(tmp_path)
    _on(conn)
    _photos(src, ("ocrfail.png",))
    _register_all(conn, src)
    _run(lib, conn)
    aid = _asset(conn, "ocrfail.png")
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (aid,)).fetchone()[0] == "ready"
    t = T.get_text(conn, aid)
    assert t["state"] == "failed" and "hiccup" in t["error"]
    assert T.status(conn)["photos_failed"] == 1


def test_switch_off_cancels_waiting_and_on_queues_unread_photos(tmp_path):
    lib, src, conn = _library(tmp_path)  # OCR off
    _photos(src)
    _register_all(conn, src)
    _run(lib, conn)
    assert conn.execute("SELECT count(*) FROM jobs WHERE stage = 'ocr'").fetchone()[0] == 0
    _on(conn)
    assert handlers.enqueue_ocr_backfill(conn) == 3
    assert handlers.enqueue_ocr_backfill(conn) == 3  # idempotent
    assert conn.execute("SELECT count(*) FROM jobs WHERE stage = 'ocr'").fetchone()[0] == 3
    assert handlers.cancel_queued_ocr(conn) == 3
    handlers.enqueue_ocr_backfill(conn)
    _run(lib, conn)
    assert handlers.enqueue_ocr_backfill(conn) == 0  # all read
    assert handlers.enqueue_ocr_backfill(conn, again=True) == 3
    assert conn.execute("SELECT count(*) FROM jobs WHERE stage = 'ocr' AND status = 'queued'").fetchone()[0] == 3


def test_api_routes(tmp_path):
    lib, src, conn = _library(tmp_path)
    _photos(src, ("shot1.png",))
    _register_all(conn, src)
    _run(lib, conn)
    aid = _asset(conn, "shot1.png")
    q = JobQueue(lib, stage_fn=jobhelpers.fake_ocr)
    c = TestClient(create_app(conn, TOKEN, time.time(), queue=q))
    assert c.get("/v1/ocr").status_code == 401
    assert c.get(f"/v1/assets/{aid}/text", headers=AUTH).json()["state"] == "not_read"
    assert c.post("/v1/ocr:rerun", headers=AUTH).status_code == 409  # off
    assert c.put("/v1/settings/ocr", headers=AUTH, json={"enabled": True}).json() == {"enabled": True}
    assert c.get("/v1/ocr", headers=AUTH).json()["photos_waiting"] == 1
    q.start()
    try:
        _drain(conn)
    finally:
        q.stop()
    r = c.get(f"/v1/assets/{aid}/text", headers=AUTH).json()
    assert r["text"].startswith("Invoice shot1") and r["origin"] == "ai"
    r = c.put(f"/v1/assets/{aid}/text", headers=AUTH, json={"text": "Mine"}).json()
    assert r["text"] == "Mine" and r["origin"] == "user"
    assert c.get("/v1/ocr", headers=AUTH).json()["photos_edited"] == 1
    assert c.put(f"/v1/assets/{aid}/text", headers=AUTH, json={"text": "x", "extra": 1}).status_code == 422
    assert c.delete(f"/v1/assets/{aid}/text/edit", headers=AUTH).json()["origin"] == "ai"
    assert c.delete(f"/v1/assets/{aid}/text/edit", headers=AUTH).status_code == 404
    assert c.post(f"/v1/assets/{aid}/text:rerun", headers=AUTH).status_code == 202
    assert c.get("/v1/assets/999999/text", headers=AUTH).status_code == 404
    assert c.post("/v1/ocr:rerun", headers=AUTH).json()["photos_waiting"] == 1
    assert c.put("/v1/settings/ocr", headers=AUTH, json={"enabled": False}).status_code == 200
    assert c.get("/v1/ocr", headers=AUTH).json()["photos_waiting"] == 0


def test_many_photos_are_read_without_crashes(tmp_path):
    """Done-when of the D2 plan: a 1,000-image fixture goes through the OCR stage (engine faked; the
    queue, decoding and storage are real). IV_OCR_N lowers it for quick local runs."""
    n = int(os.environ.get("IV_OCR_N", "1000"))
    lib, src, conn = _library(tmp_path, threads=4)
    _on(conn)
    for i in range(n):
        Image.new("RGB", (32, 24), (i % 256, i // 256, 90)).save(src / f"img{i:04d}.png")  # all different
    _register_all(conn, src)
    _run(lib, conn, timeout_s=900)
    done = conn.execute("SELECT status, count(*) FROM jobs WHERE stage = 'ocr' GROUP BY status").fetchall()
    assert done == [("succeeded", n)]
    assert conn.execute("SELECT count(*) FROM ocr_trigram").fetchone()[0] == n
    assert conn.execute("SELECT count(*) FROM text_regions").fetchone()[0] == 2 * n


def _render(lines, size=(1000, 300)):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default(size=28)
    for i, ln in enumerate(lines):
        d.text((40, 40 + i * 80), ln, fill="black", font=f)
    return img


def test_real_ocr_through_the_worker(tmp_path):
    """The real RapidOCR models, through the real worker stage and decoder (rotated JPEG too)."""
    lib, src, conn = _library(tmp_path)
    _on(conn)
    _render(["Error 0x80070005 Access denied", "see https://example.com/fix"]).save(src / "Screenshot 1.png")
    _render(["Rotated scan text"]).rotate(90, expand=True).save(src / "scan.jpg", quality=95)
    _register_all(conn, src)
    _run(lib, conn, fn=None, timeout_s=300)
    shot = T.get_text(conn, _asset(conn, "Screenshot 1.png"))
    assert shot["state"] == "done", shot
    assert "0x80070005" in shot["text"] and "example.com" in shot["text"]
    assert len(shot["lines"]) == 2 and all(0 <= ln["y"] < 1 for ln in shot["lines"])
    assert _trigram(conn, "80070005") == {_asset(conn, "Screenshot 1.png")}
    # without an orientation tag the rotated scan is read sideways or not at all: it must not crash
    assert T.get_text(conn, _asset(conn, "scan.jpg"))["state"] == "done"
