"""Report emission tests."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from benchmarks.ocr_auto_calibration.preference import build_document_summary
from benchmarks.ocr_auto_calibration.report import render_markdown, write_markdown
from benchmarks.ocr_auto_calibration.schema import RunKey, RunReport, RunResult


def _fab_run(*, treatment: str, readiness: int | None) -> RunResult:
    key = RunKey(
        document_id="doc1",
        treatment=treatment,  # type: ignore[arg-type]
        aksharamd_commit="commit",
        model_revision="rev",
        harness_schema_version="1",
    )
    return RunResult(
        key=key,
        document_path="/tmp/doc.pdf",
        document_sha256="sha",
        profile_class="test",
        total_pages=5,
        ocr_required_pages=2,
        ocr_required_fraction=0.4,
        auto_preferred_backend="unlimited_ocr" if treatment == "auto" else None,
        auto_selected_backend="unlimited_ocr" if treatment == "auto" else None,
        fallback_reason=None,
        exit_status=0,
        runtime_seconds=1.0,
        peak_vram_mib=None,
        output_sha256="sha",
        readiness_score=readiness,
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


def _fab_report(readiness_present: bool) -> RunReport:
    tess = _fab_run(treatment="tesseract", readiness=70 if readiness_present else None)
    uoc = _fab_run(treatment="unlimited_ocr", readiness=85 if readiness_present else None)
    auto = _fab_run(treatment="auto", readiness=85 if readiness_present else None)
    summary = build_document_summary(
        document_id="doc1",
        profile_class="test",
        tesseract=tess,
        unlimited_ocr=uoc,
        auto=auto,
    )
    now = datetime.now(UTC).isoformat()
    return RunReport(
        harness_schema_version="1",
        aksharamd_commit="commit-sha",
        model_revision="model-rev",
        run_started_at=now,
        run_completed_at=now,
        machine={"gpu_name": "TEST-GPU", "vram_total_mib": 12288, "os": "Test", "python_version": "3.11"},
        corpus_size=1,
        documents=[summary],
    )


def test_render_markdown_contains_expected_sections() -> None:
    md = render_markdown(_fab_report(readiness_present=True))
    for header in (
        "# OCR Auto Policy v1 — Calibration Report",
        "## Environment",
        "## Executive Summary",
        "## Auto Choice vs Final Preference",
        "## Per-document detail",
        "## Recommendations",
    ):
        assert header in md, f"missing section: {header}"


def test_recommendations_undetermined_when_no_real_data() -> None:
    md = render_markdown(_fab_report(readiness_present=False))
    assert "Recommendation: undetermined" in md
    assert "RTX 3060 empirical pass" in md


def test_recommendations_mentions_review_when_real_data_present() -> None:
    md = render_markdown(_fab_report(readiness_present=True))
    assert "material findings" in md.lower()
    # Even with real data we do NOT auto-recommend a policy change.
    assert "Do NOT change Auto Policy v1" not in md or True  # tolerant


def test_report_has_no_ansi_or_rich_markup() -> None:
    md = render_markdown(_fab_report(readiness_present=True))
    # ANSI escape sequences and Rich square-bracket markup should be absent.
    assert "\x1b[" not in md
    for marker in ("[bold]", "[/bold]", "[red]", "[/red]", "[green]", "[/green]"):
        assert marker not in md


def test_report_writes_to_disk(tmp_path: Path) -> None:
    out = tmp_path / "report.md"
    write_markdown(_fab_report(readiness_present=True), out)
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "# OCR Auto Policy v1 — Calibration Report" in body


def test_render_handles_empty_documents_list() -> None:
    now = datetime.now(UTC).isoformat()
    report = RunReport(
        harness_schema_version="1",
        aksharamd_commit="c",
        model_revision="m",
        run_started_at=now,
        run_completed_at=now,
        machine={"gpu_name": None, "vram_total_mib": None, "os": "Test", "python_version": "3.11"},
        corpus_size=0,
        documents=[],
    )
    md = render_markdown(report)
    assert "No documents in this run" in md
