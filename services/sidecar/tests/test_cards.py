"""K5: Knowledge Cards made from the text, editable, never regenerated once yours; Markdown export."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from test_jobs import AUTH, TOKEN
from test_library import _get, _seed, api  # noqa: F401 - fixture

from ivsidecar import cards as C
from ivsidecar import handlers
from ivsidecar import library as lib
from ivsidecar import text as T
from ivsidecar.app import create_app


def _ocr(conn, aid, lines):
    with lib.tx(conn):
        handlers.store_ocr(
            conn,
            aid,
            {
                "engine": "fake",
                "engine_version": "1",
                "confidence": 0.9,
                "lines": [
                    {"text": t, "confidence": 0.9, "x": 0.1, "y": 0.05 * i, "w": 0.5, "h": 0.04}
                    for i, t in enumerate(lines)
                ],
            },
        )


def test_card_is_made_from_text_and_follows_it_until_you_edit(api):  # noqa: F811
    conn, _ = api
    _seed(conn, 3)
    _ocr(conn, 1, ["12:45", "•••", "Build failed", "Error 0x80070005: Access is denied", "see https://x.com/fix"])
    card = C.get_card(conn, 1)["card"]
    assert card["title"] == "Build failed" and not card["edited"]
    assert card["summary"] == "Picture of an error with 5 lines of text; contains 2 error messages, 1 link."
    assert conn.execute("SELECT count(*) FROM knowledge_card_entities").fetchone()[0] == 3
    assert {r[0] for r in conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'card:failed'")} == {1}
    # no text: no card; text gone: card removed
    _ocr(conn, 2, [])
    assert C.get_card(conn, 2) == {"card": None}
    # editing the text updates an AI card
    with lib.tx(conn):
        T.set_text(conn, 1, "Invoice March\nTotal $42.50")
    assert C.get_card(conn, 1)["card"]["title"] == "Invoice March"
    # your edit: kept through new OCR runs and text edits
    with lib.tx(conn):
        C.edit_card(conn, 1, "My invoice", None)
    _ocr(conn, 1, ["Something else entirely"])
    with lib.tx(conn):
        T.revert_text(conn, 1)
    card = C.get_card(conn, 1)["card"]
    assert card["title"] == "My invoice" and card["edited"] and card["summary"].startswith("Picture")
    assert {r[0] for r in conn.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'card:my'")} == {1}
    with pytest.raises(lib.Invalid):
        C.edit_card(conn, 1, "  ", None)
    with pytest.raises(lib.NotFound):
        C.edit_card(conn, 3, "x", None)  # no card
    # back to AI: made from the current text again
    with lib.tx(conn):
        C.reset_card(conn, 1)
    assert C.get_card(conn, 1)["card"] == {
        **C.get_card(conn, 1)["card"],
        "title": "Something else entirely",
        "edited": False,
    }
    with pytest.raises(lib.NotFound):
        C.reset_card(conn, 1)


def test_markdown_export(api):  # noqa: F811
    conn, _ = api
    _seed(conn, 1)
    _ocr(conn, 1, ["Wi-Fi password *home*", "call +44 20 7946 0958", "code ```x```"])
    md = C.markdown(conn, 1)
    assert md["file_name"] == "Wi-Fi password home.md"
    text = md["markdown"]
    assert text.startswith("# Wi-Fi password \\*home\\*\n")
    assert "- Card: AI-generated" in text and "- Phone: `+44 20 7946 0958`" in text
    assert "```text\nWi-Fi password *home*\n" in text and "code ʼʼʼxʼʼʼ" in text


def test_api(api):  # noqa: F811
    conn, _ = api
    _seed(conn, 2)
    _ocr(conn, 1, ["Receipt from the shop", "Total ₹1,250.00"])
    c = TestClient(create_app(conn, TOKEN, time.time()))
    r = _get(c, "/v1/assets/1/card")
    assert r["card"]["title"] == "Receipt from the shop"
    assert _get(c, "/v1/assets/2/card") == {"card": None}
    r = c.patch("/v1/assets/1/card", headers=AUTH, json={"summary": "Lunch"}).json()
    assert r["card"]["summary"] == "Lunch" and r["card"]["edited"]
    assert c.patch("/v1/assets/1/card", headers=AUTH, json={"title": ""}).status_code == 422
    assert c.patch("/v1/assets/2/card", headers=AUTH, json={"title": "x"}).status_code == 404
    assert c.delete("/v1/assets/1/card/edit", headers=AUTH).json()["card"]["edited"] is False
    md = _get(c, "/v1/assets/1/card:markdown")
    assert md["file_name"] == "Receipt from the shop.md" and "INR 1250.00" not in md["markdown"]
    assert "- Amount: `₹1,250.00`" in md["markdown"]
    assert c.get("/v1/assets/2/card:markdown", headers=AUTH).status_code == 404
