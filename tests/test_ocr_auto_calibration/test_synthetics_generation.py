"""Synthetic PDF generator tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.ocr_auto_calibration.synthetics import generate_all


@pytest.fixture
def _tmp_synth(tmp_path: Path) -> Path:
    return tmp_path / "synth"


def test_generate_all_creates_eight_pdfs_and_labels(_tmp_synth: Path) -> None:
    pytest.importorskip("fitz")
    pytest.importorskip("PIL")
    results = generate_all(out_dir=_tmp_synth)
    assert len(results) == 8
    for name in results:
        pdf = _tmp_synth / name
        label = pdf.with_suffix(".json")
        hash_file = pdf.with_suffix(".hash")
        assert pdf.exists(), f"{name} missing"
        assert label.exists(), f"{name} label missing"
        assert hash_file.exists(), f"{name} hash missing"


def test_generated_pdfs_have_expected_page_counts(_tmp_synth: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("PIL")
    generate_all(out_dir=_tmp_synth)

    expected = {
        "synth_scanned_1p.pdf": 1,
        "synth_scanned_2p.pdf": 2,
        "synth_scanned_3p.pdf": 3,
        "synth_mixed_below_30pct.pdf": 20,
        "synth_mixed_exact_30pct.pdf": 10,
        "synth_mixed_above_30pct.pdf": 20,
        "synth_mostly_scanned.pdf": 10,
        "synth_digital_only.pdf": 10,
    }
    for name, expected_pages in expected.items():
        doc = fitz.open(str(_tmp_synth / name))
        try:
            assert doc.page_count == expected_pages, f"{name} pages"
        finally:
            doc.close()


def test_generated_labels_carry_expected_backend(_tmp_synth: Path) -> None:
    pytest.importorskip("fitz")
    pytest.importorskip("PIL")
    import json

    generate_all(out_dir=_tmp_synth)

    expected_backend = {
        "synth_scanned_1p.pdf": "tesseract",  # below floor
        "synth_scanned_2p.pdf": "tesseract",  # below floor
        "synth_scanned_3p.pdf": "unlimited_ocr",  # at floor, 100%
        "synth_mixed_below_30pct.pdf": "tesseract",  # 20% < 30%
        "synth_mixed_exact_30pct.pdf": "unlimited_ocr",  # exactly 30% (>= threshold)
        "synth_mixed_above_30pct.pdf": "unlimited_ocr",  # 35%
        "synth_mostly_scanned.pdf": "unlimited_ocr",  # 80%
        "synth_digital_only.pdf": "tesseract",  # 0%
    }
    for name, expected in expected_backend.items():
        label = json.loads((_tmp_synth / name).with_suffix(".json").read_text())
        assert label["expected_backend_by_policy"] == expected, name


def test_generator_is_idempotent(_tmp_synth: Path) -> None:
    pytest.importorskip("fitz")
    pytest.importorskip("PIL")
    first = generate_all(out_dir=_tmp_synth)
    mtimes = {
        n: (_tmp_synth / n).stat().st_mtime for n in first
    }
    second = generate_all(out_dir=_tmp_synth)
    # No file should be reported as regenerated on the second pass.
    assert all(v is False for v in second.values())
    for name, prior_mtime in mtimes.items():
        assert (_tmp_synth / name).stat().st_mtime == prior_mtime


def test_per_page_extractable_text_matches_profile(_tmp_synth: Path) -> None:
    """The first N pages must have no extractable text; the rest must have text."""
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("PIL")
    import json

    generate_all(out_dir=_tmp_synth)
    for name in ("synth_mixed_below_30pct.pdf", "synth_mostly_scanned.pdf"):
        label = json.loads((_tmp_synth / name).with_suffix(".json").read_text())
        scanned = int(label["ocr_required_pages"])
        doc = fitz.open(str(_tmp_synth / name))
        try:
            for i in range(scanned):
                text = doc[i].get_text().strip()
                assert text == "", f"{name} page {i} unexpectedly has text: {text!r}"
            for i in range(scanned, doc.page_count):
                text = doc[i].get_text().strip()
                assert text, f"{name} page {i} has no text"
        finally:
            doc.close()
