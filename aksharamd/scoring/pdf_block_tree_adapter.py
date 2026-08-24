"""PDF adapter that populates ``SourceProfile`` from today's pdf.py output.

Reference implementation of the neutral scoring contract for the PDF path.
Reads today's ``doc.metadata["pdf_stats"]``, ``pdf_classification``,
``pdf_ocr_available``, ``pdf_ocr_hallucination``, ``pdf_column_info`` and
attaches a ``SourceProfile`` instance at
``doc.metadata["source_profile"]``.

Mapping is verbatim from ``docs/architecture/BLOCK_TREE_CONTRACT_DESIGN.md``
Section 5.2 — one-to-one renames only, no derivations. The byte-identity
constraint (design Section 5.2) is enforced at the consumer sites (a
separate step); this adapter's contribution is to make the neutral read
available without changing any scoring behavior yet.

This module ONLY defines and exposes ``PdfBlockTreeAdapter``. It does not
wire the adapter into ``PDFParser.execute`` or into ``compute_confidence``.
Wiring happens in a later step alongside the consumer refactor.
"""
from __future__ import annotations

from typing import Any, Literal

from ..models.block import BlockType
from ..models.document import Document
from .source_profile import PageDim, SourceProfile
from .table_expectation import RejectedTableCandidate

# Sentinel string pdf.py's IMAGE-block emitter writes as the paragraph
# content when it emits a placeholder for content it could not extract.
# The adapter uses this as the transitional shim per design Section 5.2
# to populate ``block.metadata["is_placeholder"]``; long-term the emitter
# should set that key directly and this sentinel-grep can retire.
_IMAGE_PLACEHOLDER_SENTINEL = "[Image not extracted"

# Set of classification strings the SourceProfile.document_type_hint Literal
# accepts. Any other value from pdf_classification (e.g. "" or an unknown
# label) is normalized to None — same behavior as today's consumers, which
# treat an empty string as "no hint."
_VALID_DOC_TYPE_HINTS = frozenset({
    "native_text", "scanned", "hybrid",
    "table_heavy", "layout_heavy", "low_confidence",
})


class PdfBlockTreeAdapter:
    """Populate a neutral ``SourceProfile`` from today's pdf.py output.

    Idempotent: calling ``populate`` twice on the same Document produces
    the same ``SourceProfile`` value and leaves ``doc.metadata`` in an
    equivalent state. This lets the adapter run wherever it's convenient
    in the pipeline without risk of double-mutation.

    The adapter reads doc-level metadata only. Per-block ``bbox`` and
    ``is_placeholder`` populations (design Section 1.3) are separate
    concerns handled at the parser's block-emission sites (bbox for
    IMAGE blocks landed with PR #118; text-block bbox and
    ``is_placeholder`` are TODO items scheduled with the consumer
    refactor).
    """

    def populate(self, doc: Document) -> None:
        """Compute and attach the neutral scoring contract on ``doc``:

        - ``doc.metadata["source_profile"]`` — ``SourceProfile`` (Boundary 1).
        - ``doc.metadata["rejected_table_candidates_by_page"]`` —
          ``dict[int, list[RejectedTableCandidate]] | None`` (Boundary 2,
          per BLOCK_TREE_CONTRACT_DESIGN.md §3.1). Opt-in: ``None`` when
          the parser did not populate the raw accumulator.
        - Per-paragraph-block ``metadata["is_placeholder"]`` on placeholder
          content (transitional shim per §5.2).

        Overwrites any existing values at those keys. Does not remove or
        modify the raw ``pdf_*`` / ``table_rejected_candidates_by_page``
        keys — the backward-compat shim (§5.3) relies on those keys
        remaining available while consumers are migrated over.
        """
        doc.metadata["source_profile"] = self._build(doc)
        doc.metadata["rejected_table_candidates_by_page"] = (
            self._build_rejected_table_candidates(doc)
        )
        self._populate_is_placeholder(doc)

    def _build_rejected_table_candidates(
        self, doc: Document,
    ) -> dict[int, list[RejectedTableCandidate]] | None:
        """Convert pdf.py's ``table_rejected_candidates_by_page`` raw dict
        into the neutral ``dict[int, list[RejectedTableCandidate]]``.

        Opt-in semantics per design §3.1:
          - Raw key absent            → return ``None`` (parser opt-out)
          - Raw key present, empty {} → return ``{}`` (considered, no rejects)
          - Raw key populated         → convert each dict to typed model

        One-to-one field mapping. ``quality_metrics`` is a dict-of-scalars
        (``dot_leader_fraction: float``, ``empty_cell_fraction: float``,
        ``col_count: int``) — passthrough preserves the three scalar
        values through Pydantic's ``dict`` field type (no nested
        conversion). The only downstream reader is
        ``_compute_leader_dot_signal`` which accesses
        ``quality_metrics["dot_leader_fraction"]`` for the evidence dict
        of a LEADER_DOT_ROWS signal (does not gate the risk decision).
        """
        raw = doc.metadata.get("table_rejected_candidates_by_page")
        if raw is None:
            return None
        result: dict[int, list[RejectedTableCandidate]] = {}
        for page_key, candidates in raw.items():
            try:
                page_num = int(page_key)
            except (TypeError, ValueError):
                continue
            typed_list: list[RejectedTableCandidate] = []
            for c in candidates or []:
                typed_list.append(RejectedTableCandidate(
                    strategy=c.get("strategy", "unknown"),
                    page=int(c.get("page", page_num)),
                    bbox=list(c.get("bbox") or [0.0, 0.0, 0.0, 0.0]),
                    row_count=int(c.get("row_count", 0)),
                    col_count=int(c.get("col_count", 0)),
                    rejection_reasons=list(c.get("rejection_reasons") or []),
                    quality_metrics=dict(c.get("quality_metrics") or {}),
                ))
            result[page_num] = typed_list
        return result

    def _populate_is_placeholder(self, doc: Document) -> None:
        """Set ``block.metadata["is_placeholder"]`` on paragraph blocks
        whose content is a pdf.py placeholder for unextracted content.

        Transitional shim per design Section 5.2. Mirrors the sentinel
        grep the current ``IMAGE_PLACEHOLDER_NO_FALLBACK`` deduction
        does at ``readiness.py:191-194`` — the consumer moves off the
        sentinel and onto this metadata flag; the sentinel string still
        lives in ``block.content`` for human-facing output.
        """
        for block in doc.blocks:
            if block.type != BlockType.PARAGRAPH:
                continue
            if _IMAGE_PLACEHOLDER_SENTINEL in (block.content or ""):
                block.metadata["is_placeholder"] = True

    def _build(self, doc: Document) -> SourceProfile:
        meta: dict[str, Any] = doc.metadata or {}
        stats: dict[str, Any] = meta.get("pdf_stats") or {}
        column_info: dict[Any, Any] = meta.get("pdf_column_info") or {}

        # document_type_hint: pdf_classification, but normalize unknown
        # values to None so the Literal is honored. Today's consumers
        # already treat "" the same as "not classified"; None is the
        # neutral equivalent.
        raw_hint = meta.get("pdf_classification")
        document_type_hint = raw_hint if raw_hint in _VALID_DOC_TYPE_HINTS else None

        ocr_capability: Literal["available", "unavailable", "not_applicable"] = (
            "available" if meta.get("pdf_ocr_available") else "unavailable"
        )

        # hallucinated_pages: today pdf.py records a bool flag, not a
        # count. Map to 1/0 per design Section 5.2. If pdf.py starts
        # recording a real count, this becomes int(...) directly.
        hallucinated_pages = 1 if meta.get("pdf_ocr_hallucination") else 0

        # page_dimensions: preserve today's per-page dict shape as
        # PageDim instances. Support both int and str keys because
        # pdf_column_info uses ints today but the surrounding metadata
        # may serialize keys as strings.
        page_dimensions: dict[int, PageDim] = {}
        for pg_key, info in column_info.items():
            if not isinstance(info, dict):
                continue
            try:
                pg = int(pg_key)
            except (TypeError, ValueError):
                continue
            page_dimensions[pg] = PageDim(
                page_width=float(info.get("page_width") or 0.0),
                page_height=float(info.get("page_height") or 0.0),
            )

        return SourceProfile(
            # Text-layer coverage — one-to-one renames from pdf_stats.
            pages_with_text_layer=int(stats.get("text_pages") or 0),
            pages_without_text_layer=int(stats.get("image_pages") or 0),
            pages_total=int(stats.get("page_count") or 0),
            # Vision / OCR
            ocr_capability=ocr_capability,
            hallucinated_pages=hallucinated_pages,
            # Doc-type hint
            document_type_hint=document_type_hint,
            # pages_containing_tables: reads pdf_stats["table_pages"] directly.
            # DO NOT derive from blocks — design Section 2.3 explains why
            # block derivation is lossy for multi-table pages and stitched
            # cross-page tables. The mapping unit test asserts this by
            # verifying a multi-table-page fixture yields 1, not 2.
            pages_containing_tables=int(stats.get("table_pages") or 0),
            # Per-page geometry
            page_dimensions=page_dimensions,
        )
