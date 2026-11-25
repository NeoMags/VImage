"""Runs stages 1-4 on one file in order. Used by tests and the CLI check; the job queue
(B5) calls the stages individually so each can be retried on its own."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import StageError
from .exiftool import ExifTool, ExifToolError
from .hashing import sha256_file
from .identify import Identified, identify
from .metadata import Metadata, read_metadata
from .thumbnails import ThumbResult, make_thumbnails


@dataclass
class FileResult:
    ident: Identified
    sha256: bytes | None = None
    metadata: Metadata | None = None
    thumbs: ThumbResult | None = None
    status: str = "ready"  # assets.status
    error: str | None = None


def process_file(
    path: Path, library_dir: Path, uid: str, exiftool: ExifTool, max_pixels: int | None = None
) -> FileResult:
    ident = identify(path)
    res = FileResult(ident=ident)
    if not ident.supported:
        res.status, res.error = "unsupported", f"unsupported: {ident.mime}"
        return res
    res.sha256 = sha256_file(path)
    try:
        res.metadata = read_metadata(path, exiftool)
    except ExifToolError as e:  # metadata is optional; thumbnails can still be made
        res.metadata = Metadata()
        res.error = f"metadata: {e}"
    kwargs = {"max_pixels": max_pixels} if max_pixels else {}
    try:
        res.thumbs = make_thumbnails(
            path, ident, library_dir, uid, exiftool=exiftool, orientation=res.metadata.orientation, **kwargs
        )
    except StageError as e:
        res.status, res.error = "failed", str(e)
    return res
