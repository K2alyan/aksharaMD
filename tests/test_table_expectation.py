"""Tests for aksharamd/scoring/table_expectation.py and
aksharamd/plugins/validators/table_expectation.py — Phase 5.
"""
from __future__ import annotations

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.scoring.table_expectation import (
    RejectedTableCandidate,
    TableExpectationReport,
    TableExpectationSignal,
    TableExpectationSignalName,
    aggregate_expectation_findings,
    compute_table_expectation,
)
from aksharamd.scoring.table_findings import aggregate_findings, TableFinding
from aksharamd.scoring.table_quality import TableQualityReport, TableQualitySignal


# ── Helpers ────────────────────────────────────────────────────────────────────

def _para(content: str, page: int = 1) -> Block:
    return Block(type=BlockType.PARAGRAPH, content=content, page=page)


def _heading(content: str, page: int = 1) -> Block:
    return Block(type=BlockType.HEADING, content=content, level=2, page=page)


def _rejected(
    strategy: str = "whitespace",
    reasons: list[str] | None = None,
    dot_fraction: float = 0.0,
    empty_fraction: float = 0.0,
    col_count: int = 4,
    row_count: int = 8,
    page: int = 1,
) -> dict:
    return {
        "strategy": strategy,
        "page": page,
        "bbox": [50.0, 100.0, 500.0, 600.0],
        "row_count": row_count,
        "col_count": col_count,
        "rejection_reasons": reasons or [],
        "quality_metrics": {
            "dot_leader_fraction": dot_fraction,
            "empty_cell_fraction": empty_fraction,
            "col_count": col_count,
        },
    }


def _sig_report_minimal() -> TableQualityReport:
    """Minimal TableQualityReport for testing aggregate_findings."""
    return TableQualityReport(
        table_id="test",
        block_id="test",
        row_count=3,
        column_count=3,
        signals=[],
        overall_status="ok",
    )


# ── compute_table_expectation: basic cases ─────────────────────────────────────

class TestComputeTableExpectationBasic:
    def test_empty_everything_returns_false(self):
        report = compute_table_expectation(page=1, blocks=[], rejected_candidates=[])
        assert report.expected == "false"
        assert report.page == 1
        assert report.maturity == "experimental"

    def test_no_rejected_no_signals_returns_false(self):
        blocks = [_para("Hello world. This is a simple paragraph.")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        assert report.expected == "false"

    def test_one_rejected_no_other_signal_returns_unknown(self):
        """One rejected candidate with no corroborating signal returns unknown.

        Note: dot_leader also triggers LEADER_DOT_ROWS (content family), so we
        use word_split here to ensure only the parser family fires.
        """
        report = compute_table_expectation(
            page=1,
            blocks=[_para("Some ordinary text without numbers or captions")],
            rejected_candidates=[_rejected(reasons=["word_split"])],
        )
        assert report.expected == "unknown"

    def test_rejected_plus_caption_returns_true(self):
        """Rejected candidate + caption signal → expected=true."""
        blocks = [_para("Table 3 shows the quarterly results.")]
        report = compute_table_expectation(
            page=1,
            blocks=blocks,
            rejected_candidates=[_rejected(reasons=["dot_leader"])],
        )
        assert report.expected == "true"

    def test_rejected_plus_leader_dot_returns_true(self):
        """Rejected candidate (parser) + dot_leader signal (content) → true."""
        report = compute_table_expectation(
            page=1,
            blocks=[],
            rejected_candidates=[_rejected(reasons=["dot_leader"], dot_fraction=0.82)],
        )
        # REJECTED_CANDIDATE fires (parser) + LEADER_DOT_ROWS fires (content) → true
        assert report.expected == "true"

    def test_rejected_plus_numeric_alignment_alone_returns_unknown(self):
        """Rejected candidate + numeric alignment alone → unknown (not true).

        parser + numeric_column_alignment is an insufficient pair: both fire on
        chart pages reacting to the same visual structure (data axes, annotations).
        A third independent cue (caption, leader dots, archetype) is required.
        """
        numeric_content = "\n".join([
            "2021 12.5 34.2 0.8",
            "2022 14.1 31.7 1.2",
            "2023 16.0 29.3 2.1",
        ])
        blocks = [_para(numeric_content)]
        report = compute_table_expectation(
            page=1,
            blocks=blocks,
            rejected_candidates=[_rejected(reasons=["word_split"])],
        )
        assert report.expected == "unknown"

    def test_rejected_plus_numeric_plus_caption_returns_true(self):
        """Rejected candidate + numeric + table caption → true (three cues, caption breaks the tie)."""
        numeric_content = "\n".join([
            "Table 3. Annual returns by asset class",
            "2021 12.5 34.2 0.8",
            "2022 14.1 31.7 1.2",
            "2023 16.0 29.3 2.1",
        ])
        blocks = [_para(numeric_content)]
        report = compute_table_expectation(
            page=1,
            blocks=blocks,
            rejected_candidates=[_rejected(reasons=["word_split"])],
        )
        assert report.expected == "true"

    def test_only_caption_no_rejected_returns_unknown(self):
        """Caption alone (without rejected candidate) is not enough for true."""
        blocks = [_para("Table 1 presents the experimental results.")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        # Only CAPTION_NEARBY fires (text family) — no parser signal
        assert report.expected == "unknown"

    def test_only_numeric_alignment_returns_unknown(self):
        """Numeric alignment alone returns unknown."""
        numeric_content = "\n".join([
            "100 200 300 400",
            "150 250 350 450",
            "200 300 400 500",
        ])
        blocks = [_para(numeric_content)]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        assert report.expected == "unknown"


# ── Signal: CAPTION_NEARBY ────────────────────────────────────────────────────

class TestCaptionSignal:
    def test_fires_on_table_caption(self):
        blocks = [_para("Table 3 shows the quarterly results.")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.CAPTION_NEARBY)
        assert sig.status == "risk"

    def test_does_not_fire_on_figure_caption(self):
        """Figure captions no longer trigger CAPTION_NEARBY — only Table does."""
        blocks = [_para("Figure 2.1 illustrates the network topology.")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.CAPTION_NEARBY)
        assert sig.status == "ok"

    def test_does_not_fire_on_exhibit_caption(self):
        """Exhibit captions no longer trigger CAPTION_NEARBY."""
        blocks = [_para("Exhibit A summarizes financial data.")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.CAPTION_NEARBY)
        assert sig.status == "ok"

    def test_does_not_fire_on_plain_text(self):
        blocks = [_para("The experiment was conducted over three months.")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.CAPTION_NEARBY)
        assert sig.status == "ok"

    def test_does_not_fire_on_empty_blocks(self):
        report = compute_table_expectation(page=1, blocks=[], rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.CAPTION_NEARBY)
        assert sig.status == "ok"

    def test_fires_on_heading_with_caption_pattern(self):
        blocks = [_heading("Table 4 — Summary Statistics")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.CAPTION_NEARBY)
        assert sig.status == "risk"


# ── Signal: NUMERIC_COLUMN_ALIGNMENT ─────────────────────────────────────────

class TestNumericAlignmentSignal:
    def test_fires_on_numeric_heavy_paragraph(self):
        content = "\n".join([
            "2020 1234 5678 90.1",
            "2021 1350 6000 91.5",
            "2022 1480 6200 92.0",
        ])
        blocks = [_para(content)]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT)
        assert sig.status == "risk"
        assert sig.value >= 3

    def test_does_not_fire_on_prose(self):
        blocks = [_para("The study found that economic indicators improved significantly over the review period.")]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT)
        assert sig.status == "ok"

    def test_does_not_fire_on_fewer_than_3_qualifying_lines(self):
        content = "\n".join([
            "100 200 300",  # qualifies
            "400 500 600",  # qualifies
            "prose text here without numbers",  # does not qualify
        ])
        blocks = [_para(content)]
        report = compute_table_expectation(page=1, blocks=blocks, rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT)
        assert sig.status == "ok"


# ── Signal: LEADER_DOT_ROWS ───────────────────────────────────────────────────

class TestLeaderDotSignal:
    def test_fires_when_dot_leader_in_rejection(self):
        report = compute_table_expectation(
            page=1,
            blocks=[],
            rejected_candidates=[_rejected(reasons=["dot_leader"], dot_fraction=0.82)],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "risk"

    def test_does_not_fire_when_no_dot_leader(self):
        report = compute_table_expectation(
            page=1,
            blocks=[],
            rejected_candidates=[_rejected(reasons=["word_split"])],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "ok"

    def test_does_not_fire_with_no_rejected(self):
        report = compute_table_expectation(page=1, blocks=[], rejected_candidates=[])
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "ok"

    def test_fires_from_paragraph_text_dot_leaders(self):
        """Source 2: paragraph blocks with >=3 lines of 4+ consecutive dots."""
        content = "\n".join([
            "Cash and equivalents ............ 1,234",
            "Investments ..................... 5,678",
            "Total assets ................... 6,912",
        ])
        report = compute_table_expectation(
            page=1,
            blocks=[_para(content)],
            rejected_candidates=[],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "risk"
        assert sig.evidence["source"] == "paragraph_text"
        assert sig.evidence["dot_line_count"] >= 3

    def test_paragraph_dot_leader_requires_three_lines(self):
        """Fewer than 3 dot-leader lines in paragraph text does not fire."""
        content = "\n".join([
            "Cash ............................ 1,234",
            "Investments .................... 5,678",
            "Plain line without dots",
            "Another plain line",
        ])
        report = compute_table_expectation(
            page=1,
            blocks=[_para(content)],
            rejected_candidates=[],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "ok"

    def test_rejected_candidate_takes_precedence_over_paragraph_text(self):
        """When both sources exist, rejected-candidate path fires first."""
        content = "\n".join([
            "Cash ............ 1,234",
            "Total ........... 6,912",
            "Balance ......... 5,678",
        ])
        report = compute_table_expectation(
            page=1,
            blocks=[_para(content)],
            rejected_candidates=[_rejected(reasons=["dot_leader"], dot_fraction=0.9)],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "risk"
        assert sig.evidence["source"] == "rejected_candidate"

    def test_paragraph_dot_leader_enables_expected_true_without_rejected_candidate(self):
        """fqr-retail-blackrock scenario: pdfplumber skipped, but text + dot-leader fires."""
        dot_leader_content = "\n".join([
            "Fixed income .................. 42.3%",
            "Equity ........................ 38.1%",
            "Alternatives .................. 12.6%",
            "Cash .......................... 7.0%",
        ])
        numeric_content = "\n".join([
            "2021 42.3 38.1 12.6 7.0",
            "2022 41.5 39.0 13.0 6.5",
            "2023 43.0 37.5 12.0 7.5",
        ])
        blocks = [_para(dot_leader_content), _para(numeric_content)]
        report = compute_table_expectation(
            page=1,
            blocks=blocks,
            rejected_candidates=[],
        )
        # LEADER_DOT_ROWS (content) + NUMERIC_COLUMN_ALIGNMENT (text) → 2 families → true
        assert report.expected == "true"

    def test_spaced_dot_leader_pattern_fires(self):
        """Spaced-dot pattern '. . . .' fires (common in financial schedules)."""
        content = "Security name . . . . . . . . . . . USD 5,000 $ 5,012"
        report = compute_table_expectation(
            page=1,
            blocks=[
                _para(content),
                _para("Another row . . . . . . . . . 3,200 3,218"),
                _para("Third row . . . . . . . . . . 1,800 1,807"),
            ],
            rejected_candidates=[],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "risk"

    def test_cross_block_aggregation_fires_when_each_block_has_one_line(self):
        """Numeric alignment aggregates across small single-line blocks (fqr layout pattern)."""
        blocks = [
            _para(". . . 250 249,760 . . . 300 301,237"),
            _para(". . . 1,000 1,002,053 . . . 711 711,251"),
            _para(". . 500 500,889 . . 500 500,639"),
        ]
        report = compute_table_expectation(
            page=1,
            blocks=blocks,
            rejected_candidates=[],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT)
        assert sig.status == "risk"
        assert sig.value >= 3

    def test_cross_block_leader_dot_aggregation_fires_when_each_block_has_one_dot_line(self):
        """Leader-dot aggregates across small single-line blocks."""
        blocks = [
            _para(". . . . . . . . . 250 249,760"),
            _para(". . . . . . 1,000 1,002,053"),
            _para(". . . . . 500 500,889"),
        ]
        report = compute_table_expectation(
            page=1,
            blocks=blocks,
            rejected_candidates=[],
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.LEADER_DOT_ROWS)
        assert sig.status == "risk"
        assert sig.evidence["dot_line_count"] == 3


# ── Signal: DOC_TABLE_HEAVY ───────────────────────────────────────────────────

class TestDocTableHeavySignal:
    def test_fires_when_table_heavy_and_no_table(self):
        report = compute_table_expectation(
            page=1,
            blocks=[_para("text")],
            rejected_candidates=[],
            doc_type="table_heavy",
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.DOC_TABLE_HEAVY)
        assert sig.status == "risk"

    def test_does_not_fire_when_not_table_heavy(self):
        report = compute_table_expectation(
            page=1,
            blocks=[],
            rejected_candidates=[],
            doc_type="native_text",
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.DOC_TABLE_HEAVY)
        assert sig.status == "ok"

    def test_does_not_fire_when_doc_type_is_none(self):
        report = compute_table_expectation(
            page=1,
            blocks=[],
            rejected_candidates=[],
            doc_type=None,
        )
        sig = next(s for s in report.signals if s.name == TableExpectationSignalName.DOC_TABLE_HEAVY)
        assert sig.status == "ok"


# ── RejectedTableCandidate model ──────────────────────────────────────────────

class TestRejectedTableCandidate:
    def test_serializes_cleanly(self):
        cand = RejectedTableCandidate(
            strategy="whitespace",
            page=3,
            bbox=[50.0, 100.0, 500.0, 600.0],
            row_count=8,
            col_count=7,
            rejection_reasons=["dot_leader"],
            quality_metrics={"dot_leader_fraction": 0.82, "col_count": 7},
        )
        d = cand.model_dump()
        assert d["strategy"] == "whitespace"
        assert d["page"] == 3
        assert d["bbox"] == [50.0, 100.0, 500.0, 600.0]
        assert d["rejection_reasons"] == ["dot_leader"]
        assert d["quality_metrics"]["dot_leader_fraction"] == 0.82

    def test_empty_quality_metrics_allowed(self):
        cand = RejectedTableCandidate(
            strategy="hrule",
            page=1,
            bbox=[0.0, 0.0, 0.0, 0.0],
            row_count=5,
            col_count=3,
            rejection_reasons=["too_sparse"],
        )
        assert cand.quality_metrics == {}


# ── TABLE_EXPECTED_BUT_NOT_EXTRACTED in table_findings.py ─────────────────────

class TestExpectedNotExtractedFinding:
    def test_appears_in_aggregate_findings(self):
        report = _sig_report_minimal()
        findings = aggregate_findings(report)
        names = [f.name for f in findings]
        assert "TABLE_EXPECTED_BUT_NOT_EXTRACTED" in names

    def test_is_always_not_applicable_in_aggregate(self):
        report = _sig_report_minimal()
        findings = aggregate_findings(report)
        f = next(f for f in findings if f.name == "TABLE_EXPECTED_BUT_NOT_EXTRACTED")
        assert f.status == "not_applicable"
        assert f.maturity == "experimental"

    def test_aggregate_findings_returns_six(self):
        report = _sig_report_minimal()
        findings = aggregate_findings(report)
        assert len(findings) == 6


# ── aggregate_expectation_findings ───────────────────────────────────────────

class TestAggregateExpectationFindings:
    def test_returns_list(self):
        report = compute_table_expectation(
            page=2,
            blocks=[_para("Table 5 shows results.")],
            rejected_candidates=[_rejected(reasons=["dot_leader"])],
        )
        result = aggregate_expectation_findings(report)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_finding_has_expected_keys(self):
        report = compute_table_expectation(
            page=2,
            blocks=[],
            rejected_candidates=[_rejected(reasons=["word_split"])],
        )
        result = aggregate_expectation_findings(report)
        finding = result[0]
        assert finding["name"] == "TABLE_EXPECTED_BUT_NOT_EXTRACTED"
        assert finding["status"] == "not_applicable"
        assert finding["maturity"] == "experimental"
        assert "page" in finding
        assert "expected" in finding


# ── Validator integration ──────────────────────────────────────────────────────

def _run_validator(
    blocks: list[Block],
    rejected_by_page: dict | None = None,
    doc_type: str | None = None,
) -> CompilationContext:
    from aksharamd.plugins.validators.table_expectation import TableExpectationValidator
    metadata: dict = {}
    if rejected_by_page:
        metadata["table_rejected_candidates_by_page"] = rejected_by_page
    if doc_type:
        metadata["pdf_classification"] = doc_type
    doc = Document(source="test.pdf", blocks=blocks, metadata=metadata)
    doc.compute_id()
    ctx = CompilationContext(source="test.pdf")
    ctx.document = doc
    return TableExpectationValidator().execute(ctx)


class TestTableExpectationValidator:
    def test_no_warning_when_no_rejected_candidates(self):
        blocks = [_para("Some text", page=1)]
        ctx = _run_validator(blocks)
        codes = [i.code for i in ctx.validation.issues]
        assert "W_TABLE_EXPECTED_NOT_EXTRACTED" not in codes

    def test_warning_emitted_when_expected_true_and_no_table(self):
        """Rejected candidate + caption → expected=true → warning emitted."""
        blocks = [_para("Table 4 shows the annual returns.", page=1)]
        rejected_by_page = {1: [_rejected(reasons=["dot_leader"])]}
        ctx = _run_validator(blocks, rejected_by_page=rejected_by_page)
        codes = [i.code for i in ctx.validation.issues]
        assert "W_TABLE_EXPECTED_NOT_EXTRACTED" in codes

    def test_no_warning_when_table_already_extracted(self):
        """If the page already has a table block, no warning should be emitted."""
        from aksharamd.models.table import TableCell, TableData
        cells = [
            TableCell(text="A", row=0, column=0), TableCell(text="B", row=0, column=1),
            TableCell(text="1", row=1, column=0), TableCell(text="2", row=1, column=1),
        ]
        td = TableData(row_count=2, column_count=2, cells=cells, header_rows=[0])
        table_block = Block.from_table(td, page=1)
        rejected_by_page = {1: [_rejected(reasons=["dot_leader"])]}
        ctx = _run_validator(
            [table_block, _para("Table 1 shows results.", page=1)],
            rejected_by_page=rejected_by_page,
        )
        codes = [i.code for i in ctx.validation.issues]
        assert "W_TABLE_EXPECTED_NOT_EXTRACTED" not in codes

    def test_reports_stored_in_document_metadata(self):
        blocks = [_para("Table 5 quarterly breakdown.", page=2)]
        rejected_by_page = {2: [_rejected(reasons=["dot_leader"], page=2)]}
        ctx = _run_validator(blocks, rejected_by_page=rejected_by_page)
        assert "table_expectation_reports" in ctx.document.metadata
        reports = ctx.document.metadata["table_expectation_reports"]
        assert isinstance(reports, list)
        assert len(reports) >= 1

    def test_no_score_change_no_error(self):
        """Validator does not raise and does not set error state."""
        blocks = [_para("Normal text page", page=1)]
        ctx = _run_validator(blocks)
        assert ctx.validation.passed

    def test_identity_unchanged(self):
        """Running the validator does not change block checksums."""
        blocks = [_para("Some paragraph text.", page=1)]
        block_id_before = blocks[0].id
        checksum_before = blocks[0].checksum
        _run_validator(blocks)
        assert blocks[0].id == block_id_before
        assert blocks[0].checksum == checksum_before
