# 06 · AI processing pipeline and job queue (all local, all free)

## 1. Stages
| # | Stage | Phase | Engine | Output |
|---|---|---|---|---|
| 1 | identify | D1 | magic-byte sniff (extension only to tell TIFF-based RAW formats apart) | mime, kind, raw flag; unsupported files stop here (not hashed, no asset; `file_locations.mime_type` keeps the type) |
| 2 | metadata | D1 | ExifTool (bundled exe, one persistent `-stay_open` process) | `asset_metadata`, dates, GPS, raw JSON |
| 3 | hash | D1 | SHA-256 streamed in 1 MB chunks | `sha256` (asset identity) |
| 4 | thumbnail | D1 | pyvips shrink-on-load + auto-rotate → sRGB; HEIC/AVIF/BMP via Pillow + pillow-heif (libvips' libheif has no HEVC, verified 2026-11-25); RAW via embedded JPEG preview (ExifTool) | WebP `thumb_512` + `thumb_256`; pHash (DCT on 32×32 grey of the oriented image) → `vec_phash`; `preview_1600` on demand later |
| 5 | ocr | D2 | PaddleOCR (ONNX Runtime) primary; Tesseract fallback | `extracted_text`, `text_regions` |
| 6 | classify | D2–D3 | rules first (screenshot dims, no EXIF camera, UI-text density), then CLIP zero-shot vs taxonomy prompts | `asset_categories` (ai, confidence) |
| 7 | entities | D2 | regex + validators (`phonenumbers`, URL/email parsers, date parser), code/command/error heuristics | `entities` linked to `text_regions` |
| 8 | knowledge | D2 | template card from OCR + entities + category (no LLM needed) | `knowledge_cards` |
| 9 | embed | D3 | OpenCLIP ViT-B-32 `laion2b_s34b_b79k` exported to ONNX (512-d) | `embeddings` + `vec_clip_b32` |
| 10 | duplicates | D3 | sha256 (exact) → pHash Hamming ≤ 6 **[proposed]** → CLIP cosine ≥ 0.95 **[proposed]** | `duplicate_groups` |
| 11 | describe | D5, optional | Ollama vision model on localhost (user installs Ollama) | `descriptions`, richer cards |
| 12 | faces | D6, opt-in | detector + embedder (licence note Q4) | `regions`, `asset_faces` |

Stage order per file: 1 identify → 3 hash → asset created, or matched to an existing asset (exact duplicate: one more location, no further work) → 2 metadata → 4 thumbnail (asset becomes `ready`). Stages 1 and 3 run before an asset exists, so their jobs belong to the file location (`jobs.location_id`, migration 0002). Then → 5 → 6/7 → 8 → 9 → 10. Each stage is a separate job so a slow OCR never blocks thumbnails.

**Interactive priority:** when you open an asset or a folder in the UI, its pending jobs get priority 10 (vs 100 bulk), so what you look at processes first.

## 2. Model adapters
```python
class OcrEngine(Protocol):      name: str; version: str
    def recognize(self, path_or_image) -> OcrResult: ...       # lines with boxes + confidence
class EmbeddingModel(Protocol): model_id: int; dims: int
    def embed_images(self, imgs) -> np.ndarray: ...
    def embed_texts(self, texts) -> np.ndarray: ...
class VisionLLM(Protocol):      name: str                       # Ollama only in this build
    def describe(self, image, ocr_text, schema) -> dict: ...
```
Each output row stores engine + version; `pipeline_version` lets a model upgrade re-run only the affected stage.

**ONNX Runtime instead of PyTorch/PaddlePaddle runtimes [proposed]:** much smaller install and CPU-friendly; GPU via DirectML execution provider (verify for both models; see §5). Models are downloaded on first use into `models/` and SHA-256 verified, so the installer stays small. Accuracy parity of ONNX exports vs originals must be checked in the D2/D3 spikes.

## 3. Job queue (SQLite, inside the sidecar) — implemented in B5
- Table `jobs`: each job belongs to an asset **or** a file location (CHECK exactly one). Partial unique indexes on `(asset_id, stage, pipeline_version)` and `(location_id, stage, pipeline_version)` make enqueueing idempotent (`INSERT … ON CONFLICT DO NOTHING`; `requeue` resets a finished job when content changes). `file_locations.asset_id` is NULL until the file is hashed.
- Claim: `UPDATE jobs SET status='running', started_at=now WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_after <= now ORDER BY priority, run_after, id LIMIT 1) RETURNING *`.
- Workers: `threads` slot threads in the engine, each with **one worker process** (spawned, below-normal priority) that runs one job at a time and never opens the database. One job per process pins a native crash on exactly that job. libvips cache per worker = `max_ram_mb / threads / 2` (64–1024 MB). The engine writes each result, the job status and the next job in one transaction.
- Retries: backoff 30 s, 2 min, 10 min, 1 h; after `max_attempts` (5) → `dead`. `StageError` (corrupt, unsupported, too large) → `failed` at once. Worker process dies or exceeds 600 s → counts as a crash; 2 crashes → `failed`.
- Startup: `running` → `queued` (crash recovery; verified by a kill -9 test on 500 files).
- Pause/resume (persisted in `settings.queue`), "only when idle" (no input for 2 min **[proposed]**) and "only on AC" gate claiming (Windows APIs; unknown elsewhere = allowed).
- API: `GET /v1/jobs/stats`, `POST /v1/jobs/pause|resume|retry`. Live progress over WebSocket comes with B8.
- Packaged-build check: `ivsidecar --selftest-queue` runs one file through stages 1–4 in a spawned worker.

## 4. Failure handling
| Event | Result |
|---|---|
| Stage crashes the worker process (bad image, native lib segfault) | Pool replaces the process; job counts an attempt; after 2 crashes on the same asset → `failed`, no retry loop |
| Model file missing/corrupt | Stage disabled with a banner and a "Download model" action; other stages continue |
| Ollama not running (D5) | `describe` jobs `skipped`; nothing else affected |
| OCR wrong | User edits text (origin `user`); re-OCR never overwrites it |

## 5. Hardware plan for Neo's PC (RX 580 8 GB, 32 GB RAM)
| Workload | Plan | Status |
|---|---|---|
| CLIP image embeddings | ONNX Runtime with the **DirectML** execution provider (needs a DX12 GPU; RX 580 reports DX12 feature level 12_0) | U — measure images/s in the D3 spike vs CPU |
| PaddleOCR | ONNX Runtime DirectML or CPU; OCR models are small, CPU may be competitive | U — measure in D2 spike |
| Thumbnails, hashing, metadata | CPU (libvips is already fast; GPU not useful) | — |
| Local vision LLM (D5, optional) | Ollama's AMD acceleration on Windows targets ROCm-supported cards, which likely excludes Polaris (RX 580), so Ollama would run on CPU. A llama.cpp-based runtime with the **Vulkan** backend can use the RX 580. 32 GB RAM lets a small (≈3–8 B parameter, quantized) vision model run on CPU, slowly. | U — verify current Ollama AMD support list and Vulkan options in the D5 spike |
| CUDA / ROCm-only tools (e.g. PyTorch GPU builds) | Not usable on this card — avoid in the design | Fact (no CUDA per GPU-Z) |
8 GB VRAM is plenty for CLIP ViT-B/32 and OCR models; it is the limiting factor only for LLMs.

## 6. Costs
All engines are free/open source; compute is your PC. No per-image charges. Licences: see doc 09.
