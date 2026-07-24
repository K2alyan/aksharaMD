"""Layered preference labelling tests."""
from __future__ import annotations

from benchmarks.ocr_auto_calibration.preference import (
    build_document_summary,
    compute_auto_match,
    compute_automatic_preference,
    compute_final_preference,
)
from benchmarks.ocr_auto_calibration.schema import RunKey, RunResult

_KEY_STUB = RunKey(
    document_id="stub",
    treatment="tesseract",
    aksharamd_commit="c",
    model_revision="m",
    harness_schema_version="1",
)


def _make_run(
    *,
    treatment: str = "tesseract",
    readiness: int | None = 70,
    repetition: bool = False,
    exit_status: int = 0,
    runtime_seconds: float = 1.0,
    auto_selected: str | None = None,
) -> RunResult:
    key = RunKey(
        document_id="stub",
        treatment=treatment,  # type: ignore[arg-type]
        aksharamd_commit="c",
        model_revision="m",
        harness_schema_version="1",
    )
    return RunResult(
        key=key,
        document_path="/tmp/stub.pdf",
        document_sha256="sha",
        profile_class="test",
        total_pages=10,
        ocr_required_pages=3,
        ocr_required_fraction=0.3,
        auto_preferred_backend=auto_selected,
        auto_selected_backend=auto_selected,
        fallback_reason=None,
        exit_status=exit_status,
        runtime_seconds=runtime_seconds,
        peak_vram_mib=None,
        output_sha256="deadbeef",
        readiness_score=readiness,
        quality_band="Ready",
        warning_codes=[],
        output_markdown_length=1000,
        output_paragraph_count=10,
        output_heading_count=3,
        output_image_ref_count=1,
        output_table_count=1,
        max_repeated_ngram_count=1,
        repetition_flag=repetition,
        source_page_provenance_complete=True,
    )


def test_uoc_wins_when_readiness_beats_tesseract_by_ten() -> None:
    tess = _make_run(readiness=70)
    uoc = _make_run(readiness=80)
    assert compute_automatic_preference(tess, uoc) == "unlimited_ocr"


def test_uoc_readiness_win_disqualified_by_repetition() -> None:
    tess = _make_run(readiness=70)
    uoc = _make_run(readiness=80, repetition=True)
    assert compute_automatic_preference(tess, uoc) == "tesseract"


def test_uoc_readiness_win_disqualified_by_runtime_multiplier() -> None:
    tess = _make_run(readiness=70, runtime_seconds=1.0)
    uoc = _make_run(readiness=80, runtime_seconds=20.0)  # 20x
    assert compute_automatic_preference(tess, uoc) == "tesseract"


def test_uoc_readiness_win_disqualified_by_nonzero_exit() -> None:
    tess = _make_run(readiness=70)
    uoc = _make_run(readiness=80, exit_status=1)
    assert compute_automatic_preference(tess, uoc) == "tesseract"


def test_close_readiness_within_three_is_undetermined() -> None:
    # Neither the +5 UOC threshold nor the -2 Tesseract threshold triggers:
    # UOC ahead by 3 (< 5), and Tesseract is 3 below UOC (< -2 tolerance
    # means Tesseract needs to be >= UOC - 2 which is not true here).
    tess = _make_run(readiness=70)
    uoc = _make_run(readiness=73)
    assert compute_automatic_preference(tess, uoc) == "undetermined"


def test_close_readiness_within_tolerance_prefers_tesseract() -> None:
    # Tesseract 1 point below UOC -> within the -2 tolerance -> tesseract wins.
    tess = _make_run(readiness=79)
    uoc = _make_run(readiness=80)
    assert compute_automatic_preference(tess, uoc) == "tesseract"


def test_final_preference_honours_human_override() -> None:
    assert compute_final_preference("tesseract", "unlimited_ocr") == "unlimited_ocr"
    assert compute_final_preference("undetermined", "tesseract") == "tesseract"
    assert compute_final_preference("undetermined", None) == "undetermined"


def test_auto_match_none_when_final_undetermined() -> None:
    assert compute_auto_match("tesseract", "undetermined") is None


def test_auto_match_true_when_selected_equals_final() -> None:
    assert compute_auto_match("unlimited_ocr", "unlimited_ocr") is True
    assert compute_auto_match("tesseract", "unlimited_ocr") is False


def test_build_document_summary_wires_all_fields() -> None:
    tess = _make_run(readiness=70)
    uoc = _make_run(readiness=80)
    auto = _make_run(readiness=80, auto_selected="unlimited_ocr")
    summary = build_document_summary(
        document_id="doc1",
        profile_class="mixed",
        tesseract=tess,
        unlimited_ocr=uoc,
        auto=auto,
    )
    assert summary.document_id == "doc1"
    assert summary.automatic_preference == "unlimited_ocr"
    assert summary.final_preference == "unlimited_ocr"
    assert summary.auto_matched_final_preference is True
