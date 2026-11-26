"""Worker process side of the job queue (doc 06 §3).

Each worker is a separate process that runs one job at a time and never touches the
database: it gets `(job_id, stage, payload)` over a pipe and answers with a plain-dict result.
Running one job per process means a native crash (bad image, libvips/libheif segfault) is
pinned on exactly one job, and the engine itself keeps running.

Result messages: ("ok", data) | ("fatal", "code: message")  (StageError, not retried)
               | ("retry", "error text")  (anything else, retried with backoff)
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Callable
from pathlib import Path

from .stages import StageError

StageFn = Callable[[str, dict, "WorkerContext"], dict | None]


class WorkerContext:
    """Per-process state shared across jobs: the library folder and one lazy ExifTool."""

    def __init__(self, library_dir: Path):
        self.library_dir = library_dir
        self._exiftool = None
        self._ai: dict[str, object] = {}  # loaded AI models, kept for the worker's lifetime
        self._ocr = None

    def ai_model(self, kind: str, models_dir: str, threads: int):
        """CLIP or RAM++, loaded on first use in this process (about a second each)."""
        if kind not in self._ai:
            from .stages.ai import ClipImage, RamTagger

            cls = ClipImage if kind == "embed" else RamTagger
            self._ai[kind] = cls(Path(models_dir), threads)
        return self._ai[kind]

    def ocr_engine(self, threads: int):
        """RapidOCR (K2), loaded on first use in this process and kept."""
        if self._ocr is None:
            from .stages.ocr import RapidOcrEngine

            self._ocr = RapidOcrEngine(threads=threads)
        return self._ocr

    @property
    def exiftool(self):
        if self._exiftool is None:
            from .stages.exiftool import ExifTool

            self._exiftool = ExifTool()
        return self._exiftool

    def close(self) -> None:
        if self._exiftool is not None:
            self._exiftool.close()


def run_stage(stage: str, payload: dict, ctx: WorkerContext) -> dict | None:
    """Stages as pure functions of a file. Paths come from the engine; files are only read."""
    from .stages.exiftool import ExifToolError
    from .stages.hashing import sha256_file
    from .stages.identify import identify
    from .stages.metadata import Metadata, read_metadata
    from .stages.thumbnails import make_thumbnails

    path = Path(payload["path"])
    if stage == "identify":
        return dataclasses.asdict(identify(path))
    if stage == "hash":
        ident = identify(path)
        return {"sha256": sha256_file(path), "size": path.stat().st_size, **dataclasses.asdict(ident)}
    if stage == "metadata":
        try:
            md = read_metadata(path, ctx.exiftool)
            warning = None
        except ExifToolError as e:  # plan B4: bad metadata never blocks the thumbnail
            md, warning = Metadata(), f"metadata: {e}"
        return {**dataclasses.asdict(md), "warning": warning}
    if stage == "thumbnail":
        ident = identify(path)
        kwargs = {"max_pixels": payload["max_pixels"]} if payload.get("max_pixels") else {}
        res = make_thumbnails(
            path,
            ident,
            ctx.library_dir,
            payload["uid"],
            exiftool=ctx.exiftool if ident.is_raw else None,
            orientation=payload.get("orientation"),
            **kwargs,
        )
        return None if res is None else dataclasses.asdict(res)
    if stage == "ocr":  # K2: the original (not the thumbnail), so small text stays readable
        from .stages.thumbnails import decode_for_ocr

        ident = identify(path)
        img = decode_for_ocr(
            path,
            ident,
            exiftool=ctx.exiftool if ident.is_raw else None,
            orientation=payload.get("orientation"),
            **({"max_pixels": payload["max_pixels"]} if payload.get("max_pixels") else {}),
        )
        res = ctx.ocr_engine(payload.get("threads", 0)).recognize(img)
        return {
            "engine": res.engine,
            "engine_version": res.engine_version,
            "confidence": res.confidence,
            "lines": [dataclasses.asdict(ln) for ln in res.lines],
        }
    if stage in ("embed", "classify"):  # T2: from the 512 px thumbnail, never the original
        from .stages.ai import load_rgb

        img = load_rgb(path)
        model = ctx.ai_model(stage, payload["models"], payload.get("threads", 0))
        if stage == "embed":
            return {"vector": model.embed(img).tolist()}
        return {"tags": model.tag(img, payload.get("max_tags", 8))}
    raise StageError("unknown_stage", stage)


def lower_priority() -> None:
    """Background work must not make the PC feel slow: below-normal priority on Windows, nice 5 elsewhere."""
    try:
        if sys.platform == "win32":
            import ctypes

            below_normal = 0x00004000
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), below_normal)
        else:
            os.nice(5)
    except OSError:
        pass


def worker_main(pipe, library_dir: str, vips_cache_mb: int, stage_fn: StageFn | None = None) -> None:
    lower_priority()
    try:
        import pyvips

        pyvips.cache_set_max_mem(vips_cache_mb * 1024 * 1024)
    except Exception:  # noqa: BLE001 - cache limit is best effort
        pass
    fn = stage_fn or run_stage
    ctx = WorkerContext(Path(library_dir))
    try:
        while True:
            try:
                msg = pipe.recv()
            except (EOFError, OSError):  # engine gone (even kill -9): exit with it
                break
            if msg is None:
                break
            job_id, stage, payload = msg
            try:
                reply = ("ok", fn(stage, payload, ctx))
            except StageError as e:
                reply = ("fatal", str(e)[:1000])
            except Exception as e:  # noqa: BLE001 - reported to the engine, which retries
                reply = ("retry", f"{type(e).__name__}: {e}"[:1000])
            try:
                pipe.send((job_id, *reply))
            except (BrokenPipeError, OSError):
                break
    finally:
        ctx.close()
