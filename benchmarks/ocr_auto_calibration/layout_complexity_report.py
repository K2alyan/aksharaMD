"""Layout-complexity evidence report writer (Commit 3).

Serializes :class:`LayoutComplexityAnalysis` payloads into two
audit-friendly artifacts:

* ``capture.json`` — the raw per-doc captures.
* ``analysis.json`` — the structured analysis (layout vs OCR table,
  false-positive report, rejected-table predictor).
* ``REPORT.md`` — a short human-readable summary a reviewer can skim
  before commissioning any further empirical work.

Nothing here executes routing decisions. The report is evidence for
Commit 4 (Auto Policy v2) design, not a routing input.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from aksharamd.plugins.parsers.layout_complexity_evaluator import (
    LAYOUT_COMPLEXITY_POLICY_VERSION,
)
from benchmarks.ocr_auto_calibration.layout_complexity_analysis import (
    LAYOUT_COMPLEXITY_ANALYSIS_VERSION,
    LayoutComplexityAnalysis,
)
from benchmarks.ocr_auto_calibration.layout_complexity_capture import (
    LayoutComplexityCapture,
    per_signal_page_counts,
)


def write_capture_json(
    *, captures: list[LayoutComplexityCapture], out_path: Path
) -> None:
    payload = {
        "policy_version": LAYOUT_COMPLEXITY_POLICY_VERSION,
        "captures": [_capture_payload(c) for c in captures],
    }
    _write_json(out_path, payload)


def write_analysis_json(
    *, analysis: LayoutComplexityAnalysis, out_path: Path
) -> None:
    payload = _analysis_payload(analysis)
    _write_json(out_path, payload)


def write_markdown_report(
    *,
    analysis: LayoutComplexityAnalysis,
    captures: list[LayoutComplexityCapture],
    corpus_name: str,
    out_path: Path,
) -> None:
    lines: list[str] = []
    lines.append(f"# Layout Complexity v1 — Evidence Report ({corpus_name})")
    lines.append("")
    lines.append(
        f"Policy version: `{LAYOUT_COMPLEXITY_POLICY_VERSION}` "
        f"| Analysis version: `{LAYOUT_COMPLEXITY_ANALYSIS_VERSION}`"
    )
    lines.append("")
    lines.append(
        "Evidence only. No production routing decision is derived from "
        "this report. See `docs/adr/ocr-auto-policy-v1.md` for the "
        "current routing policy."
    )
    lines.append("")

    lines.extend(_render_layout_vs_ocr_section(analysis, captures))
    lines.append("")
    lines.extend(_render_false_positive_section(analysis))
    lines.append("")
    lines.extend(_render_rejected_table_section(analysis))
    lines.append("")
    lines.extend(_render_caveats_section())
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _render_layout_vs_ocr_section(
    analysis: LayoutComplexityAnalysis,
    captures: list[LayoutComplexityCapture],
) -> list[str]:
    lines: list[str] = ["## Layout complexity vs OCR difficulty", ""]
    if not analysis.layout_vs_ocr.rows:
        lines.append("_No documents captured — corpus not hydrated on this host._")
        return lines

    lines.append(
        "| doc | pages | chars | ocr_pages | ocr_frac | score | band | signals |"
    )
    lines.append(
        "|-----|------:|------:|----------:|---------:|------:|------|---------|"
    )
    for row in analysis.layout_vs_ocr.rows:
        signals = ", ".join(row.triggered_signals) or "_(none)_"
        lines.append(
            f"| `{row.document_id}` | {row.total_pages} | "
            f"{row.page_char_count_total} | {row.ocr_required_page_count} | "
            f"{row.ocr_required_fraction:.3f} | {row.layout_score:.1f} | "
            f"{row.layout_band} | {signals} |"
        )

    summary = analysis.layout_vs_ocr.summary
    lines.append("")
    lines.append(
        f"Summary: {int(summary['documents'])} document(s); bands "
        f"simple={int(summary['band.simple_count'])} "
        f"moderate={int(summary['band.moderate_count'])} "
        f"complex={int(summary['band.complex_count'])}; "
        f"native-text-dominant={int(summary['native_text_dominant_count'])}."
    )
    return lines


def _render_false_positive_section(
    analysis: LayoutComplexityAnalysis,
) -> list[str]:
    fp = analysis.false_positives
    lines: list[str] = [
        "## False-positive candidates (layout complex, OCR simple)",
        "",
        (
            f"Threshold: OCR-required fraction "
            f"<= {fp.threshold_ocr_fraction_max:.2f} AND native text char "
            f"count >= {fp.threshold_min_total_chars}. "
            f"Documents considered: {fp.total_documents_considered}. "
            f"Excluded as too-short: {fp.documents_excluded_short}."
        ),
        "",
    ]
    if not fp.entries:
        lines.append("_No false-positive candidates on this corpus._")
        return lines

    lines.append("| doc | band | score | ocr_frac | chars | signals |")
    lines.append("|-----|------|------:|---------:|------:|---------|")
    for entry in fp.entries:
        signals = ", ".join(entry.triggered_signals) or "_(none)_"
        lines.append(
            f"| `{entry.document_id}` | {entry.layout_band} | "
            f"{entry.layout_score:.1f} | "
            f"{entry.ocr_required_fraction:.3f} | "
            f"{entry.page_char_count_total} | {signals} |"
        )
    lines.append("")
    lines.append(
        "**Interpretation**: layout complexity alone MUST NOT drive UOC "
        "routing on these documents — they are native-text-dominant. "
        "Auto Policy v2 must combine layout complexity with the "
        "OCR-required signal (as v1 already does)."
    )
    return lines


def _render_rejected_table_section(
    analysis: LayoutComplexityAnalysis,
) -> list[str]:
    rp = analysis.rejected_table_predictor
    lines: list[str] = [
        "## Rejected-table-candidate as a UOC-benefit predictor",
        "",
    ]
    if not rp.pairs:
        lines.append("_No captures — nothing to correlate._")
        return lines

    lines.append("| doc | rejected_table_candidate_total | ocr_required_fraction |")
    lines.append("|-----|-------------------------------:|----------------------:|")
    for doc_id, count, frac in rp.pairs:
        lines.append(f"| `{doc_id}` | {count} | {frac:.3f} |")
    lines.append("")
    if rp.correlation_available:
        assert rp.pearson_r is not None
        lines.append(f"Pearson r = **{rp.pearson_r:.3f}**")
        lines.append("")
    lines.append(rp.interpretation)
    lines.append("")
    lines.append(
        "This is an interim proxy: it correlates the signal against the "
        "OCR-required fraction, not against a UOC-vs-Tesseract structural-gain "
        "delta. A definitive predictor evaluation requires actual OCR-treatment "
        "runs and is deliberately out of scope for this evidence commit."
    )
    return lines


def _render_caveats_section() -> list[str]:
    return [
        "## Caveats",
        "",
        (
            "* The scientific corpus (Attention, ResNet, BERT, DDPM, CLIP) "
            "is reproducible and layout-diverse but consists of mostly "
            "native-text arXiv preprints. It exercises the LAYOUT signals "
            "(multi-column, tables, figure captions, math bboxes, rejected "
            "table candidates) but is NOT an OCR-difficulty benchmark. "
            "A high layout score on these papers is correct behavior for "
            "the evaluator; it is Commit 4 (Auto Policy v2) that decides "
            "whether such a doc should be routed to UOC."
        ),
        (
            "* No production routing or manifest change is introduced by "
            "this evidence run."
        ),
        (
            "* The `rejected_table_candidate_count` signal remains "
            "conservatively capped (per-page cap 5, document cap 15 points "
            "out of 100) until a UOC-vs-Tesseract structural-gain benchmark "
            "confirms its predictive value."
        ),
    ]


def _capture_payload(capture: LayoutComplexityCapture) -> dict[str, object]:
    return {
        "document_id": capture.document_id,
        "total_pages": capture.total_pages,
        "ocr_required_page_count": capture.ocr_required_page_count,
        "ocr_required_fraction": capture.ocr_required_fraction,
        "page_char_count_total": capture.page_char_count_total,
        "rejected_table_candidate_total": capture.rejected_table_candidate_total,
        "parse_runtime_ms": capture.parse_runtime_ms,
        "evaluate_runtime_ms": capture.evaluate_runtime_ms,
        "per_signal_page_counts": dict(per_signal_page_counts(capture)),
        "decision": {
            "score": capture.decision.score,
            "band": capture.decision.band,
            "triggered_signals": list(capture.decision.triggered_signals),
            "policy_version": capture.decision.policy_version,
            "extractor_version": capture.decision.extractor_version,
            "reason": capture.decision.reason,
            "measurements": dict(capture.decision.measurements),
            "top_contributing_pages": [
                {
                    "page_index": p.page_index,
                    "contribution": p.contribution,
                    "triggered_signals": list(p.triggered_signals),
                }
                for p in capture.decision.top_contributing_pages
            ],
        },
    }


def _analysis_payload(analysis: LayoutComplexityAnalysis) -> dict[str, object]:
    return {
        "analysis_version": analysis.analysis_version,
        "layout_vs_ocr": {
            "rows": [
                {
                    "document_id": row.document_id,
                    "total_pages": row.total_pages,
                    "page_char_count_total": row.page_char_count_total,
                    "ocr_required_page_count": row.ocr_required_page_count,
                    "ocr_required_fraction": row.ocr_required_fraction,
                    "layout_score": row.layout_score,
                    "layout_band": row.layout_band,
                    "triggered_signals": list(row.triggered_signals),
                    "rejected_table_candidate_total": (
                        row.rejected_table_candidate_total
                    ),
                    "per_signal_page_counts": dict(row.per_signal_page_counts),
                    "top_contributing_page_indices": list(
                        row.top_contributing_page_indices
                    ),
                    "is_native_text_dominant": row.is_native_text_dominant,
                }
                for row in analysis.layout_vs_ocr.rows
            ],
            "summary": dict(analysis.layout_vs_ocr.summary),
        },
        "false_positives": {
            "entries": [
                {
                    "document_id": e.document_id,
                    "layout_band": e.layout_band,
                    "layout_score": e.layout_score,
                    "ocr_required_fraction": e.ocr_required_fraction,
                    "page_char_count_total": e.page_char_count_total,
                    "triggered_signals": list(e.triggered_signals),
                    "reason": e.reason,
                }
                for e in analysis.false_positives.entries
            ],
            "total_documents_considered": (
                analysis.false_positives.total_documents_considered
            ),
            "documents_excluded_short": (
                analysis.false_positives.documents_excluded_short
            ),
            "threshold_ocr_fraction_max": (
                analysis.false_positives.threshold_ocr_fraction_max
            ),
            "threshold_min_total_chars": (
                analysis.false_positives.threshold_min_total_chars
            ),
        },
        "rejected_table_predictor": {
            "pairs": [
                {"document_id": p[0], "rejected_count": p[1], "ocr_fraction": p[2]}
                for p in analysis.rejected_table_predictor.pairs
            ],
            "correlation_available": (
                analysis.rejected_table_predictor.correlation_available
            ),
            "pearson_r": analysis.rejected_table_predictor.pearson_r,
            "interpretation": analysis.rejected_table_predictor.interpretation,
        },
    }


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "write_analysis_json",
    "write_capture_json",
    "write_markdown_report",
]
