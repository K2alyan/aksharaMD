from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version


def _get_version() -> str:
    try:
        return _pkg_version("aksharamd")
    except PackageNotFoundError:
        return "0.0.0.dev"


from pydantic import BaseModel, Field


def _quality_band(score: int) -> str:
    if score >= 85:
        return "HIGH"
    if score >= 70:
        return "OK"
    if score >= 50:
        return "RISKY"
    return "POOR"


class Manifest(BaseModel):
    source_id: str = ""     # stable logical source identity
    capture_id: str = ""    # SHA-256 of raw source bytes
    document_id: str = ""   # content-derived document identity
    source: str
    file_type: str = ""
    pages: int = 0
    chunks: int = 0
    chunk_size: int = 512
    chunk_overlap: int = 0
    images: int = 0
    tables: int = 0
    original_tokens: int = 0
    optimized_tokens: int = 0
    token_reduction_percent: float = 0.0
    duplicate_blocks_removed: int = 0
    headers_removed: int = 0
    footers_removed: int = 0
    readiness_score: int = 0
    quality_band: str = ""           # HIGH | OK | RISKY | POOR
    pdf_classification: str = ""    # native_text | scanned | hybrid | table_heavy | layout_heavy | low_confidence
    ocr_available: bool | None = None
    image_pages: int = 0            # number of image-only pages (PDF only)
    vision_available: bool | None = None   # whether marker-pdf is installed
    vision_pages: int = 0                  # pages re-extracted with Marker vision
    confidence_notes: list[str] = Field(default_factory=list)
    deductions: list[dict] = Field(default_factory=list)       # structured DeductionRecord dicts
    informational: list[dict] = Field(default_factory=list)    # zero-penalty findings
    scoring_policy_version: str = ""                           # e.g. "1.0"
    elapsed_seconds: float = 0.0
    stage_timings: dict[str, float] = Field(default_factory=dict)
    ai_plugins_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)  # machine-readable codes
    errors: list[str] = Field(default_factory=list)
    compiled_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    file_modified_at: str | None = None
    blocks_extracted: int = 0
    blocks_inferred: int = 0
    blocks_ambiguous: int = 0
    aksharamd_version: str = Field(default_factory=_get_version)
    package_mode: str | None = None      # PackageMode value; null when not run in package mode
    planner_version: str | None = None   # null when not run in package mode
    # PR 100 (Auto Policy v1): OCR backend telemetry.
    # ``ocr_backend_requested`` is always populated ("tesseract" |
    # "unlimited_ocr" | "auto"). ``ocr_backend_selected`` is what
    # actually ran (only "tesseract" | "unlimited_ocr"; "auto" is
    # resolved before writing). ``ocr_auto_policy_version`` and
    # ``ocr_auto_decision`` are populated ONLY when the requested
    # backend was "auto" — a stable audit trail of the Auto Policy
    # decision. See docs/adr/ocr-auto-policy-v1.md for the field
    # schema and the ADR that governs the policy.
    ocr_backend_requested: str | None = None
    ocr_backend_selected: str | None = None
    ocr_auto_policy_version: str | None = None
    ocr_auto_decision: dict | None = None
    # Milestone: Output Safety Policy v1 (auto→UOC repetition fallback).
    # ``ocr_backend_selected`` retains its historical meaning (the final
    # effective backend that produced output — reflects the fallback
    # substitution). The six new fields below make the fallback path
    # explicit for auditors: they are non-None only when Output Safety
    # Policy v1 discarded a UOC result and re-ran the document via
    # Tesseract. ``ocr_repetition_signals`` carries bounded per-page
    # evidence (page_index, count, ratio, char_count, bounded 100-char
    # ngram preview, sha256 fingerprint) — never raw markdown, never
    # unbounded ngram text. See docs/adr/uoc-output-safety-policy-v1.md.
    ocr_output_safety_policy_version: str | None = None
    ocr_initially_selected_backend: str | None = None
    ocr_final_backend: str | None = None
    ocr_discarded_backend: str | None = None
    ocr_fallback_reason: str | None = None
    ocr_affected_page_count: int | None = None
    ocr_repetition_signals: list[dict] | None = None
    schema_version: str = "1.5"
