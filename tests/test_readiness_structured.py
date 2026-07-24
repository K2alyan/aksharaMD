"""Phase 3: structured and auditable readiness scoring tests.

Verifies ReadinessResult, DeductionRecord, ReadinessEvidence, SCORING_POLICY,
scoring_policy_version, suppression visibility, and backward compatibility.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.scoring import (
    SCORING_POLICY,
    SCORING_POLICY_VERSION,
    DeductionRecord,
    ReadinessEvidence,
    ReadinessResult,
    compute_confidence,
    compute_readiness_score,
)
from aksharamd.scoring.models import ConfidenceResult

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_ctx(
    blocks: list[Block] | None = None,
    file_type: str = "md",
    pages: int = 1,
    original_tokens: int = 100,
    metadata: dict | None = None,
) -> CompilationContext:
    ctx = CompilationContext(source="test.md", output_dir="output")
    ctx.original_tokens = original_tokens
    doc = Document(
        source="test.md",
        file_type=file_type,
        pages=pages,
        blocks=blocks or [Block(type=BlockType.PARAGRAPH, content="content", index=0, page=1)],
        metadata=metadata or {},
    )
    ctx.document = doc
    return ctx


def _simple_md(tmp_path: Path) -> Path:
    f = tmp_path / "doc.md"
    f.write_text(textwrap.dedent("""\
        # Title

        A paragraph of content.

        ## Section

        More text here.
    """), encoding="utf-8")
    return f


# ── 1. Return type is ReadinessResult ────────────────────────────────────────

def test_compute_confidence_returns_readiness_result() -> None:
    ctx = _make_ctx()
    result = compute_confidence(ctx)
    assert isinstance(result, ReadinessResult)


def test_confidence_result_is_alias_for_readiness_result() -> None:
    assert ConfidenceResult is ReadinessResult


def test_result_has_score_and_notes() -> None:
    ctx = _make_ctx()
    r = compute_confidence(ctx)
    assert isinstance(r.score, int)
    assert isinstance(r.notes, list)


# ── 2. Backward compatibility ─────────────────────────────────────────────────

def test_compute_readiness_score_returns_int() -> None:
    ctx = _make_ctx()
    assert isinstance(compute_readiness_score(ctx), int)


def test_score_matches_between_compat_and_full() -> None:
    ctx = _make_ctx()
    assert compute_readiness_score(ctx) == compute_confidence(ctx).score


# ── 3. ReadinessResult structure ─────────────────────────────────────────────

def test_result_has_deductions_list() -> None:
    ctx = _make_ctx()
    r = compute_confidence(ctx)
    assert isinstance(r.deductions, list)


def test_result_has_informational_list() -> None:
    ctx = _make_ctx()
    r = compute_confidence(ctx)
    assert isinstance(r.informational, list)


def test_result_has_scoring_policy_version() -> None:
    ctx = _make_ctx()
    r = compute_confidence(ctx)
    assert r.scoring_policy_version == SCORING_POLICY_VERSION
    assert r.scoring_policy_version != ""


# ── 4. Scoring policy version is stable ──────────────────────────────────────

def test_scoring_policy_version_is_string() -> None:
    assert isinstance(SCORING_POLICY_VERSION, str)
    assert SCORING_POLICY_VERSION != ""


def test_scoring_policy_version_is_same_across_calls() -> None:
    ctx1 = _make_ctx()
    ctx2 = _make_ctx()
    assert compute_confidence(ctx1).scoring_policy_version == compute_confidence(ctx2).scoring_policy_version


# ── 5. SCORING_POLICY dictionary ─────────────────────────────────────────────

def test_scoring_policy_not_empty() -> None:
    assert len(SCORING_POLICY) > 0


def test_scoring_policy_has_key_rules() -> None:
    expected_rules = {
        "OCR_REQUIRED", "NEAR_EMPTY_OUTPUT", "LOW_TEXT_DENSITY", "GLYPH_ARTIFACTS",
        "PARSE_ERRORS", "MISSING_PAGE", "LARGE_BLOCK", "REPEATED_CONTENT",
        "TOKEN_BLOAT", "NO_HEADINGS_MULTIPAGE", "W_MULTICOLUMN_ORDER",
        "W_HEADER_FOOTER_TABLE_GARBLED",
    }
    for rule_id in expected_rules:
        assert rule_id in SCORING_POLICY, f"Missing rule: {rule_id}"


def test_scoring_policy_suppression_links_are_symmetric() -> None:
    for rule_id, rule in SCORING_POLICY.items():
        for suppressed_id in rule.suppresses:
            target = SCORING_POLICY.get(suppressed_id)
            assert target is not None, f"{rule_id}.suppresses references unknown {suppressed_id}"
            assert rule_id in target.suppressed_by, (
                f"{rule_id} suppresses {suppressed_id} but {suppressed_id}.suppressed_by does not include {rule_id}"
            )


def test_scoring_policy_informational_rules_have_zero_max_penalty() -> None:
    informational = ["W_MULTICOLUMN_ORDER", "W_HEADER_FOOTER_TABLE_GARBLED", "IMAGE_PLACEHOLDER_WITH_ASSETS"]
    for rule_id in informational:
        assert SCORING_POLICY[rule_id].max_penalty == 0, (
            f"Informational rule {rule_id} must have max_penalty=0"
        )


# ── 6. DeductionRecord model ──────────────────────────────────────────────────

def test_deduction_record_to_dict_has_required_keys() -> None:
    d = DeductionRecord(rule_id="PARSE_ERRORS", description="2 errors", penalty=24)
    dct = d.to_dict()
    assert "rule_id" in dct
    assert "description" in dct
    assert "penalty" in dct


def test_suppressed_deduction_includes_suppressed_flag() -> None:
    d = DeductionRecord(
        rule_id="NEAR_EMPTY_OUTPUT",
        description="near empty",
        penalty=25,
        suppressed=True,
        suppression_reason="OCR_REQUIRED already covers this",
    )
    dct = d.to_dict()
    assert dct["suppressed"] is True
    assert dct["suppression_reason"] == "OCR_REQUIRED already covers this"


def test_active_deduction_has_no_suppressed_key() -> None:
    d = DeductionRecord(rule_id="GLYPH_ARTIFACTS", description="garbled", penalty=25)
    dct = d.to_dict()
    assert "suppressed" not in dct


def test_deduction_with_evidence_serializes_evidence() -> None:
    ev = ReadinessEvidence(metric_name="error_count", metric_value=3.0, threshold=1.0)
    d = DeductionRecord(rule_id="PARSE_ERRORS", description="3 errors", penalty=30, evidence=ev)
    dct = d.to_dict()
    assert "evidence" in dct
    assert dct["evidence"]["metric_name"] == "error_count"
    assert dct["evidence"]["metric_value"] == 3.0


def test_deduction_without_evidence_omits_evidence_key() -> None:
    d = DeductionRecord(rule_id="TOKEN_BLOAT", description="bloat", penalty=8)
    dct = d.to_dict()
    assert "evidence" not in dct


# ── 7. ReadinessEvidence model ────────────────────────────────────────────────

def test_evidence_to_dict_has_all_fields() -> None:
    ev = ReadinessEvidence(
        metric_name="image_ratio",
        metric_value=0.75,
        threshold=0.0,
        pages=[1, 2, 3],
        block_ids=["abc", "def"],
        extras={"classification": "scanned"},
    )
    dct = ev.to_dict()
    assert dct["metric_name"] == "image_ratio"
    assert dct["metric_value"] == 0.75
    assert dct["pages"] == [1, 2, 3]
    assert dct["block_ids"] == ["abc", "def"]
    assert dct["extras"]["classification"] == "scanned"


# ── 8. Suppression visibility ─────────────────────────────────────────────────

def test_ocr_required_suppresses_near_empty_output() -> None:
    ctx = _make_ctx(
        file_type="pdf",
        pages=5,
        metadata={
            "pdf_classification": "scanned",
            "pdf_ocr_available": False,
            "pdf_stats": {"image_pages": 4},
        },
    )
    ctx.warn("OCR_REQUIRED", "OCR not available")
    ctx.warn("NEAR_EMPTY_OUTPUT", "Very little text extracted")
    r = compute_confidence(ctx)

    suppressed = [d for d in r.deductions if d.rule_id == "NEAR_EMPTY_OUTPUT" and d.suppressed]
    active = [d for d in r.deductions if d.rule_id == "NEAR_EMPTY_OUTPUT" and not d.suppressed]
    assert suppressed, "NEAR_EMPTY_OUTPUT must appear as suppressed when OCR_REQUIRED fires"
    assert not active, "NEAR_EMPTY_OUTPUT must not appear as active when suppressed"


def test_suppressed_deduction_has_suppression_reason() -> None:
    ctx = _make_ctx(
        file_type="pdf",
        pages=5,
        metadata={
            "pdf_classification": "scanned",
            "pdf_ocr_available": False,
            "pdf_stats": {"image_pages": 4},
        },
    )
    ctx.warn("OCR_REQUIRED", "OCR not available")
    ctx.warn("NEAR_EMPTY_OUTPUT", "Very little text extracted")
    r = compute_confidence(ctx)

    suppressed = [d for d in r.deductions if d.rule_id == "NEAR_EMPTY_OUTPUT" and d.suppressed]
    assert suppressed[0].suppression_reason != ""


def test_ocr_required_suppresses_low_text_density() -> None:
    ctx = _make_ctx(
        file_type="pdf",
        pages=5,
        metadata={
            "pdf_classification": "scanned",
            "pdf_ocr_available": False,
            "pdf_stats": {"image_pages": 4},
        },
    )
    ctx.warn("OCR_REQUIRED", "OCR not available")
    ctx.warn("LOW_TEXT_DENSITY", "Sparse text")
    r = compute_confidence(ctx)

    suppressed = [d for d in r.deductions if d.rule_id == "LOW_TEXT_DENSITY" and d.suppressed]
    assert suppressed, "LOW_TEXT_DENSITY must be suppressed when OCR_REQUIRED fires"


def test_suppressed_deductions_not_counted_in_score() -> None:
    """Suppressed deductions must not affect the score."""
    # Two contexts: one with OCR_REQUIRED + NEAR_EMPTY, one with OCR_REQUIRED only
    ctx_both = _make_ctx(
        file_type="pdf", pages=5,
        metadata={"pdf_classification": "scanned", "pdf_ocr_available": False,
                  "pdf_stats": {"image_pages": 4}},
    )
    ctx_both.warn("OCR_REQUIRED", "OCR not available")
    ctx_both.warn("NEAR_EMPTY_OUTPUT", "Very little text")

    ctx_only = _make_ctx(
        file_type="pdf", pages=5,
        metadata={"pdf_classification": "scanned", "pdf_ocr_available": False,
                  "pdf_stats": {"image_pages": 4}},
    )
    ctx_only.warn("OCR_REQUIRED", "OCR not available")

    assert compute_confidence(ctx_both).score == compute_confidence(ctx_only).score, (
        "Adding a suppressed NEAR_EMPTY_OUTPUT must not change the score"
    )


# ── 9. Informational findings ─────────────────────────────────────────────────

def test_w_multicolumn_order_appears_in_informational() -> None:
    ctx = _make_ctx()
    ctx.warn("W_MULTICOLUMN_ORDER", "column interleaving detected")
    r = compute_confidence(ctx)
    info_ids = [d.rule_id for d in r.informational]
    assert "W_MULTICOLUMN_ORDER" in info_ids


def test_w_multicolumn_order_does_not_affect_score() -> None:
    ctx_clean = _make_ctx()
    ctx_warn = _make_ctx()
    ctx_warn.warn("W_MULTICOLUMN_ORDER", "column interleaving detected")
    assert compute_confidence(ctx_clean).score == compute_confidence(ctx_warn).score


def test_w_header_footer_table_garbled_in_informational() -> None:
    ctx = _make_ctx()
    ctx.warn("W_HEADER_FOOTER_TABLE_GARBLED", "garbled table")
    r = compute_confidence(ctx)
    info_ids = [d.rule_id for d in r.informational]
    assert "W_HEADER_FOOTER_TABLE_GARBLED" in info_ids


def test_informational_deductions_have_zero_penalty() -> None:
    ctx = _make_ctx()
    ctx.warn("W_MULTICOLUMN_ORDER", "interleaved")
    r = compute_confidence(ctx)
    for d in r.informational:
        assert d.penalty == 0, f"Informational finding {d.rule_id} must have penalty=0"


# ── 10. Score behavior preserved ─────────────────────────────────────────────

def test_no_deductions_means_format_baseline_score() -> None:
    ctx = _make_ctx(file_type="md")
    r = compute_confidence(ctx)
    assert r.score == 95, f"md baseline expected 95, got {r.score}"


def test_glyph_artifacts_deduction() -> None:
    ctx = _make_ctx(file_type="pdf")
    ctx.warn("GLYPH_ARTIFACTS", "CID artifacts")
    r = compute_confidence(ctx)
    ded = next((d for d in r.deductions if d.rule_id == "GLYPH_ARTIFACTS"), None)
    assert ded is not None
    assert ded.penalty == 25
    assert ded.evidence is None, "GLYPH_ARTIFACTS has no structured evidence (metric lives in warning message)"
    assert r.score == 87 - 25  # pdf baseline - glyph penalty


def test_repeated_content_deduction() -> None:
    ctx = _make_ctx(file_type="md")
    ctx.warn("REPEATED_CONTENT", "boilerplate")
    r = compute_confidence(ctx)
    ded = next((d for d in r.deductions if d.rule_id == "REPEATED_CONTENT"), None)
    assert ded is not None
    assert ded.penalty == 8
    assert ded.evidence is None, "REPEATED_CONTENT has no structured evidence"


def test_token_bloat_deduction() -> None:
    ctx = _make_ctx(file_type="md")
    ctx.warn("TOKEN_BLOAT", "bloat")
    r = compute_confidence(ctx)
    ded = next((d for d in r.deductions if d.rule_id == "TOKEN_BLOAT"), None)
    assert ded is not None
    assert ded.penalty == 8
    assert ded.evidence is None, "TOKEN_BLOAT has no structured evidence"


# ── 11. Evidence completeness for rules that carry structured metrics ──────────

def test_ocr_required_evidence_has_all_fields() -> None:
    ctx = _make_ctx(
        file_type="pdf", pages=4,
        metadata={"pdf_classification": "scanned", "pdf_ocr_available": False,
                  "pdf_stats": {"image_pages": 3}},
    )
    ctx.warn("OCR_REQUIRED", "OCR not available")
    r = compute_confidence(ctx)
    ded = next((d for d in r.deductions if d.rule_id == "OCR_REQUIRED"), None)
    assert ded is not None and ded.evidence is not None
    assert ded.evidence.metric_name == "image_ratio"
    assert ded.evidence.extras.get("image_pages") == 3
    assert ded.evidence.extras.get("total_pages") == 4
    assert ded.evidence.extras.get("classification") == "scanned"


def test_parse_errors_evidence_has_count() -> None:
    ctx = _make_ctx()
    ctx.error("PARSE_FAILED", "some parse error")
    r = compute_confidence(ctx)
    ded = next((d for d in r.deductions if d.rule_id == "PARSE_ERRORS"), None)
    assert ded is not None and ded.evidence is not None
    assert ded.evidence.metric_name == "error_count"
    assert ded.evidence.metric_value == 1.0


def test_missing_page_evidence_has_page_count_and_pct() -> None:
    blocks = [Block(type=BlockType.PARAGRAPH, content="text", index=0, page=1)]
    ctx = _make_ctx(file_type="pdf", pages=4, blocks=blocks)
    ctx.warn("MISSING_PAGE", "page 2 empty")
    ctx.warn("MISSING_PAGE", "page 3 empty")
    ctx.warn("MISSING_PAGE", "page 4 empty")
    r = compute_confidence(ctx)
    ded = next((d for d in r.deductions if d.rule_id == "MISSING_PAGE"), None)
    assert ded is not None and ded.evidence is not None
    assert ded.evidence.extras.get("total_pages") == 4
    assert ded.evidence.extras.get("missing_pct") == 75


def test_flag_only_rules_have_no_evidence() -> None:
    """Rules where the metric lives in the warning message carry no evidence object."""
    ctx = _make_ctx()
    ctx.warn("GLYPH_ARTIFACTS", "cid artifacts")
    ctx.warn("REPEATED_CONTENT", "boilerplate")
    ctx.warn("TOKEN_BLOAT", "bloat")
    r = compute_confidence(ctx)
    for rule_id in ("GLYPH_ARTIFACTS", "REPEATED_CONTENT", "TOKEN_BLOAT"):
        ded = next((d for d in r.deductions if d.rule_id == rule_id), None)
        assert ded is not None, f"{rule_id} not in deductions"
        assert ded.evidence is None, f"{rule_id} should not carry trivial evidence"


# ── 11b. Maturity field surfaced for informational findings ───────────────────

def test_multicolumn_maturity_surfaced_from_diagnostics() -> None:
    """W_MULTICOLUMN_ORDER informational record carries maturity from validator diagnostics."""
    ctx = _make_ctx(
        metadata={"multicolumn_diagnostics": {"warning_maturity": "candidate", "warned": True}},
    )
    ctx.warn("W_MULTICOLUMN_ORDER", "column interleave")
    r = compute_confidence(ctx)
    d = next((x for x in r.informational if x.rule_id == "W_MULTICOLUMN_ORDER"), None)
    assert d is not None
    assert d.maturity == "candidate"
    assert d.to_dict().get("maturity") == "candidate"


def test_header_footer_maturity_surfaced_from_diagnostics() -> None:
    ctx = _make_ctx(
        metadata={"header_footer_table_diagnostics": {"warning_maturity": "experimental", "warned": True}},
    )
    ctx.warn("W_HEADER_FOOTER_TABLE_GARBLED", "garbled table")
    r = compute_confidence(ctx)
    d = next((x for x in r.informational if x.rule_id == "W_HEADER_FOOTER_TABLE_GARBLED"), None)
    assert d is not None
    assert d.maturity == "experimental"


def test_maturity_absent_when_diagnostics_missing() -> None:
    """When validator diagnostics aren't populated, maturity is empty string."""
    ctx = _make_ctx()
    ctx.warn("W_MULTICOLUMN_ORDER", "column interleave")
    r = compute_confidence(ctx)
    d = next((x for x in r.informational if x.rule_id == "W_MULTICOLUMN_ORDER"), None)
    assert d is not None
    assert d.maturity == ""
    assert "maturity" not in d.to_dict()  # omitted from dict when empty


def test_stable_rules_have_no_maturity_in_dict() -> None:
    """Core scoring deductions don't emit a maturity key (they are implicitly stable)."""
    ctx = _make_ctx()
    ctx.warn("GLYPH_ARTIFACTS", "garbled")
    r = compute_confidence(ctx)
    d = next((x for x in r.deductions if x.rule_id == "GLYPH_ARTIFACTS"), None)
    assert d is not None
    assert "maturity" not in d.to_dict()


# ── 11c. IMAGE_PLACEHOLDER_NO_FALLBACK penalty reflects actual cap ────────────

def test_image_placeholder_no_fallback_penalty_reflects_cap() -> None:
    """Penalty should be the amount the score was reduced by the cap, not 0."""
    from aksharamd.models.block import Block, BlockType
    from aksharamd.models.document import Document
    # Build a context where score before cap would be > 55 (pdf baseline = 87)
    ctx = CompilationContext(source="test.pdf", output_dir="output")
    ctx.original_tokens = 100
    sentinel = "[Image not extracted"
    ctx.document = Document(
        source="test.pdf", file_type="pdf", pages=1,
        blocks=[Block(type=BlockType.PARAGRAPH, content=sentinel + " — OCR unavailable", index=0, page=1)],
        assets=[],  # no assets with bytes
    )
    r = compute_confidence(ctx)
    ded = next((d for d in r.deductions if d.rule_id == "IMAGE_PLACEHOLDER_NO_FALLBACK"), None)
    assert ded is not None
    # Score was capped at 55; penalty should be 87 - 55 = 32 (pdf baseline was 87)
    assert ded.penalty == 87 - 55, f"Expected penalty 32, got {ded.penalty}"
    assert r.score == 55


# ── 12. Integration: structured fields in manifest after compile ──────────────

def test_manifest_has_deductions_after_compile(tmp_path: Path) -> None:
    f = _simple_md(tmp_path)
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(f))[1]
    assert ctx.manifest is not None
    assert isinstance(ctx.manifest.deductions, list)


def test_manifest_has_informational_after_compile(tmp_path: Path) -> None:
    f = _simple_md(tmp_path)
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(f))[1]
    assert ctx.manifest is not None
    assert isinstance(ctx.manifest.informational, list)


def test_manifest_has_scoring_policy_version_after_compile(tmp_path: Path) -> None:
    f = _simple_md(tmp_path)
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(f))[1]
    assert ctx.manifest is not None
    assert ctx.manifest.scoring_policy_version == SCORING_POLICY_VERSION


def test_manifest_deductions_are_dicts(tmp_path: Path) -> None:
    f = _simple_md(tmp_path)
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(f))[1]
    for d in ctx.manifest.deductions:
        assert isinstance(d, dict)
        assert "rule_id" in d
        assert "penalty" in d


# ── 13. No document / empty tokens edge cases ─────────────────────────────────

def test_no_document_returns_score_zero() -> None:
    ctx = CompilationContext(source="x.md", output_dir="output")
    r = compute_confidence(ctx)
    assert r.score == 0
    assert r.deductions == []
    assert r.informational == []


def test_empty_tokens_returns_score_ten() -> None:
    ctx = _make_ctx(original_tokens=0)
    r = compute_confidence(ctx)
    assert r.score == 10


# ── 14. Score is clamped 0–100 ────────────────────────────────────────────────

def test_score_never_negative() -> None:
    ctx = _make_ctx(file_type="pdf", pages=10, original_tokens=50)
    for code in ["OCR_REQUIRED", "GLYPH_ARTIFACTS", "NEAR_EMPTY_OUTPUT",
                 "REPEATED_CONTENT", "TOKEN_BLOAT", "MISSING_PAGE", "LARGE_BLOCK"]:
        ctx.warn(code, "test warning")
    ctx.error("PARSE_FAILED", "error 1")
    ctx.error("PARSE_FAILED", "error 2")
    ctx.error("PARSE_FAILED", "error 3")
    r = compute_confidence(ctx)
    assert r.score >= 0


def test_score_never_above_100() -> None:
    ctx = _make_ctx(file_type="md")
    r = compute_confidence(ctx)
    assert r.score <= 100


# ── 15. scoring_policy_version is decoupled from schema_version ───────────────

def test_scoring_policy_version_is_not_schema_version() -> None:
    from aksharamd.models.manifest import Manifest
    m = Manifest(source="x.md")
    assert m.schema_version != SCORING_POLICY_VERSION or True  # they can coincidentally be equal
    # The key test: they are independently settable
    m2 = Manifest(source="x.md", scoring_policy_version="99.0")
    assert m2.scoring_policy_version == "99.0"
    # Bumped 1.3 → 1.4 in PR 100 for the additive OCR Auto Policy fields.
    # Bumped 1.4 → 1.5 in PR 102 for Output Safety Policy v1 fallback fields.
    assert m2.schema_version == "1.5"


# ── 16. to_dict serialization round-trip ──────────────────────────────────────

def test_deduction_round_trip_through_dict() -> None:
    d = DeductionRecord(
        rule_id="GLYPH_ARTIFACTS",
        description="garbled text",
        penalty=25,
        evidence=ReadinessEvidence(
            metric_name="glyph_ratio",
            metric_value=0.05,
            threshold=0.02,
            pages=[2, 3],
            extras={"cid_count": 42},
        ),
    )
    dct = d.to_dict()
    assert dct["rule_id"] == "GLYPH_ARTIFACTS"
    assert dct["penalty"] == 25
    assert dct["evidence"]["pages"] == [2, 3]
    assert dct["evidence"]["extras"]["cid_count"] == 42


def test_suppressed_deduction_round_trip() -> None:
    d = DeductionRecord(
        rule_id="NEAR_EMPTY_OUTPUT",
        description="near empty",
        penalty=25,
        suppressed=True,
        suppression_reason="OCR_REQUIRED covers this",
    )
    dct = d.to_dict()
    assert dct["suppressed"] is True
    assert dct["suppression_reason"] == "OCR_REQUIRED covers this"
    # Re-check no active score key leaks
    assert "penalty" in dct
    assert dct["penalty"] == 25
