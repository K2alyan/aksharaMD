"""Deduplication and completeness controls for the olmOCR evaluation.

Filesystem paths are not study identities.  Interrupted/copy-forward runs can
leave several files for the same ``(canonical_id, parser_id)`` pair, so all
Stage 2 consumers must pass through this module before replay or aggregation.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FROZEN_PARSER_IDS = (
    "aksharamd-reference",
    "marker",
    "docling",
    "markitdown",
)
TERMINAL_STAGE2_STATUSES = frozenset({"SCORED", "SKIPPED_DEFECT"})
STAGE2_SCORER_CONTRACT_ID = "olmocr_stage2_v2"
FROZEN_OLMOCR_N_PDFS = 1403
BENCHMARK_TEST_FILENAMES = (
    "arxiv_math.jsonl",
    "headers_footers.jsonl",
    "long_tiny_text.jsonl",
    "multi_column.jsonl",
    "old_scans.jsonl",
    "old_scans_math.jsonl",
    "table_tests.jsonl",
)
_INPUT_HASH_FIELDS = ("source_pdf_sha256", "input_sha256", "pdf_sha256")
_OUTPUT_HASH_FIELDS = ("stage1_output_sha256", "output_sha256")


class OlmocrHygieneError(RuntimeError):
    """The on-disk run cannot be reduced to trustworthy unique pairs."""


@dataclass(frozen=True)
class DeduplicationReport:
    files_seen: int
    unique_pairs: int
    duplicate_files: int


def _pair(data: dict[str, Any], path: Path) -> tuple[str, str]:
    canonical_id = data.get("canonical_id")
    parser_id = data.get("parser_id")
    if not isinstance(canonical_id, str) or not canonical_id:
        raise OlmocrHygieneError(f"{path}: missing canonical_id")
    if not isinstance(parser_id, str) or not parser_id:
        raise OlmocrHygieneError(f"{path}: missing parser_id")
    return canonical_id, parser_id


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise OlmocrHygieneError(f"could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OlmocrHygieneError(f"{path}: JSON root must be an object")
    return data


def _assert_no_hash_conflict(
    entries: list[tuple[Path, dict[str, Any]]],
    pair: tuple[str, str],
) -> None:
    """Reject divergent inputs/outputs; missing legacy fields are ignored."""
    for logical_name, fields in (
        ("input", _INPUT_HASH_FIELDS),
        ("output", _OUTPUT_HASH_FIELDS),
    ):
        values: dict[str, list[Path]] = defaultdict(list)
        for path, data in entries:
            value = next((data.get(field) for field in fields if data.get(field)), None)
            if value is not None:
                values[str(value)].append(path)
        if len(values) > 1:
            detail = "; ".join(
                f"{digest}: {', '.join(str(p) for p in paths)}"
                for digest, paths in sorted(values.items())
            )
            raise OlmocrHygieneError(
                f"conflicting duplicate {logical_name} hashes for pair {pair}: {detail}"
            )


def benchmark_test_inventory_sha256(bench_data_dir: Path) -> str:
    """Hash the complete frozen benchmark assertion inventory."""
    digest = hashlib.sha256()
    for filename in BENCHMARK_TEST_FILENAMES:
        path = bench_data_dir / filename
        if not path.is_file():
            raise OlmocrHygieneError(
                f"benchmark test inventory is incomplete: missing {path}"
            )
        payload = path.read_bytes()
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def terminal_validation_error(
    data: dict[str, Any],
    *,
    expected_scorer_contract_id: str,
    expected_test_inventory_sha256: str,
) -> str | None:
    """Return why a claimed terminal result is unsafe, or ``None``."""
    status = data.get("status")
    if status not in TERMINAL_STAGE2_STATUSES:
        return f"status={status!r} is nonterminal"
    if data.get("stage2_scorer_contract_id") != expected_scorer_contract_id:
        return "stage2 scorer contract mismatch"
    if data.get("stage2_schema_version") != "2":
        return "stage2 schema version mismatch"
    if data.get("benchmark_test_inventory_sha256") != expected_test_inventory_sha256:
        return "benchmark test inventory mismatch"

    if status == "SKIPPED_DEFECT":
        if data.get("stage1_exit_status") != "DEFECT":
            return "SKIPPED_DEFECT is not backed by a frozen Stage 1 DEFECT"
        return None

    if data.get("stage1_exit_status") != "EXECUTED":
        return "SCORED is not backed by a frozen Stage 1 EXECUTED record"
    if data.get("sha_verified") is not True:
        return "SCORED requires sha_verified=true"
    score = data.get("readiness_score")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not 0 <= float(score) <= 100
    ):
        return "SCORED requires finite readiness_score in [0, 100]"
    if data.get("scoring_error") is not None:
        return "SCORED cannot contain scoring_error"

    test_results = data.get("test_results")
    if not isinstance(test_results, list) or not test_results:
        return "SCORED requires nonempty test_results"
    if any(
        not isinstance(test, dict) or not isinstance(test.get("passed"), bool)
        for test in test_results
    ):
        return "SCORED test_results require boolean passed fields"
    n_tests = data.get("n_tests")
    n_passed = data.get("n_passed")
    n_failed = data.get("n_failed")
    if any(isinstance(value, bool) or not isinstance(value, int)
           for value in (n_tests, n_passed, n_failed)):
        return "SCORED test counts must be integers"
    actual_passed = sum(test["passed"] is True for test in test_results)
    actual_failed = sum(test["passed"] is False for test in test_results)
    if (
        n_tests != len(test_results)
        or n_passed != actual_passed
        or n_failed != actual_failed
        or n_passed + n_failed != n_tests
    ):
        return "SCORED test counts disagree with test_results"
    return None


def is_terminal_stage2_result(
    data: dict[str, Any],
    *,
    expected_scorer_contract_id: str,
    expected_test_inventory_sha256: str,
) -> bool:
    return terminal_validation_error(
        data,
        expected_scorer_contract_id=expected_scorer_contract_id,
        expected_test_inventory_sha256=expected_test_inventory_sha256,
    ) is None


def _deduplicate(
    entries: Iterable[tuple[Path, dict[str, Any]]],
    *,
    expected_manifest_sha: str,
    stage2: bool,
    expected_scorer_contract_id: str | None = None,
    expected_test_inventory_sha256: str | None = None,
) -> tuple[list[tuple[Path, dict[str, Any]]], DeduplicationReport]:
    entries = list(entries)
    groups: dict[tuple[str, str], list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    for path, data in entries:
        groups[_pair(data, path)].append((path, data))

    selected: list[tuple[Path, dict[str, Any]]] = []
    for pair, group in sorted(groups.items()):
        frozen = [
            entry for entry in group
            if entry[1].get("stage1_execution_manifest_sha256") == expected_manifest_sha
            and (
                stage2
                or entry[1].get("exit_status") in {"EXECUTED", "DEFECT"}
            )
        ]
        if not frozen:
            paths = ", ".join(str(path) for path, _ in group)
            raise OlmocrHygieneError(
                f"pair {pair} has no record anchored to frozen manifest "
                f"{expected_manifest_sha}: {paths}"
            )
        conflict_candidates = frozen
        if stage2:
            conflict_candidates = [
                entry for entry in frozen
                if entry[1].get("stage2_scorer_contract_id")
                == expected_scorer_contract_id
                and entry[1].get("benchmark_test_inventory_sha256")
                == expected_test_inventory_sha256
            ]
        _assert_no_hash_conflict(conflict_candidates, pair)

        def rank(entry: tuple[Path, dict[str, Any]]) -> tuple[Any, ...]:
            path, data = entry
            if stage2:
                current_contract = int(
                    data.get("stage2_scorer_contract_id")
                    == expected_scorer_contract_id
                    and data.get("benchmark_test_inventory_sha256")
                    == expected_test_inventory_sha256
                )
                terminal = int(is_terminal_stage2_result(
                    data,
                    expected_scorer_contract_id=str(expected_scorer_contract_id),
                    expected_test_inventory_sha256=str(
                        expected_test_inventory_sha256
                    ),
                ))
                completed_at = str(data.get("scored_at") or "")
                return current_contract, terminal, completed_at, path.as_posix()
            else:
                terminal = int(data.get("exit_status") in {"EXECUTED", "DEFECT"})
                completed_at = str(data.get("pair_finished_at") or "")
            return terminal, completed_at, path.as_posix()

        selected.append(max(frozen, key=rank))

    return selected, DeduplicationReport(
        files_seen=len(entries),
        unique_pairs=len(groups),
        duplicate_files=len(entries) - len(groups),
    )


def load_unique_execution_records(
    run_dir: Path,
    *,
    expected_manifest_sha: str,
) -> tuple[list[tuple[Path, dict[str, Any]]], DeduplicationReport]:
    entries = [
        (path, _read_json(path))
        for path in sorted(run_dir.rglob("execution_record.json"))
    ]
    return _deduplicate(
        entries, expected_manifest_sha=expected_manifest_sha, stage2=False
    )


def load_unique_stage2_results(
    run_dir: Path,
    *,
    expected_manifest_sha: str,
    expected_scorer_contract_id: str,
    expected_test_inventory_sha256: str,
    filename: str = "stage2_olmocr_result.json",
) -> tuple[list[dict[str, Any]], DeduplicationReport]:
    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(run_dir.rglob(filename)):
        data = _read_json(path)
        # V1 results initially omitted provenance hashes.  Safely recover them
        # from the colocated, frozen Stage 1 record without rewriting evidence.
        execution_path = path.parent / "execution_record.json"
        if not execution_path.is_file():
            raise OlmocrHygieneError(
                f"{path}: missing colocated frozen Stage 1 execution record"
            )
        execution = _read_json(execution_path)
        if _pair(execution, execution_path) != _pair(data, path):
            raise OlmocrHygieneError(
                f"{path}: identity disagrees with colocated execution record"
            )
        recovered = {
            "stage1_execution_manifest_sha256": execution.get(
                "stage1_execution_manifest_sha256"
            ),
            "stage1_output_sha256": execution.get("output_sha256"),
            "stage1_exit_status": execution.get("exit_status"),
        }
        for field, value in recovered.items():
            if data.get(field) is not None and data[field] != value:
                raise OlmocrHygieneError(
                    f"{path}: embedded {field} disagrees with colocated "
                    "execution record"
                )
            data.setdefault(field, value)
        for field in _INPUT_HASH_FIELDS:
            if execution.get(field):
                data.setdefault("source_pdf_sha256", execution[field])
                break
        entries.append((path, data))

    selected, report = _deduplicate(
        entries,
        expected_manifest_sha=expected_manifest_sha,
        stage2=True,
        expected_scorer_contract_id=expected_scorer_contract_id,
        expected_test_inventory_sha256=expected_test_inventory_sha256,
    )
    return [data for _, data in selected], report


def expected_pairs_from_frozen_acquisition(
    acquisition_receipt: Path,
    pdf_dir: Path,
    *,
    expected_receipt_sha256: str,
    expected_pdf_count: int = FROZEN_OLMOCR_N_PDFS,
) -> set[tuple[str, str]]:
    """Verify the pinned receipt and exact local canonical-ID inventory."""
    if not acquisition_receipt.is_file():
        raise OlmocrHygieneError(
            f"cannot prove completeness: acquisition receipt not found: "
            f"{acquisition_receipt}"
        )
    receipt_bytes = acquisition_receipt.read_bytes()
    actual_receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    if actual_receipt_sha != expected_receipt_sha256:
        raise OlmocrHygieneError(
            "cannot prove completeness: acquisition receipt SHA mismatch; "
            f"expected={expected_receipt_sha256} actual={actual_receipt_sha}"
        )
    try:
        receipt = json.loads(receipt_bytes)
    except Exception as exc:  # noqa: BLE001
        raise OlmocrHygieneError(f"invalid acquisition receipt: {exc}") from exc
    receipt_ids = []
    for entry in receipt.get("files", []):
        path = str(entry.get("path", "")).replace("\\", "/")
        prefix = "bench_data/pdfs/"
        if path.startswith(prefix) and path.endswith(".pdf"):
            if entry.get("status") != "verified":
                raise OlmocrHygieneError(f"receipt PDF is not verified: {path}")
            receipt_ids.append(path[len(prefix):-4])
    if len(receipt_ids) != len(set(receipt_ids)):
        raise OlmocrHygieneError("acquisition receipt contains duplicate PDF IDs")
    expected_ids = set(receipt_ids)
    if len(expected_ids) != expected_pdf_count:
        raise OlmocrHygieneError(
            f"frozen receipt has {len(expected_ids)} PDF IDs; "
            f"expected {expected_pdf_count}"
        )
    if not pdf_dir.exists():
        raise OlmocrHygieneError(
            f"cannot prove completeness: PDF inventory not found: {pdf_dir}"
        )
    actual_ids = {
        path.relative_to(pdf_dir).with_suffix("").as_posix()
        for path in pdf_dir.rglob("*.pdf")
    }
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    if missing or extra:
        raise OlmocrHygieneError(
            "local PDF canonical-ID inventory differs from frozen receipt: "
            f"missing={missing[:5]} ({len(missing)} total), "
            f"extra={extra[:5]} ({len(extra)} total)"
        )
    return {(canonical_id, parser_id) for canonical_id in expected_ids
            for parser_id in FROZEN_PARSER_IDS}


def build_completeness_report(
    results: Iterable[dict[str, Any]],
    expected_pairs: set[tuple[str, str]],
    *,
    expected_scorer_contract_id: str,
    expected_test_inventory_sha256: str,
) -> dict[str, Any]:
    result_by_pair = {_pair(result, Path("<memory>")): result for result in results}
    observed = set(result_by_pair)
    complete = {
        pair for pair, result in result_by_pair.items()
        if is_terminal_stage2_result(
            result,
            expected_scorer_contract_id=expected_scorer_contract_id,
            expected_test_inventory_sha256=expected_test_inventory_sha256,
        )
    }
    missing = sorted(expected_pairs - observed)
    unexpected = sorted(observed - expected_pairs)
    nonterminal = sorted((observed & expected_pairs) - complete)
    return {
        "is_complete": not missing and not unexpected and not nonterminal,
        "n_expected_pairs": len(expected_pairs),
        "n_observed_unique_pairs": len(observed),
        "n_complete_pairs": len(complete & expected_pairs),
        "n_missing_pairs": len(missing),
        "n_nonterminal_pairs": len(nonterminal),
        "n_unexpected_pairs": len(unexpected),
        "missing_pairs": [list(pair) for pair in missing],
        "nonterminal_pairs": [list(pair) for pair in nonterminal],
        "unexpected_pairs": [list(pair) for pair in unexpected],
    }
