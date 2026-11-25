# 04 · API contracts (desktop UI ↔ local sidecar)

There is one API: the sidecar's localhost API, consumed by the Tauri UI. FastAPI generates the authoritative OpenAPI document at `/v1/openapi.json`; `packages/shared-types` is generated from it. Tauri `invoke` commands cover only OS functions (§4).

**Keeping the UI in step (B9).** Every route declares a response model (`ivsidecar/schemas.py`, `extra="forbid"`, so an undeclared key fails the response instead of vanishing). `pnpm generate` writes `packages/shared-types/openapi.json`, `events.sample.json` (one real message of every `WS /v1/events` kind) and `src/api.ts`. CI fails when any of the three is stale, and `tsc` fails wherever the UI uses a field that changed. The UI talks to the engine only through `packages/api-client` (typed client, `ApiError`, TanStack Query hooks, live-event refresh).

## 1. Conventions
- Base `http://127.0.0.1:<port>/v1`; header `Authorization: Bearer <per-launch token>`; CORS only for the Tauri origin.
- JSON; ids are integers in responses plus `uid` where present; times ISO 8601.
- Keyset pagination: `?limit=60&cursor=<opaque>` → `{ items, next_cursor }`.
- Errors (problem+json): `{ type, title, status, code, detail }`. Codes: `not_found, validation_failed, conflict, source_unreachable, unsupported_media, busy`.
- Provenance on derived fields: `{ value, origin: "source"|"ai"|"user", confidence? }`.
- Thumbnails are *not* served over HTTP: responses carry `thumb_path` relative to the cache dir; the UI loads it through Tauri's asset protocol.

## 2. Endpoints
B8a status (2026-11-25): rows marked **Built** exist. B8b (same day): every error, from every route, is problem+json as in §1 (`application/problem+json`; validation errors add `errors[{loc, msg}]`; unexpected errors return code `internal` without a stack trace). User edits are written to `audit_log`; title, tags, notes and file paths are kept in the full-text index (`asset_fts`) for D2 search.

### Phase D1
| Method & path | Purpose |
|---|---|
| `GET /health` | **Built.** `{status, version, db_version, queue: {queued, running, failed}}` plus engine probes (libvips, HEIC, ExifTool, stages) |
| `GET/POST /sources` · `PATCH/DELETE /sources/{id}` | Sources CRUD; delete removes records only |
| `POST /sources/{id}/scan` · `/pause` · `/resume` | Manual scan / pause |
| `GET /assets` | **Built (B8a).** Filters: `kind` (repeatable), `status` (repeatable), `mime`, `from`/`to` (dates, inclusive), `source`, `tag` (repeatable, all must match), `collection`, `favorite`, `reviewed`, `availability=present|missing|unreachable`, `min_w`, `min_h`, `hidden` (default false: hidden photos are left out). `sort=date|indexed|size|position` (`position` = the user's order inside `collection`), `order=desc|asc`. `date` = date taken, else the file's modified date, else when added. Keyset pagination: `limit` ≤ 500, opaque `cursor` tied to its sort. |
| `GET /assets:months` | **Built (B12).** Same filters as `GET /assets`; returns `{total, months: [{month: "YYYY-MM", count}]}` newest first, by the same `date` the list sorts on. The gallery uses it to size the timeline and load one month at a time (`from`/`to`). Each item of `GET /assets` also carries `file_name` (a present location's file name first). |
| `GET /assets:facets` | **Built (B15).** Same filters as `GET /assets`, plus `tag_q` (tag search on the slug, `%`/`_` literal). Returns `{total, kind, format, status, availability: [{value, count}], source, collection: [{id, count}], tags: [{id, name, count}] (top 50), favorite, reviewed: {yes, no}, date: {min, max}, size: {min, max}}`. Each section ignores its own selection, so a count is what ticking that option gives; tags are the exception (several tags must all match, so ticking one more narrows). The simple column facets come from one grouped scan; ~200 ms on 100k assets in CI (budget 300 ms). B15 also added filters `mime` (now repeatable), `max_w`, `max_h`, `min_bytes`, `max_bytes`. |
| `GET /assets/{id}` | **Built.** Title and date as `{value, origin}`, source metadata, derivatives, locations (`path`, `state`, `reachable`), tags (origin, confidence, confirmed; rejected ones left out), collections, notes, previous versions, jobs, `availability` |
| `PATCH /assets/{id}` | **Built.** `title, taken_at, is_favorite, is_reviewed`; title and date stored with origin `user`. `title: null` clears it; `taken_at: null` re-reads the date from the file. Returns the detail |
| `POST /assets/{id}/hide` · `/unhide` | **Built.** Reversible, audited |
| `POST /assets/{id}/preview` | **Built (B13).** Returns the `preview_1600` derivative (`{kind, rel_path, width, height}`), making it on first call: a 1600 px long-edge WebP under `<library>/thumbs`, never enlarged, EXIF-rotated, written atomically; the original is only read. Later calls return the saved one, even when the drive is offline. 409 when no file can be reached, 422 when it cannot be decoded or the kind has no preview. Each item of `jobs` in `GET /assets/{id}` now carries its `id`, for `POST /jobs/{id}/retry` |
| `POST /assets/{id}/reveal` | **Built.** Returns `{path}` of a present file of a known location (409 if none can be reached); the shell opens Explorer |
| `POST /assets/{id}/tags` · `DELETE /assets/{id}/tags/{tag_id}` | **Built.** Add by names (created if new). Removing a user tag deletes it; removing an AI or file tag keeps it as rejected so it is never re-added |
| `POST /assets:bulk` | **Built.** `{ids[≤5000], add_tags[names], remove_tags[names], add_to_collection, remove_from_collection, set_favorite, set_reviewed, hide}`; one transaction, all or nothing (404 if any id is unknown) |
| `GET/POST/PATCH/DELETE /tags` · `POST /tags/{id}/merge {into}` | **Built.** `GET ?q=` prefix search with counts; names are matched case- and accent-insensitively (`slug`); delete returns how many photos lost the tag |
| `GET/POST/PATCH/DELETE /collections` · `POST/DELETE /collections/{id}/assets {ids}` · `PUT /collections/{id}/assets/{asset_id}/position {position}` | **Built.** Manual collections, nested via `parent_id` (moves that create a loop: 409); deleting one moves the collections inside it up one level (B16, kept), never deletes photos. New photos are appended; reorder by sending a position between the neighbours' |
| `GET/POST/PATCH/DELETE /assets/{id}/notes` | **Built.** Plain text ≤ 20,000 chars |
| `GET /jobs?status=&stage=&limit=&before_id=` · `POST /jobs/{id}/retry` · `POST /jobs/pause|resume|retry` · `GET /jobs/stats` | **Built (B8b).** Newest first, each with the file's `path` and `last_error`; page with `before_id` = previous `next_before_id`. Retry one: only failed, dead or skipped jobs (409 otherwise). `POST /jobs/retry` retries all failed |
| `GET/PUT /settings/resources` | `{threads, max_ram_mb, only_when_idle, only_on_ac, gpu}` |
| `GET /stats` | **Built.** Photos: total, by status, by kind, hidden, favourites, present / missing / unreachable; unsupported files; tags; collections; sources by status; last scan; jobs by status; queue state; `db_version` vs `db_latest` |
| `GET /backups` · `POST /backups` · `GET/PUT /settings/backup` | **Built (B19).** List `{backups: [{name, kind, created_at, size, schema_version}], folder, needs_restore}`; kinds `auto` (daily at start, last `keep` kept), `manual`, `pre-restore`, `pre-migration`, `damaged` (only `auto` is ever pruned). `POST` makes one now (online backup API, temp name, `integrity_check`, rename) and copies it to `copy_to` if set → `{backup, pruned, copy_error}`. Settings `{keep: 1–100 (7), copy_to: folder or null}`; a folder inside a source is refused (422). Thumbnails are not backed up |
| `POST /backups:restore` | **Built (B19).** `{name}` (a listed backup) or `{path}` (any `.db` file, e.g. on a clean install). 422 if the file fails `integrity_check`, is from a newer schema or is not a library; 409 while another backup/restore/export runs. Pauses the queue (waits up to 60 s), drops watches, cancels scans, makes a `pre-restore` backup, copies the file **into** the live database (online backup API: open connections see the new data, nothing is reopened), migrates, keeps this PC's settings, re-queues thumbnails whose files are missing, then re-watches and reconciles every source → `{restored_from, schema_version, pre_restore, thumbnails_requeued}`; sends `library.restored` |
| `POST /export` | **Built (B19).** `{format: json|csv, path, include_hidden}` → `{path, photos, bytes, format}`. JSON: everything per photo with origins (AI output labelled); CSV: one row per photo, UTF-8 with BOM, AI tags marked "(AI)". Streamed 1,000 photos at a time from a read-only snapshot; written to a temp file then renamed; `.json`/`.csv` added if missing. 422 for a relative path, a missing folder or a folder inside a source. Progress as `task.progress` events |
| `WS /events` | **Built.** Auth: subprotocols `["ivsidecar", "bearer.<token>"]` (browsers cannot set headers on a WebSocket); Origin must be a Tauri origin when present; otherwise the handshake is refused (1008/403). First message `{type: "hello", flush_ms: 250}`, then at most one `{type: "batch", events: [...]}` per 250 ms: `asset.updated {ids}` (or `{all: true}`), `job.updated {count}`, `scan.progress {source_id, run_id, kind, status, files_*}`, `source.status {source_id, status}`, `tags.updated`, `collections.updated`, `task.progress {task, done, total}` (B19 export), `library.restored` (refetch everything), `resync` (window fell behind). Events are hints: refetch what is shown; after a reconnect refetch everything. A 10k-file scan sent 3 messages in the CI container |

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
Built in B10 (`apps/desktop/src-tauri`):

| What the UI calls | Where | Notes |
|---|---|---|
| `get_sidecar_endpoint() → {port, token}` | own command | Errors with "engine not ready yet" while starting/restarting, or the give-up message |
| `get_app_paths() → {library, logs, thumbs}` | own command | For Settings and "see the log in ..." messages |
| Folder picker | `tauri-plugin-dialog`, `open({directory: true})` | Path is then sent to `POST /v1/sources`, which validates it |
| Backup file picker, save dialog (B19) | `tauri-plugin-dialog`, `open({filters: db})`, `save()` (`dialog:allow-save`) | Only paths come back; the engine reads the backup or writes the export, and refuses source folders |
| Show in Explorer | `tauri-plugin-opener`, `revealItemInDir(path)` | Path comes from `POST /v1/assets/{id}/reveal` (a known location). Only `allow-reveal-item-in-dir` is granted: no open, no shell |
| Thumbnails | asset protocol (`convertFileSrc`) | Scope set at startup to `<library>/thumbs/**` only |
| Events `engine-restarted`, `engine-failed` | Tauri events | After `engine-restarted`, fetch a new endpoint (new port and token) |

Engine lifecycle: crash → restart after 1 s, 2 s, 5 s; a 4th crash within 60 s gives up (`engine-failed`). Quit → `POST /v1/shutdown` (stop taking jobs, finish running ones, close the DB), kill after 5 s. Every step is logged to `<library>/logs/startup.log`. Later (D4): `set_autostart(bool)`, `tray_set_status(text)`, open with default app.

### Development-only thumbnail route (B12)
`GET /dev/thumbs/{rel_path}?t=<token>` serves files under `<library>/thumbs` so the UI can show thumbnails in a plain browser (dev server, end-to-end tests). It exists only when the engine is **not** the packaged exe **and** `IV_DEV_ORIGINS` is set, is left out of the OpenAPI contract, and refuses any path outside `thumbs`. The installed app loads thumbnails through the Tauri asset protocol instead (§4).
