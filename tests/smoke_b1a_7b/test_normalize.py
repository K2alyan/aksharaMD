"""Normalization stage — minimal, no structural rewrite."""
from __future__ import annotations

from benchmarks.eval_v1.smoke_b1a_7b.normalize import NORMALIZATION_VERSION, normalize


def test_version_is_2() -> None:
    assert NORMALIZATION_VERSION == "2"


def test_empty_stays_empty() -> None:
    assert normalize("") == ""


def test_whitespace_trailing_stripped() -> None:
    assert normalize("hello   \nworld  \n") == "hello\nworld\n"


def test_crlf_normalized_to_lf() -> None:
    assert normalize("a\r\nb\r\nc") == "a\nb\nc\n"


def test_multiple_trailing_newlines_collapsed_to_one() -> None:
    assert normalize("a\n\n\n\n") == "a\n"


def test_tables_not_rewritten() -> None:
    src = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert normalize(src) == src


def test_headings_not_rewritten() -> None:
    src = "# H1\n## H2\n### H3\n"
    assert normalize(src) == src


def test_list_markers_not_rewritten() -> None:
    src = "- a\n- b\n  - c\n"
    assert normalize(src) == src


def test_unicode_nfc_applied() -> None:
    # "e" + combining acute (NFD) -> "é" (NFC)
    src = "café\n"
    result = normalize(src)
    assert result == "café\n"


def test_only_trailing_whitespace_content_becomes_empty() -> None:
    assert normalize("   \n   \n") == ""
