"""D1: duplicate finder (Copies by pHash, Similar shots by CLIP + time window), review actions,
the full rescan, its speed at 100k photos, and the /v1/duplicates routes."""

from __future__ import annotations

import io
import json
import os
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageEnhance, ImageFilter

from ivsidecar import db, duplicates, handlers, settings
from ivsidecar.app import create_app
from ivsidecar.stages.hashing import phash_from_grey32

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def conn(tmp_path):
    c = db.open_library(tmp_path / "Library")
    c.execute("INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('s1', 'local_folder', 'P', '/p')")
    yield c
    c.close()


def _add(conn, i, phash=None, vec=None, taken=None, w=100, h=80, size=1000, fav=False, locations=1):
    aid = conn.execute(
        "INSERT INTO assets (uid, sha256, status, mime_type, byte_size, width, height, taken_at, phash, is_favorite) "
        "VALUES (?, ?, 'ready', 'image/jpeg', ?, ?, ?, ?, ?, ?) RETURNING id",
        (f"u{i}", i.to_bytes(32, "big"), size, w, h, taken, phash, int(fav)),
    ).fetchone()[0]
    for k in range(locations):
        conn.execute(
            "INSERT INTO file_locations (source_id, asset_id, file_key, rel_path, size_bytes, mtime) "
            "VALUES (1, ?, ?, ?, 1, '1')",
            (aid, f"k{i}.{k}", f"dir{k}/img{i}.jpg"),
        )
    if phash is not None:
        conn.execute("INSERT INTO vec_phash (rowid, phash) VALUES (?, vec_bit(?))", (aid, phash))
    if vec is not None:
        handlers._apply_embedding(conn, aid, [float(x) for x in vec])
    return aid


def _h(n: int) -> bytes:
    return n.to_bytes(8, "big")


def _flip(n: int, bits: int) -> int:
    return n ^ ((1 << bits) - 1)  # flips the lowest `bits` bits


def _unit(seed: int, base=None, noise=0.0):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=512) if base is None else base + noise * rng.normal(size=512)
    return v / np.linalg.norm(v)


def _groups(conn, level):
    return sorted(
        sorted(a for (a,) in conn.execute("SELECT asset_id FROM duplicate_group_members WHERE group_id = ?", (g,)))
        for (g,) in conn.execute("SELECT id FROM duplicate_groups WHERE level = ? AND status = 'open'", (level,))
    )


# --------------------------------------------------------------------------- detection


def test_copies_group_near_hashes_only_and_merge_groups(conn):
    base = 0x0123_4567_89AB_CDEF
    a = _add(conn, 1, _h(base))
    b = _add(conn, 2, _h(_flip(base, 8)))  # 8 bits apart: a copy
    c = _add(conn, 3, _h(_flip(base, 9)))  # 9 bits: not a copy of a (default phash_max 8)
    d = _add(conn, 4, _h(~base & (2**64 - 1)))  # unrelated
    for x in (a, b, c, d):
        duplicates.check_copies(conn, x)
    assert _groups(conn, "perceptual") == [[a, b, c]]  # c is 1 bit from b: joins through b
    assert duplicates.check_copies(conn, d) is None

    # two separate groups joined by a photo matching both
    e = _add(conn, 5, _h(0xFFFF_0000_0000_0000))
    f = _add(conn, 6, _h(0xFFFF_0000_0000_003F))  # 6 bits from e
    duplicates.check_copies(conn, f)
    assert _groups(conn, "perceptual") == [[a, b, c], [e, f]]
    opts = settings.DuplicateOptions(phash_max=16)
    g = _add(conn, 7, _h(base ^ 0xFFFF_0000_0000_0000 ^ 0x0123_4567_0000_0000))
    duplicates.check_copies(conn, g, opts)
    assert len(_groups(conn, "perceptual")) <= 2


def test_hidden_photos_are_never_matched(conn):
    a = _add(conn, 1, _h(5))
    b = _add(conn, 2, _h(5))
    conn.execute("UPDATE assets SET is_hidden = 1 WHERE id = ?", (b,))
    assert duplicates.check_copies(conn, a) is None


def test_similar_shots_need_both_a_close_look_and_a_close_time(conn):
    v = _unit(1)
    a = _add(conn, 1, vec=v, taken="2024-05-01T10:00:00")
    b = _add(conn, 2, vec=_unit(2, v, 0.01), taken="2024-05-01T10:04:00")  # same shot, 4 min later
    c = _add(conn, 3, vec=_unit(3, v, 0.01), taken="2024-05-01T11:00:00")  # looks the same, an hour later
    d = _add(conn, 4, vec=_unit(4), taken="2024-05-01T10:01:00")  # same time, different picture
    e = _add(conn, 5, vec=_unit(5, v, 0.01))  # no date at all
    for x in (a, b, c, d, e):
        duplicates.check_similar(conn, x)
    assert _groups(conn, "visual") == [[a, b]]
    assert _groups(conn, "perceptual") == []


# --------------------------------------------------------------------------- review


def test_resolve_hides_others_and_moves_tags_collections_favourite(conn):
    a = _add(conn, 1, _h(9), w=4000, h=3000)  # most pixels: suggested keeper
    b = _add(conn, 2, _h(9), w=1000, h=750, fav=True)
    c = _add(conn, 3, _h(9), w=1000, h=750)
    for x in (a, b, c):
        duplicates.check_copies(conn, x)
    from ivsidecar import library as lib

    lib.get_or_create_tags(conn, ["beach", "nope"])
    conn.execute("INSERT INTO asset_tags (asset_id, tag_id, origin) VALUES (?, 1, 'user')", (b,))
    conn.execute("INSERT INTO asset_tags (asset_id, tag_id, origin, rejected) VALUES (?, 2, 'ai', 1)", (c,))
    conn.execute("INSERT INTO collections (uid, name) VALUES ('c1', 'Trip')")
    conn.execute("INSERT INTO collection_assets (collection_id, asset_id) VALUES (1, ?)", (c,))

    [g] = duplicates.list_groups(conn, "copies")["items"]
    # a favourite outranks pixels
    assert [m["id"] for m in g["members"]] == [b, a, c] and g["members"][0]["suggested_keep"]
    out = duplicates.resolve(conn, g["id"], [a])
    assert out == {"kept": [a], "hidden": [b, c]}
    assert conn.execute("SELECT id FROM assets WHERE is_hidden = 1 ORDER BY id").fetchall() == [(b,), (c,)]
    assert conn.execute("SELECT tag_id FROM asset_tags WHERE asset_id = ?", (a,)).fetchall() == [(1,)]
    assert sorted(conn.execute("SELECT asset_id FROM collection_assets").fetchall()) == [(a,), (c,)]
    assert conn.execute("SELECT is_favorite FROM assets WHERE id = ?", (a,)).fetchone() == (1,)
    assert duplicates.list_groups(conn, "copies")["total"] == 0
    assert conn.execute("SELECT status FROM duplicate_groups").fetchone() == ("resolved",)
    with pytest.raises(duplicates.GroupError):
        duplicates.resolve(conn, g["id"])
    # a resolved photo can be unhidden; nothing was deleted
    assert conn.execute("SELECT count(*) FROM assets").fetchone() == (3,)


def test_dismissed_photos_are_never_grouped_again(conn):
    a, b = _add(conn, 1, _h(3)), _add(conn, 2, _h(3))
    duplicates.check_copies(conn, b)
    [g] = duplicates.list_groups(conn, "copies")["items"]
    assert duplicates.dismiss(conn, g["id"]) == {"pairs": 1}
    assert duplicates.check_copies(conn, a) is None
    assert duplicates.check_copies(conn, b) is None
    duplicates.rescan(conn)
    assert _groups(conn, "perceptual") == []
    # a third copy groups with both: a and b are linked through it, as the review screen shows
    c = _add(conn, 3, _h(3))
    duplicates.check_copies(conn, c)
    assert _groups(conn, "perceptual") == [[a, b, c]]


def test_resolve_all_keeps_suggested_and_skips_groups_already_emptied(conn):
    pairs = [0, 2**64 - 1, 0x0000_0000_FFFF_FFFF]  # 32+ bits apart from each other
    ids = [_add(conn, i, _h(pairs[i // 2]), w=100 + i) for i in range(6)]  # 3 pairs
    duplicates.rescan(conn)
    assert len(_groups(conn, "perceptual")) == 3
    out = duplicates.resolve_all(conn, "copies")
    assert out == {"groups": 3, "hidden": 3}
    visible = [r[0] for r in conn.execute("SELECT id FROM assets WHERE is_hidden = 0 ORDER BY id")]
    assert visible == [ids[1], ids[3], ids[5]]  # the wider photo of each pair


def test_rescan_matches_incremental_and_keeps_your_decisions(conn):
    rng = np.random.default_rng(7)
    base = [int(x) for x in rng.integers(0, 2**63, size=20)]
    ids = []
    for i, h in enumerate(base):
        ids.append(_add(conn, 2 * i, _h(h), vec=_unit(i), taken=f"2024-01-{1 + i:02d}T09:00:00"))
        ids.append(
            _add(
                conn,
                2 * i + 1,
                _h(_flip(h, 3)),
                vec=_unit(100 + i, _unit(i), 0.01),
                taken=f"2024-01-{1 + i:02d}T09:03:00",
            )
        )
    for a in ids:
        duplicates.check_copies(conn, a)
        duplicates.check_similar(conn, a)
    inc = (_groups(conn, "perceptual"), _groups(conn, "visual"))
    assert len(inc[0]) == 20 and len(inc[1]) == 20
    [g, *_] = duplicates.list_groups(conn, "copies")["items"]
    duplicates.resolve(conn, g["id"])
    out = duplicates.rescan(conn)
    assert out["copies"] == 19 and out["similar"] == 19  # the resolved pair's copy is hidden now
    assert conn.execute("SELECT count(*) FROM duplicate_groups WHERE status = 'resolved'").fetchone() == (1,)


def test_multi_location_filter(conn):
    from ivsidecar import library as lib

    _add(conn, 1)
    two = _add(conn, 2, locations=2)
    assert [a["id"] for a in lib.list_assets(conn, multi_location=True)["items"]] == [two]
    assert len(lib.list_assets(conn, multi_location=False)["items"]) == 1
    assert duplicates.summary(conn)["multi_location"] == 1


# --------------------------------------------------------------------------- thresholds


def _texture(seed: int, size=(640, 480)) -> Image.Image:
    """A photo-like test image: smooth random colour fields plus shapes, no two alike."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, size=(6, 8, 3), dtype=np.uint8)
    img = Image.fromarray(small).resize(size, Image.BICUBIC)
    px = img.load()
    for _ in range(12):
        x, y, r = (int(v) for v in rng.integers(0, 600, 3) % [size[0], size[1], 80])
        col = tuple(int(c) for c in rng.integers(0, 256, 3))
        for dx in range(-r, r, 2):
            for dy in range(-r, r, 2):
                if 0 <= x + dx < size[0] and 0 <= y + dy < size[1] and dx * dx + dy * dy < r * r:
                    px[x + dx, y + dy] = col
    return img.filter(ImageFilter.GaussianBlur(1))


def _ph(img: Image.Image) -> int:
    grey = np.asarray(img.convert("L").resize((32, 32), Image.BILINEAR), dtype=np.uint8)
    return int.from_bytes(phash_from_grey32(grey), "big")


def _jpeg(img, q):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue()))


VARIANTS = {
    "resize 50%": lambda im: im.resize((im.width // 2, im.height // 2)),
    "jpeg q60": lambda im: _jpeg(im, 60),
    "crop 5%": lambda im: im.crop((16, 12, im.width - 16, im.height - 12)),
    "brightness +10%": lambda im: ImageEnhance.Brightness(im).enhance(1.1),
    "resize 25% + jpeg q70": lambda im: _jpeg(im.resize((im.width // 4, im.height // 4)), 70),
}


def test_phash_threshold_on_edited_copies_and_unrelated_images():
    """Recall on edited copies and false matches among unrelated images at the default phash_max.
    The distances are printed for docs/06 §10 (run with -s)."""
    n = 40
    originals = [_texture(s) for s in range(n)]
    hashes = [_ph(im) for im in originals]
    found = {k: 0 for k in VARIANTS}
    dist = {k: [] for k in VARIANTS}
    for im, h in zip(originals, hashes, strict=True):
        for name, fn in VARIANTS.items():
            d = (h ^ _ph(fn(im))).bit_count()
            dist[name].append(d)
            found[name] += d <= settings.DuplicateOptions().phash_max
    unrelated = [(hashes[i] ^ hashes[j]).bit_count() for i in range(n) for j in range(i + 1, n)]
    print("\npHash bits apart (max / median):", {k: (max(v), int(np.median(v))) for k, v in dist.items()})
    print("unrelated pairs:", len(unrelated), "closest:", min(unrelated))
    for t in (6, 8, 10):
        rec = {k: sum(d <= t for d in v) / n for k, v in dist.items()}
        print(f"phash_max {t}: recall {rec}, false matches {sum(d <= t for d in unrelated)}")
    # default 8 (measured: 6 found only 70% of 5% crops, 8 finds 95%, 10 adds little): re-saved,
    # re-compressed, resized and brightened copies are all found; pHash is weakest on crops
    assert all(found[k] == n for k in VARIANTS if k != "crop 5%"), found
    assert found["crop 5%"] >= n * 0.9, found
    assert sum(d <= 8 for d in unrelated) / len(unrelated) <= 0.01


# --------------------------------------------------------------------------- speed


@pytest.mark.skipif(os.environ.get("IV_SKIP_SLOW") == "1", reason="slow")
def test_rescan_speed_100k(conn):
    """Full rescan over N photos (default 100k): 1 in 50 has an edited copy and 1 in 50 a burst
    partner. Linux budget 60 s, Windows 120 s; per-photo checks under 20 ms (Windows 40)."""
    n = int(os.environ.get("IV_DUP_N", "100000"))
    rng = np.random.default_rng(1)
    hashes = rng.integers(0, 2**63, size=n, dtype=np.int64).astype(np.uint64)
    vecs = rng.normal(size=(n, 512)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    for i in range(0, n, 50):
        hashes[i + 1] = hashes[i] ^ np.uint64(0b111)
        vecs[i + 2] = vecs[i] + 0.005 * rng.normal(size=512)
        vecs[i + 2] /= np.linalg.norm(vecs[i + 2])
    conn.execute("BEGIN")
    model = handlers._clip_model_id(conn)
    for i in range(n):
        minute = i * 7 % (60 * 24 * 365) if i % 50 != 2 else (i - 2) * 7 % (60 * 24 * 365) + 1
        taken = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(1_600_000_000 + minute * 60))
        h = int(hashes[i]).to_bytes(8, "big")
        conn.execute(
            "INSERT INTO assets (id, uid, sha256, status, mime_type, byte_size, taken_at, phash) "
            "VALUES (?, ?, ?, 'ready', 'image/jpeg', 1, ?, ?)",
            (i + 1, f"u{i}", i.to_bytes(32, "big"), taken, h),
        )
        conn.execute("INSERT INTO vec_phash (rowid, phash) VALUES (?, vec_bit(?))", (i + 1, h))
        conn.execute("INSERT INTO embeddings (id, asset_id, model_id) VALUES (?, ?, ?)", (i + 1, i + 1, model))
        conn.execute("INSERT INTO vec_clip_b32 (rowid, embedding) VALUES (?, ?)", (i + 1, vecs[i].tobytes()))
    conn.execute("COMMIT")

    t0 = time.perf_counter()
    out = duplicates.rescan(conn)
    took = time.perf_counter() - t0
    windows = os.name == "nt"
    print(f"\nrescan {n}: {took:.1f} s {out}")
    assert out["copies"] >= n // 50 and out["similar"] >= n // 50
    assert took < (120 if windows else 60)

    per = []
    for aid in rng.integers(1, n + 1, size=50):
        t = time.perf_counter()
        duplicates.check_copies(conn, int(aid))
        duplicates.check_similar(conn, int(aid))
        per.append(time.perf_counter() - t)
    print(f"per photo: median {np.median(per) * 1000:.1f} ms, max {max(per) * 1000:.1f} ms")
    assert np.median(per) < (0.04 if windows else 0.02)
    t = time.perf_counter()
    duplicates.list_groups(conn, "copies")
    duplicates.summary(conn)
    took = time.perf_counter() - t
    print(f"list + summary: {took * 1000:.0f} ms")
    assert took < (0.6 if windows else 0.3)


# --------------------------------------------------------------------------- routes


@pytest.fixture()
def api(tmp_path):
    c = db.open_library(tmp_path / "Library")
    c.execute("INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('s1', 'local_folder', 'P', '/p')")
    yield TestClient(create_app(c, TOKEN, time.time())), c
    c.close()


def test_duplicate_routes(api):
    client, c = api
    a, b = _add(c, 1, _h(42), w=2000, h=1500), _add(c, 2, _h(43))
    x, _ = _add(c, 3, _h(2**64 - 1)), _add(c, 4, _h(2**64 - 2))
    r = client.post("/v1/duplicates:rescan", headers=AUTH)
    assert r.status_code == 202
    for _ in range(100):
        s = client.get("/v1/duplicates:summary", headers=AUTH).json()
        if not s["scanning"]:
            break
        time.sleep(0.05)
    assert s == {"copies": 2, "similar": 0, "multi_location": 0, "scanning": False, "scan_error": None}

    page = client.get("/v1/duplicates", params={"tab": "copies"}, headers=AUTH).json()
    assert page["total"] == 2
    g1 = next(g for g in page["items"] if {m["id"] for m in g["members"]} == {a, b})
    assert g1["members"][0]["id"] == a and g1["members"][0]["suggested_keep"]
    assert client.post(f"/v1/duplicates/{g1['id']}/resolve", headers=AUTH, json={"keep": [x]}).status_code == 409
    r = client.post(f"/v1/duplicates/{g1['id']}/resolve", headers=AUTH)
    assert r.json() == {"kept": [a], "hidden": [b]}
    assert client.post(f"/v1/duplicates/{g1['id']}/resolve", headers=AUTH).status_code == 409
    assert client.post("/v1/duplicates/999/dismiss", headers=AUTH).status_code == 404
    g2 = next(g for g in page["items"] if g["id"] != g1["id"])
    assert client.post(f"/v1/duplicates/{g2['id']}/dismiss", headers=AUTH).json() == {"pairs": 1}
    assert client.post("/v1/duplicates:resolve-all", headers=AUTH, json={"tab": "similar"}).json() == {
        "groups": 0,
        "hidden": 0,
    }
    assert client.get("/v1/duplicates:summary", headers=AUTH).json()["copies"] == 0

    assert client.get("/v1/settings/duplicates", headers=AUTH).json() == {
        "phash_max": 8,
        "clip_min": 0.95,
        "window_min": 10,
    }
    assert client.put("/v1/settings/duplicates", headers=AUTH, json={"phash_max": 40}).status_code == 422
    r = client.put("/v1/settings/duplicates", headers=AUTH, json={"phash_max": 4})
    assert r.json()["phash_max"] == 4
    assert json.loads(c.execute("SELECT value FROM settings WHERE key = 'duplicates'").fetchone()[0])["phash_max"] == 4
    assert client.get("/v1/assets", params={"multi_location": "true"}, headers=AUTH).json()["items"] == []


# --------------------------------------------------------------------------- pipeline


def test_new_copies_are_grouped_by_the_thumbnail_stage(tmp_path):
    from test_jobs import _drain, _library, _register_all

    from ivsidecar.jobqueue import JobQueue
    from ivsidecar.probes import find_exiftool

    if not find_exiftool() and os.environ.get("IV_REQUIRE_EXIFTOOL") != "1":
        pytest.skip("exiftool not installed")
    lib, src, conn = _library(tmp_path)
    im = _texture(3)
    im.save(src / "original.jpg", quality=95)
    im.resize((320, 240)).save(src / "small copy.png")
    _texture(4).save(src / "other.jpg")
    _register_all(conn, src)
    q = JobQueue(lib)
    q.start()
    try:
        _drain(conn)
    finally:
        q.stop()
    page = duplicates.list_groups(conn, "copies")
    assert page["total"] == 1
    names = sorted(m["path"] for m in page["items"][0]["members"])
    assert names == ["original.jpg", "small copy.png"]
    assert page["items"][0]["members"][0]["path"] == "original.jpg"  # more pixels: suggested keeper
