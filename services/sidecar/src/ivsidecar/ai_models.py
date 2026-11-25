"""AI model files for automatic tags and search (T2): pinned, downloaded once, checked.

Files come from Hugging Face at a fixed revision and must match the SHA-256 recorded here
(measured in the T1 spike), so a changed or corrupted download is never used. They live in
`<library>/models/`, which backups skip (backups copy only the database).

Licences: CLIP ViT-B/32 ONNX (Xenova, from openai/clip-vit-base-patch32) MIT;
RAM++ ONNX (AX1Y2JP, from xinyu1205/recognize-anything-plus-model) Apache-2.0.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("ivsidecar.ai")

_CLIP = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/6b7c523703ae26e39be4b03774966f8571c7f523/"
_RAM = "https://huggingface.co/AX1Y2JP/recognize-anything-plus-model-ONNX/resolve/aa4ee9c6e449e9dd8b20580a938d24b38dd5fcfb/"


@dataclass(frozen=True)
class ModelFile:
    name: str  # path under <library>/models/
    url: str
    size: int
    sha256: str


FILES: tuple[ModelFile, ...] = (
    ModelFile(
        "clip/vision_model_quantized.onnx",
        _CLIP + "onnx/vision_model_quantized.onnx",
        89117001,
        "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
    ),
    ModelFile(
        "clip/text_model_quantized.onnx",
        _CLIP + "onnx/text_model_quantized.onnx",
        64504507,
        "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a",
    ),
    ModelFile(
        "clip/tokenizer.json",
        _CLIP + "tokenizer.json",
        2224119,
        "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd",
    ),
    ModelFile(
        "ram/vision_model_int8.onnx",
        _RAM + "onnx/vision_model_int8.onnx",
        251487727,
        "99b5419efdf8aa0415ac1b1424dcdacd34dbd250f2ba9e708abf9118edf5b967",
    ),
    ModelFile(
        "ram/tag_embeddings.npy",
        _RAM + "tag_embeddings.npy",
        478894208,
        "1beec774ad0a04341e0839bd532a7be995285e5809ed8ed6cd553d7727a68d2e",
    ),
    ModelFile(
        "ram/ram_tag_list.txt",
        _RAM + "ram_tag_list.txt",
        41904,
        "5d96d2f7225527480ad773aaac76b6e5c08a18adf6eae9ca7d2ab923d5e7bd8b",
    ),
    ModelFile(
        "ram/ram_tag_list_threshold.txt",
        _RAM + "ram_tag_list_threshold.txt",
        22016,
        "b6f81d0de1bc7f9c251af512c413eae976c77dbfbadf700d4e3d90510f4c6447",
    ),
    ModelFile(
        "ram/LICENSE", _RAM + "LICENSE", 11306, "d38f4edcfb09332fa497fd3ce52bd6c13dd73fa5acb8418a47aece5a2d311f26"
    ),
)
TOTAL_BYTES = sum(f.size for f in FILES)
CLIP_NAME, CLIP_VERSION, CLIP_DIMS = "openai:clip-vit-base-patch32:onnx-quantized", "xenova-6b7c523", 512


def models_dir(library_dir: Path) -> Path:
    override = os.environ.get("IV_MODELS_DIR")  # tests/CI share one download
    return Path(override) if override else library_dir / "models"


def _ok_marker(path: Path) -> Path:
    return path.with_name(path.name + ".ok")


def installed(library_dir: Path) -> bool:
    """True when every file is present and was verified (verification is remembered per file)."""
    d = models_dir(library_dir)
    return all(_file_ok(d / f.name, f) for f in FILES)


def _file_ok(path: Path, f: ModelFile) -> bool:
    if not path.is_file() or path.stat().st_size != f.size:
        return False
    marker = _ok_marker(path)
    if marker.is_file() and marker.read_text().strip() == f.sha256:
        return True
    if _sha256(path) == f.sha256:  # files placed by hand (or by CI) are verified once
        marker.write_text(f.sha256)
        return True
    return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class DownloadError(Exception):
    pass


Progress = Callable[[int, int], None]  # (bytes done, bytes total)
Opener = Callable[[str, int], object]  # (url, resume_from) -> response with .read(n)


def _open(url: str, start: int):
    req = urllib.request.Request(url, headers={"User-Agent": "ImageVault"})
    if start:
        req.add_header("Range", f"bytes={start}-")
    return urllib.request.urlopen(req, timeout=60)


def download(
    library_dir: Path,
    progress: Progress | None = None,
    stop: threading.Event | None = None,
    opener: Opener | None = None,
) -> None:
    """Fetch missing files, resuming partial ones; verify each SHA-256 before it is used."""
    opener = opener or _open
    d = models_dir(library_dir)
    done = sum(f.size for f in FILES if _file_ok(d / f.name, f))
    for f in FILES:
        dest = d / f.name
        if _file_ok(dest, f):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        if have > f.size:
            part.unlink()
            have = 0
        try:
            resp = opener(f.url, have)
            if have and getattr(resp, "status", 206) != 206:  # server ignored the range: start over
                have = 0
            with resp, open(part, "ab" if have else "wb") as out:
                while chunk := resp.read(1 << 20):
                    if stop is not None and stop.is_set():
                        raise DownloadError("cancelled")
                    out.write(chunk)
                    have += len(chunk)
                    if progress:
                        progress(done + have, TOTAL_BYTES)
        except DownloadError:
            raise
        except Exception as e:  # noqa: BLE001 - network errors: keep the part file, report, retry later
            raise DownloadError(f"{f.name}: {type(e).__name__}: {e}") from e
        if part.stat().st_size != f.size or _sha256(part) != f.sha256:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{f.name}: checksum mismatch, download discarded")
        os.replace(part, dest)
        _ok_marker(dest).write_text(f.sha256)
        done += f.size
        if progress:
            progress(done, TOTAL_BYTES)
        log.info("model file ready: %s", f.name)
