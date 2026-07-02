from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.image import (
    _exif_value,
    _is_quality_ocr,
    _ocr_to_blocks,
    _preprocess_for_ocr,
    _try_ocr_structured,
)

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _PIL_AVAILABLE, reason="Pillow not installed")


# ── _preprocess_for_ocr ──────────────────────────────────────────────────────

def test_preprocess_rgb_to_grayscale():
    img = Image.new("RGB", (200, 200), color=(128, 128, 128))
    result = _preprocess_for_ocr(img)
    assert result.mode == "L"


def test_preprocess_rgba_composited_on_white():
    img = Image.new("RGBA", (200, 200), color=(0, 0, 0, 0))  # fully transparent black
    result = _preprocess_for_ocr(img)
    assert result.mode == "L"
    # Fully transparent black on white → should be near-white (high pixel value)
    pixels = list(result.getdata())
    assert sum(pixels) / len(pixels) > 200


def test_preprocess_scales_up_small_images():
    img = Image.new("RGB", (50, 50), color=(100, 100, 100))
    result = _preprocess_for_ocr(img)
    assert max(result.size) >= 500


def test_preprocess_large_image_unchanged_size():
    img = Image.new("RGB", (1000, 800), color=(200, 200, 200))
    result = _preprocess_for_ocr(img)
    assert result.size == (1000, 800)


def test_preprocess_palette_mode():
    img = Image.new("P", (200, 200))
    result = _preprocess_for_ocr(img)
    assert result.mode == "L"


# ── _is_quality_ocr ──────────────────────────────────────────────────────────

def test_quality_ocr_rejects_short():
    assert not _is_quality_ocr("hi")


def test_quality_ocr_rejects_noise():
    assert not _is_quality_ocr("123 456 789 000 111")


def test_quality_ocr_accepts_real_text():
    assert _is_quality_ocr("This is a normal sentence with real words.")


def test_quality_ocr_rejects_empty_string():
    assert not _is_quality_ocr("")


# ── _try_ocr_structured ──────────────────────────────────────────────────────

def _make_tesseract_data(entries: list[dict]) -> dict:
    """Build a fake pytesseract.Output.DICT result from a list of word specs."""
    keys = ["level", "page_num", "block_num", "par_num", "line_num",
            "word_num", "left", "top", "width", "height", "conf", "text"]
    result = {k: [] for k in keys}
    for i, e in enumerate(entries):
        result["level"].append(5)
        result["page_num"].append(1)
        result["block_num"].append(e.get("block", 1))
        result["par_num"].append(e.get("par", 1))
        result["line_num"].append(e.get("line", 1))
        result["word_num"].append(i + 1)
        result["left"].append(0)
        result["top"].append(0)
        result["width"].append(50)
        result["height"].append(e.get("height", 20))
        result["conf"].append(e.get("conf", 80))
        result["text"].append(e.get("text", ""))
    return result


def _mock_configure(monkeypatch, structured_fn_path):
    monkeypatch.setattr(
        "aksharamd.plugins.parsers.image._configure_tesseract", lambda: True
    )


@pytest.fixture()
def tiny_img():
    return Image.new("L", (100, 100), 255)


def test_structured_ocr_returns_empty_when_tesseract_unavailable(tiny_img, monkeypatch):
    monkeypatch.setattr(
        "aksharamd.plugins.parsers.image._configure_tesseract", lambda: False
    )
    assert _try_ocr_structured(tiny_img) == []


def test_structured_ocr_detects_heading_by_height(tiny_img, monkeypatch):
    _mock_configure(monkeypatch, "")
    body_words = [{"block": 1, "par": 1, "text": w, "height": 20, "conf": 85}
                  for w in ["This", "is", "body", "text", "content"]]
    heading_words = [{"block": 2, "par": 1, "text": w, "height": 50, "conf": 85}
                     for w in ["Section", "One", "Heading"]]
    fake_data = _make_tesseract_data(body_words + heading_words)

    with patch("pytesseract.image_to_data", return_value=fake_data):
        result = _try_ocr_structured(tiny_img)

    types = [r[0] for r in result]
    assert BlockType.PARAGRAPH in types
    assert BlockType.HEADING in types


def test_structured_ocr_heading_level_scales_with_ratio(tiny_img, monkeypatch):
    _mock_configure(monkeypatch, "")
    # 10 body words keep median_h = 20 even after heading words are included
    body = [{"block": 1, "par": 1, "text": w, "height": 20, "conf": 85}
            for w in ["body", "text", "normal", "words", "here",
                      "more", "content", "fills", "the", "page"]]
    h1_words = [{"block": 2, "par": 1, "text": w, "height": 44, "conf": 85}
                for w in ["Big", "Title", "Here"]]   # ratio ~2.2 → H1
    h2_words = [{"block": 3, "par": 1, "text": w, "height": 34, "conf": 85}
                for w in ["Medium", "Heading", "Text"]]  # ratio ~1.7 → H2
    h3_words = [{"block": 4, "par": 1, "text": w, "height": 27, "conf": 85}
                for w in ["Small", "Heading", "Entry"]]  # ratio ~1.35 → H3
    fake_data = _make_tesseract_data(body + h1_words + h2_words + h3_words)

    with patch("pytesseract.image_to_data", return_value=fake_data):
        result = _try_ocr_structured(tiny_img)

    headings = [(r[0], r[2]) for r in result if r[0] == BlockType.HEADING]
    levels = {lvl for _, lvl in headings}
    assert 1 in levels
    assert 2 in levels
    assert 3 in levels


def test_structured_ocr_caps_heading_at_body_height(tiny_img, monkeypatch):
    _mock_configure(monkeypatch, "")
    # ALL CAPS phrase at body text height → should be H3
    body = [{"block": 1, "par": 1, "text": w, "height": 20, "conf": 85}
            for w in ["normal", "text", "words", "here", "today"]]
    caps = [{"block": 2, "par": 1, "text": w, "height": 20, "conf": 85}
            for w in ["SECTION", "INTRODUCTION", "OVERVIEW"]]
    fake_data = _make_tesseract_data(body + caps)

    with patch("pytesseract.image_to_data", return_value=fake_data):
        result = _try_ocr_structured(tiny_img)

    heading_blocks = [r for r in result if r[0] == BlockType.HEADING]
    assert len(heading_blocks) >= 1
    assert heading_blocks[0][2] == 3


def test_structured_ocr_rejects_low_confidence(tiny_img, monkeypatch):
    _mock_configure(monkeypatch, "")
    low_conf_words = [{"block": 1, "par": 1, "text": w, "height": 20, "conf": 15}
                      for w in ["some", "words"]]
    fake_data = _make_tesseract_data(low_conf_words)

    with patch("pytesseract.image_to_data", return_value=fake_data):
        result = _try_ocr_structured(tiny_img)

    assert result == []


def test_structured_ocr_filters_noise_blocks(tiny_img, monkeypatch):
    _mock_configure(monkeypatch, "")
    # Single short noise word — should be filtered by _is_quality_ocr
    noise = [{"block": 1, "par": 1, "text": "xx", "height": 20, "conf": 80}]
    good = [{"block": 2, "par": 1, "text": w, "height": 20, "conf": 80}
            for w in ["This", "is", "real", "content", "text"]]
    fake_data = _make_tesseract_data(noise + good)

    with patch("pytesseract.image_to_data", return_value=fake_data):
        result = _try_ocr_structured(tiny_img)

    assert len(result) == 1
    assert result[0][0] == BlockType.PARAGRAPH


# ── _exif_value ───────────────────────────────────────────────────────────────

def test_exif_value_bytes():
    result = _exif_value(b"Canon\x00")
    assert isinstance(result, str)
    assert "Canon" in result


def test_exif_value_rational_tuple():
    result = _exif_value((1, 60))
    assert result == "1/60"


def test_exif_value_string_passthrough():
    assert _exif_value("Normal") == "Normal"


def test_exif_value_int():
    assert _exif_value(100) == "100"


# ── _ocr_to_blocks ────────────────────────────────────────────────────────────

def test_ocr_to_blocks_single_paragraph():
    blocks = _ocr_to_blocks("Hello world this is text.", start_idx=0)
    assert len(blocks) == 1
    assert blocks[0].type == BlockType.PARAGRAPH
    assert blocks[0].index == 0


def test_ocr_to_blocks_multiple_paragraphs():
    text = "First paragraph text.\n\nSecond paragraph text."
    blocks = _ocr_to_blocks(text, start_idx=5)
    assert len(blocks) == 2
    assert blocks[0].index == 5
    assert blocks[1].index == 6


def test_ocr_to_blocks_skips_short_chunks():
    text = "hi\n\nThis is a real paragraph with content."
    blocks = _ocr_to_blocks(text, start_idx=0)
    # "hi" is below _MIN_OCR_CHARS; only the full paragraph should remain
    assert all(len(b.content) >= 10 for b in blocks)


# ── ImageParser.execute (no OCR) ──────────────────────────────────────────────

def _make_png_bytes() -> bytes:
    """Create a minimal valid PNG file in memory."""
    img = Image.new("RGB", (50, 50), color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_image_parser_produces_document(tmp_path):
    f = tmp_path / "test.png"
    f.write_bytes(_make_png_bytes())
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    assert ctx.document is not None
    assert ctx.document.file_type == "png"


def test_image_parser_metadata_contains_dimensions(tmp_path):
    f = tmp_path / "dims.png"
    f.write_bytes(_make_png_bytes())
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    meta = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta) >= 1
    assert "50x50" in meta[0].content


def test_image_parser_metadata_contains_mode(tmp_path):
    f = tmp_path / "mode.png"
    f.write_bytes(_make_png_bytes())
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    meta = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert "RGB" in meta[0].content


def test_image_parser_corrupt_file_does_not_crash(tmp_path):
    f = tmp_path / "bad.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    assert ctx is not None
