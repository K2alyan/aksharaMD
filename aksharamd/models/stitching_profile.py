"""Neutral stitching-profile contract for the parser-neutral scoring refactor.

Boundary 3 of the refactor per ``docs/architecture/BLOCK_TREE_CONTRACT_DESIGN.md``.

The table-level stitching signal group in ``compute_table_quality`` used to
read five raw ``td.metadata`` keys (``source_pages``, ``source_table_methods``,
``page_row_ranges``, ``repeated_header_removed``, ``stitching_confidence``)
and gate on ``extraction_method == ExtractionMethod.PDF_STITCHED``. Both
readings were pdf.py-specific. Any non-PDF parser that produced a table by
joining fragments (spreadsheets that stitch header rows, HTML tables that
concatenate ``<tbody>`` sections across pages, etc.) had no way to opt into
the stitching-quality signals.

This module defines ``StitchingProfile`` — a typed Pydantic model surfaced
as a first-class field on ``TableData``. The presence of the field (i.e.
``td.stitching is not None``) is the sole gate. The five fields carry the
same semantics as the pre-refactor metadata keys; only the input plumbing
changes. Scoring math, ``SCORING_POLICY``, and ``compute_confidence``
arithmetic are untouched.

Placement note: this model lives under ``aksharamd/models/`` (not
``aksharamd/scoring/``) because it is a first-class typed field on
``TableData``. The SourceProfile (Boundary 1) and RejectedTableCandidate
(Boundary 2) models live under ``aksharamd/scoring/`` because they are
accessed via ``doc.metadata[...]`` dict entries rather than as typed
fields on the models. Placing this Pydantic model under
``aksharamd/scoring/`` would create a circular import
(``models.table -> scoring.stitching_profile -> scoring/__init__.py ->
scoring.table_quality -> models.table``); placing it under models sidesteps
the cycle entirely.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PageRowRange(BaseModel):
    """Row-range coverage of one source page in a stitched table.

    Consumed by the row-continuity check in ``_stitching_signals`` — the
    sorted ranges must cover ``[0, td.row_count - 1]`` with no gaps.
    """
    page: int
    row_start: int
    row_end: int


class StitchingProfile(BaseModel):
    """Neutral per-table stitching characterization.

    Populated by any parser that produces a table by joining fragments
    from multiple sources (typically PDF page-break stitching, but the
    contract is parser-agnostic).

    Field-by-field semantics match the pre-refactor ``td.metadata`` keys
    the scorer used to read; only the input plumbing changes.
    """

    source_pages: list[int] = Field(default_factory=list)
    """Ordered source page numbers this stitched table draws from.

    Corresponds to the pre-refactor ``td.metadata["source_pages"]`` key.
    ``len(source_pages)`` populates the ``stitched_source_page_count``
    signal.
    """

    source_table_methods: list[str] = Field(default_factory=list)
    """Extraction-method strings for each source fragment, in order.

    Corresponds to the pre-refactor ``td.metadata["source_table_methods"]``
    key. Consistency (``len(set(non_empty_methods)) <= 1``) populates the
    ``source_method_consistency`` signal.
    """

    page_row_ranges: list[PageRowRange] = Field(default_factory=list)
    """Per-source-page row coverage in the final stitched table.

    Corresponds to the pre-refactor ``td.metadata["page_row_ranges"]``
    key. Presence populates ``page_row_ranges_available``; the gap-free
    coverage check populates ``stitching_row_continuity``.
    """

    repeated_header_removed: bool | None = None
    """Whether a duplicate header row was collapsed during stitching.

    Corresponds to the pre-refactor ``td.metadata["repeated_header_removed"]``
    key. Directly surfaces as the ``repeated_header_removed`` signal
    value. ``None`` means "unknown" (e.g. parser didn't record it).
    """

    stitching_confidence: str = "unknown"
    """Parser's own confidence in the stitching decision.

    Values today: ``"inferred"`` (spatial adjacency or repeated-header
    heuristic) or ``"unknown"``. Corresponds to the pre-refactor
    ``td.metadata["stitching_confidence"]`` key. Value ``"inferred"``
    marks the ``stitching_confidence`` signal as ``status="risk"``.
    """
