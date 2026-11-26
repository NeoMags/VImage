"""K4: the search box finds photos by words and by exact pieces of their text, through the same
filters as the gallery (so counts agree), fast enough at 100k photos."""

from __future__ import annotations

import os
import time

import pytest
from test_jobs import AUTH
from test_library import _get, _seed, api  # noqa: F401 - fixture

from ivsidecar import handlers
from ivsidecar import library as lib
from ivsidecar import text as T


def _text(conn, aid, body):
    with lib.tx(conn):
        T.set_text(conn, aid, body)


def test_words_prefixes_exact_pieces_and_phone_digits(api):  # noqa: F811
    conn, client = api
    _seed(conn, 30)
    handlers.refresh_fts(conn, range(1, 31))
    _text(conn, 1, "Error 0x80070005: Access is denied")
    _text(conn, 2, "Invoice INV-2026-0042\nCall +44 20 7946 0958")
    _text(conn, 3, "Café receipt total ₹1,250.00")
    with lib.tx(conn):
        conn.execute("UPDATE assets SET title = 'Router settings' WHERE id = 4")
        handlers.refresh_fts(conn, [4])

    def ids(q, **f):
        return sorted(a["id"] for a in _get(client, "/v1/assets", q=q, limit=100, **f)["items"])

    assert ids("access denied") == [1]  # all words, any order
    assert ids("acc") == [1]  # last word as a prefix: results while typing
    assert ids("80070005") == [1]  # inside a token: exact piece of text
    assert ids("0x8007") == [1]
    assert ids("inv-2026") == [2]
    assert ids("7946 09") == [2]  # part of a phone number as written
    assert ids("2079460958") == [2]  # the same digits typed without spaces
    assert ids("cafe") == [3] and ids("CAFÉ") == [3]  # accents and case ignored
    assert ids("router") == [4]  # titles, tags, notes and file names are searched too
    assert ids("u5") == []  # no such word
    assert ids("!!") == [] and ids("  ") == list(range(1, 31))  # blank = no search
    # together with other filters; the timeline and the filter panel count the same photos
    _text(conn, 10, "Access denied again")
    kinds = {a["id"]: a["kind"] for a in _get(client, "/v1/assets", limit=100)["items"]}
    both = ids("denied")
    assert both == [1, 10]
    shots = [i for i in both if kinds[i] == "screenshot"]
    assert ids("denied", kind="screenshot") == shots
    assert _get(client, "/v1/assets:months", q="denied")["total"] == 2
    f = _get(client, "/v1/assets:facets", q="denied")
    assert f["total"] == 2 and sum(k["count"] for k in f["kind"]) == 2
    assert client.get("/v1/assets", params={"q": "x" * 201}, headers=AUTH).status_code == 422


@pytest.mark.skipif(os.environ.get("IV_SKIP_SLOW") == "1", reason="slow")
def test_100k_text_search_timings(api):  # noqa: F811
    """D2 plan: < 200 ms at 100k photos in CI (HTTP round trip incl.), list and filter counts."""
    conn, client = api
    n = int(os.environ.get("IV_LIB_N", "100000"))
    _seed(conn, n)
    words = "invoice receipt error warning login password meeting order shipped delivered payment refund".split()
    conn.execute("BEGIN")
    handlers.refresh_fts(conn, range(1, n + 1))
    for a in range(1, n + 1):
        body = f"{words[a % 12]} {words[(a * 7) % 12]} ref {a:06d}-{(a * 31) % 9973:04d} total {a % 500}.99"
        conn.execute("UPDATE asset_fts SET ocr_text = ? WHERE rowid = ?", (body, a))
        conn.execute("INSERT INTO ocr_trigram (rowid, ocr_text) VALUES (?, ?)", (a, body))
        if a % 10 == 0:
            conn.execute(
                "INSERT INTO entities (asset_id, entity_type, value, normalized_value, origin) "
                "VALUES (?, 'phone', 'x', ?, 'ai')",
                (a, f"+4420{a:08d}"),
            )
    conn.execute("COMMIT")
    conn.execute("ANALYZE")

    def timed(fn, reps=3):
        best = float("inf")
        for _ in range(reps):
            t0 = time.perf_counter()
            out = fn()
            best = min(best, time.perf_counter() - t0)
        return best * 1000, out

    word_ms, page = timed(lambda: _get(client, "/v1/assets", q="refund password", limit=60))
    prefix_ms, _ = timed(lambda: _get(client, "/v1/assets", q="deliv", limit=60))
    exact_ms, ex = timed(lambda: _get(client, "/v1/assets", q="012345-", limit=60))
    phone_ms, ph = timed(lambda: _get(client, "/v1/assets", q="2000012340", limit=60))
    none_ms, _ = timed(lambda: _get(client, "/v1/assets", q="zebra crossing", limit=60))
    facets_ms, f = timed(lambda: _get(client, "/v1/assets:facets", q="refund"))
    months_ms, m = timed(lambda: _get(client, "/v1/assets:months", q="refund"))
    print(
        f"\n{n} photos with text: words {word_ms:.1f} ms, prefix {prefix_ms:.1f} ms, exact {exact_ms:.1f} ms, "
        f"phone {phone_ms:.1f} ms, no match {none_ms:.1f} ms, facets {facets_ms:.1f} ms, timeline {months_ms:.1f} ms"
    )
    assert len(page["items"]) == 60 and [a["id"] for a in ex["items"]] == [12345]
    assert [a["id"] for a in ph["items"]] == [12340]
    assert f["total"] == m["total"] > 0
    assert max(word_ms, prefix_ms, exact_ms, phone_ms, none_ms, months_ms) < 200
    assert facets_ms < (600 if os.name == "nt" else 300)  # the filter panel has its own budget (B15)
