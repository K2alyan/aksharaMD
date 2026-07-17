"""Page-level table-expectation scoring.

Detects pages where a table was expected (based on parser signals, text
patterns, and document archetype) but no table block was extracted.

All findings are maturity="experimental" and carry no score penalty.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


# ── Signal name constants ──────────────────────────────────────────────────────

class TableExpectationSignalName:
    REJECTED_CANDIDATE       = "rejected_candidate"       # Family: parser
    CAPTION_NEARBY           = "caption_nearby"           # Family: text
    NUMERIC_COLUMN_ALIGNMENT = "numeric_column_alignment" # Family: text
    LEADER_DOT_ROWS          = "leader_dot_rows"          # Family: content
    DOC_TABLE_HEAVY          = "doc_table_heavy"          # Family: archetype


# ── Data models ────────────────────────────────────────────────────────────────

class TableExpectationSignal(BaseModel):
    """A single page-level table-expectation signal."""
    name: str
    status: str   # "risk" | "ok" | "unknown"
    value: float | int | str | bool | None = None
    family: str   # "parser" | "text" | "content" | "archetype"
    evidence: dict = Field(default_factory=dict)


class RejectedTableCandidate(BaseModel):
    """A table candidate that was found by a strategy but rejected by _is_quality_table."""
    strategy: str
    page: int
    bbox: list[float]
    row_count: int
    col_count: int
    rejection_reasons: list[str]
    quality_metrics: dict = Field(default_factory=dict)


class TableExpectationReport(BaseModel):
    """Page-level report on whether a table was expected but not extracted.

    expected="true"    — multiple corroborating signals from different families
    expected="unknown" — only one signal fires (not enough corroboration)
    expected="false"   — no signals fire
    """
    page: int
    expected: Literal["true", "false", "unknown"]
    confidence: float | None = None
    signals: list[TableExpectationSignal] = Field(default_factory=list)
    extracted_table_block_ids: list[str] = Field(default_factory=list)
    rejected_candidates: list[RejectedTableCandidate] = Field(default_factory=list)
    maturity: str = "experimental"


# ── Regex patterns ─────────────────────────────────────────────────────────────

_CAPTION_RE = re.compile(
    r'\bTable\s+\S+',
    re.IGNORECASE,
)

_NUMERIC_TOKEN_RE = re.compile(
    r'^[-+]?[\d,]+(?:\.\d+)?%?$'
)

_LEADER_DOT_RE = re.compile(r'(?:\.{4,}|(?:\. ){3,})')  # 4+ consecutive OR spaced-dot leader


# ── Signal computation helpers ─────────────────────────────────────────────────

def _compute_rejected_candidate_signal(
    rejected_candidates: list[dict],
) -> TableExpectationSignal:
    """REJECTED_CANDIDATE: fires if any candidate was found but rejected."""
    if not rejected_candidates:
        return TableExpectationSignal(
            name=TableExpectationSignalName.REJECTED_CANDIDATE,
            status="ok",
            value=0,
            family="parser",
            evidence={"count": 0},
        )

    strategies = list({c.get("strategy", "unknown") for c in rejected_candidates})
    all_reasons: list[str] = []
    for c in rejected_candidates:
        all_reasons.extend(c.get("rejection_reasons", []))
    unique_reasons = list(dict.fromkeys(all_reasons))  # preserve order, deduplicate

    return TableExpectationSignal(
        name=TableExpectationSignalName.REJECTED_CANDIDATE,
        status="risk",
        value=len(rejected_candidates),
        family="parser",
        evidence={
            "count": len(rejected_candidates),
            "strategies": strategies,
            "rejection_reasons": unique_reasons,
        },
    )


def _compute_caption_signal(blocks: list) -> TableExpectationSignal:
    """CAPTION_NEARBY: fires if page blocks contain a table/figure caption pattern."""
    from ..models.block import BlockType
    for block in blocks:
        if block.type in (BlockType.PARAGRAPH, BlockType.HEADING, BlockType.CAPTION):
            if _CAPTION_RE.search(block.content or ""):
                return TableExpectationSignal(
                    name=TableExpectationSignalName.CAPTION_NEARBY,
                    status="risk",
                    value=True,
                    family="text",
                    evidence={"matched_content": (block.content or "")[:120]},
                )
    return TableExpectationSignal(
        name=TableExpectationSignalName.CAPTION_NEARBY,
        status="ok",
        value=False,
        family="text",
        evidence={},
    )


def _compute_numeric_alignment_signal(blocks: list) -> TableExpectationSignal:
    """NUMERIC_COLUMN_ALIGNMENT: fires if page has 3+ numeric-heavy lines across all paragraphs.

    Aggregates across all paragraph blocks so split-cell schedules (where each row
    is its own tiny block) are detected correctly.
    """
    from ..models.block import BlockType
    total_qualifying = 0
    for block in blocks:
        if block.type != BlockType.PARAGRAPH:
            continue
        content = block.content or ""
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        for line in lines:
            tokens = line.split()
            if len(tokens) < 3:
                continue
            numeric_count = sum(1 for t in tokens if _NUMERIC_TOKEN_RE.match(t))
            if numeric_count >= 2:
                total_qualifying += 1
    if total_qualifying >= 3:
        return TableExpectationSignal(
            name=TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT,
            status="risk",
            value=total_qualifying,
            family="text",
            evidence={"qualifying_line_count": total_qualifying},
        )
    return TableExpectationSignal(
        name=TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT,
        status="ok",
        value=0,
        family="text",
        evidence={},
    )


def _compute_leader_dot_signal(
    rejected_candidates: list[dict],
    blocks: list,
) -> TableExpectationSignal:
    """LEADER_DOT_ROWS: fires when dot-leader rows are detected.

    Sources (either is sufficient):
    - A rejected candidate's rejection_reasons includes "dot_leader"
    - A paragraph block has >= 3 lines containing 4+ consecutive dots
      (captures dot-leader financial tables where pdfplumber was skipped)
    """
    # Source 1: rejected candidate with dot_leader reason
    for candidate in rejected_candidates:
        if "dot_leader" in candidate.get("rejection_reasons", []):
            return TableExpectationSignal(
                name=TableExpectationSignalName.LEADER_DOT_ROWS,
                status="risk",
                value=True,
                family="content",
                evidence={
                    "source": "rejected_candidate",
                    "strategy": candidate.get("strategy"),
                    "dot_leader_fraction": candidate.get("quality_metrics", {}).get("dot_leader_fraction"),
                },
            )

    # Source 2: aggregate dot-leader lines across all paragraph blocks on the page.
    # Many financial schedules render each row as its own tiny block; we count
    # across blocks so the 3-line threshold fires for these split layouts.
    from ..models.block import BlockType
    total_dot_lines = 0
    total_para_lines = 0
    for block in blocks:
        if block.type != BlockType.PARAGRAPH:
            continue
        content = block.content or ""
        lines = [ln for ln in content.splitlines() if ln.strip()]
        total_para_lines += len(lines)
        total_dot_lines += sum(1 for ln in lines if _LEADER_DOT_RE.search(ln))
    if total_dot_lines >= 3:
        return TableExpectationSignal(
            name=TableExpectationSignalName.LEADER_DOT_ROWS,
            status="risk",
            value=total_dot_lines,
            family="content",
            evidence={
                "source": "paragraph_text",
                "dot_line_count": total_dot_lines,
                "total_lines": total_para_lines,
            },
        )

    return TableExpectationSignal(
        name=TableExpectationSignalName.LEADER_DOT_ROWS,
        status="ok",
        value=False,
        family="content",
        evidence={},
    )


def _compute_doc_table_heavy_signal(
    doc_type: str | None,
    has_extracted_tables: bool,
) -> TableExpectationSignal:
    """DOC_TABLE_HEAVY: fires when doc is table_heavy and this page has no tables."""
    if doc_type == "table_heavy" and not has_extracted_tables:
        return TableExpectationSignal(
            name=TableExpectationSignalName.DOC_TABLE_HEAVY,
            status="risk",
            value=True,
            family="archetype",
            evidence={"doc_type": doc_type},
        )
    return TableExpectationSignal(
        name=TableExpectationSignalName.DOC_TABLE_HEAVY,
        status="ok",
        value=False,
        family="archetype",
        evidence={"doc_type": doc_type},
    )


# ── Main computation entry point ───────────────────────────────────────────────

def compute_table_expectation(
    page: int,
    blocks: list,
    rejected_candidates: list[dict],
    doc_type: str | None = None,
) -> TableExpectationReport:
    """Compute page-level table expectation from parser signals and text patterns.

    Parameters
    ----------
    page:
        1-indexed page number.
    blocks:
        All Block objects for this page.
    rejected_candidates:
        Dicts from pdf.py's rejected-candidate accumulator for this page.
    doc_type:
        Document classification string (e.g. "table_heavy") from pdf_metadata.
    """
    from ..models.block import BlockType

    # Determine whether any table was already extracted from this page
    has_extracted_tables = any(
        getattr(b, "type", None) == BlockType.TABLE for b in blocks
    )

    # Compute all signals
    sig_rejected = _compute_rejected_candidate_signal(rejected_candidates)
    sig_caption = _compute_caption_signal(blocks)
    sig_numeric = _compute_numeric_alignment_signal(blocks)
    sig_leader = _compute_leader_dot_signal(rejected_candidates, blocks)
    sig_table_heavy = _compute_doc_table_heavy_signal(doc_type, has_extracted_tables)

    signals = [sig_rejected, sig_caption, sig_numeric, sig_leader, sig_table_heavy]

    # Determine expected status:
    # "true"    if signals from >= 2 independent families fire,
    #           with one exception (see below)
    # "unknown" if exactly 1 family fires, OR the weak-pair exception applies
    # "false"   if no signals fire
    #
    # Exception — parser + numeric_alignment alone is INSUFFICIENT:
    #   Both REJECTED_CANDIDATE (parser) and NUMERIC_COLUMN_ALIGNMENT (text) react
    #   to the same visual structure on chart pages (data axes, callouts, annotations).
    #   They are empirically correlated, not independent. A third independent cue is
    #   required: CAPTION_NEARBY, LEADER_DOT_ROWS, or DOC_TABLE_HEAVY.
    #   Without it the combination is downgraded to "unknown".

    risk_signals = [s for s in signals if s.status == "risk"]
    risk_families = {s.family for s in risk_signals}
    risk_names    = {s.name   for s in risk_signals}

    _parser_plus_numeric_only = (
        risk_families == {"parser", "text"}
        and TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT in risk_names
        and TableExpectationSignalName.CAPTION_NEARBY not in risk_names
    )

    if len(risk_families) >= 2 and not _parser_plus_numeric_only:
        expected: Literal["true", "false", "unknown"] = "true"
    elif risk_signals:
        expected = "unknown"
    else:
        expected = "false"

    # Confidence: proportion of families that fired (0.0–1.0)
    all_families = {"parser", "text", "content", "archetype"}
    confidence = len(risk_families) / len(all_families) if risk_families else 0.0

    rejected_models = [
        RejectedTableCandidate(
            strategy=c.get("strategy", "unknown"),
            page=c.get("page", page),
            bbox=c.get("bbox", [0.0, 0.0, 0.0, 0.0]),
            row_count=c.get("row_count", 0),
            col_count=c.get("col_count", 0),
            rejection_reasons=c.get("rejection_reasons", []),
            quality_metrics=c.get("quality_metrics", {}),
        )
        for c in rejected_candidates
    ]

    return TableExpectationReport(
        page=page,
        expected=expected,
        confidence=confidence,
        signals=signals,
        rejected_candidates=rejected_models,
        maturity="experimental",
    )


# ── Finding aggregation ────────────────────────────────────────────────────────

def aggregate_expectation_findings(report: TableExpectationReport) -> list[dict]:
    """Convert a TableExpectationReport into finding-like dicts.

    Returns a list with a single TABLE_EXPECTED_BUT_NOT_EXTRACTED finding dict.
    This finding is always not_applicable at the table level — it is emitted
    as a warning by TableExpectationValidator when expected="true" and no table
    was extracted from the page.
    """
    return [
        {
            "name": "TABLE_EXPECTED_BUT_NOT_EXTRACTED",
            "status": "not_applicable",
            "maturity": "experimental",
            "page": report.page,
            "expected": report.expected,
            "confidence": report.confidence,
            "evidence": {
                "reason": "page_level_finding_not_table_level",
                "rejected_candidate_count": len(report.rejected_candidates),
                "signals": [s.name for s in report.signals if s.status == "risk"],
            },
        }
    ]
