"""Library data for the UI (B8a, doc 04 §2): browse, edit, tags, collections, notes, bulk.

Everything here writes to the library database only; files in sources are never touched.
User edits are recorded in audit_log. Callers hold the API's database lock and run each
write function inside `tx()`, so a change and its audit row commit together.

Decisions (B8 plan, approved 2026-11-25):
* photos whose files are all gone stay, with availability 'missing' (decision 2);
* removing an AI or source tag keeps it as rejected so it is never re-added (decision 3);
* default sort: date taken, else the file's modified date, else when added (decision 4).
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import handlers

NOW = handlers.NOW
MAX_BULK = 5000


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


class Conflict(RuntimeError):
    pass


@contextlib.contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def audit(conn: sqlite3.Connection, action: str, entity: str, entity_id, before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO audit_log (action, entity, entity_id, before, after) VALUES (?, ?, ?, ?, ?)",
        (
            action,
            entity,
            None if entity_id is None else str(entity_id),
            None if before is None else json.dumps(before, default=str),
            None if after is None else json.dumps(after, default=str),
        ),
    )


def _exists(conn: sqlite3.Connection, table: str, id_: int, what: str) -> None:
    if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_,)).fetchone() is None:
        raise NotFound(f"{what} {id_} not found")


def _check_assets(conn: sqlite3.Connection, ids: list[int]) -> list[int]:
    ids = sorted(set(ids))
    if not ids:
        raise Invalid("no photos given")
    if len(ids) > MAX_BULK:
        raise Invalid(f"at most {MAX_BULK} photos per call")
    found = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        found += conn.execute(
            f"SELECT count(*) FROM assets WHERE id IN ({','.join('?' for _ in chunk)})", chunk
        ).fetchone()[0]
    if found != len(ids):
        raise NotFound(f"{len(ids) - found} of the given photos do not exist")
    return ids


def _in(ids: list[int]):
    """Yields (marks, chunk) in chunks below SQLite's variable limit."""
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        yield ",".join("?" for _ in chunk), chunk


# --------------------------------------------------------------------------- listing
AVAILABILITY_SQL = (
    "CASE WHEN EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.id AND l.state = 'present') "
    "THEN 'present' WHEN EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.id AND "
    "l.state = 'unreachable') THEN 'unreachable' ELSE 'missing' END"
)
SORTS = {"date": "a.sort_date", "indexed": "a.indexed_at", "size": "a.byte_size"}
LIST_COLS = (
    "a.id, a.uid, a.kind, a.status, a.mime_type, a.sort_date, a.taken_at, a.indexed_at, a.byte_size, a.width, "
    "a.height, a.duration_ms, a.title, a.is_favorite, a.is_reviewed, a.is_hidden, "
    "(SELECT rel_path FROM derivatives d WHERE d.asset_id = a.id AND d.kind = 'thumb_256'), "
    f"{AVAILABILITY_SQL}, "
    "(SELECT l.rel_path FROM file_locations l WHERE l.asset_id = a.id ORDER BY l.state = 'present' DESC, l.id LIMIT 1)"
)
LIST_KEYS = (
    "id uid kind status mime_type date taken_at indexed_at byte_size width height duration_ms title "
    "is_favorite is_reviewed is_hidden thumb_path availability file_name"
).split()
BOOL_KEYS = {"is_favorite", "is_reviewed", "is_hidden"}


def _row(keys, row) -> dict:
    out = {k: (bool(v) if k in BOOL_KEYS else v) for k, v in zip(keys, row, strict=True)}
    if out.get("file_name"):  # a location's rel_path -> its file name (either separator)
        out["file_name"] = re.split(r"[\\/]", out["file_name"])[-1]
    return out


def _encode_cursor(sort: str, order: str, value, id_: int) -> str:
    raw = json.dumps([sort, order, value, id_], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, sort: str, order: str) -> tuple:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        c_sort, c_order, value, id_ = json.loads(raw)
    except (ValueError, binascii.Error, TypeError) as e:
        raise Invalid("invalid cursor") from e
    if (c_sort, c_order) != (sort, order) or not isinstance(id_, int):
        raise Invalid("the cursor belongs to a different sort order; start again without it")
    return value, id_


def _asset_filters(
    *,
    hidden: bool = False,
    kind: list[str] | None = None,
    status: list[str] | None = None,
    mime: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    source: int | None = None,
    tag: list[int] | None = None,
    collection: int | None = None,
    favorite: bool | None = None,
    reviewed: bool | None = None,
    availability: str | None = None,
    min_w: int | None = None,
    min_h: int | None = None,
) -> tuple[list[str], list]:
    """WHERE clauses shared by the asset list and the month counts, so both always agree."""
    where, args = ["a.is_hidden = ?"], [int(hidden)]
    if kind:
        where.append(f"a.kind IN ({','.join('?' for _ in kind)})")
        args += kind
    if status:
        where.append(f"a.status IN ({','.join('?' for _ in status)})")
        args += status
    if mime:
        where.append("a.mime_type = ?")
        args.append(mime)
    if date_from:
        where.append("a.sort_date >= ?")
        args.append(date_from.isoformat())
    if date_to:
        where.append("a.sort_date < ?")
        args.append((date_to + timedelta(days=1)).isoformat())
    if source is not None:
        where.append("EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.id AND l.source_id = ?)")
        args.append(source)
    for t in tag or []:  # all given tags
        where.append("EXISTS (SELECT 1 FROM asset_tags x WHERE x.asset_id = a.id AND x.tag_id = ? AND x.rejected = 0)")
        args.append(t)
    if collection is not None:
        where.append("EXISTS (SELECT 1 FROM collection_assets c WHERE c.asset_id = a.id AND c.collection_id = ?)")
        args.append(collection)
    if favorite is not None:
        where.append("a.is_favorite = ?")
        args.append(int(favorite))
    if reviewed is not None:
        where.append("a.is_reviewed = ?")
        args.append(int(reviewed))
    if availability:
        if availability not in ("present", "missing", "unreachable"):
            raise Invalid("availability must be present, missing or unreachable")
        where.append(f"({AVAILABILITY_SQL}) = ?")
        args.append(availability)
    if min_w:
        where.append("a.width >= ?")
        args.append(min_w)
    if min_h:
        where.append("a.height >= ?")
        args.append(min_h)
    return where, args


def list_assets(
    conn: sqlite3.Connection,
    *,
    limit: int = 60,
    cursor: str | None = None,
    sort: str = "date",
    order: str = "desc",
    hidden: bool = False,
    kind: list[str] | None = None,
    status: list[str] | None = None,
    mime: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    source: int | None = None,
    tag: list[int] | None = None,
    collection: int | None = None,
    favorite: bool | None = None,
    reviewed: bool | None = None,
    availability: str | None = None,
    min_w: int | None = None,
    min_h: int | None = None,
) -> dict:
    if sort not in (*SORTS, "position"):
        raise Invalid(f"sort must be one of {', '.join(SORTS)}, position")
    if order not in ("asc", "desc"):
        raise Invalid("order must be asc or desc")
    if not 1 <= limit <= 500:
        raise Invalid("limit must be 1-500")
    if sort == "position":  # the user's order inside a collection
        if collection is None:
            raise Invalid("sort=position needs a collection")
        key = (
            f"(SELECT cp.position FROM collection_assets cp WHERE cp.collection_id = {int(collection)} "
            "AND cp.asset_id = a.id)"
        )
    else:
        key = SORTS[sort]
    where, args = _asset_filters(
        hidden=hidden,
        kind=kind,
        status=status,
        mime=mime,
        date_from=date_from,
        date_to=date_to,
        source=source,
        tag=tag,
        collection=collection,
        favorite=favorite,
        reviewed=reviewed,
        availability=availability,
        min_w=min_w,
        min_h=min_h,
    )
    if cursor:
        value, last_id = _decode_cursor(cursor, sort, order)
        where.append(f"({key}, a.id) {'<' if order == 'desc' else '>'} (?, ?)")
        args += [value, last_id]
    direction = "DESC" if order == "desc" else "ASC"
    rows = conn.execute(
        f"SELECT {LIST_COLS}, {key} FROM assets a WHERE {' AND '.join(where)} "
        f"ORDER BY {key} {direction}, a.id {direction} LIMIT ?",
        (*args, limit + 1),
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [_row(LIST_KEYS, r[:-1]) for r in rows]
    nxt = _encode_cursor(sort, order, rows[-1][-1], rows[-1][0]) if more and rows else None
    return {"items": items, "next_cursor": nxt}


def asset_months(conn: sqlite3.Connection, **filters) -> dict:
    """How many assets fall in each month of sort_date (newest first), for the timeline. Same filters as
    list_assets; a null month holds assets with no date at all."""
    where, args = _asset_filters(**filters)
    rows = conn.execute(
        # assets_by_date (is_hidden, sort_date, id) covers this scan; left alone, the planner picks
        # assets_by_size and reads every row (measured: 26 ms vs 188 ms on 100k assets).
        f"SELECT substr(a.sort_date, 1, 7) AS m, count(*) FROM assets a INDEXED BY assets_by_date "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY m ORDER BY m IS NULL, m DESC",
        args,
    ).fetchall()
    months = [{"month": m, "count": n} for m, n in rows]
    return {"total": sum(x["count"] for x in months), "months": months}


# --------------------------------------------------------------------------- detail
def asset_detail(conn: sqlite3.Connection, asset_id: int) -> dict:
    row = conn.execute(
        f"SELECT {LIST_COLS}, a.title_origin, a.taken_at_origin, a.file_modified_at, a.error, a.updated_at, "
        "a.sha256 FROM assets a WHERE a.id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"photo {asset_id} not found")
    out = _row(LIST_KEYS, row[: len(LIST_KEYS)])
    title_origin, taken_origin, file_mtime, error, updated_at, sha = row[len(LIST_KEYS) :]
    out["title"] = {"value": out["title"], "origin": title_origin}
    out["taken_at"] = {"value": out["taken_at"], "origin": taken_origin}
    out.update(file_modified_at=file_mtime, error=error, updated_at=updated_at, sha256=sha.hex())
    md = conn.execute("SELECT * FROM asset_metadata WHERE asset_id = ?", (asset_id,))
    cols = [c[0] for c in md.description]
    md_row = md.fetchone()
    out["metadata"] = (
        {k: v for k, v in zip(cols, md_row, strict=True) if k not in ("asset_id", "raw_exif")} if md_row else None
    )
    out["derivatives"] = [
        {"kind": k, "rel_path": p, "width": w, "height": h}
        for k, p, w, h in conn.execute(
            "SELECT kind, rel_path, width, height FROM derivatives WHERE asset_id = ? ORDER BY kind", (asset_id,)
        )
    ]
    out["locations"] = [
        {
            "id": lid,
            "source_id": sid,
            "source_name": name,
            "rel_path": rel,
            "path": str(Path(root) / rel),
            "state": state,
            "reachable": state == "present" and s_status == "active",
            "last_seen_at": seen,
        }
        for lid, sid, name, root, rel, state, s_status, seen in conn.execute(
            "SELECT l.id, s.id, s.display_name, s.root_path, l.rel_path, l.state, s.status, l.last_seen_at "
            "FROM file_locations l JOIN sources s ON s.id = l.source_id WHERE l.asset_id = ? ORDER BY l.id",
            (asset_id,),
        )
    ]
    out["tags"] = [
        {"id": tid, "name": name, "origin": origin, "confidence": conf, "confirmed": confirmed is not None}
        for tid, name, origin, conf, confirmed in conn.execute(
            "SELECT t.id, t.name, x.origin, x.confidence, x.confirmed_at FROM asset_tags x JOIN tags t ON t.id = x.tag_id "
            "WHERE x.asset_id = ? AND x.rejected = 0 ORDER BY t.name",
            (asset_id,),
        )
    ]
    out["collections"] = [
        {"id": cid, "name": name}
        for cid, name in conn.execute(
            "SELECT c.id, c.name FROM collection_assets x JOIN collections c ON c.id = x.collection_id "
            "WHERE x.asset_id = ? ORDER BY c.name",
            (asset_id,),
        )
    ]
    out["notes"] = list_notes(conn, asset_id)
    out["versions"] = [
        {"sha256": s.hex(), "byte_size": n, "seen_at": t}
        for s, n, t in conn.execute(
            "SELECT sha256, byte_size, seen_at FROM asset_versions WHERE asset_id = ? ORDER BY id", (asset_id,)
        )
    ]
    out["jobs"] = [
        {"id": jid, "stage": st, "status": s, "attempts": n, "last_error": e, "finished_at": f}
        for jid, st, s, n, e, f in conn.execute(
            "SELECT id, stage, status, attempts, last_error, finished_at FROM jobs WHERE asset_id = ? ORDER BY id",
            (asset_id,),
        )
    ]
    return out


# --------------------------------------------------------------------------- edits
def normalize_datetime(value: str) -> str:
    """ISO 8601 date or date-time, offset optional; stored without microseconds."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.fromisoformat(value).strftime("%Y-%m-%dT%H:%M:%S")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="seconds")
    except ValueError as e:
        raise Invalid(f"not a valid date: {value!r}") from e


def patch_asset(conn: sqlite3.Connection, asset_id: int, changes: dict) -> None:
    """changes: any of title, taken_at, is_favorite, is_reviewed. None clears title, and resets
    taken_at to the date recorded in the file (metadata is read again)."""
    row = conn.execute(
        "SELECT title, title_origin, taken_at, taken_at_origin, is_favorite, is_reviewed FROM assets WHERE id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"photo {asset_id} not found")
    before = dict(
        zip(("title", "title_origin", "taken_at", "taken_at_origin", "is_favorite", "is_reviewed"), row, strict=True)
    )
    sets, args = [], []
    if "title" in changes:
        t = (changes["title"] or "").strip() or None
        sets += ["title = ?", "title_origin = ?"]
        args += [t, "user" if t else None]
    if "taken_at" in changes:
        if changes["taken_at"] is None:
            sets += ["taken_at = NULL", "taken_at_origin = NULL"]
            handlers.enqueue(conn, "metadata", asset_id=asset_id, priority=10, requeue=True)
        else:
            sets += ["taken_at = ?", "taken_at_origin = 'user'"]
            args.append(normalize_datetime(changes["taken_at"]))
    for flag in ("is_favorite", "is_reviewed"):
        if flag in changes:
            sets.append(f"{flag} = ?")
            args.append(int(bool(changes[flag])))
    if not sets:
        return
    conn.execute(f"UPDATE assets SET {', '.join(sets)}, updated_at = {NOW} WHERE id = ?", (*args, asset_id))
    if "title" in changes:
        handlers.refresh_fts(conn, [asset_id])
    audit(conn, "update", "asset", asset_id, before, changes)


def set_hidden(conn: sqlite3.Connection, ids: list[int], hidden: bool) -> int:
    ids = _check_assets(conn, ids)
    n = 0
    for marks, chunk in _in(ids):
        n += conn.execute(
            f"UPDATE assets SET is_hidden = ?, updated_at = {NOW} WHERE is_hidden != ? AND id IN ({marks})",
            (int(hidden), int(hidden), *chunk),
        ).rowcount
    audit(conn, "hide" if hidden else "unhide", "asset", ids[0] if len(ids) == 1 else None, None, {"ids": ids})
    return n


def reveal_path(conn: sqlite3.Connection, asset_id: int) -> dict:
    """The full path of a present file of this photo, for the shell to show in Explorer.

    Only paths of known, present locations are ever returned (doc 04 §4)."""
    _exists(conn, "assets", asset_id, "photo")
    for root, rel in conn.execute(
        "SELECT s.root_path, l.rel_path FROM file_locations l JOIN sources s ON s.id = l.source_id "
        "WHERE l.asset_id = ? AND l.state = 'present' AND s.status = 'active' ORDER BY l.id",
        (asset_id,),
    ):
        p = Path(root) / rel
        if p.is_file():
            return {"path": str(p)}
    raise Conflict("no file of this photo can be reached right now")


def library_dir(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def preview_source(conn: sqlite3.Connection, asset_id: int) -> dict:
    """What making the 1600 px preview needs: the existing one if its file is still there, else a
    reachable original (path), the asset's uid and EXIF orientation. `path` is None when no file of
    the photo can be reached (drive offline, file missing)."""
    _exists(conn, "assets", asset_id, "photo")
    lib_dir = library_dir(conn)
    row = conn.execute(
        "SELECT rel_path, width, height FROM derivatives WHERE asset_id = ? AND kind = 'preview_1600'", (asset_id,)
    ).fetchone()
    if row and (lib_dir / row[0]).is_file():
        return {"existing": {"kind": "preview_1600", "rel_path": row[0], "width": row[1], "height": row[2]}}
    uid, orientation = conn.execute(
        "SELECT a.uid, m.orientation FROM assets a LEFT JOIN asset_metadata m ON m.asset_id = a.id WHERE a.id = ?",
        (asset_id,),
    ).fetchone()
    try:
        path = reveal_path(conn, asset_id)["path"]
    except Conflict:
        path = None
    return {"existing": None, "path": path, "uid": uid, "orientation": orientation, "library_dir": lib_dir}


def save_preview(conn: sqlite3.Connection, asset_id: int, rel_path: str, width: int, height: int, size: int) -> None:
    conn.execute(
        "INSERT INTO derivatives (asset_id, kind, rel_path, width, height, byte_size) "
        "VALUES (?, 'preview_1600', ?, ?, ?, ?) ON CONFLICT (asset_id, kind) DO UPDATE SET "
        "rel_path = excluded.rel_path, width = excluded.width, height = excluded.height, byte_size = excluded.byte_size",
        (asset_id, rel_path, width, height, size),
    )


# --------------------------------------------------------------------------- tags
def slugify(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return re.sub(r"[\s_]+", "-", plain.strip())


def _clean_tag_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not 1 <= len(name) <= 100:
        raise Invalid("a tag name must be 1-100 characters")
    return name


def list_tags(conn: sqlite3.Connection, q: str | None = None) -> list[dict]:
    sql = (
        "SELECT t.id, t.name, t.slug, (SELECT count(*) FROM asset_tags x WHERE x.tag_id = t.id AND x.rejected = 0) "
        "FROM tags t"
    )
    args: tuple = ()
    if q:
        sql += " WHERE t.slug LIKE ? ESCAPE '\\'"
        args = (slugify(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)
    return [{"id": i, "name": n, "slug": s, "count": c} for i, n, s, c in conn.execute(sql + " ORDER BY t.slug", args)]


def get_or_create_tags(conn: sqlite3.Connection, names: list[str]) -> list[int]:
    ids = []
    for raw in names:
        name = _clean_tag_name(raw)
        slug = slugify(name)
        row = conn.execute("SELECT id FROM tags WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            row = conn.execute("INSERT INTO tags (name, slug) VALUES (?, ?) RETURNING id", (name, slug)).fetchone()
            audit(conn, "create", "tag", row[0], None, {"name": name})
        ids.append(row[0])
    return ids


def create_tag(conn: sqlite3.Connection, name: str) -> dict:
    name = _clean_tag_name(name)
    existing = conn.execute("SELECT id FROM tags WHERE slug = ?", (slugify(name),)).fetchone()
    if existing:
        raise Conflict(f"a tag with that name already exists (id {existing[0]})")
    tid = get_or_create_tags(conn, [name])[0]
    return {"id": tid, "name": name, "slug": slugify(name), "count": 0}


def _tag_assets(conn: sqlite3.Connection, tag_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT asset_id FROM asset_tags WHERE tag_id = ?", (tag_id,))]


def rename_tag(conn: sqlite3.Connection, tag_id: int, name: str) -> dict:
    _exists(conn, "tags", tag_id, "tag")
    name = _clean_tag_name(name)
    slug = slugify(name)
    other = conn.execute("SELECT id FROM tags WHERE slug = ? AND id != ?", (slug, tag_id)).fetchone()
    if other:
        raise Conflict(f"a tag with that name already exists (id {other[0]}); merge the two instead")
    old = conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()[0]
    conn.execute("UPDATE tags SET name = ?, slug = ? WHERE id = ?", (name, slug, tag_id))
    handlers.refresh_fts(conn, _tag_assets(conn, tag_id))
    audit(conn, "rename", "tag", tag_id, {"name": old}, {"name": name})
    return {"id": tag_id, "name": name, "slug": slug}


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> int:
    _exists(conn, "tags", tag_id, "tag")
    assets = _tag_assets(conn, tag_id)
    name = conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()[0]
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))  # cascades asset_tags
    handlers.refresh_fts(conn, assets)
    audit(conn, "delete", "tag", tag_id, {"name": name, "photos": len(assets)}, None)
    return len(assets)


def merge_tags(conn: sqlite3.Connection, source_id: int, into_id: int) -> int:
    """Moves every photo of tag `source_id` to tag `into_id`, then deletes `source_id`."""
    if source_id == into_id:
        raise Invalid("cannot merge a tag into itself")
    _exists(conn, "tags", source_id, "tag")
    _exists(conn, "tags", into_id, "tag")
    assets = _tag_assets(conn, source_id)
    # a user's yes wins over a rejection on the other tag; otherwise keep what the target has
    conn.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, origin, confidence, rejected, confirmed_at) "
        "SELECT asset_id, ?, origin, confidence, rejected, confirmed_at FROM asset_tags WHERE tag_id = ? "
        "ON CONFLICT (asset_id, tag_id) DO UPDATE SET rejected = min(rejected, excluded.rejected), "
        "confirmed_at = coalesce(confirmed_at, excluded.confirmed_at)",
        (into_id, source_id),
    )
    conn.execute("DELETE FROM tags WHERE id = ?", (source_id,))
    handlers.refresh_fts(conn, assets)
    audit(conn, "merge", "tag", source_id, None, {"into": into_id, "photos": len(assets)})
    return len(assets)


def add_tags(conn: sqlite3.Connection, asset_ids: list[int], tag_ids: list[int]) -> None:
    """User adds tags. A tag the AI suggested becomes confirmed; one the user had rejected
    becomes the user's own again."""
    for t in tag_ids:
        _exists(conn, "tags", t, "tag")
    rows = [(a, t) for a in asset_ids for t in tag_ids]
    conn.executemany(
        "INSERT INTO asset_tags (asset_id, tag_id, origin, confirmed_at) VALUES (?, ?, 'user', "
        f"{NOW}) ON CONFLICT (asset_id, tag_id) DO UPDATE SET "
        "origin = CASE WHEN rejected = 1 THEN 'user' ELSE origin END, rejected = 0, "
        f"confirmed_at = {NOW}",
        rows,
    )
    handlers.refresh_fts(conn, asset_ids)


def remove_tags(conn: sqlite3.Connection, asset_ids: list[int], tag_ids: list[int]) -> None:
    """User removes tags. Their own tags are deleted; AI or file tags are kept as rejected so
    they are never suggested again for that photo (decision 3)."""
    for marks, chunk in _in(asset_ids):
        for t in tag_ids:
            conn.execute(
                f"DELETE FROM asset_tags WHERE tag_id = ? AND origin = 'user' AND asset_id IN ({marks})", (t, *chunk)
            )
            conn.execute(
                f"UPDATE asset_tags SET rejected = 1, confirmed_at = NULL WHERE tag_id = ? AND origin != 'user' "
                f"AND asset_id IN ({marks})",
                (t, *chunk),
            )
    handlers.refresh_fts(conn, asset_ids)


# --------------------------------------------------------------------------- collections
def _clean_collection_name(name: str) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= 200:
        raise Invalid("a collection name must be 1-200 characters")
    return name


def list_collections(conn: sqlite3.Connection) -> list[dict]:
    return [
        {"id": i, "uid": u, "name": n, "parent_id": p, "kind": k, "count": c, "created_at": t}
        for i, u, n, p, k, c, t in conn.execute(
            "SELECT c.id, c.uid, c.name, c.parent_id, c.kind, "
            "(SELECT count(*) FROM collection_assets x WHERE x.collection_id = c.id), c.created_at "
            "FROM collections c ORDER BY c.parent_id IS NOT NULL, c.parent_id, c.name COLLATE NOCASE"
        )
    ]


def _check_parent(conn: sqlite3.Connection, coll_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    _exists(conn, "collections", parent_id, "collection")
    cur, seen = parent_id, set()
    while cur is not None and cur not in seen:
        if cur == coll_id:
            raise Conflict("a collection cannot be moved inside itself")
        seen.add(cur)
        cur = conn.execute("SELECT parent_id FROM collections WHERE id = ?", (cur,)).fetchone()[0]


def create_collection(conn: sqlite3.Connection, name: str, parent_id: int | None = None) -> dict:
    name = _clean_collection_name(name)
    _check_parent(conn, None, parent_id)
    cid = conn.execute(
        "INSERT INTO collections (uid, name, parent_id) VALUES (?, ?, ?) RETURNING id",
        (str(uuid.uuid4()), name, parent_id),
    ).fetchone()[0]
    audit(conn, "create", "collection", cid, None, {"name": name, "parent_id": parent_id})
    return {"id": cid, "name": name, "parent_id": parent_id, "count": 0}


def update_collection(conn: sqlite3.Connection, coll_id: int, changes: dict) -> None:
    _exists(conn, "collections", coll_id, "collection")
    before = dict(
        zip(
            ("name", "parent_id"),
            conn.execute("SELECT name, parent_id FROM collections WHERE id = ?", (coll_id,)).fetchone(),
            strict=True,
        )
    )
    if "name" in changes:
        conn.execute("UPDATE collections SET name = ? WHERE id = ?", (_clean_collection_name(changes["name"]), coll_id))
    if "parent_id" in changes:
        _check_parent(conn, coll_id, changes["parent_id"])
        conn.execute("UPDATE collections SET parent_id = ? WHERE id = ?", (changes["parent_id"], coll_id))
    audit(conn, "update", "collection", coll_id, before, changes)


def delete_collection(conn: sqlite3.Connection, coll_id: int) -> None:
    """Deletes the collection and the collections inside it. Photos are never deleted."""
    _exists(conn, "collections", coll_id, "collection")
    name = conn.execute("SELECT name FROM collections WHERE id = ?", (coll_id,)).fetchone()[0]
    conn.execute("DELETE FROM collections WHERE id = ?", (coll_id,))
    audit(conn, "delete", "collection", coll_id, {"name": name}, None)


def add_to_collection(conn: sqlite3.Connection, coll_id: int, asset_ids: list[int]) -> int:
    """Appends photos at the end (already present ones keep their place)."""
    _exists(conn, "collections", coll_id, "collection")
    if conn.execute("SELECT kind FROM collections WHERE id = ?", (coll_id,)).fetchone()[0] != "manual":
        raise Conflict("photos cannot be added to a smart collection by hand")
    start = conn.execute(
        "SELECT coalesce(max(position), 0) FROM collection_assets WHERE collection_id = ?", (coll_id,)
    ).fetchone()[0]
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, position) VALUES (?, ?, ?)",
        [(coll_id, a, start + i + 1) for i, a in enumerate(asset_ids)],
    )
    added = conn.total_changes - before
    audit(conn, "add", "collection", coll_id, None, {"ids": asset_ids})
    return added


def remove_from_collection(conn: sqlite3.Connection, coll_id: int, asset_ids: list[int]) -> int:
    _exists(conn, "collections", coll_id, "collection")
    n = 0
    for marks, chunk in _in(asset_ids):
        n += conn.execute(
            f"DELETE FROM collection_assets WHERE collection_id = ? AND asset_id IN ({marks})", (coll_id, *chunk)
        ).rowcount
    audit(conn, "remove", "collection", coll_id, None, {"ids": asset_ids})
    return n


def set_position(conn: sqlite3.Connection, coll_id: int, asset_id: int, position: float) -> None:
    """Reorder: the UI sends a position between its new neighbours' positions."""
    if (
        conn.execute(
            "UPDATE collection_assets SET position = ? WHERE collection_id = ? AND asset_id = ?",
            (position, coll_id, asset_id),
        ).rowcount
        == 0
    ):
        raise NotFound("that photo is not in this collection")


# --------------------------------------------------------------------------- notes
def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not 1 <= len(body) <= 20000:
        raise Invalid("a note must be 1-20,000 characters")
    return body


def list_notes(conn: sqlite3.Connection, asset_id: int) -> list[dict]:
    return [
        {"id": i, "body": b, "created_at": c, "updated_at": u}
        for i, b, c, u in conn.execute(
            "SELECT id, body, created_at, updated_at FROM notes WHERE asset_id = ? ORDER BY created_at, id",
            (asset_id,),
        )
    ]


def create_note(conn: sqlite3.Connection, asset_id: int, body: str) -> dict:
    _exists(conn, "assets", asset_id, "photo")
    body = _clean_body(body)
    nid = conn.execute("INSERT INTO notes (asset_id, body) VALUES (?, ?) RETURNING id", (asset_id, body)).fetchone()[0]
    handlers.refresh_fts(conn, [asset_id])
    audit(conn, "create", "note", nid, None, {"asset_id": asset_id})
    return next(n for n in list_notes(conn, asset_id) if n["id"] == nid)


def _note_asset(conn: sqlite3.Connection, asset_id: int, note_id: int) -> str:
    row = conn.execute("SELECT body FROM notes WHERE id = ? AND asset_id = ?", (note_id, asset_id)).fetchone()
    if row is None:
        raise NotFound(f"note {note_id} not found on photo {asset_id}")
    return row[0]


def update_note(conn: sqlite3.Connection, asset_id: int, note_id: int, body: str) -> None:
    old = _note_asset(conn, asset_id, note_id)
    conn.execute(f"UPDATE notes SET body = ?, updated_at = {NOW} WHERE id = ?", (_clean_body(body), note_id))
    handlers.refresh_fts(conn, [asset_id])
    audit(conn, "update", "note", note_id, {"length": len(old)}, {"asset_id": asset_id})


def delete_note(conn: sqlite3.Connection, asset_id: int, note_id: int) -> None:
    _note_asset(conn, asset_id, note_id)
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    handlers.refresh_fts(conn, [asset_id])
    audit(conn, "delete", "note", note_id, None, {"asset_id": asset_id})


# --------------------------------------------------------------------------- bulk
def bulk(conn: sqlite3.Connection, ids: list[int], ops: dict) -> dict:
    """One transaction (the caller's): every change applies to every photo, or nothing does."""
    ids = _check_assets(conn, ids)
    done: dict[str, int] = {}
    if ops.get("add_tags"):
        tag_ids = get_or_create_tags(conn, ops["add_tags"])
        add_tags(conn, ids, tag_ids)
        done["tags_added"] = len(tag_ids)
    if ops.get("remove_tags"):
        tag_ids = [
            r[0]
            for name in ops["remove_tags"]
            for r in conn.execute("SELECT id FROM tags WHERE slug = ?", (slugify(name),))
        ]
        remove_tags(conn, ids, tag_ids)
        done["tags_removed"] = len(tag_ids)
    if ops.get("add_to_collection") is not None:
        done["added_to_collection"] = add_to_collection(conn, ops["add_to_collection"], ids)
    if ops.get("remove_from_collection") is not None:
        done["removed_from_collection"] = remove_from_collection(conn, ops["remove_from_collection"], ids)
    for flag, col in (("set_favorite", "is_favorite"), ("set_reviewed", "is_reviewed")):
        if ops.get(flag) is not None:
            for marks, chunk in _in(ids):
                conn.execute(
                    f"UPDATE assets SET {col} = ?, updated_at = {NOW} WHERE id IN ({marks})", (int(ops[flag]), *chunk)
                )
            done[flag] = len(ids)
    if ops.get("hide") is not None:
        done["hidden_changed"] = set_hidden(conn, ids, bool(ops["hide"]))
    if not done:
        raise Invalid("no change requested")
    audit(conn, "bulk", "asset", None, None, {"count": len(ids), "ops": ops})
    return {"photos": len(ids), **done}
