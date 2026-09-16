"""B1a-5a dry-selection orchestrator with phase-level checkpoints.

Corrected 2026-09-15 (Option B):

- Every expensive phase is checkpointed to its own JSON on disk BEFORE
  the next phase begins. If ``python -m ... dry_selection`` fails
  mid-run, subsequent runs reuse valid checkpoints and only recompute
  the failing phase.
- Checkpoint reuse is FAIL-CLOSED: fingerprint mismatch → recompute.
- All HTTP requests are paced and bounded-retry via ``http_policy``.
- ``NetworkExhaustionError`` propagates as a run stop; a transient
  network failure never counts as document ineligibility.
- Population snapshot files are written AFTER their phase succeeds
  and BEFORE the next phase begins, so any mid-run failure preserves
  work up to that point.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.selection.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointFingerprint,
    CheckpointMismatchError,
    load_checkpoint,
    sha256_file,
    sha256_json,
    write_checkpoint,
)
from benchmarks.eval_v1.selection.doclaynet_population import (
    DATASET_ID as DOCLAYNET_DATASET_ID,
)
from benchmarks.eval_v1.selection.doclaynet_population import (
    DOCLAYNET_ELIGIBILITY_RULE_VERSION,
    enumerate_eligible_train_population,
)
from benchmarks.eval_v1.selection.doclaynet_population import (
    N_SHARDS as DOCLAYNET_N_SHARDS,
)
from benchmarks.eval_v1.selection.doclaynet_population import (
    REVISION as DOCLAYNET_REVISION,
)
from benchmarks.eval_v1.selection.doclaynet_population import (
    SPLIT as DOCLAYNET_SPLIT,
)
from benchmarks.eval_v1.selection.federal_register_population import (
    FR_ELIGIBILITY_RULE_VERSION,
    enumerate_stratum_candidates,
)
from benchmarks.eval_v1.selection.federal_register_population import (
    POPULATION_PUBLICATION_DATE_GTE as FR_GTE,
)
from benchmarks.eval_v1.selection.federal_register_population import (
    POPULATION_PUBLICATION_DATE_LTE as FR_LTE,
)
from benchmarks.eval_v1.selection.http_policy import NetworkExhaustionError
from benchmarks.eval_v1.selection.pilot_selector import (
    DOCLAYNET_TARGET_N,
    FEDERAL_REGISTER_TARGET_N,
    FR_STRATA,
    PMC_OA_TARGET_N,
    SELECTION_ALGORITHM_VERSION,
    Candidate,
    CorpusOutcome,
    emit_dry_manifest,
    emit_dry_report,
    load_exclusion_ledger,
    make_candidate,
    select_from_dev,
    select_stratified,
    verify_pilot_invariants,
)
from benchmarks.eval_v1.selection.pmc_oa_population import (
    PMC_ELIGIBILITY_RULE_VERSION,
    PMC_INVENTORY_SNAPSHOT_UTC,
    enumerate_metadata_keys,
    fetch_and_verify_inventory,
    latest_version_per_pmcid,
    rank_walk_eligible_dev,
)

DEFAULT_OUTPUT_DIR = Path("docs/evaluation")

PMC_OA_LEDGER = DEFAULT_OUTPUT_DIR / "PMC_OA_EXCLUSION_LEDGER.jsonl"
DOCLAYNET_LEDGER = DEFAULT_OUTPUT_DIR / "DOCLAYNET_EXCLUSION_LEDGER.jsonl"
FR_LEDGER = DEFAULT_OUTPUT_DIR / "FEDERAL_REGISTER_EXCLUSION_LEDGER.jsonl"

PMC_STOP_AFTER_ELIGIBLE = 12  # 8 selected + 4 audit neighbors


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# --- PMC-OA phase --------------------------------------------------


def _pmc_source_snapshot_identity(inv) -> dict[str, Any]:
    """Structural identity of the frozen PMC-OA inventory snapshot."""
    return {
        "snapshot_utc": inv.snapshot_utc,
        "manifest_url": inv.manifest_url,
        "manifest_sha256": inv.manifest_sha256,
        "manifest_bytes_len": inv.manifest_bytes_len,
        "shards": [
            {
                "key": s["key"],
                "size": s["size"],
                "md5_from_manifest": s["md5_from_manifest"],
                "sha256_computed": s["sha256_computed"],
            }
            for s in inv.shards
        ],
    }


def _run_pmc_phase(
    output_dir: Path,
    excluded_ids: set[str],
    exclusion_ledger_sha256: str,
) -> tuple[list[Candidate], dict[str, Any], dict[str, Any]]:
    ckpt_path = output_dir / "DEV_PILOT_POPULATION_PMC_V1.json"

    print(f"[dry-selection] PMC-OA: verifying inventory {PMC_INVENTORY_SNAPSHOT_UTC}...",
          flush=True)
    inv = fetch_and_verify_inventory()
    source_identity_sha = sha256_json(_pmc_source_snapshot_identity(inv))

    fp = CheckpointFingerprint(
        corpus="pmc_oa",
        selection_algorithm_version=SELECTION_ALGORITHM_VERSION,
        source_snapshot_identity_sha256=source_identity_sha,
        eligibility_rule_version=PMC_ELIGIBILITY_RULE_VERSION,
        exclusion_ledger_sha256=exclusion_ledger_sha256,
    )
    try:
        payload = load_checkpoint(ckpt_path, fp)
        print(f"[dry-selection] PMC-OA: reusing checkpoint {ckpt_path}", flush=True)
        eligible = [
            make_candidate(c["canonical_id"], c["metadata"], "pmc_oa")
            for c in payload["eligible_candidates"]
        ]
        snapshot = payload["snapshot"]
        return eligible, snapshot, payload
    except CheckpointMismatchError as e:
        print(f"[dry-selection] PMC-OA: recomputing (checkpoint invalid: {e})",
              flush=True)

    print("[dry-selection] PMC-OA: enumerating metadata keys (full inventory scan)...",
          flush=True)
    keys = enumerate_metadata_keys(inv)
    print(f"[dry-selection] PMC-OA: {len(keys)} metadata/*.json keys", flush=True)
    latest = latest_version_per_pmcid(keys)
    print(f"[dry-selection] PMC-OA: {len(latest)} distinct PMCIDs after "
          f"latest-version resolution", flush=True)

    print(f"[dry-selection] PMC-OA: rank-walking DEV candidates "
          f"(stop after {PMC_STOP_AFTER_ELIGIBLE} eligible)...", flush=True)

    def on_progress(probed: int, elig: int) -> None:
        print(f"[dry-selection]   PMC-OA probed {probed} ({elig} eligible)",
              flush=True)

    eligible_ranked, walk_result = rank_walk_eligible_dev(
        latest, excluded_ids,
        stop_after_eligible=PMC_STOP_AFTER_ELIGIBLE,
        on_progress=on_progress,
    )
    print(f"[dry-selection] PMC-OA: probed {walk_result.metadata_records_probed} "
          f"records, {walk_result.eligible_records_found_before_stop} eligible",
          flush=True)

    snapshot = {
        "source": "pmc-oa-opendata inventory",
        "snapshot_identity": _pmc_source_snapshot_identity(inv),
        "population_snapshot_total_metadata_records": len(keys),
        "population_distinct_pmcids_after_latest_version_resolution": len(latest),
        "dev_assignment_count_before_metadata_eligibility":
            walk_result.dev_assignment_count_before_metadata_eligibility,
        "excluded_before_rank_walk": walk_result.excluded_before_rank_walk,
        "metadata_records_probed": walk_result.metadata_records_probed,
        "eligible_records_found_before_stop":
            walk_result.eligible_records_found_before_stop,
        "rank_walk_stopping_rule": walk_result.stopping_rule,
        "eligibility_evaluation": "lazy_deterministic_rank_walk",
        "ineligible_head": walk_result.ineligible_head[:20],
    }
    payload = {
        "snapshot": snapshot,
        "eligible_candidates": [
            {"canonical_id": c.canonical_id, "metadata": c.metadata}
            for c in eligible_ranked
        ],
    }
    write_checkpoint(ckpt_path, fp, payload)
    print(f"[dry-selection] PMC-OA: checkpoint -> {ckpt_path}", flush=True)
    return eligible_ranked, snapshot, payload


# --- DocLayNet phase ------------------------------------------------


def _run_doclaynet_phase(
    output_dir: Path,
    excluded_ids: set[str],
    exclusion_ledger_sha256: str,
) -> tuple[list[Candidate], dict[str, Any]]:
    ckpt_path = output_dir / "DEV_PILOT_POPULATION_DOCLAYNET_V1.json"

    source_identity_sha = sha256_json(
        {
            "dataset_id": DOCLAYNET_DATASET_ID,
            "revision": DOCLAYNET_REVISION,
            "split": DOCLAYNET_SPLIT,
            "n_shards": DOCLAYNET_N_SHARDS,
        }
    )
    fp = CheckpointFingerprint(
        corpus="doclaynet",
        selection_algorithm_version=SELECTION_ALGORITHM_VERSION,
        source_snapshot_identity_sha256=source_identity_sha,
        eligibility_rule_version=DOCLAYNET_ELIGIBILITY_RULE_VERSION,
        exclusion_ledger_sha256=exclusion_ledger_sha256,
    )
    try:
        payload = load_checkpoint(ckpt_path, fp)
        print(f"[dry-selection] DocLayNet: reusing checkpoint {ckpt_path}",
              flush=True)
        eligible = [
            make_candidate(c["canonical_id"], c["metadata"], "doclaynet")
            for c in payload["eligible_candidates"]
        ]
        # Filter exclusions defensively — they're already fingerprinted
        # in the checkpoint's ledger SHA, but let this be robust.
        eligible = [c for c in eligible if c.canonical_id not in excluded_ids]
        return eligible, payload["snapshot"]
    except CheckpointMismatchError as e:
        print(f"[dry-selection] DocLayNet: recomputing (checkpoint invalid: {e})",
              flush=True)

    print("[dry-selection] DocLayNet: scanning all 72 train shards...", flush=True)

    def on_progress(i: int, total: int, n_elig: int) -> None:
        print(f"[dry-selection]   DocLayNet shard {i}/{total} ({n_elig} eligible)",
              flush=True)

    all_eligible, dl_pop, dl_ineligible_head = enumerate_eligible_train_population(
        on_progress=on_progress
    )
    # Exclusion removal happens here — not counted as ineligibility.
    eligible = [c for c in all_eligible if c.canonical_id not in excluded_ids]
    print(f"[dry-selection] DocLayNet: {dl_pop.n_rows_total} rows scanned, "
          f"{len(all_eligible)} eligible pages "
          f"({len(all_eligible) - len(eligible)} removed by exclusion ledger)",
          flush=True)

    snapshot = {
        "source": "huggingface_datasets_parquet",
        "dataset_id": DOCLAYNET_DATASET_ID,
        "revision": DOCLAYNET_REVISION,
        "split": DOCLAYNET_SPLIT,
        "n_shards_scanned": dl_pop.n_shards_scanned,
        "n_rows_total": dl_pop.n_rows_total,
        "per_shard_rows": dl_pop.per_shard_rows,
        "eligibility_evaluation": "eager_full_materialization",
        "n_eligible_before_exclusion": len(all_eligible),
        "n_after_exclusion": len(eligible),
        "ineligible_head": dl_ineligible_head[:20],
    }
    payload = {
        "snapshot": snapshot,
        "eligible_candidates": [
            {"canonical_id": c.canonical_id, "metadata": c.metadata}
            for c in eligible
        ],
    }
    write_checkpoint(ckpt_path, fp, payload)
    print(f"[dry-selection] DocLayNet: checkpoint -> {ckpt_path}", flush=True)
    return eligible, snapshot


# --- Federal Register phases (one checkpoint per stratum) ----------

_FR_STRATUM_FILENAMES = {
    "Rule": "DEV_PILOT_POPULATION_FR_RULE_V1.json",
    "Proposed Rule": "DEV_PILOT_POPULATION_FR_PRORULE_V1.json",
    "Notice": "DEV_PILOT_POPULATION_FR_NOTICE_V1.json",
}
_FR_STRATUM_API_ENUM = {"Rule": "RULE", "Proposed Rule": "PRORULE", "Notice": "NOTICE"}


def _fr_source_snapshot_identity() -> dict[str, Any]:
    return {
        "source": "federal_register_api_v1",
        "publication_date_gte": FR_GTE.isoformat(),
        "publication_date_lte": FR_LTE.isoformat(),
    }


def _run_fr_stratum(
    output_dir: Path,
    stratum: str,
    excluded_ids: set[str],
    exclusion_ledger_sha256: str,
) -> tuple[list[Candidate], dict[str, Any]]:
    ckpt_path = output_dir / _FR_STRATUM_FILENAMES[stratum]
    source_identity_sha = sha256_json(
        {**_fr_source_snapshot_identity(), "stratum": stratum}
    )
    fp = CheckpointFingerprint(
        corpus="federal_register",
        selection_algorithm_version=SELECTION_ALGORITHM_VERSION,
        source_snapshot_identity_sha256=source_identity_sha,
        eligibility_rule_version=FR_ELIGIBILITY_RULE_VERSION,
        exclusion_ledger_sha256=exclusion_ledger_sha256,
    )
    try:
        payload = load_checkpoint(ckpt_path, fp)
        print(f"[dry-selection] FR[{stratum}]: reusing checkpoint {ckpt_path}",
              flush=True)
        eligible = [
            make_candidate(c["canonical_id"], c["metadata"], "federal_register")
            for c in payload["eligible_candidates"]
        ]
        return eligible, payload["snapshot"]
    except CheckpointMismatchError as e:
        print(f"[dry-selection] FR[{stratum}]: recomputing "
              f"(checkpoint invalid: {e})", flush=True)

    print(f"[dry-selection] FR[{stratum}]: enumerating...", flush=True)

    def on_progress(display: str, msg: str) -> None:
        print(f"[dry-selection]   FR[{display}] {msg}", flush=True)

    eligible, n_list_rows, ineligible = enumerate_stratum_candidates(
        stratum, _FR_STRATUM_API_ENUM[stratum], excluded_ids, on_progress=on_progress
    )
    print(f"[dry-selection] FR[{stratum}]: {n_list_rows} listed, "
          f"{len(eligible)} eligible", flush=True)

    snapshot = {
        "stratum": stratum,
        "api_enum": _FR_STRATUM_API_ENUM[stratum],
        "n_list_rows": n_list_rows,
        "n_eligible": len(eligible),
        "ineligible_head": ineligible[:20],
        "eligibility_evaluation": "eager_full_materialization",
    }
    payload = {
        "snapshot": snapshot,
        "eligible_candidates": [
            {"canonical_id": c.canonical_id, "metadata": c.metadata}
            for c in eligible
        ],
    }
    write_checkpoint(ckpt_path, fp, payload)
    print(f"[dry-selection] FR[{stratum}]: checkpoint -> {ckpt_path}",
          flush=True)
    return eligible, snapshot


def _run_fr_phase(
    output_dir: Path,
    excluded_ids: set[str],
    exclusion_ledger_sha256: str,
) -> tuple[dict[str, list[Candidate]], dict[str, Any]]:
    per_stratum: dict[str, list[Candidate]] = {}
    per_stratum_snapshot: dict[str, Any] = {}
    for stratum in ("Rule", "Proposed Rule", "Notice"):
        cands, snap = _run_fr_stratum(
            output_dir, stratum, excluded_ids, exclusion_ledger_sha256
        )
        per_stratum[stratum] = cands
        per_stratum_snapshot[stratum] = snap
    snapshot = {
        **_fr_source_snapshot_identity(),
        "per_stratum": per_stratum_snapshot,
        "quota_by_stratum": FR_STRATA,
        "per_type_counts": {k: v["n_list_rows"] for k, v in per_stratum_snapshot.items()},
    }
    return per_stratum, snapshot


# --- Exclusion-union helper ----------------------------------------


def _load_and_write_exclusion_union(output_dir: Path) -> tuple[
    dict[str, set[str]], str, dict[str, Any]
]:
    pmc_excl = load_exclusion_ledger(PMC_OA_LEDGER, "pmc_oa")
    dl_excl = load_exclusion_ledger(DOCLAYNET_LEDGER, "doclaynet")
    fr_excl = load_exclusion_ledger(FR_LEDGER, "federal_register")
    print(f"[dry-selection] exclusions: pmc_oa={len(pmc_excl)} "
          f"doclaynet={len(dl_excl)} federal_register={len(fr_excl)}",
          flush=True)
    union_path = output_dir / "DEV_PILOT_EXCLUSION_UNION.jsonl"
    union_path.parent.mkdir(parents=True, exist_ok=True)
    with union_path.open("w", encoding="utf-8") as fh:
        for e in pmc_excl + dl_excl + fr_excl:
            fh.write(
                json.dumps(
                    {"canonical_id": e.canonical_id, "corpus": e.corpus,
                     "reason": e.reason, "source": e.source},
                    sort_keys=True,
                ) + "\n"
            )
    union_sha = sha256_file(union_path)
    excl_ids = {
        "pmc_oa": {e.canonical_id for e in pmc_excl},
        "doclaynet": {e.canonical_id for e in dl_excl},
        "federal_register": {e.canonical_id for e in fr_excl},
    }
    summary = {
        "union_ledger_path": str(union_path),
        "union_ledger_sha256": union_sha,
        "n_entries_pmc_oa": len(pmc_excl),
        "n_entries_doclaynet": len(dl_excl),
        "n_entries_federal_register": len(fr_excl),
        "sources": {
            "pmc_oa": str(PMC_OA_LEDGER),
            "doclaynet": str(DOCLAYNET_LEDGER),
            "federal_register": str(FR_LEDGER),
        },
    }
    return excl_ids, union_sha, summary


# --- Orchestrator ---------------------------------------------------


def run(output_dir: Path) -> None:
    print(f"[dry-selection] output_dir: {output_dir}", flush=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    excl_ids, union_sha, exclusion_summary = _load_and_write_exclusion_union(output_dir)
    print(f"[dry-selection] exclusion union sha256={union_sha[:12]}...",
          flush=True)

    try:
        pmc_eligible, pmc_snapshot, _pmc_payload = _run_pmc_phase(
            output_dir, excl_ids["pmc_oa"], union_sha
        )
        dl_eligible, dl_snapshot = _run_doclaynet_phase(
            output_dir, excl_ids["doclaynet"], union_sha
        )
        fr_per_stratum, fr_snapshot = _run_fr_phase(
            output_dir, excl_ids["federal_register"], union_sha
        )
    except NetworkExhaustionError as e:
        print(f"\n=== POPULATION_EVALUATION_FAILURE ===\n{e}\n"
              "Selection STOPPED — a transient network condition must not "
              "silently change the selected set. Rerun after the upstream "
              "recovers; valid phase checkpoints will be reused.", flush=True)
        raise

    # Verify all three checkpoints exist before selection.
    for name in (
        "DEV_PILOT_POPULATION_PMC_V1.json",
        "DEV_PILOT_POPULATION_DOCLAYNET_V1.json",
        "DEV_PILOT_POPULATION_FR_RULE_V1.json",
        "DEV_PILOT_POPULATION_FR_PRORULE_V1.json",
        "DEV_PILOT_POPULATION_FR_NOTICE_V1.json",
    ):
        if not (output_dir / name).exists():
            raise RuntimeError(
                f"required checkpoint missing: {name}. Cannot proceed to selection."
            )

    # Aggregate snapshot document for the manifest.
    snapshots = {
        "written_utc": _now_utc(),
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "pmc_oa": pmc_snapshot,
        "doclaynet": dl_snapshot,
        "federal_register": fr_snapshot,
    }
    (output_dir / "DEV_PILOT_POPULATION_SNAPSHOTS.json").write_text(
        json.dumps(snapshots, indent=2, sort_keys=True)
    )

    # --- Selection ---------------------------------------------------
    corpus_outcomes: dict[str, CorpusOutcome] = {}

    print("[dry-selection] running PMC-OA selection...", flush=True)
    # PMC eligible list is already rank-ordered and hard-exclusion-cleaned
    # (exclusion removal happened before the rank walk); pass empty
    # excluded_ids to select_from_dev.
    pmc_selected, pmc_outcome = select_from_dev(
        pmc_eligible, set(), PMC_OA_TARGET_N, "pmc_oa"
    )
    pmc_outcome.n_enumerated = pmc_snapshot[
        "population_distinct_pmcids_after_latest_version_resolution"
    ]
    # PMC-OA is lazy: the full eligible-DEV population size is NOT
    # materialized. select_from_dev set n_eligible/n_after_exclusion/
    # n_in_dev based on the 12-record input list, which would misread
    # as "there are only 12 eligible DEV documents in PMC-OA." Clear
    # them and populate the lazy-walk annotations instead.
    pmc_outcome.n_eligible = None
    pmc_outcome.n_after_exclusion = None
    pmc_outcome.n_in_dev = None
    pmc_outcome.eligibility_evaluation = "lazy_deterministic_rank_walk"
    pmc_outcome.n_metadata_records_probed = pmc_snapshot["metadata_records_probed"]
    pmc_outcome.n_eligible_dev_materialized_before_stop = pmc_snapshot[
        "eligible_records_found_before_stop"
    ]
    pmc_outcome.n_eligible_dev_population_size = None  # NOT MATERIALIZED
    pmc_outcome.n_audit_neighbors = (
        pmc_snapshot["eligible_records_found_before_stop"] - PMC_OA_TARGET_N
    )
    corpus_outcomes["pmc_oa"] = pmc_outcome

    print("[dry-selection] running DocLayNet selection...", flush=True)
    dl_selected, dl_outcome = select_from_dev(
        dl_eligible, set(), DOCLAYNET_TARGET_N, "doclaynet"
    )
    dl_outcome.n_enumerated = dl_snapshot["n_rows_total"]
    corpus_outcomes["doclaynet"] = dl_outcome

    print("[dry-selection] running FR stratified selection...", flush=True)
    fr_selected, fr_outcome = select_stratified(
        fr_per_stratum, set(), "federal_register", FR_STRATA
    )
    fr_outcome.n_enumerated = sum(
        s["n_list_rows"] for s in fr_snapshot["per_stratum"].values()
    )
    corpus_outcomes["federal_register"] = fr_outcome

    print("[dry-selection] verifying pilot invariants...", flush=True)
    all_excl_ids = excl_ids["pmc_oa"] | excl_ids["doclaynet"] | excl_ids["federal_register"]
    verify_pilot_invariants(
        pmc_selected, dl_selected, fr_selected, all_excl_ids, fr_outcome
    )
    print("[dry-selection] invariants OK", flush=True)

    manifest_path = output_dir / "DEV_PILOT_MANIFEST_V1.json"
    emit_dry_manifest(manifest_path, snapshots, corpus_outcomes, exclusion_summary)
    print(f"[dry-selection] manifest -> {manifest_path}", flush=True)

    report_path = output_dir / "DEV_PILOT_DRY_REPORT.md"
    emit_dry_report(
        report_path, snapshots, corpus_outcomes, exclusion_summary, manifest_path
    )
    print(f"[dry-selection] report -> {report_path}", flush=True)

    print("\n=== B1a-5a DRY SELECTION COMPLETE ===", flush=True)
    print(f"  PMC-OA:           {len(pmc_selected)}/{PMC_OA_TARGET_N}", flush=True)
    print(f"  DocLayNet:        {len(dl_selected)}/{DOCLAYNET_TARGET_N}",
          flush=True)
    print(f"  Federal Register: {len(fr_selected)}/{FEDERAL_REGISTER_TARGET_N}",
          flush=True)
    print(f"  Total:            "
          f"{len(pmc_selected) + len(dl_selected) + len(fr_selected)}/20",
          flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = p.parse_args(argv)
    run(output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
