from __future__ import annotations
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, Field

BASELINE_A_SERIALIZER_VERSION: str = "1.1"


class DocumentSplit(StrEnum):
    DEV = "dev"
    HELD_OUT = "held_out"


class DocumentCategory(StrEnum):
    PROSE = "prose"
    ACADEMIC = "academic"
    FINANCIAL = "financial"
    TABLE_HEAVY = "table_heavy"
    CHART_HEAVY = "chart_heavy"
    SCANNED = "scanned"
    MIXED = "mixed"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"


class OcrStatus(StrEnum):
    NATIVE = "native"
    OCR = "ocr"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class CorpusEntry(BaseModel):
    document_id: str
    file_path: str                      # relative to corpus root
    file_type: str                      # "pdf", "docx", "xlsx", etc.
    split: DocumentSplit
    categories: list[DocumentCategory] = Field(default_factory=list)
    page_count: int | None = None
    source_size_bytes: int | None = None
    text_block_count: int | None = None  # populated after first compile
    table_count: int | None = None
    image_count: int | None = None
    ocr_status: OcrStatus = OcrStatus.UNKNOWN
    known_difficulties: list[str] = Field(default_factory=list)
    added_by: str = ""
    added_date: str = ""                # ISO date string


class QuestionType(StrEnum):
    TEXT_RETRIEVAL = "text_retrieval"
    TABLE_LOOKUP = "table_lookup"
    CROSS_SECTION = "cross_section"
    VISUAL_INTERPRETATION = "visual_interpretation"
    PROVENANCE = "provenance"
    MISSING_CONTENT = "missing_content"


class GradingMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    RUBRIC = "rubric"
    LLM_JUDGE = "llm_judge"


class AnswerKey(BaseModel):
    accepted_answers: list[str] = Field(default_factory=list)
    supporting_page: int | None = None
    supporting_block_id: str | None = None
    supporting_table_id: str | None = None
    supporting_asset_id: str | None = None
    answer_type: Literal["exact", "normalized", "semantic", "unsupported"] = "semantic"
    grading_method: GradingMethod = GradingMethod.LLM_JUDGE
    notes: str = ""


class QuestionRecord(BaseModel):
    question_id: str
    document_id: str
    question: str
    question_type: QuestionType
    requires_visual: bool = False
    answer_key: AnswerKey = Field(default_factory=AnswerKey)


class RepresentationName(StrEnum):
    BASELINE_A = "baseline_a"           # naïve extraction (pre-optimization tokens)
    BASELINE_B = "baseline_b"           # current optimized document.md
    CANDIDATE_C = "candidate_c"         # text_first payload
    CANDIDATE_D = "candidate_d"         # adaptive payload
    CANDIDATE_E = "candidate_e"         # fidelity_first payload


class BenchmarkMetadata(BaseModel):
    """Versioning and identity fields — must be included in every capture record."""
    parser_version: str
    package_schema_version: str = "1.0"
    planner_version: str
    payload_schema_version: str = "1.0"
    tokenizer: str
    evaluation_model: str | None = None  # None for representation-only runs
    code_commit: str
    capture_timestamp: str               # ISO datetime


class TextTokenBreakdown(BaseModel):
    markdown_tokens: int = 0
    structured_table_tokens: int = 0
    caption_context_tokens: int = 0
    warning_tokens: int = 0
    ocr_tokens: int = 0
    other_tokens: int = 0

    @property
    def total(self) -> int:
        return (self.markdown_tokens + self.structured_table_tokens +
                self.caption_context_tokens + self.warning_tokens +
                self.ocr_tokens + self.other_tokens)


class VisualMetrics(BaseModel):
    selected_visual_asset_count: int = 0
    total_image_pixels: int = 0
    page_image_count: int = 0
    region_crop_count: int = 0
    embedded_image_count: int = 0
    package_size_bytes: int = 0


class PreservationMetrics(BaseModel):
    meaningful_elements_discovered: int = 0
    elements_preserved_in_package: int = 0
    elements_emitted_in_payload: int = 0
    structured_tables_emitted: int = 0
    images_preserved: int = 0
    visual_fallbacks_selected: int = 0
    unresolved_table_expectations: int = 0
    unresolved_ocr_regions: int = 0
    missing_asset_paths: int = 0
    representation_downgrades: int = 0
    warnings_without_fallback: int = 0
    pages_with_possible_unpreserved_content: list[int] = Field(default_factory=list)


class RepresentationMetrics(BaseModel):
    capture_id: str
    document_id: str
    representation: RepresentationName
    package_profile: dict = Field(default_factory=dict)  # profile.model_dump() or {}
    metadata: BenchmarkMetadata
    emitted_text_tokens: int = 0
    token_breakdown: TextTokenBreakdown = Field(default_factory=TextTokenBreakdown)
    planned_text_tokens: int = 0
    token_delta: int = 0
    token_delta_breakdown: dict = Field(default_factory=dict)  # TokenDeltaBreakdown.model_dump()
    visual: VisualMetrics = Field(default_factory=VisualMetrics)
    preservation: PreservationMetrics = Field(default_factory=PreservationMetrics)
    compilation_time_s: float = 0.0
    payload_generation_time_s: float = 0.0
    schema_version: str = "1.0"


class DocumentCapture(BaseModel):
    """All representation metrics for one document run."""
    capture_id: str
    document_id: str
    timestamp: str
    metadata: BenchmarkMetadata
    baselines: list[RepresentationMetrics] = Field(default_factory=list)
    candidates: list[RepresentationMetrics] = Field(default_factory=list)
    schema_version: str = "1.0"


class BaselineARecord(BaseModel):
    baseline_a_text_path: str          # relative to run dir
    baseline_a_text_checksum: str      # SHA-256 of file bytes
    baseline_a_tokens: int             # counted from artifact
    manifest_original_tokens: int | None = None  # consistency check
    baseline_a_manifest_token_delta: int | None = None  # baseline_a_tokens - manifest_original_tokens
    serializer_version: str = BASELINE_A_SERIALIZER_VERSION


class HeldOutRunLock(BaseModel):
    corpus_manifest_checksum: str
    document_ids: list[str]
    code_commit: str
    parser_version: str
    policy_version: str
    planner_version: str
    payload_schema_version: str
    tokenizer: str
    run_timestamp: str
    schema_version: str = "1.0"


class TokenSavingsAttribution(BaseModel):
    document_id: str
    baseline_a_tokens: int
    repeated_furniture_removed_tokens: int
    duplicate_removed_tokens: int
    structural_omission_tokens: int
    caption_dedup_tokens: int
    table_representation_delta: int
    warning_added_tokens: int
    other_delta: int
    final_payload_tokens: int
    reconciliation_residual: int   # should be 0 if attribution is complete


class AnomalyRecord(BaseModel):
    document_id: str
    anomaly_type: str
    description: str
    baseline_a_tokens: int | None = None
    baseline_b_tokens: int | None = None
    candidate_c_tokens: int | None = None
    candidate_d_tokens: int | None = None
    candidate_e_tokens: int | None = None
    severity: str = "info"   # "info" | "warning" | "error"


class CategorySummary(BaseModel):
    category: str
    document_count: int
    baseline_a_tokens_median: float = 0.0
    baseline_b_tokens_median: float = 0.0
    candidate_c_tokens_median: float = 0.0
    candidate_d_tokens_median: float = 0.0
    candidate_e_tokens_median: float = 0.0
    c_vs_b_reduction_median_pct: float = 0.0
    d_vs_b_reduction_median_pct: float = 0.0
    d_visual_assets_median: float = 0.0
    preservation_ratio_median: float = 0.0


class CorpusRunSummary(BaseModel):
    run_id: str
    split: str
    timestamp: str
    corpus_manifest_checksum: str
    document_count: int
    successful_count: int
    failed_document_ids: list[str] = Field(default_factory=list)
    baseline_a_tokens_median: float = 0.0
    baseline_a_tokens_mean: float = 0.0
    baseline_b_tokens_median: float = 0.0
    baseline_b_tokens_mean: float = 0.0
    candidate_c_tokens_median: float = 0.0
    candidate_c_tokens_mean: float = 0.0
    candidate_d_tokens_median: float = 0.0
    candidate_d_tokens_mean: float = 0.0
    candidate_e_tokens_median: float = 0.0
    c_vs_a_reduction_median_pct: float = 0.0
    c_vs_b_reduction_median_pct: float = 0.0
    d_vs_a_reduction_median_pct: float = 0.0
    d_vs_b_reduction_median_pct: float = 0.0
    total_corpus_c_vs_b_reduction_pct: float = 0.0   # weighted
    anomaly_count: int = 0
    schema_version: str = "1.0"
