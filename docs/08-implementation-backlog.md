# 08 · Implementation backlog (Phase 1 detailed, later phases epic-level)

Priority: **P0** = Phase 1 cannot ship without it · **P1** = Phase 1 should have · **P2** = later. Size: S ≤ 1 day, M 2–4 days, L ≥ 1 week (rough estimates for one developer + Claude, not commitments). Each item is done only when its tests pass against real services (no mocks standing in for Supabase/Storage in integration tests).

## Phase 1 — Foundation (web + cloud)
| ID | Item | Pri | Size | Depends on | Done when |
|---|---|---|---|---|---|
| B0 | Neo approves this plan + decisions D1–D5 | P0 | — | — | Decisions recorded in 10-decisions.md |
| B1 | Create GitHub repo, push this skeleton, branch protection, CI skeleton | P0 | S | B0, GitHub access | CI green on empty checks |
| B2 | Supabase dev + staging projects; Supabase CLI local stack | P0 | S | B0 | `supabase db reset` applies migration |
| B3 | Finalize migration; port tests to pgTAP; CI DB job | P0 | M | B2 | RLS matrix test (4 roles × CRUD × tables) passes in CI |
| B4 | Spike: Celery + Valkey + reconciler; pyvips + HEIC; ExifTool in container | P0 | M | B1 | Spike report with measured thumb time/MP and image size of worker container |
| B5 | `python/ivcore`: identify, metadata, hash/pHash, thumbnail stages + unit tests on 50-file fixture set (incl. corrupt, HEIC, huge, no-EXIF) | P0 | L | B4 | Fixture expectations match ExifTool ground truth |
| B6 | `services/api` skeleton: JWT verification (JWKS), user-scoped DB session, problem+json, rate limiting, request ids, OpenAPI | P0 | M | B3 | Auth tests: expired/forged/other-tenant tokens rejected |
| B7 | Upload sessions (TUS to Supabase Storage), complete, duplicate short-circuit, magic-byte check, size limit | P0 | L | B6, B5 | 500-file batch + resume-after-reload E2E passes |
| B8 | Worker service: job claim, stages 1–4, retries, reconciler, WS events | P0 | L | B5, B7 | Kill worker mid-batch → all assets reach `ready` |
| B9 | Asset list/detail/patch/hide/restore, bulk ops, tags, collections, notes endpoints | P0 | L | B6 | API tests incl. viewer-role denial |
| B10 | `packages/shared-types` + `api-client` generated from OpenAPI | P0 | S | B9 | Type-check fails when API contract changes |
| B11 | Web app shell: auth (email + Google), routing, theme, layout, error/empty states | P0 | M | B10 | Google OAuth works on staging domain |
| B12 | Virtualized gallery (grid/list, keyset scroll, date headers), selection + bulk bar | P0 | L | B11 | 100k seeded assets: scroll memory < 500 MB, first page p95 < 250 ms |
| B13 | Asset detail drawer with provenance sections | P0 | M | B12 | Edits persist with origin `user` |
| B14 | Filters (Phase 1 set) | P1 | M | B12 | Seeded-dataset count tests |
| B15 | Collections UI (nested, add/remove/reorder) | P1 | M | B12 | |
| B16 | Processing queue UI + retry | P1 | S | B8 | |
| B17 | Dashboard counters | P1 | S | B9 | |
| B18 | Export (JSON + originals ZIP) and account deletion | P0 | M | B9 | Round-trip + post-deletion verification tests |
| B19 | Audit logging for hide/restore/bulk/delete/export | P0 | S | B9 | |
| B20 | Perf harness: synthetic 10k/100k dataset generator + k6/locust scripts for NFR table | P1 | M | B9 | Report against doc 01 §7 |
| B21 | Security pass: headers, CORS, CSP, signed-URL TTLs, secret scan, dependency audit | P0 | S | B11 | Checklist signed off |
| B22 | Deploy staging (API, worker, Valkey) + runbook | P0 | M | B8, D9 | Staging reachable; backup/restore drill done once |

Critical path: B0 → B1/B2 → B3 → B6 → B7 → B8 → B12 → B22.

## Later phases (epics; each gets its own detailed backlog + PRD before coding)
| Epic | Phase | Key dependencies / spikes first |
|---|---|---|
| E2.1 OCR stage (PaddleOCR + Tesseract fallback), text regions | 2 | Accuracy spike on 200 real screenshots (Neo's sample set) |
| E2.2 Entity extraction + sensitive flagging | 2 | E2.1 |
| E2.3 Knowledge Cards + edit + MD/PDF export | 2 | E2.2, LLM adapter, D4 budget |
| E2.4 FTS + exact search, Screenshots & Knowledge views | 2 | E2.1 |
| E3.1 CLIP embeddings + model registry + reprocess queue | 3 | ONNX vs PyTorch spike |
| E3.2 Hybrid search + filter builder + Smart Collections | 3 | E3.1 |
| E3.3 Duplicate detection + comparison screen | 3 | E3.1 |
| E4.x Desktop agent, watcher, local index, sync push/pull, change_log triggers, Tauri app, installer signing | 4 | D3 local store spike, target-PC benchmark |
| E5.x Expo app, media permissions, background upload, share extension, offline cache, doc/card scan | 5 | Apple/Google developer accounts |
| E6.x Connector framework + Drive, OneDrive, Dropbox, S3/R2; Google Photos Picker | 6 | OAuth app verification for restricted scopes |
| E7.x Faces (opt-in, consent), object/region search, architecture taxonomy | 7 | D6 model licence |
| E8.x Knowledge graph, MCP server, Obsidian/Notion/n8n/webhooks | 8 | |
| E9.x Orgs, teams, plans, billing, entitlements | 9 | Billing provider choice |
