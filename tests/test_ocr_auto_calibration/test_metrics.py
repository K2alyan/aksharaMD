"""Structural and repetition metric tests."""
from __future__ import annotations

from benchmarks.ocr_auto_calibration.metrics import (
    detect_repetition,
    source_page_provenance_complete,
    structural_metrics,
)


def test_detect_repetition_flags_a_looping_phrase() -> None:
    phrase = (
        "the quick brown fox jumps over the lazy dog "
    )
    text = phrase * 30
    count, flag = detect_repetition(text)
    assert flag is True
    assert count >= 20


def test_detect_repetition_does_not_flag_lorem_ipsum() -> None:
    lorem = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
        "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
        "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
        "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
        "culpa qui officia deserunt mollit anim id est laborum."
    )
    count, flag = detect_repetition(lorem)
    assert flag is False
    assert count <= 2


def test_detect_repetition_short_input_returns_zero_no_flag() -> None:
    count, flag = detect_repetition("only three words")
    assert count == 0
    assert flag is False


def test_structural_metrics_counts_headings_paragraphs_images_tables() -> None:
    md = (
        "# Heading 1\n\n"
        "First paragraph body text.\n\n"
        "## Heading 2\n\n"
        "Second paragraph body text.\n\n"
        "![alt text](image.png)\n\n"
        "| Col A | Col B |\n"
        "| --- | --- |\n"
        "| a | b |\n"
        "| c | d |\n\n"
        "Trailing paragraph.\n"
    )
    result = structural_metrics(md, manifest={})
    assert result["headings"] == 2
    # Body paragraphs (excluding heading blocks and table block).
    assert result["paragraphs"] >= 3
    assert result["image_refs"] == 1
    assert result["tables"] == 1
    assert result["markdown_length"] == len(md)


def test_structural_metrics_empty_markdown_returns_zeros() -> None:
    result = structural_metrics("", manifest={})
    assert result == {
        "paragraphs": 0,
        "headings": 0,
        "image_refs": 0,
        "tables": 0,
        "markdown_length": 0,
    }


def test_source_page_provenance_complete_true_when_all_pages_present() -> None:
    manifest = {"pages": [{"page_index": 0}, {"page_index": 1}, {"page_index": 2}]}
    assert source_page_provenance_complete(manifest, expected_page_count=3) is True


def test_source_page_provenance_complete_false_when_page_missing() -> None:
    manifest = {"pages": [{"page_index": 0}, {"page_index": 2}]}
    assert source_page_provenance_complete(manifest, expected_page_count=3) is False


def test_source_page_provenance_complete_accepts_source_pages_key() -> None:
    manifest = {"source_pages": [0, 1, 2, 3]}
    assert source_page_provenance_complete(manifest, expected_page_count=4) is True


def test_source_page_provenance_complete_expected_zero_returns_true() -> None:
    assert source_page_provenance_complete({}, expected_page_count=0) is True
