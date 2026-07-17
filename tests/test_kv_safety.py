"""Tests for the KV heuristic safety milestone (kv_promoter/v2).

Covers:
- Feature gating via KeyValueDetectionProfile
- Exclusion classifier categories
- Positive-evidence rules
- Adjacent alternating-block promotion (Strategy 2)
- Serialization and structural QA
"""
from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.models.key_value import KeyValueGroupType
from aksharamd.plugins.transformers.key_value_promoter import (
    DETECTOR_VERSION,
    _parse_alternating_blocks,
    detect_and_promote_key_value_groups,
)
from aksharamd.scoring.key_value_classifier import (
    _is_academic_definition,
    _is_citation,
    _is_configuration,
    _is_dialogue,
    _is_financial_footnote,
    _is_legal_clause,
    _is_medical_section,
    _is_numbered_list,
    _is_section_label,
    classify_kv_candidates,
)
from aksharamd.scoring.key_value_config import (
    KeyValueDetectionProfile,
)
from aksharamd.scoring.key_value_detection import (
    KeyValueCandidate,
    _infer_value_type,
    _normalize_key,
    detect_key_value_entries,
)


def _para(content: str, index: int, page: int = 1) -> Block:
    return Block(type=BlockType.PARAGRAPH, content=content, index=index, page=page)


def _cands(pairs: list[tuple[str, str]]) -> list[KeyValueCandidate]:
    return [KeyValueCandidate(k, v, f"{k}: {v}") for k, v in pairs]


def _classify(pairs):
    return classify_kv_candidates(_cands(pairs), _infer_value_type, _normalize_key)


# ── Feature gating ────────────────────────────────────────────────────────────

def test_default_profile_disables_heuristics():
    p = KeyValueDetectionProfile()
    assert p.enable_inline_heuristic is False
    assert p.enable_adjacent_heuristic is False
    assert p.enable_native_html is True
    assert p.enable_native_docx is True
    assert p.enable_native_xlsx is True


def test_experimental_profile_enables_heuristics():
    p = KeyValueDetectionProfile.experimental()
    assert p.enable_inline_heuristic is True
    assert p.enable_adjacent_heuristic is True


def test_native_only_profile_matches_default():
    p = KeyValueDetectionProfile.native_only()
    d = KeyValueDetectionProfile()
    assert p.model_dump() == d.model_dump()


def test_detector_version_is_v2():
    assert DETECTOR_VERSION == "kv_promoter/v2"


def test_default_promoter_leaves_paragraphs_unchanged():
    text = "Email: alice@example.com\nPhone: 555-1234"
    blocks = [_para(text, 0)]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(ctx)
    assert all(b.type == BlockType.PARAGRAPH for b in result.document.blocks)


def test_experimental_promoter_promotes_valid_kv():
    text = "Email: alice@example.com\nPhone: 555-1234"
    blocks = [_para(text, 0)]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    kinds = [b.type for b in result.document.blocks]
    assert BlockType.KEY_VALUE_GROUP in kinds


def test_inline_disabled_emits_diagnostic():
    """With heuristics disabled but diagnostics on, the candidate paragraph
    remains but W_KEY_VALUE_STRUCTURE_POSSIBLE is emitted."""
    text = "Email: alice@example.com\nPhone: 555-1234"
    blocks = [_para(text, 0)]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(ctx)
    codes = [i.code for i in result.validation.issues]
    assert "W_KEY_VALUE_STRUCTURE_POSSIBLE" in codes
    # Block was NOT mutated
    assert result.document.blocks[0].type == BlockType.PARAGRAPH


def test_adjacent_disabled_preserves_blocks():
    """Adjacent heuristic off — alternating blocks stay unchanged."""
    blocks = [
        _para("Email:", 0),
        _para("alice@example.com", 1),
        _para("Phone:", 2),
        _para("555-1234", 3),
    ]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(ctx)
    assert all(b.type == BlockType.PARAGRAPH for b in result.document.blocks)


def test_context_kv_profile_field_present():
    ctx = CompilationContext(source="s")
    assert hasattr(ctx, "kv_profile")
    assert ctx.kv_profile is None


def test_context_kv_profile_takes_effect_via_ctx():
    """Setting ctx.kv_profile without an explicit profile arg should enable
    heuristics."""
    text = "Email: alice@example.com\nPhone: 555-1234"
    blocks = [_para(text, 0)]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    ctx.kv_profile = KeyValueDetectionProfile.experimental()
    result = detect_and_promote_key_value_groups(ctx)
    kinds = [b.type for b in result.document.blocks]
    assert BlockType.KEY_VALUE_GROUP in kinds


# ── Exclusion categories ──────────────────────────────────────────────────────

def test_exclusion_dialogue():
    a = _classify([("Alice", "Hello there."), ("Bob", "Good morning.")])
    assert "dialogue" in a.exclusion_categories
    assert a.promotion_decision == "reject"


def test_exclusion_configuration():
    a = _classify([("debug", "false"), ("log_level", "info"), ("port", "8080")])
    assert "configuration" in a.exclusion_categories
    assert a.promotion_decision == "reject"


def test_exclusion_citation():
    a = _classify([("Smith", "2023"), ("Jones", "2021")])
    assert "citation" in a.exclusion_categories


def test_exclusion_section_label():
    a = _classify([("Section 1", "Applicability"), ("Section 2", "Definitions")])
    assert "section_label" in a.exclusion_categories


def test_exclusion_numbered_list():
    a = _classify([("1", "First"), ("2", "Second"), ("3", "Third")])
    assert "numbered_list" in a.exclusion_categories


def test_exclusion_legal_clause():
    a = _classify([("Clause 1", "Terms"), ("Clause 2", "Conditions")])
    assert "legal_clause" in a.exclusion_categories


def test_exclusion_academic_definition():
    a = _classify([
        ("Entropy", "A measure of disorder"),
        ("Enthalpy", "A measure of heat content"),
    ])
    assert "academic_definition" in a.exclusion_categories


def test_exclusion_medical_section():
    a = _classify([
        ("Impression", "Unremarkable"),
        ("Findings", "Normal"),
    ])
    assert "medical_section" in a.exclusion_categories


def test_exclusion_financial_footnote():
    a = _classify([("(1)", "Audited"), ("(2)", "Restated")])
    assert "financial_footnote" in a.exclusion_categories


def test_exclusion_short_dialogue_names():
    a = _classify([
        ("Alice", "hi"), ("Bob", "hey"), ("Carol", "yo."),
    ])
    # 1 sentence-ending is not enough on its own; need 2 name_like + 1 sentence.
    assert (
        "dialogue" in a.exclusion_categories
        or a.promotion_decision == "reject"
    )


def test_exclusion_overrides_positive_evidence_dialogue():
    """Even with a strongly-typed email, a dialogue pattern must reject."""
    a = _classify([
        ("Alice", "call me at alice@example.com."),
        ("Bob", "sure thing."),
        ("Carol", "great."),
    ])
    assert a.promotion_decision == "reject"


def test_assessment_carries_exclusion_categories():
    a = _classify([("debug", "false"), ("port", "8080")])
    assert isinstance(a.exclusion_categories, list)
    assert a.rejection_reason and "exclusion_category" in a.rejection_reason


def test_assessment_carries_rejection_reason():
    a = _classify([("K1", "v1")])
    assert a.rejection_reason == "insufficient_candidates"


def test_config_preserved_as_paragraph():
    text = "debug: false\nlog_level: info\nport: 8080"
    blocks = [_para(text, 0)]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    assert all(b.type == BlockType.PARAGRAPH for b in result.document.blocks)


def test_medical_section_two_findings():
    """_is_medical_section direct check."""
    cands = _cands([("Impression", "OK"), ("Findings", "Normal")])
    assert _is_medical_section(cands)


def test_dialogue_direct_check():
    cands = _cands([
        ("Alice", "Hello there."),
        ("Bob", "Good morning."),
    ])
    assert _is_dialogue(cands)


def test_configuration_direct_check():
    cands = _cands([
        ("debug", "false"), ("timeout", "30"), ("port", "8080"),
    ])
    assert _is_configuration(cands)


def test_citation_direct_check():
    cands = _cands([("Smith", "2023"), ("Jones", "2021")])
    assert _is_citation(cands)


def test_section_label_direct_check():
    cands = _cands([("Section 1", "A"), ("Section 2", "B")])
    assert _is_section_label(cands)


def test_academic_definition_direct_check():
    cands = _cands([
        ("Entropy", "A measure of disorder"),
        ("Enthalpy", "An enthalpy value"),
    ])
    assert _is_academic_definition(cands)


def test_financial_footnote_direct_check():
    cands = _cands([("(1)", "Audited"), ("(2)", "Restated")])
    assert _is_financial_footnote(cands)


def test_legal_clause_direct_check():
    cands = _cands([("Clause 1", "A"), ("Clause 2", "B")])
    assert _is_legal_clause(cands)


def test_numbered_list_direct_check():
    cands = _cands([("1", "x"), ("2", "y"), ("3", "z")])
    assert _is_numbered_list(cands)


# ── Positive evidence rules ───────────────────────────────────────────────────

def test_rule_a_two_emails():
    a = _classify([
        ("Sales", "sales@a.com"), ("Support", "help@b.com"),
    ])
    assert a.promotion_decision == "promote"
    assert a.strongly_typed_entries >= 2


def test_rule_a_email_and_phone():
    a = _classify([
        ("Email", "alice@example.com"), ("Phone", "555-1234"),
    ])
    assert a.promotion_decision == "promote"


def test_rule_a_two_dates():
    a = _classify([
        ("Start", "01/06/2024"), ("End", "05/06/2024"),
    ])
    assert a.promotion_decision == "promote"


def test_rule_a_email_and_url():
    a = _classify([
        ("Support", "help@example.com"),
        ("Docs", "https://example.com"),
    ])
    assert a.promotion_decision == "promote"


def test_rule_b_three_metadata_fields():
    a = _classify([
        ("Title", "Report"), ("Author", "Kalyan"), ("Version", "1.0"),
    ])
    assert a.promotion_decision == "promote"
    assert a.inferred_group_type == KeyValueGroupType.METADATA


def test_rule_b_three_schedule_fields():
    a = _classify([
        ("Monday", "9am"), ("Tuesday", "9am"), ("Wednesday", "9am"),
    ])
    assert a.promotion_decision == "promote"


def test_rule_b_three_spec_fields():
    a = _classify([
        ("Model", "XR-500"), ("Manufacturer", "TechCo"), ("Warranty", "2 years"),
    ])
    assert a.promotion_decision == "promote"


def test_soft_rule_email_plus_two_schema():
    a = _classify([
        ("Name", "Alice"),
        ("Email", "alice@example.com"),
        ("Company", "Acme"),
    ])
    assert a.promotion_decision == "promote"


def test_unknown_labels_and_values_not_promoted():
    a = _classify([("Foo", "bar"), ("Baz", "qux")])
    assert a.promotion_decision == "reject"
    assert a.rejection_reason == "insufficient_positive_evidence"


def test_two_entries_no_typing_no_schema():
    a = _classify([("Category", "wonders"), ("Item", "thingy")])
    assert a.promotion_decision == "reject"


# ── Adjacent alternating-block cases ──────────────────────────────────────────

def test_alternating_promoted_via_strategy_2():
    blocks = [
        _para("Email:", 0),
        _para("alice@example.com", 1),
        _para("Phone:", 2),
        _para("555-1234", 3),
    ]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    kinds = [b.type for b in result.document.blocks]
    assert BlockType.KEY_VALUE_GROUP in kinds


def test_abergowrie_alternating_promoted():
    blocks = [
        _para("Location:", 0),
        _para("Abergowrie", 1),
        _para("Day:", 2),
        _para("Sunday", 3),
        _para("Time:", 4),
        _para("9:00 AM", 5),
    ]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    kinds = [b.type for b in result.document.blocks]
    assert BlockType.KEY_VALUE_GROUP in kinds


def test_dialogue_alternating_rejected():
    blocks = [
        _para("Alice:", 0),
        _para("Hello.", 1),
        _para("Bob:", 2),
        _para("Good morning.", 3),
    ]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    assert all(b.type == BlockType.PARAGRAPH for b in result.document.blocks)


def test_section_alternating_rejected():
    blocks = [
        _para("Section 1:", 0),
        _para("Applicability", 1),
        _para("Section 2:", 2),
        _para("Definitions", 3),
    ]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    assert all(b.type == BlockType.PARAGRAPH for b in result.document.blocks)


def test_config_alternating_rejected():
    blocks = [
        _para("debug:", 0),
        _para("false", 1),
        _para("log_level:", 2),
        _para("info", 3),
        _para("port:", 4),
        _para("8080", 5),
    ]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    assert all(b.type == BlockType.PARAGRAPH for b in result.document.blocks)


def test_short_alternating_run_not_promoted():
    """Only 2 blocks — below adjacent min of 4."""
    blocks = [_para("Email:", 0), _para("alice@example.com", 1)]
    doc = Document(source="s", blocks=blocks, metadata={})
    ctx = CompilationContext(source="s", document=doc)
    result = detect_and_promote_key_value_groups(
        ctx, profile=KeyValueDetectionProfile.experimental()
    )
    assert all(b.type == BlockType.PARAGRAPH for b in result.document.blocks)


def test_parse_alternating_blocks_pairs_correctly():
    """_parse_alternating_blocks matches values to correct keys — no shift."""
    run = [
        _para("Email:", 0),
        _para("alice@example.com", 1),
        _para("Phone:", 2),
        _para("555-1234", 3),
    ]
    candidates = _parse_alternating_blocks(run)
    assert len(candidates) == 2
    assert candidates[0].key == "Email"
    assert candidates[0].value == "alice@example.com"
    assert candidates[1].key == "Phone"
    assert candidates[1].value == "555-1234"


def test_parse_alternating_blocks_rejects_when_value_has_colon():
    """A block containing a colon is not treated as a plain value."""
    run = [
        _para("Email:", 0),
        _para("alice@example.com: primary", 1),
    ]
    candidates = _parse_alternating_blocks(run)
    assert len(candidates) == 0


# ── Serialization and QA ──────────────────────────────────────────────────────

def test_abergowrie_tsv_includes_both_records():
    from aksharamd.renderers.key_value_markdown import render_key_value_tsv
    text = (
        "Location: Abergowrie\nDay: Saturday\nTime: 7:00 PM\n"
        "Location: Abergowrie\nDay: Sunday\nTime: 9:00 AM"
    )
    result = detect_key_value_entries(
        text, page=1, profile=KeyValueDetectionProfile.experimental()
    )
    assert result.group is not None
    tsv = render_key_value_tsv(result.group)
    assert "Saturday" in tsv and "Sunday" in tsv
    assert "7:00 PM" in tsv and "9:00 AM" in tsv
    assert "[Record 2]" in tsv


def test_qa_saturday_time_answerable_via_tsv():
    from benchmarks.kv_eval.repeated_record_qa import run_qa_comparison
    results = run_qa_comparison()
    saturday = [r for r in results if "Saturday" in r.question and r.format == "tsv"]
    assert saturday and saturday[0].correct
    assert not saturday[0].wrong_record


def test_qa_sunday_time_answerable_via_tsv():
    from benchmarks.kv_eval.repeated_record_qa import run_qa_comparison
    results = run_qa_comparison()
    sunday = [r for r in results if "Sunday" in r.question and r.format == "tsv"]
    assert sunday and sunday[0].correct
    assert not sunday[0].wrong_record


def test_qa_prose_conflates_record_boundaries():
    """Raw prose does NOT scope the record window well — wrong-record
    leakage should be flagged for all questions in the prose format."""
    from benchmarks.kv_eval.repeated_record_qa import (
        run_qa_comparison,
        summarize_qa_results,
    )
    results = run_qa_comparison()
    summary = summarize_qa_results(results)
    assert summary["prose"]["wrong_record_rate"] > 0.0
    assert summary["tsv"]["wrong_record_rate"] == 0.0
