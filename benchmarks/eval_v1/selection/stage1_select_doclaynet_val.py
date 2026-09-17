"""Stage 1 — DocLayNet validation-split selection (B1a-8).

Produces a deterministic stratified sample of 280 documents from the
DocLayNet val split, per §6.2 of STUDY_FREEZE_MANIFEST_V1.

Selection algorithm:
1. Enumerate all eligible pages from the 7 val shards (metadata only).
2. Remove any IDs in the DocLayNet exclusion ledger.
3. Stratify by ``doc_category`` proportional to category prevalence in
   the eligible pool.
4. Within each category rank pages by
   ``SHA-256(page_hash || freeze_seed)`` ascending.
5. Take the first ``allocated_n`` pages per category (floor/ceil to hit
   exactly 280 total).
6. Write the population snapshot and selection manifest to
   ``docs/evaluation/``.

Run:
    python -m benchmarks.eval_v1.selection.stage1_select_doclaynet_val

Outputs:
    docs/evaluation/STAGE1_DOCLAYNET_VAL_POPULATION.json
    docs/evaluation/STAGE1_DOCLAYNET_VAL_SELECTION.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.selection.checkpoint import (
    CheckpointFingerprint,
    CheckpointMismatchError,
    load_checkpoint,
    sha256_file,
    sha256_json,
    write_checkpoint,
)
from benchmarks.eval_v1.selection.doclaynet_val_population import (
    DATASET_ID,
    DOCLAYNET_VAL_ELIGIBILITY_RULE_VERSION,
    N_SHARDS,
    REVISION,
    SPLIT,
    STAGE1_TARGET_N_DOCUMENTS,
    enumerate_eligible_val_population,
)
from benchmarks.eval_v1.selection.pilot_selector import (
    load_exclusion_ledger,
    make_candidate,
)

FREEZE_SEED = "6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa"

DEFAULT_OUTPUT_DIR = Path("docs/evaluation")
DOCLAYNET_LEDGER = DEFAULT_OUTPUT_DIR / "DOCLAYNET_EXCLUSION_LEDGER.jsonl"

POPULATION_CHECKPOINT = DEFAULT_OUTPUT_DIR / "STAGE1_DOCLAYNET_VAL_POPULATION.json"
SELECTION_OUTPUT = DEFAULT_OUTPUT_DIR / "STAGE1_DOCLAYNET_VAL_SELECTION.json"


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _rank_key(page_hash: str) -> str:
    """SHA-256(page_hash || freeze_seed) for deterministic ranking."""
    payload = (page_hash + FREEZE_SEED).encode()
    return hashlib.sha256(payload).hexdigest()


def _stratified_select(
    eligible: list[Candidate],
    target_n: int,
) -> tuple[list[Candidate], dict[str, Any]]:
    """Stratify by doc_category, allocate proportionally, rank within each stratum.

    Returns (selected_pages, allocation_record).
    """
    # Group by category.
    by_category: dict[str, list[Candidate]] = defaultdict(list)
    for c in eligible:
        cat = c.metadata.get("doc_category") or "unknown"
        by_category[cat].append(c)

    total_eligible = len(eligible)
    categories = sorted(by_category.keys())

    # Proportional allocation with floor; distribute remainder to largest strata.
    raw_alloc = {
        cat: target_n * len(by_category[cat]) / total_eligible
        for cat in categories
    }
    floor_alloc = {cat: int(v) for cat, v in raw_alloc.items()}
    remainder = target_n - sum(floor_alloc.values())
    # Award remainder to categories with largest fractional parts.
    by_frac = sorted(
        categories,
        key=lambda cat: raw_alloc[cat] - floor_alloc[cat],
        reverse=True,
    )
    for cat in by_frac[:remainder]:
        floor_alloc[cat] += 1

    # Within each category: rank by SHA-256(page_hash || freeze_seed), take first n.
    selected: list[Candidate] = []
    allocation_record: dict[str, Any] = {}
    for cat in categories:
        pages = by_category[cat]
        pages_ranked = sorted(pages, key=lambda c: _rank_key(c.canonical_id))
        n_take = floor_alloc[cat]
        taken = pages_ranked[:n_take]
        selected.extend(taken)
        allocation_record[cat] = {
            "n_eligible": len(pages),
            "proportion": len(pages) / total_eligible,
            "raw_allocation": raw_alloc[cat],
            "n_allocated": n_take,
            "n_selected": len(taken),
        }

    return selected, allocation_record


def run(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = output_dir / "STAGE1_DOCLAYNET_VAL_POPULATION.json"

    # Exclusion ledger.
    ledger_path = output_dir / "DOCLAYNET_EXCLUSION_LEDGER.jsonl"
    if ledger_path.exists():
        excluded_entries = load_exclusion_ledger(ledger_path, "doclaynet")
        excluded_ids: set[str] = {e.canonical_id for e in excluded_entries}
        print(f"[stage1-dl-val] exclusion ledger: {len(excluded_ids)} IDs", flush=True)
    else:
        excluded_ids = set()
        print("[stage1-dl-val] no exclusion ledger found — proceeding with empty exclusion set",
              flush=True)

    source_identity_sha = sha256_json({
        "dataset_id": DATASET_ID,
        "revision": REVISION,
        "split": SPLIT,
        "n_shards": N_SHARDS,
    })
    ledger_sha = sha256_file(ledger_path) if ledger_path.exists() else sha256_json({})
    fp = CheckpointFingerprint(
        corpus="doclaynet_val",
        selection_algorithm_version="stage1-v1",
        source_snapshot_identity_sha256=source_identity_sha,
        eligibility_rule_version=DOCLAYNET_VAL_ELIGIBILITY_RULE_VERSION,
        exclusion_ledger_sha256=ledger_sha,
    )
    try:
        payload = load_checkpoint(ckpt_path, fp)
        print(f"[stage1-dl-val] reusing population checkpoint {ckpt_path}", flush=True)
        all_eligible = [
            make_candidate(c["canonical_id"], c["metadata"], "doclaynet")
            for c in payload["eligible_candidates"]
        ]
        snapshot = payload["snapshot"]
    except (CheckpointMismatchError, FileNotFoundError) as e:
        print(f"[stage1-dl-val] scanning val shards ({e})", flush=True)

        def on_progress(i: int, total: int, n_elig: int) -> None:
            print(f"[stage1-dl-val]   shard {i}/{total} ({n_elig} eligible so far)",
                  flush=True)

        all_eligible, dl_pop, ineligible_head = enumerate_eligible_val_population(
            on_progress=on_progress
        )
        print(
            f"[stage1-dl-val] scanned {dl_pop.n_rows_total} rows, "
            f"{len(all_eligible)} pages eligible",
            flush=True,
        )
        snapshot = {
            "source": "huggingface_datasets_parquet",
            "dataset_id": DATASET_ID,
            "revision": REVISION,
            "split": SPLIT,
            "n_shards_scanned": dl_pop.n_shards_scanned,
            "n_rows_total": dl_pop.n_rows_total,
            "per_shard_rows": dl_pop.per_shard_rows,
            "n_eligible_before_exclusion": len(all_eligible),
            "ineligible_head": ineligible_head[:20],
        }
        payload = {
            "snapshot": snapshot,
            "eligible_candidates": [
                {"canonical_id": c.canonical_id, "metadata": c.metadata}
                for c in all_eligible
            ],
        }
        write_checkpoint(ckpt_path, fp, payload)
        print(f"[stage1-dl-val] population checkpoint -> {ckpt_path}", flush=True)

    # Apply exclusion ledger.
    eligible = [c for c in all_eligible if c.canonical_id not in excluded_ids]
    n_excluded = len(all_eligible) - len(eligible)
    print(
        f"[stage1-dl-val] after exclusion: {len(eligible)} eligible "
        f"({n_excluded} removed)",
        flush=True,
    )

    # Stratified selection.
    print(
        f"[stage1-dl-val] selecting {STAGE1_TARGET_N_DOCUMENTS} documents "
        f"(stratified by doc_category)...",
        flush=True,
    )
    selected, alloc_record = _stratified_select(eligible, STAGE1_TARGET_N_DOCUMENTS)
    print(f"[stage1-dl-val] selected {len(selected)} pages", flush=True)
    for cat, rec in sorted(alloc_record.items()):
        print(
            f"[stage1-dl-val]   {cat}: {rec['n_selected']}/{rec['n_allocated']} "
            f"(pool={rec['n_eligible']})",
            flush=True,
        )

    # Write selection manifest.
    selection_manifest = {
        "schema_version": "1",
        "corpus": "doclaynet",
        "split": SPLIT,
        "dataset_id": DATASET_ID,
        "dataset_revision": REVISION,
        "freeze_seed": FREEZE_SEED,
        "selection_algorithm": "stratified_by_doc_category_sha256_rank",
        "target_n_documents": STAGE1_TARGET_N_DOCUMENTS,
        "n_selected": len(selected),
        "selected_utc": _now_utc(),
        "allocation_by_category": alloc_record,
        "population_snapshot_path": str(ckpt_path),
        "selected_page_hashes": [c.canonical_id for c in selected],
        "selected_entries": [
            {
                "page_hash": c.canonical_id,
                "rank_key": _rank_key(c.canonical_id),
                **c.metadata,
            }
            for c in selected
        ],
    }
    sel_path = output_dir / "STAGE1_DOCLAYNET_VAL_SELECTION.json"
    sel_path.write_text(json.dumps(selection_manifest, indent=2, sort_keys=True))
    print(f"[stage1-dl-val] selection manifest -> {sel_path}", flush=True)
    print(f"\n=== STAGE 1 DOCLAYNET VAL SELECTION COMPLETE ===", flush=True)
    print(f"  Selected: {len(selected)}/{STAGE1_TARGET_N_DOCUMENTS}", flush=True)
    print(f"  Manifest: {sel_path}", flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = p.parse_args(argv)
    run(output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
