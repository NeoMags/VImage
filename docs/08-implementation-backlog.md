# 08 · Implementation backlog (desktop-only)

Priority **P0** = D1 can't ship without it · **P1** = should have · Size S ≤ 1 day, M 2–4 days, L ≥ 1 week (rough). Done = tests pass on real files, not mocks.

## Phase D1 — Foundation
| ID | Item | Pri | Size | Depends on | Done when |
|---|---|---|---|---|---|
| B1 | GitHub repo + push skeleton + CI (ubuntu: lint/type/test incl. DB tests) | P0 | S | Q1 | CI green |
| B2 | **Spike:** package a FastAPI + pyvips + ExifTool sidecar as a Windows exe (PyInstaller vs embedded Python) and launch it from a Tauri 2 hello-world; measure installer size and cold start | P0 | M | B1 | Installer runs on Neo's PC; sizes/times recorded |
| B3 | Sidecar core: config, library folder, migrations runner + pre-migration backup, WAL, integrity check, `/health`, token auth, OpenAPI | P0 | M | B2 | Unit + API tests |
| B4 | `ivcore` stages 1–4 (identify, metadata, hash/pHash, thumbnails) + 50-file fixture set (HEIC, huge, corrupt, no EXIF, CMYK, animated GIF, video) | P0 | L | B3 | Fixture expectations vs ExifTool pass |
| B5 | Job queue: enqueue/claim/retry/backoff/crash recovery, process pool, priority, pause, resource settings | P0 | M | B3 | Kill -9 mid-batch → all assets reach `ready` |
| B6 | Sources + scanner: file ids, resumable full scan, exclusions, placeholder-file skip | P0 | L | B5 | 100k-file scan resumes after kill |
| B7 | Watcher + debouncer + reconciler; move/rename/missing/unreachable rules (doc 05 §3) | P0 | L | B6 | Rename storm + drive-unplug tests pass |
| B8 | API: assets list/detail/patch/hide, bulk, tags, collections, notes, jobs, stats, WS events | P0 | L | B5 | API tests |
| B9 | `shared-types` generation + TanStack Query hooks | P0 | S | B8 | tsc fails on contract drift |
| B10 | Tauri shell: sidecar lifecycle/restart, port/token handoff, folder picker, reveal in Explorer, asset protocol scope limited to cache dir | P0 | M | B2 | Manual + Rust unit tests |
| B11 | UI shell: layout, theme, routing, empty/error/loading states | P0 | M | B9, B10 | |
| B12 | Virtualized gallery (grid/list/timeline, date headers, keyset scroll), selection + bulk bar | P0 | L | B11 | 100k assets: < 500 MB RAM, smooth scroll on Neo's PC |
| B13 | Asset detail panel (provenance sections, locations, notes) | P0 | M | B12 | |
| B14 | Sources screen + add-folder dialog | P0 | M | B11 | |
| B15 | Filters (D1 set) | P1 | M | B12 | Counts match fixture library |
| B16 | Collections UI | P1 | M | B12 | |
| B17 | Processing queue UI + resource controls | P1 | S | B5, B11 | |
| B18 | Dashboard | P1 | S | B8 | |
| B19 | Backup/restore + JSON/CSV export | P0 | M | B3 | Restore on clean install reproduces tags/notes/collections |
| B20 | Read-only guarantee test: full pipeline over a read-only fixture folder; no write/delete/rename syscalls on sources | P0 | S | B7 | |
| B21 | Perf run on Neo's PC with his real folders (opt-in) against PRD §6 | P1 | S | B12 | Report |
| B22 | Windows build workflow → unsigned installer artifact | P1 | S | B10 | Installer downloadable from CI |

Critical path: B1 → B2 → B3 → B5 → B6 → B7 → B8 → B12 → B22. B2 is the biggest unknown and goes first.

## Later phases (epics; detailed backlog written before each phase)
| Epic | Phase | Spike first |
|---|---|---|
| OCR (PaddleOCR ONNX + Tesseract), text regions, entities, screenshot classifier, template Knowledge Cards, FTS/exact search, MD/PDF export | D2 | OCR accuracy on ~200 of Neo's real screenshots; ONNX parity |
| CLIP ONNX embeddings, semantic + similar + hybrid search, filter builder, Smart Collections, duplicate manager | D3 | DirectML GPU vs CPU speed |
| Google Takeout import, drag-drop/clipboard, Explorer "Send to", tray | D4 | Takeout JSON variants |
| Ollama descriptions, knowledge graph, local MCP server | D5 | Which free vision model runs acceptably on Neo's GPU |
| Faces (opt-in), object/region search, architecture classifiers | D6 | Face model licence (Q4) |
