from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.eval_v1.stage1.run_track_a_olmocr import MANIFEST_SHA
from benchmarks.eval_v1.stage2 import aggregate_olmocr, run_score_olmocr
from benchmarks.eval_v1.stage2.olmocr_hygiene import (
    BENCHMARK_TEST_FILENAMES,
    FROZEN_PARSER_IDS,
    STAGE2_SCORER_CONTRACT_ID,
    OlmocrHygieneError,
    assertion_set_sha256,
    build_completeness_report,
    expected_pairs_from_frozen_acquisition,
    is_terminal_stage2_result,
    load_unique_execution_records,
    load_unique_stage2_results,
    load_verified_assertion_inventory,
    verified_benchmark_test_inventory_sha256,
)
from benchmarks.eval_v1.stage2.run_score_olmocr import _atomic_write_json
from benchmarks.eval_v1.stage2.score_olmocr import (
    _load_assertion_rows,
    _readiness_band,
    load_all_unit_tests,
    replay_and_score,
)

TEST_INVENTORY_SHA = "d" * 64
TEST_ASSERTIONS = {
    "doc": (("t1", "present"),),
    "tables/doc": (("t1", "present"),),
    "tables/doc-1": (("t1", "present"),),
}


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
    exit_status: str = "EXECUTED",
) -> Path:
    path = root / copy / parser_id / "execution_record.json"
    _write_json(
        path,
        {
            "canonical_id": canonical_id,
            "parser_id": parser_id,
            "exit_status": exit_status,
            "output_sha256": output_sha,
            "stage1_execution_manifest_sha256": manifest_sha,
            "pair_finished_at": finished,
        },
    )
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
    scorer_contract_id: str = STAGE2_SCORER_CONTRACT_ID,
    test_inventory_sha: str = TEST_INVENTORY_SHA,
) -> Path:
    path = execution_path.parent / "stage2_olmocr_result.json"
    _write_json(
        path,
        {
            "stage2_schema_version": "3",
            "canonical_id": canonical_id,
            "parser_id": parser_id,
            "status": status,
            "sha_verified": status == "SCORED",
            "source_pdf_sha256": source_sha,
            "readiness_score": readiness_score,
            "stage2_scorer_contract_id": scorer_contract_id,
            "benchmark_test_inventory_sha256": test_inventory_sha,
            "expected_assertion_count": len(TEST_ASSERTIONS[canonical_id]),
            "assertion_set_sha256": assertion_set_sha256(TEST_ASSERTIONS[canonical_id]),
            "warning_codes": [],
            "n_tests": 1,
            "n_passed": 1,
            "n_failed": 0,
            "test_results": [{"test_id": "t1", "test_type": "present", "passed": True}],
            "scored_at": scored_at,
        },
    )
    return path


def _valid_result_dict(parser_id: str = "aksharamd-reference") -> dict:
    return {
        "stage2_schema_version": "3",
        "canonical_id": "doc",
        "parser_id": parser_id,
        "status": "SCORED",
        "stage1_exit_status": "EXECUTED",
        "stage2_scorer_contract_id": STAGE2_SCORER_CONTRACT_ID,
        "benchmark_test_inventory_sha256": TEST_INVENTORY_SHA,
        "expected_assertion_count": 1,
        "assertion_set_sha256": assertion_set_sha256(TEST_ASSERTIONS["doc"]),
        "sha_verified": True,
        "readiness_score": 95,
        "n_tests": 1,
        "n_passed": 1,
        "n_failed": 0,
        "test_results": [{"test_id": "t1", "test_type": "present", "passed": True}],
    }


def _make_frozen_inventory(
    root: Path,
    canonical_ids: tuple[str, ...],
) -> tuple[Path, Path, str, str]:
    bench_data = root / "bench_data"
    pdf_dir = bench_data / "pdfs"
    receipt_files = []
    for canonical_id in canonical_ids:
        pdf_path = pdf_dir / f"{canonical_id}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(canonical_id.encode())
        receipt_files.append(
            {
                "path": f"bench_data/pdfs/{canonical_id}.pdf",
                "status": "verified",
                "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            }
        )
    for filename in BENCHMARK_TEST_FILENAMES:
        test_path = bench_data / filename
        test_path.write_text(f"{filename}\n", encoding="utf-8")
        receipt_files.append(
            {
                "path": f"bench_data/{filename}",
                "status": "verified",
                "sha256": hashlib.sha256(test_path.read_bytes()).hexdigest(),
            }
        )
    receipt = root / "acquisition.json"
    _write_json(receipt, {"files": receipt_files})
    receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
    inventory_sha = verified_benchmark_test_inventory_sha256(
        receipt,
        bench_data,
        expected_receipt_sha256=receipt_sha,
    )
    return receipt, pdf_dir, receipt_sha, inventory_sha


def _load_results(root: Path) -> tuple[list[dict], object]:
    records, _ = load_unique_execution_records(root, expected_manifest_sha=MANIFEST_SHA)
    return load_unique_stage2_results(
        root,
        expected_manifest_sha=MANIFEST_SHA,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
        authoritative_execution_records=records,
    )


def _make_assertion_inventory(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    prefixes = {
        "arxiv_math.jsonl": "arxiv_math",
        "headers_footers.jsonl": "headers_footers",
        "long_tiny_text.jsonl": "long_tiny_text",
        "multi_column.jsonl": "multi_column",
        "old_scans.jsonl": "old_scans",
        "old_scans_math.jsonl": "old_scans_math",
        "table_tests.jsonl": "tables",
    }
    for index, (filename, prefix) in enumerate(prefixes.items()):
        (root / filename).write_text(
            json.dumps(
                {
                    "id": f"assertion-{index}",
                    "pdf": f"{prefix}/doc-{index}.pdf",
                    "type": "present",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return root


def test_identical_duplicates_collapse_to_latest_valid_completion(tmp_path: Path) -> None:
    old = _execution(tmp_path, "old", finished="2026-09-20T01:00:00+00:00")
    new = _execution(tmp_path, "new", finished="2026-09-20T03:00:00+00:00")
    _result(old, scored_at="2026-09-20T02:00:00+00:00", readiness_score=80)
    _result(new, scored_at="2026-09-20T04:00:00+00:00", readiness_score=90)

    records, execution_report = load_unique_execution_records(tmp_path, expected_manifest_sha=MANIFEST_SHA)
    results, result_report = _load_results(tmp_path)

    assert [path for path, _ in records] == [new]
    assert execution_report.duplicate_files == 1
    assert len(results) == 1
    assert results[0]["readiness_score"] == 90
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
    records, _ = load_unique_execution_records(tmp_path, expected_manifest_sha=MANIFEST_SHA)
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
        _load_results(tmp_path)


def test_result_provenance_must_match_colocated_execution(tmp_path: Path) -> None:
    execution = _execution(tmp_path, "one")
    result_path = _result(execution)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["stage1_output_sha256"] = "c" * 64
    _write_json(result_path, result)
    with pytest.raises(OlmocrHygieneError, match="disagrees with authoritative"):
        _load_results(tmp_path)


def test_orphan_result_cannot_claim_terminal_status(tmp_path: Path) -> None:
    orphan = tmp_path / "orphan" / "stage2_olmocr_result.json"
    _write_json(orphan, _valid_result_dict())
    with pytest.raises(OlmocrHygieneError, match="no authoritative"):
        load_unique_stage2_results(
            tmp_path,
            expected_manifest_sha=MANIFEST_SHA,
            expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
            expected_test_inventory_sha256=TEST_INVENTORY_SHA,
            expected_assertions_by_document=TEST_ASSERTIONS,
            authoritative_execution_records=[],
        )


def test_stage2_result_validates_selected_authoritative_stage1(tmp_path: Path) -> None:
    _execution(tmp_path, "authoritative", finished="2026-09-20T02:00:00+00:00")
    phantom_record = tmp_path / "result-only" / "execution_record.json"
    result_path = _result(phantom_record, status="SKIPPED_DEFECT")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["stage1_exit_status"] = "DEFECT"
    _write_json(result_path, result)
    with pytest.raises(OlmocrHygieneError, match="authoritative Stage 1"):
        _load_results(tmp_path)


def test_conflicting_stage1_terminal_statuses_fail_loudly(tmp_path: Path) -> None:
    _execution(tmp_path, "executed", exit_status="EXECUTED")
    _execution(tmp_path, "defect", exit_status="DEFECT")
    with pytest.raises(OlmocrHygieneError, match="terminal statuses"):
        load_unique_execution_records(tmp_path, expected_manifest_sha=MANIFEST_SHA)


def test_completeness_uses_unique_terminal_pairs() -> None:
    expected = {("doc", parser_id) for parser_id in FROZEN_PARSER_IDS}
    results = [_valid_result_dict(parser_id) for parser_id in FROZEN_PARSER_IDS]
    report = build_completeness_report(
        results,
        expected,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )
    assert report["is_complete"] is True
    assert report["n_expected_pairs"] == 4
    assert report["n_complete_pairs"] == 4
    assert report["n_missing_pairs"] == 0

    results[-1]["status"] = "SHA_MISMATCH"
    report = build_completeness_report(
        results,
        expected,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )
    assert report["n_missing_pairs"] == 0
    assert report["n_nonterminal_pairs"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha_verified", False),
        ("readiness_score", float("nan")),
        ("readiness_score", -1),
        ("readiness_score", 101),
        ("scoring_error", "traceback"),
        ("n_passed", 0),
    ],
)
def test_invalid_scored_terminal_is_replayable(field: str, value: object) -> None:
    result = _valid_result_dict()
    result[field] = value
    assert not is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )
    report = build_completeness_report(
        [result],
        {("doc", "aksharamd-reference")},
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )
    assert report["n_complete_pairs"] == 0
    assert report["n_nonterminal_pairs"] == 1


@pytest.mark.parametrize(
    "test_results",
    [
        [],
        [{"test_id": "fabricated", "test_type": "present", "passed": True}],
        [
            {"test_id": "t1", "test_type": "present", "passed": True},
            {"test_id": "t1", "test_type": "present", "passed": True},
        ],
        [{"test_id": "t1", "test_type": "table", "passed": True}],
        [{"test_id": "other-doc-test", "test_type": "present", "passed": True}],
    ],
    ids=["missing", "fabricated", "duplicate", "wrong-type", "wrong-document"],
)
def test_scored_terminal_requires_exact_frozen_assertion_set(test_results: list[dict]) -> None:
    result = _valid_result_dict()
    result["test_results"] = test_results
    result["n_tests"] = len(test_results)
    result["n_passed"] = len(test_results)
    result["n_failed"] = 0
    assert not is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document={
            **TEST_ASSERTIONS,
            "other/doc": (("other-doc-test", "present"),),
        },
    )


def test_assertion_set_provenance_is_required_and_auditable() -> None:
    result = _valid_result_dict()
    assert result["assertion_set_sha256"] == assertion_set_sha256(TEST_ASSERTIONS["doc"])
    result["assertion_set_sha256"] = "0" * 64
    assert not is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )


def test_scored_requires_nonempty_consistent_test_results() -> None:
    result = _valid_result_dict()
    result.update(n_tests=0, n_passed=0, n_failed=0, test_results=[])
    assert not is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )


def test_skipped_defect_requires_matching_stage1_defect() -> None:
    result = _valid_result_dict()
    result["status"] = "SKIPPED_DEFECT"
    assert not is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )
    result["stage1_exit_status"] = "DEFECT"
    assert is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )


def test_scorer_emits_v3_contract_for_stage1_defect(tmp_path: Path) -> None:
    record_path = tmp_path / "execution_record.json"
    _write_json(
        record_path,
        {
            "canonical_id": "tables/doc",
            "parser_id": "marker",
            "exit_status": "DEFECT",
            "defect_reason": "timeout",
            "output_sha256": "a" * 64,
            "stage1_execution_manifest_sha256": MANIFEST_SHA,
        },
    )
    result = replay_and_score(
        record_path=record_path,
        pdf_dir=tmp_path / "pdfs",
        unit_tests={},
        root=tmp_path,
        benchmark_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )
    assert result["stage2_schema_version"] == "3"
    assert result["stage2_scorer_contract_id"] == STAGE2_SCORER_CONTRACT_ID
    assert result["benchmark_test_inventory_sha256"] == TEST_INVENTORY_SHA
    assert result["expected_assertion_count"] == 1
    assert result["assertion_set_sha256"] == assertion_set_sha256(TEST_ASSERTIONS["tables/doc"])
    assert result["stage1_exit_status"] == "DEFECT"
    assert is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )


def test_readiness_bands_use_native_zero_to_100_scale() -> None:
    assert _readiness_band(85) == "HIGH"
    assert _readiness_band(70) == "OK"
    assert _readiness_band(50) == "RISKY"
    assert _readiness_band(49) == "POOR"


def test_changed_scorer_or_test_inventory_invalidates_terminal() -> None:
    result = _valid_result_dict()
    assert not is_terminal_stage2_result(
        result,
        expected_scorer_contract_id="olmocr_stage2_v4",
        expected_test_inventory_sha256=TEST_INVENTORY_SHA,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )
    assert not is_terminal_stage2_result(
        result,
        expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
        expected_test_inventory_sha256="e" * 64,
        expected_assertions_by_document=TEST_ASSERTIONS,
    )


def test_dedup_prefers_current_metric_contract_over_newer_stale_result(
    tmp_path: Path,
) -> None:
    current = _execution(tmp_path, "current")
    stale = _execution(tmp_path, "stale")
    _result(current, scored_at="2026-09-20T01:00:00+00:00", readiness_score=90)
    _result(
        stale,
        scored_at="2099-01-01T00:00:00+00:00",
        readiness_score=10,
        scorer_contract_id="olmocr_stage2_v1",
    )
    results, _ = _load_results(tmp_path)
    assert results[0]["readiness_score"] == 90


def test_frozen_inventory_rejects_removed_and_stray_pdf(tmp_path: Path) -> None:
    receipt, pdf_dir, receipt_sha, _ = _make_frozen_inventory(tmp_path, ("tables/a", "tables/b"))
    pairs = expected_pairs_from_frozen_acquisition(
        receipt,
        pdf_dir,
        expected_receipt_sha256=receipt_sha,
        expected_pdf_count=2,
    )
    assert len(pairs) == 8

    removed = pdf_dir / "tables" / "a.pdf"
    removed.unlink()
    with pytest.raises(OlmocrHygieneError, match="missing=.*tables/a"):
        expected_pairs_from_frozen_acquisition(
            receipt,
            pdf_dir,
            expected_receipt_sha256=receipt_sha,
            expected_pdf_count=2,
        )
    removed.write_bytes(b"tables/a")
    stray = pdf_dir / "tables" / "stray.pdf"
    stray.write_bytes(b"stray")
    with pytest.raises(OlmocrHygieneError, match="extra=.*tables/stray"):
        expected_pairs_from_frozen_acquisition(
            receipt,
            pdf_dir,
            expected_receipt_sha256=receipt_sha,
            expected_pdf_count=2,
        )


def test_frozen_inventory_rejects_tampered_pdf_bytes(tmp_path: Path) -> None:
    receipt, pdf_dir, receipt_sha, _ = _make_frozen_inventory(tmp_path, ("tables/a",))
    (pdf_dir / "tables" / "a.pdf").write_bytes(b"tampered")
    with pytest.raises(OlmocrHygieneError, match="PDF bytes differ"):
        expected_pairs_from_frozen_acquisition(
            receipt,
            pdf_dir,
            expected_receipt_sha256=receipt_sha,
            expected_pdf_count=1,
        )


def test_benchmark_inventory_rejects_tampered_assertions(tmp_path: Path) -> None:
    _, pdf_dir, _, _ = _make_frozen_inventory(tmp_path, ("tables/a",))
    test_file = pdf_dir.parent / BENCHMARK_TEST_FILENAMES[0]
    test_file.write_text("changed assertion\n", encoding="utf-8")
    with pytest.raises(OlmocrHygieneError, match="benchmark test bytes differ"):
        verified_benchmark_test_inventory_sha256(
            tmp_path / "acquisition.json",
            pdf_dir.parent,
            expected_receipt_sha256=hashlib.sha256((tmp_path / "acquisition.json").read_bytes()).hexdigest(),
        )


@pytest.mark.parametrize("failure", ["malformed", "missing_field", "missing_file"])
def test_assertion_row_loader_fails_closed(tmp_path: Path, failure: str) -> None:
    bench_data = _make_assertion_inventory(tmp_path / "bench_data")
    target = bench_data / "arxiv_math.jsonl"
    if failure == "malformed":
        target.write_text("{not json}\n", encoding="utf-8")
        match = "malformed"
    elif failure == "missing_field":
        target.write_text(
            json.dumps({"id": "x", "pdf": "arxiv_math/x.pdf"}) + "\n",
            encoding="utf-8",
        )
        match = "missing 'type'"
    else:
        target.unlink()
        match = "missing benchmark assertion file"
    with pytest.raises(RuntimeError, match=match):
        _load_assertion_rows(bench_data, expected_document_count=7)


def test_verified_assertion_inventory_pins_ids_types_and_documents(tmp_path: Path) -> None:
    bench_data = _make_assertion_inventory(tmp_path / "bench_data")
    files = []
    for filename in BENCHMARK_TEST_FILENAMES:
        path = bench_data / filename
        files.append(
            {
                "path": f"bench_data/{filename}",
                "status": "verified",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    receipt = tmp_path / "acquisition.json"
    _write_json(receipt, {"files": files})
    inventory = load_verified_assertion_inventory(
        receipt,
        bench_data,
        expected_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        expected_document_count=7,
    )
    assert inventory["tables/doc-6"] == (("assertion-6", "present"),)
    assert len(inventory) == 7


def test_benchmark_loader_rejects_unsupported_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench_data = _make_assertion_inventory(tmp_path / "bench_data")
    olmocr_module = types.ModuleType("olmocr")
    bench_module = types.ModuleType("olmocr.bench")
    tests_module = types.ModuleType("olmocr.bench.tests")

    def reject(_data: dict) -> object:
        raise ValueError("unsupported type")

    tests_module.load_single_test = reject  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "olmocr", olmocr_module)
    monkeypatch.setitem(sys.modules, "olmocr.bench", bench_module)
    monkeypatch.setitem(sys.modules, "olmocr.bench.tests", tests_module)
    with pytest.raises(RuntimeError, match="unsupported benchmark assertion"):
        load_all_unit_tests(bench_data, expected_document_count=7)


def test_benchmark_loader_preserves_all_frozen_ids_and_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench_data = _make_assertion_inventory(tmp_path / "bench_data")
    olmocr_module = types.ModuleType("olmocr")
    bench_module = types.ModuleType("olmocr.bench")
    tests_module = types.ModuleType("olmocr.bench.tests")
    tests_module.load_single_test = lambda data: SimpleNamespace(  # type: ignore[attr-defined]
        id=data["id"], type=data["type"]
    )
    monkeypatch.setitem(sys.modules, "olmocr", olmocr_module)
    monkeypatch.setitem(sys.modules, "olmocr.bench", bench_module)
    monkeypatch.setitem(sys.modules, "olmocr.bench.tests", tests_module)
    loaded = load_all_unit_tests(bench_data, expected_document_count=7)
    assert len(loaded) == 7
    assert sum(len(tests) for tests in loaded.values()) == 7


def test_benchmark_loader_rejects_loaded_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench_data = _make_assertion_inventory(tmp_path / "bench_data")
    olmocr_module = types.ModuleType("olmocr")
    bench_module = types.ModuleType("olmocr.bench")
    tests_module = types.ModuleType("olmocr.bench.tests")
    tests_module.load_single_test = lambda _data: SimpleNamespace(  # type: ignore[attr-defined]
        id="wrong", type="present"
    )
    monkeypatch.setitem(sys.modules, "olmocr", olmocr_module)
    monkeypatch.setitem(sys.modules, "olmocr.bench", bench_module)
    monkeypatch.setitem(sys.modules, "olmocr.bench.tests", tests_module)
    with pytest.raises(RuntimeError, match="identity/type drift"):
        load_all_unit_tests(bench_data, expected_document_count=7)


def test_incomplete_aggregation_blocks_by_default_and_withholds_track_b(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    out_dir = tmp_path / "out"
    receipt, pdf_dir, receipt_sha, inventory_sha = _make_frozen_inventory(tmp_path, ("tables/doc-1",))
    monkeypatch.setattr(aggregate_olmocr, "FROZEN_OLMOCR_ACQUISITION_SHA", receipt_sha)
    monkeypatch.setattr(aggregate_olmocr, "FROZEN_OLMOCR_N_PDFS", 1)
    monkeypatch.setattr(
        aggregate_olmocr,
        "load_verified_assertion_inventory",
        lambda *args, **kwargs: {"tables/doc-1": (("t1", "present"),)},
    )
    execution = _execution(run_dir, "copy")
    _result(execution, test_inventory_sha=inventory_sha)

    base_args = [
        "--run-dir",
        str(run_dir),
        "--pdf-dir",
        str(pdf_dir),
        "--acquisition-receipt",
        str(receipt),
        "--out-dir",
        str(out_dir),
        "--n-bootstrap",
        "1",
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


def test_strict_aggregate_json_normalizes_nonfinite_values() -> None:
    encoded = aggregate_olmocr._strict_json_text(  # noqa: SLF001
        {"nan": float("nan"), "nested": [float("inf"), -float("inf"), 1.0]}
    )
    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert json.loads(encoded) == {"nan": None, "nested": [None, None, 1.0]}


def test_atomic_result_write_preserves_previous_file_on_publish_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "stage2_olmocr_result.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(run_score_olmocr.os, "replace", fail_replace)
    with pytest.raises(OSError, match="publish failure"):
        _atomic_write_json(target, {"new": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob("*.tmp")) == []


def test_runner_fails_nonterminal_results_unless_partial_mode_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    pdf_dir = tmp_path / "bench_data" / "pdfs"
    bench_dir = pdf_dir.parent
    record_path = run_dir / "tables" / "doc-1" / "marker" / "execution_record.json"
    for path in (run_dir, pdf_dir, bench_dir):
        path.mkdir(parents=True, exist_ok=True)
    record = {
        "canonical_id": "tables/doc-1",
        "parser_id": "marker",
        "exit_status": "EXECUTED",
    }
    _write_json(record_path, record)
    monkeypatch.setattr(run_score_olmocr, "STAGE1_RUN_DIR", run_dir)
    monkeypatch.setattr(run_score_olmocr, "OLMOCR_PDFS_DIR", pdf_dir)
    monkeypatch.setattr(run_score_olmocr, "BENCH_DATA_DIR", bench_dir)
    monkeypatch.setattr(run_score_olmocr, "ROOT", tmp_path)
    monkeypatch.setattr(
        run_score_olmocr, "verified_benchmark_test_inventory_sha256", lambda *a, **k: TEST_INVENTORY_SHA
    )
    monkeypatch.setattr(
        run_score_olmocr,
        "load_verified_assertion_inventory",
        lambda *a, **k: {"tables/doc-1": (("t1", "present"),)},
    )
    monkeypatch.setattr(run_score_olmocr, "expected_pairs_from_frozen_acquisition", lambda *a, **k: set())
    monkeypatch.setattr(
        run_score_olmocr,
        "load_unique_execution_records",
        lambda *a, **k: ([(record_path, record)], SimpleNamespace(files_seen=1, duplicate_files=0)),
    )
    monkeypatch.setattr(
        run_score_olmocr,
        "load_unique_stage2_results",
        lambda *a, **k: ([], SimpleNamespace(duplicate_files=0)),
    )
    monkeypatch.setattr(run_score_olmocr, "_atomic_write_json", lambda *a, **k: None)
    monkeypatch.setattr(
        sys.modules["benchmarks.eval_v1.stage2.score_olmocr"],
        "load_all_unit_tests",
        lambda *a, **k: {"tables/doc-1": [object()]},
    )
    monkeypatch.setattr(
        sys.modules["benchmarks.eval_v1.stage2.score_olmocr"],
        "replay_and_score",
        lambda **kwargs: {
            "canonical_id": "tables/doc-1",
            "parser_id": "marker",
            "status": "REPLAY_DEFECT",
        },
    )

    assert run_score_olmocr.main([]) == 1
    assert run_score_olmocr.main(["--allow-partial-results"]) == 0
