# Multicolumn recalibration — ParseBench + frozen public corpus — 2026-07-19 (Issue #50)

**Commit under evaluation:** `d479b90` (main, post-#64).

**Detector under evaluation:** `W_MULTICOLUMN_ORDER` block-level multicolumn detector at `aksharamd/plugins/validators/multicolumn.py`. **No detector or scoring code was modified for this evaluation.** The four candidate rule variants (baseline, C3, C4, C3+C4) are imported verbatim from the frozen phase-2 replay harness (`benchmarks/multicolumn_candidate_replay.py`).

**Runtime discipline.**

- ParseBench PDFs were served from the pre-existing verified cache at `%LOCALAPPDATA%\aksharamd\parsebench\<revision>\` (`revision = 2805a1d940f95a203e0ae4b88be9934f7765b3fc`). No new network fetch. Every asset's `sha256` and `size_bytes` were re-verified against `benchmarks/parsebench_assets.lock.json` before compilation.
- Public-corpus per-page signals were consumed **as-is** from the frozen phase-1 artifact `benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.json` (harness_version `"1"`, commit `c4dfe86`). No recompile of the public corpus. If the frozen artifact's `harness_version` or `commit` disagrees with expected, the harness fails with a distinct error code.
- No PDF bytes were added to git.

**Machine-readable output:** `benchmarks/PARSEBENCH_RECALIBRATION_2026-07-19.json`.

## Corpus resolution

### ParseBench (12 assets)

All 12 assets are reference-fetched and reviewer-approved at document level. Slice eligibility is derived from `parsebench_assets.lock.json` per-asset fields:

| Slice | Eligible | Notes |
|---|---:|---|
| Document historical (frozen `expected_label`) | 12 | All 12 assets participate (4 TP + 8 TN). |
| Document reviewer-confirmed | 6 | Excludes ambiguous (simple2, text_dense__de) and non-multicolumn (ikea3, letter3, myctophidae, japanese_case). ikea3 hits both exclusions. |
| Page-level | 6 | One page per asset; ambiguous pages and non-multicolumn assets excluded. |
| Block-level observable | 5 | Page-level subset where `detector_observability == "block-level-observable"`. |

Document reviewer-confirmed corpus (n=6): **3colpres, elpais, 2colmercedes, battery, eastbaytimes, strikeUnderline** — 2 positives (3colpres, elpais) + 4 negatives.

Observable corpus (n=5): drops `elpais` (span-only-observable). Contains **3colpres** (only real block-level TP) + four block-level-observable negatives (2colmercedes, battery, eastbaytimes, strikeUnderline).

### Public corpus (frozen, 34 results, 22 eligible)

Resolution from `benchmarks/multicolumn_recalibration_labels.json` labels manifest:

- **Discovered by phase-1 rglob:** 34 PDFs.
- **Labels manifest entries:** 47 keys (some entries are aliases for the same asset under different paths — phase-1 uses a fallback chain `stripped → full_rel → basename`).
- **Attested labels** (`expected_positive` in {True, False}): 25 label keys → **22 distinct results** after fallback-chain resolution. This 22-doc set is what phase 2's confusion matrix consumed and what this recalibration consumes unchanged.
- **Excluded from the confusion matrix** (`expected_positive == null`): 12 results (image-only, encrypted, sparse-text / RTL). Their `excluded_reason` is preserved in the raw manifest.

Confidence check: the count 22 is **derived** from the frozen artifacts at runtime. If the labels-manifest attestation-count or the harness result-count drifts, the harness re-derives; hard-coded `21` from earlier prose is a documentation artefact, not a magic constant.

## Metrics

Four candidates. Every cell is `TP / FP / TN / FN → (P, R, F1, FPR)`. All decimals are rounded to 4 places.

### Slice 1 — ParseBench, document historical (n=12; 4 TP + 8 TN)

Historical labels are frozen `expected_label` from the lockfile; every attested asset participates regardless of ambiguity or defect class. **This slice includes non-multicolumn assets and is provided for continuity with the phase B4 report only.** It does not represent the detector's honest capability.

| Rule | TP | FP | TN | FN | P | R | F1 | FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 1 | 7 | 3 | 0.5000 | 0.2500 | 0.3333 | 0.1250 |
| C3       | 1 | 1 | 7 | 3 | 0.5000 | 0.2500 | 0.3333 | 0.1250 |
| C4       | 0 | 1 | 7 | 4 | 0.0000 | 0.0000 | 0.0000 | 0.1250 |
| C3+C4    | 0 | 1 | 7 | 4 | 0.0000 | 0.0000 | 0.0000 | 0.1250 |

Reading: baseline warns on 3colpres (TP) + strikeUnderline (FP). C4 and C3+C4 silence 3colpres (TP → FN) without silencing strikeUnderline. Historical recall is dominated by the three positives whose damage is span-only or non-observable to a block-level detector (elpais, simple2, ikea3); it structurally cannot exceed 25% for a block-level detector, so this slice is not the right decision surface.

### Slice 2 — ParseBench, document reviewer-confirmed (n=6; 2 TP + 4 TN)  ← **primary decision slice**

Excludes ambiguous assets and assets whose `defect_kind` is `non-multicolumn`. This is the slice the recalibration decision should be made on.

| Rule | TP | FP | TN | FN | P | R | F1 | FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 1 | 3 | 1 | 0.5000 | 0.5000 | 0.5000 | 0.2500 |
| C3       | 1 | 1 | 3 | 1 | 0.5000 | 0.5000 | 0.5000 | 0.2500 |
| C4       | 0 | 1 | 3 | 2 | 0.0000 | 0.0000 | 0.0000 | 0.2500 |
| C3+C4    | 0 | 1 | 3 | 2 | 0.0000 | 0.0000 | 0.0000 | 0.2500 |

Positives in this slice: **3colpres** (block-level-observable TP) + **elpais** (span-only-observable — block-level detector cannot see it; contributes to baseline FN). Negatives: 2colmercedes, battery, eastbaytimes (correct silence), and strikeUnderline (persistent FP not fixed by any candidate).

### Slice 3 — ParseBench, page-level (n=6 pages; 2 TP + 4 TN)

Identical composition to Slice 2 for this dataset (every asset has exactly one page, and every remaining asset's page counts as a whole-asset row). Recorded separately so the page slice's format is available for future multi-page ParseBench assets.

| Rule | TP | FP | TN | FN | P | R | F1 | FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 1 | 3 | 1 | 0.5000 | 0.5000 | 0.5000 | 0.2500 |
| C3       | 1 | 1 | 3 | 1 | 0.5000 | 0.5000 | 0.5000 | 0.2500 |
| C4       | 0 | 1 | 3 | 2 | 0.0000 | 0.0000 | 0.0000 | 0.2500 |
| C3+C4    | 0 | 1 | 3 | 2 | 0.0000 | 0.0000 | 0.0000 | 0.2500 |

### Slice 4 — ParseBench, block-level observable (n=5; 1 TP + 4 TN)

The honest recall corpus for the block-level detector. Removes `elpais` (span-only) from the eligible set — a block-level detector structurally cannot see span-only splices.

| Rule | TP | FP | TN | FN | P | R | F1 | FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 1 | 3 | 0 | 0.5000 | 1.0000 | 0.6667 | 0.2500 |
| C3       | 1 | 1 | 3 | 0 | 0.5000 | 1.0000 | 0.6667 | 0.2500 |
| C4       | 0 | 1 | 3 | 1 | 0.0000 | 0.0000 | 0.0000 | 0.2500 |
| C3+C4    | 0 | 1 | 3 | 1 | 0.0000 | 0.0000 | 0.0000 | 0.2500 |

Reading: on the block-level-observable slice, baseline hits **100% recall** (fires on 3colpres, the one visible TP). C4 and C3+C4 destroy that recall by silencing 3colpres. FPR stays at 25% for every candidate — strikeUnderline is the persistent FP that none of the current candidates fix.

### Slice 5 — Public corpus (frozen), document historical (n=22)

Identical for every candidate — matches the phase-2 replay report byte-for-byte.

| Rule | TP | FP | TN | FN | P | R | F1 | FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 2 | 19 | 0 | 0.5000 | 1.0000 | 0.6667 | 0.0952 |
| C3       | 1 | 2 | 19 | 0 | 0.5000 | 1.0000 | 0.6667 | 0.0952 |
| C4       | 1 | 2 | 19 | 0 | 0.5000 | 1.0000 | 0.6667 | 0.0952 |
| C3+C4    | 1 | 2 | 19 | 0 | 0.5000 | 1.0000 | 0.6667 | 0.0952 |

Reading: on the public corpus, **no candidate changes any decision**. C3's raised gap gate does not silence GeoTopo / GeoTopo-komprimiert (the two FPs), and C4's HTR-plus-signal requirement doesn't silence them either.

### Slice 6 — Combined, document historical (n=34)

Union of ParseBench (12) and public (22) at the document-historical slice.

| Rule | TP | FP | TN | FN | P | R | F1 | FPR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2 | 3 | 26 | 3 | 0.4000 | 0.4000 | 0.4000 | 0.1034 |
| C3       | 2 | 3 | 26 | 3 | 0.4000 | 0.4000 | 0.4000 | 0.1034 |
| C4       | 1 | 3 | 26 | 4 | 0.2500 | 0.2000 | 0.2222 | 0.1034 |
| C3+C4    | 1 | 3 | 26 | 4 | 0.2500 | 0.2000 | 0.2222 | 0.1034 |

## Every changed decision (vs baseline)

Total flips across both corpora and both scopes: **4** — all from C4 or C3+C4, all `silenced`, all pointing at 3colpres.

| Candidate | Corpus | Scope | Id | Baseline | Candidate | Flip | Eligibility | Affects document verdict? |
|---|---|---|---|---|---|---|---|:---:|
| C4 | parsebench | document | 3colpres | True | False | silenced | reviewer_confirmed | yes |
| C4 | parsebench | page | 3colpres#1 | True | False | silenced | reviewer_confirmed+observable | yes |
| C3+C4 | parsebench | document | 3colpres | True | False | silenced | reviewer_confirmed | yes |
| C3+C4 | parsebench | page | 3colpres#1 | True | False | silenced | reviewer_confirmed+observable | yes |

**No flip on the public corpus, in any candidate, in either direction.** This is the machine-readable proof that C3 and C4 are inert on the public evidence; the ParseBench damage they cause is not offset by any public-corpus improvement.

Signal detail (from the raw JSON): 3colpres page 1 has `transition_rate` above the HTR threshold and `gap_rel` well above 0.30, but only one of the auxiliary signals (YMT / SF) is present. The C4 rule requires HTR AND (YMT OR SF); with SF alone below its own threshold on this specific page, C4 silences it.

## Decision on C3, C4, C3+C4

**Ship: none of them.** Every candidate is either inert (C3, C4 on public; C3 on ParseBench) or actively harmful (C4 and C3+C4 silence the one real block-level TP on ParseBench without fixing the persistent FP).

- **C3** (raise `gap_rel` gate from 0.15 to 0.30) — no effect. On the public corpus, GeoTopo and GeoTopo-komprimiert clear the higher gate. On ParseBench, no positive is silenced (recall preserved) but no FP is silenced either. **Recommendation: do not ship.** No signal, no cost.
- **C4** (HTR AND (YMT OR SF)) — recall drops from 100% to 0% on the block-level-observable slice. FPR unchanged at 25%. **Recommendation: do not ship.**
- **C3+C4** — identical outcome to C4 on this corpus. **Recommendation: do not ship.**

The strikeUnderline FP (page-1 sidebar creating a phantom bimodal x0 distribution) is not addressed by any of the current candidates. Future candidate work targeting this FP class needs a different signal — the sidebar-cluster-density heuristic sketched in the phase-2 report is a starting point.

## Note on the readiness-calibration dev split

The dev-split diagnostic corpus (`benchmarks/READINESS_CALIBRATION_DEV_REPORT.md`) was NOT folded into any primary metric in this report. The dev split was not prepared under the same reviewed annotation protocol as ParseBench Phase B5 or public-corpus phase 1. If it is later cited alongside these metrics, the reader should treat it as external / historical evidence, not as a source of confusion-matrix rows.

## Reproducibility

Local reproduction:

```
# 1. Verify the ParseBench cache is populated + matches promoted checksums.
python -m benchmarks.parsebench_recalibration --verify-cache-only

# 2. Re-run the full recalibration.
python -m benchmarks.parsebench_recalibration \
  --output benchmarks/PARSEBENCH_RECALIBRATION_2026-07-19.json
```

The script fails with a distinct exit code if the ParseBench cache is missing (30), the public-corpus artifact's `harness_version` disagrees (31), or its `commit` disagrees (32).

## Constraints observed

- No detector, parser, scoring, packaging, or model code was modified.
- `SCORING_POLICY_VERSION` is still `"1.0"`.
- Promoted checksums, sizes, redistribution posture, and dataset revision on `benchmarks/parsebench_assets.lock.json` are unchanged.
- The verified external ParseBench cache was reused; no new fetch was performed.
- No PDF bytes were added to git.
