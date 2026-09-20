from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.eval_v1.stage1.run_track_a_olmocr import MANIFEST_SHA
from benchmarks.eval_v1.stage2 import aggregate_olmocr
from benchmarks.eval_v1.stage2.olmocr_hygiene import (
    FROZEN_PARSER_IDS,
    OlmocrHygieneError,
    build_completeness_report,
    load_unique_execution_records,
    load_unique_stage2_results,
)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _execution(
    root: Path,
    copy: str,
    *,
    canonical_id: str = "tables/doc-1",
    parser_id: str = "aksharamd-reference",
    output_sha: str = "a" * 64,
    manifest_sha: str = MANIFEST_SHA,
    finished: str = "2026-09-20T01:00:00+00:00",
) -> Path:
    path = root / copy / parser_id / "execution_record.json"
    _write_json(path, {
        "canonical_id": canonical_id,
        "parser_id": parser_id,
        "exit_status": "EXECUTED",
        "output_sha256": output_sha,
        "stage1_execution_manifest_sha256": manifest_sha,
        "pair_finished_at": finished,
    })
    return path


def _result(
    execution_path: Path,
    *,
    canonical_id: str = "tables/doc-1",
    parser_id: str = "aksharamd-reference",
    status: str = "SCORED",
    scored_at: str = "2026-09-20T02:00:00+00:00",
    readiness_score: float = 0.95,
    source_sha: str = "b" * 64,
) -> Path:
    path = execution_path.parent / "stage2_olmocr_result.json"
    _write_json(path, {
        "stage2_schema_version": "1",
        "canonical_id": canonical_id,
        "parser_id": parser_id,
        "status": status,
        "sha_verified": status == "SCORED",
        "source_pdf_sha256": source_sha,
        "readiness_score": readiness_score,
        "warning_codes": [],
        "n_tests": 1,
        "n_passed": 1,
        "n_failed": 0,
        "test_results": [],
        "scored_at": scored_at,
    })
    return path


def test_identical_duplicates_collapse_to_latest_valid_completion(tmp_path: Path) -> None:
    old = _execution(tmp_path, "old", finished="2026-09-20T01:00:00+00:00")
    new = _execution(tmp_path, "new", finished="2026-09-20T03:00:00+00:00")
    _result(old, scored_at="2026-09-20T02:00:00+00:00", readiness_score=0.8)
    _result(new, scored_at="2026-09-20T04:00:00+00:00", readiness_score=0.9)

    records, execution_report = load_unique_execution_records(
        tmp_path, expected_manifest_sha=MANIFEST_SHA
    )
    results, result_report = load_unique_stage2_results(
        tmp_path, expected_manifest_sha=MANIFEST_SHA
    )

    assert [path for path, _ in records] == [new]
    assert execution_report.duplicate_files == 1
    assert len(results) == 1
    assert results[0]["readiness_score"] == 0.9
    assert result_report.duplicate_files == 1


def test_stale_provenance_is_never_authoritative(tmp_path: Path) -> None:
    frozen = _execution(tmp_path, "frozen", finished="2026-09-20T01:00:00+00:00")
    _execution(
        tmp_path,
        "stale",
        manifest_sha="f" * 64,
        output_sha="c" * 64,
        finished="2099-01-01T00:00:00+00:00",
    )
    records, _ = load_unique_execution_records(
        tmp_path, expected_manifest_sha=MANIFEST_SHA
    )
    assert [path for path, _ in records] == [frozen]


def test_conflicting_duplicate_output_hashes_fail_loudly(tmp_path: Path) -> None:
    _execution(tmp_path, "one", output_sha="a" * 64)
    _execution(tmp_path, "two", output_sha="c" * 64)
    with pytest.raises(OlmocrHygieneError, match="conflicting duplicate output"):
        load_unique_execution_records(tmp_path, expected_manifest_sha=MANIFEST_SHA)


def test_conflicting_duplicate_input_hashes_fail_loudly(tmp_path: Path) -> None:
    one = _execution(tmp_path, "one")
    two = _execution(tmp_path, "two")
    _result(one, source_sha="b" * 64)
    _result(two, source_sha="d" * 64)
    with pytest.raises(OlmocrHygieneError, match="conflicting duplicate input"):
        load_unique_stage2_results(tmp_path, expected_manifest_sha=MANIFEST_SHA)


def test_result_provenance_must_match_colocated_execution(tmp_path: Path) -> None:
    execution = _execution(tmp_path, "one")
    result_path = _result(execution)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["stage1_output_sha256"] = "c" * 64
    _write_json(result_path, result)
    with pytest.raises(OlmocrHygieneError, match="disagrees with colocated"):
        load_unique_stage2_results(tmp_path, expected_manifest_sha=MANIFEST_SHA)


def test_completeness_uses_unique_terminal_pairs() -> None:
    expected = {("doc", parser_id) for parser_id in FROZEN_PARSER_IDS}
    results = [
        {"canonical_id": "doc", "parser_id": parser_id, "status": "SCORED"}
        for parser_id in FROZEN_PARSER_IDS
    ]
    report = build_completeness_report(results, expected)
    assert report["is_complete"] is True
    assert report["n_expected_pairs"] == 4
    assert report["n_complete_pairs"] == 4
    assert report["n_missing_pairs"] == 0

    results[-1]["status"] = "SHA_MISMATCH"
    report = build_completeness_report(results, expected)
    assert report["n_missing_pairs"] == 0
    assert report["n_nonterminal_pairs"] == 1


def test_incomplete_aggregation_blocks_by_default_and_withholds_track_b(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    pdf_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdf_dir / "tables" / "doc-1.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    execution = _execution(run_dir, "copy")
    _result(execution)

    base_args = [
        "--run-dir", str(run_dir),
        "--pdf-dir", str(pdf_dir),
        "--out-dir", str(out_dir),
        "--n-bootstrap", "1",
    ]
    assert aggregate_olmocr.main(base_args) == 2
    assert not list(out_dir.glob("stage2-olmocr-*-aggregated.json"))

    assert aggregate_olmocr.main([*base_args, "--allow-incomplete"]) == 0
    outputs = list(out_dir.glob("stage2-olmocr-*-aggregated.json"))
    assert len(outputs) == 1
    payload = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert payload["completeness"]["is_complete"] is False
    assert payload["track_b_allocation_manifest_path"] is None
    assert not list(out_dir.glob("track-b-allocation-manifest-*.json"))
