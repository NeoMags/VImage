"""Stage 5 engine (doc 06 §1-2): text with line boxes from an image, all on this PC.

`RapidOcrEngine` runs the PaddleOCR PP-OCR models through ONNX Runtime via RapidOCR
(Apache-2.0; model weights Apache-2.0, see the package's MODEL_LICENSES.md). The models
ship inside the package, so nothing is downloaded. This module only reads the image it
is given; persisting results is the job queue's work (K2).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Protocol

import numpy as np
from PIL import Image

from . import StageError


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    # Axis-aligned box as fractions of the image's width/height (0..1), so it maps onto any
    # thumbnail or preview size.
    x: float
    y: float
    w: float
    h: float


@dataclass
class OcrResult:
    engine: str
    engine_version: str
    lines: list[OcrLine] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def full_text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)

    @property
    def confidence(self) -> float | None:
        """Mean line confidence weighted by text length; None when nothing was found."""
        total = sum(len(ln.text) for ln in self.lines)
        if not total:
            return None
        return sum(ln.confidence * len(ln.text) for ln in self.lines) / total


class OcrEngine(Protocol):
    name: str
    version: str

    def recognize(self, image: Image.Image) -> OcrResult: ...


def reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Top-to-bottom, then left-to-right for boxes on the same visual row."""
    rows: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda b: b.y + b.h / 2):
        mid = ln.y + ln.h / 2
        if rows:
            last = rows[-1]
            ref = sum(b.y + b.h / 2 for b in last) / len(last)
            if abs(mid - ref) < min(ln.h, min(b.h for b in last)) * 0.5:
                last.append(ln)
                continue
        rows.append([ln])
    return [b for row in rows for b in sorted(row, key=lambda b: b.x)]


class RapidOcrEngine:
    """Thread-safe wrapper; the ONNX sessions load on first use (about a second)."""

    name = "rapidocr"

    def __init__(self, threads: int = 0, use_dml: bool = False, min_score: float = 0.5):
        self.version = version("rapidocr")
        self._params = {
            "Global.log_level": "warning",
            "Global.text_score": min_score,
            "EngineConfig.onnxruntime.intra_op_num_threads": threads if threads > 0 else -1,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_dml": use_dml,
        }
        self._engine = None
        self._lock = threading.Lock()

    def _get(self):
        if self._engine is None:
            from rapidocr import RapidOCR  # heavy import, only when OCR actually runs

            self._engine = RapidOCR(params=self._params)
        return self._engine

    def recognize(self, image: Image.Image) -> OcrResult:
        rgb = image.convert("RGB")
        width, height = rgb.size
        if width < 8 or height < 8:
            return OcrResult(self.name, self.version)
        bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])
        t0 = time.perf_counter()
        try:
            with self._lock:  # one ONNX session set; the job queue parallelises across processes
                out = self._get()(bgr)
        except Exception as e:  # native/model failure on this image: report, don't crash the worker
            raise StageError("ocr_failed", f"{type(e).__name__}: {e}") from e
        elapsed = (time.perf_counter() - t0) * 1000
        lines: list[OcrLine] = []
        if out.boxes is not None and out.txts:
            for box, text, score in zip(out.boxes, out.txts, out.scores, strict=True):
                text = text.strip()
                if not text:
                    continue
                pts = np.asarray(box, dtype=float)
                x0, y0 = max(pts[:, 0].min(), 0.0), max(pts[:, 1].min(), 0.0)
                x1, y1 = min(pts[:, 0].max(), width), min(pts[:, 1].max(), height)
                lines.append(
                    OcrLine(
                        text=text,
                        confidence=round(float(score), 4),
                        x=round(x0 / width, 5),
                        y=round(y0 / height, 5),
                        w=round(max(x1 - x0, 0.0) / width, 5),
                        h=round(max(y1 - y0, 0.0) / height, 5),
                    )
                )
        return OcrResult(self.name, self.version, reading_order(lines), elapsed)
