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
STAGE2_SCORER_CONTRACT_ID = "olmocr_stage2_v3"
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
_BENCHMARK_PREFIXES = {
    "arxiv_math.jsonl": "arxiv_math/",
    "headers_footers.jsonl": "headers_footers/",
    "long_tiny_text.jsonl": "long_tiny_text/",
    "multi_column.jsonl": "multi_column/",
    "old_scans.jsonl": "old_scans/",
    "old_scans_math.jsonl": "old_scans_math/",
    "table_tests.jsonl": "tables/",
}
AssertionInventory = dict[str, tuple[tuple[str, str], ...]]
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
                f"{digest}: {', '.join(str(p) for p in paths)}" for digest, paths in sorted(values.items())
            )
            raise OlmocrHygieneError(f"conflicting duplicate {logical_name} hashes for pair {pair}: {detail}")


def _load_verified_receipt(
    acquisition_receipt: Path,
    *,
    expected_receipt_sha256: str,
) -> dict[str, dict[str, Any]]:
    if not acquisition_receipt.is_file():
        raise OlmocrHygieneError(f"frozen acquisition receipt not found: {acquisition_receipt}")
    receipt_bytes = acquisition_receipt.read_bytes()
    actual_receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    if actual_receipt_sha != expected_receipt_sha256:
        raise OlmocrHygieneError(
            "acquisition receipt SHA mismatch; "
            f"expected={expected_receipt_sha256} actual={actual_receipt_sha}"
        )
    try:
        receipt = json.loads(receipt_bytes)
    except Exception as exc:  # noqa: BLE001
        raise OlmocrHygieneError(f"invalid acquisition receipt: {exc}") from exc
    if not isinstance(receipt, dict) or not isinstance(receipt.get("files"), list):
        raise OlmocrHygieneError("invalid acquisition receipt: files must be a list")
    entries: dict[str, dict[str, Any]] = {}
    for entry in receipt["files"]:
        if not isinstance(entry, dict):
            raise OlmocrHygieneError("invalid acquisition receipt file entry")
        path = str(entry.get("path", "")).replace("\\", "/")
        if not path or path in entries:
            raise OlmocrHygieneError(f"invalid or duplicate acquisition receipt path: {path!r}")
        if entry.get("status") != "verified":
            raise OlmocrHygieneError(f"receipt file is not verified: {path}")
        sha = entry.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise OlmocrHygieneError(f"receipt file has invalid sha256: {path}")
        entries[path] = entry
    return entries


def verified_benchmark_test_inventory_sha256(
    acquisition_receipt: Path,
    bench_data_dir: Path,
    *,
    expected_receipt_sha256: str,
) -> str:
    """Verify assertion bytes and hash their frozen receipt identities."""
    entries = _load_verified_receipt(
        acquisition_receipt,
        expected_receipt_sha256=expected_receipt_sha256,
    )
    digest = hashlib.sha256()
    for filename in BENCHMARK_TEST_FILENAMES:
        receipt_path = f"bench_data/{filename}"
        entry = entries.get(receipt_path)
        if entry is None:
            raise OlmocrHygieneError(f"frozen receipt is missing benchmark inventory: {receipt_path}")
        path = bench_data_dir / filename
        if not path.is_file():
            raise OlmocrHygieneError(f"benchmark test inventory is incomplete: missing {path}")
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha != entry["sha256"]:
            raise OlmocrHygieneError(
                f"benchmark test bytes differ from frozen receipt: {path}; "
                f"expected={entry['sha256']} actual={actual_sha}"
            )
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
    return digest.hexdigest()


def assertion_set_sha256(assertions: Iterable[tuple[str, str]]) -> str:
    payload = json.dumps(sorted(assertions), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_verified_assertion_inventory(
    acquisition_receipt: Path,
    bench_data_dir: Path,
    *,
    expected_receipt_sha256: str,
    expected_document_count: int = FROZEN_OLMOCR_N_PDFS,
) -> AssertionInventory:
    """Return exact per-document ``(assertion_id, type)`` tuples after byte verification."""
    verified_benchmark_test_inventory_sha256(
        acquisition_receipt,
        bench_data_dir,
        expected_receipt_sha256=expected_receipt_sha256,
    )
    inventory: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen_ids: set[str] = set()
    for filename, prefix in _BENCHMARK_PREFIXES.items():
        path = bench_data_dir / filename
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OlmocrHygieneError(
                    f"malformed frozen assertion at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise OlmocrHygieneError(f"frozen assertion is not an object at {path}:{line_number}")
            values = [row.get(field) for field in ("id", "pdf", "type")]
            if any(not isinstance(value, str) or not value for value in values):
                raise OlmocrHygieneError(f"frozen assertion missing id/pdf/type at {path}:{line_number}")
            assertion_id, pdf_field, assertion_type = values
            if assertion_id in seen_ids:
                raise OlmocrHygieneError(f"duplicate frozen assertion id: {assertion_id}")
            seen_ids.add(assertion_id)
            if not pdf_field.endswith(".pdf"):
                raise OlmocrHygieneError(f"invalid assertion PDF at {path}:{line_number}")
            canonical_id = pdf_field.removesuffix(".pdf")
            if not canonical_id.startswith(prefix):
                raise OlmocrHygieneError(f"assertion category mismatch at {path}:{line_number}")
            inventory[canonical_id].append((assertion_id, assertion_type))
    if len(inventory) != expected_document_count:
        raise OlmocrHygieneError(
            f"frozen assertions cover {len(inventory)} documents; expected {expected_document_count}"
        )
    return {canonical_id: tuple(assertions) for canonical_id, assertions in inventory.items()}


def terminal_validation_error(
    data: dict[str, Any],
    *,
    expected_scorer_contract_id: str,
    expected_test_inventory_sha256: str,
    expected_assertions_by_document: AssertionInventory,
) -> str | None:
    """Return why a claimed terminal result is unsafe, or ``None``."""
    status = data.get("status")
    if status not in TERMINAL_STAGE2_STATUSES:
        return f"status={status!r} is nonterminal"
    if data.get("stage2_scorer_contract_id") != expected_scorer_contract_id:
        return "stage2 scorer contract mismatch"
    if data.get("stage2_schema_version") != "3":
        return "stage2 schema version mismatch"
    if data.get("benchmark_test_inventory_sha256") != expected_test_inventory_sha256:
        return "benchmark test inventory mismatch"
    canonical_id = data.get("canonical_id")
    expected_assertions = expected_assertions_by_document.get(str(canonical_id))
    if not expected_assertions:
        return "canonical_id has no frozen assertion set"
    if data.get("expected_assertion_count") != len(expected_assertions):
        return "expected assertion count mismatch"
    if data.get("assertion_set_sha256") != assertion_set_sha256(expected_assertions):
        return "assertion set digest mismatch"

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
    if any(not isinstance(test, dict) or not isinstance(test.get("passed"), bool) for test in test_results):
        return "SCORED test_results require boolean passed fields"
    actual_assertions = [(test.get("test_id"), test.get("test_type")) for test in test_results]
    if any(
        not isinstance(assertion_id, str)
        or not assertion_id
        or not isinstance(assertion_type, str)
        or not assertion_type
        for assertion_id, assertion_type in actual_assertions
    ):
        return "SCORED test_results require nonempty test_id/test_type fields"
    if sorted(actual_assertions) != sorted(expected_assertions):
        return "SCORED assertion IDs/types differ from frozen document inventory"
    n_tests = data.get("n_tests")
    n_passed = data.get("n_passed")
    n_failed = data.get("n_failed")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (n_tests, n_passed, n_failed)):
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
    expected_assertions_by_document: AssertionInventory,
) -> bool:
    return (
        terminal_validation_error(
            data,
            expected_scorer_contract_id=expected_scorer_contract_id,
            expected_test_inventory_sha256=expected_test_inventory_sha256,
            expected_assertions_by_document=expected_assertions_by_document,
        )
        is None
    )


def _deduplicate(
    entries: Iterable[tuple[Path, dict[str, Any]]],
    *,
    expected_manifest_sha: str,
    stage2: bool,
    expected_scorer_contract_id: str | None = None,
    expected_test_inventory_sha256: str | None = None,
    expected_assertions_by_document: AssertionInventory | None = None,
) -> tuple[list[tuple[Path, dict[str, Any]]], DeduplicationReport]:
    entries = list(entries)
    groups: dict[tuple[str, str], list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    for path, data in entries:
        groups[_pair(data, path)].append((path, data))

    selected: list[tuple[Path, dict[str, Any]]] = []
    for pair, group in sorted(groups.items()):
        frozen = [
            entry
            for entry in group
            if entry[1].get("stage1_execution_manifest_sha256") == expected_manifest_sha
            and (stage2 or entry[1].get("exit_status") in {"EXECUTED", "DEFECT"})
        ]
        if not frozen:
            paths = ", ".join(str(path) for path, _ in group)
            raise OlmocrHygieneError(
                f"pair {pair} has no record anchored to frozen manifest {expected_manifest_sha}: {paths}"
            )
        if not stage2:
            terminal_statuses = {entry[1].get("exit_status") for entry in frozen}
            if len(terminal_statuses) > 1:
                raise OlmocrHygieneError(
                    f"conflicting duplicate Stage 1 terminal statuses for pair "
                    f"{pair}: {sorted(str(s) for s in terminal_statuses)}"
                )
        conflict_candidates = frozen
        if stage2:
            conflict_candidates = [
                entry
                for entry in frozen
                if entry[1].get("stage2_scorer_contract_id") == expected_scorer_contract_id
                and entry[1].get("benchmark_test_inventory_sha256") == expected_test_inventory_sha256
            ]
        _assert_no_hash_conflict(conflict_candidates, pair)

        def rank(entry: tuple[Path, dict[str, Any]]) -> tuple[Any, ...]:
            path, data = entry
            if stage2:
                current_contract = int(
                    data.get("stage2_scorer_contract_id") == expected_scorer_contract_id
                    and data.get("benchmark_test_inventory_sha256") == expected_test_inventory_sha256
                )
                terminal = int(
                    is_terminal_stage2_result(
                        data,
                        expected_scorer_contract_id=str(expected_scorer_contract_id),
                        expected_test_inventory_sha256=str(expected_test_inventory_sha256),
                        expected_assertions_by_document=expected_assertions_by_document or {},
                    )
                )
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
    entries = [(path, _read_json(path)) for path in sorted(run_dir.rglob("execution_record.json"))]
    return _deduplicate(entries, expected_manifest_sha=expected_manifest_sha, stage2=False)


def load_unique_stage2_results(
    run_dir: Path,
    *,
    expected_manifest_sha: str,
    expected_scorer_contract_id: str,
    expected_test_inventory_sha256: str,
    expected_assertions_by_document: AssertionInventory,
    authoritative_execution_records: Iterable[tuple[Path, dict[str, Any]]],
    filename: str = "stage2_olmocr_result.json",
) -> tuple[list[dict[str, Any]], DeduplicationReport]:
    authoritative = {_pair(data, path): data for path, data in authoritative_execution_records}
    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(run_dir.rglob(filename)):
        data = _read_json(path)
        pair = _pair(data, path)
        execution = authoritative.get(pair)
        if execution is None:
            raise OlmocrHygieneError(f"{path}: no authoritative frozen Stage 1 execution record")
        recovered = {
            "stage1_execution_manifest_sha256": execution.get("stage1_execution_manifest_sha256"),
            "stage1_output_sha256": execution.get("output_sha256"),
            "stage1_exit_status": execution.get("exit_status"),
        }
        for field, value in recovered.items():
            if data.get(field) is not None and data[field] != value:
                raise OlmocrHygieneError(
                    f"{path}: embedded {field} disagrees with authoritative Stage 1 record"
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
        expected_assertions_by_document=expected_assertions_by_document,
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
    entries = _load_verified_receipt(
        acquisition_receipt,
        expected_receipt_sha256=expected_receipt_sha256,
    )
    receipt_ids = []
    pdf_shas: dict[str, str] = {}
    for path, entry in entries.items():
        prefix = "bench_data/pdfs/"
        if path.startswith(prefix) and path.endswith(".pdf"):
            canonical_id = path[len(prefix) : -4]
            receipt_ids.append(canonical_id)
            pdf_shas[canonical_id] = str(entry["sha256"])
    expected_ids = set(receipt_ids)
    if len(expected_ids) != expected_pdf_count:
        raise OlmocrHygieneError(
            f"frozen receipt has {len(expected_ids)} PDF IDs; expected {expected_pdf_count}"
        )
    if not pdf_dir.exists():
        raise OlmocrHygieneError(f"cannot prove completeness: PDF inventory not found: {pdf_dir}")
    actual_ids = {path.relative_to(pdf_dir).with_suffix("").as_posix() for path in pdf_dir.rglob("*.pdf")}
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    if missing or extra:
        raise OlmocrHygieneError(
            "local PDF canonical-ID inventory differs from frozen receipt: "
            f"missing={missing[:5]} ({len(missing)} total), "
            f"extra={extra[:5]} ({len(extra)} total)"
        )
    for canonical_id in sorted(expected_ids):
        pdf_path = pdf_dir / f"{canonical_id}.pdf"
        actual_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        expected_sha = pdf_shas[canonical_id]
        if actual_sha != expected_sha:
            raise OlmocrHygieneError(
                f"PDF bytes differ from frozen receipt: {canonical_id}; "
                f"expected={expected_sha} actual={actual_sha}"
            )
    return {(canonical_id, parser_id) for canonical_id in expected_ids for parser_id in FROZEN_PARSER_IDS}


def build_completeness_report(
    results: Iterable[dict[str, Any]],
    expected_pairs: set[tuple[str, str]],
    *,
    expected_scorer_contract_id: str,
    expected_test_inventory_sha256: str,
    expected_assertions_by_document: AssertionInventory,
) -> dict[str, Any]:
    result_by_pair = {_pair(result, Path("<memory>")): result for result in results}
    observed = set(result_by_pair)
    complete = {
        pair
        for pair, result in result_by_pair.items()
        if is_terminal_stage2_result(
            result,
            expected_scorer_contract_id=expected_scorer_contract_id,
            expected_test_inventory_sha256=expected_test_inventory_sha256,
            expected_assertions_by_document=expected_assertions_by_document,
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
