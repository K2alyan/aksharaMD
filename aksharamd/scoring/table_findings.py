"""Consolidated user-facing table-quality findings.

Converts the 36 raw TableQualitySignal values from a TableQualityReport into
a small set of named, actionable findings. No raw signal maps to a score
penalty directly — only findings promoted to "stable" maturity should ever
influence readiness scoring.

All findings in this module start as maturity="experimental".

Warning code compatibility
--------------------------
COL_GENERIC_TABLES (readiness.py): overlaps TABLE_HEADER_UNCERTAIN via
    generic_header_count > 0. The two detectors use different data (Markdown
    text vs structured TableData). They may diverge. Merger deferred.

W_HEADER_FOOTER_TABLE_GARBLED (validators/header_footer_table.py): overlaps
    TABLE_PAGE_FURNITURE_SUSPECTED via geometry + fragmentation signals.
    Merger deferred; this finding adds structural evidence.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .table_quality import SigName, TableQualityReport, TableQualitySignal

# ── Finding model ──────────────────────────────────────────────────────────────

class TableFinding(BaseModel):
    """A consolidated, user-facing table-quality finding.

    Aggregated from one or more raw TableQualitySignals. Findings are the
    unit that can eventually carry a readiness-score penalty; raw signals
    are retained for debugging but not surfaced to users directly.
    """
    name: str
    status: str        # "risk" | "ok" | "unknown" | "not_applicable"
    maturity: str      # "experimental" | "candidate" | "stable"
    supporting_signals: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    applicable_methods: list[str] | None = None  # None = all methods


# ── Trigger helpers ────────────────────────────────────────────────────────────

def _get(report: TableQualityReport, name: str) -> TableQualitySignal | None:
    return next((s for s in report.signals if s.name == name), None)


def _val(report: TableQualityReport, name: str, default=None):
    s = _get(report, name)
    return s.value if s is not None else default


def _is_risk(report: TableQualityReport, name: str) -> bool:
    s = _get(report, name)
    return s is not None and s.status == "risk"


def _is_unknown(report: TableQualityReport, name: str) -> bool:
    s = _get(report, name)
    return s is not None and s.status == "unknown"


# ── Finding: TABLE_STRUCTURE_INCOMPLETE ───────────────────────────────────────

def _finding_structure_incomplete(report: TableQualityReport) -> TableFinding:
    """Missing cell coordinates or ragged extraction.

    Triggered by: missing_coordinate_count > 0 OR ragged_row_count > 0

    Note: when span_detection="unsupported", coordinates covered by spans
    appear as missing. The signal already excludes span-covered positions
    from the missing count, so this should not fire for well-formed span
    tables under unsupported span detection.

    Maturity: experimental — not yet validated against known failures.
    """
    missing = _val(report, SigName.MISSING_COORDINATE_COUNT, 0)
    ragged = _val(report, SigName.RAGGED_ROW_COUNT, 0)
    fires = (missing or 0) > 0 or (ragged or 0) > 0

    supporting = []
    evidence: dict = {}
    if (missing or 0) > 0:
        supporting.append(SigName.MISSING_COORDINATE_COUNT)
        evidence["missing_coordinate_count"] = missing
    if (ragged or 0) > 0:
        supporting.append(SigName.RAGGED_ROW_COUNT)
        evidence["ragged_row_count"] = ragged

    return TableFinding(
        name="TABLE_STRUCTURE_INCOMPLETE",
        status="risk" if fires else "ok",
        maturity="experimental",
        supporting_signals=supporting,
        evidence=evidence,
    )


# ── Finding: TABLE_CELL_FRAGMENTATION ─────────────────────────────────────────

def _finding_cell_fragmentation(report: TableQualityReport) -> TableFinding:
    """Cell content appears fragmented (single characters or punctuation shards).

    Triggered by:
      (single_char_cell_fraction > 0.5 AND numeric_only_fraction <= 0.7)
      OR punctuation_only_cell_fraction > 0.3

    The numeric guard prevents valid numeric tables (with many single-digit
    cells) from triggering this finding.

    Maturity: experimental — numeric guard threshold is uncalibrated.
    """
    single_char_frac = _val(report, SigName.SINGLE_CHAR_CELL_FRACTION, 0.0) or 0.0
    punct_frac       = _val(report, SigName.PUNCTUATION_ONLY_FRACTION, 0.0) or 0.0
    numeric_frac     = _val(report, SigName.NUMERIC_ONLY_FRACTION, 0.0) or 0.0
    ws_only          = _val(report, SigName.WHITESPACE_ONLY_CELL_COUNT, 0) or 0

    fragmented_chars = single_char_frac > 0.5 and numeric_frac <= 0.7
    punct_heavy      = punct_frac > 0.3
    fires = fragmented_chars or punct_heavy

    supporting = []
    evidence: dict = {}
    if fragmented_chars:
        supporting += [SigName.SINGLE_CHAR_CELL_FRACTION, SigName.NUMERIC_ONLY_FRACTION]
        evidence.update({
            "single_char_cell_fraction": single_char_frac,
            "numeric_only_fraction": numeric_frac,
        })
    if punct_heavy:
        supporting.append(SigName.PUNCTUATION_ONLY_FRACTION)
        evidence["punctuation_only_cell_fraction"] = punct_frac
    if ws_only > 0:
        supporting.append(SigName.WHITESPACE_ONLY_CELL_COUNT)
        evidence["whitespace_only_cell_count"] = ws_only

    return TableFinding(
        name="TABLE_CELL_FRAGMENTATION",
        status="risk" if fires else "ok",
        maturity="experimental",
        supporting_signals=list(dict.fromkeys(supporting)),
        evidence=evidence,
    )


# ── Finding: TABLE_HEADER_UNCERTAIN ───────────────────────────────────────────

def _finding_header_uncertain(report: TableQualityReport) -> TableFinding:
    """Header quality is uncertain: generic names, duplicates, or repeated in body.

    Triggered by:
      generic_header_count > 0 OR duplicate_header_names > 0
      OR repeated_header_in_body > 0

    Note: generic headers are expected for auto-generated sheets (col1/col2
    from CSV export). This finding is most meaningful when combined with
    extraction_method != XLSX_NATIVE.

    Maturity: experimental — FP rate for auto-generated CSVs is unknown.
    """
    generic   = _val(report, SigName.GENERIC_HEADER_COUNT, 0) or 0
    dup_hdrs  = _val(report, SigName.DUPLICATE_HEADER_NAMES, 0) or 0
    repeated  = _val(report, SigName.REPEATED_HEADER_IN_BODY, 0) or 0

    fires = generic > 0 or dup_hdrs > 0 or repeated > 0

    supporting = []
    evidence: dict = {}
    if generic > 0:
        supporting.append(SigName.GENERIC_HEADER_COUNT)
        evidence["generic_header_count"] = generic
    if dup_hdrs > 0:
        supporting.append(SigName.DUPLICATE_HEADER_NAMES)
        evidence["duplicate_header_names"] = dup_hdrs
    if repeated > 0:
        supporting.append(SigName.REPEATED_HEADER_IN_BODY)
        evidence["repeated_header_in_body"] = repeated

    return TableFinding(
        name="TABLE_HEADER_UNCERTAIN",
        status="risk" if fires else "ok",
        maturity="experimental",
        supporting_signals=supporting,
        evidence=evidence,
    )


# ── Finding: TABLE_PAGE_FURNITURE_SUSPECTED ────────────────────────────────────

def _finding_page_furniture(report: TableQualityReport) -> TableFinding:
    """Table may be a page header/footer misidentified as tabular data.

    Triggered by:
      (table_near_top_margin OR table_near_bottom_margin)
      AND (table_one_row OR single_char_cell_fraction > 0.45)

    Both conditions required: margin alone is not sufficient; a real table
    near the page edge must not be classified as furniture.

    Requires bbox. Returns not_applicable when no bbox is available.

    Maturity: experimental — calibrated on 1 known positive (PWC document).
    """
    bbox_available = _val(report, SigName.TABLE_BBOX_AVAILABLE)
    if bbox_available is None or bbox_available is False:
        return TableFinding(
            name="TABLE_PAGE_FURNITURE_SUSPECTED",
            status="not_applicable",
            maturity="experimental",
            supporting_signals=[SigName.TABLE_BBOX_AVAILABLE],
            evidence={"reason": "no_bbox"},
        )

    near_top     = _val(report, SigName.TABLE_NEAR_TOP_MARGIN, False) or False
    near_bottom  = _val(report, SigName.TABLE_NEAR_BOTTOM_MARGIN, False) or False
    one_row      = _val(report, SigName.TABLE_ONE_ROW, False) or False
    single_char  = _val(report, SigName.SINGLE_CHAR_CELL_FRACTION, 0.0) or 0.0

    in_margin    = near_top or near_bottom
    fragmented   = one_row or (single_char > 0.45)
    fires        = in_margin and fragmented

    supporting = []
    evidence: dict = {}
    if near_top:
        supporting.append(SigName.TABLE_NEAR_TOP_MARGIN)
        evidence["near_top_margin"] = True
    if near_bottom:
        supporting.append(SigName.TABLE_NEAR_BOTTOM_MARGIN)
        evidence["near_bottom_margin"] = True
    if one_row:
        supporting.append(SigName.TABLE_ONE_ROW)
        evidence["one_row"] = True
    if single_char > 0.45:
        supporting.append(SigName.SINGLE_CHAR_CELL_FRACTION)
        evidence["single_char_cell_fraction"] = single_char

    return TableFinding(
        name="TABLE_PAGE_FURNITURE_SUSPECTED",
        status="risk" if fires else "ok",
        maturity="experimental",
        applicable_methods=["pdf.ruled", "pdf.booktabs", "pdf.whitespace", "pdf.stitched"],
        supporting_signals=supporting,
        evidence=evidence,
    )


# ── Finding: TABLE_STITCHING_UNCERTAIN ────────────────────────────────────────

def _finding_stitching_uncertain(report: TableQualityReport) -> TableFinding:
    """Cross-page table stitching reliability is uncertain.

    Triggered by:
      stitching_confidence == "inferred"
      AND (source_method_consistency == False OR stitching_row_continuity == False)

    stitching_confidence alone is NOT sufficient: all stitched tables currently
    have confidence="inferred". The finding requires a corroborating signal
    (mixed extraction methods or row-continuity gap).

    Not applicable for non-stitched tables.

    Maturity: experimental — single corroborating signal required but not yet
    validated against known cross-page stitching failures.
    """
    # Not applicable unless this is a stitched table
    if report.extraction_method != "pdf.stitched":
        return TableFinding(
            name="TABLE_STITCHING_UNCERTAIN",
            status="not_applicable",
            maturity="experimental",
            supporting_signals=[],
            evidence={"reason": "not_stitched"},
        )

    confidence         = _val(report, SigName.STITCHING_CONFIDENCE, "unknown")
    method_consistent  = _val(report, SigName.SOURCE_METHOD_CONSISTENCY, True)
    row_continuity     = _val(report, SigName.ROW_CONTINUITY_OK, True)

    is_inferred = str(confidence) == "inferred"
    corroborated = (not bool(method_consistent)) or (not bool(row_continuity))
    fires = is_inferred and corroborated

    supporting = []
    evidence: dict = {"stitching_confidence": confidence}
    if not bool(method_consistent):
        supporting.append(SigName.SOURCE_METHOD_CONSISTENCY)
        evidence["source_method_consistency"] = False
    if not bool(row_continuity):
        supporting.append(SigName.ROW_CONTINUITY_OK)
        evidence["stitching_row_continuity"] = False

    return TableFinding(
        name="TABLE_STITCHING_UNCERTAIN",
        status="risk" if fires else "ok",
        maturity="experimental",
        applicable_methods=["pdf.stitched"],
        supporting_signals=[SigName.STITCHING_CONFIDENCE] + supporting,
        evidence=evidence,
    )


# ── Finding: TABLE_EXPECTED_BUT_NOT_EXTRACTED ─────────────────────────────────

def _finding_expected_not_extracted(report: TableQualityReport) -> TableFinding:
    """Experimental — cannot be triggered from a TableQualityReport since it is page-level.

    This finding is computed separately via the TableExpectationValidator and does not
    use the signal-based aggregation path. Returns not_applicable always from here.
    """
    return TableFinding(
        name="TABLE_EXPECTED_BUT_NOT_EXTRACTED",
        status="not_applicable",
        maturity="experimental",
        supporting_signals=[],
        evidence={"reason": "page_level_finding_not_table_level"},
    )


# ── Aggregation entry point ────────────────────────────────────────────────────

_FINDING_FNS = [
    _finding_structure_incomplete,
    _finding_cell_fragmentation,
    _finding_header_uncertain,
    _finding_page_furniture,
    _finding_stitching_uncertain,
    _finding_expected_not_extracted,
]


def aggregate_findings(report: TableQualityReport) -> list[TableFinding]:
    """Convert a TableQualityReport into consolidated user-facing findings.

    Returns all 6 findings. Status is "risk", "ok", or "not_applicable".
    Findings with status="not_applicable" are included for completeness but
    should not be surfaced to the user or scored.

    Note: TABLE_EXPECTED_BUT_NOT_EXTRACTED is always "not_applicable" here —
    it is computed at page level by TableExpectationValidator.
    """
    return [fn(report) for fn in _FINDING_FNS]


def risk_findings(report: TableQualityReport) -> list[TableFinding]:
    """Return only findings with status='risk'."""
    return [f for f in aggregate_findings(report) if f.status == "risk"]
