"""Frozen detector configuration.

Provides both the v1 lock (preserved for historical evidence) and the v2
lock introduced with the KeyValueGroup safety milestone.
"""
from __future__ import annotations
import hashlib, json
from datetime import UTC, datetime
from pydantic import BaseModel

class KeyValueDetectorLock(BaseModel):
    detector_version: str
    code_commit: str
    pipeline_stage: str            # "post_clean_pre_optimize"
    inline_max_chars: int          # _MAX_PARA_CHARS in promoter
    inline_min_entries: int        # min entries required from detector
    adjacent_min_blocks: int       # min blocks to attempt adjacent promotion
    adjacent_page_tolerance: int   # max page difference allowed in run
    max_label_words: int           # _MAX_LABEL_WORDS in detector
    max_value_chars: int           # _MAX_VALUE_CHARS in detector
    xlsx_max_rows: int             # _is_kv_region row limit
    xlsx_required_columns: int     # exactly 2
    rhetorical_label_set_checksum: str   # SHA-256[:16] of sorted frozenset as JSON
    value_type_rules_version: str  # "v1"
    run_timestamp: str
    path_maturity: dict[str, str] = {}   # reviewer-approved maturity labels per path


class KeyValueDetectorLockV2(BaseModel):
    """v2 lock — supersedes v1 for the KeyValueGroup safety milestone.

    v1 metrics are preserved as ``calibration_v1_fpr`` so reviewers can see
    the improvement path without re-reading the older lock record.
    """

    detector_version: str
    code_commit: str
    pipeline_stage: str
    inline_max_chars: int
    inline_min_entries: int
    adjacent_min_blocks: int
    adjacent_page_tolerance: int
    max_label_words: int
    max_value_chars: int
    xlsx_max_rows: int
    xlsx_required_columns: int
    rhetorical_label_set_checksum: str
    value_type_rules_version: str
    run_timestamp: str
    path_maturity: dict[str, str] = {}

    # v2-specific additions
    exclusion_categories: list[str]
    positive_evidence_rule_a_threshold: int = 2  # strongly-typed values
    positive_evidence_rule_b_threshold: int = 3  # schema field matches
    heuristic_inline_enabled_default: bool = False
    heuristic_adjacent_enabled_default: bool = False
    calibration_v1_fpr: float = 0.929  # Round 1 hard-negative FPR
    schema_names: list[str] = []
    strong_value_types: list[str] = []


def _get_commit() -> str:
    import subprocess
    try:
        return subprocess.run(["git","rev-parse","--short","HEAD"],
            capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _rhetorical_checksum() -> str:
    from aksharamd.scoring.key_value_detection import _RHETORICAL_LABELS
    payload = json.dumps(sorted(_RHETORICAL_LABELS))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_lock() -> KeyValueDetectorLock:
    """Build the v1 lock — DO NOT MODIFY. Preserved for historical evidence."""
    from aksharamd.scoring.key_value_detection import _MAX_LABEL_WORDS, _MAX_VALUE_CHARS
    from aksharamd.plugins.transformers.key_value_promoter import (
        _MAX_PARA_CHARS, _MAX_PARA_LINES,
    )
    from benchmarks.kv_eval.ground_truth import PathMaturityLabels
    maturity = PathMaturityLabels()
    path_maturity = {
        "native_html_dl": maturity.native_html_dl,
        "native_docx_props": maturity.native_docx_props,
        "native_xlsx_kv": maturity.native_xlsx_kv,
        "heuristic_inline": maturity.heuristic_inline,
        "heuristic_adjacent": maturity.heuristic_adjacent,
    }
    # v1 detector_version hardcoded here so this record remains stable
    # even though the runtime promoter is now v2.
    return KeyValueDetectorLock(
        detector_version="kv_promoter/v1",
        code_commit=_get_commit(),
        pipeline_stage="post_clean_pre_optimize",
        inline_max_chars=_MAX_PARA_CHARS,
        inline_min_entries=2,
        adjacent_min_blocks=4,
        adjacent_page_tolerance=1,
        max_label_words=_MAX_LABEL_WORDS,
        max_value_chars=_MAX_VALUE_CHARS,
        xlsx_max_rows=20,
        xlsx_required_columns=2,
        rhetorical_label_set_checksum=_rhetorical_checksum(),
        value_type_rules_version="v1",
        run_timestamp=datetime.now(UTC).isoformat(),
        path_maturity=path_maturity,
    )


def build_lock_v2() -> KeyValueDetectorLockV2:
    """Build the v2 lock — kv_promoter/v2 configuration snapshot."""
    from aksharamd.scoring.key_value_detection import _MAX_LABEL_WORDS, _MAX_VALUE_CHARS
    from aksharamd.plugins.transformers.key_value_promoter import (
        DETECTOR_VERSION, _MAX_PARA_CHARS, _MAX_PARA_LINES,
    )
    from aksharamd.scoring.key_value_classifier import (
        _RECOGNIZED_SCHEMAS, _STRONG_VALUE_TYPES,
    )
    from aksharamd.scoring.key_value_config import (
        KeyValueDetectionProfile, KeyValueCandidateCategory,
    )
    from benchmarks.kv_eval.ground_truth import PathMaturityLabels

    maturity = PathMaturityLabels()
    path_maturity = {
        "native_html_dl": maturity.native_html_dl,
        "native_docx_props": maturity.native_docx_props,
        "native_xlsx_kv": maturity.native_xlsx_kv,
        "heuristic_inline": maturity.heuristic_inline,
        "heuristic_adjacent": maturity.heuristic_adjacent,
    }
    default = KeyValueDetectionProfile()

    exclusions = [
        KeyValueCandidateCategory.DIALOGUE,
        KeyValueCandidateCategory.CONFIGURATION,
        KeyValueCandidateCategory.CITATION,
        KeyValueCandidateCategory.SECTION_LABEL,
        KeyValueCandidateCategory.NUMBERED_LIST,
        KeyValueCandidateCategory.LEGAL_CLAUSE,
        KeyValueCandidateCategory.ACADEMIC_DEFINITION,
        KeyValueCandidateCategory.MEDICAL_SECTION,
        KeyValueCandidateCategory.FINANCIAL_FOOTNOTE,
    ]

    return KeyValueDetectorLockV2(
        detector_version=DETECTOR_VERSION,
        code_commit=_get_commit(),
        pipeline_stage="post_clean_pre_optimize",
        inline_max_chars=_MAX_PARA_CHARS,
        inline_min_entries=2,
        adjacent_min_blocks=4,
        adjacent_page_tolerance=1,
        max_label_words=_MAX_LABEL_WORDS,
        max_value_chars=_MAX_VALUE_CHARS,
        xlsx_max_rows=20,
        xlsx_required_columns=2,
        rhetorical_label_set_checksum=_rhetorical_checksum(),
        value_type_rules_version="v1",
        run_timestamp=datetime.now(UTC).isoformat(),
        path_maturity=path_maturity,
        exclusion_categories=exclusions,
        positive_evidence_rule_a_threshold=2,
        positive_evidence_rule_b_threshold=3,
        heuristic_inline_enabled_default=default.enable_inline_heuristic,
        heuristic_adjacent_enabled_default=default.enable_adjacent_heuristic,
        calibration_v1_fpr=0.929,
        schema_names=sorted(_RECOGNIZED_SCHEMAS.keys()),
        strong_value_types=sorted(str(v) for v in _STRONG_VALUE_TYPES),
    )


if __name__ == "__main__":
    import pathlib
    lock = build_lock()
    out = pathlib.Path(__file__).parent / "detector_lock.json"
    out.write_text(json.dumps(lock.model_dump(), indent=2), encoding="utf-8")
    print(f"Written: {out}")

    lock2 = build_lock_v2()
    out2 = pathlib.Path(__file__).parent / "detector_lock_v2.json"
    out2.write_text(json.dumps(lock2.model_dump(), indent=2), encoding="utf-8")
    print(f"Written: {out2}")
