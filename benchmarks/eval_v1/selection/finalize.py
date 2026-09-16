"""B1a-5a manifest + dry-report finalization from frozen checkpoints.

Reads the 5 phase checkpoints, the aggregate population snapshot, and
the exclusion union from disk. Verifies integrity end-to-end without
touching the network. Regenerates DEV_PILOT_MANIFEST_V1.json and
DEV_PILOT_DRY_REPORT.md with corrected PMC reporting terminology
(lazy rank-walk vs eager materialization). Refuses to emit if the
replayed selection differs from the pre-existing manifest by even one
canonical ID or one cutoff neighbor.

Never modifies:

- the five ``DEV_PILOT_POPULATION_*_V1.json`` phase checkpoints
- ``DEV_PILOT_POPULATION_SNAPSHOTS.json``
- ``DEV_PILOT_EXCLUSION_UNION.jsonl``

The checkpoints, aggregate snapshot, and exclusion union are the frozen
selection *inputs*. Only the reporting layer (manifest + dry report)
is being corrected, so the cryptographic chain from frozen inputs to
selected IDs is preserved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.selection.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    sha256_file,
    sha256_json,
)
from benchmarks.eval_v1.selection.pilot_selector import (
    DOCLAYNET_TARGET_N,
    FR_STRATA,
    PMC_OA_TARGET_N,
    SELECTION_ALGORITHM_VERSION,
    emit_dry_manifest,
    emit_dry_report,
    make_candidate,
    select_from_dev,
    select_stratified,
    verify_pilot_invariants,
)

DEFAULT_OUTPUT_DIR = Path("docs/evaluation")

CHECKPOINT_FILES = {
    "pmc_oa":     "DEV_PILOT_POPULATION_PMC_V1.json",
    "doclaynet":  "DEV_PILOT_POPULATION_DOCLAYNET_V1.json",
    "fr_rule":    "DEV_PILOT_POPULATION_FR_RULE_V1.json",
    "fr_prorule": "DEV_PILOT_POPULATION_FR_PRORULE_V1.json",
    "fr_notice":  "DEV_PILOT_POPULATION_FR_NOTICE_V1.json",
}
FR_CKPT_TO_STRATUM = {
    "fr_rule":    "Rule",
    "fr_prorule": "Proposed Rule",
    "fr_notice":  "Notice",
}

REQUIRED_FP_KEYS = frozenset({
    "corpus",
    "selection_algorithm_version",
    "source_snapshot_identity_sha256",
    "eligibility_rule_version",
    "exclusion_ledger_sha256",
    "checkpoint_schema_version",
})


class FinalizationError(RuntimeError):
    """Refusal to emit a corrected report because a durable invariant was violated."""


def _load_and_verify_envelope(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FinalizationError(f"checkpoint missing: {path}")
    body = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(body, dict)
        or {"fingerprint", "payload", "payload_sha256"} - body.keys()
    ):
        raise FinalizationError(f"{path}: not a valid checkpoint envelope")
    fp = body["fingerprint"]
    missing = REQUIRED_FP_KEYS - set(fp.keys())
    if missing:
        raise FinalizationError(f"{path}: fingerprint missing keys {sorted(missing)}")
    if fp["checkpoint_schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        raise FinalizationError(
            f"{path}: checkpoint_schema_version={fp['checkpoint_schema_version']!r} "
            f"expected {CHECKPOINT_SCHEMA_VERSION!r}"
        )
    if fp["selection_algorithm_version"] != SELECTION_ALGORITHM_VERSION:
        raise FinalizationError(
            f"{path}: selection_algorithm_version={fp['selection_algorithm_version']!r} "
            f"expected {SELECTION_ALGORITHM_VERSION!r}"
        )
    recomputed = sha256_json(body["payload"])
    if recomputed != body["payload_sha256"]:
        raise FinalizationError(
            f"{path}: payload_sha256 mismatch — stored "
            f"{body['payload_sha256']!r}, recomputed {recomputed!r}"
        )
    return body


def _verify_shared_exclusion_ledger(envelopes: dict[str, dict[str, Any]]) -> str:
    shas = {k: env["fingerprint"]["exclusion_ledger_sha256"] for k, env in envelopes.items()}
    ref = next(iter(shas.values()))
    if any(v != ref for v in shas.values()):
        raise FinalizationError(f"checkpoints disagree on exclusion_ledger_sha256: {shas}")
    return ref


def _load_exclusion_union(union_path: Path, expected_sha: str) -> set[str]:
    if not union_path.exists():
        raise FinalizationError(f"exclusion union missing: {union_path}")
    got = sha256_file(union_path)
    if got != expected_sha:
        raise FinalizationError(
            f"exclusion union file SHA {got!r} does not match "
            f"checkpoint fingerprints {expected_sha!r}"
        )
    excluded_ids: set[str] = set()
    with union_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cid = row.get("canonical_id")
            if cid:
                excluded_ids.add(cid)
    return excluded_ids


def _selected_id_tuples(manifest: dict[str, Any]) -> list[tuple]:
    return sorted(
        (s["corpus"], s.get("stratum"), s["selection_rank"], s["canonical_id"])
        for s in manifest["selected"]
    )


def finalize(output_dir: Path) -> tuple[str, str]:
    """Regenerate MANIFEST + DRY_REPORT. Return (manifest_sha256, report_sha256).

    Raises FinalizationError with prior manifest restored if replay diverges.
    """
    output_dir = Path(output_dir)

    # 1. Load and verify the five phase checkpoints
    envelopes: dict[str, dict[str, Any]] = {}
    for key, name in CHECKPOINT_FILES.items():
        envelopes[key] = _load_and_verify_envelope(output_dir / name)

    # 2. All five checkpoints must agree on the exclusion ledger SHA
    ref_ledger_sha = _verify_shared_exclusion_ledger(envelopes)

    # 3. The exclusion union file on disk must match that SHA
    excluded_ids = _load_exclusion_union(
        output_dir / "DEV_PILOT_EXCLUSION_UNION.jsonl", ref_ledger_sha
    )

    # 4. Snapshot the pre-existing manifest so we can compare, and
    #    restore it if we later refuse to emit.
    manifest_path = output_dir / "DEV_PILOT_MANIFEST_V1.json"
    if not manifest_path.exists():
        raise FinalizationError(f"pre-existing manifest missing: {manifest_path}")
    prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prior_manifest_bytes = manifest_path.read_bytes()

    # 5. Reconstruct Candidate lists from stored eligible_candidates
    pmc_cands = [
        make_candidate(c["canonical_id"], c["metadata"], "pmc_oa")
        for c in envelopes["pmc_oa"]["payload"]["eligible_candidates"]
    ]
    dl_cands = [
        make_candidate(c["canonical_id"], c["metadata"], "doclaynet")
        for c in envelopes["doclaynet"]["payload"]["eligible_candidates"]
    ]
    fr_per_stratum = {
        FR_CKPT_TO_STRATUM[ck]: [
            make_candidate(c["canonical_id"], c["metadata"], "federal_register")
            for c in envelopes[ck]["payload"]["eligible_candidates"]
        ]
        for ck in FR_CKPT_TO_STRATUM
    }

    # 6. Replay selection. Exclusions were applied during enumeration
    #    for DocLayNet + FR and before the PMC rank-walk, so we pass
    #    empty excluded_ids here — matching the original orchestrator.
    pmc_selected, pmc_outcome = select_from_dev(
        pmc_cands, set(), PMC_OA_TARGET_N, "pmc_oa"
    )
    dl_selected, dl_outcome = select_from_dev(
        dl_cands, set(), DOCLAYNET_TARGET_N, "doclaynet"
    )
    fr_selected, fr_outcome = select_stratified(
        fr_per_stratum, set(), "federal_register", FR_STRATA
    )

    # 7. Cross-invariant check (same as the live orchestrator ran)
    verify_pilot_invariants(
        pmc_selected, dl_selected, fr_selected, excluded_ids, fr_outcome
    )

    # 8. Populate reporting fields with corrected lazy-vs-eager schema
    pmc_snap = envelopes["pmc_oa"]["payload"]["snapshot"]
    pmc_outcome.n_enumerated = pmc_snap[
        "population_distinct_pmcids_after_latest_version_resolution"
    ]
    pmc_outcome.n_eligible = None
    pmc_outcome.n_after_exclusion = None
    pmc_outcome.n_in_dev = None
    pmc_outcome.eligibility_evaluation = "lazy_deterministic_rank_walk"
    pmc_outcome.n_metadata_records_probed = pmc_snap["metadata_records_probed"]
    pmc_outcome.n_eligible_dev_materialized_before_stop = pmc_snap[
        "eligible_records_found_before_stop"
    ]
    pmc_outcome.n_eligible_dev_population_size = None  # NOT MATERIALIZED
    pmc_outcome.n_audit_neighbors = (
        pmc_snap["eligible_records_found_before_stop"] - PMC_OA_TARGET_N
    )

    dl_outcome.n_enumerated = envelopes["doclaynet"]["payload"]["snapshot"]["n_rows_total"]
    dl_outcome.eligibility_evaluation = "eager_full_materialization"

    fr_outcome.n_enumerated = sum(
        envelopes[k]["payload"]["snapshot"]["n_list_rows"]
        for k in FR_CKPT_TO_STRATUM
    )
    fr_outcome.eligibility_evaluation = "eager_full_materialization"

    corpus_outcomes = {
        "pmc_oa": pmc_outcome,
        "doclaynet": dl_outcome,
        "federal_register": fr_outcome,
    }

    # 9. Preserve the aggregate SNAPSHOTS file byte-for-byte by loading
    #    it from disk and passing its contents into the new manifest.
    snapshots_path = output_dir / "DEV_PILOT_POPULATION_SNAPSHOTS.json"
    if not snapshots_path.exists():
        raise FinalizationError(f"aggregate snapshots missing: {snapshots_path}")
    snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))

    # 10. Emit new MANIFEST. exclusion_summary is copied verbatim from
    #     the prior manifest — its content is a function of the
    #     already-verified exclusion union.
    exclusion_summary = prior_manifest["exclusion_summary"]
    emit_dry_manifest(manifest_path, snapshots, corpus_outcomes, exclusion_summary)
    new_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 11. Fail-closed replay-vs-prior comparison. Any drift restores the
    #     prior manifest bytes and refuses.
    prior_ids = _selected_id_tuples(prior_manifest)
    new_ids   = _selected_id_tuples(new_manifest)
    if prior_ids != new_ids:
        manifest_path.write_bytes(prior_manifest_bytes)
        raise FinalizationError(
            "REPLAY DIVERGENCE — selected IDs differ from prior manifest. "
            "Prior manifest restored.\n"
            f"  prior: {prior_ids}\n"
            f"  new:   {new_ids}"
        )
    prior_neighbors_sha = sha256_json(prior_manifest["cutoff_neighbors"])
    new_neighbors_sha   = sha256_json(new_manifest["cutoff_neighbors"])
    if prior_neighbors_sha != new_neighbors_sha:
        manifest_path.write_bytes(prior_manifest_bytes)
        raise FinalizationError(
            "REPLAY DIVERGENCE — cutoff_neighbors differ from prior manifest. "
            f"Prior manifest restored (prior sha={prior_neighbors_sha!r}, "
            f"new sha={new_neighbors_sha!r})."
        )

    # 12. Emit new DRY_REPORT
    report_path = output_dir / "DEV_PILOT_DRY_REPORT.md"
    emit_dry_report(
        report_path, snapshots, corpus_outcomes, exclusion_summary, manifest_path
    )

    return sha256_file(manifest_path), sha256_file(report_path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = p.parse_args(argv)
    try:
        new_manifest_sha, new_report_sha = finalize(args.output_dir)
    except FinalizationError as e:
        print(f"FINALIZATION REFUSED: {e}", file=sys.stderr)
        return 2
    print(f"MANIFEST sha256:   {new_manifest_sha}")
    print(f"DRY_REPORT sha256: {new_report_sha}")
    print("FINALIZATION OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
