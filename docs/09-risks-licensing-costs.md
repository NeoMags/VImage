# 09 · Risks, limitations, licensing, costs (desktop-only, personal)

Legend: **V** verified from an official/primary source on 2026-11-25 · **U** unverified, check before relying on it · **E** estimate.

## 1. Costs
**$0 running cost.** No cloud services, no API keys, no subscriptions. Everything runs on your PC.
| Item | Cost | Status |
|---|---|---|
| Tauri, React, FastAPI, SQLite, sqlite-vec, PaddleOCR, Tesseract, OpenCLIP, ONNX Runtime, ExifTool, libvips, Ollama | Free / open source | U except sqlite-vec (**V**: MIT/Apache-2.0 from package metadata) |
| GitHub private repo + Actions | Free plan includes private repos and a monthly Actions minutes quota; Windows runners consume minutes faster | U — check your plan's quota |
| Code-signing certificate | Not needed for personal use (SmartScreen warning on first install) | — |
| Disk | Thumbnails ≈ 0.3 MB/asset → ~30 GB per 100k assets; models ≈ 0.3–1 GB | E |
| Google Takeout | Free | U (Google service, no API) |
No longer applicable from the first plan: Supabase ($25+/mo), R2, cloud LLM usage, API hosting, Apple/Google developer accounts, Google OAuth verification.

## 2. Technical risks
| Risk | Impact | Mitigation |
|---|---|---|
| Packaging Python + native libs (pyvips/libvips, ONNX Runtime, ExifTool) as a Tauri sidecar on Windows | Could block D1 | B2 spike first; fall back to embedded Python distribution |
| Antivirus false positives on PyInstaller exes | Annoying on own PC | Embedded Python instead of one-file exe; add AV exclusion if needed |
| sqlite-vec brute-force search beyond ~300k images | Slower similar search | Measured 90 ms @100k (container); options at scale: int8/binary quantization, prefilter, or ANN library |
| Cloud-client placeholder files getting downloaded during scans | Bandwidth/disk surprise | Skip offline-attribute files by default (verify per client) |
| NAS / exFAT lack stable file ids | Renames look like delete+add | Re-link by sha256 during reconcile |
| HEIC decoding in libvips Windows build | HEIC thumbnails missing | Verify libheif in the build; fallback `pillow-heif` |
| OCR quality on small/mixed-language text | Core D2 feature | Accuracy spike on your screenshots; editable text |
| RX 580 (Polaris) is an older GPU: DirectML speed unknown, and Ollama/ROCm GPU support likely absent | Slower AI stages | CPU path always works; measure DirectML in D2/D3 spikes; Vulkan llama.cpp for D5 |
| Large scope even desktop-only | Delivery | Strict phase gates, one phase at a time |

## 3. Licensing (personal use — all fine; noted for a possible future product)
| Component | Licence | Note | Status |
|---|---|---|---|
| sqlite-vec | MIT / Apache-2.0 | — | **V** |
| OpenCLIP ViT-B-32 laion2b weights | MIT | LAION datasets are research-oriented; weights MIT | **V** (model card summary) |
| InsightFace pretrained face models | Non-commercial research only | OK personally; replace before any commercial use | **V** |
| Redis 8 | RSALv2/SSPLv1/AGPLv3 | No longer used | **V** |
| PaddleOCR, Tesseract, ONNX Runtime | Apache-2.0 / Apache-2.0 / MIT | — | U |
| libvips | LGPL-2.1+ | ship as DLL + notice | U |
| libheif / HEVC | LGPL; HEVC patents | Personal use low concern; check before distribution | U |
| ExifTool | Artistic/GPL dual | run as separate process | U |
| Ollama models | per model | check each model's licence | U |

## 4. External limitations
| Item | Limitation | Status |
|---|---|---|
| Google Photos API | Since 2025-03-31 only app-created media or Picker selections; not used — Takeout instead | **V** |
| Google Takeout format | Not formally documented; JSON sidecar names/fields vary | U — parse defensively |
| Windows file watching | `ReadDirectoryChangesW` can overflow and doesn't see changes while the app is closed | Reconcile scans (doc 05) |
