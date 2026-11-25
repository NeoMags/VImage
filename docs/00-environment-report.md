# 00 · Development environment report

Inspected 2026-11-25 in the cloud build container used for this planning work (not Neo's PC).

| Tool | Found | Notes |
|---|---|---|
| Node.js | 22.23.5 | OK for Vite/Expo tooling |
| pnpm / yarn / bun | 10.28.0 / 1.22.22 / 1.4.4 | pnpm chosen for workspaces |
| Python | 3.13.18 | **Check before Phase 2:** PaddleOCR/PaddlePaddle and some CLIP wheels may lag new Python versions. Pin worker to the newest version all wheels support (verify, likely 3.11 or 3.12). |
| uv / poetry | 0.11.32 / 2.4.3 | uv chosen for Python workspace |
| Rust / cargo | 1.97.0 | Enough for Tauri builds on Linux; Windows installer must be built on Windows (CI runner) |
| Docker | 29.9.3 | Daemon not running by default; started manually |
| PostgreSQL client | 16.15 | Server tested via `pgvector/pgvector:pg17` image (Postgres 17.12, pgvector 0.8.7) |
| gh | 2.89.0 | No GitHub repo attached to the project yet |
| Supabase CLI | **missing** | Install via `npx supabase` when a project exists |
| Expo CLI / Tauri CLI | **missing** | Installed per-package (`npx expo`, `@tauri-apps/cli`) — no global install needed |
| Hardware | 4 vCPU, 15 GiB RAM, no GPU | Fine for API/worker dev; local AI benchmarks must run on Neo's target Windows PC |

Neo's PC (from his GPU-Z screenshot and message, 2026-11-25): AMD Radeon RX 580 8 GB GDDR5 (Polaris/Ellesmere, DX12 12_0, DirectML, Vulkan, OpenCL, no CUDA), Adrenalin 26.5.2 driver for Windows 10 64-bit, 32 GB RAM. CPU model not yet known. The desktop performance targets in the PRD need that hardware profile — see Q2 in [10-decisions.md](10-decisions.md).

## What was actually executed
- [Cloud plan, now archived] Draft migration loaded into Postgres 17 + pgvector 0.8.7 without errors.
- `archive/cloud-supabase/tests/rls_isolation_test.sql` passed: sign-up creates a personal workspace; user A sees only A's assets/workspace; a direct insert into B's workspace is rejected by RLS; a face cannot be assigned a person without `confirmed_by`; CLIP HNSW and pHash (bit/Hamming HNSW) indexes are used by the planner.
- Not executed: any Supabase-hosted behaviour (Auth, Storage, Vault), any AI model, any client app.

## Update 2026-11-25 (desktop-only re-plan)
- The Postgres/pgvector results above now apply to the parked schema in `archive/cloud-supabase/`.
- Local schema `services/sidecar/db/migrations/0001_initial.sql` tested with Python 3.13 sqlite3 (SQLite 3.53.1, FTS5 on) + sqlite-vec 0.1.9: 13/13 checks passed; 100k-asset benchmark in doc 03 §4.
- Windows-specific behaviour (file ids, watcher, packaging, HEIC, DirectML) cannot be tested in this Linux container; it needs Neo's PC or a Windows CI runner (backlog B2, B22).
