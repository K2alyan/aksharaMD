"""Configuration profile for KeyValue detection and promotion.

Introduced in kv_promoter/v2 as part of the KeyValueGroup heuristic safety
milestone. Controls which detection paths are active (native semantic vs.
heuristic inference) and exposes assessment records used by the classifier.

Design notes
------------
- Native paths (HTML DL, DOCX properties, XLSX two-column KV) are always on
  by default. They rely on parser-native structural signals and were shown
  in Round 1 calibration to reach P=1.0 / R=1.0 (native_html_dl,
  native_docx_props) or restricted_beta with strict structural guards
  (native_xlsx_kv).
- Heuristic paths (inline paragraph inference, adjacent-block inference) are
  off by default because Round 1 measured FPR=0.929 on a hard-negative
  corpus and the adjacent path had no meaningful validation. Callers must
  opt in via KeyValueDetectionProfile.experimental().
"""
from __future__ import annotations

from pydantic import BaseModel

from ..models.key_value import KeyValueGroupType


class KeyValueDetectionProfile(BaseModel):
    """Controls which KV detection paths are active."""

    # Native semantic extraction — always safe.
    enable_native_html: bool = True
    enable_native_docx: bool = True
    enable_native_xlsx: bool = True

    # Heuristic inference — disabled by default because Round 1 hard-negative
    # FPR was 0.929 on the inline path.
    enable_inline_heuristic: bool = False
    enable_adjacent_heuristic: bool = False

    # When heuristics are disabled but candidates are plausible, still emit
    # W_KEY_VALUE_STRUCTURE_POSSIBLE so downstream tooling can inspect them.
    emit_candidate_diagnostics: bool = True

    @classmethod
    def experimental(cls) -> "KeyValueDetectionProfile":
        """All paths enabled — for calibration and offline evaluation only."""
        return cls(
            enable_inline_heuristic=True,
            enable_adjacent_heuristic=True,
        )

    @classmethod
    def native_only(cls) -> "KeyValueDetectionProfile":
        """Only native semantic extraction — production-safe default."""
        return cls()


class KeyValueCandidateCategory:
    """String constants for candidate category labels.

    Kept as a plain constant holder (not a StrEnum) so it can be freely
    compared to the schema names returned by the classifier without any
    conversion overhead.
    """

    CONTACT = "contact"
    SCHEDULE = "schedule"
    EVENT = "event"
    METADATA = "metadata"
    SPECIFICATION = "specification"
    FORM = "form"
    DIALOGUE = "dialogue"
    CONFIGURATION = "configuration"
    CITATION = "citation"
    SECTION_LABEL = "section_label"
    NUMBERED_LIST = "numbered_list"
    LEGAL_CLAUSE = "legal_clause"
    ACADEMIC_DEFINITION = "academic_definition"
    MEDICAL_SECTION = "medical_section"
    FINANCIAL_FOOTNOTE = "financial_footnote"
    UNKNOWN = "unknown"


class KeyValueCandidateAssessment(BaseModel):
    """Deterministic assessment of a KV candidate list before promotion."""

    candidate_entries: int
    strongly_typed_entries: int
    inferred_group_type: KeyValueGroupType
    group_type_confidence: float  # 0.0-1.0
    category: str
    exclusion_categories: list[str]
    recognized_schema_fields: list[str]
    promotion_decision: str  # "promote", "reject", "candidate_only"
    rejection_reason: str | None = None


class KVSerializationDecision(BaseModel):
    """Records the token-aware format selection for a KV group."""

    selected_format: str  # "markdown" or "tsv"
    markdown_tokens: int
    tsv_tokens: int
    record_count: int
    ambiguity_risk: bool
    selection_reason: str
