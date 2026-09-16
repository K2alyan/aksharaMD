# B1a-5a Finalization Provenance

This record documents the reporting correction applied to the B1a-5a dry
selection manifest and human-readable report after the initial dry
selection run of 2026-09-15T15:08:10+00:00. The correction is scoped to
terminology in the *reporting* layer only. The selected 20 canonical IDs,
the cutoff-neighbor sets, the population checkpoints, the aggregate
population snapshots, and the exclusion union are all left byte-for-byte
unchanged.

## What was corrected

Prior to the correction, the manifest's `corpus_outcomes.pmc_oa` block
carried:

```
n_enumerated:      9129850
n_eligible:        12
n_after_exclusion: 12
n_in_dev:          12
n_selected:        8
```

This reads as *"the PMC-OA DEV-eligible population is 12"*, which is
false. The PMC-OA phase uses a lazy deterministic rank-walk that stops
after the first 12 eligible DEV candidates are materialized (8 for the
selection + 4 audit neighbors). The full eligible DEV population was
**never materialized**, so no honest number exists for
`n_eligible_dev_population_size`.

The corrected `corpus_outcomes.pmc_oa` block distinguishes:

```
eligibility_evaluation:                      "lazy_deterministic_rank_walk"
n_enumerated (distinct latest-version PMCIDs): 9129850
n_metadata_records_probed:                    18
n_eligible_dev_materialized_before_stop:      12
n_eligible_dev_population_size:               null   (NOT MATERIALIZED)
n_audit_neighbors:                            4
n_selected:                                   8
```

DocLayNet and Federal Register (both eager-materialization corpora) are
unchanged in shape; each acquires an `eligibility_evaluation:
"eager_full_materialization"` label to make the distinction explicit.

The dry report (`DEV_PILOT_DRY_REPORT.md`) is regenerated to match.

## Immutable artifacts (hashes MUST NOT change)

Recorded from the successful 2026-09-15 recovery validation. If any of
these hashes changes across the finalization, the finalization is
invalid.

| Artifact | SHA-256 |
|---|---|
| `DEV_PILOT_POPULATION_PMC_V1.json`        | `37c8b0ead4e8667cd0dce7a080bc7dcde8e9ab1fd8c14bb8a81c3b040966d028` |
| `DEV_PILOT_POPULATION_DOCLAYNET_V1.json`  | `d3f4913b1e09df51c4f018e9522087d131360c1bce4f30225191038b7ba05215` |
| `DEV_PILOT_POPULATION_FR_RULE_V1.json`    | `d0484b3b87ae26461c5c91c6869d7071d9ccce195529b3173b29b6a6673b9de7` |
| `DEV_PILOT_POPULATION_FR_PRORULE_V1.json` | `a0e93613d152795aa8df4dee040536917745c2191034541444b5f1aefc856fa3` |
| `DEV_PILOT_POPULATION_FR_NOTICE_V1.json`  | `bee147e7431f3e1186a875911ef69fc321cc50254b9a4bd025dab9eba6c31de7` |
| `DEV_PILOT_POPULATION_SNAPSHOTS.json`     | `1c1690bd54e5a608abb06f0a7f5251a7c008ee393d7d47bb7a90499a06334c25` |
| `DEV_PILOT_EXCLUSION_UNION.jsonl`         | `345630bf8624f7c4a7616fa74def61a924e1d5954a51de389953c1a5018ed082` |

## Regenerated artifacts (hashes DO change; both recorded)

| Artifact | Pre-correction SHA-256 | Post-correction SHA-256 |
|---|---|---|
| `DEV_PILOT_MANIFEST_V1.json`  | `8b0124240c9e564865ba2b10cf6e497d19869e6b2fff403e70514409fb3219ba` | `6fc5d91da9cb896a49e396ea952b8b1aa417db422826bcd1512e9e58c69370b4` |
| `DEV_PILOT_DRY_REPORT.md`     | `e7f968cb78f5516a02d26e368592c72f96a0c3000f5cb5a311224f89a7523946` | `99c3b1a5a1115eecabd76973be95cb039a564101aeac2ebb7ba0ebe0b1503cfc` |

The `emitted_utc` field in the manifest and the `Emitted:` line in the
report will differ from the pre-correction versions by design — this is
a re-emission event with its own timestamp. All other selection-bearing
fields (selected IDs, cutoff neighbors, per-corpus outcomes for eager
corpora, exclusion summary, population snapshots) reproduce
byte-identically. `benchmarks.eval_v1.selection.finalize` verified the
selected-IDs and cutoff-neighbors invariance before overwriting the
files.

## Selected-set invariance attestation

`benchmarks.eval_v1.selection.finalize` refuses to emit unless the 20
selected canonical IDs and the cutoff-neighbor sets replayed from the
frozen checkpoints match the pre-existing manifest byte-for-byte. This
attestation is embedded in the module (not a comment) so any future
schema drift is caught by the same fail-closed gate.

The pre-existing selected set (which the finalization preserves):

- PMC-OA (8): `PMC3569185.1`, `PMC4160324.1`, `PMC12303665.1`,
  `PMC10454006.1`, `PMC7773825.1`, `PMC6839998.1`, `PMC5773191.1`,
  `PMC6374309.1`
- DocLayNet (6): `3a504c7c…`, `601febfd…`, `664f52bb…`, `8731e4b0…`,
  `a1652ae4…`, `7d2023bb…`
- Federal Register (6): `2025-07879` and `2025-13505` (Rule),
  `2025-11271` and `2025-16333` (Proposed Rule), `2025-19924` and
  `2025-12869` (Notice)

## What was NOT changed

- Selected canonical IDs
- Cutoff-neighbor sets
- Population checkpoint payloads or fingerprints
- Exclusion union file
- Aggregate `DEV_PILOT_POPULATION_SNAPSHOTS.json` (already used honest labels)
- `selection_algorithm_version` — still `"1"`
- `checkpoint_schema_version` — still `"1"`

## What triggers a re-selection (bump `selection_algorithm_version`)

Per the manifest's `hard_rule`: any change to eligibility, exclusion,
partition, or ranking that could alter selected IDs REQUIRES bumping
`selection_algorithm_version`. This finalization deliberately changes
none of those.
