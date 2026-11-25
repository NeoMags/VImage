# ImageVault

Personal, offline-first Windows app that indexes your images where they already live (folders, external drives,
NAS), extracts metadata, text and visual features on your own PC, turns screenshots into searchable Knowledge
Cards, and finds things by text, meaning or visual similarity.

**Status:** planning approved in part; no application code yet. Start with [docs/README.md](docs/README.md).
**Scope (2026-11-25):** desktop only, single user, free tools only, no cloud services.

| Path | Purpose | Stack |
|---|---|---|
| `apps/desktop` | Windows app: UI + Tauri shell | Tauri 2, Rust, React, TypeScript, Vite, Tailwind, shadcn/ui |
| `services/sidecar` | Local engine: library DB, scanner/watcher, job queue, localhost API | Python, FastAPI, SQLite + FTS5 + sqlite-vec |
| `python/ivcore` | Pipeline stages and model adapters | Python, ExifTool, pyvips, PaddleOCR, OpenCLIP (ONNX) |
| `packages/ui` | React components | shadcn/ui |
| `packages/shared-types` | TS types generated from the sidecar OpenAPI | TypeScript |
| `packages/config` | Shared tsconfig/ESLint/Prettier | — |
| `archive/cloud-supabase` | Parked cloud schema for a possible future sync/multi-user version | Postgres, pgvector |
| `docs/` | PRD, architecture, schema, API, indexing, AI pipeline, UI, backlog, risks | Markdown |

Tooling: pnpm workspaces (JS), uv workspace (Python).
