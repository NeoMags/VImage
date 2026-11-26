"""Duplicate finder (D1, doc 06 stage 10).

Two kinds of group, kept apart:

* Copies (level 'perceptual'): the same picture re-saved, resized or re-compressed. Perceptual
  hashes (pHash, 64 bits) at most `phash_max` bits apart.
* Similar shots (level 'visual'): bursts and near-identical shots. CLIP cosine similarity at
  least `clip_min` AND dates at most `window_min` minutes apart. The time window keeps this
  precise (two beach photos from different years are not "the same shot") and cheap: a photo is
  only compared with the few taken around the same time.

Identical files are not grouped here: they are already one photo with several locations.

Nothing is ever deleted. Resolving a group hides the photos you did not keep (reversible in the
Hidden view) after copying their tags, manual collections and favourite onto the one you kept.
"Not duplicates" is remembered per pair, so the same photos are never grouped again.

New photos are checked as they are processed (`check_copies` after the thumbnail stage,
`check_similar` after the embedding stage); `rescan` rebuilds all open groups.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from itertools import combinations

import numpy as np

from . import settings

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
LEVELS = {"copies": "perceptual", "similar": "visual"}
TABS = {v: k for k, v in LEVELS.items()}
PHASH_K = 16  # nearest hashes looked at per new photo; more than this many copies is very unlikely
WINDOW_CAP = 200  # most photos compared per new photo for Similar shots
DATE_FMT = "%Y-%m-%dT%H:%M:%S"
# a real date for Similar shots: taken, else the file's modified time (never "when it was added",
# which would put a whole import in one time window)
REAL_DATE = "coalesce(a.taken_at, a.file_modified_at)"


def options(conn: sqlite3.Connection) -> settings.DuplicateOptions:
    return settings.load(conn, "duplicates")


def _parse(d: str | None) -> datetime | None:
    if not d:
        return None
    try:
        return datetime.strptime(d[:19], DATE_FMT)
    except ValueError:
        return None


def _dismissed(conn: sqlite3.Connection, level: str, a: int, others: Iterable[int]) -> set[int]:
    others = list(others)
    if not others:
        return set()
    marks = ",".join("?" for _ in others)
    rows = conn.execute(
        f"SELECT asset_hi FROM duplicate_dismissals WHERE level = ? AND asset_lo = ? AND asset_hi IN ({marks}) "
        f"UNION SELECT asset_lo FROM duplicate_dismissals WHERE level = ? AND asset_hi = ? AND asset_lo IN ({marks})",
        (level, a, *others, level, a, *others),
    )
    return {r[0] for r in rows}


def _link(conn: sqlite3.Connection, level: str, asset_id: int, matches: dict[int, float]) -> int | None:
    """Put `asset_id` and its matches (asset -> similarity) in one open group of `level`, joining
    or merging the open groups any of them are already in. Returns the group id."""
    for other in _dismissed(conn, level, asset_id, matches):
        matches.pop(other)
    if not matches:
        return None
    members = {asset_id: max(matches.values()), **matches}
    ids = list(members)
    marks = ",".join("?" for _ in ids)
    groups = [
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT g.id FROM duplicate_group_members m JOIN duplicate_groups g ON g.id = m.group_id "
            f"WHERE g.level = ? AND g.status = 'open' AND m.asset_id IN ({marks}) ORDER BY g.id",
            (level, *ids),
        )
    ]
    if groups:
        target = groups[0]
        for other in groups[1:]:  # merge: the photo links groups that were apart
            conn.execute(
                "INSERT INTO duplicate_group_members (group_id, asset_id, similarity) "
                "SELECT ?, asset_id, similarity FROM duplicate_group_members WHERE group_id = ? "
                "ON CONFLICT (group_id, asset_id) DO UPDATE SET similarity = max(similarity, excluded.similarity)",
                (target, other),
            )
            conn.execute("DELETE FROM duplicate_groups WHERE id = ?", (other,))
        conn.execute(f"UPDATE duplicate_groups SET updated_at = {NOW} WHERE id = ?", (target,))
    else:
        target = conn.execute(
            f"INSERT INTO duplicate_groups (level, updated_at) VALUES (?, {NOW}) RETURNING id", (level,)
        ).fetchone()[0]
    conn.executemany(
        "INSERT INTO duplicate_group_members (group_id, asset_id, similarity) VALUES (?, ?, ?) "
        "ON CONFLICT (group_id, asset_id) DO UPDATE SET similarity = max(similarity, excluded.similarity)",
        [(target, a, round(s, 4)) for a, s in members.items()],
    )
    return target


# --------------------------------------------------------------------------- new photos


def check_copies(conn: sqlite3.Connection, asset_id: int, opts: settings.DuplicateOptions | None = None) -> int | None:
    """Group this photo with visible photos whose pHash is within `phash_max` bits."""
    opts = opts or options(conn)
    row = conn.execute("SELECT phash FROM assets WHERE id = ? AND is_hidden = 0", (asset_id,)).fetchone()
    if row is None or row[0] is None:
        return None
    rows = conn.execute(
        "WITH knn AS (SELECT rowid, distance FROM vec_phash WHERE phash MATCH vec_bit(?) AND k = ?) "
        "SELECT knn.rowid, knn.distance FROM knn JOIN assets a ON a.id = knn.rowid "
        "WHERE a.is_hidden = 0 AND a.id != ? AND knn.distance <= ?",
        (row[0], PHASH_K, asset_id, opts.phash_max),
    ).fetchall()
    return _link(conn, "perceptual", asset_id, {a: 1 - d / 64 for a, d in rows})


def check_similar(conn: sqlite3.Connection, asset_id: int, opts: settings.DuplicateOptions | None = None) -> int | None:
    """Group this photo with visible photos taken within `window_min` minutes that look alike."""
    opts = opts or options(conn)
    row = conn.execute(
        f"SELECT {REAL_DATE}, v.embedding FROM assets a JOIN embeddings e ON e.asset_id = a.id "
        "JOIN vec_clip_b32 v ON v.rowid = e.id WHERE a.id = ? AND a.is_hidden = 0 LIMIT 1",
        (asset_id,),
    ).fetchone()
    when = _parse(row[0]) if row else None
    if when is None:
        return None
    w = timedelta(minutes=opts.window_min)
    # sort_date (the indexed column) equals the real date whenever there is one
    rows = conn.execute(
        "WITH near AS (SELECT a.id FROM assets a WHERE a.is_hidden = 0 AND a.sort_date >= ? AND a.sort_date <= ? "
        f"AND a.id != ? AND {REAL_DATE} IS NOT NULL ORDER BY a.sort_date LIMIT ?) "
        "SELECT near.id, 1 - vec_distance_cosine(v.embedding, ?) AS s FROM near JOIN embeddings e ON e.asset_id = near.id "
        "JOIN vec_clip_b32 v ON v.rowid = e.id WHERE s >= ?",
        (
            (when - w).strftime(DATE_FMT),
            (when + w).strftime(DATE_FMT) + "~",  # '~' sorts after any fraction/zone suffix
            asset_id,
            WINDOW_CAP,
            row[1],
            opts.clip_min,
        ),
    ).fetchall()
    return _link(conn, "visual", asset_id, dict(rows))


# --------------------------------------------------------------------------- whole library


class _Union:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        p = self.parent.setdefault(x, x)
        while p != self.parent[p]:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        self.parent[x] = p
        return p

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def copy_pairs(ids: np.ndarray, hashes: np.ndarray, max_bits: int, rows: int = 512, cols: int = 8192):
    """All pairs (id_a, id_b, bits) with Hamming distance <= max_bits; hashes are uint64.
    Tiled brute force (about 32 MB per tile): 100k photos is 5e9 XOR + popcount."""
    n = len(ids)
    for i in range(0, n, rows):
        a = hashes[i : i + rows]
        for j in range(i, n, cols):
            d = np.bitwise_count(a[:, None] ^ hashes[None, j : j + cols])
            r, c = np.nonzero(d <= max_bits)
            keep = (j + c) > (i + r)  # each pair once, never a photo with itself
            for rr, cc in zip(r[keep], c[keep], strict=True):
                yield int(ids[i + rr]), int(ids[j + cc]), int(d[rr, cc])


def similar_pairs(ids: np.ndarray, seconds: np.ndarray, vectors: Callable, clip_min: float, window_s: int):
    """Pairs (id_a, id_b, cosine) of photos at most window_s apart with cosine >= clip_min.
    ids/seconds sorted by time; vectors(list of ids) -> unit-length float32 rows."""
    n = len(ids)
    ends = np.searchsorted(seconds, seconds + window_s, side="right")
    cache: dict[int, np.ndarray] = {}
    for i in range(n):
        if ends[i] <= i + 1:
            continue
        window = ids[i : ends[i]].tolist()
        need = [x for x in window if x not in cache]
        if need:
            keep = set(window)
            for k in [k for k in cache if k not in keep]:
                del cache[k]  # only the current window stays in memory
            # fetch ahead so the next photos rarely need another query
            cache.update(vectors(need + [x for x in ids[ends[i] : ends[i] + 256].tolist() if x not in cache]))
        me = cache.get(int(ids[i]))
        if me is None:
            continue
        others = [int(x) for x in ids[i + 1 : ends[i]] if int(x) in cache]
        if not others:
            continue
        sims = np.stack([cache[o] for o in others]) @ me
        for o, s in zip(others, sims, strict=True):
            if s >= clip_min:
                yield int(ids[i]), o, float(s)


def _load_vectors(conn: sqlite3.Connection, ids: list[int]) -> dict[int, np.ndarray]:
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" for _ in chunk)
        for aid, blob in conn.execute(
            f"SELECT e.asset_id, v.embedding FROM embeddings e JOIN vec_clip_b32 v ON v.rowid = e.id "
            f"WHERE e.asset_id IN ({marks})",
            chunk,
        ):
            v = np.frombuffer(blob, dtype=np.float32)
            out[aid] = v / (np.linalg.norm(v) or 1.0)
    return out


def _groups_from_pairs(pairs, dismissed: set[tuple[int, int]]) -> list[dict[int, float]]:
    uf, best = _Union(), {}
    for a, b, s in pairs:
        if (min(a, b), max(a, b)) in dismissed:
            continue
        uf.union(a, b)
        best[a] = max(best.get(a, -1.0), s)
        best[b] = max(best.get(b, -1.0), s)
    comps: dict[int, dict[int, float]] = {}
    for a, s in best.items():
        comps.setdefault(uf.find(a), {})[a] = s
    return [c for c in comps.values() if len(c) >= 2]


def rescan(
    conn: sqlite3.Connection,
    lock=None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Rebuild every open group from scratch with the current thresholds. Resolved and dismissed
    groups (your decisions) are kept. Reading and comparing run outside `lock` (the database
    lock, when the API runs this in the background); only the final write takes it."""
    import contextlib

    lock = lock or contextlib.nullcontext()
    with lock:
        opts = options(conn)
        hashes = conn.execute(
            "SELECT id, phash FROM assets WHERE is_hidden = 0 AND phash IS NOT NULL ORDER BY id"
        ).fetchall()
        dated = conn.execute(
            f"SELECT a.id, {REAL_DATE} FROM assets a WHERE a.is_hidden = 0 AND {REAL_DATE} IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM embeddings e WHERE e.asset_id = a.id)"
        ).fetchall()
        dismissed = {lvl: set() for lvl in LEVELS.values()}
        for lo, hi, lvl in conn.execute("SELECT asset_lo, asset_hi, level FROM duplicate_dismissals"):
            dismissed[lvl].add((lo, hi))
    total = len(hashes) + len(dated)
    if progress:
        progress(0, total)

    ids = np.array([r[0] for r in hashes], dtype=np.int64)
    hv = np.array([int.from_bytes(r[1], "big") for r in hashes], dtype=np.uint64)
    copies = _groups_from_pairs(copy_pairs(ids, hv, opts.phash_max), dismissed["perceptual"])
    if progress:
        progress(len(hashes), total)

    timed = sorted(((t.timestamp(), a) for a, d in dated if (t := _parse(d)) is not None))
    sids = np.array([a for _, a in timed], dtype=np.int64)
    secs = np.array([t for t, _ in timed], dtype=np.float64)

    def vectors(need):
        with lock:
            return _load_vectors(conn, need)

    similar = _groups_from_pairs(
        similar_pairs(sids, secs, vectors, opts.clip_min, opts.window_min * 60), dismissed["visual"]
    )
    if progress:
        progress(total, total)

    with lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM duplicate_groups WHERE status = 'open'")
            for level, groups in (("perceptual", copies), ("visual", similar)):
                for members in groups:
                    gid = conn.execute(
                        f"INSERT INTO duplicate_groups (level, updated_at) VALUES (?, {NOW}) RETURNING id", (level,)
                    ).fetchone()[0]
                    conn.executemany(
                        "INSERT INTO duplicate_group_members (group_id, asset_id, similarity) VALUES (?, ?, ?)",
                        [(gid, a, round(s, 4)) for a, s in members.items()],
                    )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return {"copies": len(copies), "similar": len(similar), "photos_checked": len(hashes)}


# --------------------------------------------------------------------------- review

MEMBER_COLS = (
    "a.id, a.uid, a.kind, a.mime_type, a.sort_date, a.byte_size, a.width, a.height, a.is_favorite, "
    "(SELECT rel_path FROM derivatives d WHERE d.asset_id = a.id AND d.kind = 'thumb_256'), "
    "(SELECT l.rel_path FROM file_locations l WHERE l.asset_id = a.id ORDER BY l.state = 'present' DESC, l.id LIMIT 1), "
    "(SELECT count(*) FROM file_locations l WHERE l.asset_id = a.id), m.similarity"
)
MEMBER_KEYS = (
    "id uid kind mime_type date byte_size width height is_favorite thumb_path path locations similarity"
).split()
VISIBLE_GROUP = (
    # CROSS JOIN: members first (the group's few rows), else SQLite walks every visible photo
    "g.status = 'open' AND (SELECT count(*) FROM duplicate_group_members m CROSS JOIN assets a ON a.id = m.asset_id "
    "WHERE m.group_id = g.id AND a.is_hidden = 0) >= 2"
)


def _keeper_rank(m: dict) -> tuple:
    """Suggested keeper first: favourite, most pixels, larger file, earliest date, oldest in library."""
    return (
        not m["is_favorite"],
        -((m["width"] or 0) * (m["height"] or 0)),
        -(m["byte_size"] or 0),
        m["date"] or "9999",
        m["id"],
    )


def _members(conn: sqlite3.Connection, group_id: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT {MEMBER_COLS} FROM duplicate_group_members m CROSS JOIN assets a ON a.id = m.asset_id "
        "WHERE m.group_id = ? AND a.is_hidden = 0",
        (group_id,),
    ).fetchall()
    out = [dict(zip(MEMBER_KEYS, r, strict=True)) for r in rows]
    for m in out:
        m["is_favorite"] = bool(m["is_favorite"])
    out.sort(key=_keeper_rank)
    for i, m in enumerate(out):
        m["suggested_keep"] = i == 0
    return out


def list_groups(conn: sqlite3.Connection, tab: str, offset: int = 0, limit: int = 50) -> dict:
    level = LEVELS[tab]
    total = conn.execute(
        f"SELECT count(*) FROM duplicate_groups g WHERE g.level = ? AND {VISIBLE_GROUP}", (level,)
    ).fetchone()[0]
    ids = [
        r[0]
        for r in conn.execute(
            f"SELECT g.id FROM duplicate_groups g WHERE g.level = ? AND {VISIBLE_GROUP} ORDER BY g.id DESC "
            "LIMIT ? OFFSET ?",
            (level, limit, offset),
        )
    ]
    return {"total": total, "items": [{"id": g, "level": tab, "members": _members(conn, g)} for g in ids]}


def summary(conn: sqlite3.Connection) -> dict:
    counts = dict(
        conn.execute(
            f"SELECT g.level, count(*) FROM duplicate_groups g WHERE {VISIBLE_GROUP} GROUP BY g.level"
        ).fetchall()
    )
    multi = conn.execute(
        "SELECT count(*) FROM assets a WHERE a.is_hidden = 0 AND "
        "(SELECT count(*) FROM file_locations l WHERE l.asset_id = a.id) >= 2"
    ).fetchone()[0]
    return {"copies": counts.get("perceptual", 0), "similar": counts.get("visual", 0), "multi_location": multi}


class GroupError(ValueError):
    pass


def _open_group(conn: sqlite3.Connection, group_id: int) -> tuple[str, list[dict]]:
    row = conn.execute("SELECT level, status FROM duplicate_groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise LookupError(f"duplicate group {group_id} not found")
    if row[1] != "open":
        raise GroupError(f"this group is already {row[1]}")
    return row[0], _members(conn, group_id)


def merge_onto(conn: sqlite3.Connection, keeper: int, others: list[int]) -> None:
    """Copy tags (not ones you rejected), manual collections and favourite onto the kept photo."""
    marks = ",".join("?" for _ in others)
    conn.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, origin, confidence, confirmed_at) "
        f"SELECT ?, tag_id, origin, max(confidence), max(confirmed_at) FROM asset_tags "
        f"WHERE asset_id IN ({marks}) AND rejected = 0 GROUP BY tag_id "
        "ON CONFLICT (asset_id, tag_id) DO NOTHING",
        (keeper, *others),
    )
    conn.execute(
        "INSERT INTO collection_assets (collection_id, asset_id, position) "
        f"SELECT collection_id, ?, min(position) FROM collection_assets WHERE asset_id IN ({marks}) "
        "GROUP BY collection_id ON CONFLICT (collection_id, asset_id) DO NOTHING",
        (keeper, *others),
    )
    conn.execute(
        f"UPDATE assets SET is_favorite = 1, updated_at = {NOW} WHERE id = ? AND is_favorite = 0 AND EXISTS "
        f"(SELECT 1 FROM assets WHERE id IN ({marks}) AND is_favorite = 1)",
        (keeper, *others),
    )


def resolve(conn: sqlite3.Connection, group_id: int, keep: list[int] | None = None) -> dict:
    """Keep `keep` (default: the suggested keeper), hide the rest of the group. The first kept
    photo receives the hidden photos' tags, collections and favourite."""
    from . import handlers
    from . import library as lib

    _, members = _open_group(conn, group_id)
    visible = [m["id"] for m in members]
    keep = keep or visible[:1]
    if not set(keep) <= set(visible):
        raise GroupError("you can only keep photos of this group")
    hide = [a for a in visible if a not in keep]
    if hide:
        merge_onto(conn, keep[0], hide)
        lib.set_hidden(conn, hide, True)
    conn.executemany(
        "UPDATE duplicate_group_members SET decision = ? WHERE group_id = ? AND asset_id = ?",
        [("keep", group_id, a) for a in keep] + [("hide", group_id, a) for a in hide],
    )
    conn.execute(
        f"UPDATE duplicate_groups SET status = 'resolved', resolved_at = {NOW}, updated_at = {NOW} WHERE id = ?",
        (group_id,),
    )
    handlers.refresh_fts(conn, [keep[0]])
    return {"kept": keep, "hidden": hide}


def resolve_all(conn: sqlite3.Connection, tab: str) -> dict:
    level = LEVELS[tab]
    ids = [
        r[0]
        for r in conn.execute(f"SELECT g.id FROM duplicate_groups g WHERE g.level = ? AND {VISIBLE_GROUP}", (level,))
    ]
    hidden = 0
    for g in ids:
        # an earlier group may have hidden photos of this one; skip it if fewer than 2 remain
        if len(_members(conn, g)) >= 2:
            hidden += len(resolve(conn, g)["hidden"])
    return {"groups": len(ids), "hidden": hidden}


def dismiss(conn: sqlite3.Connection, group_id: int) -> dict:
    """Not duplicates: every pair of the group's photos is remembered and never grouped again."""
    level, members = _open_group(conn, group_id)
    ids = sorted(m["id"] for m in members)
    conn.executemany(
        "INSERT INTO duplicate_dismissals (asset_lo, asset_hi, level) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        [(a, b, level) for a, b in combinations(ids, 2)],
    )
    conn.execute(f"UPDATE duplicate_groups SET status = 'dismissed', updated_at = {NOW} WHERE id = ?", (group_id,))
    return {"pairs": len(ids) * (len(ids) - 1) // 2}
