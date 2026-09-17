"""Stage 1 — FinTabNet.c two-tier stratified selection (B1a-8b).

Produces a deterministic stratified sample of 500 tables from the
FinTabNet.c val split, per §6.3 of STUDY_FREEZE_MANIFEST_V1.md
(pre-execution corpus-capability correction: two tiers, not three).

Tier definitions (frozen):
  SIMPLE:   every cell has len(row_nums) == 1 and len(column_nums) == 1.
  COMPOUND: at least one cell has len(row_nums) > 1 or len(column_nums) > 1.
  (Multi-page spanning removed: not measurable from FinTabNet.c V1 GT.)

Selection algorithm:
  1. Scan FinTabNet.c-PDF_Annotations.tar.gz for val-split tables.
  2. Exclude tables with exclude_for_structure = True.
  3. Classify each eligible table as SIMPLE or COMPOUND via frozen definition.
  4. Allocate 500 slots proportionally with largest-remainder rounding.
  5. Within each tier rank by SHA-256(structure_id || freeze_seed) ascending.
  6. Take the allocated N per tier.
  7. Write the selection manifest.

Run:
    python -m benchmarks.eval_v1.selection.stage1_select_fintabnet_c

Outputs:
    docs/evaluation/STAGE1_FINTABNET_C_SELECTION.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.selection.fintabnet_c_complexity import (
    TIER_COMPOUND,
    TIER_SIMPLE,
    classify_table_complexity,
    largest_remainder_allocate,
)

DATASET_ID = "bsmock/FinTabNet.c"
REVISION = "e5673a90b98d02c4832f9e836d72762f0e8933a0"
SPLIT = "val"
TARGET_N = 500
FREEZE_SEED = "6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa"

DEFAULT_ARCHIVE = Path("corpus/eval_v1/fintabnet_c/FinTabNet.c-PDF_Annotations.tar.gz")
DEFAULT_OUTPUT_DIR = Path("docs/evaluation")
SELECTION_OUTPUT_NAME = "STAGE1_FINTABNET_C_SELECTION.json"

TIER_DEFINITIONS = {
    TIER_SIMPLE: (
        "Every cell has len(row_nums) == 1 and len(column_nums) == 1; no spanning cells."
    ),
    TIER_COMPOUND: (
        "At least one cell has len(row_nums) > 1 or len(column_nums) > 1."
    ),
}


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _rank_key(structure_id: str) -> str:
    """SHA-256(structure_id || freeze_seed) for deterministic ranking."""
    return hashlib.sha256((structure_id + FREEZE_SEED).encode()).hexdigest()


def enumerate_val_tables(
    archive_path: Path,
    *,
    verbose: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan PDF_Annotations archive and return all eligible val-split tables.

    Returns (eligible_tables, enumeration_summary).
    Each table dict has: structure_id, tier, n_rows, n_cols, pdf_folder,
    pdf_file_name, pdf_page_index, document_table_index.

    Fails closed if any table's cells are malformed (propagates ValueError).
    """
    n_total = 0
    n_excluded = 0
    n_errors = 0
    eligible: list[dict[str, Any]] = []
    tier_counts: dict[str, int] = defaultdict(int)

    with tarfile.open(archive_path, "r:gz") as arc:
        members = [m for m in arc.getmembers() if m.name.endswith(".json") and m.isfile()]
        if verbose:
            print(
                f"[ftc-select] scanning {len(members):,} JSON files from archive...",
                flush=True,
            )
        for idx, member in enumerate(members):
            if verbose and idx > 0 and idx % 10000 == 0:
                print(
                    f"[ftc-select]   {idx:,}/{len(members):,} files "
                    f"({len(eligible):,} eligible so far)",
                    flush=True,
                )
            f = arc.extractfile(member)
            assert f is not None  # member.isfile() guarantees this
            tables = json.loads(f.read())
            for t in tables:
                if t.get("split") != SPLIT:
                    continue
                n_total += 1
                if t.get("exclude_for_structure", False):
                    n_excluded += 1
                    continue
                cells = t.get("cells")
                if cells is None:
                    n_errors += 1
                    raise ValueError(
                        f"table {t.get('structure_id')!r} in {member.name} "
                        f"has no 'cells' field"
                    )
                tier = classify_table_complexity(cells)
                tier_counts[tier] += 1
                eligible.append({
                    "structure_id": t["structure_id"],
                    "tier": tier,
                    "n_rows": len(t.get("rows") or {}),
                    "n_cols": len(t.get("columns") or {}),
                    "pdf_folder": t.get("pdf_folder", ""),
                    "pdf_file_name": t.get("pdf_file_name", ""),
                    "pdf_page_index": t.get("pdf_page_index"),
                    "document_table_index": t.get("document_table_index"),
                    "document_id": t.get("document_id", ""),
                })

    summary = {
        "archive": str(archive_path),
        "dataset_id": DATASET_ID,
        "revision": REVISION,
        "split": SPLIT,
        "n_total_split_tables": n_total,
        "n_excluded_for_structure": n_excluded,
        "n_eligible": len(eligible),
        "n_per_tier": dict(tier_counts),
    }
    if verbose:
        print(
            f"[ftc-select] enumerated {n_total:,} val tables -> "
            f"{len(eligible):,} eligible "
            f"({n_excluded} excluded by exclude_for_structure)",
            flush=True,
        )
        for tier in (TIER_SIMPLE, TIER_COMPOUND):
            print(
                f"[ftc-select]   {tier}: {tier_counts.get(tier, 0):,}",
                flush=True,
            )
    return eligible, summary


def select_tables(
    eligible: list[dict[str, Any]],
    target_n: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply two-tier stratified selection with largest-remainder allocation.

    Returns (selected, allocation_record).
    """
    by_tier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in eligible:
        by_tier[t["tier"]].append(t)

    tier_counts = {tier: len(by_tier[tier]) for tier in (TIER_SIMPLE, TIER_COMPOUND)}
    allocation = largest_remainder_allocate(tier_counts, target_n)

    selected: list[dict[str, Any]] = []
    allocation_record: dict[str, Any] = {}
    for tier in (TIER_SIMPLE, TIER_COMPOUND):
        tables = by_tier[tier]
        ranked = sorted(tables, key=lambda t: _rank_key(t["structure_id"]))
        n_take = allocation[tier]
        taken = ranked[:n_take]
        selected.extend(taken)
        allocation_record[tier] = {
            "n_eligible": len(tables),
            "proportion": len(tables) / sum(tier_counts.values()),
            "n_allocated": n_take,
            "n_selected": len(taken),
            "definition": TIER_DEFINITIONS[tier],
        }
    return selected, allocation_record


def run(
    archive_path: Path = DEFAULT_ARCHIVE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    verbose: bool = True,
) -> dict[str, Any]:
    """Enumerate, classify, select, write selection manifest. Returns manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if not archive_path.exists():
        raise FileNotFoundError(
            f"PDF_Annotations archive not found: {archive_path}\n"
            "Run: python -m benchmarks.eval_v1.acquisition.fintabnet_c_hf --download"
        )

    eligible, enum_summary = enumerate_val_tables(archive_path, verbose=verbose)

    if verbose:
        print(
            f"[ftc-select] selecting {TARGET_N} tables "
            f"(two-tier stratified, freeze seed)...",
            flush=True,
        )
    selected, alloc_record = select_tables(eligible, TARGET_N)

    manifest = {
        "schema_version": "1",
        "corpus": "fintabnet_c",
        "dataset_id": DATASET_ID,
        "revision": REVISION,
        "split": SPLIT,
        "archive": str(archive_path),
        "freeze_seed": FREEZE_SEED,
        "selection_algorithm": "two_tier_stratified_sha256_rank",
        "tier_definitions": TIER_DEFINITIONS,
        "target_n": TARGET_N,
        "n_selected": len(selected),
        "selected_utc": _now_utc(),
        "enumeration_summary": enum_summary,
        "allocation_by_tier": alloc_record,
        "selected_structure_ids": [t["structure_id"] for t in selected],
        "selected_entries": [
            {
                "structure_id": t["structure_id"],
                "tier": t["tier"],
                "rank_key": _rank_key(t["structure_id"]),
                "n_rows": t["n_rows"],
                "n_cols": t["n_cols"],
                "pdf_folder": t["pdf_folder"],
                "pdf_file_name": t["pdf_file_name"],
                "pdf_page_index": t["pdf_page_index"],
                "document_id": t["document_id"],
            }
            for t in selected
        ],
    }

    out_path = output_dir / SELECTION_OUTPUT_NAME
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    if verbose:
        print(f"[ftc-select] selection manifest -> {out_path}", flush=True)
        print("\n=== STAGE 1 FINTABNET.C SELECTION COMPLETE ===", flush=True)
        print(f"  Selected: {len(selected)}/{TARGET_N}", flush=True)
        for tier in (TIER_SIMPLE, TIER_COMPOUND):
            rec = alloc_record[tier]
            print(
                f"  {tier}: {rec['n_selected']}/{rec['n_allocated']} "
                f"(pool={rec['n_eligible']})",
                flush=True,
            )
        print(f"  Manifest: {out_path}", flush=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--archive",
        type=Path,
        default=DEFAULT_ARCHIVE,
        help="Path to FinTabNet.c-PDF_Annotations.tar.gz (default: %(default)s)",
    )
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    run(archive_path=args.archive, output_dir=args.output_dir, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
