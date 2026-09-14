"""Tests for GibberishValidator — W_GIBBERISH (P2)."""
from __future__ import annotations

from pathlib import Path

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.plugins.validators.gibberish import (
    GibberishValidator,
    _is_non_standard_char,
)

# ── Character classification isolation ─────────────────────────────────────

def test_ascii_letters_are_standard():
    for c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
        assert not _is_non_standard_char(c)


def test_digits_are_standard():
    for c in "0123456789":
        assert not _is_non_standard_char(c)


def test_whitespace_is_standard():
    for c in " \t\n\r":
        assert not _is_non_standard_char(c)


def test_ascii_punctuation_is_standard():
    for c in ".,;:?!'\"()[]{}<>-/\\@#%&*+=|~`^$_":
        assert not _is_non_standard_char(c), f"{c!r} should be standard"


def test_latin1_accented_letters_are_standard():
    """International content must not be flagged as gibberish."""
    for c in "aeouncAEOUNCcaoinuresumeCafe":
        assert not _is_non_standard_char(c), f"{c!r} should be standard"


def test_mojibake_byte_fragments_are_non_standard():
    """Actual mojibake replacement char and unusual glyphs are non-standard."""
    assert _is_non_standard_char("�")  # replacement character
    assert _is_non_standard_char("¤")
    assert _is_non_standard_char("§")
    assert _is_non_standard_char("†")


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_doc(paragraphs: list[str], file_type: str = "pdf") -> CompilationContext:
    blocks = [
        Block(
            type=BlockType.PARAGRAPH,
            content=text,
            page=1,
            index=i,
            confidence=ExtractionConfidence.EXTRACTED,
        )
        for i, text in enumerate(paragraphs)
    ]
    doc = Document(
        source="test.pdf",
        file_type=file_type,
        pages=1,
        blocks=blocks,
        metadata={},
    )
    ctx = CompilationContext(source="test.pdf")
    ctx.document = doc
    return ctx


def _fixture_paragraphs(fixture_path: Path) -> list[str]:
    text = fixture_path.read_text(encoding="utf-8")
    return [p.strip() for p in text.split("\n\n") if p.strip()]


# ── Signal A: non-standard character density ───────────────────────────────

def test_high_symbol_density_fires_the_detector():
    paragraphs = [
        "Normal prose line one with regular words.",
        "¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤",
        "§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§§",
        "††††††††††††††††††††††††††††††††††††††††††††††††††",
    ] + ["Body line with real words."] * 8
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is True
    assert diag["non_standard_density"] >= 0.15
    assert "non_standard_density" in diag["fired_triggers"]


def test_clean_prose_does_not_fire_the_detector():
    paragraphs = [
        "The company reported strong quarterly earnings this year.",
        "North America contributed forty-two percent of the total revenue.",
        "European operations grew sequentially quarter over quarter.",
        "Asia Pacific posted its strongest annual result on record.",
        "Consolidated gross margin expanded by two hundred forty basis points.",
        "Operating margin reached twenty-one percent for the full year.",
        "Free cash flow totaled one hundred eighty-seven million dollars.",
        "Shareholders received a portion of that through dividends.",
        "Management guided flat-to-slightly-up growth for the coming year.",
        "Investors received the update on the quarterly earnings call.",
        "The full financial statements appear on page forty-seven.",
    ]
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False


def test_international_content_does_not_fire_the_detector():
    """Latin-1 accented characters must not count as non-standard."""
    paragraphs = [
        "Résumé submitted by François Müller with the following credentials.",
        "The candidate holds a Baccalauréat and a Master's from Université de Genève.",
        "Prior experience includes work at Österreichischer Rundfunk in Vienna.",
        "References from München Universität faculty are attached separately.",
        "Naïve treatment of encoding issues has plagued past applications.",
        "The candidate is fluent in English, Français, and Español.",
        "Willingness to relocate to São Paulo or Zürich for the right role.",
        "Salary expectations align with market rates for the position.",
        "Available to start work within the standard notice period.",
        "Contact information appears on the final page of this document.",
        "Portfolio and additional references available on request.",
    ]
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False


# ── Signal B: character repetition ─────────────────────────────────────────

def test_extreme_repetition_run_fires_immediately():
    """A single run of 8+ identical characters is enough."""
    paragraphs = [
        "Normal line one.",
        "Normal line two.",
        "Corrupted content: aaaaaaaaaa in the middle of a line.",
    ] + ["Body content line."] * 10
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is True
    assert diag["extreme_run_count"] >= 1
    assert "extreme_run" in diag["fired_triggers"]


def test_long_run_needs_three_occurrences():
    """Runs of 6-7 chars need >=3 occurrences to fire."""
    paragraphs = [
        "First run: aaaaaa here.",
        "Second run: 111111 there.",
        "Third run: bbbbbb elsewhere.",
    ] + ["Body content line."] * 10
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is True
    assert diag["long_run_count"] >= 3
    assert "long_run" in diag["fired_triggers"]


def test_decorative_dash_run_does_not_fire():
    """Section separators like `--------------------` are legitimate typography."""
    paragraphs = [
        "First section header.",
        "-" * 40,
        "Body content follows.",
    ] + ["More content."] * 12
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False, (
        f"decorative dash run FP'd: extreme_run_count={diag.get('extreme_run_count')}"
    )


def test_decorative_equals_run_does_not_fire():
    """Section separators like `====================` are legitimate typography."""
    paragraphs = [
        "First section header.",
        "=" * 30,
        "Body content follows.",
    ] + ["More content."] * 12
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False


def test_decorative_dots_and_underscores_do_not_fire():
    """Ellipsis-like `..........` and underscore runs `__________` are typography."""
    paragraphs = [
        "Table of contents:",
        "Chapter 1 " + "." * 25 + " page 3",
        "Signature: " + "_" * 30,
    ] + ["Body content."] * 12
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    # Note: 3 underscore runs of >=5 chars would trip the underscore trigger
    # in W_PLACEHOLDER_STUB — but not W_GIBBERISH. Confirm gibberish stays quiet.
    assert diag["warned"] is False


def test_unusual_symbol_run_still_fires():
    """Obscure symbols like ¤¤¤ / §§§ / ††† are NOT decorative typography and must still fire."""
    paragraphs = [
        "Normal line one.",
        "¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤",
    ] + ["Body content."] * 12
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is True
    assert diag["extreme_run_count"] >= 1


def test_alphanumeric_run_still_fires():
    """`aaaaaaaa` and `1111111111` are never authored typography — always suspect."""
    paragraphs = [
        "Line one: aaaaaaaaaa here.",
    ] + ["Body content."] * 12
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is True


def test_single_long_run_does_not_fire():
    """One run of 6 chars — below the 3-run threshold."""
    paragraphs = [
        "Only one long run: aaaaaa in the doc.",
    ] + ["Body content line."] * 12
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False


# ── Skip guards ────────────────────────────────────────────────────────────

def test_ineligible_file_type_is_skipped_silently():
    paragraphs = ["¤" * 100] + ["Body"] * 12
    ctx = _make_doc(paragraphs, file_type="jpg")
    GibberishValidator().execute(ctx)
    assert "gibberish_diagnostics" not in ctx.document.metadata


def test_tiny_doc_guard_prevents_firing():
    paragraphs = ["¤" * 100]  # 1 non-empty line
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False
    assert "too few nonempty lines" in diag.get("suppressed_reason", "")


# ── Diagnostic completeness ────────────────────────────────────────────────

def test_diagnostics_always_populated_when_eligible():
    paragraphs = ["Clean prose line."] * 15
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False
    assert "non_standard_density" in diag
    assert "extreme_run_count" in diag
    assert "long_run_count" in diag
    assert diag["warning_maturity"] == "experimental"


def test_warning_message_carries_lower_bound_framing():
    paragraphs = ["¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤¤"] + ["Body content."] * 12
    ctx = _make_doc(paragraphs)
    GibberishValidator().execute(ctx)
    warnings = [i for i in ctx.validation.issues if i.code == "W_GIBBERISH"]
    assert len(warnings) == 1
    assert "LOWER BOUND" in warnings[0].message or "lower bound" in warnings[0].message.lower()


# ── Integration with P0.5 corpus (updated fixture) ─────────────────────────

def test_ocr_gibberish_fixture_positive_fires_the_detector():
    """P0.5 positive fixture (rewritten for P2 to use mojibake / symbol junk)."""
    from benchmarks.substance_calibration.corpus import list_fixture_class
    entries = [
        e for e in list_fixture_class("ocr_gibberish") if not e.is_negative
    ]
    assert entries, "expected at least one positive ocr_gibberish fixture"
    entry = entries[0]
    paragraphs = _fixture_paragraphs(entry.markdown_path)
    ctx = _make_doc(paragraphs, file_type="md")
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is True, (
        f"positive fixture {entry.document_id} did not fire the detector; "
        f"diagnostics: {diag}"
    )


def test_ocr_gibberish_fixture_negative_does_not_fire_the_detector():
    """P0.5 negative fixture (code snippet) must not fire."""
    from benchmarks.substance_calibration.corpus import list_fixture_class
    entries = [
        e for e in list_fixture_class("ocr_gibberish") if e.is_negative
    ]
    assert entries, "expected at least one negative ocr_gibberish fixture"
    entry = entries[0]
    paragraphs = _fixture_paragraphs(entry.markdown_path)
    ctx = _make_doc(paragraphs, file_type="md")
    GibberishValidator().execute(ctx)
    diag = ctx.document.metadata["gibberish_diagnostics"]
    assert diag["warned"] is False, (
        f"negative fixture {entry.document_id} incorrectly fired the detector; "
        f"diagnostics: {diag}"
    )
