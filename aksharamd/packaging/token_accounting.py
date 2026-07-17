"""Token counting and TokenReport construction."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    RepresentationTokenBreakdown,
    RepresentationType,
    TokenReport,
    VisualAssetStats,
)

if TYPE_CHECKING:
    from .models import DocumentPackagePlan, PackageAssetReference


def count_text_tokens(text: str) -> int:
    """Count tokens; delegates to utils.count_tokens (tiktoken or heuristic)."""
    from ..utils import count_tokens
    return count_tokens(text)


def _tokenizer_name() -> str:
    try:
        import tiktoken  # noqa: F401
        return "tiktoken/cl100k_base"
    except ImportError:
        return "heuristic"


def build_token_report(
    document_id: str,
    plan: "DocumentPackagePlan",
    raw_extracted_tokens: int,
    optimized_tokens: int,
    asset_refs: "list[PackageAssetReference]",
) -> TokenReport:
    """Build a TokenReport from a finalized plan and asset list."""
    selected = [e for e in plan.elements if e.include_by_default]

    # Sum token breakdowns across all selected elements
    total_breakdown = RepresentationTokenBreakdown()
    for elem in selected:
        total_breakdown = RepresentationTokenBreakdown(
            markdown_tokens=total_breakdown.markdown_tokens + elem.token_breakdown.markdown_tokens,
            structured_table_tokens=(
                total_breakdown.structured_table_tokens
                + elem.token_breakdown.structured_table_tokens
            ),
            caption_context_tokens=(
                total_breakdown.caption_context_tokens
                + elem.token_breakdown.caption_context_tokens
            ),
            ocr_tokens=total_breakdown.ocr_tokens + elem.token_breakdown.ocr_tokens,
            other_text_tokens=(
                total_breakdown.other_text_tokens + elem.token_breakdown.other_text_tokens
            ),
        )

    selected_payload_tokens = total_breakdown.total

    # Visual asset stats
    full_page = sum(1 for a in asset_refs if a.role == "page_render")
    region_crop = sum(1 for a in asset_refs if a.role == "region_crop")
    embedded = sum(1 for a in asset_refs if a.role == "embedded_image")
    total_pixels = sum(
        a.metadata.get("width", 0) * a.metadata.get("height", 0)
        for a in asset_refs
        if a.metadata.get("width") and a.metadata.get("height")
    )
    largest_w = max((a.metadata.get("width") or 0 for a in asset_refs), default=0)
    largest_h = max((a.metadata.get("height") or 0 for a in asset_refs), default=0)
    visual_stats = VisualAssetStats(
        visual_asset_count=len(asset_refs),
        full_page_count=full_page,
        region_crop_count=region_crop,
        embedded_image_count=embedded,
        total_pixels=total_pixels,
        largest_width=largest_w,
        largest_height=largest_h,
    )

    raw_pct = (
        round((raw_extracted_tokens - selected_payload_tokens) / raw_extracted_tokens * 100, 2)
        if raw_extracted_tokens > 0
        else 0.0
    )
    opt_pct = (
        round((optimized_tokens - selected_payload_tokens) / optimized_tokens * 100, 2)
        if optimized_tokens > 0
        else 0.0
    )

    return TokenReport(
        document_id=document_id,
        raw_extracted_text_tokens=raw_extracted_tokens,
        optimized_text_tokens=optimized_tokens,
        selected_payload_tokens=selected_payload_tokens,
        token_breakdown=total_breakdown,
        visual_stats=visual_stats,
        token_reduction_vs_raw_pct=raw_pct,
        token_reduction_vs_optimized_pct=opt_pct,
        tokenizer=_tokenizer_name(),
        planner_version=plan.planner_version,
    )
