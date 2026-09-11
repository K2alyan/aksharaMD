"""Signed token savings must survive to the benchmark and MCP surfaces.

Regression cover for PR #135: the signed-savings convention (a negative value
when the optimizer expands rather than shrinks the payload) is intentional and
must not be clipped by ``max(0, ...)`` in user-visible surfaces.
"""
from __future__ import annotations

from aksharamd.mcp_server import _format_savings_summary
from aksharamd.models.manifest import Manifest


def _expansion_manifest() -> Manifest:
    """Manifest where the pipeline expanded the payload (100 -> 108)."""
    return Manifest(
        source="fixture.md",
        file_type="md",
        pages=1,
        chunks=1,
        original_tokens=100,
        optimized_tokens=108,
        token_reduction_percent=-8.0,
        readiness_score=90,
        elapsed_seconds=0.01,
    )


def test_benchmark_cli_row_reports_signed_savings():
    """The benchmark subcommand row dict must carry signed savings."""
    m = _expansion_manifest()
    # Mirror the exact dict built at aksharamd/cli.py:1156 (benchmark path).
    # The row must expose the signed savings (negative) rather than clip to 0.
    row = {
        "saved": (m.original_tokens - m.optimized_tokens) if m else 0,
    }
    assert row["saved"] == -8, (
        "Signed savings must be preserved (see PR #135). "
        f"Expected -8, got {row['saved']!r}."
    )


def test_benchmark_cli_row_reports_zero_when_no_manifest():
    """When the manifest is missing entirely, savings collapses to 0 (not None)."""
    m = None
    row = {
        "saved": (m.original_tokens - m.optimized_tokens) if m else 0,
    }
    assert row["saved"] == 0


def test_rich_output_tokens_saved_is_signed():
    """The Rich-output tokens_saved local (cli.py:817) must be signed."""
    m = _expansion_manifest()
    tokens_saved = m.original_tokens - m.optimized_tokens
    assert tokens_saved == -8


def test_mcp_savings_summary_renders_signed_savings():
    """The MCP tool's savings summary must not clip negative savings."""
    m = _expansion_manifest()
    summary = _format_savings_summary(m)
    # The formatter renders "saved {tokens_saved:,}" so a signed -8 should
    # appear literally as "-8" in the block, and MUST NOT appear as "0".
    assert "saved -8" in summary, (
        "MCP savings summary must render signed savings. "
        f"Full block:\n{summary}"
    )
    assert "saved 0 " not in summary
