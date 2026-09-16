"""Blinded reviewer artifact construction — smoke wrapper.

The B1a-7b.2 smoke does not emit labels. It exercises artifact
construction only. Q1/Q2/Q3 stay unset; the mapping reference is the
frozen ``appendix_b_v1::v1``.

This module wraps ``benchmarks.eval_v1.adjudication.prepare_reviewer_artifact``
so the smoke does not import an alternative code path for reviewer
artifacts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.eval_v1 import adjudication as _adj

# The mapping reference the smoke uses. Kept as a module constant so
# the harness fails loudly if the underlying mapping file version
# drifts unexpectedly.
SMOKE_MAPPING_REFERENCE = "appendix_b_v1::v1"


@dataclass(frozen=True)
class _SmokeMapping:
    """Minimal shape ``prepare_reviewer_artifact`` reads from a
    SeverityMapper: it uses only ``.mapping_id`` and ``.version`` for
    the artifact's ``mapping_reference`` field. The smoke never
    invokes the real severity mapping (it emits no labels), so a
    frozen shim is sufficient and keeps the smoke off the
    label-emitting code path."""

    mapping_id: str = "appendix_b_v1"
    version: str = "v1"


class ReviewerArtifactBlindingError(RuntimeError):
    """Blinding invariant violated in a constructed artifact."""


def build_smoke_reviewer_artifact(
    *,
    canonical_id: str,
    parser_id: str,
    source_pdf_path: Path,
    extraction_markdown_path: Path,
    normalized_markdown_path: Path,
    extra_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the blinded artifact for one smoke pair.

    The returned dict is exactly what would be written to
    ``analysis_record.json`` under the ``reviewer_artifact`` key. It
    does NOT contain answers to Q1/Q2/Q3; those remain absent (no
    label is emitted by the smoke).

    Raises ReviewerArtifactBlindingError if the constructed artifact
    would leak parser identity in a field that is meant to be blinded.
    """
    artifact = _adj.prepare_reviewer_artifact(
        doc_id=canonical_id,
        parser=parser_id,
        source_pdf_path=str(source_pdf_path),
        extraction_markdown_path=str(extraction_markdown_path),
        normalized_markdown_path=str(normalized_markdown_path),
        mapping=_SmokeMapping(),  # type: ignore[arg-type]
        extra_provenance={
            "smoke_spec_version": "v1",
            "smoke_no_labels_emitted": True,
            **(extra_provenance or {}),
        },
    )
    d = artifact.to_dict()

    # Blinding invariants.
    expected_hash = _adj._blind_parser(parser_id)
    if d["blinded_parser_hash"] != expected_hash:
        raise ReviewerArtifactBlindingError(
            f"blinded_parser_hash={d['blinded_parser_hash']!r} does not match "
            f"sha256(parser_id)[:16]={expected_hash!r}"
        )
    if len(d["blinded_parser_hash"]) != 16:
        raise ReviewerArtifactBlindingError(
            "blinded_parser_hash must be 16 hex chars"
        )
    # No parser-identity string should appear in the artifact payload.
    identity_strings = {
        "marker",
        "docling",
        "markitdown",
        "aksharamd",
        "aksharamd-reference",
        "pymupdf",
        "pymupdf4llm",
    }
    payload = json.dumps(d).lower()
    # The blinded_parser_hash MAY coincidentally contain a substring
    # that looks like one of these names, but hex chars can't spell
    # them — so any occurrence is proof of leakage.
    for name in identity_strings:
        if name in payload:
            raise ReviewerArtifactBlindingError(
                f"reviewer artifact contains parser-identity string {name!r}: "
                f"{payload[:200]!r}"
            )
    # No label fields.
    for label_key in ("q1_answer", "q2_answer", "q3_answer",
                      "q1_coverage_answer", "q2_fidelity_answer",
                      "q3_downstream_usability_answer",
                      "severity_label", "derived_label"):
        if label_key in d:
            raise ReviewerArtifactBlindingError(
                f"reviewer artifact contains prohibited label field {label_key!r}"
            )
    return d
