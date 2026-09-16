"""Normalization stage — smoke wrapper equals canonical frozen v2.

The smoke's ``normalize`` MUST be byte-for-byte identical to the
canonical frozen normalizer at ``benchmarks.eval_v1.normalization``.
The tests below fix representative inputs and assert equivalence,
so any silent drift between the two producers is a red test rather
than a subtle instrument-shift at label time.
"""
from __future__ import annotations

from benchmarks.eval_v1 import normalization as _canonical
from benchmarks.eval_v1.smoke_b1a_7b.normalize import (
    NORMALIZATION_VERSION,
    normalize,
)


def _canonical_result(markdown: str) -> _canonical.NormalizationResult:
    return _canonical.get_default_normalizer().normalize(markdown)


def test_version_matches_canonical() -> None:
    assert NORMALIZATION_VERSION == _canonical.NORMALIZATION_VERSION
    assert NORMALIZATION_VERSION == "2"


def test_empty_stays_empty() -> None:
    assert normalize("") == _canonical_result("").text
    assert normalize("") == ""


def test_crlf_to_lf() -> None:
    src = "a\r\nb\r\nc"
    assert normalize(src) == _canonical_result(src).text
    # Canonical rule: LF line-endings only, no other whitespace change.
    assert normalize(src) == "a\nb\nc"


def test_cr_only_to_lf() -> None:
    src = "a\rb\rc"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == "a\nb\nc"


def test_trailing_two_space_hard_break_preserved() -> None:
    """Markdown hard-break syntax: two trailing spaces at end of a
    line. The v2 canonical normalizer MUST NOT rtrim this, or it
    would silently alter Markdown semantics."""
    src = "line one  \nline two\n"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == src  # bytes-identical


def test_table_cell_trailing_spaces_preserved() -> None:
    """Trailing spaces in table cells must not be stripped — the
    v2 canonical normalizer leaves them alone."""
    src = "| a   | b   |\n|-----|-----|\n| 1   | 2   |\n"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == src


def test_multiple_terminal_blank_lines_preserved() -> None:
    """Multi-blank-line collapse was rejected in v2. Terminal blank
    lines must survive intact."""
    src = "para\n\n\n\n"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == src


def test_nfkc_normalization_applied() -> None:
    """NFKC folds compatibility characters. NFC would not — the two
    forms differ for compatibility characters. This test pins the
    canonical choice."""
    # U+2160 ROMAN NUMERAL ONE folds to ASCII "I" under NFKC, not NFC.
    src = "Ⅰ"
    result = normalize(src)
    assert result == _canonical_result(src).text
    assert result == "I"


def test_nfc_vs_nfkc_boundary() -> None:
    """Compatibility characters where NFC and NFKC differ:
    U+FB01 (ﬁ ligature) -> "fi" under NFKC, unchanged under NFC.
    Pins the canonical NFKC choice."""
    src = "ﬁle"  # "ﬁle"
    result = normalize(src)
    assert result == _canonical_result(src).text
    assert result == "file"


def test_per_line_rtrim_NOT_applied() -> None:
    """Per-line rtrim was rejected in v2. Confirm the smoke does not
    reintroduce it."""
    src = "hello   \nworld  \n"
    assert normalize(src) == _canonical_result(src).text
    # Byte-identical: trailing spaces preserved.
    assert normalize(src) == src


def test_horizontal_whitespace_collapse_NOT_applied() -> None:
    """Horizontal whitespace collapse was rejected in v2. Multiple
    interior spaces must survive."""
    src = "hello    world"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == src


def test_headings_not_rewritten() -> None:
    src = "# H1\n## H2\n### H3\n"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == src


def test_list_markers_not_rewritten() -> None:
    src = "- a\n- b\n  - c\n"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == src


def test_soft_hyphen_preserved() -> None:
    """Soft-hyphen removal was rejected in v2. U+00AD must survive."""
    src = "long­word"
    result = normalize(src)
    assert result == _canonical_result(src).text
    assert "­" in result


def test_indented_code_block_preserved() -> None:
    """Fenced/indented code block interior whitespace must not be
    touched."""
    src = "    def x():\n        return 1\n"
    assert normalize(src) == _canonical_result(src).text
    assert normalize(src) == src


def test_normalization_result_object_available_for_audit() -> None:
    """The full NormalizationResult is available via
    ``get_default_normalizer().normalize(...)`` for callers that need
    the ``rules_applied`` audit field."""
    src = "a\r\nb\r\n"
    result = _canonical.get_default_normalizer().normalize(src)
    assert result.version == "2"
    assert result.text == "a\nb\n"
    assert "lf_line_endings" in result.rules_applied
