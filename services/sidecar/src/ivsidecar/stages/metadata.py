"""Stage 2: source metadata from ExifTool, normalised to `asset_metadata` / `assets` fields.

The raw ExifTool JSON is kept as-is (`raw_exif`) so nothing is lost; the normalised fields
are a convenience for search and sorting. Values that are missing or out of range become
None instead of failing the stage.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .exiftool import ExifTool

# Preference order for "when was this taken"; first one present wins.
_DATE_TAGS = [
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "XMP:DateTimeOriginal",
    "XMP:CreateDate",
    "QuickTime:CreationDate",
    "QuickTime:CreateDate",
    "PNG:CreationTime",
]
_DATE_RE = re.compile(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(\.\d+)?\s*(Z|[+\-]\d{2}:?\d{2})?")


@dataclass
class Metadata:
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    taken_at: str | None = None  # ISO 8601; offset kept when the file records one
    camera_make: str | None = None
    camera_model: str | None = None
    lens: str | None = None
    iso: int | None = None
    exposure_s: float | None = None
    f_number: float | None = None
    focal_length_mm: float | None = None
    orientation: int | None = None
    color_profile: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_alt_m: float | None = None
    raw_exif: str = "{}"
    extractor: str = "exiftool"

    def as_dict(self) -> dict:
        return asdict(self)


def _first(tags: dict, *names: str):
    for n in names:
        v = tags.get(n)
        if v not in (None, "", "0000:00:00 00:00:00"):
            return v
    # fall back to any group: "Make" may sit under EXIF, MakerNotes, QuickTime …
    for n in names:
        short = n.split(":", 1)[-1]
        for k, v in tags.items():
            if k.split(":", 1)[-1] == short and v not in (None, ""):
                return v
    return None


def _num(v, cast=float, lo=None, hi=None):
    try:
        x = cast(v)
    except (TypeError, ValueError):
        return None
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        return None
    return x


def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().strip("\x00")
    return s or None


def parse_date(v) -> str | None:
    """'2023:05:01 12:34:56.12+02:00' → '2023-05-01T12:34:56.12+02:00'; invalid → None."""
    if v is None:
        return None
    m = _DATE_RE.match(str(v).strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, tz = m.groups()
    if not (1800 <= int(y) <= 2200 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
        return None
    out = f"{y}-{mo}-{d}T{h}:{mi}:{s}{frac or ''}"
    if tz:
        out += "Z" if tz == "Z" else (tz if ":" in tz else f"{tz[:3]}:{tz[3:]}")
    return out


def normalise(tags: dict) -> Metadata:
    md = Metadata(raw_exif=json.dumps(tags, ensure_ascii=False, default=str))
    md.width = _num(
        _first(tags, "File:ImageWidth", "EXIF:ExifImageWidth", "PNG:ImageWidth", "QuickTime:ImageWidth"), int, 1
    )
    md.height = _num(
        _first(tags, "File:ImageHeight", "EXIF:ExifImageHeight", "PNG:ImageHeight", "QuickTime:ImageHeight"), int, 1
    )
    dur = _num(_first(tags, "QuickTime:Duration", "Composite:Duration"), float, 0)
    md.duration_ms = round(dur * 1000) if dur is not None else None

    raw_date = _first(tags, *_DATE_TAGS)
    md.taken_at = parse_date(raw_date)
    if md.taken_at and len(md.taken_at) == 19:  # no offset in the date tag itself
        off = _first(tags, "EXIF:OffsetTimeOriginal", "EXIF:OffsetTime")
        if off and re.fullmatch(r"[+\-]\d{2}:\d{2}", str(off)):
            md.taken_at += str(off)

    md.camera_make = _text(_first(tags, "EXIF:Make", "QuickTime:Make"))
    md.camera_model = _text(_first(tags, "EXIF:Model", "QuickTime:Model"))
    md.lens = _text(_first(tags, "EXIF:LensModel", "Composite:LensID", "XMP:Lens"))
    md.iso = _num(_first(tags, "EXIF:ISO"), int, 1, 10_000_000)
    md.exposure_s = _num(_first(tags, "EXIF:ExposureTime"), float, 0)
    md.f_number = _num(_first(tags, "EXIF:FNumber"), float, 0)
    md.focal_length_mm = _num(_first(tags, "EXIF:FocalLength"), float, 0)
    md.orientation = _num(_first(tags, "EXIF:Orientation"), int, 1, 8)
    md.color_profile = _text(_first(tags, "ICC_Profile:ProfileDescription"))

    lat = _num(_first(tags, "Composite:GPSLatitude"), float, -90, 90)
    lon = _num(_first(tags, "Composite:GPSLongitude"), float, -180, 180)
    if lat is not None and lon is not None and not (lat == 0 and lon == 0):  # 0,0 = unset on many cameras
        md.gps_lat, md.gps_lon = lat, lon
        md.gps_alt_m = _num(_first(tags, "Composite:GPSAltitude"), float, -1000, 100_000)
    return md


def read_metadata(path: Path, exiftool: ExifTool) -> Metadata:
    return normalise(exiftool.read_json(path))
