from __future__ import annotations
from enum import StrEnum
from pydantic import BaseModel, Field


class PayloadContentType(StrEnum):
    TEXT = "text"
    STRUCTURED_TABLE = "structured_table"
    IMAGE_REFERENCE = "image_reference"
    WARNING = "warning"
    KEY_VALUE_GROUP = "key_value_group"


class LLMPayloadItem(BaseModel):
    item_id: str
    content_type: PayloadContentType
    document_id: str
    element_id: str
    block_id: str | None = None
    page: int | None = None
    text: str | None = None
    table_artifact_path: str | None = None    # package-relative path to tables/<id>.json
    table_markdown: str | None = None         # compact markdown, included in tokens
    asset_path: str | None = None             # package-relative path to images/ or regions/
    mime_type: str | None = None
    caption: str | None = None               # for image items; counts toward tokens
    context_text: str | None = None          # nearby heading/context; counts toward tokens
    warning_codes: list[str] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
    estimated_tokens: int = 0
    # Table serialization metadata
    table_payload_format: str | None = None
    table_rows_total: int = 0
    table_rows_inline: int = 0
    table_rows_omitted: int = 0
    table_columns_total: int = 0
    full_table_artifact_path: str | None = None
    inline_complete: bool = True
    # Key-value group metadata
    kv_artifact_path: str | None = None    # package-relative path to key_values/<id>.json
    kv_record_count: int = 0
    kv_entry_count: int = 0
    kv_selected_format: str = ""           # "markdown" or "tsv" or "" if not KV
    kv_markdown_tokens: int = 0
    kv_tsv_tokens: int = 0


class TokenDeltaBreakdown(BaseModel):
    caption_dedup_delta: int = 0       # tokens removed by caption deduplication (typically negative)
    warning_delta: int = 0             # tokens added by warning items (positive)
    representation_downgrade_delta: int = 0  # token change from representation changes
    missing_asset_delta: int = 0       # token change from unavailable assets
    other_delta: int = 0               # remaining unclassified delta


class PayloadFidelity(BaseModel):
    planned_elements: int = 0
    emitted_items: int = 0
    skipped_reference_only: int = 0
    skipped_omitted: int = 0
    skipped_duplicate_captions: int = 0
    unresolved_element_ids: list[str] = Field(default_factory=list)
    missing_asset_paths: list[str] = Field(default_factory=list)
    plan_payload_mismatches: list[str] = Field(default_factory=list)
    representation_downgrades: list[str] = Field(default_factory=list)
    schema_version: str = "1.0"


class LLMPayload(BaseModel):
    document_id: str
    package_mode: str
    planner_version: str
    payload_schema_version: str = "1.0"
    items: list[LLMPayloadItem]
    planned_text_tokens: int = 0          # sum from planner estimates
    actual_text_token_count: int = 0      # sum from emitted text
    token_delta: int = 0                  # actual - planned (can be negative)
    selected_visual_asset_count: int = 0
    unresolved_element_ids: list[str] = Field(default_factory=list)
    fidelity: PayloadFidelity = Field(default_factory=PayloadFidelity)
    token_delta_breakdown: TokenDeltaBreakdown = Field(default_factory=TokenDeltaBreakdown)
