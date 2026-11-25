"""B4: stages 1-4 against the generated fixture set."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import numpy as np
import pytest
from fixtures_gen import Case, build, run_exiftool, scene
from PIL import Image

from ivsidecar.probes import find_exiftool
from ivsidecar.stages import StageError, metadata
from ivsidecar.stages.exiftool import ExifTool
from ivsidecar.stages.hashing import hamming, phash_from_grey32, sha256_file
from ivsidecar.stages.identify import identify, identify_bytes
from ivsidecar.stages.pipeline import process_file
from ivsidecar.stages.thumbnails import _load_raw_preview, make_thumbnails

if not find_exiftool():
    if os.environ.get("IV_REQUIRE_EXIFTOOL") == "1":
        raise RuntimeError("IV_REQUIRE_EXIFTOOL=1 but exiftool was not found")
    pytest.skip("exiftool not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def fixture_set(tmp_path_factory):
    src = tmp_path_factory.mktemp("sources")
    cases = build(src)
    for p in src.iterdir():  # sources are read-only: any write attempt by a stage fails loudly
        p.chmod(0o444)
    yield src, cases
    for p in src.iterdir():
        p.chmod(0o644)


@pytest.fixture(scope="module")
def exiftool():
    with ExifTool() as et:
        yield et


@pytest.fixture(scope="module")
def results(fixture_set, exiftool, tmp_path_factory):
    src, cases = fixture_set
    lib = tmp_path_factory.mktemp("library")
    out = {}
    for c in cases:
        if c.needs and not shutil.which(c.needs):
            continue
        out[c.name] = process_file(src / c.file, lib, str(uuid.uuid4()), exiftool, max_pixels=c.max_pixels)
    return lib, out


def _cases():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        return build(Path(d))


ALL_CASES = _cases()


def test_fixture_set_has_at_least_50_files():
    assert len(ALL_CASES) >= 50


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_fixture(case: Case, results):
    lib, out = results
    if case.name not in out:
        pytest.skip(f"needs {case.needs}")
    r = out[case.name]
    assert r.ident.mime == case.mime
    assert r.status == case.status, r.error
    if case.error_code:
        assert r.error and r.error.startswith(case.error_code), r.error
    if case.status == "ready" and r.ident.kind == "image":
        assert r.thumbs is not None
        assert (r.thumbs.width, r.thumbs.height) == case.size
        kinds = {d.kind: d for d in r.thumbs.derivatives}
        assert set(kinds) == {"thumb_512", "thumb_256"}
        for d in kinds.values():
            f = lib / d.rel_path
            assert f.is_file() and f.stat().st_size == d.byte_size
            with Image.open(f) as im:
                assert im.format == "WEBP" and im.size == (d.width, d.height)
                assert max(im.size) <= int(d.kind.split("_")[1])
                assert im.mode in ("RGB", "RGBA")
        assert len(r.thumbs.phash) == 8
    if r.ident.kind == "video":
        assert r.thumbs is None
    for key, expected in case.meta.items():
        got = getattr(r.metadata, key)
        if isinstance(expected, float):
            assert got == pytest.approx(expected, abs=1e-4), key
        else:
            assert got == expected, key


def test_orientation_is_applied_to_thumbnails(results):
    """Red block is top-left in the stored pixels; after rotation it must move (orientation 6 = 90° CW)."""
    lib, out = results

    def red_corner(name):
        d = next(x for x in out[name].thumbs.derivatives if x.kind == "thumb_256")
        with Image.open(lib / d.rel_path) as im:
            a = np.asarray(im.convert("RGB")).astype(int)
        h, w = a.shape[:2]
        corners = {"tl": a[2, 2], "tr": a[2, w - 3], "bl": a[h - 3, 2], "br": a[h - 3, w - 3]}
        return max(corners, key=lambda k: corners[k][0] - corners[k][1])

    assert red_corner("jpeg_orientation_1") == "tl"
    assert red_corner("jpeg_orientation_3") == "br"
    assert red_corner("jpeg_orientation_6") == "tr"
    assert red_corner("jpeg_orientation_8") == "bl"


def test_exact_duplicate_has_same_sha256(results):
    _, out = results
    assert out["exact_duplicate"].sha256 == out["jpeg_no_exif"].sha256
    assert out["jpeg_no_exif"].sha256 != out["png_rgb"].sha256


def test_sha256_matches_hashlib(fixture_set):
    import hashlib

    src, _ = fixture_set
    f = src / "full_exif.jpg"
    assert sha256_file(f) == hashlib.sha256(f.read_bytes()).digest()


def test_phash_near_and_far(tmp_path, exiftool):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"  # same picture, smaller and recompressed
    c = tmp_path / "c.jpg"  # different picture
    Image.fromarray(scene(800, 600, 40)).save(a, quality=95)
    Image.fromarray(scene(800, 600, 40)).resize((400, 300)).save(b, quality=60)
    Image.fromarray(scene(800, 600, 41)).save(c, quality=95)
    lib = tmp_path / "lib"
    h = {p.stem: make_thumbnails(p, identify(p), lib, str(uuid.uuid4())).phash for p in (a, b, c)}
    assert hamming(h["a"], h["b"]) <= 6
    assert hamming(h["a"], h["c"]) >= 16


def test_phash_rotation_corrected(results):
    _, out = results
    # Orientation 1 and orientation 3 (180°) show different pictures once rotated, so
    # their hashes differ: the hash is taken after orientation is applied.
    assert hamming(out["jpeg_orientation_1"].thumbs.phash, out["jpeg_orientation_3"].thumbs.phash) > 10


def test_phash_constant_image_is_stable():
    # Only the DC term is non-zero, so only the first bit is set, for any grey level.
    expected = b"\x80" + bytes(7)
    assert phash_from_grey32(np.full((32, 32), 128, dtype=np.uint8)) == expected
    assert phash_from_grey32(np.full((32, 32), 30, dtype=np.uint8)) == expected


def test_raw_preview_path_uses_embedded_jpeg(tmp_path, exiftool):
    f = tmp_path / "with_thumb.jpg"
    Image.fromarray(scene(1200, 800, 50)).save(f)
    thumb = tmp_path / "t.jpg"
    Image.fromarray(scene(160, 120, 50)).save(thumb)
    exe = find_exiftool()

    run_exiftool(exe, "-q", "-overwrite_original", f"-ThumbnailImage<={thumb}", str(f))
    img, w, h = _load_raw_preview(f, exiftool, orientation=6)
    assert (w, h) == (120, 160)  # embedded 160x120 preview, rotated by orientation 6


def test_stages_never_write_to_sources(fixture_set, results):
    # Files were read-only while the stages ran (any write would have raised), and no new
    # file (ExifTool backup, temp file, thumbnail) appeared next to them.
    src, cases = fixture_set
    assert {p.name for p in src.iterdir()} == {c.file for c in cases if (src / c.file).exists()}
    is_root = getattr(os, "geteuid", lambda: 1)() == 0  # root ignores file permissions
    assert all(is_root or not os.access(p, os.W_OK) for p in src.iterdir())


@pytest.mark.parametrize(
    ("head", "suffix", "mime"),
    [
        (b"\x00\x00\x00\x18ftypheic", "", "image/heic"),
        (b"\x00\x00\x00\x18ftypmif1", "", "image/heic"),
        (b"\x00\x00\x00\x18ftypavif", "", "image/avif"),
        (b"\x00\x00\x00\x18ftypcrx ", "", "image/x-canon-cr3"),
        (b"II*\x00\x10\x00\x00\x00CR\x02\x00", ".cr2", "image/x-canon-cr2"),
        (b"II*\x00\x08\x00\x00\x00", ".NEF", "image/x-nikon-nef"),
        (b"II*\x00\x08\x00\x00\x00", ".arw", "image/x-sony-arw"),
        (b"FUJIFILMCCD-RAW 0201", "", "image/x-fuji-raf"),
        (b"IIU\x00", ".rw2", "image/x-panasonic-rw2"),
        (b"RIFF\x00\x00\x00\x00AVI LIST", "", "video/x-msvideo"),
    ],
)
def test_identify_magic(head, suffix, mime):
    assert identify_bytes(head, suffix).mime == mime


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("2024:07:14 18:30:05", "2024-07-14T18:30:05"),
        ("2024:07:14 18:30:05.123+0530", "2024-07-14T18:30:05.123+05:30"),
        ("2024-07-14T18:30:05Z", "2024-07-14T18:30:05Z"),
        ("0000:00:00 00:00:00", None),
        ("garbage", None),
        (None, None),
    ],
)
def test_parse_date(raw, iso):
    assert metadata.parse_date(raw) == iso


def test_exiftool_timeout_restarts(tmp_path):
    f = tmp_path / "x.jpg"
    Image.fromarray(scene(50, 50, 60)).save(f)
    with ExifTool(timeout_s=0.000001) as et:
        with pytest.raises(Exception, match="timed out"):
            et.read_json(f)
        et.timeout_s = 30
        assert et.read_json(f)["File:FileType"] == "JPEG"  # restarted transparently


def test_unsupported_kind_raises(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(StageError):
        make_thumbnails(f, identify(f), tmp_path / "lib", str(uuid.uuid4()))


def test_preview_is_1600_px_never_enlarged_and_reads_only(fixture_set, exiftool, tmp_path):
    from ivsidecar.stages.identify import identify
    from ivsidecar.stages.thumbnails import make_preview

    src, _ = fixture_set
    big = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (200, 30, 30)).save(big, quality=80)
    lib = tmp_path / "lib"
    uid = str(uuid.uuid4())
    d = make_preview(big, identify(big), lib, uid)
    assert (d.kind, d.width, d.height) == ("preview_1600", 1600, 1200)
    assert (lib / d.rel_path).is_file() and d.rel_path.startswith("thumbs/") and d.rel_path.endswith("_1600.webp")

    small = make_preview(src / "photo.heic", identify(src / "photo.heic"), lib, str(uuid.uuid4()))
    assert (small.width, small.height) == (640, 480)  # smaller originals stay their size

    o6 = src / "orient_6.jpg"
    rotated = make_preview(o6, identify(o6), lib, str(uuid.uuid4()))
    assert rotated.height > rotated.width  # EXIF rotation applied

    for name, code in (("truncated.jpg", "decode_failed"), ("doc.pdf", "unsupported")):
        with pytest.raises(StageError) as e:
            make_preview(src / name, identify(src / name), lib, str(uuid.uuid4()))
        assert e.value.code == code
