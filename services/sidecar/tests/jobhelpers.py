"""Stage functions for job-queue tests. Module-level so spawned worker processes can import them."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ivsidecar.worker import run_stage


def crash_on_bad(stage, payload, ctx):
    """Kills the worker process (like a native segfault) for files named *crash*."""
    if "crash" in Path(payload["path"]).name and stage == "identify":
        os._exit(3)
    return run_stage(stage, payload, ctx)


def flaky(stage, payload, ctx):
    """An ordinary, retryable error for files named *flaky*."""
    if "flaky" in Path(payload["path"]).name and stage == "identify":
        raise OSError("file is locked by another program")
    return run_stage(stage, payload, ctx)


def hang(stage, payload, ctx):
    if "hang" in Path(payload["path"]).name and stage == "identify":
        time.sleep(60)
    return run_stage(stage, payload, ctx)


def fake_ai(stage, payload, ctx):
    """Stands in for CLIP/RAM++ (real models are tested in test_ai_models.py): a vector and tags
    derived from the thumbnail's average colour."""
    if stage not in ("embed", "classify"):
        return run_stage(stage, payload, ctx)
    from PIL import Image, ImageStat

    with Image.open(payload["path"]) as im:
        r, g, b = ImageStat.Stat(im.convert("RGB")).mean
    if stage == "embed":
        v = [r, g, b] + [1.0] * 509
        n = sum(x * x for x in v) ** 0.5
        return {"vector": [x / n for x in v]}
    main = max((r, "red"), (g, "green"), (b, "blue"))[1]
    return {"tags": [(f"{main} thing", 0.9), ("object", 0.6)][: payload["max_tags"]]}


def fake_ocr(stage, payload, ctx):
    """Stands in for RapidOCR (real OCR is tested in test_text.py / test_ocr.py): one line naming
    the file, and a failure for files named *ocrfail*."""
    if stage != "ocr":
        return run_stage(stage, payload, ctx)
    name = Path(payload["path"]).stem
    if "ocrfail" in name:
        raise OSError("ocr engine hiccup")
    if "blank" in name:
        lines = []
    else:
        lines = [
            {"text": f"Invoice {name}", "confidence": 0.98, "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.05},
            {"text": "Call +1 555 0100 1234", "confidence": 0.9, "x": 0.1, "y": 0.3, "w": 0.6, "h": 0.05},
        ]
    return {"engine": "fake", "engine_version": "1", "confidence": 0.95 if lines else None, "lines": lines}
