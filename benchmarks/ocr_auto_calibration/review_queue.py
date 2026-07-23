"""Human-review queue emission for the OCR Auto Policy v1 harness.

The queue captures every document where the harness cannot rank Tesseract
vs. UOC with confidence, plus any hard-fail or provenance-incomplete case.
Priority buckets:

* ``high`` — repetition detector fired OR a treatment errored out.
* ``medium`` — the two backends disagree materially but automated metrics
  cannot rank them.
* ``low`` — near-empty output for a small document (edge case, unlikely to
  materially change policy calibration).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from .harness import DEFAULT_COMPILE_OUTPUTS_DIR
from .preference import (
    MATERIAL_DISAGREEMENT_READINESS_WINDOW,
    NEAR_EMPTY_MARKDOWN_CHARS,
)
from .schema import DocumentSummary, RunResult

Priority = Literal["high", "medium", "low"]


def _artifact_paths_for(
    summary: DocumentSummary,
    treatment: str,
    *,
    out_root: Path,
) -> dict[str, Any]:
    """Resolve real paths to the compile artifacts for one treatment.

    Returns a dict with four path fields (``input``, ``output_dir``,
    ``markdown``, ``manifest``) plus an optional ``error_reasons`` list.
    Every path is either an absolute string that exists on disk right
    now or ``None`` accompanied by a reason in ``error_reasons``.
    Placeholder values (empty strings, non-existent paths) are never
    emitted — a reviewer following any populated path is guaranteed to
    reach a real file.
    """
    run: RunResult = getattr(summary, treatment)
    treatment_dir = out_root / summary.document_id / treatment
    result: dict[str, Any] = {
        "input": None,
        "output_dir": None,
        "markdown": None,
        "manifest": None,
    }
    error_reasons: list[str] = []

    # Input PDF
    if not run.document_path:
        error_reasons.append("no_document_path_recorded")
    elif not Path(run.document_path).exists():
        error_reasons.append("input_pdf_missing_on_disk")
    else:
        result["input"] = str(Path(run.document_path).resolve())

    # Treatment output directory + compile-package root discovery
    if not treatment_dir.exists():
        error_reasons.append("treatment_output_dir_missing")
    else:
        result["output_dir"] = str(treatment_dir.resolve())
        manifests = sorted(treatment_dir.rglob("manifest.json"))
        markdowns = sorted(treatment_dir.rglob("document.md"))
        if not manifests:
            error_reasons.append("no_manifest_found")
        elif len(manifests) > 1:
            error_reasons.append("ambiguous_multiple_manifests")
        else:
            result["manifest"] = str(manifests[0].resolve())
        if not markdowns:
            error_reasons.append("no_markdown_found")
        elif len(markdowns) > 1:
            error_reasons.append("ambiguous_multiple_markdowns")
        else:
            result["markdown"] = str(markdowns[0].resolve())

    # Treatment failure surfaces even when artifacts happen to exist —
    # the queue reader must see that the reported paths belong to a run
    # that did not complete cleanly.
    if run.exit_status != 0:
        error_reasons.append(f"treatment_exited_status_{run.exit_status}")

    if error_reasons:
        result["error_reasons"] = error_reasons
    return result


def _reasons_and_priority(
    summary: DocumentSummary,
) -> tuple[list[tuple[str, list[str], Priority]], bool]:
    """Return per-treatment ``(treatment, reasons, priority)`` triples.

    Second tuple element: True if the *auto* treatment itself needs review
    because it disagreed with the apparent structural winner. The caller
    emits a separate queue row for that case.
    """
    rows: list[tuple[str, list[str], Priority]] = []

    tess = summary.tesseract
    uoc = summary.unlimited_ocr
    auto = summary.auto

    # 1. Repetition
    for treatment_name, run in (
        ("tesseract", tess),
        ("unlimited_ocr", uoc),
        ("auto", auto),
    ):
        if run.repetition_flag:
            rows.append(
                (
                    treatment_name,
                    ["repetition_detected"],
                    "high",
                )
            )

    # 2. Failure (non-zero exit)
    for treatment_name, run in (
        ("tesseract", tess),
        ("unlimited_ocr", uoc),
        ("auto", auto),
    ):
        if run.exit_status != 0:
            rows.append(
                (
                    treatment_name,
                    ["treatment_failed"],
                    "high",
                )
            )

    # 3. Provenance incomplete
    for treatment_name, run in (
        ("tesseract", tess),
        ("unlimited_ocr", uoc),
        ("auto", auto),
    ):
        if not run.source_page_provenance_complete:
            rows.append(
                (
                    treatment_name,
                    ["source_page_provenance_incomplete"],
                    "medium",
                )
            )

    # 4. Near-empty output for non-trivial-page-count docs
    for treatment_name, run in (
        ("tesseract", tess),
        ("unlimited_ocr", uoc),
        ("auto", auto),
    ):
        if (
            run.output_markdown_length < NEAR_EMPTY_MARKDOWN_CHARS
            and run.total_pages > 1
            and run.exit_status == 0
        ):
            rows.append(
                (
                    treatment_name,
                    ["near_empty_output"],
                    "low",
                )
            )

    # 5. Material disagreement between Tesseract and UOC with no obvious winner
    tess_r = tess.readiness_score if tess.readiness_score is not None else 0
    uoc_r = uoc.readiness_score if uoc.readiness_score is not None else 0
    if (
        abs(tess_r - uoc_r) <= MATERIAL_DISAGREEMENT_READINESS_WINDOW
        and not tess.repetition_flag
        and not uoc.repetition_flag
        and tess.exit_status == 0
        and uoc.exit_status == 0
        and _structural_gap_material(tess, uoc)
    ):
        rows.append(
            (
                "tesseract",
                ["materially_disagree_but_metrics_inconclusive"],
                "medium",
            )
        )
        rows.append(
            (
                "unlimited_ocr",
                ["materially_disagree_but_metrics_inconclusive"],
                "medium",
            )
        )

    # 6. Auto chose a backend the other treatment appears to beat structurally
    auto_row_needed = False
    if auto.auto_selected_backend == "unlimited_ocr" and _structural_winner(
        tess, uoc
    ) == "tesseract":
        rows.append(
            (
                "auto",
                ["auto_chose_uoc_but_tesseract_appears_stronger"],
                "medium",
            )
        )
        auto_row_needed = True
    elif auto.auto_selected_backend == "tesseract" and _structural_winner(
        tess, uoc
    ) == "unlimited_ocr":
        rows.append(
            (
                "auto",
                ["auto_chose_tesseract_but_uoc_appears_stronger"],
                "medium",
            )
        )
        auto_row_needed = True

    return rows, auto_row_needed


def _structural_gap_material(a: RunResult, b: RunResult) -> bool:
    """Heuristic: any of the structural counts differ by > 20% or > 2 units."""
    keys = (
        "output_paragraph_count",
        "output_heading_count",
        "output_table_count",
        "output_image_ref_count",
    )
    for k in keys:
        va = getattr(a, k)
        vb = getattr(b, k)
        base = max(va, vb, 1)
        if abs(va - vb) >= 2 and abs(va - vb) / base > 0.20:
            return True
    return False


def _structural_winner(a: RunResult, b: RunResult) -> str | None:
    """Return 'tesseract' or 'unlimited_ocr' when one clearly leads structurally."""
    tess_score = a.output_heading_count + a.output_paragraph_count + a.output_table_count
    uoc_score = b.output_heading_count + b.output_paragraph_count + b.output_table_count
    if tess_score > uoc_score * 1.25:
        return "tesseract"
    if uoc_score > tess_score * 1.25:
        return "unlimited_ocr"
    return None


def build_review_queue(
    summaries: list[DocumentSummary],
    *,
    out_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Emit the queue for a list of DocumentSummary records.

    ``out_root`` defaults to :data:`~.harness.DEFAULT_COMPILE_OUTPUTS_DIR`
    so path resolution matches the location the harness writes to.
    """
    resolved_out_root = out_root or DEFAULT_COMPILE_OUTPUTS_DIR
    queue: list[dict[str, Any]] = []
    for summary in summaries:
        rows, _auto_needed = _reasons_and_priority(summary)
        # Merge multiple reasons for the same treatment into a single row so
        # a reviewer sees the whole story at once.
        merged: dict[str, dict[str, Any]] = {}
        for treatment_name, reasons, priority in rows:
            entry = merged.setdefault(
                treatment_name,
                {
                    "doc_id": summary.document_id,
                    "treatment": treatment_name,
                    "reasons": [],
                    "artifact_paths": _artifact_paths_for(
                        summary, treatment_name, out_root=resolved_out_root
                    ),
                    "priority": priority,
                },
            )
            for reason in reasons:
                if reason not in entry["reasons"]:
                    entry["reasons"].append(reason)
            # Escalate priority to the strictest across merged rows.
            entry["priority"] = _max_priority(entry["priority"], priority)
        queue.extend(merged.values())
    return queue


def _max_priority(a: Priority, b: Priority) -> Priority:
    order: dict[Priority, int] = {"low": 0, "medium": 1, "high": 2}
    return a if order[a] >= order[b] else b


def write_review_queue(queue: list[dict[str, Any]], output_path: Path) -> None:
    """Persist the queue as JSON at *output_path*.

    Every populated (non-null) path in ``artifact_paths`` is re-validated
    against the filesystem just before writing. If a path was resolved
    but the file has vanished between build and write, we downgrade the
    field to ``None`` and append a ``vanished_between_resolve_and_write``
    reason. This keeps the on-disk queue truthful even under concurrent
    cleanup.
    """
    for entry in queue:
        paths = entry.get("artifact_paths") or {}
        reasons = list(paths.get("error_reasons", []))
        for field in ("input", "output_dir", "markdown", "manifest"):
            value = paths.get(field)
            if value and not Path(value).exists():
                paths[field] = None
                marker = f"vanished_between_resolve_and_write_{field}"
                if marker not in reasons:
                    reasons.append(marker)
        if reasons:
            paths["error_reasons"] = reasons
        elif "error_reasons" in paths:
            del paths["error_reasons"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(queue, fh, indent=2, sort_keys=False)
