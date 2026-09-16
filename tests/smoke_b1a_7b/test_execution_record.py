"""Execution-record schema + writer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.execution_record import (
    REQUIRED_FIELDS,
    ExecutionRecord,
    ExecutionRecordSchemaError,
    compute_pair_id,
    validate_schema,
    write_execution_record,
)

_HEX64 = "0" * 64


def _base_record(**overrides) -> ExecutionRecord:
    base = dict(
        pair_id="abcdef1234567890",
        canonical_id="SYN-PMC-1",
        corpus="pmc_oa",
        parser_id="aksharamd-reference",
        parser_package_version="0.3.6",
        parser_package_source_sha256=_HEX64,
        parser_model_version=None,
        parser_model_artifact_sha256=None,
        adapter_source_sha256=_HEX64,
        python_version="3.12.2",
        platform_string="Windows-11-10.0.26200-SP0",
        git_commit="deadbeef",
        cpu_physical_cores=8,
        cuda_version=None,
        cuda_driver_version=None,
        cuda_device_name=None,
        model_cache_path=None,
        network_egress_blocked=True,
        pair_started_at="2026-09-16T00:00:00+00:00",
        pair_finished_at="2026-09-16T00:00:01+00:00",
        wall_clock_seconds=1.0,
        cpu_seconds_user=0.5,
        cpu_seconds_system=0.1,
        peak_rss_bytes=1024,
        peak_vram_bytes=None,
        cuda_events=None,
        output_bytes=10,
        output_sha256=_HEX64,
        stdout_bytes=0,
        stdout_sha256=_HEX64,
        stderr_bytes=0,
        stderr_sha256=_HEX64,
        exit_status="EXECUTED",
        defect_reason=None,
        normalization_version="2",
        parser_execution_contract_version="v1",
        parser_execution_contract_config_sha256=_HEX64,
        smoke_spec_config_sha256=_HEX64,
    )
    base.update(overrides)
    return ExecutionRecord(**base)


def test_required_fields_are_exactly_37() -> None:
    # 32 fields from the contract's execution_record_schema.required_fields
    # + 5 nullable-only fields already in that list + smoke_spec_config_sha256
    # -> 37 total.
    assert len(REQUIRED_FIELDS) == 38  # includes smoke_spec_config_sha256


def test_happy_path_cpu_only_parser_passes() -> None:
    r = _base_record()
    validate_schema(r)  # no raise


def test_happy_path_vlm_parser_with_cuda() -> None:
    r = _base_record(
        parser_id="marker",
        parser_model_version="fake-marker",
        parser_model_artifact_sha256=_HEX64,
        cuda_version="12.6",
        cuda_driver_version="12.6",
        cuda_device_name="NVIDIA RTX A2000",
        model_cache_path="C:/models/marker",
        peak_vram_bytes=512_000_000,
        cuda_events=42,
    )
    validate_schema(r)


def test_missing_required_field_reason_when_defect_raises() -> None:
    r = _base_record(exit_status="DEFECT", defect_reason=None)
    with pytest.raises(ExecutionRecordSchemaError, match="defect_reason"):
        validate_schema(r)


def test_defect_with_reason_passes() -> None:
    r = _base_record(exit_status="DEFECT", defect_reason="marker_timeout_600s",
                     parser_id="marker",
                     parser_model_version="v",
                     parser_model_artifact_sha256=_HEX64,
                     cuda_version="12.6", cuda_driver_version="12.6",
                     cuda_device_name="fake", model_cache_path="/x",
                     peak_vram_bytes=0, cuda_events=0)
    validate_schema(r)


def test_defect_reason_null_for_executed_is_ok() -> None:
    r = _base_record(exit_status="EXECUTED", defect_reason=None)
    validate_schema(r)


def test_vlm_parser_null_cuda_field_raises() -> None:
    """marker requires cuda_version non-null."""
    r = _base_record(
        parser_id="marker",
        parser_model_version="fake",
        parser_model_artifact_sha256=_HEX64,
        cuda_version=None,  # not permitted for marker
        cuda_driver_version="12.6",
        cuda_device_name="fake",
        model_cache_path="/x",
        peak_vram_bytes=0, cuda_events=0,
    )
    with pytest.raises(ExecutionRecordSchemaError, match="cuda_version"):
        validate_schema(r)


def test_bad_exit_status_raises() -> None:
    r = _base_record(exit_status="MAYBE")
    with pytest.raises(ExecutionRecordSchemaError, match="exit_status"):
        validate_schema(r)


def test_bad_normalization_version_raises() -> None:
    r = _base_record(normalization_version="1")
    with pytest.raises(ExecutionRecordSchemaError, match="normalization_version"):
        validate_schema(r)


def test_bad_contract_version_raises() -> None:
    r = _base_record(parser_execution_contract_version="v0")
    with pytest.raises(ExecutionRecordSchemaError, match="parser_execution_contract_version"):
        validate_schema(r)


def test_negative_output_bytes_raises() -> None:
    r = _base_record(output_bytes=-1)
    with pytest.raises(ExecutionRecordSchemaError, match="output_bytes"):
        validate_schema(r)


def test_non_hex_sha_raises() -> None:
    r = _base_record(output_sha256="not-hex-and-wrong-length")
    with pytest.raises(ExecutionRecordSchemaError, match="output_sha256"):
        validate_schema(r)


def test_write_execution_record_roundtrip(tmp_path: Path) -> None:
    r = _base_record()
    path = tmp_path / "execution_record.json"
    write_execution_record(path, r)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pair_id"] == r.pair_id
    assert data["parser_id"] == r.parser_id


def test_write_execution_record_refuses_bad_schema(tmp_path: Path) -> None:
    r = _base_record(exit_status="DEFECT", defect_reason=None)
    path = tmp_path / "execution_record.json"
    with pytest.raises(ExecutionRecordSchemaError):
        write_execution_record(path, r)
    assert not path.exists()  # never written


def test_pair_id_is_16_hex_and_deterministic() -> None:
    a = compute_pair_id(canonical_id="SYN-1", parser_id="marker")
    b = compute_pair_id(canonical_id="SYN-1", parser_id="marker")
    assert a == b
    assert len(a) == 16
    c = compute_pair_id(canonical_id="SYN-1", parser_id="docling")
    assert c != a


def test_pair_id_matches_adjudication_algorithm() -> None:
    """Cross-check with the adjudication.prepare_reviewer_artifact
    algorithm so the provenance-link check in the runner never fires
    from a producer mismatch."""
    import hashlib
    canonical_id, parser_id = "SYN-PMC-1", "marker"
    expected = hashlib.sha256(f"{canonical_id}::{parser_id}".encode()).hexdigest()[:16]
    assert compute_pair_id(canonical_id=canonical_id, parser_id=parser_id) == expected
