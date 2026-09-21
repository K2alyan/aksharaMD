"""Stage 2 olmOCR scoring entry point.

Usage
-----
    python -m benchmarks.eval_v1.stage2.run_score_olmocr [--dry-run]

What it does
------------
Walks every ``execution_record.json`` under STAGE1_RUN_DIR, calls
``replay_and_score()``, and writes ``stage2_olmocr_result.json`` next to
each execution record.  Already-completed results (status SCORED or
SKIPPED_DEFECT) are skipped.

GPU parsers (marker, docling)
-----------------------------
This script re-runs the same four parsers that Stage 1 ran, including
``marker`` and ``docling`` which require CUDA.  Run on a machine with a
compatible GPU if those parsers are in scope.  ``aksharamd-reference`` and
``markitdown`` are CPU-only and safe to replay on any machine.

Summary
-------
A JSON summary is written to::

    benchmarks/results/stage2-olmocr-{YYYY-MM-DD}-summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.eval_v1.stage1.execution_manifest import (
    FROZEN_OLMOCR_ACQUISITION_SHA,
    OLMOCR_ACQUISITION_PATH,
)
from benchmarks.eval_v1.stage1.run_track_a_olmocr import MANIFEST_SHA
from benchmarks.eval_v1.stage2.olmocr_hygiene import (
    FROZEN_OLMOCR_N_PDFS,
    STAGE2_SCORER_CONTRACT_ID,
    OlmocrHygieneError,
    expected_pairs_from_frozen_acquisition,
    is_terminal_stage2_result,
    load_unique_execution_records,
    load_unique_stage2_results,
    verified_benchmark_test_inventory_sha256,
)

ROOT = Path(__file__).resolve().parent.parent.parent.parent
STAGE1_RUN_DIR = ROOT / "benchmarks" / "results" / "stage1-track-a-olmocr-2026-09-17" / "olmocr_bench"
OLMOCR_PDFS_DIR = ROOT / "tmp" / "olmocr-full-data" / "bench_data" / "pdfs"
BENCH_DATA_DIR = ROOT / "tmp" / "olmocr-full-data" / "bench_data"

RESULT_FILENAME = "stage2_olmocr_result.json"
# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _now_date() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _progress_line(i: int, total: int, canonical_id: str, parser_id: str, status: str, elapsed: float) -> str:
    return f"[{i}/{total}] {canonical_id} x {parser_id} -> {status} ({elapsed:.1f}s)"


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 2: replay Stage 1 records and score with olmOCR tests."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without running parsers or writing files.",
    )
    parser.add_argument(
        "--parser-id",
        dest="parser_id_filter",
        default=None,
        help="Only process records for this parser_id.",
    )
    args = parser.parse_args(argv)

    # ------------------------------------------------------------------ #
    # Validate paths.
    # ------------------------------------------------------------------ #
    if not STAGE1_RUN_DIR.exists():
        print(f"ERROR: Stage 1 run directory not found: {STAGE1_RUN_DIR}", file=sys.stderr)
        return 1
    if not OLMOCR_PDFS_DIR.exists():
        print(f"ERROR: PDFs directory not found: {OLMOCR_PDFS_DIR}", file=sys.stderr)
        return 1
    if not BENCH_DATA_DIR.exists():
        print(f"ERROR: Bench data directory not found: {BENCH_DATA_DIR}", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------ #
    # Load unit tests (done once).
    # ------------------------------------------------------------------ #
    if not args.dry_run:
        print("Loading olmOCR unit tests …", flush=True)
        from benchmarks.eval_v1.stage2.score_olmocr import load_all_unit_tests  # noqa: PLC0415

        unit_tests = load_all_unit_tests(BENCH_DATA_DIR)
    else:
        unit_tests = {}
    try:
        test_inventory_sha256 = verified_benchmark_test_inventory_sha256(
            OLMOCR_ACQUISITION_PATH,
            BENCH_DATA_DIR,
            expected_receipt_sha256=FROZEN_OLMOCR_ACQUISITION_SHA,
        )
    except OlmocrHygieneError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        expected_pairs_from_frozen_acquisition(
            OLMOCR_ACQUISITION_PATH,
            OLMOCR_PDFS_DIR,
            expected_receipt_sha256=FROZEN_OLMOCR_ACQUISITION_SHA,
            expected_pdf_count=FROZEN_OLMOCR_N_PDFS,
        )
    except OlmocrHygieneError as exc:
        print(f"ERROR: frozen corpus inventory check failed: {exc}", file=sys.stderr)
        return 1
    if not args.dry_run:
        n_loaded_assertions = sum(len(tests) for tests in unit_tests.values())
        print(
            f"  Loaded tests for {len(unit_tests)} canonical_ids "
            f"({n_loaded_assertions} assertions; "
            f"inventory {test_inventory_sha256[:12]}...).",
            flush=True,
        )

    # ------------------------------------------------------------------ #
    # Collect records.
    # ------------------------------------------------------------------ #
    try:
        unique_records, execution_dedup = load_unique_execution_records(
            STAGE1_RUN_DIR, expected_manifest_sha=MANIFEST_SHA
        )
        existing_results, result_dedup = load_unique_stage2_results(
            STAGE1_RUN_DIR,
            expected_manifest_sha=MANIFEST_SHA,
            expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
            expected_test_inventory_sha256=test_inventory_sha256,
            authoritative_execution_records=unique_records,
        )
    except OlmocrHygieneError as exc:
        print(f"ERROR: olmOCR run hygiene check failed: {exc}", file=sys.stderr)
        return 1
    completed_results = {
        (r["canonical_id"], r["parser_id"]): r
        for r in existing_results
        if is_terminal_stage2_result(
            r,
            expected_scorer_contract_id=STAGE2_SCORER_CONTRACT_ID,
            expected_test_inventory_sha256=test_inventory_sha256,
        )
    }
    all_records = unique_records
    if args.parser_id_filter:
        all_records = [entry for entry in all_records if entry[1]["parser_id"] == args.parser_id_filter]

    total = len(all_records)
    print(
        f"Found {execution_dedup.files_seen} execution files -> "
        f"{total} unique pairs ({execution_dedup.duplicate_files} duplicates removed).",
        flush=True,
    )
    if result_dedup.duplicate_files:
        print(
            f"Found and collapsed {result_dedup.duplicate_files} duplicate Stage 2 results.",
            flush=True,
        )

    # ------------------------------------------------------------------ #
    # Process.
    # ------------------------------------------------------------------ #
    from benchmarks.eval_v1.stage2.score_olmocr import replay_and_score  # noqa: PLC0415

    counters: dict[str, int] = {}
    wall_total = 0.0

    for i, (record_path, record) in enumerate(all_records, 1):
        # Derive identifiers for progress display.
        # Layout: STAGE1_RUN_DIR/{canonical_id}/{parser_id}/execution_record.json
        parser_id = record["parser_id"]
        canonical_id = record["canonical_id"]

        existing = completed_results.get((canonical_id, parser_id))
        skip = existing is not None
        existing_status = existing.get("status", "") if existing else ""
        if skip:
            counters[existing_status] = counters.get(existing_status, 0) + 1
            print(_progress_line(i, total, canonical_id, parser_id, f"SKIP({existing_status})", 0.0))
            continue

        if args.dry_run:
            print(_progress_line(i, total, canonical_id, parser_id, "DRY_RUN", 0.0))
            counters["DRY_RUN"] = counters.get("DRY_RUN", 0) + 1
            continue

        t0 = time.monotonic()
        try:
            result = replay_and_score(
                record_path=record_path,
                pdf_dir=OLMOCR_PDFS_DIR,
                unit_tests=unit_tests,
                root=ROOT,
                benchmark_test_inventory_sha256=test_inventory_sha256,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - t0
            wall_total += elapsed
            status = "HARNESS_ERROR"
            counters[status] = counters.get(status, 0) + 1
            print(_progress_line(i, total, canonical_id, parser_id, status, elapsed))
            print(f"  ERROR: {exc}", file=sys.stderr)
            continue

        elapsed = time.monotonic() - t0
        wall_total += elapsed
        status = result.get("status", "UNKNOWN")
        counters[status] = counters.get(status, 0) + 1

        # Write result.
        result_path = record_path.parent / RESULT_FILENAME
        result_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(_progress_line(i, total, canonical_id, parser_id, status, elapsed))

    # ------------------------------------------------------------------ #
    # Summary.
    # ------------------------------------------------------------------ #
    summary = {
        "stage2_summary_schema_version": "2",
        "run_date": _now_date(),
        "stage1_run_dir": str(STAGE1_RUN_DIR),
        "stage2_scorer_contract_id": STAGE2_SCORER_CONTRACT_ID,
        "benchmark_test_inventory_sha256": test_inventory_sha256,
        "total_records": total,
        "status_counts": counters,
        "wall_clock_seconds": round(wall_total, 2),
    }

    summary_path = ROOT / "benchmarks" / "results" / f"stage2-olmocr-{_now_date()}-summary.json"
    if not args.dry_run:
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nSummary written to: {summary_path}")

    print("\nStatus counts:")
    for status, count in sorted(counters.items()):
        print(f"  {status}: {count}")
    print(f"Total wall time: {wall_total:.1f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
