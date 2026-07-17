from __future__ import annotations

from pydantic import BaseModel, Field

from aksharamd.models.key_value import KeyValueGroupType, KeyValueValueType


class GroundTruthEntry(BaseModel):
    key: str
    value: str
    normalized_key: str | None = None
    expected_value_type: KeyValueValueType | None = None
    record_number: int = 0
    page: int | None = None

class KeyValueGroundTruth(BaseModel):
    case_id: str
    document_id: str
    source_format: str          # "html", "docx", "xlsx", "pdf", "text"
    detection_path: str         # "native_html_dl", "native_docx_props", "native_xlsx_kv",
                                # "heuristic_inline", "heuristic_adjacent", "negative_control"
    is_key_value_group: bool
    group_type: KeyValueGroupType | None = None
    title: str | None = None
    records: list[list[GroundTruthEntry]] = Field(default_factory=list)
    negative_reason: str | None = None   # only for negative controls
    notes: str | None = None

class DetectionOutcome(BaseModel):
    case_id: str
    predicted_is_kv: bool
    predicted_group_type: KeyValueGroupType | None = None
    predicted_entry_count: int = 0
    predicted_record_count: int = 0
    detection_path_used: str | None = None
    source_block_ids: list[str] = Field(default_factory=list)

class CorpusMetrics(BaseModel):
    path_name: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    fpr: float = 0.0   # false positive rate = fp / (fp + tn)

    def compute(self) -> CorpusMetrics:
        self.precision = self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0
        self.recall = self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0
        self.f1 = (2 * self.precision * self.recall / (self.precision + self.recall)
                   if (self.precision + self.recall) > 0 else 0.0)
        self.fpr = self.fp / (self.fp + self.tn) if (self.fp + self.tn) > 0 else 0.0
        return self


class PathMaturityLabels(BaseModel):
    native_html_dl: str = "production_candidate"
    native_docx_props: str = "production_candidate"
    native_xlsx_kv: str = "restricted_beta"
    heuristic_inline: str = "experimental_disabled_by_default"
    heuristic_adjacent: str = "experimental_disabled_by_default"

    rationale: dict[str, str] = Field(default_factory=lambda: {
        "native_html_dl": "P=1.0/R=1.0 on corrected 11-case corpus; no FPR; native semantic markup",
        "native_docx_props": "schema-driven extraction; no heuristic risk; structured property API",
        "native_xlsx_kv": "5+5 corpus too small for full production; strict structural guards applied",
        "heuristic_inline": (
            "Round 1 hard-negative FPR=0.929; v2 classifier adds 9 exclusion categories and "
            "positive-evidence rules; still disabled by default until held-out corpus confirms."
        ),
        "heuristic_adjacent": (
            "v2 adds Strategy 2 alternating key-only/value-only detection; still requires "
            "explicit opt-in via KeyValueDetectionProfile.experimental()."
        ),
    })
