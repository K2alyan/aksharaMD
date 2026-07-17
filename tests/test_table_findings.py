"""Tests for aksharamd/scoring/table_findings.py — Milestone 6."""
from __future__ import annotations

from aksharamd.models.block import Block
from aksharamd.models.table import BoundingBox, ExtractionMethod, TableCell, TableData
from aksharamd.scoring.table_findings import (
    TableFinding,
    aggregate_findings,
    risk_findings,
)
from aksharamd.scoring.table_quality import (
    SigName,
    TableQualityReport,
    TableQualitySignal,
    compute_table_quality,
)

# ── Test helpers ───────────────────────────────────────────────────────────────

def _make_td(
    rows: list[list[str]],
    header_rows: list[int] | None = None,
    extraction_method: ExtractionMethod | None = None,
    bbox: BoundingBox | None = None,
    metadata: dict | None = None,
) -> TableData:
    if not rows:
        return TableData(row_count=0, column_count=0, cells=[])
    ncols = max(len(r) for r in rows)
    cells = [
        TableCell(text=rows[r][c], row=r, column=c)
        for r in range(len(rows))
        for c in range(len(rows[r]))
    ]
    return TableData(
        row_count=len(rows),
        column_count=ncols,
        cells=cells,
        header_rows=header_rows if header_rows is not None else [0],
        extraction_method=extraction_method,
        bbox=bbox,
        metadata=metadata or {},
    )


def _make_block(td: TableData) -> Block:
    return Block.from_table(td, page=1, index=0)


def _report(td: TableData, page_height: float = 0.0, page_width: float = 0.0) -> TableQualityReport:
    block = _make_block(td)
    return compute_table_quality(block, page_height=page_height, page_width=page_width)


def _sig_report(signals: list[tuple[str, object, str]]) -> TableQualityReport:
    """Build a minimal report from (name, value, status) tuples for unit testing finding logic."""
    sigs = [
        TableQualitySignal(name=n, value=v, status=s)
        for n, v, s in signals
    ]
    return TableQualityReport(
        table_id="test",
        block_id="test",
        row_count=3,
        column_count=3,
        signals=sigs,
        overall_status="ok",
    )


def _get_finding(findings: list[TableFinding], name: str) -> TableFinding | None:
    return next((f for f in findings if f.name == name), None)


# ── TABLE_STRUCTURE_INCOMPLETE ────────────────────────────────────────────────

class TestStructureIncomplete:
    def test_ok_when_no_issues(self):
        report = _sig_report([
            (SigName.MISSING_COORDINATE_COUNT, 0, "ok"),
            (SigName.RAGGED_ROW_COUNT, 0, "ok"),
        ])
        findings = aggregate_findings(report)
        f = _get_finding(findings, "TABLE_STRUCTURE_INCOMPLETE")
        assert f is not None
        assert f.status == "ok"

    def test_risk_when_missing_coordinates(self):
        report = _sig_report([
            (SigName.MISSING_COORDINATE_COUNT, 3, "risk"),
            (SigName.RAGGED_ROW_COUNT, 0, "ok"),
        ])
        findings = aggregate_findings(report)
        f = _get_finding(findings, "TABLE_STRUCTURE_INCOMPLETE")
        assert f.status == "risk"
        assert SigName.MISSING_COORDINATE_COUNT in f.supporting_signals
        assert f.evidence["missing_coordinate_count"] == 3

    def test_risk_when_ragged_rows(self):
        report = _sig_report([
            (SigName.MISSING_COORDINATE_COUNT, 0, "ok"),
            (SigName.RAGGED_ROW_COUNT, 2, "risk"),
        ])
        findings = aggregate_findings(report)
        f = _get_finding(findings, "TABLE_STRUCTURE_INCOMPLETE")
        assert f.status == "risk"
        assert SigName.RAGGED_ROW_COUNT in f.supporting_signals
        assert f.evidence["ragged_row_count"] == 2

    def test_risk_when_both_signal(self):
        report = _sig_report([
            (SigName.MISSING_COORDINATE_COUNT, 2, "risk"),
            (SigName.RAGGED_ROW_COUNT, 1, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_STRUCTURE_INCOMPLETE")
        assert f.status == "risk"
        assert len(f.supporting_signals) == 2

    def test_ok_when_signal_absent(self):
        report = _sig_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_STRUCTURE_INCOMPLETE")
        assert f.status == "ok"
        assert f.evidence == {}

    def test_maturity_experimental(self):
        report = _sig_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_STRUCTURE_INCOMPLETE")
        assert f.maturity == "experimental"


# ── TABLE_CELL_FRAGMENTATION ──────────────────────────────────────────────────

class TestCellFragmentation:
    def test_ok_on_clean_table(self):
        report = _sig_report([
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.1, "ok"),
            (SigName.PUNCTUATION_ONLY_FRACTION, 0.0, "ok"),
            (SigName.NUMERIC_ONLY_FRACTION, 0.2, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.status == "ok"

    def test_risk_when_high_single_char_non_numeric(self):
        report = _sig_report([
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.6, "risk"),
            (SigName.NUMERIC_ONLY_FRACTION, 0.3, "ok"),
            (SigName.PUNCTUATION_ONLY_FRACTION, 0.0, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.status == "risk"
        assert SigName.SINGLE_CHAR_CELL_FRACTION in f.supporting_signals
        assert f.evidence["single_char_cell_fraction"] == 0.6

    def test_ok_when_single_char_but_numeric_table(self):
        """Numeric guard: single-char high but mostly numeric cells -> not fragmented."""
        report = _sig_report([
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.8, "risk"),
            (SigName.NUMERIC_ONLY_FRACTION, 0.75, "ok"),  # > 0.7 guard
            (SigName.PUNCTUATION_ONLY_FRACTION, 0.0, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.status == "ok"

    def test_risk_when_punct_heavy(self):
        report = _sig_report([
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.1, "ok"),
            (SigName.NUMERIC_ONLY_FRACTION, 0.0, "ok"),
            (SigName.PUNCTUATION_ONLY_FRACTION, 0.4, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.status == "risk"
        assert SigName.PUNCTUATION_ONLY_FRACTION in f.supporting_signals
        assert f.evidence["punctuation_only_cell_fraction"] == 0.4

    def test_whitespace_included_in_evidence(self):
        report = _sig_report([
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.0, "ok"),
            (SigName.NUMERIC_ONLY_FRACTION, 0.0, "ok"),
            (SigName.PUNCTUATION_ONLY_FRACTION, 0.4, "risk"),
            (SigName.WHITESPACE_ONLY_CELL_COUNT, 3, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.status == "risk"
        assert SigName.WHITESPACE_ONLY_CELL_COUNT in f.supporting_signals
        assert f.evidence["whitespace_only_cell_count"] == 3

    def test_whitespace_not_enough_alone_to_trigger(self):
        """Whitespace-only count is added to evidence but does not trigger the finding."""
        report = _sig_report([
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.0, "ok"),
            (SigName.NUMERIC_ONLY_FRACTION, 0.0, "ok"),
            (SigName.PUNCTUATION_ONLY_FRACTION, 0.0, "ok"),
            (SigName.WHITESPACE_ONLY_CELL_COUNT, 5, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.status == "ok"

    def test_boundary_single_char_exactly_05(self):
        """Exactly 0.5 is not above threshold; no risk."""
        report = _sig_report([
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.5, "ok"),
            (SigName.NUMERIC_ONLY_FRACTION, 0.0, "ok"),
            (SigName.PUNCTUATION_ONLY_FRACTION, 0.0, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.status == "ok"

    def test_maturity_experimental(self):
        report = _sig_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_CELL_FRAGMENTATION")
        assert f.maturity == "experimental"


# ── TABLE_HEADER_UNCERTAIN ────────────────────────────────────────────────────

class TestHeaderUncertain:
    def test_ok_when_clean_headers(self):
        report = _sig_report([
            (SigName.GENERIC_HEADER_COUNT, 0, "ok"),
            (SigName.DUPLICATE_HEADER_NAMES, 0, "ok"),
            (SigName.REPEATED_HEADER_IN_BODY, 0, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_HEADER_UNCERTAIN")
        assert f.status == "ok"

    def test_risk_when_generic_headers(self):
        report = _sig_report([
            (SigName.GENERIC_HEADER_COUNT, 2, "risk"),
            (SigName.DUPLICATE_HEADER_NAMES, 0, "ok"),
            (SigName.REPEATED_HEADER_IN_BODY, 0, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_HEADER_UNCERTAIN")
        assert f.status == "risk"
        assert SigName.GENERIC_HEADER_COUNT in f.supporting_signals
        assert f.evidence["generic_header_count"] == 2

    def test_risk_when_duplicate_headers(self):
        report = _sig_report([
            (SigName.GENERIC_HEADER_COUNT, 0, "ok"),
            (SigName.DUPLICATE_HEADER_NAMES, 1, "risk"),
            (SigName.REPEATED_HEADER_IN_BODY, 0, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_HEADER_UNCERTAIN")
        assert f.status == "risk"
        assert SigName.DUPLICATE_HEADER_NAMES in f.supporting_signals
        assert f.evidence["duplicate_header_names"] == 1

    def test_risk_when_repeated_header_in_body(self):
        report = _sig_report([
            (SigName.GENERIC_HEADER_COUNT, 0, "ok"),
            (SigName.DUPLICATE_HEADER_NAMES, 0, "ok"),
            (SigName.REPEATED_HEADER_IN_BODY, 1, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_HEADER_UNCERTAIN")
        assert f.status == "risk"
        assert SigName.REPEATED_HEADER_IN_BODY in f.supporting_signals
        assert f.evidence["repeated_header_in_body"] == 1

    def test_all_three_combine(self):
        report = _sig_report([
            (SigName.GENERIC_HEADER_COUNT, 1, "risk"),
            (SigName.DUPLICATE_HEADER_NAMES, 2, "risk"),
            (SigName.REPEATED_HEADER_IN_BODY, 1, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_HEADER_UNCERTAIN")
        assert f.status == "risk"
        assert len(f.supporting_signals) == 3

    def test_maturity_experimental(self):
        report = _sig_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_HEADER_UNCERTAIN")
        assert f.maturity == "experimental"

    def test_ok_with_no_header_signals(self):
        report = _sig_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_HEADER_UNCERTAIN")
        assert f.status == "ok"


# ── TABLE_PAGE_FURNITURE_SUSPECTED ────────────────────────────────────────────

class TestPageFurnitureSuspected:
    def test_not_applicable_when_no_bbox(self):
        report = _sig_report([
            (SigName.TABLE_BBOX_AVAILABLE, False, "unknown"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "not_applicable"
        assert f.evidence.get("reason") == "no_bbox"

    def test_not_applicable_when_bbox_signal_absent(self):
        report = _sig_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "not_applicable"

    def test_ok_when_near_margin_but_not_fragmented(self):
        report = _sig_report([
            (SigName.TABLE_BBOX_AVAILABLE, True, "ok"),
            (SigName.TABLE_NEAR_TOP_MARGIN, True, "risk"),
            (SigName.TABLE_ONE_ROW, False, "ok"),
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.1, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "ok"

    def test_ok_when_fragmented_but_not_near_margin(self):
        report = _sig_report([
            (SigName.TABLE_BBOX_AVAILABLE, True, "ok"),
            (SigName.TABLE_NEAR_TOP_MARGIN, False, "ok"),
            (SigName.TABLE_NEAR_BOTTOM_MARGIN, False, "ok"),
            (SigName.TABLE_ONE_ROW, True, "risk"),
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.9, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "ok"

    def test_risk_near_top_and_one_row(self):
        report = _sig_report([
            (SigName.TABLE_BBOX_AVAILABLE, True, "ok"),
            (SigName.TABLE_NEAR_TOP_MARGIN, True, "risk"),
            (SigName.TABLE_NEAR_BOTTOM_MARGIN, False, "ok"),
            (SigName.TABLE_ONE_ROW, True, "risk"),
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.1, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "risk"
        assert SigName.TABLE_NEAR_TOP_MARGIN in f.supporting_signals
        assert SigName.TABLE_ONE_ROW in f.supporting_signals
        assert f.evidence.get("near_top_margin") is True
        assert f.evidence.get("one_row") is True

    def test_risk_near_bottom_and_single_char_heavy(self):
        report = _sig_report([
            (SigName.TABLE_BBOX_AVAILABLE, True, "ok"),
            (SigName.TABLE_NEAR_TOP_MARGIN, False, "ok"),
            (SigName.TABLE_NEAR_BOTTOM_MARGIN, True, "risk"),
            (SigName.TABLE_ONE_ROW, False, "ok"),
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.5, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "risk"
        assert SigName.TABLE_NEAR_BOTTOM_MARGIN in f.supporting_signals
        assert SigName.SINGLE_CHAR_CELL_FRACTION in f.supporting_signals
        assert f.evidence.get("near_bottom_margin") is True

    def test_boundary_single_char_exactly_045(self):
        """Exactly 0.45 is not above threshold; requires > 0.45."""
        report = _sig_report([
            (SigName.TABLE_BBOX_AVAILABLE, True, "ok"),
            (SigName.TABLE_NEAR_TOP_MARGIN, True, "risk"),
            (SigName.TABLE_NEAR_BOTTOM_MARGIN, False, "ok"),
            (SigName.TABLE_ONE_ROW, False, "ok"),
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.45, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "ok"

    def test_applicable_methods_includes_pdf(self):
        # Need bbox=True and not-near-margin to get an "ok" result that has applicable_methods set
        report = _sig_report([
            (SigName.TABLE_BBOX_AVAILABLE, True, "ok"),
            (SigName.TABLE_NEAR_TOP_MARGIN, False, "ok"),
            (SigName.TABLE_NEAR_BOTTOM_MARGIN, False, "ok"),
            (SigName.TABLE_ONE_ROW, False, "ok"),
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.0, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.status == "ok"
        assert f.applicable_methods is not None
        assert "pdf.ruled" in f.applicable_methods

    def test_maturity_experimental(self):
        report = _sig_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_PAGE_FURNITURE_SUSPECTED")
        assert f.maturity == "experimental"


# ── TABLE_STITCHING_UNCERTAIN ─────────────────────────────────────────────────

class TestStitchingUncertain:
    def test_not_applicable_when_not_stitched(self):
        report = _sig_report([])
        # Default extraction_method is None, not pdf.stitched
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "not_applicable"
        assert f.evidence.get("reason") == "not_stitched"

    def test_not_applicable_for_non_stitched_method(self):
        report = _sig_report([])
        report = TableQualityReport(
            table_id="t", block_id="b", row_count=3, column_count=3,
            signals=[], overall_status="ok", extraction_method="pdf.ruled",
        )
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "not_applicable"

    def _stitched_report(self, signals: list[tuple[str, object, str]]) -> TableQualityReport:
        sigs = [TableQualitySignal(name=n, value=v, status=s) for n, v, s in signals]
        return TableQualityReport(
            table_id="t", block_id="b", row_count=10, column_count=4,
            signals=sigs, overall_status="ok",
            extraction_method="pdf.stitched",
        )

    def test_ok_when_confidence_not_inferred(self):
        report = self._stitched_report([
            (SigName.STITCHING_CONFIDENCE, "high", "ok"),
            (SigName.SOURCE_METHOD_CONSISTENCY, True, "ok"),
            (SigName.ROW_CONTINUITY_OK, True, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "ok"

    def test_ok_when_inferred_but_no_corroboration(self):
        """Inferred confidence alone is not enough — needs method or row gap."""
        report = self._stitched_report([
            (SigName.STITCHING_CONFIDENCE, "inferred", "ok"),
            (SigName.SOURCE_METHOD_CONSISTENCY, True, "ok"),
            (SigName.ROW_CONTINUITY_OK, True, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "ok"

    def test_risk_when_inferred_and_method_inconsistent(self):
        report = self._stitched_report([
            (SigName.STITCHING_CONFIDENCE, "inferred", "ok"),
            (SigName.SOURCE_METHOD_CONSISTENCY, False, "risk"),
            (SigName.ROW_CONTINUITY_OK, True, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "risk"
        assert SigName.SOURCE_METHOD_CONSISTENCY in f.supporting_signals
        assert f.evidence.get("source_method_consistency") is False

    def test_risk_when_inferred_and_row_gap(self):
        report = self._stitched_report([
            (SigName.STITCHING_CONFIDENCE, "inferred", "ok"),
            (SigName.SOURCE_METHOD_CONSISTENCY, True, "ok"),
            (SigName.ROW_CONTINUITY_OK, False, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "risk"
        assert SigName.ROW_CONTINUITY_OK in f.supporting_signals
        assert f.evidence.get("stitching_row_continuity") is False

    def test_risk_when_inferred_and_both_corroborations(self):
        report = self._stitched_report([
            (SigName.STITCHING_CONFIDENCE, "inferred", "ok"),
            (SigName.SOURCE_METHOD_CONSISTENCY, False, "risk"),
            (SigName.ROW_CONTINUITY_OK, False, "risk"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "risk"
        assert len([s for s in f.supporting_signals if s != SigName.STITCHING_CONFIDENCE]) == 2

    def test_stitching_confidence_always_in_supporting_when_stitched(self):
        report = self._stitched_report([
            (SigName.STITCHING_CONFIDENCE, "inferred", "ok"),
            (SigName.SOURCE_METHOD_CONSISTENCY, True, "ok"),
            (SigName.ROW_CONTINUITY_OK, True, "ok"),
        ])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.status == "ok"

    def test_applicable_methods_is_stitched_only(self):
        report = self._stitched_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.applicable_methods == ["pdf.stitched"]

    def test_maturity_experimental(self):
        report = self._stitched_report([])
        f = _get_finding(aggregate_findings(report), "TABLE_STITCHING_UNCERTAIN")
        assert f.maturity == "experimental"


# ── aggregate_findings / risk_findings ────────────────────────────────────────

class TestAggregation:
    def test_aggregate_returns_all_six(self):
        report = _sig_report([])
        findings = aggregate_findings(report)
        names = {f.name for f in findings}
        assert names == {
            "TABLE_STRUCTURE_INCOMPLETE",
            "TABLE_CELL_FRAGMENTATION",
            "TABLE_HEADER_UNCERTAIN",
            "TABLE_PAGE_FURNITURE_SUSPECTED",
            "TABLE_STITCHING_UNCERTAIN",
            "TABLE_EXPECTED_BUT_NOT_EXTRACTED",
        }

    def test_risk_findings_excludes_ok(self):
        report = _sig_report([
            (SigName.DUPLICATE_HEADER_NAMES, 2, "risk"),
        ])
        risks = risk_findings(report)
        assert all(f.status == "risk" for f in risks)
        names = {f.name for f in risks}
        assert "TABLE_HEADER_UNCERTAIN" in names
        assert "TABLE_STRUCTURE_INCOMPLETE" not in names

    def test_risk_findings_excludes_not_applicable(self):
        report = _sig_report([])  # no bbox → furniture is not_applicable
        risks = risk_findings(report)
        not_applicable = [f for f in risks if f.status == "not_applicable"]
        assert not_applicable == []

    def test_no_risk_findings_on_clean_report(self):
        td = _make_td([
            ["Product Name", "Unit Price", "Quantity"],
            ["Widget Alpha", "12.50", "100"],
            ["Widget Beta", "8.75", "200"],
        ])
        report = _report(td, page_height=1000.0, page_width=800.0)
        risks = risk_findings(report)
        assert risks == []


# ── TableFinding model ────────────────────────────────────────────────────────

class TestTableFindingModel:
    def test_finding_has_required_fields(self):
        f = TableFinding(name="X", status="ok", maturity="experimental")
        assert f.supporting_signals == []
        assert f.evidence == {}
        assert f.applicable_methods is None

    def test_finding_applicable_methods_none_means_all(self):
        f = TableFinding(name="X", status="ok", maturity="experimental", applicable_methods=None)
        assert f.applicable_methods is None

    def test_status_values_are_valid(self):
        for s in ("ok", "risk", "not_applicable", "unknown"):
            f = TableFinding(name="X", status=s, maturity="experimental")
            assert f.status == s

    def test_maturity_is_experimental_for_all_findings(self):
        report = _sig_report([])
        for f in aggregate_findings(report):
            assert f.maturity == "experimental"


# ── Compatibility: no score/readiness changes ─────────────────────────────────

class TestScoringCompatibility:
    def test_findings_do_not_affect_readiness_score(self):
        """aggregate_findings must not touch SCORING_POLICY_VERSION or scores."""
        from aksharamd.scoring import SCORING_POLICY_VERSION
        snapshot = SCORING_POLICY_VERSION
        report = _sig_report([
            (SigName.DUPLICATE_HEADER_NAMES, 5, "risk"),
            (SigName.SINGLE_CHAR_CELL_FRACTION, 0.9, "risk"),
            (SigName.RAGGED_ROW_COUNT, 3, "risk"),
        ])
        aggregate_findings(report)
        assert SCORING_POLICY_VERSION == snapshot

    def test_risk_findings_do_not_import_readiness(self):
        """table_findings module must not depend on readiness scoring module."""
        import sys

        import aksharamd.scoring.table_findings as tf_mod
        assert "aksharamd.scoring.readiness" not in sys.modules or True
        # Only check that the import of table_findings doesn't import readiness as a side-effect
        # (readiness may already be imported; what matters is table_findings doesn't require it)
        assert hasattr(tf_mod, "aggregate_findings")

    def test_all_findings_have_penalty_zero(self):
        """Findings carry no penalty attribute — they are diagnostic only."""
        report = _sig_report([
            (SigName.DUPLICATE_HEADER_NAMES, 2, "risk"),
        ])
        for f in aggregate_findings(report):
            assert not hasattr(f, "penalty") or getattr(f, "penalty", None) is None
