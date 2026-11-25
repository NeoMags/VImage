"""Builds the stage fixture set (B4) into a folder at test time.

Generated rather than committed: no binary blobs in git, no licence questions about sample
photos, and every expectation is known exactly because we wrote the file. Real camera
files (RAW, iPhone HEIC with EXIF) still need samples from Neo's library (plan, decision 4).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pillow_heif
import pyvips
from PIL import Image, ImageCms

from ivsidecar.probes import find_exiftool


@dataclass
class Case:
    name: str
    file: str
    mime: str
    status: str = "ready"  # expected FileResult.status
    size: tuple[int, int] | None = None  # expected oriented (w, h) of the original
    error_code: str | None = None
    meta: dict = field(default_factory=dict)  # expected normalised metadata fields
    max_pixels: int | None = None
    needs: str | None = None  # 'ffmpeg' when the case needs an external tool


def scene(w: int, h: int, seed: int = 0) -> np.ndarray:
    """Smooth, non-symmetric RGB test picture; different seeds give different pictures."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    out = np.zeros((h, w, 3))
    for c in range(3):
        fx, fy, ph = rng.uniform(1, 6), rng.uniform(1, 6), rng.uniform(0, 6.28)
        out[..., c] = np.sin(x / w * fx * 3.14 + ph) * np.cos(y / h * fy * 3.14) * 0.5 + 0.5
    out[: h // 3, : w // 4] = [0.9, 0.1, 0.1]  # a block in the top-left corner shows orientation
    return (out * 255).astype(np.uint8)


def _pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr)


def _exif(path: Path, *assignments: str) -> None:
    exe = find_exiftool()
    if not exe:
        raise RuntimeError("exiftool is required to build fixtures")
    subprocess.run(
        [exe, "-q", "-charset", "filename=utf8", "-overwrite_original", "-n", *assignments, str(path)], check=True
    )


def build(dest: Path) -> list[Case]:  # noqa: PLR0915 - a flat list of fixtures reads best
    dest.mkdir(parents=True, exist_ok=True)
    cases: list[Case] = []

    def add(case: Case) -> None:
        cases.append(case)

    base = scene(600, 400, 1)

    # JPEG orientations 1-8: stored 600x400, displayed 400x600 for 5-8.
    for k in range(1, 9):
        f = dest / f"orient_{k}.jpg"
        _pil(base).save(f, quality=90)
        _exif(f, f"-Orientation={k}")
        size = (400, 600) if k >= 5 else (600, 400)
        add(Case(f"jpeg_orientation_{k}", f.name, "image/jpeg", size=size, meta={"orientation": k}))

    f = dest / "full_exif.jpg"
    _pil(scene(800, 600, 2)).save(f, quality=90)
    _exif(
        f,
        "-Make=Canon",
        "-Model=Canon EOS R6",
        "-LensModel=RF24-105mm F4 L IS USM",
        "-ISO=400",
        "-ExposureTime=0.004",
        "-FNumber=5.6",
        "-FocalLength=50",
        "-DateTimeOriginal=2024:07:14 18:30:05",
        "-OffsetTimeOriginal=+02:00",
        "-GPSLatitude=48.8584",
        "-GPSLatitudeRef=N",
        "-GPSLongitude=2.2945",
        "-GPSLongitudeRef=E",
        "-GPSAltitude=35",
        "-GPSAltitudeRef=0",
    )
    add(
        Case(
            "jpeg_full_exif",
            f.name,
            "image/jpeg",
            size=(800, 600),
            meta={
                "camera_make": "Canon",
                "camera_model": "Canon EOS R6",
                "lens": "RF24-105mm F4 L IS USM",
                "iso": 400,
                "exposure_s": 0.004,
                "f_number": 5.6,
                "focal_length_mm": 50.0,
                "taken_at": "2024-07-14T18:30:05+02:00",
                "gps_lat": 48.8584,
                "gps_lon": 2.2945,
                "gps_alt_m": 35.0,
            },
        )
    )

    f = dest / "gps_west_south.jpg"
    _pil(scene(300, 200, 3)).save(f)
    _exif(f, "-GPSLatitude=33.8688", "-GPSLatitudeRef=S", "-GPSLongitude=151.2093", "-GPSLongitudeRef=W")
    add(Case("jpeg_gps_signs", f.name, "image/jpeg", size=(300, 200), meta={"gps_lat": -33.8688, "gps_lon": -151.2093}))

    f = dest / "gps_zero.jpg"
    _pil(scene(300, 200, 4)).save(f)
    _exif(f, "-GPSLatitude=0", "-GPSLatitudeRef=N", "-GPSLongitude=0", "-GPSLongitudeRef=E")
    add(Case("jpeg_gps_zero_ignored", f.name, "image/jpeg", size=(300, 200), meta={"gps_lat": None, "gps_lon": None}))

    f = dest / "bad_date.jpg"
    _pil(scene(300, 200, 5)).save(f)
    _exif(f, "-DateTimeOriginal=0000:00:00 00:00:00")
    add(Case("jpeg_zero_date", f.name, "image/jpeg", size=(300, 200), meta={"taken_at": None}))

    f = dest / "create_date_only.jpg"
    _pil(scene(300, 200, 6)).save(f)
    _exif(f, "-CreateDate=2019:01:02 03:04:05")
    add(
        Case(
            "jpeg_create_date_fallback", f.name, "image/jpeg", size=(300, 200), meta={"taken_at": "2019-01-02T03:04:05"}
        )
    )

    f = dest / "no_exif.jpg"
    _pil(scene(640, 480, 7)).save(f)
    add(Case("jpeg_no_exif", f.name, "image/jpeg", size=(640, 480), meta={"taken_at": None, "camera_make": None}))

    f = dest / "cmyk.jpg"
    _pil(scene(400, 300, 8)).convert("CMYK").save(f, quality=90)
    add(Case("jpeg_cmyk", f.name, "image/jpeg", size=(400, 300)))

    f = dest / "grey.jpg"
    _pil(scene(400, 300, 9)).convert("L").save(f)
    add(Case("jpeg_greyscale", f.name, "image/jpeg", size=(400, 300)))

    f = dest / "progressive.jpg"
    _pil(scene(400, 300, 10)).save(f, progressive=True)
    add(Case("jpeg_progressive", f.name, "image/jpeg", size=(400, 300)))

    f = dest / "icc.jpg"
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    _pil(scene(400, 300, 11)).save(f, icc_profile=srgb.tobytes())
    add(Case("jpeg_icc_profile", f.name, "image/jpeg", size=(400, 300), meta={"color_profile": "sRGB built-in"}))

    f = dest / "tiny.jpg"
    _pil(scene(1, 1, 12)).save(f)
    add(Case("jpeg_1x1", f.name, "image/jpeg", size=(1, 1)))

    f = dest / "фото é 写真.jpg"
    _pil(scene(320, 240, 13)).save(f)
    _exif(f, "-Make=Apple")
    add(Case("jpeg_unicode_name", f.name, "image/jpeg", size=(320, 240), meta={"camera_make": "Apple"}))

    # PNG variants
    f = dest / "rgb.png"
    _pil(scene(500, 300, 14)).save(f)
    add(Case("png_rgb", f.name, "image/png", size=(500, 300)))
    f = dest / "rgba.png"
    rgba = np.dstack([scene(500, 300, 15), np.linspace(0, 255, 500 * 300).reshape(300, 500).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(f)
    add(Case("png_alpha", f.name, "image/png", size=(500, 300)))
    f = dest / "sixteen.png"
    pyvips.Image.new_from_array(scene(300, 200, 16).astype(np.uint16) * 257, interpretation="rgb16").pngsave(str(f))
    add(Case("png_16bit", f.name, "image/png", size=(300, 200)))
    f = dest / "palette.png"
    _pil(scene(300, 200, 17)).convert("P", palette=Image.Palette.ADAPTIVE).save(f)
    add(Case("png_palette", f.name, "image/png", size=(300, 200)))
    f = dest / "grey.png"
    _pil(scene(300, 200, 18)).convert("L").save(f)
    add(Case("png_greyscale", f.name, "image/png", size=(300, 200)))
    f = dest / "wide.png"
    _pil(scene(4000, 10, 19)).save(f)
    add(Case("png_extreme_aspect", f.name, "image/png", size=(4000, 10)))

    # GIF / WebP / TIFF / BMP
    frames = [_pil(scene(200, 150, 20 + i)) for i in range(3)]
    f = dest / "static.gif"
    frames[0].save(f)
    add(Case("gif_static", f.name, "image/gif", size=(200, 150)))
    f = dest / "animated.gif"
    frames[0].save(f, save_all=True, append_images=frames[1:], duration=100, loop=0)
    add(Case("gif_animated_first_frame", f.name, "image/gif", size=(200, 150)))
    f = dest / "lossy.webp"
    _pil(scene(400, 300, 24)).save(f, quality=80)
    add(Case("webp_lossy", f.name, "image/webp", size=(400, 300)))
    f = dest / "alpha.webp"
    Image.fromarray(rgba, "RGBA").save(f, lossless=True)
    add(Case("webp_lossless_alpha", f.name, "image/webp", size=(500, 300)))
    f = dest / "animated.webp"
    frames[0].save(f, save_all=True, append_images=frames[1:], duration=100)
    add(Case("webp_animated", f.name, "image/webp", size=(200, 150)))
    f = dest / "rgb.tif"
    _pil(scene(400, 300, 25)).save(f)
    add(Case("tiff_rgb", f.name, "image/tiff", size=(400, 300)))
    f = dest / "cmyk.tif"
    _pil(scene(400, 300, 26)).convert("CMYK").save(f)
    add(Case("tiff_cmyk", f.name, "image/tiff", size=(400, 300)))
    f = dest / "sixteen.tif"
    pyvips.Image.new_from_array(scene(300, 200, 27).astype(np.uint16) * 257, interpretation="rgb16").tiffsave(str(f))
    add(Case("tiff_16bit", f.name, "image/tiff", size=(300, 200)))
    f = dest / "plain.bmp"
    _pil(scene(300, 200, 28)).save(f)
    add(Case("bmp_via_pillow", f.name, "image/bmp", size=(300, 200)))

    # HEIC (pillow-heif encoder)
    pillow_heif.register_heif_opener()
    f = dest / "photo.heic"
    _pil(scene(640, 480, 29)).save(f, format="HEIF", quality=80)
    add(Case("heic", f.name, "image/heic", size=(640, 480)))
    f = dest / "alpha.heic"
    Image.fromarray(rgba, "RGBA").save(f, format="HEIF", quality=80)
    add(Case("heic_alpha", f.name, "image/heic", size=(500, 300)))

    # Wrong extensions: content decides
    f = dest / "really_jpeg.png"
    _pil(scene(300, 200, 30)).save(f, format="JPEG")
    add(Case("jpeg_named_png", f.name, "image/jpeg", size=(300, 200)))
    f = dest / "really_png.jpg"
    _pil(scene(300, 200, 31)).save(f, format="PNG")
    add(Case("png_named_jpg", f.name, "image/png", size=(300, 200)))

    # Broken and unsupported
    good = dest / "orient_1.jpg"
    data = good.read_bytes()
    f = dest / "truncated.jpg"
    f.write_bytes(data[: len(data) // 2])
    add(Case("jpeg_truncated", f.name, "image/jpeg", status="failed", error_code="decode_failed"))
    f = dest / "empty.jpg"
    f.write_bytes(b"")
    add(Case("zero_bytes", f.name, "application/octet-stream", status="unsupported"))
    f = dest / "noise.jpg"
    f.write_bytes(np.random.default_rng(1).bytes(4096))
    add(Case("random_bytes", f.name, "application/octet-stream", status="unsupported"))
    f = dest / "notes.png"
    f.write_text("not an image\n")
    add(Case("text_named_png", f.name, "application/octet-stream", status="unsupported"))
    f = dest / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    add(Case("pdf_unsupported_for_now", f.name, "application/pdf", status="unsupported"))
    f = dest / "large.jpg"
    _pil(scene(1500, 1000, 32)).save(f)
    add(Case("too_large", f.name, "image/jpeg", status="failed", error_code="too_large", max_pixels=1_000_000))
    f = dest / "raw_without_preview.dng"
    _pil(scene(300, 200, 33)).save(f, format="TIFF")
    add(Case("raw_without_preview", f.name, "image/x-adobe-dng", status="failed", error_code="no_preview"))

    # Video: identified, metadata read, no thumbnail yet
    f = dest / "header_only.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64)
    add(Case("mp4_header_only", f.name, "video/mp4"))
    f = dest / "header_only.mov"
    f.write_bytes(b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 64)
    add(Case("mov_header_only", f.name, "video/quicktime"))
    f = dest / "header_only.mkv"
    f.write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 64)
    add(Case("mkv_header_only", f.name, "video/x-matroska"))
    ffmpeg = shutil.which("ffmpeg")
    f = dest / "clip.mp4"
    if ffmpeg:
        subprocess.run(
            [
                ffmpeg,
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=10",
                "-t",
                "2",
                "-pix_fmt",
                "yuv420p",
                str(f),
            ],
            check=True,
        )
    add(Case("mp4_real_clip", f.name, "video/mp4", size=(320, 240), meta={"duration_ms": 2000}, needs="ffmpeg"))

    # Exact duplicate of another file (same bytes, different name)
    f = dest / "copy_of_no_exif.jpg"
    shutil.copyfile(dest / "no_exif.jpg", f)
    add(Case("exact_duplicate", f.name, "image/jpeg", size=(640, 480)))
    return cases
