"""Stage 4: thumbnails (WebP, 512 px and 256 px on the long side) and the perceptual hash.

Decode paths, by format:
- most images: libvips `thumbnail` (shrink-on-load, auto-rotate from EXIF, CMYK/16-bit → sRGB 8-bit);
- HEIC/HEIF: pillow-heif (libvips' bundled libheif cannot decode HEVC), handed to libvips in memory;
- camera RAW: the JPEG preview the camera embedded in the file, read with ExifTool;
- BMP and anything libvips rejects: Pillow;
- video: no thumbnail here (poster frames are a later item).

Files are written to <library>/thumbs/<xx>/<yy>/<uid>_<size>.webp via a temp file + rename,
so a crash never leaves a half-written thumbnail behind.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyvips

from . import StageError
from .exiftool import ExifTool
from .hashing import phash_from_grey32
from .identify import Identified

MAX_PIXELS = 500_000_000  # safety limit (proposed value, doc "B4 plan")
SIZES = ((512, "thumb_512"), (256, "thumb_256"))
WEBP_OPTS = {"Q": 80, "strip": True}
_RAW_PREVIEW_TAGS = ("JpgFromRaw", "PreviewImage", "OtherImage", "ThumbnailImage")


@dataclass
class Derivative:
    kind: str
    rel_path: str
    width: int
    height: int
    byte_size: int


@dataclass
class ThumbResult:
    width: int  # original image size, after orientation
    height: int
    phash: bytes
    derivatives: list[Derivative] = field(default_factory=list)


def shard_dir(uid: str) -> Path:
    hexuid = uid.replace("-", "")
    return Path("thumbs") / hexuid[-4:-2] / hexuid[-2:]


def _check_pixels(w: int, h: int, max_pixels: int) -> None:
    if w * h > max_pixels:
        raise StageError("too_large", f"{w}x{h} = {w * h / 1e6:.0f} MP exceeds the {max_pixels / 1e6:.0f} MP limit")


def _from_pil(im) -> pyvips.Image:
    from PIL import ImageOps

    im = ImageOps.exif_transpose(im)  # no-op when there is no orientation tag
    if im.mode not in ("RGB", "RGBA", "L", "LA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    bands = len(im.getbands())
    return pyvips.Image.new_from_memory(im.tobytes(), im.width, im.height, bands, "uchar")


def _load_pil(path: Path, max_pixels: int) -> tuple[pyvips.Image, int, int]:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    try:
        with Image.open(path) as im:
            _check_pixels(im.width, im.height, max_pixels)
            im.seek(0)
            im.load()
            img = _from_pil(im)
    except StageError:
        raise
    except Exception as e:
        raise StageError("decode_failed", f"{type(e).__name__}: {e}") from e
    return img, img.width, img.height


def _load_raw_preview(path: Path, exiftool: ExifTool, orientation: int | None) -> tuple[pyvips.Image, int, int]:
    for tag in _RAW_PREVIEW_TAGS:
        data = exiftool.read_binary_tag(path, tag)
        if data and data[:3] == b"\xff\xd8\xff":
            img = pyvips.Image.new_from_buffer(data, "", access="sequential")
            if orientation and orientation != 1:
                img = img.copy()
                img.set_type(pyvips.GValue.gint_type, "orientation", orientation)
                img = img.autorot()
            return img, img.width, img.height
    raise StageError("no_preview", "camera RAW file has no embedded JPEG preview")


def _to_srgb8(img: pyvips.Image) -> pyvips.Image:
    if img.interpretation not in ("srgb", "b-w") or img.format != "uchar":
        img = img.colourspace("srgb")
    if img.format != "uchar":
        img = img.cast("uchar")
    return img


def _phash(img: pyvips.Image) -> bytes:
    if img.hasalpha():
        img = img.flatten(background=[255])
    grey = img.colourspace("b-w")[0].thumbnail_image(32, height=32, size="force")
    pixels = np.ndarray(buffer=grey.write_to_memory(), dtype=np.uint8, shape=(grey.height, grey.width))
    return phash_from_grey32(pixels)


def _write_atomic(img: pyvips.Image, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.webp")
    img.webpsave(str(tmp), **WEBP_OPTS)
    os.replace(tmp, dest)
    return dest.stat().st_size


def make_thumbnails(
    path: Path,
    ident: Identified,
    library_dir: Path,
    uid: str,
    exiftool: ExifTool | None = None,
    orientation: int | None = None,
    max_pixels: int = MAX_PIXELS,
) -> ThumbResult | None:
    """Returns None for videos (no poster yet). Raises StageError for unreadable images."""
    if ident.kind == "video":
        return None
    if not ident.supported or ident.kind != "image":
        raise StageError("unsupported", f"no thumbnail for {ident.mime}")

    if ident.is_raw:
        if exiftool is None:
            raise StageError("no_exiftool", "ExifTool is needed for camera RAW previews")
        base, w, h = _load_raw_preview(path, exiftool, orientation)
        big = base.thumbnail_image(SIZES[0][0], height=SIZES[0][0], size="down")
    elif ident.mime in ("image/heic", "image/avif", "image/bmp"):
        base, w, h = _load_pil(path, max_pixels)
        big = base.thumbnail_image(SIZES[0][0], height=SIZES[0][0], size="down")
    else:
        try:
            header = pyvips.Image.new_from_file(str(path), access="sequential")
            hw, hh = header.width, header.height
            _check_pixels(hw, hh, max_pixels)
            big = pyvips.Image.thumbnail(str(path), SIZES[0][0], height=SIZES[0][0], size="down", fail_on="error")
            # Orientation-corrected original size: swap when the thumbnail was rotated.
            swapped = (big.width > big.height) != (hw > hh) and hw != hh
            w, h = (hh, hw) if swapped else (hw, hh)
        except StageError:
            raise
        except pyvips.Error as e:
            msg = str(e).splitlines()[0] if str(e) else "libvips error"
            if "is not a known file format" in str(e):
                base, w, h = _load_pil(path, max_pixels)
                big = base.thumbnail_image(SIZES[0][0], height=SIZES[0][0], size="down")
            else:
                raise StageError("decode_failed", msg[:300]) from e

    try:
        # Render once into memory: the source is read sequentially, and both the hash and
        # the two sizes below reuse these pixels.
        big = _to_srgb8(big).copy_memory()
        result = ThumbResult(width=w, height=h, phash=_phash(big))
        folder = library_dir / shard_dir(uid)
        hexuid = uid.replace("-", "")
        for size, kind in SIZES:
            img = big if size == SIZES[0][0] else big.thumbnail_image(size, height=size, size="down")
            dest = folder / f"{hexuid}_{size}.webp"
            n = _write_atomic(img, dest)
            result.derivatives.append(
                Derivative(kind, dest.relative_to(library_dir).as_posix(), img.width, img.height, n)
            )
    except pyvips.Error as e:  # truncated data often only fails when pixels are read
        raise StageError("decode_failed", (str(e).splitlines() or ["libvips error"])[0][:300]) from e
    return result


__all__ = ["make_thumbnails", "ThumbResult", "Derivative", "shard_dir", "MAX_PIXELS"]
