"""Review queue emission tests."""
from __future__ import annotations

import json
from pathlib import Path

from benchmarks.ocr_auto_calibration.preference import build_document_summary
from benchmarks.ocr_auto_calibration.review_queue import (
    build_review_queue,
    write_review_queue,
)
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
    # artifact_paths always carries the four resolvable-path fields; the
    # optional error_reasons list appears when any of them could not be
    # resolved (missing PDF, missing compile output, non-zero exit).
    required_path_fields = {"input", "output_dir", "markdown", "manifest"}
    assert required_path_fields <= set(row["artifact_paths"].keys())
    extras = set(row["artifact_paths"].keys()) - required_path_fields
    assert extras <= {"error_reasons"}
    # Stub _summary() uses /tmp/stub.pdf which does not exist and has no
    # compile output — so we expect an error_reasons list with at least
    # the missing-input and missing-output-dir markers, and every path
    # field should be None (never an empty-string placeholder).
    for field in required_path_fields:
        assert row["artifact_paths"][field] is None
    assert "error_reasons" in row["artifact_paths"]
    assert row["artifact_paths"]["error_reasons"]


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


# ── Artifact-path resolution — success / missing / failed / ambiguous ──


def _lay_compile_output(
    out_root: Path,
    doc_id: str,
    treatment: str,
    *,
    markdown: bytes = b"# hello\n",
    manifest: dict | None = None,
    n_manifests: int = 1,
) -> Path:
    """Materialise a plausible compile package at ``out_root/doc_id/treatment/doc_id/``."""
    pkg = out_root / doc_id / treatment / doc_id
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "document.md").write_bytes(markdown)
    payload = json.dumps(manifest or {"pages": 3}, indent=2)
    for i in range(n_manifests):
        name = "manifest.json" if i == 0 else f"manifest_{i}.json"
        # Ambiguous case needs the same *filename* twice, in different dirs.
        target = pkg if i == 0 else (pkg / f"nested_{i}")
        target.mkdir(parents=True, exist_ok=True)
        (target / "manifest.json").write_text(payload, encoding="utf-8")
    return pkg


def test_artifact_paths_resolve_on_success(tmp_path: Path) -> None:
    """When compile output exists on disk, all four fields must resolve to
    absolute paths that exist, and there must be NO error_reasons list."""
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out_root = tmp_path / "compile_outputs"
    _lay_compile_output(out_root, doc_id="doc1", treatment="unlimited_ocr")
    run = _run("unlimited_ocr", document_path=str(pdf), repetition_flag=True)
    q = build_review_queue([_summary(unlimited_ocr=run)], out_root=out_root)
    row = _rows_for(q, "unlimited_ocr")[0]
    ap = row["artifact_paths"]
    assert ap["input"] and Path(ap["input"]).exists()
    assert ap["output_dir"] and Path(ap["output_dir"]).exists()
    assert ap["markdown"] and Path(ap["markdown"]).exists()
    assert ap["manifest"] and Path(ap["manifest"]).exists()
    assert Path(ap["markdown"]).name == "document.md"
    assert Path(ap["manifest"]).name == "manifest.json"
    assert "error_reasons" not in ap


def test_missing_artifacts_produce_null_paths_with_reasons(tmp_path: Path) -> None:
    """Treatment dir absent → every path field is None (never ""), and
    the reasons list must name what could not be resolved."""
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out_root = tmp_path / "compile_outputs"  # deliberately not created
    run = _run("unlimited_ocr", document_path=str(pdf), repetition_flag=True)
    q = build_review_queue([_summary(unlimited_ocr=run)], out_root=out_root)
    row = _rows_for(q, "unlimited_ocr")[0]
    ap = row["artifact_paths"]
    assert ap["input"] and Path(ap["input"]).exists()  # PDF still resolves
    assert ap["output_dir"] is None
    assert ap["markdown"] is None
    assert ap["manifest"] is None
    # No empty-string placeholder anywhere.
    for field in ("input", "output_dir", "markdown", "manifest"):
        assert ap[field] != ""
    assert "treatment_output_dir_missing" in ap["error_reasons"]


def test_failed_treatment_records_exit_status_reason(tmp_path: Path) -> None:
    """When a treatment exited non-zero, error_reasons must name the exit
    status even if the compile output happens to be on disk. Reviewers
    must never mistake a partial/failed run for a clean one."""
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out_root = tmp_path / "compile_outputs"
    _lay_compile_output(out_root, doc_id="doc1", treatment="unlimited_ocr")
    run = _run(
        "unlimited_ocr",
        document_path=str(pdf),
        exit_status=124,
        error_message="timeout",
        repetition_flag=False,
    )
    # exit_status=0 branch is a clean run; force queue entry via failure
    q = build_review_queue([_summary(unlimited_ocr=run)], out_root=out_root)
    row = _rows_for(q, "unlimited_ocr")[0]
    ap = row["artifact_paths"]
    assert "treatment_exited_status_124" in ap["error_reasons"]
    # Paths still resolve — the reviewer needs to inspect whatever was
    # produced before the failure — but the failure reason is loud.
    assert ap["input"] and Path(ap["input"]).exists()
    assert ap["markdown"] and Path(ap["markdown"]).exists()


def test_ambiguous_multiple_manifests_flags_the_ambiguity(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out_root = tmp_path / "compile_outputs"
    _lay_compile_output(
        out_root, doc_id="doc1", treatment="unlimited_ocr", n_manifests=2
    )
    run = _run("unlimited_ocr", document_path=str(pdf), repetition_flag=True)
    q = build_review_queue([_summary(unlimited_ocr=run)], out_root=out_root)
    row = _rows_for(q, "unlimited_ocr")[0]
    ap = row["artifact_paths"]
    assert ap["manifest"] is None
    assert "ambiguous_multiple_manifests" in ap["error_reasons"]


def test_write_review_queue_revalidates_paths_before_serializing(
    tmp_path: Path,
) -> None:
    """A resolved path that vanishes before write must be downgraded to
    None with a vanished_between_resolve_and_write_<field> reason."""
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out_root = tmp_path / "compile_outputs"
    _lay_compile_output(out_root, doc_id="doc1", treatment="unlimited_ocr")
    run = _run("unlimited_ocr", document_path=str(pdf), repetition_flag=True)
    q = build_review_queue([_summary(unlimited_ocr=run)], out_root=out_root)

    # Simulate concurrent cleanup: delete the input PDF between build and write.
    pdf.unlink()

    out_json = tmp_path / "queue.json"
    write_review_queue(q, out_json)

    written = json.loads(out_json.read_text(encoding="utf-8"))
    ap = written[0]["artifact_paths"]
    assert ap["input"] is None
    assert "vanished_between_resolve_and_write_input" in ap["error_reasons"]
    # Markdown + manifest were left untouched, so they should still resolve.
    assert ap["markdown"] and Path(ap["markdown"]).exists()
    assert ap["manifest"] and Path(ap["manifest"]).exists()
