"""Tests for PlaceholderStubValidator — W_PLACEHOLDER_STUB (P1.1 + P4)."""
from __future__ import annotations

import json
from pathlib import Path

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.plugins.validators.placeholder_stub import (
    _CATALOG_DIR,
    BRACKET_PLACEHOLDER_RE,
    UNDERSCORE_RUN_RE,
    PlaceholderStubValidator,
    _effective_stub_catalog,
    _load_stub_catalog,
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


# ── P4: parser-stub catalog loading ────────────────────────────────────────

_KNOWN_PARSERS = ("common", "reference", "marker", "docling", "markitdown", "mineru")


def test_all_ship_time_catalogs_exist():
    for parser_id in _KNOWN_PARSERS:
        path = _CATALOG_DIR / f"{parser_id}.json"
        assert path.is_file(), f"missing shipped catalog: {path}"


def test_all_ship_time_catalogs_parse_and_have_required_keys():
    for parser_id in _KNOWN_PARSERS:
        path = _CATALOG_DIR / f"{parser_id}.json"
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["parser_id"] == parser_id, (
            f"{parser_id}.json declares parser_id={payload.get('parser_id')!r}"
        )
        assert "stubs" in payload
        assert isinstance(payload["stubs"], list)


def test_common_catalog_has_nonempty_stubs():
    """The common baseline must ship with real stub patterns."""
    stubs = _load_stub_catalog("common")
    assert len(stubs) >= 5, f"common catalog too sparse: {stubs}"


def test_load_stub_catalog_returns_lowercase_patterns():
    """Patterns are normalized to lowercase for case-insensitive matching."""
    stubs = _load_stub_catalog("common")
    for pattern in stubs:
        assert pattern == pattern.lower(), f"pattern not lowercased: {pattern!r}"


def test_load_unknown_parser_returns_empty():
    assert _load_stub_catalog("does_not_exist_parser") == ()


def test_load_empty_parser_id_returns_empty():
    assert _load_stub_catalog("") == ()


def test_effective_catalog_includes_common_baseline():
    effective = _effective_stub_catalog()
    common = _load_stub_catalog("common")
    for pattern in common:
        assert pattern in effective


def test_effective_catalog_merges_parser_specific():
    """Marker's catalog adds parser-specific stubs on top of common."""
    common = _load_stub_catalog("common")
    marker = _load_stub_catalog("marker")
    effective = _effective_stub_catalog("marker")
    for pattern in common:
        assert pattern in effective
    for pattern in marker:
        assert pattern in effective


def test_effective_catalog_dedupes_overlap():
    """If a pattern appears in both common and parser-specific, it appears once."""
    common = _load_stub_catalog("common")
    # Pick any common pattern; it must appear in effective exactly once even
    # if a parser-specific catalog happens to duplicate it.
    effective = _effective_stub_catalog("markitdown")
    for pattern in common:
        assert effective.count(pattern) == 1, (
            f"pattern {pattern!r} duplicated in effective catalog"
        )


def test_effective_catalog_unknown_parser_uses_only_common():
    effective_unknown = _effective_stub_catalog("does_not_exist_parser")
    effective_default = _effective_stub_catalog()
    assert effective_unknown == effective_default


def test_diagnostics_expose_parser_id_and_catalog_size():
    """P4: the detector records which catalog resolved for this document."""
    paragraphs = [
        "Line one paragraph with real content and no stub markers.",
        "Line two paragraph with real content.",
    ] * 8
    ctx = _make_doc(paragraphs)
    ctx.parser_name = "marker"
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["parser_id"] == "marker"
    assert diag["catalog_size"] >= len(_load_stub_catalog("common"))


def test_diagnostics_parser_id_defaults_to_unknown_when_ctx_missing_name():
    paragraphs = ["Line."] * 15
    ctx = _make_doc(paragraphs)
    # ctx.parser_name defaults to None on CompilationContext.
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["parser_id"] == "unknown"


def test_parser_specific_stub_fires_on_parser_specific_content():
    """Marker's `![Image]` pattern fires when marker is the parser."""
    marker_stubs = _load_stub_catalog("marker")
    if not marker_stubs:
        # Marker catalog is currently non-empty by design; skip if that ever changes.
        return
    stub_literal = marker_stubs[0]
    paragraphs = [
        f"Body paragraph mentions {stub_literal} inline.",
    ] + ["More body content."] * 12
    ctx = _make_doc(paragraphs)
    ctx.parser_name = "marker"
    PlaceholderStubValidator().execute(ctx)
    diag = ctx.document.metadata["placeholder_stub_diagnostics"]
    assert diag["extraction_stub_count"] >= 1
    assert diag["warned"] is True
