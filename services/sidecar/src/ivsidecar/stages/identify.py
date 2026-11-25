"""Stage 1: identify a file by its leading bytes (magic numbers), not its extension.

Extensions are only used to tell camera RAW formats apart, because most of them are
TIFF containers and share TIFF's magic bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

HEADER_BYTES = 64

# ftyp brands (ISO base media file format, bytes 8-12)
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_AVIF_BRANDS = {b"avif", b"avis"}
_CR3_BRANDS = {b"crx "}
_QUICKTIME_BRANDS = {b"qt  "}

# TIFF-based RAW formats, recognised by extension once the TIFF magic matches.
_TIFF_RAW_EXT = {
    ".nef": "image/x-nikon-nef",
    ".nrw": "image/x-nikon-nrw",
    ".arw": "image/x-sony-arw",
    ".sr2": "image/x-sony-sr2",
    ".dng": "image/x-adobe-dng",
    ".cr2": "image/x-canon-cr2",
    ".pef": "image/x-pentax-pef",
    ".rw2": "image/x-panasonic-rw2",
    ".orf": "image/x-olympus-orf",
    ".3fr": "image/x-hasselblad-3fr",
    ".erf": "image/x-epson-erf",
}


@dataclass(frozen=True)
class Identified:
    mime: str
    kind: str  # 'image' | 'video' | 'other'  (assets.kind values)
    is_raw: bool = False
    supported: bool = True


def identify_bytes(head: bytes, suffix: str = "") -> Identified:
    suffix = suffix.lower()
    if head.startswith(b"\xff\xd8\xff"):
        return Identified("image/jpeg", "image")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Identified("image/png", "image")
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return Identified("image/gif", "image")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Identified("image/webp", "image")
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return Identified("video/x-msvideo", "video")
    if head[:2] == b"BM" and len(head) >= 14:
        return Identified("image/bmp", "image")
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        if head[:4] == b"II*\x00" and head[8:10] == b"CR":
            return Identified("image/x-canon-cr2", "image", is_raw=True)
        if suffix in _TIFF_RAW_EXT:
            return Identified(_TIFF_RAW_EXT[suffix], "image", is_raw=True)
        return Identified("image/tiff", "image")
    if head[:4] == b"IIRO" or head[:4] == b"IIRS":  # Olympus ORF variants
        return Identified("image/x-olympus-orf", "image", is_raw=True)
    if head[:4] == b"IIU\x00":  # Panasonic RW2
        return Identified("image/x-panasonic-rw2", "image", is_raw=True)
    if head.startswith(b"FUJIFILMCCD-RAW"):
        return Identified("image/x-fuji-raf", "image", is_raw=True)
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _HEIF_BRANDS:
            return Identified("image/heic", "image")
        if brand in _AVIF_BRANDS:
            return Identified("image/avif", "image")
        if brand in _CR3_BRANDS:
            return Identified("image/x-canon-cr3", "image", is_raw=True)
        if brand in _QUICKTIME_BRANDS:
            return Identified("video/quicktime", "video")
        return Identified("video/mp4", "video")  # isom, mp41, mp42, M4V, 3gp …
    if head[:4] == b"\x1aE\xdf\xa3":
        return Identified("video/x-matroska", "video")  # also WebM
    if head.startswith(b"%PDF-"):
        return Identified("application/pdf", "other", supported=False)
    return Identified("application/octet-stream", "other", supported=False)


def identify(path: Path) -> Identified:
    with open(path, "rb") as f:
        head = f.read(HEADER_BYTES)
    return identify_bytes(head, path.suffix)
