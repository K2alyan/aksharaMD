"""Regression assertions for implemented warning codes.

These tests use synthetic block fixtures that reproduce the structural
characteristics of known real-document false-safes. They are NOT integration
tests with real PDFs (those live in parsebench), but they assert that:

  1. Each implemented warning fires on a representative positive fixture.
  2. Each implemented warning is absent on representative negative fixtures.
  3. Diagnostic metadata fields are present and stable.
  4. Warning codes are stable (not renamed or removed).
  5. No alerting warning fires on clean PASS/PASS_WITH_WARNINGS equivalents.

Calibration document correspondence (parsebench calibration corpus):

  W_MULTICOLUMN_ORDER positives:
    - text_multicolumns__3colpres (trans=0.30)
    - text_multicolumns__4c (trans=~0.35+)
  W_MULTICOLUMN_ORDER controls (must stay silent):
    - text_multicolumns__battery (no column gap)
    - text_multicolumns__2colmercedes (gap_rel=0.27, but trans=0.0)
  W_MULTICOLUMN_ORDER false negatives (span-level, not testable here):
    - text_misc__ikea3, text_multicolumns__elpais, text_multicolumns__simple2

  W_HEADER_FOOTER_TABLE_GARBLED positives:
    - text_multicolumns__pwc (y_top=-36, short_frac=0.58)
  W_HEADER_FOOTER_TABLE_GARBLED controls (must stay silent):
    - text_multicolumns__gridofnumbers (in_margin=True, short_frac=0.00)
    - All 20 non-PWC docs in calibration set

  W_TABLE_MISSING positives (Phase 3, Trigger A leader-dot):
    - text_misc__ikea3 (21 lines with leader-dot matches — per-line gate)
    - text_simple__strikeUnderline (1 line, 54 total matches — fallback gate)
  W_TABLE_MISSING controls (must stay silent):
    - All 19 non-ikea3/non-strikeUnderline docs in calibration set —
      0 leader-dot lines AND 0 total matches observed.

  W_ENCODING_ARTIFACTS positives (Phase 3, XML residue + mojibake):
    - text_dense__de (numbered pt-tag residue + replacement chars)
  W_ENCODING_ARTIFACTS controls (must stay silent):
    - All 20 non-de docs in calibration set — 0 XML fragments AND
      mojibake density below the 0.5 percent threshold.

Phase 1 re-score findings (2026-07-13):
  - Silent false-safe rate: 35% (6/17 HIGH-band FAIL docs, no alerting warning)
  - 3colpres, 4c, pwc: correctly warned
  - de, japanese, letter3, myctophidae, simple2, strikeUnderline: still silent
  - Gate: BLOCKED (35% > 10% target)

Phase 3 (this PR) closes ikea3 and strikeUnderline (via W_TABLE_MISSING
per-line + fallback gates) and de (via new W_ENCODING_ARTIFACTS).
japanese, letter3, myctophidae, simple2 remain open.
"""
from __future__ import annotations

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.plugins.validators.encoding_artifacts import EncodingArtifactsValidator
from aksharamd.plugins.validators.header_footer_table import HeaderFooterTableValidator
from aksharamd.plugins.validators.multicolumn import MultiColumnOrderValidator
from aksharamd.plugins.validators.table_missing import TableMissingValidator

# ── shared helpers ────────────────────────────────────────────────────────────

_PAGE_HEIGHT = 841.89  # A4
_PAGE_WIDTH = 595.0


def _make_ctx(
    blocks: list[Block],
    page_height: float = _PAGE_HEIGHT,
    page_width: float = _PAGE_WIDTH,
) -> CompilationContext:
    doc = Document(
        source="regression_test.pdf",
        file_type="pdf",
        pages=1,
        blocks=blocks,
        metadata={"pdf_column_info": {1: {"page_width": page_width, "page_height": page_height}}},
    )
    doc.compute_id()
    ctx = CompilationContext(source="regression_test.pdf", output_dir="/tmp/out")
    ctx.document = doc
    return ctx


def _warning_codes(ctx: CompilationContext) -> set[str]:
    return {
        getattr(i, "code", None)
        for i in ctx.validation.issues
        if i.severity.value == "warning"
    }


# ── W_MULTICOLUMN_ORDER ───────────────────────────────────────────────────────

# Synthetic fixture approximating text_multicolumns__3colpres (trans=0.30):
# Two columns of text interleaved — left col words at x0=50, right col at x0=350.
# Blocks alternate between columns faster than threshold.
_MC_BLOCKS_INTERLEAVED = [
    Block(type=BlockType.PARAGRAPH, content="word " * 10, page=1, index=i,
          metadata={"x0": 50.0 if i % 2 == 0 else 350.0, "y0": float(i * 40)},
          confidence=ExtractionConfidence.EXTRACTED)
    for i in range(10)
]

# Single-column fixture (all blocks on same x side) — no interleaving.
_MC_BLOCKS_SINGLE_COLUMN = [
    Block(type=BlockType.PARAGRAPH, content="word " * 10, page=1, index=i,
          metadata={"x0": 50.0, "y0": float(i * 40)},
          confidence=ExtractionConfidence.EXTRACTED)
    for i in range(10)
]

# Two-column fixture with NO transitions — sorted correctly (all left first, then right).
_MC_BLOCKS_SORTED = (
    [Block(type=BlockType.PARAGRAPH, content="word " * 10, page=1, index=i,
           metadata={"x0": 50.0, "y0": float(i * 40)},
           confidence=ExtractionConfidence.EXTRACTED)
     for i in range(5)]
    + [Block(type=BlockType.PARAGRAPH, content="word " * 10, page=1, index=i + 5,
             metadata={"x0": 350.0, "y0": float(i * 40)},
             confidence=ExtractionConfidence.EXTRACTED)
       for i in range(5)]
)


class TestMultiColumnOrderWarning:
    """Regression suite for W_MULTICOLUMN_ORDER."""

    def test_interleaved_two_columns_warns(self) -> None:
        """Approximates text_multicolumns__3colpres: alternating x0 → high trans → warn."""
        ctx = _make_ctx(_MC_BLOCKS_INTERLEAVED)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" in _warning_codes(ctx), (
            "Expected W_MULTICOLUMN_ORDER on interleaved two-column fixture "
            "(analogous to text_multicolumns__3colpres, trans=0.30)"
        )

    def test_single_column_silent(self) -> None:
        """Single-column doc must not warn (battery / eastbaytimes analogue)."""
        ctx = _make_ctx(_MC_BLOCKS_SINGLE_COLUMN)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" not in _warning_codes(ctx), (
            "W_MULTICOLUMN_ORDER must not fire on single-column content "
            "(analogous to text_multicolumns__battery)"
        )

    def test_sorted_two_columns_silent(self) -> None:
        """Correctly-sorted two-column doc must not warn (2colmercedes analogue)."""
        ctx = _make_ctx(_MC_BLOCKS_SORTED)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" not in _warning_codes(ctx), (
            "W_MULTICOLUMN_ORDER must not fire on correctly-sorted two-column content "
            "(analogous to text_multicolumns__2colmercedes)"
        )

    def test_diagnostic_metadata_always_present(self) -> None:
        """Diagnostics must be stored even when no warning fires."""
        ctx = _make_ctx(_MC_BLOCKS_SINGLE_COLUMN)
        ctx = MultiColumnOrderValidator().execute(ctx)
        diag = ctx.document.metadata.get("multicolumn_diagnostics", {})
        assert "page_analyses" in diag
        assert "warned" in diag
        assert diag["warned"] is False

    def test_diagnostic_metadata_on_warning(self) -> None:
        """Diagnostics must include problem_pages when warning fires."""
        ctx = _make_ctx(_MC_BLOCKS_INTERLEAVED)
        ctx = MultiColumnOrderValidator().execute(ctx)
        diag = ctx.document.metadata.get("multicolumn_diagnostics", {})
        assert diag.get("warned") is True
        assert len(diag.get("problem_pages", [])) >= 1

    def test_warning_code_is_stable(self) -> None:
        """The warning code string must not have been renamed."""
        ctx = _make_ctx(_MC_BLOCKS_INTERLEAVED)
        ctx = MultiColumnOrderValidator().execute(ctx)
        codes = _warning_codes(ctx)
        assert "W_MULTICOLUMN_ORDER" in codes, (
            "Warning code W_MULTICOLUMN_ORDER renamed or removed — "
            "downstream consumers depend on this exact string"
        )

    def test_no_output_mutation(self) -> None:
        """The validator must not alter block content."""
        original_content = [b.content for b in _MC_BLOCKS_INTERLEAVED]
        ctx = _make_ctx(_MC_BLOCKS_INTERLEAVED)
        ctx = MultiColumnOrderValidator().execute(ctx)
        for block, orig in zip(ctx.document.blocks, original_content):
            assert block.content == orig

    def test_no_score_cap_applied(self) -> None:
        """W_MULTICOLUMN_ORDER is warning-only and must not set a score cap."""
        ctx = _make_ctx(_MC_BLOCKS_INTERLEAVED)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None


# ── W_HEADER_FOOTER_TABLE_GARBLED ─────────────────────────────────────────────

# Synthetic fixture for PWC analogue: table in top margin with high short-cell fraction.
_PWC_TABLE_CONTENT = """\
| Tokio M | Financial |
| --- | --- |
| arine | and |
| Holdings, Inc | Non-Financial |
| . \\| Integrated Annual Report 2021 | Data |
| 176 | 17 |
| Indeendent Audtos Reot Indeendent Audtos Reot | |"""

_PWC_BBOX = (-0.003, -36.85, 1172.08, 406.77)  # y_top=-36, clearly in top margin

# Mid-page table with long cells (gridofnumbers analogue) — must stay silent.
_LONG_CELL_TABLE = """\
| Product Category | Previous Quarter | Current Quarter | Year-over-Year |
| --- | --- | --- | --- |
| Software Subscriptions | 125,400,000 | 138,200,000 | +10.2% increase |
| Professional Services | 42,100,000 | 45,800,000 | +8.8% increase |"""

_MID_PAGE_BBOX = (50.0, 200.0, 500.0, 350.0)  # well within page body

# gridofnumbers analogue: table is in margin but cells are NOT short.
_GRIDOFNUMBERS_BBOX = (50.0, 5.0, 500.0, 120.0)  # y_top=5 → in top margin


def _table_block(content: str, bbox: tuple) -> Block:
    return Block(
        type=BlockType.TABLE,
        content=content,
        page=1, index=0,
        metadata={"table_bbox": bbox},
    )


class TestHeaderFooterTableWarning:
    """Regression suite for W_HEADER_FOOTER_TABLE_GARBLED."""

    def test_pwc_analogue_warns(self) -> None:
        """Approximates text_multicolumns__pwc: top-margin table with garbled cells."""
        ctx = _make_ctx([_table_block(_PWC_TABLE_CONTENT, _PWC_BBOX)])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" in _warning_codes(ctx), (
            "Expected W_HEADER_FOOTER_TABLE_GARBLED on margin+short-cell fixture "
            "(analogous to text_multicolumns__pwc, y_top=-36, short_frac=0.58)"
        )

    def test_mid_page_long_cells_silent(self) -> None:
        """Mid-page table with substantive cells must not warn."""
        ctx = _make_ctx([_table_block(_LONG_CELL_TABLE, _MID_PAGE_BBOX)])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" not in _warning_codes(ctx)

    def test_margin_table_long_cells_silent(self) -> None:
        """Table in margin but with long cells must not warn (one-signal-only case).

        Corresponds to gridofnumbers in the calibration corpus:
        in_margin=True, short_frac=0.00 → warn=False.
        """
        ctx = _make_ctx([_table_block(_LONG_CELL_TABLE, _GRIDOFNUMBERS_BBOX)])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" not in _warning_codes(ctx), (
            "W_HEADER_FOOTER_TABLE_GARBLED must not fire when margin signal fires "
            "but short-cell signal does not (analogous to text_multicolumns__gridofnumbers)"
        )

    def test_diagnostic_metadata_always_present(self) -> None:
        """Diagnostics must be stored even when no warning fires."""
        ctx = _make_ctx([_table_block(_LONG_CELL_TABLE, _MID_PAGE_BBOX)])
        ctx = HeaderFooterTableValidator().execute(ctx)
        diag = ctx.document.metadata.get("header_footer_table_diagnostics", {})
        assert "table_analyses" in diag
        assert "warned" in diag
        assert diag["warned"] is False

    def test_diagnostic_metadata_on_warning(self) -> None:
        """Diagnostics must include problem_tables when warning fires."""
        ctx = _make_ctx([_table_block(_PWC_TABLE_CONTENT, _PWC_BBOX)])
        ctx = HeaderFooterTableValidator().execute(ctx)
        diag = ctx.document.metadata.get("header_footer_table_diagnostics", {})
        assert diag.get("warned") is True
        assert len(diag.get("problem_tables", [])) >= 1

    def test_warning_code_is_stable(self) -> None:
        """The warning code string must not have been renamed."""
        ctx = _make_ctx([_table_block(_PWC_TABLE_CONTENT, _PWC_BBOX)])
        ctx = HeaderFooterTableValidator().execute(ctx)
        codes = _warning_codes(ctx)
        assert "W_HEADER_FOOTER_TABLE_GARBLED" in codes, (
            "Warning code W_HEADER_FOOTER_TABLE_GARBLED renamed or removed"
        )

    def test_no_output_mutation(self) -> None:
        """The validator must not alter table content."""
        block = _table_block(_PWC_TABLE_CONTENT, _PWC_BBOX)
        ctx = _make_ctx([block])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert ctx.document.blocks[0].content == _PWC_TABLE_CONTENT

    def test_no_score_cap_applied(self) -> None:
        """W_HEADER_FOOTER_TABLE_GARBLED is warning-only; must not set a score cap."""
        ctx = _make_ctx([_table_block(_PWC_TABLE_CONTENT, _PWC_BBOX)])
        ctx = HeaderFooterTableValidator().execute(ctx)
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None


# ── W_TABLE_MISSING (Phase 3, Trigger A leader-dot) ──────────────────────────


# Synthetic fixture for ikea3 analogue: TOC block with heavy leader-dot lines.
_IKEA3_TOC_CONTENT = "\n".join(
    f"Chapter {i} Overview ........................ {i * 4 + 12}"
    for i in range(1, 6)  # 5 leader-dot lines
)
# strikeUnderline analogue: ONE line with many leader-dot matches (>=5).
_STRIKE_UNDERLINE_ONE_LINE_CONTENT = " ".join(
    f"Chapter {i} ..............{i + 12}"
    for i in range(10)  # 10 matches on a single line
)
# 20 lines of prose padding to satisfy the tiny-doc guard.
_PROSE_PAD_CONTENT = "\n".join(
    f"Prose line number {i} with several words in a plain paragraph."
    for i in range(20)
)


class TestTableMissingWarning:
    """Regression suite for W_TABLE_MISSING (Trigger A, Phase 3).

    Positive fixtures model:
      - text_misc__ikea3 (per-line gate, 21 lines observed)
      - text_simple__strikeUnderline (fallback gate, 1 line 54 matches observed)

    Controls model the 19 non-ikea3/non-strikeUnderline docs in the dev split,
    all of which have 0 leader-dot lines AND 0 total matches.
    """

    def test_ikea3_analogue_warns(self) -> None:
        """Approximates text_misc__ikea3 (many leader-dot TOC lines)."""
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH, content=_IKEA3_TOC_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx), (
            "Expected W_TABLE_MISSING on TOC-with-leader-dots fixture "
            "(analogous to text_misc__ikea3, 21 leader-dot lines observed)"
        )

    def test_strike_underline_analogue_warns(self) -> None:
        """Approximates text_simple__strikeUnderline (1 line, many matches).

        The per-line gate MISSES this (only 1 line with matches), but the
        fallback gate CATCHES it (>=5 total matches). This is the whole
        reason the fallback gate was added.
        """
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH,
                  content=_STRIKE_UNDERLINE_ONE_LINE_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx), (
            "Expected W_TABLE_MISSING on strikeUnderline analogue "
            "(1 line, many matches — fallback gate)"
        )

    def test_clean_prose_no_warning(self) -> None:
        """A clean prose document without leader dots must stay silent."""
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" not in _warning_codes(ctx)

    def test_warning_code_is_stable(self) -> None:
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH, content=_IKEA3_TOC_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = TableMissingValidator().execute(ctx)
        assert "W_TABLE_MISSING" in _warning_codes(ctx), (
            "Warning code W_TABLE_MISSING renamed or removed — "
            "downstream consumers depend on this exact string"
        )

    def test_warning_maturity_is_candidate(self) -> None:
        """Maturity metadata must be exposed for the eventual cap PR."""
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH, content=_IKEA3_TOC_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = TableMissingValidator().execute(ctx)
        diag = ctx.document.metadata.get("table_missing_diagnostics", {})
        assert diag.get("warning_maturity") == "candidate"

    def test_no_score_cap_applied(self) -> None:
        """Detection-only signal — no readiness cap in this PR."""
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH, content=_IKEA3_TOC_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = TableMissingValidator().execute(ctx)
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None


# ── W_ENCODING_ARTIFACTS (Phase 3, XML residue + mojibake) ──────────────────

# text_dense__de analogue: numbered pt-tag residue + replacement chars.
_DE_ENCODING_ARTIFACT_CONTENT = (
    "Die Vorrichtung </pt192> mit der Halterung </pt193> und dem "
    "Anschluss </pt194> für die Steuerung der wesentlichen Bauteile. "
    "Weitere Details � folgen im Absatz � drei nach Abschnitt zwei."
)


class TestEncodingArtifactsWarning:
    """Regression suite for W_ENCODING_ARTIFACTS (Phase 3).

    Positive fixture models text_dense__de: numbered pt-tag residue plus
    replacement characters in the extracted output.

    Controls model the 20 non-de docs in the dev split, all of which have
    0 XML fragments and mojibake density below the 0.5 percent threshold.
    """

    def test_de_analogue_warns(self) -> None:
        """Approximates text_dense__de: numbered pt-tag residue + mojibake."""
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH,
                  content=_DE_ENCODING_ARTIFACT_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx), (
            "Expected W_ENCODING_ARTIFACTS on de analogue "
            "(numbered pt-tag residue + mojibake)"
        )

    def test_clean_prose_no_warning(self) -> None:
        """A clean prose document must stay silent."""
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" not in _warning_codes(ctx)

    def test_warning_code_is_stable(self) -> None:
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH,
                  content=_DE_ENCODING_ARTIFACT_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert "W_ENCODING_ARTIFACTS" in _warning_codes(ctx), (
            "Warning code W_ENCODING_ARTIFACTS renamed or removed — "
            "downstream consumers depend on this exact string"
        )

    def test_warning_maturity_is_candidate(self) -> None:
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH,
                  content=_DE_ENCODING_ARTIFACT_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = EncodingArtifactsValidator().execute(ctx)
        diag = ctx.document.metadata.get("encoding_artifacts_diagnostics", {})
        assert diag.get("warning_maturity") == "candidate"

    def test_no_score_cap_applied(self) -> None:
        """Detection-only signal — no readiness cap in this PR."""
        blocks = [
            Block(type=BlockType.PARAGRAPH, content=_PROSE_PAD_CONTENT,
                  page=1, index=0, metadata={}),
            Block(type=BlockType.PARAGRAPH,
                  content=_DE_ENCODING_ARTIFACT_CONTENT,
                  page=1, index=1, metadata={}),
        ]
        ctx = _make_ctx(blocks)
        ctx = EncodingArtifactsValidator().execute(ctx)
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None


# ── Cross-warning: clean docs must emit no alerting warnings ─────────────────

class TestCleanDocsNoAlertingWarnings:
    """PASS/PASS_WITH_WARNINGS equivalent fixtures must not receive any alerting warning.

    Alerting warnings are ones that would change a user's trust in the output.
    These fixtures approximate the clean-doc controls from the calibration set.
    """

    _ALERTING_CODES = {
        "W_MULTICOLUMN_ORDER",
        "W_HEADER_FOOTER_TABLE_GARBLED",
        "W_TABLE_MISSING",
        "W_ENCODING_ARTIFACTS",
        "OCR_REQUIRED",
        "NEAR_EMPTY_OUTPUT",
        "LOW_TEXT_DENSITY",
        "GLYPH_ARTIFACTS",
    }

    def _run_validators(self, blocks: list[Block]) -> set[str]:
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        ctx = HeaderFooterTableValidator().execute(ctx)
        ctx = TableMissingValidator().execute(ctx)
        ctx = EncodingArtifactsValidator().execute(ctx)
        return _warning_codes(ctx)

    def test_clean_prose_doc_silent(self) -> None:
        """Single-column prose (minutes2 / budget analogue) must be clean."""
        blocks = [
            Block(type=BlockType.HEADING, content="Meeting Minutes", page=1, index=0,
                  level=1, metadata={"x0": 50.0, "y0": 50.0}),
            Block(type=BlockType.PARAGRAPH, content="The meeting was called to order at 9:00 AM.",
                  page=1, index=1, metadata={"x0": 50.0, "y0": 100.0}),
            Block(type=BlockType.PARAGRAPH, content="All members were present and voted in favour.",
                  page=1, index=2, metadata={"x0": 50.0, "y0": 150.0}),
        ]
        codes = self._run_validators(blocks)
        alerting = codes & self._ALERTING_CODES
        assert not alerting, f"Alerting warnings on clean prose fixture: {alerting}"

    def test_clean_mid_page_table_silent(self) -> None:
        """Mid-page table with substantive cells (gridofnumbers analogue) must be clean."""
        blocks = [_table_block(_LONG_CELL_TABLE, _MID_PAGE_BBOX)]
        codes = self._run_validators(blocks)
        alerting = codes & self._ALERTING_CODES
        assert not alerting, f"Alerting warnings on clean mid-page table: {alerting}"
