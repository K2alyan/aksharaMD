"""Analysis-ready record shape emitted per (doc, parser) pair.

An analysis-ready record is the single JSON document a downstream
analysis script can consume to reproduce any published number without
re-running the pipeline. It bundles:

- The pair identity (doc_id, parser, corpus).
- All stage results (status + reason + payload).
- All hashes needed to re-verify (source, extraction, normalized).
- The versioned instrument identifiers (aksharamd, scoring policy,
  normalization, mapping).
- The corpus's declared capabilities.

Under A.1 the record schema is defined and emitted for the 3-doc rerun.
It is not yet finalized as the V1 freeze schema — that happens at
Authorization C.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any


ANALYSIS_RECORD_SCHEMA_VERSION = "0.1"


def build_analysis_record(pair_summary: dict[str, Any]) -> dict[str, Any]:
    """Consolidate a per-pair summary into the canonical analysis record.

    ``pair_summary`` is the shape emitted by
    ``smoke_run_v2._pair_stages``. This function pulls out the fields a
    downstream analysis pass needs into a stable, versioned schema.
    """
    stages = {s["stage"]: s for s in pair_summary.get("stages", [])}

    def _payload(name: str) -> dict[str, Any]:
        return dict((stages.get(name) or {}).get("payload") or {})

    def _status(name: str) -> str:
        return (stages.get(name) or {}).get("status", "MISSING")

    return {
        "schema_version": ANALYSIS_RECORD_SCHEMA_VERSION,
        "authorization": "A.1c",
        "identity": {
            "doc_id": pair_summary.get("doc_id"),
            "parser": pair_summary.get("parser"),
            "corpus_name": pair_summary.get("corpus_name"),
        },
        "corpus_capabilities": pair_summary.get("corpus_capabilities", {}),
        "hashes": {
            "source_sha256": _payload("source_ingestion").get("sha256"),
            "extraction_sha256": _payload("output_capture").get("output_sha256"),
            "normalized_sha256": _payload("normalization").get("normalized_sha256"),
        },
        "instruments": {
            "aksharamd_scoring_policy_version": _payload(
                "aksharamd_evaluation"
            ).get("scoring_policy_version"),
            "normalization_version": _payload("normalization").get(
                "normalization_version"
            ),
        },
        "stage_status": {name: _status(name) for name in stages.keys()},
        "conventional_metric_summary": _payload("conventional_measurement"),
        "aksharamd_result": _payload("aksharamd_evaluation"),
        "detector_diagnostics": _payload("detector_diagnostics"),
        "provenance": _payload("provenance_recording"),
        "timing": {
            "total_elapsed_s": pair_summary.get("total_elapsed_s"),
            "peak_rss_delta_mb": pair_summary.get("peak_rss_delta_mb"),
        },
    }
