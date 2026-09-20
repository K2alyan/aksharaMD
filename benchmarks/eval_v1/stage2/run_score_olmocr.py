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

ROOT = Path(__file__).resolve().parent.parent.parent.parent
STAGE1_RUN_DIR = (
    ROOT / "benchmarks" / "results" / "stage1-track-a-olmocr-2026-09-17" / "olmocr_bench"
)
OLMOCR_PDFS_DIR = ROOT / "tmp" / "olmocr-full-data" / "bench_data" / "pdfs"
BENCH_DATA_DIR = ROOT / "tmp" / "olmocr-full-data" / "bench_data"

RESULT_FILENAME = "stage2_olmocr_result.json"
RESUMABLE_STATUSES = {"SCORED", "SKIPPED_DEFECT"}


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _now_date() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _collect_record_paths(run_dir: Path) -> list[Path]:
    """Return all execution_record.json paths under run_dir, sorted."""
    return sorted(run_dir.rglob("execution_record.json"))


def _should_skip(record_path: Path) -> tuple[bool, str]:
    """Return (skip, existing_status) if a valid resumable result exists."""
    result_path = record_path.parent / RESULT_FILENAME
    if not result_path.exists():
        return False, ""
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        status = data.get("status", "")
        if status in RESUMABLE_STATUSES:
            return True, status
        return False, status
    except Exception:  # noqa: BLE001
        return False, ""


def _progress_line(i: int, total: int, canonical_id: str, parser_id: str,
                   status: str, elapsed: float) -> str:
    return (
        f"[{i}/{total}] {canonical_id} x {parser_id} -> {status} ({elapsed:.1f}s)"
    )


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
        print(
            f"  Loaded tests for {len(unit_tests)} canonical_ids.",
            flush=True,
        )
    else:
        unit_tests = {}

    # ------------------------------------------------------------------ #
    # Collect records.
    # ------------------------------------------------------------------ #
    all_records = _collect_record_paths(STAGE1_RUN_DIR)
    if args.parser_id_filter:
        all_records = [
            p for p in all_records
            if p.parent.name == args.parser_id_filter
        ]

    total = len(all_records)
    print(f"Found {total} execution records.", flush=True)

    # ------------------------------------------------------------------ #
    # Process.
    # ------------------------------------------------------------------ #
    from benchmarks.eval_v1.stage2.score_olmocr import replay_and_score  # noqa: PLC0415

    counters: dict[str, int] = {}
    wall_total = 0.0

    for i, record_path in enumerate(all_records, 1):
        # Derive identifiers for progress display.
        # Layout: STAGE1_RUN_DIR/{canonical_id}/{parser_id}/execution_record.json
        parser_id = record_path.parent.name
        # canonical_id may be multi-segment (e.g. arxiv_math/2502.15977_pg21)
        # Reconstruct from path relative to STAGE1_RUN_DIR.
        rel = record_path.relative_to(STAGE1_RUN_DIR)
        # rel = canonical_id_parts... / parser_id / execution_record.json
        parts = list(rel.parts)
        # last part is "execution_record.json", second-to-last is parser_id
        canonical_id = "/".join(parts[:-2])

        skip, existing_status = _should_skip(record_path)
        if skip:
            counters[existing_status] = counters.get(existing_status, 0) + 1
            print(_progress_line(i, total, canonical_id, parser_id,
                                 f"SKIP({existing_status})", 0.0))
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
        "stage2_summary_schema_version": "1",
        "run_date": _now_date(),
        "stage1_run_dir": str(STAGE1_RUN_DIR),
        "total_records": total,
        "status_counts": counters,
        "wall_clock_seconds": round(wall_total, 2),
    }

    summary_path = (
        ROOT
        / "benchmarks"
        / "results"
        / f"stage2-olmocr-{_now_date()}-summary.json"
    )
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
