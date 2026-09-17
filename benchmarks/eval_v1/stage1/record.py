"""Stage 1 per-invocation execution record: writer + schema validator.

Mirrors ``benchmarks.eval_v1.smoke_b1a_7b.execution_record`` but
replaces ``smoke_spec_config_sha256`` (smoke bookkeeping field) with
``stage1_execution_manifest_sha256`` — the raw-byte SHA-256 of
``docs/evaluation/STAGE1_EXECUTION_MANIFEST.json`` as written at
Stage 1 start.  All other 36 fields and nullability rules are
identical to the parser-execution contract v1 schema.

The runner validates every record before writing; a record that
violates the schema raises ``Stage1RecordSchemaError`` and is not
written to disk.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class Stage1RecordSchemaError(RuntimeError):
    """Record violates the Stage 1 execution-record schema."""


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
    "stage1_execution_manifest_sha256",
)

NULLABILITY: dict[str, set[str]] = {
    "parser_model_version": {"aksharamd-reference", "markitdown"},
    "parser_model_artifact_sha256": {"aksharamd-reference", "markitdown"},
    "cuda_version": {"aksharamd-reference", "markitdown"},
    "cuda_driver_version": {"aksharamd-reference", "markitdown"},
    "cuda_device_name": {"aksharamd-reference", "markitdown"},
    "model_cache_path": {"aksharamd-reference", "markitdown"},
    "peak_vram_bytes": {"aksharamd-reference", "markitdown"},
    "cuda_events": {"aksharamd-reference", "markitdown"},
}

CPU_ONLY_PARSERS = {"aksharamd-reference", "markitdown"}


@dataclass
class Stage1ExecutionRecord:
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
    stage1_execution_manifest_sha256: str


def validate_schema(record: Stage1ExecutionRecord) -> None:
    d = asdict(record)
    for name in REQUIRED_FIELDS:
        if name not in d:
            raise Stage1RecordSchemaError(f"missing required field: {name}")
    for name in REQUIRED_FIELDS:
        value = d[name]
        if value is not None:
            continue
        if name == "defect_reason":
            if d["exit_status"] != "EXECUTED":
                raise Stage1RecordSchemaError(
                    "defect_reason is null but exit_status is not EXECUTED"
                )
            continue
        allowed = NULLABILITY.get(name)
        if allowed is None:
            raise Stage1RecordSchemaError(
                f"field {name!r} is null but not on the nullable allowlist"
            )
        if d["parser_id"] not in allowed:
            raise Stage1RecordSchemaError(
                f"field {name!r} is null but parser_id={d['parser_id']!r} "
                f"is not permitted to null it (allowed: {sorted(allowed)})"
            )
    if d["exit_status"] not in {"EXECUTED", "DEFECT"}:
        raise Stage1RecordSchemaError(
            f"exit_status must be EXECUTED or DEFECT, got {d['exit_status']!r}"
        )
    if d["exit_status"] == "DEFECT" and not d["defect_reason"]:
        raise Stage1RecordSchemaError(
            "exit_status=DEFECT requires a non-empty defect_reason"
        )
    if d["parser_execution_contract_version"] != "v1":
        raise Stage1RecordSchemaError(
            "parser_execution_contract_version must be 'v1'"
        )
    if d["normalization_version"] != "2":
        raise Stage1RecordSchemaError("normalization_version must be '2'")
    for size_field, sha_field in (
        ("output_bytes", "output_sha256"),
        ("stdout_bytes", "stdout_sha256"),
        ("stderr_bytes", "stderr_sha256"),
    ):
        if d[size_field] < 0:
            raise Stage1RecordSchemaError(f"{size_field} must be >= 0")
        _validate_sha(d[sha_field], sha_field)
    _validate_sha(d["parser_execution_contract_config_sha256"],
                  "parser_execution_contract_config_sha256")
    _validate_sha(d["stage1_execution_manifest_sha256"],
                  "stage1_execution_manifest_sha256")


def _validate_sha(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise Stage1RecordSchemaError(
            f"{name} must be a 64-hex-char SHA-256 string, got {value!r}"
        )
    try:
        int(value, 16)
    except ValueError as exc:
        raise Stage1RecordSchemaError(f"{name} is not hex") from exc


def write_execution_record(path: Path, record: Stage1ExecutionRecord) -> None:
    validate_schema(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(asdict(record), indent=2, sort_keys=True)
    path.write_text(body, encoding="utf-8")


def compute_pair_id(*, canonical_id: str, parser_id: str) -> str:
    return hashlib.sha256(f"{canonical_id}::{parser_id}".encode()).hexdigest()[:16]


def load_execution_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
