"""Tests for PlaceholderStubValidator — W_PLACEHOLDER_STUB (P1.1)."""
from __future__ import annotations

from pathlib import Path

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.plugins.validators.placeholder_stub import (
    BRACKET_PLACEHOLDER_RE,
    UNDERSCORE_RUN_RE,
    PlaceholderStubValidator,
)

# ── Regex isolation ────────────────────────────────────────────────────────

def test_bracket_regex_matches_form_placeholders():
    text = "Full Name: ___ Employee ID: [Employee ID] Date: [Date]"
    assert len(BRACKET_PLACEHOLDER_RE.findall(text)) == 2


def test_bracket_regex_excludes_short_and_lowercase_brackets():
    """[1], [a], [note], [ab] must not match. Real footnote refs and short brackets."""
    text = "See [1] and [a] and [note 3] and [ab]."
    assert BRACKET_PLACEHOLDER_RE.findall(text) == []


def test_bracket_regex_matches_multi_word_capitalized_placeholders():
    text = "Return this to [Witness Name and Signature] please."
    assert BRACKET_PLACEHOLDER_RE.findall(text) == ["[Witness Name and Signature]"]


def test_underscore_regex_needs_five_or_more():
    """Four underscores must not match (too short); five must."""
    assert UNDERSCORE_RUN_RE.findall("____") == []
    assert UNDERSCORE_RUN_RE.findall("_____") == ["_____"]
    assert UNDERSCORE_RUN_RE.findall("____________") == ["____________"]


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_doc(paragraphs: list[str], file_type: str = "pdf") -> CompilationContext:
    """Build a CompilationContext with the given paragraphs as PARAGRAPH blocks."""
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
    """Read a P0.5 fixture .md and return its double-newline-separated paragraphs."""
    text = fixture_path.read_text(encoding="utf-8")
    return [p.strip() for p in text.split("\n\n") if p.strip()]


# ── Trigger A: bracket placeholders ────────────────────────────────────────

def test_bracket_trigger_fires_at_threshold():
    """3+ bracketed placeholders across text-bearing blocks fires the warning."""
    paragraphs = [
        "Header one paragraph with [FirstField] placeholder.",
        "Header two paragraph with [SecondField] placeholder.",
        "Header three paragraph with [ThirdField] placeholder.",
    ] + [f"Body line {n} with real content." for n in range(1, 12)]
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is True
    assert diag["bracket_count"] >= 3
    assert "bracket_placeholder" in diag["fired_triggers"]


def test_bracket_trigger_below_threshold_does_not_fire():
    """Only 2 bracket placeholders — below the threshold."""
    paragraphs = ["Line with [First] then [Second]."] * 15
    ctx = _make_doc(paragraphs)
    # Count = 2 * 15 = 30 total, well over threshold. Adjust: only 2 unique
    # placeholders means bracket_count = 30. That's over 3. So this fires.
    # Rewrite to hit exactly 2 total occurrences:
    paragraphs = ["Line one." for _ in range(15)]
    paragraphs[0] = "Ref [First] and [Second]."
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["bracket_count"] == 2
    assert diag["warned"] is False


# ── Trigger B: extraction stubs ────────────────────────────────────────────

def test_extraction_stub_fires_on_single_occurrence():
    paragraphs = [
        "Introduction paragraph.",
        "See figure below: [Image omitted]",
        "Body content line one.",
        "Body content line two.",
    ] + ["More body content."] * 10
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is True
    assert diag["extraction_stub_count"] >= 1
    assert "extraction_stub" in diag["fired_triggers"]


def test_extraction_stub_case_insensitive():
    paragraphs = ["Line."] * 10 + ["<FIGURE>"]
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is True


# ── Trigger C: LLM refusal stubs ───────────────────────────────────────────

def test_llm_refusal_fires_on_single_occurrence():
    paragraphs = [
        "As an AI language model, I cannot process this specific request.",
    ] + ["Body content line."] * 10
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is True
    assert diag["refusal_count"] >= 1
    assert "llm_refusal" in diag["fired_triggers"]


# ── Trigger D: underscored blanks ──────────────────────────────────────────

def test_underscore_trigger_fires_at_threshold():
    paragraphs = [
        "Name: ___________ Date: ___________ Signature: ___________",
    ] + ["Body content."] * 10
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is True
    assert diag["underscore_run_count"] >= 3
    assert "underscore_run" in diag["fired_triggers"]


# ── Skip guards ────────────────────────────────────────────────────────────

def test_ineligible_file_type_is_skipped_silently():
    """Image/audio file types do not run this detector."""
    paragraphs = ["[Placeholder] " * 20] + ["Body"] * 15
    ctx = _make_doc(paragraphs, file_type="jpg")
    PlaceholderStubValidator().execute(ctx)
    # Detector self-skips before touching metadata; no diagnostics dict either.
    assert "placeholder_stub_diagnostics" not in ctx.document.metadata


def test_tiny_doc_guard_prevents_firing():
    """Fewer than 10 non-empty lines — do not evaluate."""
    paragraphs = ["Line with [Placeholder One] and [Placeholder Two] and [Placeholder Three]."]
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is False
    assert "too few nonempty lines" in diag.get("suppressed_reason", "")


# ── Diagnostic completeness ────────────────────────────────────────────────

def test_diagnostics_always_populated_when_eligible():
    """Even when the detector does not fire, diagnostics record the counts."""
    paragraphs = ["Perfectly clean prose with no placeholder patterns."] * 15
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is False
    assert diag["bracket_count"] == 0
    assert diag["extraction_stub_count"] == 0
    assert diag["refusal_count"] == 0
    assert diag["underscore_run_count"] == 0
    assert diag["nonempty_lines"] >= 10
    assert diag["warning_maturity"] == "experimental"


def test_warning_message_carries_lower_bound_framing():
    """The lower-bound framing must appear in the emitted warning message."""
    paragraphs = ["[Image omitted]"] + ["Body content."] * 15
    ctx = _make_doc(paragraphs)
    PlaceholderStubValidator().execute(ctx)
    warnings = [i for i in ctx.validation.issues if i.code == "W_PLACEHOLDER_STUB"]
    assert len(warnings) == 1
    assert "LOWER BOUND" in warnings[0].message or "lower bound" in warnings[0].message.lower()


# ── Integration with P0.5 corpus ───────────────────────────────────────────

def test_placeholder_stubs_fixture_positive_fires_the_detector():
    """P0.5 seed positive fixture must fire W_PLACEHOLDER_STUB."""
    from benchmarks.substance_calibration.corpus import (
        list_fixture_class,
    )
    entries = [
        e for e in list_fixture_class("placeholder_stubs")
        if not e.is_negative
    ]
    assert entries, "expected at least one positive placeholder_stubs fixture"
    # Use the first positive fixture as the smoke test.
    entry = entries[0]
    paragraphs = _fixture_paragraphs(entry.markdown_path)
    ctx = _make_doc(paragraphs, file_type="md")
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is True, (
        f"positive fixture {entry.document_id} did not fire the detector; "
        f"diagnostics: {diag}"
    )


def test_placeholder_stubs_fixture_negative_does_not_fire_the_detector():
    """P0.5 seed negative fixture must NOT fire W_PLACEHOLDER_STUB."""
    from benchmarks.substance_calibration.corpus import list_fixture_class
    entries = [
        e for e in list_fixture_class("placeholder_stubs")
        if e.is_negative
    ]
    assert entries, "expected at least one negative placeholder_stubs fixture"
    entry = entries[0]
    paragraphs = _fixture_paragraphs(entry.markdown_path)
    ctx = _make_doc(paragraphs, file_type="md")
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["warned"] is False, (
        f"negative fixture {entry.document_id} incorrectly fired the detector; "
        f"diagnostics: {diag}"
    )
