"""Capability probes reported by /v1/health. The B2 spike uses these to prove that the
packaged Windows build can load every native dependency."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def sqlite_info(conn: sqlite3.Connection) -> dict:
    return {
        "sqlite": sqlite3.sqlite_version,
        "sqlite_vec": conn.execute("select vec_version()").fetchone()[0],
        "fts5": bool(conn.execute("select sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]),
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
    }


def vips_info() -> dict:
    try:
        import pyvips
    except Exception as exc:  # native library missing or broken
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    info: dict = {
        "ok": True,
        "libvips": pyvips.version(0) and f"{pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)}",
    }
    for loader in ("jpegload", "pngload", "webpload", "tiffload", "gifload", "heifload"):
        info[loader] = pyvips.type_find("VipsOperation", loader) != 0
    t = time.perf_counter()
    try:
        img = pyvips.Image.black(4000, 3000, bands=3) + 128  # 12 MP synthetic image
        thumb = img.thumbnail_image(256)
        buf = thumb.write_to_buffer(".webp[Q=80]")
        info["thumbnail_selftest"] = {"ok": True, "bytes": len(buf), "ms": round((time.perf_counter() - t) * 1000, 1)}
    except Exception as exc:
        info["thumbnail_selftest"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # heifload exists in builds that only decode AVIF; an HEVC round trip shows whether
    # iPhone-style HEIC can be handled (encoder and decoder ship together in libheif builds).
    try:
        small = pyvips.Image.black(64, 64, bands=3) + 50
        buf = small.heifsave_buffer(compression="hevc")
        info["heic_hevc_roundtrip"] = pyvips.Image.new_from_buffer(buf, "").width == 64
    except Exception as exc:
        info["heic_hevc_roundtrip"] = False
        info["heic_hevc_error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    return info


def heif_info() -> dict:
    """HEIC/HEIF decoding through pillow-heif (libheif + libde265).

    libvips' bundled HEIF support cannot handle HEVC (iPhone photos), so HEIC goes through
    pillow-heif. Self-test: encode a 64x48 image to HEIC in memory, decode it with Pillow.
    """
    try:
        import io

        import pillow_heif
        from PIL import Image

        pillow_heif.register_heif_opener()
        t = time.perf_counter()
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, format="HEIF", quality=80)
        buf.seek(0)
        with Image.open(buf) as im:
            im.load()
            ok = im.size == (64, 48) and im.format == "HEIF"
        return {
            "ok": ok,
            "pillow_heif": pillow_heif.__version__,
            "libheif": pillow_heif.libheif_info().get("libheif"),
            "hevc_roundtrip_ms": round((time.perf_counter() - t) * 1000, 1),
        }
    except Exception as e:  # report, never crash the health endpoint
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def find_exiftool() -> str | None:
    env = os.environ.get("IV_EXIFTOOL")
    if env and Path(env).is_file():
        return env
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for cand in (base / "exiftool" / "exiftool.exe", Path(sys.executable).parent / "exiftool" / "exiftool.exe"):
        if cand.is_file():
            return str(cand)
    return shutil.which("exiftool")


def exiftool_info() -> dict:
    exe = find_exiftool()
    if not exe:
        return {"ok": False, "error": "exiftool not found"}
    try:
        kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW
        out = subprocess.run([exe, "-ver"], capture_output=True, text=True, timeout=20, **kwargs)
        return {"ok": out.returncode == 0, "version": out.stdout.strip(), "path": exe}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": exe}
