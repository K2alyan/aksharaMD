"""Allowlist projection — the review surface for the smoke.

Given a complete execution record (dict) and a reviewer artifact
(dict), return a projection containing only the fields on the
smoke-spec's inspectable allowlist. Prohibited fields, if present in
the input, are dropped; the projection function refuses to emit them.

The projection surface is intentionally the ONLY code path the
smoke-review reads. If a reviewer wants to see a value not in the
allowlist, they cannot get it via this function; they would have to
open the raw file, which is outside the smoke-review procedure and
would be flagged in the review log.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# From smoke_spec_v1.json review_allowlist. Kept in sync with the config
# via test (tests/smoke_b1a_7b/test_allowlist.py) rather than by loading
# the config here — the config is a static declaration; this module is
# the enforcement point.

INSPECTABLE_EXECUTION_RECORD_FIELDS = frozenset({
    "pair_id",
    "canonical_id",
    "corpus",
    "parser_id",
    "python_version",
    "platform_string",
    "git_commit",
    "cpu_physical_cores",
    "cuda_version",
    "cuda_driver_version",
    "cuda_device_name",
    "parser_package_version",
    "parser_package_source_sha256",
    "parser_model_version",
    "parser_model_artifact_sha256",
    "adapter_source_sha256",
    "model_cache_path",
    "normalization_version",
    "parser_execution_contract_version",
    "parser_execution_contract_config_sha256",
    "smoke_spec_config_sha256",
    "network_egress_blocked",
    "pair_started_at",
    "pair_finished_at",
    "wall_clock_seconds",
    "cpu_seconds_user",
    "cpu_seconds_system",
    "peak_rss_bytes",
    "peak_vram_bytes",
    "cuda_events",
    "output_bytes",
    "output_sha256",
    "stdout_bytes",
    "stdout_sha256",
    "stderr_bytes",
    "stderr_sha256",
    "exit_status",
    "defect_reason",
})


INSPECTABLE_REVIEWER_ARTIFACT_FIELDS = frozenset({
    "pair_id",
    "blinded_parser_hash",
    "source_pdf_path",
    "extraction_markdown_path",
    "normalized_markdown_path",
    "mapping_reference",
    "questions_populated",  # boolean projection, not the questions themselves
})


# Fields the smoke spec explicitly prohibits appearing on the review
# surface. If any is found in an input dict, ``project`` raises rather
# than silently dropping — a prohibited field's presence is itself
# evidence of a harness leak upstream.

PROHIBITED_FIELDS = frozenset({
    # Semantic content indicators.
    "raw_markdown",
    "raw_output",
    "raw_output_text",
    "normalized_markdown",
    "normalized_output_text",
    "markdown_content",
    # AksharaMD scoring / warning / detector output.
    "aksharamd_readiness_score",
    "aksharamd_severity_band",
    "aksharamd_warning_list",
    "readiness_score",
    "severity_band",
    "warning_codes",
    "warnings",
    "W_DROPPED_CONTENT",
    "W_GIBBERISH",
    "W_PLACEHOLDER_STUB",
    "W_ENCODING_ARTIFACTS",
    "W_TABLE_MISSING",
    "W_MULTICOLUMN_ORDER",
    "W_HEADER_FOOTER_TABLE_GARBLED",
    # Ground-truth overlap metrics.
    "pmc_word_overlap_ratio",
    "doclaynet_region_agreement",
    "cuad_span_presence",
    "ground_truth_overlap",
    # Reviewer answers.
    "q1_answer",
    "q2_answer",
    "q3_answer",
    "q1_coverage_answer",
    "q2_fidelity_answer",
    "q3_downstream_usability_answer",
    "severity_label",
    "derived_label",
    # Cross-parser comparison.
    "parser_quality_comparison",
    "cross_parser_ranking",
})


class ProhibitedFieldError(RuntimeError):
    """The input dict contains a field that must not appear on the
    review surface. Presence is a harness-level defect."""


def _reject_prohibited(d: dict[str, Any], where: str) -> None:
    for key in d:
        if key in PROHIBITED_FIELDS:
            raise ProhibitedFieldError(
                f"prohibited field {key!r} present in {where}"
            )


def project_execution_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return only allowlisted execution-record fields."""
    _reject_prohibited(record, "execution_record")
    return {k: v for k, v in record.items() if k in INSPECTABLE_EXECUTION_RECORD_FIELDS}


def project_reviewer_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return only allowlisted reviewer-artifact fields. ``questions``
    itself is *replaced* by a boolean ``questions_populated`` so the
    reviewer surface confirms that the Q1/Q2/Q3 dict is present
    without exposing the wording. Similarly, ``provenance`` is not
    passed through: it may hold parser-identity strings depending on
    caller misuse."""
    _reject_prohibited(artifact, "reviewer_artifact")
    projection: dict[str, Any] = {}
    for k in INSPECTABLE_REVIEWER_ARTIFACT_FIELDS:
        if k == "questions_populated":
            projection[k] = bool(artifact.get("questions"))
        elif k in artifact:
            projection[k] = artifact[k]
    return projection


def project_pair(
    *,
    execution_record: dict[str, Any],
    reviewer_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    """Combined projection used by the smoke review script."""
    result = {
        "execution_record": project_execution_record(execution_record),
    }
    if reviewer_artifact is not None:
        result["reviewer_artifact"] = project_reviewer_artifact(reviewer_artifact)
    return result
