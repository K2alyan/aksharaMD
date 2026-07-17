"""Deterministic package plan generation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field as dc_field
from typing import TYPE_CHECKING

from .models import (
    BlockTableFindings,
    DocumentPackagePlan,
    ElementRelationship,
    OmitReason,
    OmittedElement,
    PackageElementPlan,
    PackageMode,
    PackageProfile,
    PackageSourceKind,
    PlannerContext,
    ReasonCode,
    RelationshipType,
    RepresentationTokenBreakdown,
    RepresentationType,
)
from .policy import POLICY_VERSION, route_element
from .token_accounting import count_text_tokens

if TYPE_CHECKING:
    from ..models.document import Document
    from ..models.validation import ValidationReport

PLANNER_VERSION = POLICY_VERSION


# ── Element ID derivation ──────────────────────────────────────────────────────

def _element_id(prefix: str, *parts: str) -> str:
    raw = ":".join([prefix] + list(parts))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def _block_element_id(document_id: str, block_id: str) -> str:
    return _element_id("block", document_id, block_id)

def _page_region_element_id(document_id: str, page: int, bbox: list[float]) -> str:
    bbox_str = ",".join(f"{v:.2f}" for v in bbox)
    return _element_id("region", document_id, str(page), bbox_str)

def _page_element_id(document_id: str, page: int) -> str:
    return _element_id("page", document_id, str(page))

def _fallback_element_id(document_id: str, block_id: str, role: str) -> str:
    return _element_id("fallback", document_id, block_id, role)


# ── Element type classification ────────────────────────────────────────────────

def _classify_element_type(block) -> str:
    btype = str(block.type)
    if btype in ("heading", "paragraph", "list", "blockquote", "footnote",
                 "code_block", "admonition", "caption"):
        return "text"
    if btype == "table":
        return "table"
    if btype == "image":
        return "figure"
    if btype == "math":
        return "formula"
    if btype == "key_value_group":
        return "text"
    return "structural"


# ── PlannerContext construction ────────────────────────────────────────────────

def _build_planner_context(
    document: "Document",
    profile: PackageProfile,
    validation_report: "ValidationReport | None",
) -> PlannerContext:
    """Assemble all routing information from document + validation in one pass."""
    # Warnings by block and page
    block_warnings: dict[str, frozenset[str]] = {}
    page_warnings: dict[int, frozenset[str]] = {}
    if validation_report:
        for issue in validation_report.issues:
            if issue.block_id:
                existing = block_warnings.get(issue.block_id, frozenset())
                block_warnings[issue.block_id] = existing | {issue.code}
            if issue.page:
                existing_p = page_warnings.get(issue.page, frozenset())
                page_warnings[issue.page] = existing_p | {issue.code}

    # Table quality findings from block.metadata["table_quality"]
    table_findings: dict[str, BlockTableFindings] = {}
    for block in document.blocks:
        if str(block.type) == "table":
            tq = block.metadata.get("table_quality") or {}
            overall_status = tq.get("overall_status", "ok")
            risk_codes: list[str] = []
            for sig in tq.get("signals", []):
                if sig.get("status") == "risk":
                    risk_codes.append(sig.get("name", ""))
            has_bbox = (
                block.table_data is not None
                and block.table_data.bbox is not None
            )
            has_page = block.page is not None
            table_findings[block.id] = BlockTableFindings(
                block_id=block.id,
                overall_status=overall_status,
                risk_finding_codes=[c for c in risk_codes if c],
                has_bbox=has_bbox,
                has_page=has_page,
            )

    # Adjacent caption detection: scan consecutive blocks
    # An IMAGE block followed by a CAPTION block → that caption is for the image
    # Also a CAPTION block followed by an IMAGE block
    caption_for_image: dict[str, str] = {}
    blocks = document.blocks
    for i, block in enumerate(blocks):
        if str(block.type) == "image":
            # Check if next block is a caption
            if i + 1 < len(blocks) and str(blocks[i + 1].type) == "caption":
                caption_for_image[block.id] = blocks[i + 1].id
            # Check if previous block is a caption
            elif i > 0 and str(blocks[i - 1].type) == "caption":
                caption_for_image[block.id] = blocks[i - 1].id

    # Heading before table detection
    heading_for_table: dict[str, str] = {}
    for i, block in enumerate(blocks):
        if str(block.type) == "table":
            # Look back up to 2 blocks for a heading
            for j in range(i - 1, max(-1, i - 3), -1):
                if str(blocks[j].type) == "heading":
                    heading_for_table[block.id] = blocks[j].id
                    break

    # OCR status from document metadata
    has_ocr = bool(document.metadata.get("pdf_ocr_available", False))

    return PlannerContext(
        mode=str(profile.mode),
        block_warnings=block_warnings,
        page_warnings=page_warnings,
        table_findings=table_findings,
        caption_for_image=caption_for_image,
        heading_for_table=heading_for_table,
        has_ocr=has_ocr,
        image_fallback_for_uncertain=profile.image_fallback_for_uncertain,
        include_structured_tables=profile.include_structured_tables,
    )


# ── Token breakdown helpers ────────────────────────────────────────────────────

def _compute_token_breakdown(
    rep: RepresentationType,
    elem_type: str,
    content: str,
) -> tuple[int, RepresentationTokenBreakdown]:
    if rep in (RepresentationType.OMIT, RepresentationType.REFERENCE_ONLY, RepresentationType.IMAGE):
        return 0, RepresentationTokenBreakdown()
    tokens = count_text_tokens(content or "")
    if rep == RepresentationType.STRUCTURED_TABLE:
        return tokens, RepresentationTokenBreakdown(structured_table_tokens=tokens)
    if rep == RepresentationType.IMAGE_AND_TEXT:
        return tokens, RepresentationTokenBreakdown(ocr_tokens=tokens)
    if rep == RepresentationType.KEY_VALUE_GROUP:
        return tokens, RepresentationTokenBreakdown(markdown_tokens=tokens)
    if elem_type == "text":
        return tokens, RepresentationTokenBreakdown(markdown_tokens=tokens)
    # TABLE as MARKDOWN (legacy)
    return tokens, RepresentationTokenBreakdown(markdown_tokens=tokens)


# ── Missed-table page detection ────────────────────────────────────────────────

def _pages_with_missed_table_warnings(validation_report) -> set[int]:
    if validation_report is None:
        return set()
    return {
        issue.page
        for issue in validation_report.issues
        if issue.code == "W_TABLE_EXPECTED_NOT_EXTRACTED" and issue.page is not None
    }

def _pages_with_extracted_tables(document) -> set[int]:
    return {
        block.page
        for block in document.blocks
        if str(block.type) == "table" and block.page is not None
    }

def _bbox_from_issue(issue) -> list[float] | None:
    src = issue.source or ""
    if src.startswith("bbox:"):
        try:
            return [float(v) for v in src[5:].split(",")]
        except ValueError:
            pass
    return None


# ── Main entry point ───────────────────────────────────────────────────────────

def plan_document(
    document: "Document",
    profile: PackageProfile | None = None,
    validation_report: "ValidationReport | None" = None,
) -> DocumentPackagePlan:
    """Generate a deterministic package plan for the given document.

    Same document + profile + PLANNER_VERSION -> identical plan always.
    """
    if profile is None:
        profile = PackageProfile()

    ctx = _build_planner_context(document, profile, validation_report)
    mode = ctx.mode
    document_id = document.document_id or document.id or ""

    elements: list[PackageElementPlan] = []

    # ── Route each block ───────────────────────────────────────────────────────
    for block in document.blocks:
        decision = route_element(block, ctx)
        rep = decision.representation

        elem_id = _block_element_id(document_id, block.id)
        elem_type = _classify_element_type(block)

        # Use canonical table serialization for STRUCTURED_TABLE token estimates
        if rep == RepresentationType.STRUCTURED_TABLE and block.table_data is not None:
            from .payload_builder import render_table_for_payload as _rtp
            table_text, _cand = _rtp(block.table_data, profile, block.id, block.content, artifact_path=None)
            canonical_content = table_text
        else:
            canonical_content = block.content or ""

        tokens, breakdown = _compute_token_breakdown(rep, elem_type, canonical_content)

        include_by_default = rep not in (RepresentationType.OMIT, RepresentationType.REFERENCE_ONLY)

        # Build relationships for this block
        relationships: list[ElementRelationship] = []

        # Caption → image relationship (from caption block itself)
        if str(block.type) == "caption":
            # Find if this caption is linked to an image
            for img_id, cap_id in ctx.caption_for_image.items():
                if cap_id == block.id:
                    img_elem_id = _block_element_id(document_id, img_id)
                    relationships.append(ElementRelationship(
                        target_element_id=img_elem_id,
                        relationship_type=RelationshipType.CAPTION_OF,
                    ))

        # Heading context for tables
        if str(block.type) == "table":
            heading_id = ctx.heading_for_table.get(block.id)
            if heading_id:
                h_elem_id = _block_element_id(document_id, heading_id)
                relationships.append(ElementRelationship(
                    target_element_id=h_elem_id,
                    relationship_type=RelationshipType.CONTEXT_FOR,
                ))

        elem = PackageElementPlan(
            element_id=elem_id,
            source_kind=PackageSourceKind.BLOCK,
            block_id=block.id,
            page=block.page,
            element_type=elem_type,
            representation=rep,
            omit_reason=decision.omit_reason,
            reason_code=decision.reason_code,
            reason=decision.reason,
            supporting_reason_codes=decision.supporting_codes,
            confidence=str(block.confidence) if hasattr(block, "confidence") else None,
            table_id=block.table_data.id if (block.table_data and block.table_data.id) else None,
            warning_codes=sorted(ctx.block_warnings.get(block.id, frozenset())),
            include_by_default=include_by_default,
            estimated_text_tokens=tokens,
            token_breakdown=breakdown,
            relationships=relationships,
        )
        elements.append(elem)

        # ── Create visual fallback for risky tables ────────────────────────────
        # Only in fidelity_first and adaptive mode when bbox is available.
        if decision.needs_visual_fallback and block.page is not None:
            bbox: list[float] | None = None
            if block.table_data and block.table_data.bbox:
                bx = block.table_data.bbox
                bbox = [bx.x0, bx.y0, bx.x1, bx.y1]

            if bbox is not None:
                fallback_id = _page_region_element_id(document_id, block.page, bbox)
            else:
                fallback_id = _fallback_element_id(document_id, block.id, "table_crop")

            fb_source_kind = PackageSourceKind.PAGE_REGION if bbox else PackageSourceKind.PAGE
            if bbox is None and block.page:
                fb_source_kind = PackageSourceKind.PAGE

            fallback_include = mode in ("fidelity_first", "adaptive")

            fb_elem = PackageElementPlan(
                element_id=fallback_id,
                source_kind=fb_source_kind,
                block_id=None,
                page=block.page,
                bbox=bbox,
                element_type="table",
                representation=RepresentationType.IMAGE,
                reason_code=ReasonCode.TABLE_VISUAL_FALLBACK,
                reason=f"visual fallback crop for risky table (block {block.id})",
                supporting_reason_codes=decision.supporting_codes,
                warning_codes=sorted(ctx.block_warnings.get(block.id, frozenset())),
                include_by_default=fallback_include,
                estimated_text_tokens=0,
                relationships=[
                    ElementRelationship(
                        target_element_id=elem_id,
                        relationship_type=RelationshipType.VISUAL_FALLBACK_FOR,
                    )
                ],
            )
            elements.append(fb_elem)
            # Back-link: add relationship on the primary table element
            elem.relationships.append(ElementRelationship(
                target_element_id=fallback_id,
                relationship_type=RelationshipType.VISUAL_FALLBACK_FOR,
            ))

    # ── Page-level fallbacks for missed-table warnings ─────────────────────────
    missed_pages = _pages_with_missed_table_warnings(validation_report)
    table_pages = _pages_with_extracted_tables(document)
    fallback_pages = missed_pages - table_pages

    for page in sorted(fallback_pages):
        page_warning_codes = ctx.page_warnings.get(page, frozenset())

        bbox = None
        if validation_report:
            for issue in validation_report.issues:
                if issue.code == "W_TABLE_EXPECTED_NOT_EXTRACTED" and issue.page == page:
                    bbox = _bbox_from_issue(issue)
                    break

        if bbox is not None:
            elem_id = _page_region_element_id(document_id, page, bbox)
            source_kind = PackageSourceKind.PAGE_REGION
        else:
            elem_id = _page_element_id(document_id, page)
            source_kind = PackageSourceKind.PAGE
            bbox = None

        # In text_first mode: missed-table fallbacks are preserved but not included by default
        include_fallback = (mode != "text_first") and ctx.image_fallback_for_uncertain

        fb_elem = PackageElementPlan(
            element_id=elem_id,
            source_kind=source_kind,
            block_id=None,
            page=page,
            bbox=bbox,
            element_type="table",
            representation=RepresentationType.IMAGE,
            reason_code=ReasonCode.TABLE_EXPECTED_NOT_EXTRACTED,
            reason=(
                "missed-table warning with bbox: region crop fallback"
                if bbox
                else "missed-table warning without bbox: full-page image fallback"
            ),
            warning_codes=sorted(page_warning_codes),
            include_by_default=include_fallback,
            estimated_text_tokens=0,
            relationships=[
                ElementRelationship(
                    target_element_id="warning:W_TABLE_EXPECTED_NOT_EXTRACTED",
                    relationship_type=RelationshipType.WARNING_FALLBACK_FOR,
                )
            ],
        )
        elements.append(fb_elem)

    # ── Compute plan totals ────────────────────────────────────────────────────
    selected = [e for e in elements if e.include_by_default]
    estimated_tokens = sum(e.estimated_text_tokens for e in selected)

    preserved_asset_count = sum(
        1 for e in elements if e.representation != RepresentationType.OMIT
    )
    omitted_count = sum(
        1 for e in elements if e.representation == RepresentationType.OMIT
    )

    return DocumentPackagePlan(
        document_id=document_id,
        mode=mode,
        elements=elements,
        estimated_tokens=estimated_tokens,
        preserved_asset_count=preserved_asset_count,
        omitted_element_count=omitted_count,
        planner_version=PLANNER_VERSION,
    )
