"""37-field execution record: writer + schema validator.

The record shape is enumerated in
``benchmarks/eval_v1/config/parser_execution_contract_v1.json ::
execution_record_schema``. This module builds a record from
per-invocation observations and validates every field is present +
respects the per-field nullability rules.

Fail-closed. A record that would violate the schema raises
``ExecutionRecordSchemaError`` before it is written to disk.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ExecutionRecordSchemaError(RuntimeError):
    """Record violates the execution_record_schema."""


REQUIRED_FIELDS = (
    "pair_id",
    "canonical_id",
    "corpus",
    "parser_id",
    "parser_package_version",
    "parser_package_source_sha256",
    "parser_model_version",
    "parser_model_artifact_sha256",
    "adapter_source_sha256",
    "python_version",
    "platform_string",
    "git_commit",
    "cpu_physical_cores",
    "cuda_version",
    "cuda_driver_version",
    "cuda_device_name",
    "model_cache_path",
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
    "normalization_version",
    "parser_execution_contract_version",
    "parser_execution_contract_config_sha256",
    # Not in the parser-execution contract required list itself but
    # required for smoke bookkeeping:
    "smoke_spec_config_sha256",
)

# Per-field nullability rules. Every other field must be non-null.
NULLABILITY = {
    "parser_model_version": {"aksharamd-reference", "markitdown"},
    "parser_model_artifact_sha256": {"aksharamd-reference", "markitdown"},
    "cuda_version": {"aksharamd-reference", "markitdown"},
    "cuda_driver_version": {"aksharamd-reference", "markitdown"},
    "cuda_device_name": {"aksharamd-reference", "markitdown"},
    "model_cache_path": {"aksharamd-reference", "markitdown"},
    "peak_vram_bytes": {"aksharamd-reference", "markitdown"},
    "cuda_events": {"aksharamd-reference", "markitdown"},
    # defect_reason is nullable only when exit_status == EXECUTED.
    # Handled specially below.
}

CPU_ONLY_PARSERS = {"aksharamd-reference", "markitdown"}


@dataclass
class ExecutionRecord:
    pair_id: str
    canonical_id: str
    corpus: str
    parser_id: str
    parser_package_version: str
    parser_package_source_sha256: str
    parser_model_version: str | None
    parser_model_artifact_sha256: str | None
    adapter_source_sha256: str
    python_version: str
    platform_string: str
    git_commit: str
    cpu_physical_cores: int
    cuda_version: str | None
    cuda_driver_version: str | None
    cuda_device_name: str | None
    model_cache_path: str | None
    network_egress_blocked: bool
    pair_started_at: str
    pair_finished_at: str
    wall_clock_seconds: float
    cpu_seconds_user: float
    cpu_seconds_system: float
    peak_rss_bytes: int
    peak_vram_bytes: int | None
    cuda_events: int | None
    output_bytes: int
    output_sha256: str
    stdout_bytes: int
    stdout_sha256: str
    stderr_bytes: int
    stderr_sha256: str
    exit_status: str
    defect_reason: str | None
    normalization_version: str
    parser_execution_contract_version: str
    parser_execution_contract_config_sha256: str
    smoke_spec_config_sha256: str


def validate_schema(record: ExecutionRecord) -> None:
    """Raise ExecutionRecordSchemaError on any violation of the
    contract-level schema. The runner calls this before writing."""
    d = asdict(record)
    # 1. Every required field present.
    for name in REQUIRED_FIELDS:
        if name not in d:
            raise ExecutionRecordSchemaError(f"missing required field: {name}")
    # 2. Non-null unless the field is on the nullable allowlist for
    #    this parser_id, or is the special exit_status/defect_reason
    #    pair.
    for name in REQUIRED_FIELDS:
        value = d[name]
        if value is not None:
            continue
        # Special: defect_reason may be null iff exit_status == EXECUTED.
        if name == "defect_reason":
            if d["exit_status"] != "EXECUTED":
                raise ExecutionRecordSchemaError(
                    "defect_reason is null but exit_status is not EXECUTED"
                )
            continue
        # Special: parser-family nullability by parser_id.
        allowed_parsers = NULLABILITY.get(name)
        if allowed_parsers is None:
            raise ExecutionRecordSchemaError(
                f"field {name!r} is null but not on the nullable allowlist"
            )
        if d["parser_id"] not in allowed_parsers:
            raise ExecutionRecordSchemaError(
                f"field {name!r} is null but parser_id={d['parser_id']!r} "
                f"is not permitted to null it (allowed: {sorted(allowed_parsers)})"
            )
    # 3. exit_status must be EXECUTED or DEFECT.
    if d["exit_status"] not in {"EXECUTED", "DEFECT"}:
        raise ExecutionRecordSchemaError(
            f"exit_status must be EXECUTED or DEFECT, got {d['exit_status']!r}"
        )
    # 4. DEFECT must carry defect_reason.
    if d["exit_status"] == "DEFECT" and not d["defect_reason"]:
        raise ExecutionRecordSchemaError(
            "exit_status=DEFECT requires a non-empty defect_reason"
        )
    # 5. Contract versions pinned.
    if d["parser_execution_contract_version"] != "v1":
        raise ExecutionRecordSchemaError(
            "parser_execution_contract_version must be 'v1'"
        )
    if d["normalization_version"] != "2":
        raise ExecutionRecordSchemaError("normalization_version must be '2'")
    # 6. Byte lengths + hashes are internally consistent (non-negative
    #    ints; SHAs are 64-hex-char strings).
    for size_field, sha_field in (
        ("output_bytes", "output_sha256"),
        ("stdout_bytes", "stdout_sha256"),
        ("stderr_bytes", "stderr_sha256"),
    ):
        if d[size_field] < 0:
            raise ExecutionRecordSchemaError(f"{size_field} must be >= 0")
        _validate_sha(d[sha_field], sha_field)
    # 7. Contract-config SHAs are 64-hex-char.
    _validate_sha(d["parser_execution_contract_config_sha256"], "parser_execution_contract_config_sha256")
    _validate_sha(d["smoke_spec_config_sha256"], "smoke_spec_config_sha256")


def _validate_sha(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ExecutionRecordSchemaError(f"{name} must be a 64-hex-char SHA-256 string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ExecutionRecordSchemaError(f"{name} is not hex") from exc


def write_execution_record(path: Path, record: ExecutionRecord) -> None:
    """Validate then write. Fail-closed on schema violation."""
    validate_schema(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(asdict(record), indent=2, sort_keys=True)
    path.write_text(body, encoding="utf-8")


def load_execution_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compute_pair_id(*, canonical_id: str, parser_id: str) -> str:
    """pair_id = sha256(f"{canonical_id}::{parser_id}")[:16].

    This exactly matches the algorithm in
    ``benchmarks.eval_v1.adjudication.prepare_reviewer_artifact`` so
    the smoke's execution-record pair_id and its reviewer-artifact
    pair_id are provably equal. The provenance-mismatch check in the
    runner catches any drift between the two producers.
    """
    digest = hashlib.sha256(f"{canonical_id}::{parser_id}".encode()).hexdigest()
    return digest[:16]
