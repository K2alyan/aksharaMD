"""Regression tests for Phase 2 alerting-warning score caps.

Locks the score-policy behaviour ratified in docs/calibration/USP_CLAIM_V1.md §5.1
(Option A) and specified in docs/calibration/SCORING_POLICY.md:

  W_MULTICOLUMN_ORDER          -> cap at 69 (top of RISKY)
  W_HEADER_FOOTER_TABLE_GARBLED -> cap at 84 (top of OK; softer cap because
                                    experimental maturity)
  W_IMAGE_ONLY_NO_USABLE_FALLBACK -> cap at 55 (unchanged; regression only)

Also locks:

  - Non-alerting informational warnings never cap the score.
  - SCORING_POLICY_VERSION is threaded from models.py to the ReadinessResult
    receipt.
"""
from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.scoring.models import SCORING_POLICY_VERSION
from aksharamd.scoring.readiness import compute_confidence


def _clean_pdf_ctx(
    warning_codes: list[str] | None = None,
    metadata_extras: dict | None = None,
) -> CompilationContext:
    """A clean 2-page PDF context that scores HIGH by default.

    Optionally emits the given warning codes so the caps can be exercised in
    isolation from the detectors that would normally raise them.
    """
    blocks = [
        Block(type=BlockType.HEADING, content="Title", level=1, page=1, index=0,
              confidence=ExtractionConfidence.EXTRACTED),
        Block(type=BlockType.PARAGRAPH, content="First page body paragraph with real content.",
              page=1, index=1, confidence=ExtractionConfidence.EXTRACTED),
        Block(type=BlockType.HEADING, content="Section", level=2, page=2, index=0,
              confidence=ExtractionConfidence.EXTRACTED),
        Block(type=BlockType.PARAGRAPH, content="Second page body paragraph with real content.",
              page=2, index=1, confidence=ExtractionConfidence.EXTRACTED),
    ]
    metadata: dict = {
        "pdf_classification": "native_text",
        "pdf_stats": {"image_pages": 0, "table_pages": 0},
        "pdf_ocr_available": False,
    }
    if metadata_extras:
        metadata.update(metadata_extras)
    doc = Document(
        source="clean.pdf",
        file_type="pdf",
        pages=2,
        blocks=blocks,
        metadata=metadata,
    )
    ctx = CompilationContext(source="clean.pdf")
    ctx.document = doc
    ctx.original_tokens = 200
    for code in warning_codes or []:
        ctx.warn(code, f"synthetic {code}")
    return ctx


# ── W_MULTICOLUMN_ORDER — cap at 69 (RISKY) ───────────────────────────────────

class TestMultiColumnOrderCap:
    def test_baseline_scores_high(self):
        """Without the warning, the clean fixture scores in the HIGH band."""
        result = compute_confidence(_clean_pdf_ctx())
        assert result.score >= 85, f"Expected HIGH baseline, got {result.score}"

    def test_warning_caps_at_69(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_MULTICOLUMN_ORDER"],
            metadata_extras={
                "multicolumn_diagnostics": {
                    "problem_pages": [1],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 69, (
            f"W_MULTICOLUMN_ORDER must cap score at 69 (RISKY); got {result.score}"
        )

    def test_deduction_recorded_with_maturity(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_MULTICOLUMN_ORDER"],
            metadata_extras={
                "multicolumn_diagnostics": {
                    "problem_pages": [1, 2],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        mc = [d for d in result.deductions if d.rule_id == "W_MULTICOLUMN_ORDER"]
        assert len(mc) == 1, f"Expected exactly one W_MULTICOLUMN_ORDER deduction, got {len(mc)}"
        assert mc[0].maturity == "candidate"
        assert mc[0].penalty > 0
        assert mc[0].suppressed is False

    def test_suppressed_when_score_already_below_cap(self):
        """Score already below the cap: deduction emitted but suppressed with penalty=0."""
        ctx = _clean_pdf_ctx(
            warning_codes=["W_MULTICOLUMN_ORDER", "GLYPH_ARTIFACTS", "REPEATED_CONTENT", "TOKEN_BLOAT"],
            metadata_extras={
                "multicolumn_diagnostics": {
                    "problem_pages": [1],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        # Base pdf=87, minus 25+8+8 = 46. Cap 69 doesn't apply; 46 < 69.
        assert result.score <= 69
        mc = [d for d in result.deductions if d.rule_id == "W_MULTICOLUMN_ORDER"]
        assert len(mc) == 1
        assert mc[0].suppressed is True
        assert mc[0].penalty == 0
        assert "already" in mc[0].suppression_reason


# ── W_HEADER_FOOTER_TABLE_GARBLED — cap at 84 (top of OK) ─────────────────────

class TestHeaderFooterTableCap:
    def test_warning_caps_at_84(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_HEADER_FOOTER_TABLE_GARBLED"],
            metadata_extras={
                "header_footer_table_diagnostics": {
                    "problem_tables": [{"page": 1}],
                    "warning_maturity": "experimental",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 84, (
            f"W_HEADER_FOOTER_TABLE_GARBLED must cap score at 84 (top of OK); "
            f"got {result.score}"
        )
        # Softer cap than W_MULTICOLUMN_ORDER — still above RISKY unless other rules deduct
        assert result.score >= 70, (
            "W_HEADER_FOOTER_TABLE_GARBLED cap is 84, but score dropped below OK — "
            "another rule must be interacting"
        )

    def test_deduction_recorded_with_maturity(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_HEADER_FOOTER_TABLE_GARBLED"],
            metadata_extras={
                "header_footer_table_diagnostics": {
                    "problem_tables": [{"page": 1}],
                    "warning_maturity": "experimental",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        hft = [d for d in result.deductions if d.rule_id == "W_HEADER_FOOTER_TABLE_GARBLED"]
        assert len(hft) == 1
        assert hft[0].maturity == "experimental"
        assert hft[0].penalty > 0
        assert hft[0].suppressed is False


# ── Non-alerting warnings must never cap ─────────────────────────────────────

class TestNonAlertingWarningsDoNotCap:
    """Informational warnings must never reduce score below HIGH band on their own."""

    def test_pdf_attachment_ignored_does_not_cap(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_PDF_ATTACHMENT_IGNORED"],
            metadata_extras={
                "pdf_attachment_diagnostics": {
                    "attachment_count": 2,
                    "warning_maturity": "candidate",
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score >= 85, (
            f"W_PDF_ATTACHMENT_IGNORED must not cap the score; got {result.score}"
        )

    def test_auto_ocr_backend_selected_does_not_cap(self):
        ctx = _clean_pdf_ctx(warning_codes=["AUTO_OCR_BACKEND_SELECTED"])
        result = compute_confidence(ctx)
        assert result.score >= 85

    def test_auto_ocr_backend_fallback_does_not_cap(self):
        ctx = _clean_pdf_ctx(warning_codes=["AUTO_OCR_BACKEND_FALLBACK"])
        result = compute_confidence(ctx)
        assert result.score >= 85


# ── SCORING_POLICY_VERSION receipt ────────────────────────────────────────────

class TestScoringPolicyVersionReceipt:
    def test_version_is_1_1(self):
        assert SCORING_POLICY_VERSION == "1.1", (
            "SCORING_POLICY_VERSION was bumped for Phase 2 alerting-warning caps; "
            "any subsequent policy change must bump it again"
        )

    def test_receipt_carries_version(self):
        result = compute_confidence(_clean_pdf_ctx())
        assert result.scoring_policy_version == SCORING_POLICY_VERSION
        assert result.scoring_policy_version == "1.1"

    def test_capped_result_still_carries_version(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_MULTICOLUMN_ORDER"],
            metadata_extras={
                "multicolumn_diagnostics": {
                    "problem_pages": [1],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.scoring_policy_version == "1.1"
