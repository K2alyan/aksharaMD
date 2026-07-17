from __future__ import annotations

import hashlib
from dataclasses import dataclass, field as dc_field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from ..models.table import TableData


class PackageMode(StrEnum):
    TEXT_FIRST = "text_first"
    FIDELITY_FIRST = "fidelity_first"
    ADAPTIVE = "adaptive"


class RepresentationType(StrEnum):
    MARKDOWN = "markdown"
    STRUCTURED_TABLE = "structured_table"
    IMAGE = "image"
    IMAGE_AND_TEXT = "image_and_text"
    REFERENCE_ONLY = "reference_only"
    KEY_VALUE_GROUP = "key_value_group"
    OMIT = "omit"


class PackageSourceKind(StrEnum):
    BLOCK = "block"
    PAGE_REGION = "page_region"
    PAGE = "page"
    ASSET = "asset"


class OmitReason(StrEnum):
    REPEATED_PAGE_FURNITURE = "repeated_page_furniture"
    STRUCTURAL_MARKER = "structural_marker"
    DECORATIVE = "decorative"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"


class ReasonCode(StrEnum):
    # Text
    TEXT_RELIABLE = "TEXT_RELIABLE"
    TEXT_OCR_UNCERTAIN = "TEXT_OCR_UNCERTAIN"
    # Tables
    TABLE_STRUCTURED_RELIABLE = "TABLE_STRUCTURED_RELIABLE"
    TABLE_STRUCTURED_RISKY = "TABLE_STRUCTURED_RISKY"
    TABLE_LEGACY_MARKDOWN = "TABLE_LEGACY_MARKDOWN"
    TABLE_EXPECTED_NOT_EXTRACTED = "TABLE_EXPECTED_NOT_EXTRACTED"
    TABLE_VISUAL_FALLBACK = "TABLE_VISUAL_FALLBACK"
    # Images
    IMAGE_CAPTIONED = "IMAGE_CAPTIONED"
    IMAGE_DECORATIVE = "IMAGE_DECORATIVE"
    IMAGE_EXCLUDED_TEXT_FIRST = "IMAGE_EXCLUDED_TEXT_FIRST"
    IMAGE_UNCLASSIFIED = "IMAGE_UNCLASSIFIED"
    # Formulas
    FORMULA_STRUCTURED = "FORMULA_STRUCTURED"
    FORMULA_VISUAL_FALLBACK = "FORMULA_VISUAL_FALLBACK"
    FORMULA_VISUAL_UNAVAILABLE = "FORMULA_VISUAL_UNAVAILABLE"
    # Key-value groups
    KEY_VALUE_GROUP_STRUCTURED = "KEY_VALUE_GROUP_STRUCTURED"
    # Structural
    STRUCTURAL_MARKER = "STRUCTURAL_MARKER"
    EMPTY_ELEMENT = "EMPTY_ELEMENT"


class RelationshipType(StrEnum):
    CAPTION_OF = "caption_of"
    CONTEXT_FOR = "context_for"
    VISUAL_FALLBACK_FOR = "visual_fallback_for"
    OCR_SOURCE_FOR = "ocr_source_for"
    REPRESENTATION_OF = "representation_of"
    WARNING_FALLBACK_FOR = "warning_fallback_for"


class ElementRelationship(BaseModel):
    target_element_id: str
    relationship_type: RelationshipType
    metadata: dict = Field(default_factory=dict)


class BlockTableFindings(BaseModel):
    block_id: str
    overall_status: str = "ok"      # "ok" | "risk" | "unknown"
    risk_finding_codes: list[str] = Field(default_factory=list)
    has_bbox: bool = False
    has_page: bool = False


@dataclass
class PlannerContext:
    """Internal context assembled once per plan_document() call.

    Consolidates all routing information so policy functions receive
    only what they need without touching the compilation context.
    """
    mode: str = "adaptive"
    # block_id -> frozenset of warning codes for that block
    block_warnings: dict[str, frozenset[str]] = dc_field(default_factory=dict)
    # page -> frozenset of warning codes for that page
    page_warnings: dict[int, frozenset[str]] = dc_field(default_factory=dict)
    # block_id -> BlockTableFindings (for TABLE blocks)
    table_findings: dict[str, BlockTableFindings] = dc_field(default_factory=dict)
    # image block_id -> adjacent caption block_id
    caption_for_image: dict[str, str] = dc_field(default_factory=dict)
    # table block_id -> nearest preceding heading block_id
    heading_for_table: dict[str, str] = dc_field(default_factory=dict)
    # whether the document was processed by OCR (ambiguous blocks may exist)
    has_ocr: bool = False
    # PackageProfile fields
    image_fallback_for_uncertain: bool = True
    include_structured_tables: bool = True


class RepresentationTokenBreakdown(BaseModel):
    markdown_tokens: int = 0
    structured_table_tokens: int = 0
    caption_context_tokens: int = 0
    ocr_tokens: int = 0
    other_text_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.markdown_tokens
            + self.structured_table_tokens
            + self.caption_context_tokens
            + self.ocr_tokens
            + self.other_text_tokens
        )


class VisualAssetStats(BaseModel):
    visual_asset_count: int = 0
    full_page_count: int = 0
    region_crop_count: int = 0
    embedded_image_count: int = 0
    total_pixels: int = 0
    largest_width: int = 0
    largest_height: int = 0


class VisualCostEstimate(BaseModel):
    estimator: str
    model: str | None = None
    units: float = 0.0
    estimated_tokens: int | None = None
    estimated_cost: float | None = None


class PackageElementPlan(BaseModel):
    element_id: str
    source_kind: PackageSourceKind
    block_id: str | None = None
    page: int | None = None
    bbox: list[float] | None = None        # [x0, y0, x1, y1] in page points
    element_type: str                      # "text" | "table" | "figure" | "formula" | "structural"
    representation: RepresentationType
    omit_reason: OmitReason | None = None
    reason_code: str
    reason: str
    confidence: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    table_id: str | None = None
    related_element_ids: list[str] = Field(default_factory=list)
    context_block_ids: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    include_by_default: bool = True
    estimated_text_tokens: int = 0
    token_breakdown: RepresentationTokenBreakdown = Field(
        default_factory=RepresentationTokenBreakdown
    )
    supporting_reason_codes: list[str] = Field(default_factory=list)
    relationships: list[ElementRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_source_constraints(self) -> "PackageElementPlan":
        if self.source_kind == PackageSourceKind.BLOCK and self.block_id is None:
            raise ValueError("block sources require block_id")
        if self.source_kind == PackageSourceKind.PAGE_REGION:
            if self.page is None:
                raise ValueError("page_region sources require page")
            if self.bbox is None:
                raise ValueError("page_region sources require bbox")
        if self.source_kind == PackageSourceKind.PAGE and self.page is None:
            raise ValueError("page sources require page")
        return self


class DocumentPackagePlan(BaseModel):
    document_id: str
    mode: str
    elements: list[PackageElementPlan]
    estimated_tokens: int = 0             # text-only selected payload (excludes visual cost)
    estimated_raw_tokens: int | None = None
    estimated_token_reduction_pct: float | None = None
    preserved_asset_count: int = 0
    omitted_element_count: int = 0
    planner_version: str
    schema_version: str = "1.0"


class PackageAssetReference(BaseModel):
    package_asset_id: str
    source_asset_id: str | None = None    # None for page renders/crops without a source asset
    role: str                             # "embedded_image"|"table_crop"|"region_crop"|"page_render"
    file_path: str                        # relative to package root: "images/abc.png"
    checksum: str = ""                    # SHA-256 of written bytes; empty on write failure
    include_by_default: bool = True
    related_element_ids: list[str] = Field(default_factory=list)
    extraction_method: str = ""           # "embedded"|"rendered"|"cropped"|"page_render"
    page: int | None = None
    bbox: list[float] | None = None
    metadata: dict = Field(default_factory=dict)
    schema_version: str = "1.0"


class TableArtifact(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    block_id: str
    table: "TableData"

    model_config = {"arbitrary_types_allowed": True}


class OmittedElement(BaseModel):
    element_id: str
    reason_code: OmitReason
    block_id: str | None = None
    page: int | None = None


class PackageFidelityReport(BaseModel):
    document_id: str
    meaningful_elements_discovered: int = 0
    elements_preserved_in_package: int = 0    # MARKDOWN+STRUCTURED_TABLE+IMAGE+IMAGE_AND_TEXT+REFERENCE_ONLY
    elements_included_in_default_payload: int = 0   # include_by_default=True, not OMIT/REFERENCE_ONLY
    elements_reference_only: int = 0           # REFERENCE_ONLY — preserved but not in default payload
    elements_intentionally_omitted: int = 0    # OMIT
    omitted_elements: list[OmittedElement] = Field(default_factory=list)
    preservation_failures: int = 0
    structured_tables: int = 0
    tables_with_visual_fallback: int = 0
    images_preserved: int = 0
    visual_regions_preserved: int = 0
    warnings_without_visual_fallback: int = 0
    pages_with_possible_unpreserved_content: list[int] = Field(default_factory=list)
    unresolved_table_expectations: int = 0
    ocr_uncertainty_blocks: int = 0
    asset_extraction_failures: int = 0
    unresolved_element_ids: list[str] = Field(default_factory=list)
    schema_version: str = "1.0"


class TokenReport(BaseModel):
    document_id: str
    raw_extracted_text_tokens: int = 0
    optimized_text_tokens: int = 0
    selected_payload_tokens: int = 0       # text-only; includes structured table serializations
    token_breakdown: RepresentationTokenBreakdown = Field(
        default_factory=RepresentationTokenBreakdown
    )
    visual_stats: VisualAssetStats = Field(default_factory=VisualAssetStats)
    visual_cost_estimate: VisualCostEstimate | None = None
    token_reduction_vs_raw_pct: float = 0.0
    token_reduction_vs_optimized_pct: float = 0.0
    tokenizer: str = "heuristic"
    comparison_note: str = (
        "text-token reduction vs. AksharaMD optimized output; "
        "visual cost reported separately in visual_stats"
    )
    planner_version: str
    schema_version: str = "1.0"


class TablePayloadFormat(StrEnum):
    MARKDOWN = "markdown"
    TSV = "tsv"
    ROW_RECORDS = "row_records"
    PREVIEW_REFERENCE = "preview_reference"
    JSON_REFERENCE = "json_reference"


class TableSerializationCandidate(BaseModel):
    format: TablePayloadFormat
    text: str
    token_count: int
    preserves_all_rows_inline: bool  # True for markdown/tsv/row_records, False for preview/json_ref
    preserves_structure_inline: bool  # True for all except json_reference
    artifact_path: str | None = None
    omitted_row_count: int = 0


class PackageProfile(BaseModel):
    mode: PackageMode = PackageMode.ADAPTIVE
    include_images: str = "important_only"    # "all" | "important_only" | "none"
    include_structured_tables: bool = True
    include_table_markdown: bool = True
    preserve_source_file: bool = False
    max_text_tokens: int | None = None
    image_fallback_for_uncertain: bool = True
    table_payload_format: Literal["markdown", "json_reference"] = "markdown"
    include_warning_items: bool = True
    include_provenance: bool = True
    table_payload_strategy: Literal["auto", "full_inline", "preview_reference", "reference_only"] = "auto"
    max_inline_table_tokens: int = 1200
    table_preview_rows: int = 5
    allow_table_artifact_references: bool = True


from ..models.table import TableData as _TableData  # noqa: E402
TableArtifact.model_rebuild(_types_namespace={"TableData": _TableData})
