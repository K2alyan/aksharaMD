"""Deduplication and completeness controls for the olmOCR evaluation.

Filesystem paths are not study identities.  Interrupted/copy-forward runs can
leave several files for the same ``(canonical_id, parser_id)`` pair, so all
Stage 2 consumers must pass through this module before replay or aggregation.
"""
from __future__ import annotations

import json
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


def _deduplicate(
    entries: Iterable[tuple[Path, dict[str, Any]]],
    *,
    expected_manifest_sha: str,
    stage2: bool,
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
        _assert_no_hash_conflict(frozen, pair)

        def rank(entry: tuple[Path, dict[str, Any]]) -> tuple[int, str, str]:
            path, data = entry
            if stage2:
                terminal = int(data.get("status") in TERMINAL_STAGE2_STATUSES)
                completed_at = str(data.get("scored_at") or "")
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
    filename: str = "stage2_olmocr_result.json",
) -> tuple[list[dict[str, Any]], DeduplicationReport]:
    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(run_dir.rglob(filename)):
        data = _read_json(path)
        # V1 results initially omitted provenance hashes.  Safely recover them
        # from the colocated, frozen Stage 1 record without rewriting evidence.
        execution_path = path.parent / "execution_record.json"
        if execution_path.exists():
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
        entries, expected_manifest_sha=expected_manifest_sha, stage2=True
    )
    return [data for _, data in selected], report


def expected_pairs_from_pdfs(pdf_dir: Path) -> set[tuple[str, str]]:
    if not pdf_dir.exists():
        raise OlmocrHygieneError(
            f"cannot prove completeness: PDF inventory not found: {pdf_dir}"
        )
    canonical_ids = {
        path.relative_to(pdf_dir).with_suffix("").as_posix()
        for path in pdf_dir.rglob("*.pdf")
    }
    if not canonical_ids:
        raise OlmocrHygieneError(
            f"cannot prove completeness: no PDFs found under {pdf_dir}"
        )
    return {(canonical_id, parser_id) for canonical_id in canonical_ids
            for parser_id in FROZEN_PARSER_IDS}


def build_completeness_report(
    results: Iterable[dict[str, Any]],
    expected_pairs: set[tuple[str, str]],
) -> dict[str, Any]:
    result_by_pair = {_pair(result, Path("<memory>")): result for result in results}
    observed = set(result_by_pair)
    complete = {
        pair for pair, result in result_by_pair.items()
        if result.get("status") in TERMINAL_STAGE2_STATUSES
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
