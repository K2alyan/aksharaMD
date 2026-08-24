"""Neutral source-profile contract for the parser-neutral scoring refactor.

Defines ``SourceProfile`` and ``PageDim`` per
``docs/architecture/BLOCK_TREE_CONTRACT_DESIGN.md`` Section 2.1. This module
adds ONLY the model definitions — no adapter, no consumer wiring, no
scoring behavior change. The adapter that fills ``SourceProfile`` from a
parser's raw output lives in ``pdf_block_tree_adapter.py``. Consumer sites
that read ``SourceProfile`` are refactored in a later step.

The contract collapses the OCR / image-only cluster (currently 7 of 12
COUPLED scoring signals against parser internals) plus the
``pages_containing_tables`` note consumer into a single neutral shape that
any parser can populate. Every field is defined in terms of what the
scorer needs to know, independent of how the parser produced the output.

Field-by-field mapping to today's pdf.py metadata lives in the design doc's
Section 5.2 table; ``PdfBlockTreeAdapter`` implements it verbatim.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PageDim(BaseModel):
    """Per-page physical dimensions in the source document's coordinate system.

    Populated by parsers that expose page geometry (PDF today; other paginated
    formats potentially). Consumers (``W_MULTICOLUMN_ORDER``,
    ``W_HEADER_FOOTER_TABLE_GARBLED``) already skip when ``page_width`` or
    ``page_height`` is zero, so an empty ``page_dimensions`` map is the
    correct default for parsers that don't emit geometry.
    """
    page_width: float = 0.0
    page_height: float = 0.0


class SourceProfile(BaseModel):
    """Neutral per-document source characterization.

    Consumed by every OCR / image-only scoring signal plus the
    ``{tp} table page(s)`` note. Fields are named in terms of what the
    scorer needs; adapters translate parser-specific metadata into these
    names. Parsers that cannot populate a given field leave it at the
    neutral default (0, ``"unavailable"``, ``None``, empty dict) and the
    dependent signals stay silent — same behavior as today when the raw
    pdf_* keys are absent.

    Design reference: ``docs/architecture/BLOCK_TREE_CONTRACT_DESIGN.md``
    Section 2.1. Adapter mapping: same doc Section 5.2.
    """

    # Text-layer coverage
    pages_with_text_layer: int = 0
    """Number of pages whose text layer produced usable text.

    PDF adapter: ``pdf_stats["text_pages"]``.
    """

    pages_without_text_layer: int = 0
    """Number of pages without a usable text layer (image-only pages).

    PDF adapter: ``pdf_stats["image_pages"]``.
    """

    pages_total: int = 0
    """Total page count. Must equal ``Document.pages``.

    PDF adapter: ``pdf_stats["page_count"]``.
    """

    # Vision / OCR capability and outcomes
    ocr_capability: Literal["available", "unavailable", "not_applicable"] = "unavailable"
    """Whether OCR is applicable and available on this parser.

    - ``"available"``: parser could run OCR and the runtime is installed.
    - ``"unavailable"``: parser could run OCR but the runtime is missing.
    - ``"not_applicable"``: parser has no OCR concept (e.g. Markdown).

    Distinguishing the three lets the ``OCR_ATTEMPTED_SPARSE`` guard
    separate "parser could OCR but produced nothing" from "parser doesn't
    do OCR at all". PDF adapter emits only ``"available"`` or
    ``"unavailable"`` (mapped from ``pdf_ocr_available``); the
    ``"not_applicable"`` value is reserved for non-PDF adapters.
    """

    hallucinated_pages: int = 0
    """Count of pages where the vision-OCR path rejected its output as
    hallucinated.

    PDF adapter: ``1 if pdf_ocr_hallucination else 0`` (today's pdf.py
    records a boolean flag, not a page count; the mapping upgrades to
    a real count when pdf.py starts recording one).
    """

    # Document-type hint
    document_type_hint: Literal[
        "native_text", "scanned", "hybrid", "table_heavy",
        "layout_heavy", "low_confidence",
    ] | None = None
    """Optional classification hint. Signals that read it (e.g.
    ``OCR_REQUIRED`` gate ``hint in ("scanned", "hybrid")``) treat
    ``None`` the same way the current code treats an empty
    ``pdf_classification`` string.

    PDF adapter: ``pdf_classification`` (string values already match the
    Literal set).
    """

    # Table page count (kept explicit because it is NOT block-derivable)
    pages_containing_tables: int = 0
    """Count of pages containing at least one extracted table.

    NOT the same as ``len([b for b in blocks if b.type == TABLE])``. A
    page with N tables increments once (not N times); a stitched
    cross-page table increments once per source page it touches (not
    once per resulting Block). See design Section 2.3 for the byte-
    identity impact of deriving this from the block tree instead.

    PDF adapter: ``pdf_stats["table_pages"]`` — one-to-one, no
    computation. Non-PDF adapters MAY fall back to
    ``len({b.page for b in blocks if b.type == TABLE and b.page is
    not None})`` as a **fidelity fallback**; stitched cases will
    undercount.
    """

    # Per-page dimensions
    page_dimensions: dict[int, PageDim] = Field(default_factory=dict)
    """Per-page physical dimensions keyed by page number.

    PDF adapter: ``{pg: PageDim(page_width=..., page_height=...)}`` from
    ``pdf_column_info[pg]``. Missing pages: consumers already skip when
    ``page_width == 0.0`` or ``page_height == 0.0``.
    """
