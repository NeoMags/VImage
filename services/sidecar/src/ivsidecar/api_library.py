"""HTTP routes for library data (B8a, doc 04 §2). The logic lives in library.py.

The API shares one database connection between request threads; `lock` serialises its use so
one request's transaction never sees or commits another's statements.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from . import library as lib
from . import schemas as S
from .events import bus

Ids = Annotated[list[int], Field(min_length=1, max_length=lib.MAX_BULK)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetPatch(_Model):
    title: str | None = Field(default=None, max_length=500)
    taken_at: str | None = Field(default=None, max_length=40)
    is_favorite: bool | None = None
    is_reviewed: bool | None = None


class IdsBody(_Model):
    ids: Ids


def asset_filters(
    hidden: bool = False,
    kind: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    mime: Annotated[list[str] | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    source: int | None = None,
    tag: Annotated[list[int] | None, Query()] = None,
    collection: int | None = None,
    favorite: bool | None = None,
    reviewed: bool | None = None,
    availability: Literal["present", "missing", "unreachable"] | None = None,
    min_w: int | None = Query(None, ge=1),
    min_h: int | None = Query(None, ge=1),
    max_w: int | None = Query(None, ge=1),
    max_h: int | None = Query(None, ge=1),
    min_bytes: int | None = Query(None, ge=0),
    max_bytes: int | None = Query(None, ge=0),
) -> dict:
    """The asset filters shared by GET /assets and GET /assets:months."""
    return {k: v for k, v in locals().items()}


class BulkBody(_Model):
    ids: Ids
    add_tags: list[str] | None = Field(default=None, max_length=50)
    remove_tags: list[str] | None = Field(default=None, max_length=50)
    confirm_ai_tags: bool | None = None
    """Accept every AI-suggested tag on these photos (T3 review)."""
    add_to_collection: int | None = None
    remove_from_collection: int | None = None
    set_favorite: bool | None = None
    set_reviewed: bool | None = None
    hide: bool | None = None


class TagBody(_Model):
    name: str = Field(min_length=1, max_length=100)


class TagMerge(_Model):
    into: int


class AssetTags(_Model):
    names: list[str] = Field(min_length=1, max_length=50)


class CollectionBody(_Model):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None


class CollectionPatch(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: int | None = None


class Position(_Model):
    position: float


class NoteBody(_Model):
    body: str = Field(min_length=1, max_length=20000)


def build_router(conn: sqlite3.Connection, lock: threading.RLock) -> APIRouter:
    r = APIRouter(prefix="/v1")

    def run(fn: Callable, *args, write: bool = False, notify_asset: int | None = None, notify: str | None = None, **kw):
        with lock:
            try:
                if write:
                    with lib.tx(conn):
                        out = fn(conn, *args, **kw)
                    if notify_asset is not None:  # after the commit: the UI refetches committed data
                        bus.asset_updated(notify_asset)
                    if notify:
                        bus.changed(notify)
                    return out
                return fn(conn, *args, **kw)
            except lib.NotFound as e:
                raise HTTPException(status_code=404, detail=str(e)) from e
            except lib.Invalid as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
            except lib.Conflict as e:
                raise HTTPException(status_code=409, detail=str(e)) from e

    # ------------------------------------------------------------------ assets
    @r.get("/assets")
    def list_assets(
        filters: Annotated[dict, Depends(asset_filters)],
        limit: int = Query(60, ge=1, le=500),
        cursor: str | None = Query(None, max_length=500),
        sort: Literal["date", "indexed", "size", "position"] = "date",
        order: Literal["asc", "desc"] = "desc",
    ) -> S.AssetPage:
        return run(lib.list_assets, limit=limit, cursor=cursor, sort=sort, order=order, **filters)

    @r.get("/assets:months")
    def asset_months(filters: Annotated[dict, Depends(asset_filters)]) -> S.AssetMonths:
        """Assets per month of their date (newest first), with the same filters as GET /assets."""
        return run(lib.asset_months, **filters)

    @r.get("/assets:facets")
    def asset_facets(
        filters: Annotated[dict, Depends(asset_filters)],
        tag_q: str | None = Query(None, max_length=100),
    ) -> S.AssetFacets:
        """How many assets match each filter option, with the same filters as GET /assets. Each section
        ignores its own selection (except tags, which must all match), so it shows what ticking an
        option would give."""
        return run(lib.asset_facets, tag_q=tag_q, **filters)

    @r.post("/assets:bulk", response_model_exclude_none=True)
    def bulk(body: BulkBody) -> S.BulkResult:
        out = run(lib.bulk, body.ids, body.model_dump(exclude={"ids"}, exclude_none=True), write=True)
        bus.asset_updated(*body.ids)
        if body.add_tags or body.remove_tags or body.confirm_ai_tags:
            bus.changed("tags")
        if body.add_to_collection is not None or body.remove_from_collection is not None:
            bus.changed("collections")
        return out

    @r.get("/assets/{asset_id}")
    def detail(asset_id: int) -> S.AssetDetail:
        return run(lib.asset_detail, asset_id)

    @r.patch("/assets/{asset_id}")
    def patch(asset_id: int, body: AssetPatch) -> S.AssetDetail:
        changes = {k: getattr(body, k) for k in body.model_fields_set}
        if any(changes.get(f) is None for f in ("is_favorite", "is_reviewed") if f in changes):
            raise HTTPException(status_code=422, detail="is_favorite and is_reviewed must be true or false")
        run(lib.patch_asset, asset_id, changes, write=True)
        bus.asset_updated(asset_id)
        return run(lib.asset_detail, asset_id)

    @r.post("/assets/{asset_id}/hide")
    def hide(asset_id: int) -> S.Hidden:
        run(lib.set_hidden, [asset_id], True, write=True)
        bus.asset_updated(asset_id)
        return {"id": asset_id, "is_hidden": True}

    @r.post("/assets/{asset_id}/unhide")
    def unhide(asset_id: int) -> S.Hidden:
        run(lib.set_hidden, [asset_id], False, write=True)
        bus.asset_updated(asset_id)
        return {"id": asset_id, "is_hidden": False}

    @r.post("/assets/{asset_id}/reveal")
    def reveal(asset_id: int) -> S.RevealPath:
        return run(lib.reveal_path, asset_id)

    @r.post("/assets/{asset_id}/preview")
    def asset_preview(asset_id: int) -> S.Derivative:
        """The 1600 px preview, made from the original the first time a photo is opened and kept in the
        library cache. Decoding runs outside the database lock. 409: no file of the photo can be
        reached right now; 422: the file cannot be decoded (the UI keeps showing the thumbnail)."""
        src = run(lib.preview_source, asset_id)
        if src["existing"]:
            return src["existing"]
        if src["path"] is None:
            raise HTTPException(status_code=409, detail="no file of this photo can be reached right now")
        from .stages import StageError
        from .stages.exiftool import ExifTool
        from .stages.identify import identify
        from .stages.thumbnails import make_preview

        path = Path(src["path"])
        try:
            ident = identify(path)
            et = ExifTool() if ident.is_raw else None
            try:
                d = make_preview(path, ident, src["library_dir"], src["uid"], et, src["orientation"])
            finally:
                if et is not None:
                    et.close()
        except StageError as e:
            raise HTTPException(status_code=422, detail=e.message) from e
        except OSError as e:  # the file went away or is locked between the lookup and the read
            raise HTTPException(status_code=409, detail=f"could not read the file: {e.strerror or e}") from e
        run(lib.save_preview, asset_id, d.rel_path, d.width, d.height, d.byte_size, write=True)
        return {"kind": d.kind, "rel_path": d.rel_path, "width": d.width, "height": d.height}

    @r.post("/assets/{asset_id}/tags")
    def tag_asset(asset_id: int, body: AssetTags) -> S.AssetTags:
        def go(c):
            lib._exists(c, "assets", asset_id, "photo")
            lib.add_tags(c, [asset_id], lib.get_or_create_tags(c, body.names))
            lib.audit(c, "tag", "asset", asset_id, None, {"names": body.names})

        run(go, write=True)
        bus.asset_updated(asset_id)
        bus.changed("tags")
        return {"tags": run(lib.asset_detail, asset_id)["tags"]}

    @r.delete("/assets/{asset_id}/tags/{tag_id}", status_code=204)
    def untag_asset(asset_id: int, tag_id: int) -> Response:
        def go(c):
            lib._exists(c, "assets", asset_id, "photo")
            lib._exists(c, "tags", tag_id, "tag")
            lib.remove_tags(c, [asset_id], [tag_id])
            lib.audit(c, "untag", "asset", asset_id, None, {"tag_id": tag_id})

        run(go, write=True)
        bus.asset_updated(asset_id)
        bus.changed("tags")
        return Response(status_code=204)

    # ------------------------------------------------------------------ notes
    @r.get("/assets/{asset_id}/notes")
    def notes(asset_id: int) -> list[S.Note]:
        def go(c):
            lib._exists(c, "assets", asset_id, "photo")
            return lib.list_notes(c, asset_id)

        return run(go)

    @r.post("/assets/{asset_id}/notes", status_code=201)
    def add_note(asset_id: int, body: NoteBody) -> S.Note:
        return run(lib.create_note, asset_id, body.body, write=True, notify_asset=asset_id)

    @r.patch("/assets/{asset_id}/notes/{note_id}")
    def edit_note(asset_id: int, note_id: int, body: NoteBody) -> list[S.Note]:
        run(lib.update_note, asset_id, note_id, body.body, write=True, notify_asset=asset_id)
        return run(lib.list_notes, asset_id)

    @r.delete("/assets/{asset_id}/notes/{note_id}", status_code=204)
    def remove_note(asset_id: int, note_id: int) -> Response:
        run(lib.delete_note, asset_id, note_id, write=True, notify_asset=asset_id)
        return Response(status_code=204)

    # ------------------------------------------------------------------ tags
    @r.get("/tags")
    def tags(q: str | None = Query(None, max_length=100)) -> list[S.Tag]:
        return run(lib.list_tags, q)

    @r.post("/tags", status_code=201)
    def new_tag(body: TagBody) -> S.Tag:
        return run(lib.create_tag, body.name, write=True, notify="tags")

    @r.patch("/tags/{tag_id}")
    def rename_tag(tag_id: int, body: TagBody) -> S.RenamedTag:
        return run(lib.rename_tag, tag_id, body.name, write=True, notify="tags")

    @r.delete("/tags/{tag_id}")
    def delete_tag(tag_id: int) -> S.TagDeleted:
        return {"photos_untagged": run(lib.delete_tag, tag_id, write=True, notify="tags")}

    @r.post("/tags/{tag_id}/merge")
    def merge_tag(tag_id: int, body: TagMerge) -> S.TagMerged:
        return {"photos_moved": run(lib.merge_tags, tag_id, body.into, write=True, notify="tags")}

    # ------------------------------------------------------------------ collections
    @r.get("/collections")
    def collections() -> list[S.Collection]:
        return run(lib.list_collections)

    @r.post("/collections", status_code=201)
    def new_collection(body: CollectionBody) -> S.NewCollection:
        return run(lib.create_collection, body.name, body.parent_id, write=True, notify="collections")

    @r.patch("/collections/{coll_id}")
    def edit_collection(coll_id: int, body: CollectionPatch) -> S.Collection:
        changes = {k: getattr(body, k) for k in body.model_fields_set}
        if "name" in changes and changes["name"] is None:
            raise HTTPException(status_code=422, detail="name cannot be empty")
        run(lib.update_collection, coll_id, changes, write=True, notify="collections")
        return next(c for c in run(lib.list_collections) if c["id"] == coll_id)

    @r.delete("/collections/{coll_id}", status_code=204)
    def delete_collection(coll_id: int) -> Response:
        run(lib.delete_collection, coll_id, write=True, notify="collections")
        return Response(status_code=204)

    @r.post("/collections/{coll_id}/assets")
    def collect(coll_id: int, body: IdsBody) -> S.Added:
        def go(c):
            lib._check_assets(c, body.ids)
            return lib.add_to_collection(c, coll_id, list(dict.fromkeys(body.ids)))  # keeps the given order

        return {"added": run(go, write=True, notify="collections")}

    @r.delete("/collections/{coll_id}/assets")
    def uncollect(coll_id: int, body: IdsBody) -> S.Removed:
        return {"removed": run(lib.remove_from_collection, coll_id, body.ids, write=True, notify="collections")}

    @r.put("/collections/{coll_id}/assets/{asset_id}/position", status_code=204)
    def reorder(coll_id: int, asset_id: int, body: Position) -> Response:
        run(lib.set_position, coll_id, asset_id, body.position, write=True, notify="collections")
        return Response(status_code=204)

    return r
