"""Tests for MultiColumnOrderValidator.

Regression cases:
  - text_multicolumns__3colpres analogue: trans_rate=0.30 → should warn (threshold=0.28)
  - text_simple__eastbaytimes analogue: trans_rate=0.25 → should NOT warn
  - correctly column-sorted blocks: low trans_rate → should NOT warn
  - single-column page (no gap): should NOT warn
  - controls: battery/2colmercedes analogues should stay silent
"""
from __future__ import annotations

import pytest

from aksharamd.context import CompilationContext
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.plugins.validators.multicolumn import MultiColumnOrderValidator, _analyse_page

# ── block builders ────────────────────────────────────────────────────────────

def _para(content: str, x0: float, y0: float, page: int = 1) -> Block:
    return Block(
        type=BlockType.PARAGRAPH,
        content=content,
        page=page,
        index=0,
        metadata={"x0": x0, "y0": y0},
    )


def _make_ctx(blocks: list[Block], column_info: dict | None = None) -> CompilationContext:
    doc = Document(
        source="test.pdf",
        file_type="pdf",
        pages=1,
        blocks=blocks,
        metadata={"pdf_column_info": column_info if column_info is not None else {1: {"page_width": 600.0}}},
    )
    doc.compute_id()
    ctx = CompilationContext(source="test.pdf", output_dir="/tmp/out")
    ctx.document = doc
    return ctx


def _warning_codes(ctx: CompilationContext) -> set[str]:
    return {
        getattr(i, "code", None)
        for i in ctx.validation.issues
        if i.severity.value == "warning"
    }


# ── _analyse_page unit tests ──────────────────────────────────────────────────

def _blocks_interleaved(n_pairs: int = 8) -> list[Block]:
    """Blocks alternating left (x0=72) and right (x0=320), y increasing."""
    blocks = []
    for i in range(n_pairs * 2):
        x0 = 72.0 if i % 2 == 0 else 320.0
        content = "word " * 5
        blocks.append(_para(content, x0=x0, y0=float(i * 20)))
    return blocks


def _blocks_column_sorted(col_size: int = 8) -> list[Block]:
    """All left-column blocks first, then all right-column blocks (correct order)."""
    blocks = []
    for i in range(col_size):
        blocks.append(_para("word " * 5, x0=72.0, y0=float(i * 20)))
    for i in range(col_size):
        blocks.append(_para("word " * 5, x0=320.0, y0=float(i * 20)))
    return blocks


class TestAnalysePage:
    def test_interleaved_warns(self) -> None:
        blocks = _blocks_interleaved(n_pairs=8)
        result = _analyse_page(blocks, page_width=600.0)
        assert result["warn"] is True
        assert any("high_transition_rate" in s for s in result["signals"])

    def test_column_sorted_does_not_warn(self) -> None:
        blocks = _blocks_column_sorted(col_size=8)
        result = _analyse_page(blocks, page_width=600.0)
        assert result["warn"] is False

    def test_single_column_no_gap_silent(self) -> None:
        """Blocks all near x0=72 — no bimodal gap."""
        blocks = [_para("word " * 5, x0=72.0 + (i % 3), y0=float(i * 20)) for i in range(10)]
        result = _analyse_page(blocks, page_width=600.0)
        assert result["warn"] is False
        assert result["gap_rel"] < 0.15

    def test_too_few_blocks_silent(self) -> None:
        blocks = _blocks_interleaved(n_pairs=2)  # 4 blocks < min 5
        result = _analyse_page(blocks, page_width=600.0)
        assert result["warn"] is False

    # ── threshold regression tests ────────────────────────────────────────────

    def test_trans_030_warns(self) -> None:
        """3colpres analogue: transition_rate ≈ 0.30 should fire at threshold=0.28."""
        # 10 blocks: 3 transitions out of 9 pairs = 0.333 > 0.28
        # Pattern: LLRLLLRLL (3 switches)
        left, right = 72.0, 320.0
        pattern = [left, left, right, left, left, left, right, left, left, left]
        blocks = [_para("word " * 5, x0=pattern[i], y0=float(i * 20)) for i in range(len(pattern))]
        result = _analyse_page(blocks, page_width=600.0)
        assert result["transition_rate"] >= 0.28
        assert result["warn"] is True

    def test_trans_025_does_not_warn_from_primary_signal(self) -> None:
        """eastbaytimes analogue: transition_rate=0.25 should NOT fire primary signal.

        Uses ≥8 words per block so short_frac stays 0 and only y_monotonic_with_transitions
        can fire — one signal is not enough for a warn.
        """
        # 9 blocks: 2 transitions out of 8 pairs = 0.25 < 0.28
        # Pattern: LLRLLLLLL (2 switches / 8 pairs = 0.25)
        left, right = 72.0, 320.0
        pattern = [left, left, right, left, left, left, left, left, left]
        # 10 words per block — above the 8-word short_frac threshold
        blocks = [_para("word " * 10, x0=pattern[i], y0=float(i * 20)) for i in range(len(pattern))]
        result = _analyse_page(blocks, page_width=600.0)
        assert result["transition_rate"] == pytest.approx(0.25, abs=0.01)
        # high_transition_rate signal must NOT fire
        assert not any("high_transition_rate" in s for s in result["signals"])
        # may have y_monotonic_with_transitions but not enough for warn alone
        assert result["warn"] is False


# ── validator integration tests ───────────────────────────────────────────────

class TestMultiColumnOrderValidator:
    def test_interleaved_emits_warning(self) -> None:
        blocks = _blocks_interleaved(n_pairs=8)
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" in _warning_codes(ctx)

    def test_column_sorted_no_warning(self) -> None:
        blocks = _blocks_column_sorted(col_size=8)
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" not in _warning_codes(ctx)

    def test_no_document_is_noop(self) -> None:
        ctx = CompilationContext(source="test.pdf", output_dir="/tmp/out")
        ctx.document = None
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" not in _warning_codes(ctx)

    def test_missing_page_width_skips_page(self) -> None:
        """Page with no column_info entry should be silently skipped."""
        blocks = _blocks_interleaved(n_pairs=8)
        ctx = _make_ctx(blocks, column_info={})  # no entry for page 1
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" not in _warning_codes(ctx)

    def test_diagnostics_stored_when_no_warning(self) -> None:
        blocks = _blocks_column_sorted(col_size=8)
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        diag = ctx.document.metadata.get("multicolumn_diagnostics", {})
        assert "page_analyses" in diag
        assert diag["warned"] is False

    def test_diagnostics_stored_when_warning(self) -> None:
        blocks = _blocks_interleaved(n_pairs=8)
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        diag = ctx.document.metadata.get("multicolumn_diagnostics", {})
        assert diag["warned"] is True
        assert len(diag["problem_pages"]) >= 1

    def test_no_score_cap(self) -> None:
        """W_MULTICOLUMN_ORDER must not reduce readiness score — warning-only."""
        blocks = _blocks_interleaved(n_pairs=8)
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        # Validator is not a scorer — score is managed by readiness.py separately.
        # Here we verify the validator does not store a cap or a score override.
        assert ctx.document.metadata.get("readiness_score_override") is None
        assert ctx.document.metadata.get("score_cap") is None

    # ── known-good control analogues ──────────────────────────────────────────

    def test_battery_analogue_silent(self) -> None:
        """battery: single-column detected, trans=0 → no warning."""
        # All blocks at the same x column — no gap
        blocks = [_para("word " * 6, x0=72.0, y0=float(i * 15)) for i in range(12)]
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" not in _warning_codes(ctx)

    def test_2colmercedes_analogue_silent(self) -> None:
        """2colmercedes: bimodal gap exists but blocks are column-sorted (low trans)."""
        blocks = _blocks_column_sorted(col_size=10)
        ctx = _make_ctx(blocks)
        ctx = MultiColumnOrderValidator().execute(ctx)
        assert "W_MULTICOLUMN_ORDER" not in _warning_codes(ctx)
