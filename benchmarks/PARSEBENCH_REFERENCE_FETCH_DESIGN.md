# ParseBench reference-fetcher design — 2026-07-18 (Issue #53, phase B1)

**Status:** DESIGN ONLY. No implementation. No file was downloaded, mirrored,
committed, or redistributed by this document.

**Companion artefact:** `benchmarks/parsebench_assets.lock.json` — the frozen
lockfile that any future fetcher will operate against.

**Policy:** every entry in the lockfile is `redistribution =
reference-fetch-only`. This document describes an *external* fetcher that
downloads the pinned files at CI time from LlamaIndex's HuggingFace
dataset. It never redistributes bytes, never mirrors, never surfaces the
files inside AksharaMD-controlled hosting.

## Non-goals

- Do not implement the fetcher in this PR.
- Do not populate `sha256`, `size_bytes`, or `hf_repo_revision` values in
  the lockfile before an authorised local fetch happens (in a later PR).
- Do not stage or commit any downloaded PDF or its bytes into this
  repository.
- Do not gate PR merges on the fetcher today (`ci_retrieval: "nightly"`
  for every asset per the lockfile).

## Contract for the eventual fetcher script

### Inputs

- The frozen `benchmarks/parsebench_assets.lock.json`.
- Optional environment overrides:
  - `AKSHARAMD_PARSEBENCH_CACHE` — override the cache root. Defaults to
    `~/.cache/aksharamd/parsebench` on POSIX and `%LOCALAPPDATA%\aksharamd\parsebench` on Windows.
  - `AKSHARAMD_PARSEBENCH_REVISION` — override the pinned dataset
    revision (developer-only escape hatch; nightly CI must pin the value
    already in the lockfile).
  - `AKSHARAMD_PARSEBENCH_ALLOW_NETWORK` — must be set to a positive value
    for the fetcher to open a connection. Missing = refuse to run.

### Outputs

- Files placed under the cache root, one per asset, at the path
  `<cache_root>/<hf_repo_revision>/<hf_repo_path>`.
- A machine-readable run log at
  `<cache_root>/last_fetch_report.json` recording per-asset outcome.

The cache root **must be outside git-tracked paths**. Every consumer
(e.g., the calibration harness) reads from the cache and never from the
repository tree.

### Errors — no silent skipping

The fetcher must distinguish these terminal states and report each with
a distinct exit code + entry in `last_fetch_report.json`. **Every state
must be reported explicitly.** No state may silently reduce the corpus
size in a calibration run.

| Code | Meaning | Behaviour |
|---:|---|---|
| `0` | All expected assets present in cache with matching checksums. | Fetcher exits 0; downstream calibration proceeds. |
| `10` | `AKSHARAMD_PARSEBENCH_ALLOW_NETWORK` not set. | Fetcher exits before any HTTP call; downstream calibration MUST refuse to run against a stale-or-partial cache without the operator's acknowledgement. |
| `20` | Network unreachable (DNS, TCP, TLS). | Distinct from `21` — infrastructure issue vs. licence issue. Downstream calibration exits with a matching "corpus incomplete" error. |
| `21` | Licence-restricted asset skipped. | Reserved for future assets classified as anything other than `reference-fetch-only`; today every entry is `reference-fetch-only` so `21` should never trigger. If it does, calibration exits with an explicit "corpus incomplete due to licence policy" error. |
| `22` | Asset absent upstream at the pinned revision. | Fetcher reports the asset id, expected path, and pinned revision. Downstream calibration exits explicitly rather than silently continuing. |
| `23` | Checksum mismatch. | Fetcher deletes the cached file, records the mismatch, exits. Downstream calibration never proceeds with a mismatched checksum. |
| `24` | Unresolved identity in the lockfile. | Fetcher skips the entry with an explicit warning. Any asset with `resolved_identity: null` or `hf_repo_path: null` triggers this. The Japanese case was resolved in phase B1; this code exists so a future added asset that stays unresolved cannot silently poison metrics. |

**Explicit anti-pattern (prohibited):** treating a network-unreachable
outcome as "asset not present" and proceeding with a shrunken corpus.
Any implementation that does this must be rejected in review.

### Checksum policy

- The lockfile ships with `sha256: null` and `size_bytes: null` today.
- On the first authorised fetch (in a later PR), the fetcher writes the
  observed sha256 and size_bytes to a *separate* `parsebench_assets.lock.checksums.json`
  file that reviewers audit before promoting the values into the main
  lockfile. This decouples the download from the lockfile mutation and
  makes checksum tampering visible in review.
- Subsequent fetches compare the downloaded bytes against the *promoted*
  checksums in the main lockfile. Mismatches trigger code `23`.

### Revision pinning

- `dataset_revision` in the lockfile records a HuggingFace dataset
  commit SHA. Today it is `null` — the first authorised fetch will pin a
  concrete value.
- A reference-fetch without a revision pin is not reproducible: HuggingFace
  datasets can be rebased or updated. The fetcher MUST refuse to run if
  `dataset_revision` is null AND `AKSHARAMD_PARSEBENCH_REVISION` is not
  set as an operator-provided escape hatch.

### Attribution surface

Every fetch pipeline exit path emits:

- The ParseBench citation (Zhang et al., 2026, arXiv:2604.08538).
- A link to LlamaIndex's Apache-2.0 dataset licence.
- The Apache-2.0 SPDX identifier.

Written into `last_fetch_report.json` and displayed on stderr on success.

## What the fetcher does NOT do (locked constraints)

- Does not upload / mirror / republish any binary.
- Does not create a GitHub Release asset, GCS object, or S3 object with
  the fetched bytes.
- Does not persist the fetched bytes inside the AksharaMD git tree.
- Does not treat `reference-fetch-only` availability as permission to
  redistribute.
- Does not fall back to a mirror we control if the canonical source is
  down; that failure mode surfaces as exit code `20` or `22` and is not
  softened by a shadow mirror.

## Sequencing after this document lands

1. **Merge Phase B1 lockfile + design.** (This PR.)
2. **Later PR — authorised fetch + checksum capture.** The reviewer runs
   the fetcher once locally, captures sha256 + size_bytes to
   `parsebench_assets.lock.checksums.json`, PRs the checksums for
   review, and only then are they promoted into `parsebench_assets.lock.json`.
3. **Later PR — nightly CI job.** Wires the fetcher into a scheduled
   workflow, gates the reference-fetch calibration re-run behind
   green fetcher output.
4. **Later PR — corpus expansion by re-running Phase 1.** Uses the
   filled cache to re-run `benchmarks/multicolumn_recalibration.py`
   against the expanded corpus, producing a fresh
   `MULTICOLUMN_RECALIBRATION_<newdate>.json` alongside the frozen
   2026-07-18 evidence.
5. **Later PR — candidate replay against the expanded corpus.**
   Feeds the new phase-1 output into
   `benchmarks/multicolumn_candidate_replay.py`.
6. **Later PR (only if warranted) — detector implementation.** C3+C4
   ships only if the expanded corpus confirms the improvement.
7. **Later PR (deferred) — scoring calibration.** Only after detection is
   trustworthy on the expanded corpus.

Each step of that sequence is a separate PR with its own diff scope.
This document is only step 1's design surface — no code is shipped by
this PR that fetches, mirrors, or persists anything.

## Rights-review queue

The lockfile carries a `rights_review_queue` array. Every asset starts
in it. An asset can only be reclassified from `reference-fetch-only` to
`direct` by:

1. Identifying the original document's copyright owner AND its licence.
2. Recording explicit direct-permission evidence (a licence URL, a
   written permission, or a public-domain / CC BY / CC BY-SA
   declaration on the original source).
3. Updating both the lockfile entry and the queue row in the same PR.
4. Passing the reviewer bar.

Until all four steps are complete, the asset remains
`reference-fetch-only`. No permission is inferred from availability on
HuggingFace.
