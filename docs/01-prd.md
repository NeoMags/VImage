# 01 · Product Requirements Document — ImageVault

Source of truth: master spec (`Vimage System Prompt.txt`). Items marked **[proposed]** are mine, not in the spec, and need Neo's approval. Open choices live in [10-decisions.md](10-decisions.md) as D1…D10.

## 1. Problem
People accumulate tens of thousands of photos, screenshots, renders and scans across phones, PCs, drives and cloud accounts. The information inside them (text, code, contacts, design references) is effectively unsearchable. ImageVault indexes them non-destructively, extracts what they contain, and makes it retrievable from any device.

## 2. Users and roles
| Persona | Primary need |
|---|---|
| Knowledge worker / developer | Find the screenshot with *that* error, command, or phone number |
| Architect / interior designer | Reference boards by style, material, element; "find similar" on a region |
| Family archivist | Timeline, events, people (opt-in), shared family albums |
| Small team (Phase 9) | Shared workspace with permissions |

Workspace roles: **owner, admin, editor, viewer** (enforced by RLS and API). Share links grant view/edit on a single collection.

## 3. Product principles (from spec, binding)
1. Originals are never modified or deleted automatically — including at cloud providers.
2. Three provenance levels on every derived fact: *source*, *AI*, *user-confirmed*. AI never overwrites user-confirmed data and never re-adds a tag the user rejected.
3. AI output is labelled as AI output in UI, search results and exports.
4. Person identity is only ever assigned by an explicit user action.
5. Every extracted fact links back to its asset and, where possible, its text region.
6. No feature counts as done because its screen renders; no mock data stands in for a real integration.

## 4. Scope by phase (spec §26, with proposed re-cut)

The spec puts "local indexing" in Phase 1 but the desktop app in Phase 4, and a web browser cannot index local folders. **Recommended (D1):** Phase 1 = web + cloud only; local-folder indexing moves to Phase 4 with the desktop agent. Everything below assumes that cut.

| Phase | Deliverable (user-visible) |
|---|---|
| **1 Foundation** | Sign-up/sign-in (email, Google), personal workspace, browser upload (batch, resumable), exact-duplicate prevention, EXIF/metadata, thumbnails, virtualized gallery (grid, list), asset detail, tags, manual collections, basic filters, processing-queue view, audit log, data export |
| 2 Knowledge | OCR with text boxes, entity extraction, Knowledge Cards (edit, copy, Markdown/PDF export), full-text + exact text search, screenshot classifier |
| 3 Semantic | CLIP embeddings, natural-language + similar-image search, hybrid ranking, filter builder (AND/OR/NOT), Smart Collections, pHash duplicates + comparison screen |
| 4 Desktop | Tauri app, folder/drive/NAS indexing, watcher, offline browse/search, local OCR/embeddings, sync |
| 5 Mobile | Expo apps, camera/gallery import, share sheet, background upload, offline cache, doc/card scanning |
| 6 Connectors | Google Drive, OneDrive, Dropbox, S3/R2 adapters with incremental sync; Google Photos via Picker only (see §9) |
| 7 Vision | Opt-in face clustering, object/region search, architecture classifiers |
| 8 Graph & MCP | Knowledge graph UI, MCP server (read-only first), Obsidian/Notion/n8n/webhooks |
| 9 SaaS | Organizations, team workspaces, plans, billing, entitlements |

## 5. Phase 1 functional requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| F1.1 | Email + Google OAuth sign-in via Supabase Auth | New user lands in an auto-created personal workspace; session refresh works; sign-out revokes refresh token |
| F1.2 | Upload images (JPEG, PNG, WebP, HEIC, GIF, TIFF) **[proposed list]** up to 200 MB each **[proposed]** | Batch of 500 files completes; closing the tab mid-upload and returning resumes without re-sending completed files |
| F1.3 | File-type verification by magic bytes, not extension | A `.jpg` that is really an executable is rejected and logged |
| F1.4 | Exact-duplicate prevention (SHA-256 per workspace) | Uploading the same file twice yields one asset and a second *location/import record*, not two assets |
| F1.5 | Metadata extraction: dimensions, format, size, dates, EXIF, camera, GPS, orientation, colour profile, SHA-256, pHash | Values match ExifTool output on a 50-file reference set; missing fields are null, not guessed |
| F1.6 | Thumbnails (256, 512) and preview (1600 px) | Generated for every supported format incl. HEIC; corrupt files marked `failed` with reason, upload not lost |
| F1.7 | Gallery: virtualized grid + list, sort by taken/imported date, infinite scroll | See NFR performance targets |
| F1.8 | Asset detail: preview, metadata panel (source vs user fields separated), notes, tags, collections, locations | User edits are saved with provenance `user` |
| F1.9 | Tags: add/remove/bulk-tag | Bulk-tag 1,000 selected assets in one request |
| F1.10 | Manual collections, nested | Add/remove/reorder; deleting a collection never deletes assets |
| F1.11 | Filters: kind, date range, format, size, dimensions, tags, collection, favourite, reviewed | Combined filters return correct counts against a seeded dataset |
| F1.12 | Processing queue view | Shows per-asset stage status, errors, retry button |
| F1.13 | Hide/soft-delete inside ImageVault (never touches originals) | Reversible for 30 days **[proposed]**; logged in audit_logs |
| F1.14 | Export my data (metadata JSON + originals I uploaded) | Export of 1,000 assets completes and re-imports cleanly (round-trip test) |
| F1.15 | Delete my account and all data | All rows and stored objects removed; verified by post-deletion query |

## 6. Later-phase requirements (summary; detailed PRDs written per phase before coding)
Spec §§5–21 are adopted as written. Additions **[proposed]**:
- **Notes** as a first-class table (spec lists "user notes" on cards but no table).
- **Devices** registry for sync and token revocation.
- **Consents** table for face processing and cloud-AI processing (legal basis, revocable, purge on revoke).
- **Text regions** (OCR boxes) so facts link to a region (spec §6 requirement).
- **Embedding model/version** recorded per vector so models can be swapped without corrupting search.
- **Video**: Phase 1–3 store video as an asset with poster frame and metadata only; no frame-level analysis until a video PRD is approved (D8).

## 7. Non-functional requirements — proposed targets (D7)
The spec requires measurable targets but sets none. Proposed, to be validated on a seeded 100k-asset dataset:

| Metric | Target @10k assets | Target @100k | Notes |
|---|---|---|---|
| Gallery first page (60 thumbs) API p95 | 150 ms | 250 ms | Keyset pagination, no OFFSET |
| Keyword/FTS search p95 | 300 ms | 600 ms | |
| Vector search (HNSW) p95 | 200 ms | 400 ms | ef_search tuned for recall ≥ 0.95 vs exact |
| Hybrid search p95 | 500 ms | 900 ms | |
| Thumbnail ready after upload (cloud, Phase 1) p95 | 10 s | 10 s | per image ≤ 25 MP |
| Browser memory, scrolling 100k assets | < 500 MB | < 500 MB | virtualized grid |
| Upload resume | 100% of completed chunks reused | | |
| Availability | Supabase/host SLA; no own SLA in Phase 1 | | |
1M assets is a design target only: partitioning by workspace and moving vectors to a dedicated index would be evaluated then.

Security: RLS on every tenant table; server-side authorization in the API; private bucket + short-lived signed URLs; OAuth tokens in Supabase Vault (verify availability on chosen plan); rate limits per user; sensitive entities (phones, emails, addresses) excluded from logs.

## 8. Out of scope for Phase 1
OCR, AI descriptions, semantic search, faces, connectors, desktop, mobile, MCP, billing, sharing links, knowledge graph, browser extension.

## 9. Known external constraints (verified 2026-11-25)
- **Google Photos:** since 31 Mar 2025 the Library API only lists/reads media created by the calling app; reading a user's existing library requires the **Picker API** (user selects items each session). A "sync my whole Google Photos library" feature is not possible with official APIs. Source: developers.google.com/photos/support/updates.
- **iOS/Android background execution:** continuous background sync is not guaranteed; OS schedules it. Design for opportunistic sync + foreground catch-up (details in doc 05).
- **Face models:** InsightFace pretrained models are licensed for non-commercial research only (insightface.ai, GitHub README). A commercial-safe face model must be selected before Phase 7 (D6).
