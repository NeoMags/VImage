# 10 · Decisions

## Decided
| ID | Decision | Date | Effect |
|---|---|---|---|
| D0 | **Desktop-only (Windows), single user (Neo), free tools only, no paid or commercial cloud services or storage.** Scale to others later, if ever. | 2026-11-25 | Replaces D1 below. Supabase, Postgres, pgvector, Redis/Celery, R2, cloud AI, web and mobile are out of scope. |
| D1 | ~~Web + cloud first~~ — superseded by D0 the same day | 2026-11-25 | — |
| D2 | Originals stay where they are; ImageVault stores only its own database, thumbnails and previews | 2026-11-25 (follows from D0) | No copies of user files; nothing uploaded |
| D3 | Local store: SQLite + FTS5 + sqlite-vec | 2026-11-25 (follows from D0) | Tested: 13/13 schema checks; 100k CLIP vectors KNN ≈ 90 ms median in the build container (doc 03) |
| D4 | No cloud AI. Optional local vision LLM through Ollama (free) in phase D5 | 2026-11-25 (follows from D0) | No AI running costs |
| D5 | No Redis/Celery: persistent job queue in SQLite inside the sidecar | 2026-11-25 (follows from D0) | One process, nothing extra to install |
| D6 | Google Photos via **Google Takeout export** (free), not the Google Photos API | 2026-11-25 (follows from D0) | No Google Cloud project or OAuth app needed |

## Deviations from the master spec (flagged, accepted by D0)
The spec names Supabase, PostgreSQL, pgvector, RLS, Supabase Auth, Redis, Celery, S3/R2, cloud connectors, web and mobile apps, subscriptions and multi-tenant SaaS. None of these are needed for a single-user offline Windows app, and several have running costs. They are parked, not deleted: the tested Postgres schema is in `archive/cloud-supabase/` and maps table-for-table to the SQLite schema. FastAPI, React/TypeScript, Tauri, OpenCLIP, PaddleOCR, Tesseract, Pillow, libvips, ExifTool, SHA-256 and pHash stay as specified.

## Open (none block starting Phase D1)
| ID | Question | Recommendation | Needed by |
|---|---|---|---|
| Q1 | GitHub repo name (`imagevault` or `vimage`) — please create it empty and private | `imagevault` | Now (push) |
| Q2 | Your PC: Windows version, CPU, RAM, GPU (make/model), and roughly how many images and how many GB you want indexed | — | Before D2 (OCR/CLIP sizing); helps D1 perf targets |
| Q3 | Where the library database + thumbnails live | `%LOCALAPPDATA%\ImageVault` with an option to move it to another drive | D1 |
| Q4 | Face grouping (phase D6): InsightFace models are licensed non-commercial; fine for personal use, but must be replaced if this ever becomes a product | Use only if you still want faces at D6 | D6 |
| Q5 | Video | Metadata + poster frame only | D1 |
