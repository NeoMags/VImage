"""K1: OCR adapter and benchmark. Uses the real bundled models (no network)."""

from __future__ import annotations

import json

import pytest
from PIL import Image, ImageDraw, ImageFont

from ivsidecar import ocrbench
from ivsidecar.stages.ocr import OcrLine, RapidOcrEngine, reading_order


@pytest.fixture(scope="module")
def engine() -> RapidOcrEngine:
    return RapidOcrEngine(threads=2)


def _render(lines: list[str], size=(900, 260), dark=False) -> Image.Image:
    bg, fg = ("black", "white") if dark else ("white", "black")
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default(size=24)
    for i, ln in enumerate(lines):
        d.text((30 + i * 5, 30 + i * 70), ln, fill=fg, font=f)
    return img


def test_reads_text_with_normalised_boxes_in_reading_order(engine):
    lines = ["Error: No module named cv2", "mail neo@example.com", "https://example.com/a/b"]
    res = engine.recognize(_render(lines))
    assert [ln.text for ln in res.lines] == lines
    assert res.full_text == "\n".join(lines)
    for ln in res.lines:
        assert 0 <= ln.x < 1 and 0 <= ln.y < 1 and 0 < ln.w <= 1 and 0 < ln.h <= 1
        assert 0.5 <= ln.confidence <= 1
    assert res.lines[0].y < res.lines[1].y < res.lines[2].y
    assert res.engine == "rapidocr" and res.engine_version
    assert 0.5 <= res.confidence <= 1


def test_dark_mode_and_rgba_and_greyscale(engine):
    assert engine.recognize(_render(["Dark mode text"], dark=True)).full_text == "Dark mode text"
    rgba = _render(["Transparent PNG"]).convert("RGBA")
    assert engine.recognize(rgba).full_text == "Transparent PNG"
    assert engine.recognize(_render(["Grey scan"]).convert("L")).full_text == "Grey scan"


def test_no_text_and_tiny_images_give_empty_results(engine):
    blank = engine.recognize(Image.new("RGB", (400, 300), (120, 160, 200)))
    assert blank.lines == [] and blank.full_text == "" and blank.confidence is None
    assert engine.recognize(Image.new("RGB", (4, 4))).lines == []


def test_reading_order_groups_a_row_left_to_right():
    a = OcrLine("right", 1, 0.6, 0.10, 0.2, 0.05)
    b = OcrLine("left", 1, 0.1, 0.11, 0.2, 0.05)
    c = OcrLine("below", 1, 0.1, 0.30, 0.2, 0.05)
    assert [ln.text for ln in reading_order([c, a, b])] == ["left", "right", "below"]


def test_error_rates():
    assert ocrbench.error_rates("hello  world", "hello world") == (0.0, 0.0)
    cer, wer = ocrbench.error_rates("abcd efgh", "abXd efgh")
    assert cer == pytest.approx(1 / 9) and wer == pytest.approx(0.5)
    assert ocrbench.error_rates("", "") == (0.0, 0.0)


def test_benchmark_on_a_folder_is_read_only_and_reports_accuracy(tmp_path):
    src = tmp_path / "shots"
    ocrbench.make_synthetic(src, n=4)
    (src / "broken.png").write_bytes(b"not an image")
    before = {p.name: p.stat().st_mtime_ns for p in src.iterdir()}
    out = tmp_path / "report.json"
    rep = ocrbench.run(str(src), out, limit=10, threads=2)
    assert {p.name: p.stat().st_mtime_ns for p in src.iterdir()} == before  # nothing changed or added
    assert rep["images"] == 4 and rep["failed"] == 1 and rep["with_ground_truth"] == 4
    assert rep["cer_mean"] < 0.05
    assert json.loads(out.read_text(encoding="utf-8"))["images"] == 4
    assert "CER" in ocrbench.summary(rep)
