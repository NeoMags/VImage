# 06 · AI processing pipeline and job queue (all local, all free)

## 1. Stages
| # | Stage | Phase | Engine | Output |
|---|---|---|---|---|
| 1 | identify | D1 | magic-byte sniff + Pillow/pyvips open | mime, kind guess, corrupt flag |
| 2 | metadata | D1 | ExifTool (bundled exe, subprocess) + Pillow | `asset_metadata`, dates, GPS |
| 3 | hash | D1 | SHA-256 streamed; pHash (DCT on 32×32 grey) | `sha256`, `phash` → `vec_phash` |
| 4 | thumbnail | D1 | pyvips (libvips, shrink-on-load; HEIC via libheif — verify Windows build includes it) | WebP 256/512/1600 in cache |
| 5 | ocr | D2 | PaddleOCR (ONNX Runtime) primary; Tesseract fallback | `extracted_text`, `text_regions` |
| 6 | classify | D2–D3 | rules first (screenshot dims, no EXIF camera, UI-text density), then CLIP zero-shot vs taxonomy prompts | `asset_categories` (ai, confidence) |
| 7 | entities | D2 | regex + validators (`phonenumbers`, URL/email parsers, date parser), code/command/error heuristics | `entities` linked to `text_regions` |
| 8 | knowledge | D2 | template card from OCR + entities + category (no LLM needed) | `knowledge_cards` |
| 9 | embed | D3 | OpenCLIP ViT-B-32 `laion2b_s34b_b79k` exported to ONNX (512-d) | `embeddings` + `vec_clip_b32` |
| 10 | duplicates | D3 | sha256 (exact) → pHash Hamming ≤ 6 **[proposed]** → CLIP cosine ≥ 0.95 **[proposed]** | `duplicate_groups` |
| 11 | describe | D5, optional | Ollama vision model on localhost (user installs Ollama) | `descriptions`, richer cards |
| 12 | faces | D6, opt-in | detector + embedder (licence note Q4) | `regions`, `asset_faces` |

Stage order per asset: 1 → 2 → 3 → 4 (asset becomes visible) → 5 → 6/7 → 8 → 9 → 10. Each stage is a separate job so a slow OCR never blocks thumbnails.

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

**ONNX Runtime instead of PyTorch/PaddlePaddle runtimes [proposed]:** much smaller install and CPU-friendly; GPU via DirectML execution provider (verify for both models). Models are downloaded on first use into `models/` and SHA-256 verified, so the installer stays small. Accuracy parity of ONNX exports vs originals must be checked in the D2/D3 spikes.

## 3. Job queue (SQLite, inside the sidecar)
- Table `jobs`; `UNIQUE (asset_id, stage, pipeline_version)` makes enqueueing idempotent (`INSERT … ON CONFLICT DO NOTHING`).
- Claim: `UPDATE jobs SET status='running', started_at=now WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_after <= now ORDER BY priority, run_after, id LIMIT 1) RETURNING *` — one writer process, so no lock contention.
- Workers: a process pool (CPU-heavy stages: OCR, CLIP, thumbnails) sized by the resource settings; results written back by the main process.
- Retries: exponential backoff (`run_after`), max 5, then `dead` (visible with error + Retry button). Non-retryable errors (corrupt, unsupported) fail immediately.
- Startup: `running` → `queued` (crash recovery).
- Pause/resume: global and per source; "only when idle / on AC" gates claiming.

## 4. Failure handling
| Event | Result |
|---|---|
| Stage crashes the worker process (bad image, native lib segfault) | Pool replaces the process; job counts an attempt; after 2 crashes on the same asset → `failed`, no retry loop |
| Model file missing/corrupt | Stage disabled with a banner and a "Download model" action; other stages continue |
| Ollama not running (D5) | `describe` jobs `skipped`; nothing else affected |
| OCR wrong | User edits text (origin `user`); re-OCR never overwrites it |

## 5. Costs
All engines are free/open source; compute is your PC. No per-image charges. Licences: see doc 09.
