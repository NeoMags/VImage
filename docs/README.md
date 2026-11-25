# ImageVault documentation — desktop-only plan (revised 2026-11-25)

Scope: Windows desktop, single user, free tools, no cloud (decision D0).

| # | Document | Covers |
|---|---|---|
| 00 | [Environment report](00-environment-report.md) | tools inspected, what was executed |
| 01 | [PRD](01-prd.md) | requirements, phases D1–D6, targets |
| 02 | [Architecture](02-architecture.md) | Tauri + Python sidecar + SQLite, diagrams |
| 03 | [Local schema & ERD](03-database-schema.md) + [migration](../services/sidecar/src/ivsidecar/migrations/0001_initial.sql) | tested, with 100k benchmark |
| 04 | [API contracts](04-api-contracts.md) | UI ↔ sidecar |
| 05 | [Local indexing](05-local-indexing-and-sync.md) | scan, watch, reconcile; why no sync |
| 06 | [AI pipeline & job queue](06-ai-pipeline-and-job-queue.md) | local stages, SQLite queue |
| 07 | [UI & wireframes](07-ui-navigation-and-wireframes.md) | desktop screens |
| 08 | [Backlog](08-implementation-backlog.md) | D1 items B1–B22 + later epics |
| 09 | [Risks, licensing, costs](09-risks-licensing-costs.md) | $0 running cost |
| 10 | [Decisions](10-decisions.md) | decided + open questions |

The first (cloud) plan's schema is parked in `archive/cloud-supabase/`.
