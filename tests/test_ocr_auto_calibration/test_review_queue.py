"""Review queue emission tests."""
from __future__ import annotations

from benchmarks.ocr_auto_calibration.preference import build_document_summary
from benchmarks.ocr_auto_calibration.review_queue import build_review_queue
from benchmarks.ocr_auto_calibration.schema import RunKey, RunResult


def _run(
    treatment: str = "tesseract",
    **overrides,
) -> RunResult:
    key = RunKey(
        document_id="stub",
        treatment=treatment,  # type: ignore[arg-type]
        aksharamd_commit="c",
        model_revision="m",
        harness_schema_version="1",
    )
    base = dict(
        key=key,
        document_path="/tmp/stub.pdf",
        document_sha256="sha",
        profile_class="test",
        total_pages=5,
        ocr_required_pages=2,
        ocr_required_fraction=0.4,
        auto_preferred_backend=None,
        auto_selected_backend=None,
        fallback_reason=None,
        exit_status=0,
        runtime_seconds=1.0,
        peak_vram_mib=None,
        output_sha256="sha",
        readiness_score=70,
        quality_band="Ready",
        warning_codes=[],
        output_markdown_length=1000,
        output_paragraph_count=10,
        output_heading_count=3,
        output_image_ref_count=1,
        output_table_count=1,
        max_repeated_ngram_count=1,
        repetition_flag=False,
        source_page_provenance_complete=True,
    )
    base.update(overrides)
    return RunResult(**base)


def _summary(**runs) -> object:
    tess = runs.get("tesseract", _run("tesseract"))
    uoc = runs.get("unlimited_ocr", _run("unlimited_ocr"))
    auto = runs.get("auto", _run("auto"))
    return build_document_summary(
        document_id="doc1",
        profile_class="test",
        tesseract=tess,
        unlimited_ocr=uoc,
        auto=auto,
    )


def _rows_for(queue, treatment):
    return [r for r in queue if r["treatment"] == treatment]


def test_repetition_triggers_high_priority_row() -> None:
    q = build_review_queue(
        [_summary(unlimited_ocr=_run("unlimited_ocr", repetition_flag=True))]
    )
    rows = _rows_for(q, "unlimited_ocr")
    assert rows and rows[0]["priority"] == "high"
    assert "repetition_detected" in rows[0]["reasons"]


def test_treatment_failure_triggers_high_priority_row() -> None:
    q = build_review_queue(
        [_summary(tesseract=_run("tesseract", exit_status=1))]
    )
    rows = _rows_for(q, "tesseract")
    assert rows and rows[0]["priority"] == "high"
    assert "treatment_failed" in rows[0]["reasons"]


def test_provenance_incomplete_triggers_medium_row() -> None:
    q = build_review_queue(
        [_summary(auto=_run("auto", source_page_provenance_complete=False))]
    )
    rows = _rows_for(q, "auto")
    assert rows and rows[0]["priority"] == "medium"
    assert "source_page_provenance_incomplete" in rows[0]["reasons"]


def test_near_empty_output_triggers_low_row() -> None:
    q = build_review_queue(
        [_summary(
            auto=_run("auto", output_markdown_length=50, total_pages=5),
        )]
    )
    rows = _rows_for(q, "auto")
    assert rows and rows[0]["priority"] == "low"
    assert "near_empty_output" in rows[0]["reasons"]


def test_material_disagreement_triggers_medium_rows_for_both_backends() -> None:
    # Readiness within 3; structural counts differ substantially.
    tess = _run("tesseract", readiness_score=70, output_heading_count=10, output_paragraph_count=30)
    uoc = _run("unlimited_ocr", readiness_score=72, output_heading_count=2, output_paragraph_count=5)
    q = build_review_queue([_summary(tesseract=tess, unlimited_ocr=uoc)])
    reasons_tess = _rows_for(q, "tesseract")[0]["reasons"]
    reasons_uoc = _rows_for(q, "unlimited_ocr")[0]["reasons"]
    assert "materially_disagree_but_metrics_inconclusive" in reasons_tess
    assert "materially_disagree_but_metrics_inconclusive" in reasons_uoc


def test_auto_chose_uoc_but_tesseract_structurally_stronger_triggers_auto_row() -> None:
    tess = _run("tesseract", output_heading_count=10, output_paragraph_count=30)
    uoc = _run("unlimited_ocr", output_heading_count=1, output_paragraph_count=2)
    auto = _run("auto", auto_selected_backend="unlimited_ocr")
    q = build_review_queue([_summary(tesseract=tess, unlimited_ocr=uoc, auto=auto)])
    rows = _rows_for(q, "auto")
    assert rows
    assert "auto_chose_uoc_but_tesseract_appears_stronger" in rows[0]["reasons"]


def test_auto_chose_tesseract_but_uoc_structurally_stronger_triggers_auto_row() -> None:
    tess = _run("tesseract", output_heading_count=1, output_paragraph_count=2)
    uoc = _run("unlimited_ocr", output_heading_count=10, output_paragraph_count=30)
    auto = _run("auto", auto_selected_backend="tesseract")
    q = build_review_queue([_summary(tesseract=tess, unlimited_ocr=uoc, auto=auto)])
    rows = _rows_for(q, "auto")
    assert rows
    assert "auto_chose_tesseract_but_uoc_appears_stronger" in rows[0]["reasons"]


def test_queue_schema_matches_user_spec_shape() -> None:
    q = build_review_queue(
        [_summary(unlimited_ocr=_run("unlimited_ocr", repetition_flag=True))]
    )
    assert q, "expected at least one row"
    row = q[0]
    assert set(row.keys()) == {"doc_id", "treatment", "reasons", "artifact_paths", "priority"}
    assert row["priority"] in ("high", "medium", "low")
    assert set(row["artifact_paths"].keys()) == {"input", "markdown", "manifest"}


def test_clean_run_produces_empty_queue() -> None:
    q = build_review_queue([_summary()])
    assert q == []


def test_priority_escalates_when_multiple_reasons_stack() -> None:
    # Same treatment fires provenance-incomplete (medium) and repetition (high).
    bad = _run(
        "unlimited_ocr",
        repetition_flag=True,
        source_page_provenance_complete=False,
    )
    q = build_review_queue([_summary(unlimited_ocr=bad)])
    row = _rows_for(q, "unlimited_ocr")[0]
    assert row["priority"] == "high"
    assert "repetition_detected" in row["reasons"]
    assert "source_page_provenance_incomplete" in row["reasons"]
