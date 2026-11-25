# 10 · Decisions needed from Neo

Each has a recommendation; Phase 1 coding (B1+) waits on **D1–D5**. D6–D10 can wait until their phase.

| ID | Decision | Options | Recommendation | Why |
|---|---|---|---|---|
| **D1** | MVP platform cut | (a) Web + cloud first, local indexing moves to Phase 4 · (b) Keep "local indexing" in Phase 1 via an early desktop agent · (c) Build web, desktop, mobile in parallel | **(a)** | Browser cannot index local folders; (b) pulls the hardest packaging work forward; (c) contradicts spec §26 "do not implement all phases simultaneously" |
| **D2** | Where originals live | (a) Uploads stored in cloud; desktop/connector sources reference-only by default with per-source "back up originals" toggle · (b) Everything copied to cloud · (c) Metadata/thumbnails only, never originals | **(a)** | Lowest cost and least surprise; uploads need somewhere to live; avoids silently duplicating multi-TB drives |
| **D3** | Desktop local store | (a) SQLite + FTS5 + sqlite-vec · (b) SQLite + LanceDB · (c) Embedded Postgres | **(a)**, confirmed by Phase 4 spike | One file, same engine family as mobile; spike must show 100k-vector search on target PC |
| **D4** | Cloud vision-LLM | (a) None in Phase 2 (OCR + rules only), add later · (b) OpenAI-compatible provider with budget cap · (c) Self-hosted Ollama on a GPU host | **(b) with cap**, or (a) if budget is tight | Cards need summarization; cap bounds spend; provider stays swappable |
| **D5** | Queue broker | (a) Valkey (BSD) · (b) Redis 8 under AGPLv3 · (c) No broker: Postgres-only queue (SKIP LOCKED) | **(a)** | Spec names Redis+Celery; Valkey keeps the protocol without licence questions. (c) is simpler and worth considering if you prefer fewer moving parts |
| D6 | Face recognition model | Commercial-licensed model vs open model with permissive licence vs drop faces | Decide in Phase 7 | InsightFace pretrained weights are non-commercial only |
| D7 | Performance targets | Accept doc 01 §7 or adjust; also share target Windows PC specs | Accept as starting point | Spec requires targets; none given |
| D8 | Video | Metadata + poster only until a video PRD vs full pipeline | Metadata + poster only | No pipeline defined in spec; costly |
| D9 | API/worker hosting | VM with Docker Compose vs PaaS | Decide at B22 | Price not verified yet |
| D10 | Product name | "ImageVault" vs "VImage" | Your call | Spec and project name differ; affects repo, bundle IDs, domain |
