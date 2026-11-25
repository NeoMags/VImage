# ImageVault

AI-powered image library and personal knowledge base: import visual assets, extract metadata/OCR/embeddings,
turn screenshots into Knowledge Cards, and search everything from web, Windows, Android and iOS.

**Status: Phase 1 planning. No application code yet.** Start with [docs/README.md](docs/README.md).

| Path | Purpose | Stack | Arrives in |
|---|---|---|---|
| `apps/web` | Web client | React, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query | Phase 1 |
| `apps/desktop` | Windows client (Tauri shell around shared UI) | Tauri 2, Rust, React | Phase 4 |
| `apps/mobile` | Android/iOS client | Expo (React Native), TypeScript, expo-sqlite | Phase 5 |
| `services/api` | REST + WebSocket API, auth verification, search | Python, FastAPI, Pydantic | Phase 1 |
| `services/worker` | Cloud processing pipeline (thumbnails, metadata, OCR, embeddings) | Python, Celery | Phase 1 |
| `services/desktop-agent` | Local indexer + local AI sidecar for the desktop app | Python (bundled), SQLite | Phase 4 |
| `python/ivcore` | Shared Python library: extractors, hashing, model adapters, pipeline stages | Python | Phase 1 |
| `packages/shared-types` | TS types generated from the API's OpenAPI document | TypeScript | Phase 1 |
| `packages/api-client` | Typed API client used by all three clients | TypeScript | Phase 1 |
| `packages/ui` | Shared React components (web + desktop) | React, shadcn/ui | Phase 1 |
| `packages/config` | Shared tsconfig / ESLint / Prettier | — | Phase 1 |
| `supabase/` | SQL migrations, seed, database tests | Postgres 17, pgvector | Phase 1 |
| `infra/` | Docker Compose for local dev, deployment config | Docker | Phase 1 |
| `docs/` | PRD, architecture, schema, API, sync, AI pipeline, UI, backlog, risks | Markdown | now |

Tooling: pnpm workspaces (JS), uv workspace (Python). No Turborepo/Nx until build times justify it.
