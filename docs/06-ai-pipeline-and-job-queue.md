# 06 · AI processing pipeline and job queue (all local, all free)

## 1. Stages
| # | Stage | Phase | Engine | Output |
|---|---|---|---|---|
| 1 | identify | D1 | magic-byte sniff (extension only to tell TIFF-based RAW formats apart) | mime, kind, raw flag; unsupported files stop here (not hashed, no asset; `file_locations.mime_type` keeps the type) |
| 2 | metadata | D1 | ExifTool (bundled exe, one persistent `-stay_open` process) | `asset_metadata`, dates, GPS, raw JSON |
| 3 | hash | D1 | SHA-256 streamed in 1 MB chunks | `sha256` (asset identity) |
| 4 | thumbnail | D1 | pyvips shrink-on-load + auto-rotate → sRGB; HEIC/AVIF/BMP via Pillow + pillow-heif (libvips' libheif has no HEVC, verified 2026-11-25); RAW via embedded JPEG preview (ExifTool) | WebP `thumb_512` + `thumb_256`; pHash (DCT on 32×32 grey of the oriented image) → `vec_phash`; `preview_1600` on demand later |
| 5 | ocr | D2 | RapidOCR 3.10 (PaddleOCR PP-OCRv6 small det/rec, ONNX Runtime, models bundled); Tesseract only if needed | `extracted_text`, `text_regions` |
| 6 | classify | T2 (built) | RAM++ int8 ONNX (AX1Y2JP, Apache-2.0) on the 512 px thumbnail, 384 px input, per-tag thresholds, generic tags dropped, up to `max_tags` (default 8). Slot 0 only, priority thumbnail+50. Rules-based categories (screenshot etc.) stay planned for D2–D3 | `asset_tags` (origin ai, confidence); user-rejected or confirmed tags are never overwritten |
| 7 | entities | D2 | regex + validators (`phonenumbers`, URL/email parsers, date parser), code/command/error heuristics | `entities` linked to `text_regions` |
| 8 | knowledge | D2 | template card from OCR + entities + category (no LLM needed) | `knowledge_cards` |
| 9 | embed | T2 (built) | OpenAI CLIP ViT-B/32 quantized ONNX (Xenova @6b7c523, MIT), 224 px centre crop, L2-normalised 512-d; registered as the active image model in `embedding_models` (replaces the planned OpenCLIP laion2b model, T1 decision) | `embeddings` + `vec_clip_b32` |
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

## 7. K1 OCR spike results (2026-11-25)
`ivsidecar --ocr-bench DIR|synthetic` (read-only) reports speed and, where `image.png.txt` holds the true text, accuracy.
| Where | Images | Accuracy | Speed (CPU) |
|---|---|---|---|
| Linux build container, 4 cores | 40 synthetic screenshots (light/dark, 12–28 px, PNG + JPEG) | CER 0.30 %, WER 0.91 %, 85 % exact | median 1.44 s/image, 0.64 images/s |
| Windows CI runner (packaged exe) | same | see the build summary | see the build summary |
| Neo's PC, real screenshots | — | not yet measured | not yet measured |
Licences verified from the wheel: RapidOCR Apache-2.0; PP-OCRv6 weights Apache-2.0 (MODEL_LICENSES.md).


## 8. AI models (T2)

Model files (about 886 MB) are downloaded once from Hugging Face at pinned revisions into `<library>/models/` (backups skip it). Each file's SHA-256 is checked before use; partial downloads resume; a mismatch is discarded. AI is off until the user downloads the models and switches it on in Settings → Automatic tags. Switching on queues every ready photo (`embed` + `classify`); switching off drops queued AI jobs only. Measured (T1, Linux CI, COCO 500): RAM++ F1 0.81 at ~2.2 s/photo on CPU, CLIP ~41 ms/photo.

**T3 (review in the UI).** Photo details list AI tags apart ("Suggested by AI", ✓ accept / × remove; a removed one never returns). The filter panel has an "AI tags" section; the sidebar has "Sorted by AI" groups (virtual: the tag filter, nothing moved). Migration 0005 adds the partial index `asset_tags_mine`. Measured, 100k photos × 5 AI tags, Linux: facets 359 ms (B15 budget 300 ms was set without tags; guard 500 ms), Sorted by AI 187 ms.

**T4 (search by description).** The engine loads the CLIP text model on the first search (`stages/ai.ClipText`) with a built-in CLIP BPE tokenizer (`stages/clip_tokenizer.py`, no `tokenizers` package: it would add huggingface-hub and an HTTP stack). Text and image vectors share CLIP's space; the query vector is matched against `vec_clip_b32` (exact, cosine). Measured: "More like this" over 100k vectors 97 ms (Linux, budget 300 ms). UI: header search box (Ctrl+K or /), results screen, "More like this" in photo details.

**T5 (teach a tag).** On a description search, "Teach a tag from this search…" lets you pick how many of the best matches count (the rest dim) and name the tag. The score of the last match you keep becomes the rule's threshold; the rule is saved in `taught_tags` (migration 0006: tag, prompt, text vector, threshold). Teaching tags every photo at or above it now (nearest 256 first; if all pass, nearest 4096; a full scan only if all 4096 pass), and the embed stage applies every rule to each newly analysed photo. Tags arrive as AI tags, so you review them like RAM++ ones; removed ones are not added back. Undo is under Settings → Automatic tags. Measured at 100k vectors (Linux): keeping the best 120 of a search 138 ms (budget 750 ms; 1500 ms Windows guard); a loose bar tagging 1,132 photos 482 ms. Large k is slow on the Windows CI runner (k = 4096 took 3.3 s), so the loose case has a 4 s Windows guard until B21 measures on Neo's PC.
