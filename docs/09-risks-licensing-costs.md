# 09 · Risks, API limitations, licensing, infrastructure costs

Legend: **V** = verified from an official/primary source on 2026-11-25 · **U** = unverified, must be checked before relying on it · **E** = my estimate.

## 1. Technical risks
| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| HNSW + RLS/workspace filtering returns too few results for small tenants in a big table | Search quality | Medium | pgvector iterative scans; partition by workspace at scale; perf test in Phase 3 |
| Desktop installer size with PaddleOCR + CLIP runtimes | Adoption | High | ONNX Runtime + first-run model download (doc 06 §5) |
| Python sidecar packaging on Windows (PyInstaller/embedded Python, AV false positives) | Desktop release | Medium | Spike in Phase 4; code-sign sidecar; consider Rust-native ONNX later |
| Mobile background limits stop "auto-sync everything" | Mobile UX | High (by OS design) | Opportunistic sync + foreground catch-up; set user expectations |
| OCR quality on mixed-language / small-font screenshots | Core feature | Medium | Accuracy spike on Neo's real screenshots before committing engine; user correction UI |
| LLM-generated cards present wrong facts | Trust | Medium | Provenance labels, confidence, link every fact to OCR region, user review state |
| Sync conflicts lose user edits | Data integrity | Medium | Rules in doc 05; audit trail of losing values; conflict test matrix |
| Scope: 9 phases × 4 platforms is very large | Delivery | High | Phase gates; web+cloud first (D1); no parallel phases |
| Python 3.13 wheel availability for Paddle/Torch | Build | Medium (U) | Pin worker Python to supported version |

## 2. API limitations
| API | Limitation | Status |
|---|---|---|
| Google Photos Library API | Since 2025-03-31 only app-created media can be listed/read; full-library access removed. Use **Picker API** (user-selected items). | **V** (developers.google.com/photos/support/updates) |
| Google Drive / Gmail-class restricted scopes | Apps using restricted scopes need Google OAuth verification and possibly a third-party security assessment; `drive.file` scope avoids it but only sees files the user opens with the app | **U** — check Google's OAuth verification docs before Phase 6 |
| OneDrive (Microsoft Graph) / Dropbox | Delta/cursor APIs exist for incremental sync; rate limits and throttling headers differ per provider | **U** — per-adapter spike |
| iOS Photos | Limited-library permission; no unrestricted background access | **U** for current iOS specifics; design assumes worst case |
| Supabase Storage resumable uploads | TUS protocol supported; chunk size/limits plan-dependent | **U** |
| Supabase free tier | Projects pause after 1 week inactivity | **V** (supabase.com/pricing) |

## 3. Licensing (for a commercial product)
| Component | Licence | Concern | Status |
|---|---|---|---|
| Tauri, React, Vite, Expo, FastAPI, Celery, TanStack, Zod, shadcn/ui | MIT / Apache-2.0 / BSD | None expected | U (standard licences, confirm in SBOM) |
| OpenCLIP code + ViT-B-32 laion2b_s34b_b79k weights | MIT | LAION notes datasets are for research; weights themselves MIT | **V** (model card summary) — re-check model card at adoption |
| PaddleOCR / PaddlePaddle | Apache-2.0 | None expected | U |
| Tesseract | Apache-2.0 | None | U |
| libvips | LGPL-2.1+ | Dynamic linking OK; keep it a shared lib, ship notices | U |
| libheif / HEVC decoding | libheif LGPL; HEVC decoders may carry **patent** licensing questions | Legal check before shipping HEIC decode in desktop installer | U |
| ExifTool | Perl Artistic / GPL (dual) | Run as separate process, not linked | U |
| Redis 8 | RSALv2 / SSPLv1 / **AGPLv3** (choose one) | AGPL network clause if modified; RSAL restricts offering Redis as a service | **V** (redis.io/legal/licenses) |
| Valkey | BSD-3-Clause | None | **V** (redis.io blog comparison; confirm at valkey.io) |
| InsightFace pretrained face models | **Non-commercial research only** | Cannot use in commercial product without paid licence | **V** (GitHub README, insightface.ai) |
| sqlite-vec | MIT/Apache (expected) | Maturity for 100k+ vectors | U |
| Ollama | MIT (runtime); each model has its own licence | Check each vision model licence | U |

## 4. Infrastructure costs
**Verified unit prices (2026-11-25):**
- Supabase Pro: from **$25/month**; includes 8 GB DB disk, 100 GB storage, 250 GB egress, 100k MAU. Overage: storage **$0.0213/GB**, DB disk **$0.125/GB**, egress **$0.09/GB**. Compute add-ons listed: Micro $10, Small $15, Medium $60 (how much compute credit Pro includes — **U**). Source: supabase.com/pricing.
- Cloudflare R2 Standard: **$0.015/GB-month**, Class A $4.50/M, Class B $0.36/M, egress free, free tier 10 GB-month + 1M A + 10M B. Source: developers.cloudflare.com/r2/pricing.

**Worked example — single user, 100k assets (E):** avg original 4 MB → ~400 GB originals; derivatives ~0.3 MB/asset → ~30 GB; embeddings 100k × 512 × 4 B ≈ 0.2 GB + HNSW overhead → DB well under 8 GB.
| Option | Monthly (E, storage only) |
|---|---|
| Originals in Supabase Storage | $25 base + (430 − 100) × $0.0213 ≈ **$32** |
| Originals in R2, derivatives in Supabase | $25 + (400 − 10) × $0.015 ≈ **$31**, but R2 egress free (matters if originals are downloaded often) |
| Reference-only (originals stay on PC/Drive) | ≈ **$25** + compute |
Plus (all **U**, not priced): API/worker/Valkey host, compute add-on beyond included credit, cloud vision-LLM usage, Sentry, domain, Windows code-signing certificate, Apple Developer Program, Google Play developer account, Google OAuth security assessment (Phase 6).

**LLM cost formula (E):** `images_gated × (input_tokens + output_tokens) × provider_price`. With gating (doc 06 §1) only screenshots/documents with text get cards; a monthly budget cap per workspace bounds worst case. Prices to be pulled from the chosen provider's official page when D4 is decided.
