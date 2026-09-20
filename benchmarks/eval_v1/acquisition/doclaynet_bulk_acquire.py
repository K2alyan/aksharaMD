"""Bulk acquisition of DocLayNet pages selected for Stage 1 Track A.

Reads ``docs/evaluation/STAGE1_DOCLAYNET_VAL_SELECTION.json``, groups the
280 selected page_hashes by shard_key (7 shards), and downloads each shard
once using streaming Parquet range reads.  For each target page it calls
:func:`acquire_page` to write the per-page cache under
``corpus/eval_v1/doclaynet/<page_hash>/``.

Usage::

    python -m benchmarks.eval_v1.acquisition.doclaynet_bulk_acquire

Flags::

    --dry-run       List pages/shards without downloading anything.
    --shard SHARD   Restrict to one shard_key (repeat for multiple).
    --selection     Path to the selection JSON (default: auto-detected).
    --corpus-dir    Target corpus directory (default: corpus/eval_v1/doclaynet).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------

def _find_root() -> Path:
    candidate = Path(__file__).resolve()
    for _ in range(8):
        if (candidate / "pyproject.toml").exists():
            return candidate
        candidate = candidate.parent
    raise RuntimeError("Could not locate repository root (pyproject.toml not found)")


ROOT = _find_root()
DEFAULT_SELECTION = ROOT / "docs" / "evaluation" / "STAGE1_DOCLAYNET_VAL_SELECTION.json"
DEFAULT_CORPUS_DIR = ROOT / "corpus" / "eval_v1" / "doclaynet"


# ---------------------------------------------------------------------------
# Core acquisition logic
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_selection(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Load selection file; return (locked_revision_sha, selected_entries)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    revision: str = data["dataset_revision"]
    entries: list[dict[str, Any]] = data["selected_entries"]
    return revision, entries


def acquire_shard(
    shard_key: str,
    target_hashes: list[str],
    revision: str,
    corpus_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    """Download one shard and acquire all target pages from it.

    Returns mapping of page_hash -> status ("acquired" | "cached" | "error:<msg>").
    """
    from benchmarks.eval_v1.acquisition.doclaynet_hf import (
        AcquiredPage,
        AcquisitionError,
        HfDatasetRef,
        acquire_page,
        build_page,
        iter_pages_in_row_group,
        open_parquet_shard,
    )

    # Build a frozen HfDatasetRef from the locked revision (no live API call
    # per-shard; the caller may optionally verify against live before calling).
    ref = HfDatasetRef(
        dataset_id="docling-project/DocLayNet-v1.2",
        sha=revision,
        last_modified="",  # not needed for acquisition
        api_url="https://huggingface.co/api/datasets/docling-project/DocLayNet-v1.2",
    )

    target_set = set(target_hashes)
    results: dict[str, str] = {}

    print(f"[shard] {shard_key} — {len(target_hashes)} target pages (rev {revision[:12]}...)")

    if dry_run:
        for ph in target_hashes:
            page_dir = corpus_dir / ph
            cached = (page_dir / "manifest.json").exists()
            status = "cached" if cached else "DRY_RUN"
            results[ph] = status
            print(f"  {ph[:24]}... -> {status}")
        return results

    # Open the shard for streaming range reads (footer + selected row groups).
    print(f"  Opening shard (streaming)...")
    pf = open_parquet_shard(shard_key, revision=revision)
    n_rg = pf.metadata.num_row_groups
    print(f"  {n_rg} row groups, {pf.metadata.num_rows} rows total")

    # Pass 1: lightweight metadata scan to locate target page_hashes.
    hash_to_rg_row: dict[str, tuple[int, int]] = {}
    for rg in range(n_rg):
        tbl = pf.read_row_group(rg, columns=["metadata"])
        md_col = tbl.column("metadata").to_pylist()
        for row_i, md in enumerate(md_col):
            ph = (md or {}).get("page_hash")
            if ph and ph in target_set and ph not in hash_to_rg_row:
                hash_to_rg_row[ph] = (rg, row_i)

    found = set(hash_to_rg_row.keys())
    missing = target_set - found
    if missing:
        for ph in missing:
            msg = f"error:page_hash_not_in_shard:{shard_key}"
            results[ph] = msg
            print(f"  MISSING {ph[:24]}... -> {msg}")

    # Pass 2: group targets by row group to avoid re-reading the same rg twice.
    rg_to_hashes: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for ph, (rg, row_i) in hash_to_rg_row.items():
        rg_to_hashes[rg].append((row_i, ph))

    for rg, row_pairs in sorted(rg_to_hashes.items()):
        target_row_indices = {row_i for row_i, _ in row_pairs}
        row_i_to_hash = {row_i: ph for row_i, ph in row_pairs}
        print(f"  Row group {rg}: fetching {len(row_pairs)} page(s) (image+pdf columns)...")

        rows = list(iter_pages_in_row_group(pf, rg, include_image=True, include_pdf=True))
        for row_i, ph in row_pairs:
            if row_i >= len(rows):
                results[ph] = f"error:row_index_out_of_range:{row_i}>={len(rows)}"
                print(f"    {ph[:24]}... -> {results[ph]}")
                continue
            row = rows[row_i]
            # Integrity check
            got_hash = (row["metadata"] or {}).get("page_hash")
            if got_hash != ph:
                results[ph] = f"error:hash_mismatch:expected={ph[:16]} got={str(got_hash)[:16]}"
                print(f"    {ph[:24]}... -> {results[ph]}")
                continue

            try:
                page = build_page(row)
                discovery = {
                    "discovery_method": "bulk_acquire_from_selection",
                    "selection_file": str(DEFAULT_SELECTION.relative_to(ROOT)),
                    "shard_key": shard_key,
                    "row_group": rg,
                    "row_index_in_rg": row_i,
                }
                acquired = acquire_page(
                    page,
                    ref,
                    shard_key=shard_key,
                    shard_sha256=None,  # streaming path; shard SHA not computed
                    root=corpus_dir,
                    split="validation",
                    discovery_provenance=discovery,
                    selection_role="corpus-eval-v1-stage1-doclaynet-bulk",
                )
                results[ph] = "acquired"
                print(f"    {ph[:24]}... -> acquired ({len(page.annotations)} annotations)")
            except Exception as exc:  # noqa: BLE001
                results[ph] = f"error:{type(exc).__name__}:{str(exc)[:80]}"
                print(f"    {ph[:24]}... -> {results[ph]}")

    return results


def run(
    selection_path: Path,
    corpus_dir: Path,
    *,
    shard_filter: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    """Acquire all selected DocLayNet pages. Returns exit code (0=success)."""
    print(f"[bulk-acquire] Selection: {selection_path}")
    print(f"[bulk-acquire] Corpus dir: {corpus_dir}")
    print(f"[bulk-acquire] Dry run: {dry_run}")
    print(f"[bulk-acquire] Started: {_now_utc()}")

    revision, entries = _load_selection(selection_path)
    print(f"[bulk-acquire] Dataset revision (locked): {revision}")
    print(f"[bulk-acquire] Total selected pages: {len(entries)}")

    # Group by shard_key.
    shard_to_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        sk = e["shard_key"]
        if shard_filter and sk not in shard_filter:
            continue
        shard_to_entries[sk].append(e)

    print(f"[bulk-acquire] Shards to process: {len(shard_to_entries)}")

    all_results: dict[str, str] = {}
    for shard_key, shard_entries in sorted(shard_to_entries.items()):
        target_hashes = [e["page_hash"] for e in shard_entries]
        shard_results = acquire_shard(
            shard_key,
            target_hashes,
            revision,
            corpus_dir,
            dry_run=dry_run,
        )
        all_results.update(shard_results)

    # Summary
    n_acquired = sum(1 for v in all_results.values() if v == "acquired")
    n_cached = sum(1 for v in all_results.values() if v == "cached")
    n_dry = sum(1 for v in all_results.values() if v == "DRY_RUN")
    n_errors = sum(1 for v in all_results.values() if v.startswith("error:"))
    n_total = len(all_results)

    print()
    print("=" * 60)
    print(f"[bulk-acquire] DONE — {_now_utc()}")
    if dry_run:
        print(f"  DRY RUN: {n_dry} to-fetch, {n_cached} already cached")
    else:
        print(f"  acquired:  {n_acquired}")
        print(f"  cached:    {n_cached}")
        print(f"  errors:    {n_errors}")
        print(f"  total:     {n_total}")

    if n_errors:
        print("\nErrors:")
        for ph, status in all_results.items():
            if status.startswith("error:"):
                print(f"  {ph[:24]}... -> {status}")
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--selection",
        type=Path,
        default=DEFAULT_SELECTION,
        help="Path to STAGE1_DOCLAYNET_VAL_SELECTION.json",
    )
    p.add_argument(
        "--corpus-dir",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help="Corpus root directory",
    )
    p.add_argument(
        "--shard",
        action="append",
        dest="shards",
        metavar="SHARD_KEY",
        help="Restrict to this shard (repeat for multiple)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fetched without downloading",
    )
    args = p.parse_args(argv)
    return run(
        args.selection,
        args.corpus_dir,
        shard_filter=args.shards,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
