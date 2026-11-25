# 04 · API contracts (desktop UI ↔ local sidecar)

There is one API: the sidecar's localhost API, consumed by the Tauri UI. FastAPI generates the authoritative OpenAPI document at `/v1/openapi.json`; `packages/shared-types` is generated from it. Tauri `invoke` commands cover only OS functions (§4).

## 1. Conventions
- Base `http://127.0.0.1:<port>/v1`; header `Authorization: Bearer <per-launch token>`; CORS only for the Tauri origin.
- JSON; ids are integers in responses plus `uid` where present; times ISO 8601.
- Keyset pagination: `?limit=60&cursor=<opaque>` → `{ items, next_cursor }`.
- Errors (problem+json): `{ type, title, status, code, detail }`. Codes: `not_found, validation_failed, conflict, source_unreachable, unsupported_media, busy`.
- Provenance on derived fields: `{ value, origin: "source"|"ai"|"user", confidence? }`.
- Thumbnails are *not* served over HTTP: responses carry `thumb_path` relative to the cache dir; the UI loads it through Tauri's asset protocol.

## 2. Endpoints
B8a status (2026-11-25): rows marked **Built** exist. Errors are still FastAPI's `{detail}` until B8b switches every endpoint to the problem+json format in §1. User edits are written to `audit_log`; title, tags, notes and file paths are kept in the full-text index (`asset_fts`) for D2 search.

### Phase D1
| Method & path | Purpose |
|---|---|
| `GET /health` | `{status, version, db_version, queue: {queued, running, failed}}` |
| `GET/POST /sources` · `PATCH/DELETE /sources/{id}` | Sources CRUD; delete removes records only |
| `POST /sources/{id}/scan` · `/pause` · `/resume` | Manual scan / pause |
| `GET /assets` | **Built (B8a).** Filters: `kind` (repeatable), `status` (repeatable), `mime`, `from`/`to` (dates, inclusive), `source`, `tag` (repeatable, all must match), `collection`, `favorite`, `reviewed`, `availability=present|missing|unreachable`, `min_w`, `min_h`, `hidden` (default false: hidden photos are left out). `sort=date|indexed|size|position` (`position` = the user's order inside `collection`), `order=desc|asc`. `date` = date taken, else the file's modified date, else when added. Keyset pagination: `limit` ≤ 500, opaque `cursor` tied to its sort. |
| `GET /assets/{id}` | **Built.** Title and date as `{value, origin}`, source metadata, derivatives, locations (`path`, `state`, `reachable`), tags (origin, confidence, confirmed; rejected ones left out), collections, notes, previous versions, jobs, `availability` |
| `PATCH /assets/{id}` | **Built.** `title, taken_at, is_favorite, is_reviewed`; title and date stored with origin `user`. `title: null` clears it; `taken_at: null` re-reads the date from the file. Returns the detail |
| `POST /assets/{id}/hide` · `/unhide` | **Built.** Reversible, audited |
| `POST /assets/{id}/reveal` | **Built.** Returns `{path}` of a present file of a known location (409 if none can be reached); the shell opens Explorer |
| `POST /assets/{id}/tags` · `DELETE /assets/{id}/tags/{tag_id}` | **Built.** Add by names (created if new). Removing a user tag deletes it; removing an AI or file tag keeps it as rejected so it is never re-added |
| `POST /assets:bulk` | **Built.** `{ids[≤5000], add_tags[names], remove_tags[names], add_to_collection, remove_from_collection, set_favorite, set_reviewed, hide}`; one transaction, all or nothing (404 if any id is unknown) |
| `GET/POST/PATCH/DELETE /tags` · `POST /tags/{id}/merge {into}` | **Built.** `GET ?q=` prefix search with counts; names are matched case- and accent-insensitively (`slug`); delete returns how many photos lost the tag |
| `GET/POST/PATCH/DELETE /collections` · `POST/DELETE /collections/{id}/assets {ids}` · `PUT /collections/{id}/assets/{asset_id}/position {position}` | **Built.** Manual collections, nested via `parent_id` (moves that create a loop: 409); deleting removes nested collections too, never photos. New photos are appended; reorder by sending a position between the neighbours' |
| `GET/POST/PATCH/DELETE /assets/{id}/notes` | **Built.** Plain text ≤ 20,000 chars |
| `GET /jobs?status=&stage=` · `POST /jobs/{id}/retry` · `POST /queue/pause|resume` | |
| `GET/PUT /settings/resources` | `{threads, max_ram_mb, only_when_idle, only_on_ac, gpu}` |
| `GET /stats` | Dashboard counters |
| `POST /library/backup` · `POST /library/export` | Snapshot DB; export JSON/CSV |
| `WS /events` | `asset.updated, job.updated, scan.progress, source.status` |

### Phase D2–D3
| Method & path | Purpose |
|---|---|
| `POST /search` | Unified search (below) |
| `GET /assets/{id}/text` | OCR text + line boxes |
| `GET/PATCH /knowledge-cards/{id}` · `GET …/export?format=md|pdf` | |
| `POST /assets/{id}/tags/{tag_id}/confirm` · `/reject` | provenance actions |
| `GET /duplicates?status=open` · `POST /duplicates/{id}/resolve` | keep/hide only |
| `GET/POST /saved-searches` | |

### Phase D4–D6
`POST /imports/takeout` (path to extracted Takeout folder), `POST /imports/files` (drag-drop/clipboard paths), `GET /graph?node=&depth=`, `/people`, `/face-clusters/{id}:merge|split|assign` (assign only by explicit user call), MCP server (stdio, read-only tools from spec §21).

## 3. Search request/response
```jsonc
// POST /v1/search
{ "mode": "all|screenshots|knowledge|ocr|similar|documents|videos",
  "q": "postgres connection refused",
  "exact": false,
  "similar_to": { "asset_id": 123, "region": {"x":0.1,"y":0.2,"w":0.3,"h":0.3} },
  "filter": { "and": [ { "field": "kind", "op": "in", "value": ["screenshot"] },
                       { "or": [ {"field":"tag","op":"eq","value":"postgresql"},
                                 {"field":"category","op":"eq","value":"programming"} ] },
                       { "not": {"field":"is_reviewed","op":"eq","value":true} } ] },
  "limit": 60, "cursor": null }
// 200
{ "items": [ { "asset": {"id":5,"thumb_path":"…","kind":"screenshot"},
               "score": 0.83, "match_type": "exact_text|keyword|semantic|visual|filter_only",
               "ai_suggested": false,
               "highlights": [ {"source":"ocr","text":"…connection <em>refused</em>…","text_region_id": 77} ] } ],
  "next_cursor": null, "took_ms": 41 }
```
Filter fields/operators are a closed allow-list, validated by Pydantic and Zod from one JSON Schema; no SQL fragments cross the API. Ranking: reciprocal-rank fusion of FTS5 bm25 and vector distance, after filters.

## 4. Tauri commands (Rust shell)
`pick_folder()`, `reveal_in_explorer(path)`, `open_with_default_app(path)`, `get_sidecar_endpoint() → {port, token}`, `set_autostart(bool)`, `tray_set_status(text)`. File paths passed to `reveal`/`open` must belong to a known `file_location` (checked by the sidecar first) so the UI can't be used to open arbitrary paths.
