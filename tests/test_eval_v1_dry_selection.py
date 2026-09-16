"""Durable invariants over the B1a-5a frozen dry-selection artifacts.

These tests act as a fail-closed audit of the committed pilot manifest,
population checkpoints, aggregate snapshot, and exclusion union. They
replay the selection deterministically from the checkpoints (no network)
and require byte-for-byte agreement with what the pre-existing manifest
records.

The frozen artifacts are part of the repository (they are the study's
selection inputs and outputs). A missing artifact is therefore a hard
failure — the test suite refuses to run rather than quietly pass, so a
frozen study input cannot accidentally be dropped without CI catching it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.eval_v1.corpus_split import Partition, assign_partition
from benchmarks.eval_v1.selection.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    sha256_json,
)
from benchmarks.eval_v1.selection.pilot_selector import (
    DOCLAYNET_TARGET_N,
    FEDERAL_REGISTER_TARGET_N,
    FR_STRATA,
    PMC_OA_TARGET_N,
    make_candidate,
    select_from_dev,
    select_stratified,
)

DOCS = Path("docs/evaluation")

CHECKPOINT_FILES = {
    "pmc_oa":     DOCS / "DEV_PILOT_POPULATION_PMC_V1.json",
    "doclaynet":  DOCS / "DEV_PILOT_POPULATION_DOCLAYNET_V1.json",
    "fr_rule":    DOCS / "DEV_PILOT_POPULATION_FR_RULE_V1.json",
    "fr_prorule": DOCS / "DEV_PILOT_POPULATION_FR_PRORULE_V1.json",
    "fr_notice":  DOCS / "DEV_PILOT_POPULATION_FR_NOTICE_V1.json",
}
MANIFEST_PATH  = DOCS / "DEV_PILOT_MANIFEST_V1.json"
SNAPSHOTS_PATH = DOCS / "DEV_PILOT_POPULATION_SNAPSHOTS.json"
UNION_PATH     = DOCS / "DEV_PILOT_EXCLUSION_UNION.jsonl"

FR_CKPT_TO_STRATUM = {
    "fr_rule": "Rule",
    "fr_prorule": "Proposed Rule",
    "fr_notice": "Notice",
}

REQUIRED_FP_KEYS = {
    "corpus",
    "selection_algorithm_version",
    "source_snapshot_identity_sha256",
    "eligibility_rule_version",
    "exclusion_ledger_sha256",
    "checkpoint_schema_version",
}

# Locked anchors — literal values from PROTOCOL_V1 §8.x and the B1a-5a spec
LOCKED_PMC_SNAPSHOT_UTC = "2026-09-01T01-00Z"
LOCKED_DL_DATASET       = "docling-project/DocLayNet-v1.2"
LOCKED_DL_REVISION      = "0daf93102e2efce76c3e11a274a5e0d0969391d3"
LOCKED_DL_SPLIT         = "train"
LOCKED_DL_N_SHARDS      = 72
LOCKED_FR_GTE           = "2025-01-01"
LOCKED_FR_LTE           = "2025-12-31"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_all_artifacts_present():
    # The frozen B1a-5a artifacts are committed to the repository as
    # study inputs/outputs. If one is missing at test time, that is a
    # regression: the durable selection has been damaged. Fail loudly
    # rather than skipping — a silent skip would let the study's
    # permanence invariant erode without any CI signal.
    for path in [MANIFEST_PATH, SNAPSHOTS_PATH, UNION_PATH, *CHECKPOINT_FILES.values()]:
        assert path.exists(), f"missing required B1a-5a frozen artifact: {path}"


@pytest.fixture(scope="module")
def envelopes() -> dict[str, dict]:
    _require_all_artifacts_present()
    return {
        key: json.loads(path.read_text(encoding="utf-8"))
        for key, path in CHECKPOINT_FILES.items()
    }


@pytest.fixture(scope="module")
def manifest() -> dict:
    _require_all_artifacts_present()
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def excluded_ids() -> set[str]:
    _require_all_artifacts_present()
    ids: set[str] = set()
    with UNION_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cid = row.get("canonical_id")
            if cid:
                ids.add(cid)
    return ids


# ---------------------------------------------------------------------
# Envelope + fingerprint invariants
# ---------------------------------------------------------------------


@pytest.mark.parametrize("key", list(CHECKPOINT_FILES))
def test_checkpoint_envelope_shape_and_payload_sha(envelopes, key):
    body = envelopes[key]
    assert isinstance(body, dict)
    assert set(body).issuperset({"fingerprint", "payload", "payload_sha256"})
    fp = body["fingerprint"]
    missing = REQUIRED_FP_KEYS - set(fp)
    assert not missing, f"{key}: fingerprint missing keys {sorted(missing)}"
    assert fp["checkpoint_schema_version"] == CHECKPOINT_SCHEMA_VERSION
    # This test pins the FROZEN V1 checkpoints, which by construction
    # record selection_algorithm_version="1". The current code constant
    # (SELECTION_ALGORITHM_VERSION) has moved to "2" for the V2
    # instrument; the frozen bytes on disk have not.
    assert fp["selection_algorithm_version"] == "1"
    assert sha256_json(body["payload"]) == body["payload_sha256"]


def test_all_five_checkpoints_agree_on_exclusion_ledger_sha(envelopes):
    shas = {k: env["fingerprint"]["exclusion_ledger_sha256"] for k, env in envelopes.items()}
    ref = next(iter(shas.values()))
    assert all(v == ref for v in shas.values()), f"exclusion_ledger_sha divergent: {shas}"


def test_exclusion_union_file_matches_manifest_and_checkpoints(envelopes, manifest):
    file_sha = _sha256_file(UNION_PATH)
    checkpoint_sha = next(iter(envelopes.values()))["fingerprint"]["exclusion_ledger_sha256"]
    manifest_sha = manifest["exclusion_summary"]["union_ledger_sha256"]
    assert file_sha == checkpoint_sha == manifest_sha


# ---------------------------------------------------------------------
# Locked anchors (would need a bump of selection_algorithm_version to alter)
# ---------------------------------------------------------------------


def test_pmc_inventory_snapshot_utc_locked(envelopes):
    snap = envelopes["pmc_oa"]["payload"]["snapshot"]
    assert snap["snapshot_identity"]["snapshot_utc"] == LOCKED_PMC_SNAPSHOT_UTC


def test_doclaynet_dataset_identity_locked(envelopes):
    snap = envelopes["doclaynet"]["payload"]["snapshot"]
    assert snap["dataset_id"]     == LOCKED_DL_DATASET
    assert snap["revision"]       == LOCKED_DL_REVISION
    assert snap["split"]          == LOCKED_DL_SPLIT
    assert snap["n_shards_scanned"] == LOCKED_DL_N_SHARDS
    keys = [s["shard_key"] for s in snap["per_shard_rows"]]
    assert len(keys) == LOCKED_DL_N_SHARDS
    assert len(set(keys)) == LOCKED_DL_N_SHARDS


@pytest.mark.parametrize("ck,stratum", list(FR_CKPT_TO_STRATUM.items()))
def test_federal_register_stratum_anchors(envelopes, ck, stratum):
    snap = envelopes[ck]["payload"]["snapshot"]
    assert snap["stratum"] == stratum


def test_selection_algorithm_version_is_1(envelopes, manifest):
    assert manifest["selection_algorithm_version"] == "1"
    for env in envelopes.values():
        assert env["fingerprint"]["selection_algorithm_version"] == "1"


# ---------------------------------------------------------------------
# Non-overlap + invariants
# ---------------------------------------------------------------------


def test_no_selected_canonical_id_in_exclusion_union(manifest, excluded_ids):
    hits = [s["canonical_id"] for s in manifest["selected"] if s["canonical_id"] in excluded_ids]
    assert not hits, f"selected IDs overlap exclusion union: {hits}"


def test_selection_target_counts(manifest):
    counts = {"pmc_oa": 0, "doclaynet": 0, "federal_register": 0}
    for s in manifest["selected"]:
        counts[s["corpus"]] += 1
    assert len(manifest["selected"]) == 20
    assert counts["pmc_oa"]           == PMC_OA_TARGET_N          == 8
    assert counts["doclaynet"]        == DOCLAYNET_TARGET_N       == 6
    assert counts["federal_register"] == FEDERAL_REGISTER_TARGET_N == 6


def test_federal_register_stratum_quotas_met(manifest):
    per_stratum = {"Rule": 0, "Proposed Rule": 0, "Notice": 0}
    for s in manifest["selected"]:
        if s["corpus"] == "federal_register":
            per_stratum[s["stratum"]] += 1
    for stratum, target in FR_STRATA.items():
        assert per_stratum[stratum] == target, f"FR[{stratum}] expected {target}, got {per_stratum[stratum]}"


def test_all_selected_are_dev(manifest):
    for s in manifest["selected"]:
        assert assign_partition(s["canonical_id"]) is Partition.DEV, s["canonical_id"]


def test_no_duplicate_canonical_ids(manifest):
    ids = [s["canonical_id"] for s in manifest["selected"]]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------
# Selection replay from checkpoints (the strongest invariant)
# ---------------------------------------------------------------------


def _replay(envelopes: dict, manifest: dict) -> tuple[list, list, dict[str, list]]:
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
    pmc_selected, _ = select_from_dev(pmc_cands, set(), PMC_OA_TARGET_N, "pmc_oa")
    dl_selected, _  = select_from_dev(dl_cands, set(), DOCLAYNET_TARGET_N, "doclaynet")
    fr_selected, _  = select_stratified(
        fr_per_stratum, set(), "federal_register", FR_STRATA
    )
    return pmc_selected, dl_selected, {"selected": fr_selected, "cands": fr_per_stratum}


def test_selection_replay_reproduces_manifest_selected(envelopes, manifest):
    pmc_r, dl_r, fr_r = _replay(envelopes, manifest)
    by_corpus = {"pmc_oa": [], "doclaynet": [], "federal_register": []}
    for s in manifest["selected"]:
        by_corpus[s["corpus"]].append(s)

    expected_pmc = [(s["selection_rank"], s["canonical_id"]) for s in by_corpus["pmc_oa"]]
    got_pmc      = [(i, c.canonical_id) for i, c in enumerate(pmc_r)]
    assert expected_pmc == got_pmc

    expected_dl = [(s["selection_rank"], s["canonical_id"]) for s in by_corpus["doclaynet"]]
    got_dl      = [(i, c.canonical_id) for i, c in enumerate(dl_r)]
    assert expected_dl == got_dl

    expected_fr = sorted(
        (s["stratum"], s["selection_rank"], s["canonical_id"])
        for s in by_corpus["federal_register"]
    )
    got_fr = sorted(
        (s.metadata["type"], i, s.canonical_id)
        for stratum in ("Rule", "Proposed Rule", "Notice")
        for i, s in enumerate(
            [c for c in fr_r["selected"] if c.metadata["type"] == stratum]
        )
    )
    assert expected_fr == got_fr


def test_selection_replay_reproduces_cutoff_neighbors(envelopes, manifest):
    # PMC: candidates list is already DEV-filtered + rank-ordered
    pmc_cands = envelopes["pmc_oa"]["payload"]["eligible_candidates"]
    pmc_lo, pmc_hi = max(0, PMC_OA_TARGET_N - 2), min(len(pmc_cands), PMC_OA_TARGET_N + 5)
    got_pmc_n = [
        {
            "canonical_id":       pmc_cands[i]["canonical_id"],
            "partition_hash_hex": hashlib.sha256(
                pmc_cands[i]["canonical_id"].encode("utf-8")
            ).hexdigest(),
            "rank":     i,
            "selected": i < PMC_OA_TARGET_N,
        }
        for i in range(pmc_lo, pmc_hi)
    ]
    assert sha256_json(manifest["cutoff_neighbors"]["pmc_oa"]) == sha256_json(got_pmc_n)

    # DocLayNet: DEV-filter and rank-sort ourselves
    dl_cands = envelopes["doclaynet"]["payload"]["eligible_candidates"]
    dl_dev = sorted(
        (c for c in dl_cands if assign_partition(c["canonical_id"]) is Partition.DEV),
        key=lambda c: hashlib.sha256(c["canonical_id"].encode("utf-8")).hexdigest(),
    )
    dl_lo, dl_hi = max(0, DOCLAYNET_TARGET_N - 2), min(len(dl_dev), DOCLAYNET_TARGET_N + 5)
    got_dl_n = [
        {
            "canonical_id":       dl_dev[i]["canonical_id"],
            "partition_hash_hex": hashlib.sha256(
                dl_dev[i]["canonical_id"].encode("utf-8")
            ).hexdigest(),
            "rank":     i,
            "selected": i < DOCLAYNET_TARGET_N,
        }
        for i in range(dl_lo, dl_hi)
    ]
    assert sha256_json(manifest["cutoff_neighbors"]["doclaynet"]) == sha256_json(got_dl_n)

    # FR: flat neighbor list with stratum, ranks 0..6 per stratum
    got_fr_flat = []
    for ck, stratum in FR_CKPT_TO_STRATUM.items():
        fr_sorted = sorted(
            envelopes[ck]["payload"]["eligible_candidates"],
            key=lambda c: hashlib.sha256(c["canonical_id"].encode("utf-8")).hexdigest(),
        )
        lo, hi = max(0, 2 - 2), min(len(fr_sorted), 2 + 5)
        for i in range(lo, hi):
            got_fr_flat.append({
                "canonical_id":       fr_sorted[i]["canonical_id"],
                "partition_hash_hex": hashlib.sha256(
                    fr_sorted[i]["canonical_id"].encode("utf-8")
                ).hexdigest(),
                "rank":     i,
                "selected": i < 2,
                "stratum":  stratum,
            })
    assert sha256_json(manifest["cutoff_neighbors"]["federal_register"]) == sha256_json(
        got_fr_flat
    )


# ---------------------------------------------------------------------
# PMC reporting-schema regression guard
# ---------------------------------------------------------------------


def test_pmc_corpus_outcome_uses_lazy_schema(manifest):
    """Regression guard: never let PMC's outcome go back to reporting the
    lazy rank-walk 'materialized before stop' count as `n_eligible`."""
    pmc = manifest["corpus_outcomes"]["pmc_oa"]
    assert pmc["eligibility_evaluation"] == "lazy_deterministic_rank_walk"
    assert pmc["n_eligible"] is None
    assert pmc["n_after_exclusion"] is None
    assert pmc["n_in_dev"] is None
    assert pmc["n_eligible_dev_materialized_before_stop"] == 12
    assert pmc["n_eligible_dev_population_size"] is None
    assert pmc["n_audit_neighbors"] == 4
    assert pmc["n_metadata_records_probed"] == 18
    assert pmc["n_enumerated"] == 9_129_850
    assert pmc["n_selected"] == 8


def test_eager_corpora_use_eager_schema(manifest):
    for name in ("doclaynet", "federal_register"):
        row = manifest["corpus_outcomes"][name]
        assert row["eligibility_evaluation"] == "eager_full_materialization"
        assert row["n_eligible"] is not None
        assert row["n_after_exclusion"] is not None
        assert row["n_in_dev"] is not None
