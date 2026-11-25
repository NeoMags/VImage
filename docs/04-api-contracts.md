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
### Phase D1
| Method & path | Purpose |
|---|---|
| `GET /health` | `{status, version, db_version, queue: {queued, running, failed}}` |
| `GET/POST /sources` · `PATCH/DELETE /sources/{id}` | Sources CRUD; delete removes records only |
| `POST /sources/{id}/scan` · `/pause` · `/resume` | Manual scan / pause |
| `GET /assets` | Filters: `kind, from, to, source, tag, collection, favorite, reviewed, status, mime, min_w, min_h, hidden`; `sort=taken_at|indexed_at|size` |
| `GET /assets/{id}` | Detail: metadata (source vs user), derivatives, locations (+ reachable flag), tags, collections, notes, jobs |
| `PATCH /assets/{id}` | `title, taken_at, is_favorite, is_reviewed` (stored as user origin) |
| `POST /assets/{id}/hide` · `/unhide` | Reversible, audited |
| `POST /assets/{id}/reveal` | Asks the shell to open the file location in Explorer (returns path) |
| `POST /assets:bulk` | `{ids[≤5000], add_tags, remove_tags, add_to_collection, set_favorite, hide}` |
| `GET/POST/PATCH/DELETE /tags` | |
| `GET/POST/PATCH/DELETE /collections` · `POST/DELETE /collections/{id}/assets` | nested, reorder via `position` |
| `GET/POST/PATCH/DELETE /assets/{id}/notes` | |
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
