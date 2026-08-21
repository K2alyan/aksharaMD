"""Tests for TableMissingValidator (W_TABLE_MISSING, Trigger A).

Trigger A now has two OR-branches:
  * per-line gate:      leader_dot_lines >= 3
  * fallback gate:      total_leader_dot_matches >= 5

Regression cases:
  - ikea3 analogue (many leader-dot lines) → should fire (per-line gate)
  - strikeUnderline analogue (single line, many matches) → should fire
    (fallback gate)
  - clean single-column prose → must not fire
  - table already emitted with pipes → must not fire
  - scanned-OCR document (OCR_REQUIRED active) → must not fire (suppression guard)
  - tiny document (< 20 nonempty lines) → must not fire (tiny-doc guard)
  - non-PDF text file → must not fire (file-type guard)
  - borderline (2 lines, 4 total matches) → must not fire

Threshold + guards come from ``TABLE_READINESS_DIAGNOSTIC_DESIGN.md`` §Signal 3,
Phase 3 scope in ``docs/calibration/PLAN.md``, and the user-confirmed
``PHASE_3_DETECTION.md`` §3.1 Option A design for the fallback gate.
"""
from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.models.validation import Severity, ValidationIssue
from aksharamd.plugins.validators.table_missing import (
    LEADER_DOT_RE,
    TableMissingValidator,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(
    blocks: list[Block],
    file_type: str = "pdf",
    pages: int = 1,
) -> CompilationContext:
    doc = Document(
        source="test." + file_type,
        file_type=file_type,
        pages=pages,
        blocks=blocks,
        metadata={},
    )
    doc.compute_id()
    ctx = CompilationContext(source="test." + file_type, output_dir="/tmp/out")
    ctx.document = doc
    return ctx


def _warning_codes(ctx: CompilationContext) -> set[str]:
    return {
        getattr(i, "code", None)
        for i in ctx.validation.issues
        if i.severity.value == "warning"
    }


def _leader_dot_line(heading: str, page: int) -> str:
    """Produce a TOC-style line: `Heading Title ....... 12`."""
    return f"{heading} ........................... {page}"


def _many_leader_dot_lines(n: int) -> str:
    """Return content with ``n`` leader-dot lines interleaved with prose."""
    lines = []
    for i in range(n):
        lines.append(_leader_dot_line(f"Chapter {i + 1} Overview", i * 4 + 12))
    return "\n".join(lines)


def _padding_prose(n_lines: int) -> str:
    """Non-empty prose lines used to satisfy the 20-nonempty-lines guard."""
    return "\n".join(f"Prose line number {i} with several words." for i in range(n_lines))


def _single_smushed_leader_dot_line(n_matches: int) -> str:
    """Return ONE line containing ``n_matches`` leader-dot matches concatenated.

    Models the ``strikeUnderline`` failure mode: the parser emits the entire
    TOC on a single smushed line, so the per-line gate misses it but the
    total-match fallback should still fire.
    """
    return " ".join(
        f"Chapter {i + 1} ..............{i + 12}"
        for i in range(n_matches)
    )


# ── regex sanity ──────────────────────────────────────────────────────────────


class TestLeaderDotRegex:
    def test_dot_run_matches(self) -> None:
        assert LEADER_DOT_RE.search("Chapter ..... 12")
        assert LEADER_DOT_RE.search("Chapter ......... 12")

    def test_dot_space_pattern_matches(self) -> None:
        assert LEADER_DOT_RE.search("Chapter . . . . . 12")

    def test_short_run_does_not_match(self) -> None:
        assert not LEADER_DOT_RE.search("End of sentence. Next.")
        # 4 dots, no spaces — below threshold
        assert not LEADER_DOT_RE.search("Chapter .... 12")
        # 3 dot-space pairs — below (\.\s){4,}
        assert not LEADER_DOT_RE.search("Chapter . . . 12")


# ── Trigger A per-line gate: fire on leader-dot documents ─────────────────────


class TestTableMissingFiresPerLineGate:
    def test_ikea3_analogue_fires(self) -> None:
        """Analogue of text_misc__ikea3 (21 lines with leader-dot matches)."""
        # 20 lines of prose padding to satisfy min-nonempty guard, plus
        # 3 leader-dot lines to hit the >=3 threshold.
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx), (
            "Expected W_TABLE_MISSING on ikea3 analogue (>=3 leader-dot lines)"
        )
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert "leader_dot_lines" in diag.get("fired_triggers", [])

    def test_diagnostics_recorded_when_warned(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("warned") is True
        assert diag.get("leader_dot_lines") >= 3
        assert diag.get("total_leader_dot_matches") >= 5
        assert diag.get("warning_maturity") == "candidate"

    def test_warning_maturity_is_candidate(self) -> None:
        """The maturity metadata must be surfaced for the eventual cap PR."""
        v = TableMissingValidator()
        assert v.warning_maturity == "candidate"

    def test_docx_eligible(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc], file_type="docx")
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx)


# ── Trigger A fallback gate: fire on smushed single-line docs ─────────────────


class TestTableMissingFiresFallbackGate:
    """The strikeUnderline case: many matches on ONE line."""

    def test_strike_underline_analogue_fires(self) -> None:
        """Analogue of text_simple__strikeUnderline (1 line, 54 total matches).

        Only ONE line has leader-dot matches, so the per-line gate (>=3
        lines) does NOT fire. The total-match fallback (>=5) does.
        """
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        # 54 matches on a single line — models strikeUnderline directly.
        smushed = Block(
            type=BlockType.PARAGRAPH,
            content=_single_smushed_leader_dot_line(54),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, smushed])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx), (
            "Expected W_TABLE_MISSING on strikeUnderline analogue "
            "(1 line, 54 total matches — fallback gate)"
        )
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("warned") is True
        assert diag.get("leader_dot_lines") == 1
        assert diag.get("total_leader_dot_matches") >= 5
        assert "total_leader_dot_matches" in diag.get("fired_triggers", [])
        # Per-line trigger did NOT fire on its own — only the fallback did.
        assert "leader_dot_lines" not in diag.get("fired_triggers", [])

    def test_exactly_five_matches_one_line_fires(self) -> None:
        """Threshold boundary: exactly 5 total matches on one line fires."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        smushed = Block(
            type=BlockType.PARAGRAPH,
            content=_single_smushed_leader_dot_line(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, smushed])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx)

    def test_both_gates_fire_together(self) -> None:
        """When both gates would fire, both should appear in fired_triggers."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        # 5 lines, each with multiple matches → both gates trip.
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("warned") is True
        assert "leader_dot_lines" in diag.get("fired_triggers", [])
        assert "total_leader_dot_matches" in diag.get("fired_triggers", [])


# ── Trigger A: silent on negative cases ───────────────────────────────────────


class TestTableMissingSilent:
    def test_clean_prose_silent(self) -> None:
        """Ordinary prose with no leader dots must not fire."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(40),
            page=1,
            index=0,
            metadata={},
        )
        ctx = _make_ctx([prose])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("leader_dot_lines") == 0
        assert diag.get("total_leader_dot_matches") == 0
        assert diag.get("warned") is False

    def test_two_leader_dot_lines_below_threshold(self) -> None:
        """Boundary: exactly 2 leader-dot lines and 2 total matches must NOT fire.

        The per-line gate requires >=3 lines and the fallback requires >=5
        total matches. Two single-match lines are below both.
        """
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(2),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)

    def test_four_total_matches_below_fallback_threshold(self) -> None:
        """4 matches on 2 lines: below per-line gate (needs 3) AND below
        fallback gate (needs 5). Must stay silent."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        # 2 lines with 2 matches each = 4 total.
        smushed = Block(
            type=BlockType.PARAGRAPH,
            content=(
                "Chapter 1 ..............12 Chapter 2 ..............14\n"
                "Chapter 3 ..............16 Chapter 4 ..............18"
            ),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, smushed])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("total_leader_dot_matches") == 4
        assert diag.get("leader_dot_lines") == 2

    def test_three_total_matches_below_fallback_threshold(self) -> None:
        """3 matches, 1 line: below per-line (needs 3 lines) and below
        fallback (needs 5). Must stay silent."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        smushed = Block(
            type=BlockType.PARAGRAPH,
            content=_single_smushed_leader_dot_line(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, smushed])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("total_leader_dot_matches") == 3

    def test_clean_markdown_table_silent(self) -> None:
        """A properly-emitted markdown table must not fire — leader dots are
        the failure indicator; a real table has none."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(30),
            page=1,
            index=0,
            metadata={},
        )
        table = Block(
            type=BlockType.TABLE,
            content="| Product | Price |\n| --- | --- |\n| Widget | 12.50 |\n",
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, table])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)


# ── Guard 1: file-type eligibility ────────────────────────────────────────────


class TestTableMissingFileTypeGuard:
    def test_txt_file_not_evaluated(self) -> None:
        """Plain text files are not eligible — leader dots may be intentional."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc], file_type="txt")
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)
        # And no diagnostics recorded (validator exited early)
        assert "table_missing_diagnostics" not in ctx.document.metadata

    def test_markdown_file_not_evaluated(self) -> None:
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc], file_type="md")
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)

    def test_txt_file_fallback_gate_not_evaluated(self) -> None:
        """File-type guard also applies to the fallback gate."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        smushed = Block(
            type=BlockType.PARAGRAPH,
            content=_single_smushed_leader_dot_line(54),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, smushed], file_type="txt")
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)


# ── Guard 2: OCR_REQUIRED suppression ─────────────────────────────────────────


class TestTableMissingOcrSuppression:
    def test_ocr_required_suppresses_warning(self) -> None:
        """If OCR_REQUIRED already fired, the leader-dot signal must not fire —
        image-only documents are a separate failure class."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        # Emulate the structure validator having already fired OCR_REQUIRED
        ctx.add_issue(
            ValidationIssue(
                severity=Severity.WARNING,
                code="OCR_REQUIRED",
                message="scanned pages, OCR not installed",
            ),
        )
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("warned") is False
        assert "OCR_REQUIRED" in (diag.get("suppressed_reason") or "")

    def test_ocr_required_suppresses_fallback_gate_too(self) -> None:
        """OCR_REQUIRED guard applies to the smushed-single-line fallback."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        smushed = Block(
            type=BlockType.PARAGRAPH,
            content=_single_smushed_leader_dot_line(54),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, smushed])
        ctx.add_issue(
            ValidationIssue(
                severity=Severity.WARNING,
                code="OCR_REQUIRED",
                message="scanned pages, OCR not installed",
            ),
        )
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)


# ── Guard 3: tiny-doc guard ───────────────────────────────────────────────────


class TestTableMissingTinyDocGuard:
    def test_tiny_doc_not_evaluated(self) -> None:
        """Documents with fewer than 20 nonempty lines must not fire the warning.

        This models the case where a very short scanned-page fallback or a
        single-line brochure would otherwise trip the trigger.
        """
        # 5 leader-dot lines but only 5 nonempty lines total — under the guard.
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(5),
            page=1,
            index=0,
            metadata={},
        )
        ctx = _make_ctx([toc])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("warned") is False
        assert "nonempty" in (diag.get("suppressed_reason") or "")

    def test_boundary_at_twenty_lines_evaluated(self) -> None:
        """20 nonempty lines is above the guard; evaluation proceeds."""
        # 20 nonempty prose lines and 3 leader-dot lines → should fire.
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(3),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx)

    def test_tiny_doc_guard_applies_to_fallback_gate(self) -> None:
        """A 1-line document with 54 matches must NOT fire — tiny-doc guard."""
        smushed = Block(
            type=BlockType.PARAGRAPH,
            content=_single_smushed_leader_dot_line(54),
            page=1,
            index=0,
            metadata={},
        )
        ctx = _make_ctx([smushed])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("warned") is False
        assert "nonempty" in (diag.get("suppressed_reason") or "")


# ── Diagnostics: both counts always recorded ─────────────────────────────────


class TestTableMissingDiagnostics:
    def test_both_counts_recorded_when_silent(self) -> None:
        """When no warning fires, both leader_dot_lines AND
        total_leader_dot_matches must still appear in diagnostics.

        Downstream calibration audits and the eventual cap PR need both
        counts even on silent cases to build precision/recall statistics.
        """
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(40),
            page=1,
            index=0,
            metadata={},
        )
        ctx = _make_ctx([prose])
        ctx = TableMissingValidator().execute(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert "leader_dot_lines" in diag
        assert "total_leader_dot_matches" in diag
        assert diag.get("leader_dot_lines") == 0
        assert diag.get("total_leader_dot_matches") == 0

    def test_both_counts_recorded_on_borderline(self) -> None:
        """Borderline case (2 lines, 2 matches): both counts still recorded."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(2),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("leader_dot_lines") == 2
        assert diag.get("total_leader_dot_matches") == 2
        assert diag.get("warned") is False


# ── code stability + no-mutation contract ─────────────────────────────────────


class TestTableMissingContract:
    def test_no_document_is_noop(self) -> None:
        ctx = CompilationContext(source="none.pdf", output_dir="/tmp/out")
        ctx.document = None
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)

    def test_no_output_mutation(self) -> None:
        """The validator must not modify block content."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc_content = _many_leader_dot_lines(4)
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=toc_content,
            page=1,
            index=1,
            metadata={},
        )
        original_prose = prose.content
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        assert ctx.document.blocks[0].content == original_prose
        assert ctx.document.blocks[1].content == toc_content

    def test_no_score_cap_applied(self) -> None:
        """Detection-only signal — must not attach a readiness cap in v1."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(5),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None

    def test_warning_code_is_stable(self) -> None:
        """The warning code string is public API and must not be renamed."""
        prose = Block(
            type=BlockType.PARAGRAPH,
            content=_padding_prose(20),
            page=1,
            index=0,
            metadata={},
        )
        toc = Block(
            type=BlockType.PARAGRAPH,
            content=_many_leader_dot_lines(4),
            page=1,
            index=1,
            metadata={},
        )
        ctx = _make_ctx([prose, toc])
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx)
