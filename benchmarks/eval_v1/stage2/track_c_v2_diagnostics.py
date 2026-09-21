"""Exploratory, no-network Track C V2 source/candidate diagnostics.

This tool is deliberately outside the frozen V1 analysis.  It reads V1
artifacts without modifying them, evaluates the exact source/candidate bytes
with :func:`aksharamd.assessment.assess_source_candidate`, and writes a
separate, versioned evidence tree.

The ranking signal used below is *only* the activated
``source.pdf_text_token_retention`` detector score.  The source/candidate API
intentionally has no combined scalar, and this module does not invent one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, cast

from aksharamd.assessment import CandidateArtifact, SourceArtifact, assess_source_candidate
from aksharamd.assessment.source_candidate import (
    SOURCE_CANDIDATE_IMPLEMENTATION_VERSION,
    SOURCE_CANDIDATE_POLICY_ID,
    SOURCE_CANDIDATE_SCHEMA_VERSION,
)

DIAGNOSTIC_SCHEMA_VERSION = "track-c-v2-diagnostics-2"
DIAGNOSTIC_CONTRACT_ID = "track-c-source-candidate-diagnostics-v2-exploratory-2"
SOURCE_RESOLUTION_CONTRACT_ID = "track-c-canonical-cache-layout-v1"
ROOT = Path(__file__).resolve().parents[3]
FROZEN_IDENTITY_MANIFEST_PATH = (
    ROOT / "docs" / "evaluation" / "TRACK_C_V1_FROZEN_IDENTITY_MANIFEST.json"
)
FROZEN_IDENTITY_MANIFEST_SHA256 = (
    "c9649a4169c72aaeb85677b39e5419e686abe3256d0ade7c1567274a5714974d"
)
V1_RESULT_SCHEMA_VERSION = "3"
V1_METRIC_SCHEMA_VERSION = "3"
V1_SCORING_CONTRACT_ID = "markdown_only_v1"
V1_PROMPT_SHA256 = "2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601"
V1_MODEL_ID = "claude-sonnet-4-6"
V1_STAGE1_EXECUTION_MANIFEST_SHA256 = (
    "4b5116f4a965f2b4e653705827d3f0a4115b2b4b4e3a1b2af4f7e234bd26b3db"
)
V1_PARSER_CONTRACT_CONFIG_SHA256 = (
    "a0ca496e562cee200c393c146c7ed16efee9b01ee2f94ebdd4e623c936f9baa4"
)
TEXT_DETECTOR_ID = "source.pdf_text_token_retention"
DEFAULT_THRESHOLDS = (80, 90, 95)
DEFAULT_BOOTSTRAP = 2_000
DEFAULT_BAD_REGRET_MARGIN = 0.05
PARSER_ARMS = frozenset({"aksharamd-reference", "marker", "docling", "markitdown"})
KNOWN_ARMS = PARSER_ARMS | {"corpus_gold"}
SHA256_ZERO = "0" * 64


class EvidenceValidationError(ValueError):
    """A V1 input could not be safely reused."""


class IneligibleV1Record(EvidenceValidationError):
    """A declared non-executed V1 arm is reported but not assessed."""


@dataclass(frozen=True)
class SourceBinding:
    path: Path
    data: bytes
    sha256: str


@dataclass(frozen=True)
class ValidPair:
    corpus: str
    canonical_id: str
    parser_id: str
    pair_dir: Path
    result_path: Path
    result: dict[str, Any]
    result_sha256: str
    candidate_path: Path
    candidate_data: bytes
    candidate_sha256: str
    candidate_phase2_sha256: str
    execution_path: Path | None
    execution_sha256: str | None
    source: SourceBinding


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json_object(path: Path) -> tuple[dict[str, Any], bytes, str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceValidationError("JSON root must be an object")
    return value, raw, _sha256(raw)


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return _sha256(raw)


def _load_frozen_identity_manifest(
    path: Path = FROZEN_IDENTITY_MANIFEST_PATH,
) -> frozenset[tuple[str, str, str, str]]:
    payload, _, _ = _load_json_object(path)
    actual_hash = _canonical_json_sha256(payload)
    if actual_hash != FROZEN_IDENTITY_MANIFEST_SHA256:
        raise EvidenceValidationError(
            "frozen identity manifest hash mismatch: "
            f"expected {FROZEN_IDENTITY_MANIFEST_SHA256}, got {actual_hash}"
        )
    expected_metadata = {
        "schema_version": "track-c-v1-frozen-identity-manifest-1",
        "stage1_execution_manifest_sha256": V1_STAGE1_EXECUTION_MANIFEST_SHA256,
        "v1_result_schema_version": V1_RESULT_SCHEMA_VERSION,
        "v1_metric_schema_version": V1_METRIC_SCHEMA_VERSION,
        "v1_scoring_contract_id": V1_SCORING_CONTRACT_ID,
    }
    for field, expected in expected_metadata.items():
        if payload.get(field) != expected:
            raise EvidenceValidationError(f"frozen identity manifest {field} mismatch")
    identities = _require_exact(payload.get("identities"), list, "manifest identities")
    parsed: list[tuple[str, str, str, str]] = []
    required_fields = {"corpus", "canonical_id", "parser_id", "execution_status"}
    for index, row in enumerate(identities):
        if not isinstance(row, dict) or set(row) != required_fields:
            raise EvidenceValidationError(
                f"manifest identities[{index}] must contain exactly {sorted(required_fields)!r}"
            )
        corpus = _require_exact(row["corpus"], str, "manifest corpus")
        canonical_id = _require_exact(row["canonical_id"], str, "manifest canonical_id")
        parser_id = _require_exact(row["parser_id"], str, "manifest parser_id")
        status = _require_exact(row["execution_status"], str, "manifest execution_status")
        if corpus not in {"qasper", "tat_dqa"} or parser_id not in KNOWN_ARMS:
            raise EvidenceValidationError(f"unsupported manifest identity at index {index}")
        if status not in {"EXECUTED", "DEFECT"}:
            raise EvidenceValidationError(f"unsupported manifest status at index {index}")
        parsed.append((corpus, canonical_id, parser_id, status))
    inventory = frozenset(parsed)
    if len(parsed) != 245 or len(inventory) != 245:
        raise EvidenceValidationError("frozen identity manifest must contain 245 unique identities")
    if sum(identity[3] == "EXECUTED" for identity in inventory) != 239:
        raise EvidenceValidationError("frozen identity manifest must contain 239 EXECUTED identities")
    if sum(identity[3] == "DEFECT" for identity in inventory) != 6:
        raise EvidenceValidationError("frozen identity manifest must contain six DEFECT identities")
    return inventory


def _inventory_difference(
    expected: frozenset[tuple[str, str, str, str]],
    observed: set[tuple[str, str, str, str]],
    discovered_count: int,
) -> dict[str, Any] | None:
    """Describe any exact-set/count mismatch, including duplicate discovery."""
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    duplicate_count = discovered_count - len(observed)
    if not missing and not unexpected and duplicate_count == 0:
        return None
    return {
        "expected_count": len(expected),
        "discovered_count": discovered_count,
        "validated_unique_count": len(observed),
        "duplicate_or_unvalidated_count": duplicate_count,
        "missing": missing,
        "unexpected": unexpected,
    }


def _require_exact(value: Any, expected_type: type, name: str) -> Any:
    if type(value) is not expected_type:
        raise EvidenceValidationError(f"{name} must be {expected_type.__name__}")
    return value


def _require_sha(value: Any, name: str, *, allow_zero: bool = False) -> str:
    value = _require_exact(value, str, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise EvidenceValidationError(f"{name} must be a lowercase SHA-256")
    if not allow_zero and value == SHA256_ZERO:
        raise EvidenceValidationError(f"{name} cannot be the all-zero placeholder")
    return value


def _require_finite_number(value: Any, name: str) -> float:
    if type(value) not in {int, float}:
        raise EvidenceValidationError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceValidationError(f"{name} must be finite")
    return number


def _resolve_source(cache_root: Path, corpus: str, canonical_id: str) -> SourceBinding:
    if not canonical_id or any(token in canonical_id for token in ("/", "\\", "..")):
        raise EvidenceValidationError("canonical_id is unsafe for source resolution")
    if corpus == "qasper":
        paths = [
            path
            for path in sorted((cache_root / "qasper").glob(f"{canonical_id}-????????????.pdf"))
            if all(char in "0123456789abcdef" for char in path.stem[-12:])
        ]
        if len(paths) != 1:
            raise EvidenceValidationError(
                f"expected one QASPER source for {canonical_id}, found {len(paths)}"
            )
        path = paths[0]
    elif corpus == "tat_dqa":
        path = cache_root / "tat_dqa" / "tat_docs" / "dev" / f"{canonical_id}.pdf"
        if not path.is_file():
            raise EvidenceValidationError(f"TAT-DQA source is missing: {path}")
    else:
        raise EvidenceValidationError(f"unsupported corpus: {corpus!r}")
    data = path.read_bytes()
    if not data:
        raise EvidenceValidationError(f"source is empty: {path}")
    return SourceBinding(path=path.resolve(), data=data, sha256=_sha256(data))


def _validate_qa_cache(record: dict[str, Any]) -> None:
    n_pairs = _require_exact(record.get("n_qa_pairs"), int, "n_qa_pairs")
    n_answered = _require_exact(record.get("n_answered"), int, "n_answered")
    n_errors = _require_exact(record.get("n_llm_errors"), int, "n_llm_errors")
    if n_pairs < 0 or n_answered < 0 or n_errors != 0:
        raise EvidenceValidationError("invalid cached QA counts or nonzero LLM errors")
    rows = _require_exact(record.get("qa_results"), list, "qa_results")
    if len(rows) != n_pairs:
        raise EvidenceValidationError("qa_results length does not match n_qa_pairs")
    answered = 0
    em_values: list[float] = []
    primary_values: list[float] = []
    seen_ids: set[int | str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise EvidenceValidationError(f"qa_results[{index}] must be an object")
        status = row.get("status")
        if status not in {"answered", "no_gold"}:
            raise EvidenceValidationError(f"qa_results[{index}] has non-reusable status {status!r}")
        raw_question_id = row.get("question_id")
        if type(raw_question_id) is int:
            question_id: int | str = int(raw_question_id)
        elif type(raw_question_id) is str:
            question_id = str(raw_question_id)
        else:
            raise EvidenceValidationError("question_id values must be unique strings or integers")
        if question_id in seen_ids:
            raise EvidenceValidationError("question_id values must be unique strings or integers")
        seen_ids.add(question_id)
        if status == "answered":
            answered += 1
            prediction = _require_exact(row.get("prediction"), str, "prediction")
            expected = _require_sha(row.get("prediction_sha256"), "prediction_sha256")
            if _sha256(prediction.encode("utf-8")) != expected:
                raise EvidenceValidationError("prediction_sha256 mismatch")
            em_value = _require_finite_number(row.get("em_score"), "qa em_score")
            primary_value = _require_finite_number(row.get("primary_score"), "qa primary_score")
            if not 0 <= em_value <= 1 or not 0 <= primary_value <= 1:
                raise EvidenceValidationError("cached QA row scores must be between 0 and 1")
            em_values.append(em_value)
            primary_values.append(primary_value)
    if answered != n_answered:
        raise EvidenceValidationError("answered QA row count does not match n_answered")
    aggregate_em = _require_finite_number(record.get("em_score"), "em_score")
    aggregate_primary = _require_finite_number(record.get("primary_score"), "primary_score")
    if not 0 <= aggregate_em <= 1 or not 0 <= aggregate_primary <= 1:
        raise EvidenceValidationError("aggregate cached QA scores must be between 0 and 1")
    if not answered:
        raise EvidenceValidationError("executed reusable record has no answered QA rows")
    if not math.isclose(aggregate_em, mean(em_values), rel_tol=0.0, abs_tol=1e-12):
        raise EvidenceValidationError("em_score does not equal cached answered-row mean")
    if not math.isclose(aggregate_primary, mean(primary_values), rel_tol=0.0, abs_tol=1e-12):
        raise EvidenceValidationError("primary_score does not equal cached answered-row mean")


def _validate_execution_contract(execution: dict[str, Any]) -> None:
    manifest_sha = _require_sha(
        execution.get("stage1_execution_manifest_sha256"),
        "stage1_execution_manifest_sha256",
    )
    if manifest_sha != V1_STAGE1_EXECUTION_MANIFEST_SHA256:
        raise EvidenceValidationError("stage1_execution_manifest_sha256 mismatch")
    contract_sha = _require_sha(
        execution.get("parser_execution_contract_config_sha256"),
        "parser_execution_contract_config_sha256",
    )
    if contract_sha != V1_PARSER_CONTRACT_CONFIG_SHA256:
        raise EvidenceValidationError("parser_execution_contract_config_sha256 mismatch")


def _validate_frozen_defect(
    record: dict[str, Any], pair_dir: Path, cache_root: Path,
    *, corpus: str, canonical_id: str, parser_id: str,
    frozen_inventory: frozenset[tuple[str, str, str, str]],
) -> None:
    identity = (corpus, canonical_id, parser_id, "DEFECT")
    if identity not in frozen_inventory:
        raise EvidenceValidationError(f"unexpected V1 DEFECT identity: {identity!r}")
    expected_values: dict[str, object] = {
        "n_answered": 0,
        "n_llm_errors": 0,
        "em_score": None,
        "primary_score": None,
        "primary_metric": None,
        "readiness_score": None,
        "input_sha256": None,
        "llm_evaluated": False,
        "qa_results": [],
    }
    for field, expected_value in expected_values.items():
        if record.get(field) != expected_value:
            raise EvidenceValidationError(
                f"DEFECT record {field} must equal {expected_value!r}"
            )
    n_pairs = _require_exact(record.get("n_qa_pairs"), int, "DEFECT n_qa_pairs")
    if n_pairs < 0:
        raise EvidenceValidationError("DEFECT n_qa_pairs must be nonnegative")
    if (pair_dir / "parser_output.md").exists():
        raise EvidenceValidationError("DEFECT record unexpectedly has parser_output.md")
    execution_path = pair_dir / "execution_record.json"
    if not execution_path.is_file():
        raise EvidenceValidationError("DEFECT execution_record.json is missing")
    execution, _, _ = _load_json_object(execution_path)
    for field, expected_value in (
        ("corpus", corpus), ("canonical_id", canonical_id),
        ("parser_id", parser_id), ("exit_status", "DEFECT"),
    ):
        if execution.get(field) != expected_value:
            raise EvidenceValidationError(f"DEFECT execution {field} mismatch")
    reason = _require_exact(execution.get("defect_reason"), str, "defect_reason")
    if not reason:
        raise EvidenceValidationError("defect_reason must not be empty")
    if _require_exact(execution.get("output_bytes"), int, "DEFECT output_bytes") != 0:
        raise EvidenceValidationError("DEFECT output_bytes must be zero")
    if _require_sha(execution.get("output_sha256"), "DEFECT output_sha256") != _sha256(b""):
        raise EvidenceValidationError("DEFECT output_sha256 must bind empty output")
    _validate_execution_contract(execution)
    _resolve_source(cache_root, corpus, canonical_id)


def _validate_pair(
    result_path: Path,
    run_dir: Path,
    cache_root: Path,
    frozen_inventory: frozenset[tuple[str, str, str, str]] | None,
) -> ValidPair:
    record, _, record_sha = _load_json_object(result_path)
    for name, expected in (
        ("schema_version", V1_RESULT_SCHEMA_VERSION),
        ("metric_schema_version", V1_METRIC_SCHEMA_VERSION),
        ("scoring_contract_id", V1_SCORING_CONTRACT_ID),
    ):
        if record.get(name) != expected:
            raise EvidenceValidationError(f"{name} must equal {expected!r}")
    corpus = _require_exact(record.get("corpus"), str, "corpus")
    canonical_id = _require_exact(record.get("canonical_id"), str, "canonical_id")
    parser_id = _require_exact(record.get("parser_id"), str, "parser_id")
    if parser_id not in KNOWN_ARMS:
        raise EvidenceValidationError(f"unknown parser arm: {parser_id!r}")
    if _require_sha(record.get("prompt_sha256"), "prompt_sha256") != V1_PROMPT_SHA256:
        raise EvidenceValidationError("prompt_sha256 does not match frozen V1 prompt")
    if record.get("model") != V1_MODEL_ID:
        raise EvidenceValidationError("model does not match frozen V1 model")
    pair_dir = result_path.parent
    try:
        relative = pair_dir.relative_to(run_dir)
    except ValueError as exc:
        raise EvidenceValidationError("result is outside run_dir") from exc
    if relative.parts != (corpus, canonical_id, parser_id):
        raise EvidenceValidationError("result path does not match record identity")
    execution_status = record.get("execution_status")
    if execution_status == "DEFECT":
        if frozen_inventory is None:
            raise EvidenceValidationError("DEFECT validation requires the frozen identity manifest")
        _validate_frozen_defect(
            record, pair_dir, cache_root,
            corpus=corpus, canonical_id=canonical_id, parser_id=parser_id,
            frozen_inventory=frozen_inventory,
        )
        raise IneligibleV1Record("validated frozen V1 execution_status=DEFECT")
    if execution_status != "EXECUTED":
        raise EvidenceValidationError("execution_status must be EXECUTED or declared DEFECT")
    if record.get("llm_evaluated") is not True:
        raise EvidenceValidationError("llm_evaluated must be true")
    expected_metric = "token_f1" if corpus == "qasper" else "numeric_em"
    if record.get("primary_metric") != expected_metric:
        raise EvidenceValidationError("primary_metric does not match corpus contract")
    _validate_qa_cache(record)

    candidate_path = pair_dir / "parser_output.md"
    if not candidate_path.is_file():
        raise EvidenceValidationError("parser_output.md is missing")
    candidate_data = candidate_path.read_bytes()
    candidate_sha = _sha256(candidate_data)
    # Phase 2 hashed universal-newline text. Accept only that exact historical
    # transformation, then independently bind the V2 assessment to raw bytes.
    try:
        phase2_bytes = candidate_path.read_text(encoding="utf-8").encode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceValidationError("parser_output.md is not UTF-8") from exc
    phase2_sha = _sha256(phase2_bytes)
    if phase2_sha != _require_sha(record.get("input_sha256"), "input_sha256"):
        raise EvidenceValidationError("input_sha256 mismatch")

    receipt_path = pair_dir / "execution_record.json"
    execution_path: Path | None = receipt_path
    execution_sha: str | None = None
    if parser_id == "corpus_gold":
        # This is a virtual annotation-derived ceiling arm, not a parser run.
        if receipt_path.exists():
            raise EvidenceValidationError("corpus_gold unexpectedly has an execution receipt")
        execution_path = None
    else:
        if not receipt_path.is_file():
            raise EvidenceValidationError("execution_record.json is missing")
        execution, _, execution_sha = _load_json_object(receipt_path)
        for field, expected in (
            ("corpus", corpus), ("canonical_id", canonical_id),
            ("parser_id", parser_id), ("exit_status", "EXECUTED"),
        ):
            if execution.get(field) != expected:
                raise EvidenceValidationError(f"execution {field} mismatch")
        # The V1 runner hashed the adapter's LF text before Path.write_text
        # serialized it on Windows. Phase 2's universal-newline read recovers
        # those logical bytes, while V2 separately binds the exact disk bytes.
        if _require_exact(execution.get("output_bytes"), int, "execution output_bytes") != len(phase2_bytes):
            raise EvidenceValidationError("execution output_bytes mismatch")
        if _require_sha(execution.get("output_sha256"), "execution output_sha256") != phase2_sha:
            raise EvidenceValidationError("execution output_sha256 mismatch")
        _validate_execution_contract(execution)

    source = _resolve_source(cache_root, corpus, canonical_id)
    return ValidPair(
        corpus=corpus, canonical_id=canonical_id, parser_id=parser_id,
        pair_dir=pair_dir, result_path=result_path, result=record,
        result_sha256=record_sha, candidate_path=candidate_path,
        candidate_data=candidate_data, candidate_sha256=candidate_sha,
        candidate_phase2_sha256=phase2_sha,
        execution_path=execution_path, execution_sha256=execution_sha,
        source=source,
    )


def discover_and_validate(
    run_dir: Path,
    cache_root: Path,
    frozen_inventory: frozenset[tuple[str, str, str, str]] | None = None,
) -> tuple[list[ValidPair], list[dict[str, str]], int]:
    """Return valid reusable records, exclusions, and discovered record count."""
    run_dir = run_dir.resolve()
    candidates = sorted(run_dir.rglob("track_c_result.json")) if run_dir.is_dir() else []
    valid: list[ValidPair] = []
    exclusions: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in candidates:
        try:
            pair = _validate_pair(
                path.resolve(), run_dir, cache_root.resolve(), frozen_inventory,
            )
            key = (pair.corpus, pair.canonical_id, pair.parser_id)
            if key in seen:
                raise EvidenceValidationError("duplicate corpus/document/parser identity")
            seen.add(key)
            valid.append(pair)
        except (EvidenceValidationError, OSError) as exc:
            exclusion = {
                "path": str(path.resolve()),
                "category": (
                    "v1_ineligible" if isinstance(exc, IneligibleV1Record)
                    else "validation_failure"
                ),
                "reason": str(exc),
            }
            if isinstance(exc, IneligibleV1Record):
                parts = path.parent.relative_to(run_dir).parts
                exclusion["identity"] = "/".join(parts)
            exclusions.append(exclusion)
    valid.sort(key=lambda pair: (pair.corpus, pair.canonical_id, pair.parser_id))
    exclusions.sort(key=lambda item: (item["path"], item["reason"]))
    return valid, exclusions, len(candidates)


def _assessment_sidecar(pair: ValidPair) -> dict[str, Any]:
    source = SourceArtifact(
        content_hash=pair.source.sha256,
        byte_size=len(pair.source.data),
        media_type="application/pdf",
        logical_id=f"{pair.corpus}/{pair.canonical_id}",
        storage_reference=str(pair.source.path),
        data=pair.source.data,
    )
    candidate = CandidateArtifact(
        content_hash=pair.candidate_sha256,
        byte_size=len(pair.candidate_data),
        media_type="text/markdown",
        logical_id=f"{pair.corpus}/{pair.canonical_id}/{pair.parser_id}",
        storage_reference=str(pair.candidate_path.resolve()),
        data=pair.candidate_data,
        parser_name=pair.parser_id,
        original_source_hash=pair.source.sha256,
    )
    assessment = assess_source_candidate(source=source, candidate=candidate)
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "contract_id": DIAGNOSTIC_CONTRACT_ID,
        "exploratory": True,
        "frozen_v1_unchanged": True,
        "identity": {
            "corpus": pair.corpus,
            "canonical_id": pair.canonical_id,
            "parser_id": pair.parser_id,
            "arm_kind": "corpus_gold" if pair.parser_id == "corpus_gold" else "parser",
        },
        "input_bindings": {
            "source_sha256": pair.source.sha256,
            "source_path": str(pair.source.path),
            "source_resolution_contract_id": SOURCE_RESOLUTION_CONTRACT_ID,
            "source_binding_limitation": (
                "V1 receipts did not store source PDF hashes; this binds the canonical cache artifact "
                "resolved at V2 execution time."
            ),
            "candidate_sha256": pair.candidate_sha256,
            "candidate_phase2_canonical_sha256": pair.candidate_phase2_sha256,
            "candidate_path": str(pair.candidate_path.resolve()),
            "v1_result_sha256": pair.result_sha256,
            "v1_result_path": str(pair.result_path.resolve()),
            "execution_record_sha256": pair.execution_sha256,
            "execution_record_path": (
                str(pair.execution_path.resolve()) if pair.execution_path is not None else None
            ),
        },
        "cached_qa_metrics": {
            "metric_schema_version": pair.result["metric_schema_version"],
            "prompt_sha256": pair.result["prompt_sha256"],
            "model": pair.result["model"],
            "n_qa_pairs": pair.result["n_qa_pairs"],
            "n_answered": pair.result["n_answered"],
            "n_llm_errors": pair.result["n_llm_errors"],
            "em_score": pair.result["em_score"],
            "primary_score": pair.result["primary_score"],
            "primary_metric": pair.result["primary_metric"],
        },
        "assessment": assessment.model_dump(mode="json"),
    }


def _text_score(sidecar: dict[str, Any]) -> float | None:
    detectors = sidecar["assessment"]["source_comparison"]["detectors"]
    detector = next((item for item in detectors if item["detector_id"] == TEXT_DETECTOR_ID), None)
    if detector is None or detector["status"] != "activated":
        return None
    return float(detector["score"])


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    result = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return round(result, 6)


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    numbers = list(values)
    return {
        "n": len(numbers),
        "min": min(numbers) if numbers else None,
        "p25": _percentile(numbers, 0.25),
        "median": _percentile(numbers, 0.5),
        "p75": _percentile(numbers, 0.75),
        "max": max(numbers) if numbers else None,
        "mean": round(mean(numbers), 6) if numbers else None,
        "distinct_values": len(set(numbers)),
    }


def _detector_summary(sidecars: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Counter[str]] = defaultdict(Counter)
    scores: dict[str, list[float]] = defaultdict(list)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for sidecar in sidecars:
        assessment = sidecar["assessment"]
        for group_name in ("candidate_intrinsic", "source_comparison"):
            for detector in assessment[group_name]["detectors"]:
                detector_id = detector["detector_id"]
                summary[detector_id]["total"] += 1
                summary[detector_id]["eligible"] += int(detector["eligible"])
                summary[detector_id][detector["status"]] += 1
                summary[detector_id][f"verdict_{detector['verdict']}"] += 1
                if detector["score"] is not None:
                    scores[detector_id].append(float(detector["score"]))
                if detector["abstention_reason"]:
                    reasons[detector_id][detector["abstention_reason"]] += 1
    return {
        detector_id: {
            **dict(sorted(counts.items())),
            "score_distribution": _distribution(scores[detector_id]),
            "abstention_reasons": dict(sorted(reasons[detector_id].items())),
        }
        for detector_id, counts in sorted(summary.items())
    }


def _usable_rows(sidecars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sidecar in sidecars:
        identity = sidecar["identity"]
        if identity["parser_id"] not in PARSER_ARMS:
            continue
        score = _text_score(sidecar)
        qa = sidecar["cached_qa_metrics"]["primary_score"]
        if score is None or qa is None:
            continue
        rows.append({**identity, "evidence_score": score, "qa_score": float(qa)})
    return rows


def _document_bundles(
    sidecars: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, list[dict[str, Any]]]]:
    """Group every cached QA arm and every eligible parser output by document."""
    bundles: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"oracle_rows": [], "eligible_rows": []}
    )
    for sidecar in sidecars:
        identity = sidecar["identity"]
        key = (identity["corpus"], identity["canonical_id"])
        bundle = bundles[key]
        qa = sidecar["cached_qa_metrics"].get("primary_score")
        if qa is None:
            continue
        row = {
            **identity,
            "qa_score": float(qa),
            "evidence_score": _text_score(sidecar),
        }
        bundle["oracle_rows"].append(row)
        if identity["parser_id"] in PARSER_ARMS:
            bundle["eligible_rows"].append(row)
    for bundle in bundles.values():
        bundle["oracle_rows"].sort(key=lambda row: row["parser_id"])
        bundle["eligible_rows"].sort(key=lambda row: row["parser_id"])
    return dict(sorted(bundles.items()))


def _comparison_documents(
    sidecars: list[dict[str, Any]], *, require_scorable: bool,
) -> tuple[dict[tuple[str, str], dict[str, list[dict[str, Any]]]], dict[str, Any]]:
    bundles = _document_bundles(sidecars)
    included: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    lost: list[dict[str, Any]] = []
    for (corpus, canonical_id), bundle in bundles.items():
        reasons: list[str] = []
        if not bundle["oracle_rows"]:
            reasons.append("no_cached_qa_oracle_arm")
        if not bundle["eligible_rows"]:
            reasons.append("no_eligible_parser_output")
        if require_scorable and not any(
            row["evidence_score"] is not None for row in bundle["eligible_rows"]
        ):
            reasons.append("no_scorable_parser_output")
        if reasons:
            lost.append({
                "corpus": corpus,
                "canonical_id": canonical_id,
                "reasons": reasons,
            })
        else:
            included[(corpus, canonical_id)] = bundle
    reason_counts = Counter(reason for item in lost for reason in item["reasons"])
    return included, {
        "n_documents_observed": len(bundles),
        "n_documents_included": len(included),
        "n_documents_lost": len(lost),
        "lost_reason_counts": dict(sorted(reason_counts.items())),
        "lost_documents": lost,
    }


def _by_document(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[(row["corpus"], row["canonical_id"])].append(row)
    for values in result.values():
        values.sort(key=lambda row: row["parser_id"])
    return dict(sorted(result.items()))


def _pairwise_counts(documents: dict[tuple[str, str], list[dict[str, Any]]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for rows in documents.values():
        for left_index, left in enumerate(rows):
            for right in rows[left_index + 1:]:
                counts["total"] += 1
                evidence_delta = left["evidence_score"] - right["evidence_score"]
                qa_delta = left["qa_score"] - right["qa_score"]
                if evidence_delta == 0:
                    counts["evidence_ties"] += 1
                    continue
                if qa_delta == 0:
                    counts["qa_ties"] += 1
                    continue
                counts["comparable"] += 1
                if evidence_delta * qa_delta > 0:
                    counts["concordant"] += 1
                else:
                    counts["discordant"] += 1
    return counts


def _bootstrap_documents(
    documents: dict[tuple[str, str], Any],
    statistic,
    *,
    n_bootstrap: int,
    seed: int,
) -> tuple[float | None, float | None]:
    keys = sorted(documents)
    if len(keys) < 2 or n_bootstrap <= 0:
        return None, None
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(n_bootstrap):
        sample = [documents[rng.choice(keys)] for _ in keys]
        value = statistic(sample)
        if value is not None and math.isfinite(value):
            values.append(value)
    return _percentile(values, 0.025), _percentile(values, 0.975)


def _concordance(sidecars: list[dict[str, Any]], n_bootstrap: int) -> dict[str, Any]:
    documents = _by_document(_usable_rows(sidecars))
    counts = _pairwise_counts(documents)
    rate = counts["concordant"] / counts["comparable"] if counts["comparable"] else None
    tie_rate = counts["evidence_ties"] / counts["total"] if counts["total"] else None

    def statistic(sample: list[list[dict[str, Any]]]) -> float | None:
        synthetic = {("sample", str(index)): rows for index, rows in enumerate(sample)}
        sample_counts = _pairwise_counts(synthetic)
        if not sample_counts["comparable"]:
            return None
        return sample_counts["concordant"] / sample_counts["comparable"]

    ci_low, ci_high = _bootstrap_documents(
        documents, statistic, n_bootstrap=n_bootstrap, seed=1729,
    )
    return {
        "definition": (
            "Within-document unordered parser pairs; evidence and QA ties are excluded from "
            "concordance, while evidence ties remain in tie_rate."
        ),
        "n_documents": len(documents),
        **dict(sorted(counts.items())),
        "tie_rate": round(tie_rate, 6) if tie_rate is not None else None,
        "concordance": round(rate, 6) if rate is not None else None,
        "document_bootstrap_95_ci": [ci_low, ci_high],
        "n_bootstrap": n_bootstrap,
    }


def _selection(sidecars: list[dict[str, Any]], n_bootstrap: int) -> dict[str, Any]:
    documents, accounting = _comparison_documents(sidecars, require_scorable=True)

    def document_metrics(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        oracle = max(row["qa_score"] for row in bundle["oracle_rows"])
        scorable = [
            row for row in bundle["eligible_rows"] if row["evidence_score"] is not None
        ]
        selected = min(
            scorable, key=lambda row: (-float(row["evidence_score"]), row["parser_id"]),
        )
        random_regret = mean(oracle - row["qa_score"] for row in bundle["eligible_rows"])
        fixed_regret = {
            row["parser_id"]: oracle - row["qa_score"]
            for row in bundle["eligible_rows"]
        }
        fixed_correct = {
            row["parser_id"]: float(math.isclose(row["qa_score"], oracle, abs_tol=1e-12))
            for row in bundle["eligible_rows"]
        }
        return {
            "selector_regret": oracle - selected["qa_score"],
            "selector_correct": float(math.isclose(selected["qa_score"], oracle, abs_tol=1e-12)),
            "random_regret": random_regret,
            "random_accuracy": mean(fixed_correct.values()),
            "fixed_regret": fixed_regret,
            "fixed_correct": fixed_correct,
        }

    selector_regrets: list[float] = []
    selector_correct: list[float] = []
    random_regrets: list[float] = []
    random_accuracy: list[float] = []
    fixed_regrets: dict[str, list[float]] = defaultdict(list)
    fixed_correct: dict[str, list[float]] = defaultdict(list)
    for bundle in documents.values():
        metrics = document_metrics(bundle)
        selector_regrets.append(metrics["selector_regret"])
        selector_correct.append(metrics["selector_correct"])
        random_regrets.append(metrics["random_regret"])
        random_accuracy.append(metrics["random_accuracy"])
        for parser_id, regret in metrics["fixed_regret"].items():
            fixed_regrets[parser_id].append(regret)
        for parser_id, correct in metrics["fixed_correct"].items():
            fixed_correct[parser_id].append(correct)
    eligible_fixed = {
        parser_id: values for parser_id, values in fixed_regrets.items()
        if len(values) == len(documents)
    }
    fixed_means = {parser_id: mean(values) for parser_id, values in eligible_fixed.items()}
    fixed_accuracy = {
        parser_id: mean(fixed_correct[parser_id]) for parser_id in eligible_fixed
    }
    best_fixed = min(fixed_means, key=lambda parser_id: (fixed_means[parser_id], parser_id)) if fixed_means else None

    def statistic(sample: list[dict[str, list[dict[str, Any]]]]) -> float | None:
        return mean(document_metrics(bundle)["selector_regret"] for bundle in sample) if sample else None

    def accuracy_statistic(sample: list[dict[str, list[dict[str, Any]]]]) -> float | None:
        return mean(document_metrics(bundle)["selector_correct"] for bundle in sample) if sample else None

    ci_low, ci_high = _bootstrap_documents(
        documents, statistic, n_bootstrap=n_bootstrap, seed=2718,
    )
    accuracy_ci_low, accuracy_ci_high = _bootstrap_documents(
        documents, accuracy_statistic, n_bootstrap=n_bootstrap, seed=3141,
    )
    return {
        "definition": (
            f"Select max {TEXT_DETECTOR_ID} score; break ties by parser_id. Regret is the "
            "best cached primary_score across every arm (including corpus_gold when present) "
            "minus selected cached primary_score. Random and fixed baselines use eligible parser "
            "arms only and are evaluated on the same documents as the source-evidence selector."
        ),
        "document_accounting": accounting,
        "n_documents": len(documents),
        "source_evidence_selector_mean_regret": (
            round(mean(selector_regrets), 6) if selector_regrets else None
        ),
        "source_evidence_selector_document_bootstrap_95_ci": [ci_low, ci_high],
        "source_evidence_selector_top_one_accuracy": (
            round(mean(selector_correct), 6) if selector_correct else None
        ),
        "source_evidence_selector_accuracy_document_bootstrap_95_ci": [
            accuracy_ci_low, accuracy_ci_high,
        ],
        "best_fixed_parser": best_fixed,
        "best_fixed_parser_mean_regret": (
            round(fixed_means[best_fixed], 6) if best_fixed is not None else None
        ),
        "fixed_parser_mean_regret": {
            key: round(value, 6) for key, value in sorted(fixed_means.items())
        },
        "fixed_parser_top_one_accuracy": {
            key: round(value, 6) for key, value in sorted(fixed_accuracy.items())
        },
        "random_parser_expected_mean_regret": (
            round(mean(random_regrets), 6) if random_regrets else None
        ),
        "random_parser_expected_top_one_accuracy": (
            round(mean(random_accuracy), 6) if random_accuracy else None
        ),
        "oracle_mean_regret": 0.0 if documents else None,
        "oracle_top_one_accuracy": 1.0 if documents else None,
        "baseline_limitation": (
            "Best-fixed is estimated in-sample and is descriptive, not an out-of-sample policy estimate."
        ),
        "n_bootstrap": n_bootstrap,
    }


def _risk_coverage(
    sidecars: list[dict[str, Any]], thresholds: tuple[int, ...], bad_regret_margin: float,
    n_bootstrap: int,
) -> dict[str, Any]:
    documents, accounting = _comparison_documents(sidecars, require_scorable=False)

    def metrics(
        sample: list[dict[str, list[dict[str, Any]]]], threshold: int,
    ) -> dict[str, float | int | None]:
        eligible = sum(len(bundle["eligible_rows"]) for bundle in sample)
        scorable = sum(
            row["evidence_score"] is not None
            for bundle in sample for row in bundle["eligible_rows"]
        )
        accepted: list[tuple[dict[str, Any], float]] = []
        for bundle in sample:
            oracle = max(row["qa_score"] for row in bundle["oracle_rows"])
            accepted.extend(
                (row, oracle - row["qa_score"])
                for row in bundle["eligible_rows"]
                if row["evidence_score"] is not None and row["evidence_score"] >= threshold
            )
        regrets = [regret for _, regret in accepted]
        false_accepts = sum(regret >= bad_regret_margin for regret in regrets)
        return {
            "n_accepted": len(accepted),
            "n_eligible": eligible,
            "n_scorable": scorable,
            "n_abstained": eligible - scorable,
            "coverage": len(accepted) / eligible if eligible else None,
            "conditional_coverage": len(accepted) / scorable if scorable else None,
            "risk": mean(regrets) if regrets else None,
            "false_accepts": false_accepts,
            "false_accept_rate": false_accepts / len(accepted) if accepted else None,
        }

    document_bundles = list(documents.values())
    reports: list[dict[str, Any]] = []
    for threshold in thresholds:
        observed = metrics(document_bundles, threshold)
        cis: dict[str, list[float | None]] = {}
        for offset, metric_name in enumerate(
            ("coverage", "conditional_coverage", "risk", "false_accept_rate"),
        ):
            def statistic(
                sample: list[dict[str, list[dict[str, Any]]]], *, name: str = metric_name,
            ) -> float | None:
                value = metrics(sample, threshold)[name]
                return float(value) if value is not None else None

            low, high = _bootstrap_documents(
                documents, statistic, n_bootstrap=n_bootstrap,
                seed=4000 + threshold * 10 + offset,
            )
            cis[metric_name] = [low, high]
        reports.append({
            "threshold": threshold,
            "n_documents": len(documents),
            "n_accepted": observed["n_accepted"],
            "n_eligible_outputs": observed["n_eligible"],
            "n_rejected_outputs": (
                cast(int, observed["n_eligible"]) - cast(int, observed["n_accepted"])
            ),
            "n_scorable": observed["n_scorable"],
            "n_abstained_rejected": observed["n_abstained"],
            "coverage": (
                round(float(observed["coverage"]), 6)
                if observed["coverage"] is not None else None
            ),
            "coverage_document_bootstrap_95_ci": cis["coverage"],
            "coverage_definition": (
                "accepted / all eligible parser outputs; detector abstentions are rejected"
            ),
            "conditional_on_scorable_coverage": (
                round(float(observed["conditional_coverage"]), 6)
                if observed["conditional_coverage"] is not None else None
            ),
            "conditional_on_scorable_coverage_document_bootstrap_95_ci": (
                cis["conditional_coverage"]
            ),
            "risk_mean_oracle_regret": (
                round(float(observed["risk"]), 6) if observed["risk"] is not None else None
            ),
            "risk_document_bootstrap_95_ci": cis["risk"],
            "false_accept_count": observed["false_accepts"],
            "false_accept_rate": (
                round(float(observed["false_accept_rate"]), 6)
                if observed["false_accept_rate"] is not None else None
            ),
            "false_accept_rate_document_bootstrap_95_ci": cis["false_accept_rate"],
            "n_bootstrap": n_bootstrap,
        })
    return {
        "document_accounting": accounting,
        "thresholds": reports,
    }


def _bootstrap_mean_ci(
    values: list[float], *, n_bootstrap: int, seed: int,
) -> list[float | None]:
    if len(values) < 2 or n_bootstrap <= 0:
        return [None, None]
    rng = random.Random(seed)
    boot = [mean(rng.choice(values) for _ in values) for _ in range(n_bootstrap)]
    return [_percentile(boot, 0.025), _percentile(boot, 0.975)]


def _group_summaries(
    sidecars: list[dict[str, Any]], n_bootstrap: int,
) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for sidecar in sidecars:
        identity = sidecar["identity"]
        groups[(identity["corpus"], identity["parser_id"])].append(sidecar)
    corpora: dict[str, dict[str, Any]] = defaultdict(dict)
    for (corpus, parser_id), values in sorted(groups.items()):
        evidence = [score for item in values if (score := _text_score(item)) is not None]
        qa = [float(item["cached_qa_metrics"]["primary_score"]) for item in values]
        verdicts = Counter(item["assessment"]["source_comparison"]["verdict"] for item in values)
        seed = int(_sha256(f"{corpus}/{parser_id}".encode())[:8], 16)
        corpora[corpus][parser_id] = {
            "n": len(values),
            "source_comparison_verdicts": dict(sorted(verdicts.items())),
            "text_retention_score": _distribution(evidence),
            "text_retention_mean_document_bootstrap_95_ci": _bootstrap_mean_ci(
                evidence, n_bootstrap=n_bootstrap, seed=seed,
            ),
            "cached_primary_score": _distribution(qa),
            "cached_primary_mean_document_bootstrap_95_ci": _bootstrap_mean_ci(
                qa, n_bootstrap=n_bootstrap, seed=seed + 1,
            ),
            "n_bootstrap": n_bootstrap,
        }
    return {corpus: dict(parsers) for corpus, parsers in sorted(corpora.items())}


def _sidecar_relative_path(sidecar: dict[str, Any]) -> Path:
    identity = sidecar["identity"]
    return (
        Path("sidecars") / identity["corpus"] / identity["canonical_id"]
        / identity["parser_id"] / "track_c_v2_source_candidate.json"
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_report(
    sidecars: list[dict[str, Any]], exclusions: list[dict[str, str]], discovered: int,
    *, thresholds: tuple[int, ...] = DEFAULT_THRESHOLDS,
    bad_regret_margin: float = DEFAULT_BAD_REGRET_MARGIN,
    n_bootstrap: int = DEFAULT_BOOTSTRAP,
) -> dict[str, Any]:
    """Build a deterministic report from versioned sidecars."""
    thresholds = tuple(sorted(set(thresholds)))
    evidence_scores = [score for item in sidecars if (score := _text_score(item)) is not None]
    verdicts = Counter(item["assessment"]["source_comparison"]["verdict"] for item in sidecars)
    corpora = sorted({item["identity"]["corpus"] for item in sidecars})
    by_corpus = {
        corpus: [item for item in sidecars if item["identity"]["corpus"] == corpus]
        for corpus in corpora
    }
    fatal_exclusions = [item for item in exclusions if item.get("category") != "v1_ineligible"]
    ineligible = [item for item in exclusions if item.get("category") == "v1_ineligible"]
    risk_coverage = _risk_coverage(
        sidecars, thresholds, bad_regret_margin, n_bootstrap,
    )
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "contract_id": DIAGNOSTIC_CONTRACT_ID,
        "exploratory": True,
        "frozen_v1_unchanged": True,
        "source_candidate_contract": {
            "schema_version": SOURCE_CANDIDATE_SCHEMA_VERSION,
            "policy_id": SOURCE_CANDIDATE_POLICY_ID,
            "implementation_version": SOURCE_CANDIDATE_IMPLEMENTATION_VERSION,
        },
        "frozen_v1_identity_contract": {
            "manifest_path": str(FROZEN_IDENTITY_MANIFEST_PATH),
            "canonical_manifest_sha256": FROZEN_IDENTITY_MANIFEST_SHA256,
            "stage1_execution_manifest_sha256": V1_STAGE1_EXECUTION_MANIFEST_SHA256,
            "n_identities": 245,
        },
        "sidecar_inventory": [
            {
                "corpus": item["identity"]["corpus"],
                "canonical_id": item["identity"]["canonical_id"],
                "parser_id": item["identity"]["parser_id"],
                "relative_path": _sidecar_relative_path(item).as_posix(),
                "sha256": _sha256(_json_bytes(item)),
            }
            for item in sidecars
        ],
        "completeness": {
            "n_v1_records_discovered": discovered,
            "n_v2_assessed": len(sidecars),
            "n_excluded": len(exclusions),
            "n_v1_ineligible": len(ineligible),
            "n_validation_failures": len(fatal_exclusions),
            "complete": (
                discovered > 0 and not fatal_exclusions
                and len(sidecars) + len(ineligible) == discovered
            ),
            "exclusions": exclusions,
        },
        "ranking_signal": {
            "detector_id": TEXT_DETECTOR_ID,
            "note": "No combined source/candidate scalar is computed.",
            "score_distribution": _distribution(evidence_scores),
            "source_comparison_verdicts": dict(sorted(verdicts.items())),
        },
        "detectors": _detector_summary(sidecars),
        "within_document_pairwise": {
            **_concordance(sidecars, n_bootstrap),
            "by_corpus": {
                corpus: _concordance(items, n_bootstrap)
                for corpus, items in by_corpus.items()
            },
        },
        "parser_selection": {
            **_selection(sidecars, n_bootstrap),
            "by_corpus": {
                corpus: _selection(items, n_bootstrap)
                for corpus, items in by_corpus.items()
            },
        },
        "risk_coverage": {
            "bad_outcome_definition": (
                "Accepted output has cached primary_score at least bad_regret_margin below the "
                "best cached QA arm for the same document, including corpus_gold when present."
            ),
            "bad_regret_margin": bad_regret_margin,
            **risk_coverage,
            "by_corpus": {
                corpus: _risk_coverage(items, thresholds, bad_regret_margin, n_bootstrap)
                for corpus, items in by_corpus.items()
            },
        },
        "per_corpus_parser": _group_summaries(sidecars, n_bootstrap),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(payload))


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def execute(
    run_dir: Path,
    cache_root: Path,
    output_dir: Path,
    *,
    dry_run: bool = False,
    thresholds: tuple[int, ...] = DEFAULT_THRESHOLDS,
    bad_regret_margin: float = DEFAULT_BAD_REGRET_MARGIN,
    n_bootstrap: int = DEFAULT_BOOTSTRAP,
    enforce_frozen_inventory: bool = True,
) -> tuple[dict[str, Any], int]:
    """Validate, optionally assess/write, and return (summary/report, exit code)."""
    if _paths_overlap(output_dir, run_dir):
        raise EvidenceValidationError("output_dir must not overlap the frozen V1 run_dir")
    if _paths_overlap(output_dir, cache_root):
        raise EvidenceValidationError("output_dir must not overlap the source cache root")
    frozen_inventory = _load_frozen_identity_manifest() if enforce_frozen_inventory else None
    valid, exclusions, discovered = discover_and_validate(
        run_dir, cache_root, frozen_inventory,
    )
    if enforce_frozen_inventory:
        assert frozen_inventory is not None
        observed_inventory = {
            (pair.corpus, pair.canonical_id, pair.parser_id, "EXECUTED")
            for pair in valid
        }
        for item in exclusions:
            if item.get("category") != "v1_ineligible" or not item.get("identity"):
                continue
            parts = item["identity"].split("/")
            if len(parts) != 3:
                continue
            observed_inventory.add((parts[0], parts[1], parts[2], "DEFECT"))
        difference = _inventory_difference(
            frozen_inventory, observed_inventory, discovered,
        )
        if difference is not None:
            exclusions.append({
                "path": str(run_dir.resolve()),
                "category": "validation_failure",
                "reason": (
                    "frozen V1 identity inventory mismatch: "
                    f"expected={difference['expected_count']}, "
                    f"discovered={difference['discovered_count']}, "
                    f"validated_unique={difference['validated_unique_count']}, "
                    f"duplicate_or_unvalidated={difference['duplicate_or_unvalidated_count']}, "
                    f"missing={difference['missing'][:10]!r}, "
                    f"unexpected={difference['unexpected'][:10]!r}"
                ),
            })
            exclusions.sort(key=lambda item: (item["path"], item["reason"]))
    validation_failures = [
        item for item in exclusions if item.get("category") != "v1_ineligible"
    ]
    if output_dir.exists() and any(output_dir.iterdir()):
        raise EvidenceValidationError(
            "output_dir must be absent or empty; use a fresh versioned directory"
        )
    if dry_run:
        return ({
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "contract_id": DIAGNOSTIC_CONTRACT_ID,
            "dry_run": True,
            "n_v1_records_discovered": discovered,
            "n_validated": len(valid),
            "n_excluded": len(exclusions),
            "exclusions": exclusions,
            "would_write": str(output_dir.resolve()),
        }, 2 if validation_failures or not discovered else 0)

    sidecars: list[dict[str, Any]] = []
    assessment_exclusions = list(exclusions)
    for pair in valid:
        try:
            sidecar = _assessment_sidecar(pair)
        except Exception as exc:  # detector boundary: preserve explicit exclusion
            assessment_exclusions.append({
                "path": str(pair.result_path.resolve()),
                "category": "assessment_failure",
                "reason": f"source-candidate assessment failed: {type(exc).__name__}: {exc}",
            })
            continue
        sidecars.append(sidecar)
        sidecar_path = output_dir / _sidecar_relative_path(sidecar)
        _write_json(sidecar_path, sidecar)
    assessment_exclusions.sort(key=lambda item: (item["path"], item["reason"]))
    report = build_report(
        sidecars, assessment_exclusions, discovered,
        thresholds=thresholds, bad_regret_margin=bad_regret_margin,
        n_bootstrap=n_bootstrap,
    )
    _write_json(output_dir / "track_c_v2_diagnostics.json", report)
    fatal_exclusions = [
        item for item in assessment_exclusions if item.get("category") != "v1_ineligible"
    ]
    return report, 2 if fatal_exclusions or not discovered else 0


def _parse_thresholds(value: str) -> tuple[int, ...]:
    try:
        thresholds = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("thresholds must be comma-separated integers") from exc
    if not thresholds or any(item < 0 or item > 100 for item in thresholds):
        raise argparse.ArgumentTypeError("thresholds must contain values from 0 to 100")
    return thresholds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="Frozen V1 Track C run root")
    parser.add_argument(
        "--cache-root", type=Path, required=True,
        help="Cache root containing qasper/ and tat_dqa/ original PDFs",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Distinct V2 output root; must not equal --run-dir",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate identities and print the plan; do not assess or write files",
    )
    parser.add_argument(
        "--thresholds", type=_parse_thresholds, default=DEFAULT_THRESHOLDS,
        help="Comma-separated detector thresholds (default: 80,90,95)",
    )
    parser.add_argument(
        "--bad-regret-margin", type=float, default=DEFAULT_BAD_REGRET_MARGIN,
        help="Absolute cached-QA regret defining a false accept (default: 0.05)",
    )
    parser.add_argument(
        "--n-bootstrap", type=int, default=DEFAULT_BOOTSTRAP,
        help=f"Document bootstrap resamples (default: {DEFAULT_BOOTSTRAP})",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.bad_regret_margin <= 1:
        parser.error("--bad-regret-margin must be between 0 and 1")
    if args.n_bootstrap < 0:
        parser.error("--n-bootstrap must be nonnegative")
    try:
        report, exit_code = execute(
            args.run_dir, args.cache_root, args.output_dir,
            dry_run=args.dry_run, thresholds=args.thresholds,
            bad_regret_margin=args.bad_regret_margin, n_bootstrap=args.n_bootstrap,
        )
    except (EvidenceValidationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
