"""Element routing policy — deterministic, versioned, centralized.

route_element(block, planner_context) -> RoutingDecision
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import BlockTableFindings, OmitReason, PlannerContext, ReasonCode, RepresentationType

if TYPE_CHECKING:
    pass

POLICY_VERSION = "1.0"

_STRUCTURED_TABLE_METHODS = frozenset({
    "pdf.ruled", "pdf.booktabs", "pdf.whitespace", "pdf.stitched",
    "xlsx.native", "xls.native", "csv.native", "tsv.native",
    "docx.native", "html.native", "pptx.native", "odf.native", "pandoc.ast",
})

_TABLE_QUALITY_CODES = frozenset({
    "W_TABLE_LOW_CONTENT", "W_TABLE_MISSING_CELLS", "W_TABLE_HEADER_AMBIGUOUS",
    "W_TABLE_WORD_SPLITS", "W_TABLE_TEXT_HEAVY",
})

_STRUCTURAL_TYPES = frozenset({"page_break", "metadata"})

_TEXT_TYPES = frozenset({
    "heading", "paragraph", "list", "blockquote",
    "footnote", "code_block", "admonition", "caption",
})


class RoutingDecision:
    """Result of routing a single block."""
    __slots__ = ("representation", "reason_code", "reason", "omit_reason", "supporting_codes", "needs_visual_fallback")

    def __init__(
        self,
        representation: RepresentationType,
        reason_code: ReasonCode | str,
        reason: str,
        omit_reason: OmitReason | None = None,
        supporting_codes: list[str] | None = None,
        needs_visual_fallback: bool = False,
    ) -> None:
        self.representation = representation
        self.reason_code = str(reason_code)
        self.reason = reason
        self.omit_reason = omit_reason
        self.supporting_codes: list[str] = supporting_codes or []
        self.needs_visual_fallback = needs_visual_fallback  # True → planner should create a crop/page fallback element


def route_element(block, ctx: PlannerContext) -> RoutingDecision:
    """Route a single block to its best representation.

    Deterministic: same block + same PlannerContext content → same RoutingDecision
    for the same POLICY_VERSION.
    """
    btype = str(block.type)
    mode = ctx.mode
    block_warning_codes = ctx.block_warnings.get(block.id, frozenset())

    # ── Structural markers ─────────────────────────────────────────────────────
    if btype in _STRUCTURAL_TYPES:
        return RoutingDecision(
            RepresentationType.OMIT,
            ReasonCode.STRUCTURAL_MARKER,
            "structural marker excluded from payload",
            omit_reason=OmitReason.STRUCTURAL_MARKER,
        )

    # ── Empty blocks ───────────────────────────────────────────────────────────
    content = block.content or ""
    if not content.strip() and btype not in ("table", "image", "math", "key_value_group"):
        return RoutingDecision(
            RepresentationType.OMIT,
            ReasonCode.EMPTY_ELEMENT,
            "empty block",
            omit_reason=OmitReason.EMPTY,
        )

    # ── Tables ─────────────────────────────────────────────────────────────────
    if btype == "table":
        td = block.table_data
        if td is None:
            return RoutingDecision(
                RepresentationType.MARKDOWN,
                ReasonCode.TABLE_LEGACY_MARKDOWN,
                "table has no structured data; Markdown fallback",
            )
        extraction_method = str(td.extraction_method or "")
        if extraction_method not in _STRUCTURED_TABLE_METHODS:
            return RoutingDecision(
                RepresentationType.MARKDOWN,
                ReasonCode.TABLE_LEGACY_MARKDOWN,
                f"extraction method {extraction_method!r} is not structured; Markdown fallback",
            )

        # Get table quality findings from context
        findings: BlockTableFindings | None = ctx.table_findings.get(block.id)
        has_quality_warning = bool(block_warning_codes & _TABLE_QUALITY_CODES)
        has_risk_finding = findings is not None and findings.overall_status == "risk"
        has_bbox = findings.has_bbox if findings is not None else (td.bbox is not None)

        if (has_quality_warning or has_risk_finding) and mode in ("fidelity_first", "adaptive"):
            # Risky: still structured, but create a visual fallback if bbox is available
            supporting = [c for c in sorted(block_warning_codes & _TABLE_QUALITY_CODES)]
            if findings:
                supporting += [c for c in findings.risk_finding_codes if c not in supporting]
            return RoutingDecision(
                RepresentationType.STRUCTURED_TABLE,
                ReasonCode.TABLE_STRUCTURED_RISKY,
                "table extracted with quality concerns; visual fallback linked when bbox available",
                supporting_codes=supporting,
                needs_visual_fallback=(has_bbox and mode in ("fidelity_first", "adaptive")),
            )

        return RoutingDecision(
            RepresentationType.STRUCTURED_TABLE,
            ReasonCode.TABLE_STRUCTURED_RELIABLE,
            "table reliably extracted",
        )

    # ── Images ─────────────────────────────────────────────────────────────────
    if btype == "image":
        has_caption = bool(block.metadata.get("caption") or block.metadata.get("alt_text"))
        # Also check adjacent caption registered in context
        has_adj_caption = block.id in ctx.caption_for_image

        if mode == "text_first":
            if has_caption or has_adj_caption:
                return RoutingDecision(
                    RepresentationType.REFERENCE_ONLY,
                    ReasonCode.IMAGE_CAPTIONED,
                    "captioned image preserved as reference in text_first mode",
                )
            return RoutingDecision(
                RepresentationType.REFERENCE_ONLY,
                ReasonCode.IMAGE_EXCLUDED_TEXT_FIRST,
                "image excluded from text_first payload; preserved as reference",
            )

        # fidelity_first / adaptive
        w = int(block.metadata.get("width") or 0)
        h = int(block.metadata.get("height") or 0)
        if w and h and (w < 50 or h < 50):
            return RoutingDecision(
                RepresentationType.REFERENCE_ONLY,
                ReasonCode.IMAGE_DECORATIVE,
                "small image (< 50px); preserved as reference only",
            )
        if has_caption or has_adj_caption:
            return RoutingDecision(
                RepresentationType.IMAGE,
                ReasonCode.IMAGE_CAPTIONED,
                "captioned image",
            )
        return RoutingDecision(
            RepresentationType.IMAGE,
            ReasonCode.IMAGE_UNCLASSIFIED,
            "image included (no caption available)",
        )

    # ── Math / formulas ────────────────────────────────────────────────────────
    if btype == "math":
        if content.strip():
            return RoutingDecision(
                RepresentationType.MARKDOWN,
                ReasonCode.FORMULA_STRUCTURED,
                "formula as structured text",
            )
        # No visual asset available — avoid claiming IMAGE_AND_TEXT
        return RoutingDecision(
            RepresentationType.MARKDOWN,
            ReasonCode.FORMULA_VISUAL_UNAVAILABLE,
            "formula without structured content; no visual asset available",
        )

    # ── Key-value groups ───────────────────────────────────────────────────────
    if btype == "key_value_group":
        return RoutingDecision(
            RepresentationType.KEY_VALUE_GROUP,
            ReasonCode.KEY_VALUE_GROUP_STRUCTURED,
            "structured key-value group routed for KV serialization",
        )

    # ── Text-like blocks ───────────────────────────────────────────────────────
    if btype in _TEXT_TYPES:
        confidence = str(getattr(block, "confidence", "") or "")
        if confidence == "ambiguous":
            if mode == "text_first":
                # In text_first: keep as Markdown with warning, don't force image
                return RoutingDecision(
                    RepresentationType.MARKDOWN,
                    ReasonCode.TEXT_OCR_UNCERTAIN,
                    "low-confidence OCR block; Markdown retained in text_first mode",
                    supporting_codes=["ocr_confidence_ambiguous"],
                )
            # fidelity_first / adaptive: full image+text fallback
            return RoutingDecision(
                RepresentationType.IMAGE_AND_TEXT,
                ReasonCode.TEXT_OCR_UNCERTAIN,
                "low-confidence OCR block; image and text fallback",
            )
        return RoutingDecision(
            RepresentationType.MARKDOWN,
            ReasonCode.TEXT_RELIABLE,
            "text block",
        )

    # ── Unknown ────────────────────────────────────────────────────────────────
    if content.strip():
        return RoutingDecision(
            RepresentationType.MARKDOWN,
            ReasonCode.TEXT_RELIABLE,
            f"unknown block type {btype!r} with content",
        )
    return RoutingDecision(
        RepresentationType.OMIT,
        ReasonCode.EMPTY_ELEMENT,
        f"empty unknown block type {btype!r}",
        omit_reason=OmitReason.EMPTY,
    )
