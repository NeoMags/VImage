# 06 · AI processing pipeline and job queue

## 1. Stages (spec §17, staged so expensive models run only when justified)
| # | Stage | Phase | Engine (adapter default) | Runs on | Output |
|---|---|---|---|---|---|
| 1 | identify | 1 | libmagic-style sniff + Pillow open | cloud / device | mime, kind guess, corrupt flag |
| 2 | metadata | 1 | ExifTool (subprocess) + Pillow | cloud / device | `asset_metadata`, dates, GPS, orientation |
| 3 | hash | 1 | SHA-256 (stream) + pHash (`imagehash` or own DCT) | cloud / device | `sha256`, `phash` |
| 4 | thumbnail | 1 | pyvips (libvips) incl. HEIC via libheif — verify HEIC build licensing/codec availability | cloud / device | `asset_derivatives` (WebP) |
| 5 | ocr | 2 | PaddleOCR; Tesseract fallback | device first, cloud fallback | `extracted_text`, `text_regions` |
| 6 | classify | 2–3 | Rules (screenshot dims, EXIF absence, UI-text density) → CLIP zero-shot over taxonomy prompts | cloud / device | `asset_categories` (origin ai, confidence) |
| 7 | embed | 3 | OpenCLIP ViT-B-32 `laion2b_s34b_b79k` (512-d) | device / cloud | `asset_embeddings` |
| 8 | entities | 2 | Regex + validators (URLs, emails, phones via `phonenumbers`, dates) → optional LLM | cloud / device | `extracted_entities` with `text_region_id` |
| 9 | describe / knowledge | 2–3 | Vision LLM via adapter (Ollama local or OpenAI-compatible cloud) — **gated** | cloud (opt-in) / device (Ollama) | `asset_descriptions`, `knowledge_cards` |
| 10 | duplicates | 3 | exact (sha) → pHash Hamming ≤ 6 **[proposed]** → CLIP cosine ≥ 0.95 **[proposed]** | cloud | `duplicate_groups` |
| 11 | faces | 7 | Opt-in only; model TBD (D6) | device preferred | `asset_regions`, `asset_faces` |

**Gating for stage 9 [proposed]:** run only if (kind = screenshot/document AND OCR chars > 40) OR user clicks "Generate card" OR asset in a collection with auto-cards on. Per-workspace monthly budget cap; stage skipped with status `skipped_budget` when exceeded. This is the main cloud-cost lever.

**Local-first reality check:** "local-first AI" is only possible on desktop (and partly mobile). On web, processing is cloud by definition. Sensitive-category screenshots (detected contacts, chats, financial) can be marked **"device-only"**: the cloud never receives OCR text or sends them to a cloud LLM; only derivatives + hashes are uploaded (spec §17 "sensitive screenshot processing").

## 2. Model adapters
```python
class OcrEngine(Protocol):      name: str; version: str
    def recognize(self, image: Image) -> OcrResult: ...           # lines with boxes + confidence
class EmbeddingModel(Protocol): model_id: int; dims: int
    def embed_images(self, imgs) -> np.ndarray: ...
    def embed_texts(self, texts) -> np.ndarray: ...
class VisionLLM(Protocol):      name: str
    def describe(self, image, ocr_text, schema) -> (dict, Usage): ...  # JSON-schema-constrained output
```
Config: `models.yaml` per environment (which adapter per stage, thresholds, budgets). Every output row records engine + version so reprocessing is targeted by `pipeline_version`.

## 3. Job queue
- **Source of truth:** `processing_jobs` table. Each stage = one row with `idempotency_key = asset:{id}:{stage}:{pipeline_version}`.
- **Transport:** Celery with **Valkey** broker (BSD-3, Redis-protocol compatible). Redis 8 is RSALv2/SSPLv1/AGPLv3 — acceptable only if Neo is fine with AGPL obligations; Valkey avoids the question (D5). Celery speaking to Valkey via the redis transport is expected to work but **unverified — test in Phase 1 spike**.
- **Flow:** API inserts job rows in the same transaction as the asset → after commit, enqueues Celery task with job id. Worker: `update … set status='running', locked_by, started_at where id=$1 and status='queued' returning *` (claim; if 0 rows → already taken/finished → ack and exit). On success writes outputs + `succeeded` + enqueues next stage(s) in one transaction.
- **Retries:** exponential backoff with jitter (`run_after`), `max_attempts` 5, then `dead` + visible in Processing Queue with error. Non-retryable errors (corrupt file, unsupported) fail immediately.
- **Reconciler (every minute):** re-enqueue `queued` jobs older than 2 min (lost broker message); reset `running` with stale `heartbeat_at` (> 5 min) back to `queued`. Makes broker loss non-fatal.
- **Priorities / queues:** `interactive` (user is looking at it: thumbnails, user-requested card), `bulk` (imports), `reprocess` (model upgrades). Separate Celery queues so a 50k import does not starve a single upload.
- **Concurrency limits:** per-workspace in-flight cap **[proposed 8]** to stop one tenant hogging workers; external LLM calls behind a token-bucket per provider.
- **Cost accounting:** `cost_usd` per job from adapter `Usage`; dashboard shows month-to-date per workspace.

## 4. What happens if…
| Event | Result |
|---|---|
| Same task delivered twice | Second claim updates 0 rows → no-op |
| Worker dies mid-stage | Heartbeat goes stale → reconciler requeues; outputs are upserts keyed by (asset, engine version) |
| Broker wiped | Reconciler re-enqueues all `queued` from Postgres |
| LLM provider down / 429 | Retry with backoff; stage 9 failure never blocks stages 1–8 |
| Malformed/corrupt image | identify marks `failed` with reason; original kept; user can retry after fixing |
| Model upgraded | New `embedding_models` row → `reprocess` queue fills new vectors → flip `is_active` → old vectors dropped later |

## 5. Bundle-size and runtime notes (desktop)
PaddlePaddle runtime + OCR models and PyTorch + CLIP weights together are large (order of hundreds of MB to >1 GB — **estimate, measure in Phase 4 spike**). Proposed: ship ONNX Runtime with ONNX exports of CLIP and PaddleOCR models (both have ONNX paths — verify accuracy parity), download models on first run with checksum verification instead of bundling in the installer.
