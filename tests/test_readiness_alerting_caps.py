"""Regression tests for Phase 2 + Phase 3 + Phase 3.5 alerting-warning score caps.

Locks the score-policy behaviour ratified in docs/calibration/USP_CLAIM_V1.md §5.1
(Option A) and §5.2 (text-only bar), specified in docs/calibration/SCORING_POLICY.md:

  W_MULTICOLUMN_ORDER            -> cap at 69 (top of RISKY)             [Phase 2]
  W_HEADER_FOOTER_TABLE_GARBLED  -> cap at 84 (top of OK; softer cap     [Phase 2]
                                     because experimental maturity)
  W_TABLE_MISSING                -> cap at 69 (top of RISKY)             [Phase 3]
  W_ENCODING_ARTIFACTS           -> cap at 69 (top of RISKY)             [Phase 3]
  W_IMAGE_ONLY_TEXT_BAR_FAIL     -> cap at 69 (top of RISKY)             [Phase 3.5]
  W_TABLE_EXPECTED_NOT_EXTRACTED -> cap at 84 (top of OK; experimental)  [Phase 3.5]
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


# ── W_TABLE_MISSING — cap at 69 (RISKY) ──────────────────────────────────────

class TestTableMissingCap:
    def test_warning_caps_at_69(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_TABLE_MISSING"],
            metadata_extras={
                "table_missing_diagnostics": {
                    "leader_dot_lines": 21,
                    "total_leader_dot_matches": 21,
                    "fired_triggers": ["leader_dot_lines"],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 69, (
            f"W_TABLE_MISSING must cap score at 69 (RISKY); got {result.score}"
        )

    def test_fallback_trigger_also_caps(self):
        """strikeUnderline analogue: 1 line, many total matches."""
        ctx = _clean_pdf_ctx(
            warning_codes=["W_TABLE_MISSING"],
            metadata_extras={
                "table_missing_diagnostics": {
                    "leader_dot_lines": 1,
                    "total_leader_dot_matches": 54,
                    "fired_triggers": ["total_leader_dot_matches"],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 69

    def test_deduction_recorded_with_maturity(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_TABLE_MISSING"],
            metadata_extras={
                "table_missing_diagnostics": {
                    "leader_dot_lines": 5,
                    "total_leader_dot_matches": 12,
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        tm = [d for d in result.deductions if d.rule_id == "W_TABLE_MISSING"]
        assert len(tm) == 1
        assert tm[0].maturity == "candidate"
        assert tm[0].penalty > 0
        assert tm[0].suppressed is False
        # evidence carries both counts
        assert tm[0].evidence is not None
        assert tm[0].evidence.metric_name == "leader_dot_lines"
        assert tm[0].evidence.metric_value == 5.0
        assert tm[0].evidence.extras.get("total_leader_dot_matches") == 12


# ── W_ENCODING_ARTIFACTS — cap at 69 (RISKY) ─────────────────────────────────

class TestEncodingArtifactsCap:
    def test_warning_caps_at_69(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_ENCODING_ARTIFACTS"],
            metadata_extras={
                "encoding_artifacts_diagnostics": {
                    "xml_fragment_count": 4,
                    "mojibake_density": 0.001,
                    "fired_triggers": ["xml_fragment_count"],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 69, (
            f"W_ENCODING_ARTIFACTS must cap score at 69 (RISKY); got {result.score}"
        )

    def test_mojibake_trigger_also_caps(self):
        """Trigger C only: high mojibake density, zero XML fragments."""
        ctx = _clean_pdf_ctx(
            warning_codes=["W_ENCODING_ARTIFACTS"],
            metadata_extras={
                "encoding_artifacts_diagnostics": {
                    "xml_fragment_count": 0,
                    "mojibake_density": 0.012,
                    "fired_triggers": ["mojibake_density"],
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 69

    def test_deduction_recorded_with_maturity(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_ENCODING_ARTIFACTS"],
            metadata_extras={
                "encoding_artifacts_diagnostics": {
                    "xml_fragment_count": 7,
                    "mojibake_density": 0.008,
                    "warning_maturity": "candidate",
                    "warned": True,
                },
            },
        )
        result = compute_confidence(ctx)
        ea = [d for d in result.deductions if d.rule_id == "W_ENCODING_ARTIFACTS"]
        assert len(ea) == 1
        assert ea[0].maturity == "candidate"
        assert ea[0].penalty > 0
        assert ea[0].suppressed is False
        assert ea[0].evidence is not None
        assert ea[0].evidence.metric_name == "xml_fragment_count"
        assert ea[0].evidence.metric_value == 7.0
        assert ea[0].evidence.extras.get("mojibake_density") == 0.008


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
    def test_version_is_1_3(self):
        assert SCORING_POLICY_VERSION == "1.3", (
            "SCORING_POLICY_VERSION was bumped to 1.3 for Phase 3.5 cap "
            "attachment on W_IMAGE_ONLY_TEXT_BAR_FAIL and "
            "W_TABLE_EXPECTED_NOT_EXTRACTED; any subsequent policy "
            "change must bump it again"
        )

    def test_receipt_carries_version(self):
        result = compute_confidence(_clean_pdf_ctx())
        assert result.scoring_policy_version == SCORING_POLICY_VERSION
        assert result.scoring_policy_version == "1.3"

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
        assert result.scoring_policy_version == "1.3"


# ── W_IMAGE_ONLY_TEXT_BAR_FAIL — cap at 69 (RISKY) ────────────────────────────

class TestImageOnlyTextBarFailCap:
    """Cap regression for Phase 3.5 Gap 1 (image-only text-bar FAIL).

    Ratified per docs/calibration/USP_CLAIM_V1.md §5.2 — a fully image-only
    PDF (classification=="scanned", text_pages==0) fails the text-only bar
    regardless of whether OCR was applied.

    Closes silent HIGH-band failures on the Phase 4 dev split:
      - text_simple__myctophidae
      - text_simple__letter3
      - text_dense__japanese
    """

    def test_warning_caps_at_69(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_IMAGE_ONLY_TEXT_BAR_FAIL"],
            metadata_extras={
                "image_only_text_bar_diagnostics": {
                    "classification": "scanned",
                    "image_pages": 1,
                    "text_pages": 0,
                    "page_count": 1,
                    "ocr_available": True,
                    "warned": True,
                    "warning_maturity": "candidate",
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 69, (
            f"W_IMAGE_ONLY_TEXT_BAR_FAIL must cap score at 69 (RISKY); "
            f"got {result.score}"
        )

    def test_deduction_recorded_with_maturity(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_IMAGE_ONLY_TEXT_BAR_FAIL"],
            metadata_extras={
                "image_only_text_bar_diagnostics": {
                    "classification": "scanned",
                    "image_pages": 3,
                    "text_pages": 0,
                    "page_count": 3,
                    "ocr_available": True,
                    "warned": True,
                    "warning_maturity": "candidate",
                },
            },
        )
        result = compute_confidence(ctx)
        io = [d for d in result.deductions if d.rule_id == "W_IMAGE_ONLY_TEXT_BAR_FAIL"]
        assert len(io) == 1
        assert io[0].maturity == "candidate"
        assert io[0].penalty > 0
        assert io[0].suppressed is False
        assert io[0].evidence is not None
        assert io[0].evidence.metric_name == "image_pages"
        assert io[0].evidence.metric_value == 3.0
        assert io[0].evidence.extras.get("page_count") == 3
        assert io[0].evidence.extras.get("text_pages") == 0
        assert io[0].evidence.extras.get("classification") == "scanned"
        assert io[0].evidence.extras.get("ocr_available") is True

    def test_suppressed_when_score_already_below_cap(self):
        """Coexists gracefully with OCR_REQUIRED — the OCR-required deduction
        typically drops the score into the 40s, well below the 69 cap."""
        ctx = _clean_pdf_ctx(
            warning_codes=[
                "W_IMAGE_ONLY_TEXT_BAR_FAIL",
                "GLYPH_ARTIFACTS",
                "REPEATED_CONTENT",
                "TOKEN_BLOAT",
            ],
            metadata_extras={
                "image_only_text_bar_diagnostics": {
                    "classification": "scanned",
                    "image_pages": 1,
                    "text_pages": 0,
                    "page_count": 1,
                    "ocr_available": False,
                    "warned": True,
                    "warning_maturity": "candidate",
                },
            },
        )
        result = compute_confidence(ctx)
        # Base pdf=87 minus 25+8+8 = 46. 46 < 69 → cap doesn't apply.
        assert result.score <= 69
        io = [d for d in result.deductions if d.rule_id == "W_IMAGE_ONLY_TEXT_BAR_FAIL"]
        assert len(io) == 1
        assert io[0].suppressed is True
        assert io[0].penalty == 0
        assert "already" in io[0].suppression_reason


# ── W_TABLE_EXPECTED_NOT_EXTRACTED — cap at 84 (top of OK) ───────────────────

class TestTableExpectedNotExtractedCap:
    """Cap regression for Phase 3.5 Gap 2.

    Wires the existing TableExpectationValidator warning (already emitted on
    Phase 4 dev-split docs fqr-retail-blackrock, ikea3, VRSK) into the score
    layer. Softer cap (84, experimental) mirrors W_HEADER_FOOTER_TABLE_GARBLED
    — the signal has real evidence but the evidence base is narrow.
    """

    def test_warning_caps_at_84(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_TABLE_EXPECTED_NOT_EXTRACTED"],
            metadata_extras={
                "table_expectation_diagnostics": {
                    "pages_expected_not_extracted": [1],
                    "warned": True,
                    "warning_maturity": "experimental",
                },
            },
        )
        result = compute_confidence(ctx)
        assert result.score <= 84, (
            f"W_TABLE_EXPECTED_NOT_EXTRACTED must cap score at 84 (top of OK); "
            f"got {result.score}"
        )
        # Softer cap — must stay above RISKY unless other rules deduct.
        assert result.score >= 70, (
            "W_TABLE_EXPECTED_NOT_EXTRACTED cap is 84, but score dropped "
            "below OK — another rule must be interacting"
        )

    def test_deduction_recorded_with_maturity(self):
        ctx = _clean_pdf_ctx(
            warning_codes=["W_TABLE_EXPECTED_NOT_EXTRACTED"],
            metadata_extras={
                "table_expectation_diagnostics": {
                    "pages_expected_not_extracted": [3, 5, 7],
                    "warned": True,
                    "warning_maturity": "experimental",
                },
            },
        )
        result = compute_confidence(ctx)
        te = [d for d in result.deductions if d.rule_id == "W_TABLE_EXPECTED_NOT_EXTRACTED"]
        assert len(te) == 1
        assert te[0].maturity == "experimental"
        assert te[0].penalty > 0
        assert te[0].suppressed is False
        assert te[0].evidence is not None
        assert te[0].evidence.metric_name == "pages_expected_not_extracted"
        assert te[0].evidence.metric_value == 3.0
        assert te[0].evidence.extras.get("pages") == [3, 5, 7]

    def test_suppressed_when_score_already_below_cap(self):
        ctx = _clean_pdf_ctx(
            warning_codes=[
                "W_TABLE_EXPECTED_NOT_EXTRACTED",
                "GLYPH_ARTIFACTS",  # -25
            ],
            metadata_extras={
                "table_expectation_diagnostics": {
                    "pages_expected_not_extracted": [1],
                    "warned": True,
                    "warning_maturity": "experimental",
                },
            },
        )
        result = compute_confidence(ctx)
        # Base pdf=87 -25 = 62; 62 < 84 → cap doesn't apply.
        assert result.score <= 84
        te = [d for d in result.deductions if d.rule_id == "W_TABLE_EXPECTED_NOT_EXTRACTED"]
        assert len(te) == 1
        assert te[0].suppressed is True
        assert te[0].penalty == 0
