# 04 · API contracts (web, desktop, mobile ↔ backend)

The FastAPI service will generate the authoritative OpenAPI 3.1 document at `/v1/openapi.json`; `packages/shared-types` is generated from it. This file is the design contract that code must match.

## 1. Conventions
- Base: `https://api.<domain>/v1`. JSON, UTF-8. IDs are UUIDs. Times RFC 3339 UTC.
- Auth: `Authorization: Bearer <Supabase access JWT>`. API verifies signature (JWKS), `exp`, `aud`; then queries Postgres *as the user* so RLS applies.
- Workspace scope: every tenant route is under `/workspaces/{ws}`; API re-checks membership (defence in depth).
- Pagination: keyset. `?limit=60&cursor=<opaque>` → `{ items, next_cursor }`. No offset paging.
- Concurrency: mutable resources return `row_version`; updates send `If-Match: <row_version>` → `409 conflict` with the current resource if stale.
- Idempotency: `POST` that creates things accepts `Idempotency-Key` header (stored 24 h).
- Errors (RFC 9457 problem+json): `{ type, title, status, code, detail, request_id }`. Codes: `unauthenticated, forbidden, not_found, conflict, validation_failed, duplicate_asset, quota_exceeded, rate_limited, unsupported_media, source_unreachable`.
- Rate limits **[proposed]**: 600 req/min/user general, 60 search/min, 20 upload sessions/min; `429` with `Retry-After`.
- Provenance on every derived field: `{ value, origin: "source"|"ai"|"user", confidence? }`.

## 2. Endpoints by phase

### Phase 1
| Method & path | Purpose |
|---|---|
| `GET /me` | Profile, workspaces, roles |
| `POST /devices` · `DELETE /devices/{id}` | Register / revoke a device (desktop, mobile) |
| `POST /workspaces/{ws}/uploads` | Start upload: `{filename, byte_size, mime_type, sha256}` → `201 {upload_id, transport:"tus", endpoint, headers, expires_at}` or `200 {duplicate_of: asset_id}` |
| `POST /workspaces/{ws}/uploads/{id}/complete` | Verify object exists + hash; create asset + jobs → `{asset}` |
| `GET /workspaces/{ws}/assets` | List with filters (`kind, from, to, tag, collection, favorite, reviewed, mime, min_w, …`), `sort=imported_at|taken_at` |
| `GET /workspaces/{ws}/assets/{id}` | Detail: metadata, derivatives (signed URLs, 10 min), locations, tags, collections, notes, job states |
| `PATCH /workspaces/{ws}/assets/{id}` | `title, is_favorite, review_state, taken_at` (user origin) |
| `POST /workspaces/{ws}/assets/{id}/hide` · `/restore` | Soft-hide / restore (never touches originals) |
| `GET /workspaces/{ws}/assets/{id}/original` | `302` to signed URL if stored; `409 source_unreachable` if reference-only |
| `POST /workspaces/{ws}/assets:bulk` | `{asset_ids[≤1000], add_tags[], remove_tags[], add_to_collection?, set_favorite?}` |
| `GET/POST/PATCH/DELETE /workspaces/{ws}/tags` | Tag CRUD |
| `GET/POST/PATCH/DELETE /workspaces/{ws}/collections` | Collections (manual; `smart` from Phase 3) |
| `POST/DELETE /workspaces/{ws}/collections/{id}/assets` | Add/remove/reorder |
| `GET/POST/PATCH/DELETE /workspaces/{ws}/assets/{id}/notes` | Notes |
| `GET /workspaces/{ws}/jobs?status=&stage=` · `POST /jobs/{id}/retry` | Processing queue |
| `GET /workspaces/{ws}/stats` | Dashboard counters |
| `POST /workspaces/{ws}/exports` · `GET /exports/{id}` | Async export → signed ZIP URL |
| `DELETE /me` | Account deletion (async, confirmed by re-auth) |
| `WS /workspaces/{ws}/events` | Server push: `asset.updated`, `job.updated`, `sync.progress` |

### Phase 2–3
| Method & path | Purpose |
|---|---|
| `POST /workspaces/{ws}/search` | Unified search (below) |
| `GET /workspaces/{ws}/assets/{id}/text` | OCR text + regions |
| `GET/PATCH /workspaces/{ws}/knowledge-cards/{id}` | Card read/edit (edits flip status to `user_reviewed`) |
| `GET /workspaces/{ws}/knowledge-cards/{id}/export?format=md|pdf` | Export |
| `POST /workspaces/{ws}/assets/{id}/tags/{tag}/confirm` · `/reject` | Provenance actions |
| `GET /workspaces/{ws}/duplicates?status=open` · `POST /duplicates/{id}/resolve` | Duplicate manager; resolve = keep/hide only |
| `GET/POST /workspaces/{ws}/saved-searches` | Saved searches |

### Phase 4–6 (sync & connectors)
| Method & path | Purpose |
|---|---|
| `GET /workspaces/{ws}/sync/changes?since=<seq>&limit=500` | Pull change feed `{changes:[{seq,entity,id,op,row_version,data}], next_seq, has_more}` |
| `POST /workspaces/{ws}/sync/push` | Batch of device-originated mutations `{device_id, mutations:[{client_mutation_id, entity, id, op, base_row_version, fields}]}` → per-item `applied|conflict|rejected` |
| `POST /workspaces/{ws}/sync/assets:upsert-local` | Desktop/mobile register locally indexed assets (metadata, hashes, optional embeddings with model id) |
| `POST /workspaces/{ws}/derivatives:upload-url` | Signed URL for device-generated thumbnails |
| `GET/POST/PATCH/DELETE /workspaces/{ws}/connections` | Storage connections; `POST /connections/{id}:pause|resume|scan` |
| `GET /oauth/{provider}/start` · `/callback` | Provider OAuth (tokens → Vault) |
| `POST /workspaces/{ws}/google-photos/picker-sessions` | Create Google Photos Picker session; poll; import selected items |

### Phase 7–8
`/people`, `/people/{id}/faces`, `/face-clusters/{id}:merge|split|assign` (assign requires explicit user call), `/consents`, `/graph?node=…&depth=…`, MCP server (separate process, OAuth scopes `assets:read`, `knowledge:read`, later `knowledge:write`).

## 3. Search contract
```jsonc
// POST /v1/workspaces/{ws}/search
{
  "mode": "all|knowledge|screenshots|ocr|similar|people|architecture|documents|videos",
  "q": "postgres connection refused",           // optional
  "exact": false,                                // true = phrase/substring only, no semantic
  "similar_to": { "asset_id": "…", "region": {"x":0.1,"y":0.2,"w":0.3,"h":0.3} }, // optional
  "filter": {                                    // FilterExpression, also used by Smart Collections
    "and": [
      { "field": "kind", "op": "in", "value": ["screenshot"] },
      { "or": [ { "field": "tag", "op": "eq", "value": "postgresql" },
                { "field": "category", "op": "eq", "value": "programming" } ] },
      { "not": { "field": "review_state", "op": "eq", "value": "reviewed" } },
      { "field": "imported_at", "op": "between", "value": ["2026-01-01","2026-12-31"] }
    ]
  },
  "limit": 60, "cursor": null
}
// 200
{ "items": [ { "asset": { /* summary + thumb url */ },
               "score": 0.83,
               "match_type": "exact_text|keyword|semantic|visual|filter_only",
               "highlights": [ { "source": "ocr", "text": "…connection <em>refused</em>…", "region_id": "…" } ],
               "ai_suggested": false } ],
  "next_cursor": "…", "took_ms": 212 }
```
Filter fields and operators are a closed allow-list validated by Pydantic (server) and Zod (client) from one JSON Schema in `packages/shared-types`. No raw SQL or column names cross the wire.

## 4. Desktop agent ↔ desktop UI (local IPC, Phase 4)
Tauri spawns the agent as a sidecar bound to `127.0.0.1:<random port>` with a per-launch bearer token passed via Tauri; the UI talks to it with the **same resource shapes** as the cloud API (`/local/v1/assets`, `/local/v1/search`, `/local/v1/sources`, `/local/v1/settings/resources`). This lets `packages/api-client` target either backend. Tauri commands are used only for OS things (folder picker, tray, autostart, open in Explorer).

## 5. Versioning & compatibility
Breaking changes → `/v2`. Additive fields allowed in `/v1`; clients must ignore unknown fields. Mobile/desktop send `X-Client-Version`; API can return `426 upgrade_required` below a minimum version.
