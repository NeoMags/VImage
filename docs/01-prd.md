# 01 · Product Requirements Document — ImageVault (desktop, personal)

Scope set by decision D0 (2026-11-25): **Windows desktop app, single user, free tools, no cloud.** Items marked **[proposed]** are mine and need Neo's OK. Master spec requirements that depend on cloud, multiple users or mobile are deferred (see [10-decisions.md](10-decisions.md)).

## 1. Problem
Tens of thousands of photos, screenshots, renders, drawings and scans are spread across folders, external drives and a NAS. What's *inside* them — text, code, contacts, design references — can't be searched. ImageVault indexes them in place, without modifying them, extracts what they contain on the local PC, and makes everything findable offline.

## 2. User
Neo, on his own Windows 10/11 PC. No accounts, no sign-in, no sharing. (Multi-user/cloud is a possible future, not a requirement.)

## 3. Principles (from spec, binding)
1. Original files are never modified, moved or deleted. ImageVault writes only to its own library folder.
2. Every derived fact has a provenance — *source*, *AI* or *user* — and AI never overwrites a user value or re-adds a tag the user rejected.
3. AI output is labelled as AI output everywhere, including search results and exports.
4. A person's identity is only ever assigned by an explicit user action.
5. Every extracted fact links back to its image and, where possible, the text region it came from.
6. Works fully offline. No network calls except optional model downloads and an optional local Ollama.
7. No placeholder screens; a feature is done only when it works on real files.

## 4. Phases (desktop re-cut of spec §26)
| Phase | Deliverable | Spec phases covered |
|---|---|---|
| **D1 Foundation** | Tauri app + sidecar; add folders, external drives, NAS shares; full scan + live watching + reconcile; metadata, hashes, thumbnails; gallery (grid, list, timeline); asset detail; tags; collections; basic filters; processing queue with pause/resume and CPU/RAM limits; hide; open in Explorer; library backup/export | 1, 4 |
| D2 Knowledge | OCR with text boxes; entity extraction (URLs, emails, phones, commands, errors); screenshot classification; Knowledge Cards (template-based summary from OCR, editable); full-text + exact-text search; Markdown/PDF export | 2 |
| D3 Semantic | CLIP embeddings; natural-language and similar-image search; hybrid ranking; filter builder (AND/OR/NOT); Smart Collections; duplicate manager (exact, pHash, visual) | 3 |
| D4 Imports | Google Takeout import (Photos JSON sidecars), drag-and-drop, clipboard, Explorer "Send to ImageVault", tray app | 4, 6 (local equivalent) |
| D5 Local AI+ | Optional Ollama vision model for descriptions and richer cards; knowledge graph view; local read-only MCP server | 5–8 parts |
| D6 Vision | Opt-in face grouping; object/region search; architecture and design classifiers | 7 |
| Deferred | Cloud sync, web, mobile, multi-user, billing | 5, 6, 9 |

## 5. Phase D1 requirements
| ID | Requirement | Acceptance criteria |
|---|---|---|
| F1 | Add/remove sources: local folder, external drive, network share; include/exclude globs; pause a source | Removing a source removes only ImageVault's records, never files |
| F2 | Initial scan of supported files (JPEG, PNG, WebP, HEIC, GIF, TIFF, BMP **[proposed]**; video files recorded with metadata + poster only) | 100k-file folder scans to completion; resumes where it stopped after app/PC restart |
| F3 | Live watching + periodic reconcile | New screenshot appears in the gallery within 10 s **[proposed]**; renames/moves keep the same asset (tags/notes preserved) |
| F4 | Exact duplicates collapse to one asset with multiple locations | Same file in two folders = one asset, two locations listed |
| F5 | Removed/unplugged files | Unplugged drive → assets stay searchable with "offline" badge; deleted file → "missing" badge; nothing auto-removed |
| F6 | Metadata: dimensions, format, size, dates, EXIF, camera, GPS, orientation, colour profile, SHA-256, pHash | Matches ExifTool on a 50-file fixture set; missing values stay empty, not guessed |
| F7 | Thumbnails 256/512 + preview 1600 in the library cache | HEIC supported; corrupt files show "failed: reason", retry available |
| F8 | Gallery: virtualized grid, list, timeline; sort by taken/indexed date | See NFRs |
| F9 | Asset detail: preview, source metadata vs your edits, notes, tags, collections, all locations, "Open in Explorer" | User edits stored as origin `user` |
| F10 | Tags + bulk tagging; manual nested collections | Bulk-tag 1,000 assets in one action |
| F11 | Filters: kind, date range, format, size, dimensions, source, tag, collection, favourite, reviewed, status | Counts correct on fixture library |
| F12 | Processing queue: status per stage, errors, retry, pause/resume, CPU threads / RAM cap / "only when idle" | Settings take effect without restart; queue survives reboot |
| F13 | Hide from library (reversible) with audit log | Never touches the file |
| F14 | Library backup (DB snapshot) and export (JSON + CSV of metadata/tags/notes) | Restore from backup on a clean install reproduces tags/notes/collections |

## 6. Non-functional targets **[proposed]**, measured on Neo's PC (Q2)
| Metric | Target @100k assets |
|---|---|
| App cold start to usable gallery | < 3 s |
| Gallery page (60 thumbs) query | < 50 ms (measured 1 ms in container) |
| Full-text search | < 200 ms |
| CLIP similar/semantic search | < 300 ms (measured ≈ 90 ms median brute-force in container; re-measure on PC) |
| Indexing throughput, stages 1–4 | ≥ 5 images/s on 4 cores for ≤ 12 MP JPEG (estimate, to measure) |
| UI memory while scrolling 100k | < 500 MB |
| Sidecar idle CPU | ~0% when queue empty |
1M assets is a stretch target: brute-force vector search would be ~10× slower, so an ANN index (or sqlite-vec's planned ANN support, unverified) would be needed then.

## 7. Out of scope (for now)
Accounts/sign-in, cloud storage, cloud AI, sync across devices, web app, mobile apps, sharing, billing, Google Photos API, Drive/OneDrive/Dropbox APIs (files already synced to disk by those clients *can* be indexed as ordinary folders).
