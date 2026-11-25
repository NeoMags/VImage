"""OCR spike / benchmark (K1). Read-only over the images it is pointed at.

    ivsidecar --ocr-bench DIR [--ocr-out report.json] [--ocr-limit 200] [--ocr-threads N] [--ocr-dml]
    ivsidecar --ocr-bench synthetic [--ocr-out ...]

For a folder: OCRs up to `limit` images and reports speed. Where `photo.png` has a
`photo.png.txt` (or `photo.txt`) beside it holding the true text, it also reports accuracy.
`synthetic` renders screenshot-like test images with known text into a temp folder first.

The report holds counts, timings and error rates, plus the recognised text only for images
that have a ground-truth file; it never copies or changes the images.
"""

from __future__ import annotations

import json
import os
import platform
import random
import statistics
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .stages.ocr import OcrResult, RapidOcrEngine

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _norm(s: str) -> str:
    return " ".join(s.split())


def edit_distance(a: list[str] | str, b: list[str] | str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def error_rates(truth: str, got: str) -> tuple[float, float]:
    """(character error rate, word error rate), whitespace-normalised; 0.0 = perfect."""
    t, g = _norm(truth), _norm(got)
    cer = edit_distance(t, g) / max(len(t), 1)
    tw, gw = t.split(), g.split()
    wer = edit_distance(tw, gw) / max(len(tw), 1)
    return cer, wer


def _truth_for(p: Path) -> str | None:
    for cand in (p.with_name(p.name + ".txt"), p.with_suffix(".txt")):
        if cand.is_file():
            return cand.read_text(encoding="utf-8")
    return None


# ------------------------------------------------------------------ synthetic screenshots
_SNIPPETS = [
    "Error: ModuleNotFoundError: No module named 'cv2'",
    "TypeError: Cannot read properties of undefined (reading 'map')",
    "git push -u origin feat/k1-ocr",
    "pip install rapidocr onnxruntime",
    "https://github.com/NeoMags/VImage/pull/17",
    "Contact: support@example.com",
    "Phone +1 (415) 555-0132",
    "Invoice #INV-2026-0042  Total: $1,249.50",
    "Meeting moved to Thursday 27 Nov 2026, 14:30",
    "Settings > Privacy > Location Services",
    "Your download is complete. Open folder",
    "Order shipped: tracking 1Z999AA10123456784",
    "SELECT id, name FROM assets WHERE status = 'ready';",
    "The quick brown fox jumps over the lazy dog",
    "Wi-Fi password: Tr0ub4dor&3",
    "Battery 23% - Low Power Mode",
    "def recognize(self, image: Image.Image) -> OcrResult:",
    "Unsubscribe | Manage preferences | View in browser",
    "Flight AI 101 - Gate 32B - Boarding 21:05",
    "npm ERR! code ERESOLVE unable to resolve dependency tree",
]


def _fonts() -> list[str]:
    cands = [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ]
    return [f for f in cands if os.path.isfile(f)]


def make_synthetic(out: Path, n: int = 40, seed: int = 7) -> Path:
    """Screenshot-like images (light/dark, 12-28 px text, several fonts) with .txt truth."""
    rng = random.Random(seed)
    fonts = _fonts()
    out.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        dark = i % 3 == 0
        bg, fg = ((30, 30, 34), (225, 225, 225)) if dark else ((255, 255, 255), (20, 20, 20))
        size = rng.choice([12, 14, 16, 18, 22, 28])
        font = ImageFont.truetype(rng.choice(fonts), size) if fonts else ImageFont.load_default(size=size)
        lines = rng.sample(_SNIPPETS, rng.randint(2, 6))
        img = Image.new("RGB", (rng.choice([1280, 1920]), 60 + len(lines) * (size * 2 + 10)), bg)
        d = ImageDraw.Draw(img)
        y = 30
        for ln in lines:
            d.text((40 + rng.randint(0, 60), y), ln, fill=fg, font=font)
            y += size * 2 + 10
        name = f"synthetic_{i:03d}_{'dark' if dark else 'light'}_{size}px"
        if i % 4 == 1:  # some as lossy JPEG, like shared screenshots
            img.save(out / f"{name}.jpg", quality=70)
            (out / f"{name}.jpg.txt").write_text("\n".join(lines), encoding="utf-8")
        else:
            img.save(out / f"{name}.png")
            (out / f"{name}.png.txt").write_text("\n".join(lines), encoding="utf-8")
    return out


# ------------------------------------------------------------------ run
def _images(folder: Path, limit: int) -> list[Path]:
    found = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXT and p.is_file())
    return found[:limit]


def run(target: str, out: Path | None = None, limit: int = 200, threads: int = 0, use_dml: bool = False) -> dict:
    folder = make_synthetic(Path(tempfile.mkdtemp(prefix="iv-ocrbench-"))) if target == "synthetic" else Path(target)
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {folder}")
    engine = RapidOcrEngine(threads=threads, use_dml=use_dml)
    t0 = time.perf_counter()
    engine.recognize(Image.new("RGB", (64, 32), "white"))  # load models outside the timings
    load_ms = (time.perf_counter() - t0) * 1000

    rows, times, cers, wers, failed = [], [], [], [], 0
    started = time.perf_counter()
    for p in _images(folder, limit):
        try:
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im)
                im.load()
                res: OcrResult = engine.recognize(im)
                size = im.size
        except Exception as e:  # noqa: BLE001 - a bad file is a data point, not a crash
            failed += 1
            rows.append({"file": p.name, "error": f"{type(e).__name__}: {e}"})
            continue
        times.append(res.elapsed_ms)
        row = {
            "file": p.name,
            "size": size,
            "lines": len(res.lines),
            "ms": round(res.elapsed_ms, 1),
            "confidence": None if res.confidence is None else round(res.confidence, 3),
        }
        truth = _truth_for(p)
        if truth is not None:
            cer, wer = error_rates(truth, res.full_text)
            cers.append(cer)
            wers.append(wer)
            row.update(cer=round(cer, 4), wer=round(wer, 4), text=res.full_text)
        rows.append(row)
    wall = time.perf_counter() - started

    def pct(v: list[float], q: float) -> float | None:
        return round(statistics.quantiles(v, n=100)[int(q) - 1], 1) if len(v) >= 2 else (round(v[0], 1) if v else None)

    report = {
        "engine": f"{engine.name} {engine.version}",
        "provider": "DirectML" if use_dml else "CPU",
        "threads": threads or "auto",
        "machine": {
            "os": platform.platform(),
            "cpu": platform.processor() or platform.machine(),
            "cores": os.cpu_count(),
        },
        "folder": "synthetic" if target == "synthetic" else str(folder),
        "images": len(times),
        "failed": failed,
        "model_load_ms": round(load_ms),
        "ms_per_image": {
            "median": pct(times, 50),
            "p95": pct(times, 95),
            "max": round(max(times), 1) if times else None,
        },
        "images_per_s": round(len(times) / wall, 2) if wall and times else None,
        "with_ground_truth": len(cers),
        "cer_mean": round(statistics.fmean(cers), 4) if cers else None,
        "wer_mean": round(statistics.fmean(wers), 4) if wers else None,
        "exact_text_rate": round(sum(c == 0 for c in cers) / len(cers), 3) if cers else None,
        "rows": rows,
    }
    if out:
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def summary(r: dict) -> str:
    acc = (
        f"accuracy on {r['with_ground_truth']} images with known text: CER {r['cer_mean']:.2%}, "
        f"WER {r['wer_mean']:.2%}, exact {r['exact_text_rate']:.0%}"
        if r["with_ground_truth"]
        else "accuracy: no ground-truth .txt files, speed only"
    )
    return (
        f"{r['engine']} on {r['provider']} ({r['machine']['cores']} cores): {r['images']} images, {r['failed']} failed; "
        f"median {r['ms_per_image']['median']} ms, p95 {r['ms_per_image']['p95']} ms, {r['images_per_s']} img/s; "
        f"model load {r['model_load_ms']} ms; {acc}"
    )
