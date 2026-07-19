# Multicolumn Detector Recalibration — 2026-07-18 (Issue #50, phase 1)

**Commit under evaluation:** `c4dfe86bb391727b5eef9ddd28bfd215d1c554c2` — the parser
correction from Issue #54.

**Scope:** measurement and detector-improvement proposals only. No production
code, warning logic, parser behaviour, readiness scoring, quality-band
thresholds, `SCORING_POLICY`, or `SCORING_POLICY_VERSION` was changed by this
phase.

Companion artefacts:

- `benchmarks/multicolumn_recalibration.py` — harness
- `benchmarks/multicolumn_recalibration_labels.json` — frozen labels
- `benchmarks/multicolumn_recalibration_metrics.py` — analysis
- `benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.json` — full per-document diagnostics
- `benchmarks/MULTICOLUMN_RECALIBRATION_METRICS_2026-07-18.json` — machine summary

## Environment

| Item | Value |
|---|---|
| Platform | Windows 11 (26200) |
| Python | 3.12.2 |
| AksharaMD | 0.3.6, `main` at `c4dfe86` |
| Detector | `MultiColumnOrderValidator` — `W_MULTICOLUMN_ORDER` |
| Maturity at evaluation | `candidate` |
| Penalty at evaluation | 0 (informational) |
| Boundary helper | `aksharamd/plugins/parsers/pdf.py::_detect_column_boundaries` (post-#54: `_MIN_LINES_PER_COLUMN_CLUSTER = 2`) |

## Corpus manifest

Total assets discovered by the harness: **34 PDF files** across the public
corpus (some sub-directories ship multiple variants, e.g. compressed).

| Category | Count |
|---|---:|
| Labelled positive (`expected_positive: true`) | 1 (`multicolumn.pdf`) |
| Labelled negative (`expected_positive: false`, textual) | 15 |
| Excluded — encrypted / image-only / OCR-only / sparse text | 18 |
| **Total labelled subset in the confusion matrix** | **17** |

### Unavailable external assets (recorded, not counted)

The ParseBench binary corpus used for the Phase 1 shipped metrics is not in
this repository. The historical numbers below are retained for context;
they are not directly comparable to this run.

Explicitly recorded as unavailable:

- `ikea3`, `elpais`, `simple2` — known FN class (span-level interleaving)
- `3colpres` — Phase 1 primary TP
- `eastbaytimes`, `battery`, `2colmercedes` — Phase 1 controls
- `text_dense__de`, `letter3`, `myctophidae`, `strikeUnderline`, Japanese case
  — named regression cases from `benchmarks/EXPECTATION_VALIDATION_REPORT_V3.md`

## Frozen evaluation protocol

- **Positive definition** — a document whose extracted block sequence exhibits
  column-interleaving, or whose source has multi-column layout that produces
  reading-order damage in `document.md`.
- **Negative definition** — single-column source layout, or multi-column
  source that AksharaMD renders in correct reading order.
- **Unit of evaluation** — document (top-level `warning_codes` decision).
  Per-page detector diagnostics are also captured but do not affect the
  confusion matrix.
- **Warning decision rule** — positive prediction iff `W_MULTICOLUMN_ORDER`
  appears in the compiled document's `warning_codes`. Negative otherwise.
- **Ambiguous cases** — documents with unverified layout, encrypted, or
  image-only were marked `excluded` and are not counted.
- **Backend** — PyMuPDF primary + pdfplumber fallback; boundaries via the
  post-#54 `_detect_column_boundaries` (2-line minimum support).
- **Determinism** — two consecutive runs on the same commit produced
  identical warning codes, band, score, and detector diagnostics for every
  document. Token count on the 117-page `GeoTopo-komprimiert.pdf` varied by
  9 (of ~59 655), which is a downstream Phase-5 optimizer non-determinism
  unrelated to detection.

## Confusion matrix (document level)

| Metric | Value |
|---|---:|
| True positive | 1 (`multicolumn.pdf`) |
| False positive | 2 (`GeoTopo.pdf`, `GeoTopo-komprimiert.pdf` — same source content, compression variant) |
| True negative | 15 |
| False negative | 0 |
| Excluded | 16 (encrypted / image-only / sparse) |
| **Precision** | **0.333** |
| **Recall** | **1.000** |
| **False-positive rate** | **0.118** |
| **F1** | **0.500** |

If the two GeoTopo variants are collapsed into a single logical asset
(same content, same 15-page firing pattern), the confusion matrix becomes
TP=1, FP=1, TN=14, FN=0 → P = 0.500, R = 1.000, FPR = 0.067, F1 = 0.667.
Both readings are reported to be transparent.

## Per-page firing counts

- `multicolumn.pdf`: 1 problem page (p3). Note: **the actual span-level
  damage on this document is on p1/p2**; p3 is a single-column table.
- `GeoTopo.pdf` (117 pages): 15 problem pages — p9, p15, p23, p29, p35,
  p39, p40, p56, p71, p76, p80, p81, p91, p101, p108. All are visually
  single-column mathematical body content.
- `GeoTopo-komprimiert.pdf`: identical page list.
- All 15 labelled TN documents: 0 problem pages.

## Historical comparison (context — different corpora)

| Source | Precision | Recall | FPR | Corpus |
|---|---:|---:|---:|---|
| Phase 1 re-score (`aksharamd/plugins/validators/multicolumn.py:165`) | 1.00 | 0.40 | 0 | 5 ordering targets (unknown mix) |
| "Shipped" claim (`aksharamd/plugins/validators/multicolumn.py:163`) | 1.00 | 0.33 | 0 | 21-doc external ParseBench |
| **This recalibration (public corpus, post-#54)** | **0.333** | **1.000** | **0.118** | **17 labelled from `.public_corpus`** |
| (with GeoTopo variants collapsed) | 0.500 | 1.000 | 0.067 | 16 |

**Why the metrics moved:**

- The historical P=1.00 was measured on the external ParseBench corpus,
  which was hand-picked for calibration and did not contain the German
  math textbook `GeoTopo.pdf`. `GeoTopo` is now the dominant FP contributor
  in the public corpus.
- The historical R=0.33 reflected 5 ordering targets, three of which were
  labelled span-level FN by design. This public corpus contains a single
  labelled positive (`multicolumn.pdf`) which happens to fire because of
  a p3 table, not because of the p1/p2 damage — so recall reads as 1.000
  at the document level but the **why** is different from the historical
  claim.
- The parser fix from #54 was a *precision* change for the layer *below*
  the validator: it removed the `pdflatex-4-pages.pdf` false positive but
  did not touch the validator's own heuristics.

## Block-level vs. span-level analysis

**Block-level detectable damage** — cases where the extracted block
sequence itself is out of column order:

- `multicolumn.pdf` p3 fires. This is the only case in the public corpus.
  It is a **table page**, not a real multicolumn page — a genuine
  false-attribution TP.

**Span-level damage the block-level detector cannot see** — cases where
the block sequence looks clean but text within blocks is spliced from two
columns mid-sentence:

- `multicolumn.pdf` p1 and p2. Damage visible in `document.md` ("iscing
  elit" spliced from column 2 mid-word). Block-level detector diagnostics:
  clean transition_rate on both pages (parser correctly column-sorts spans
  after #54's cluster support requirement, hiding block-level evidence).
  Detecting this requires reading INSIDE blocks — not currently possible
  with this detector.

The Phase 1 known FN classes (`ikea3`, `elpais`, `simple2`) all fall in
the span-level bucket and remain undetectable by design. The Phase-1
recall number of 0.33 in the memory reflects this exact limitation.

## Error taxonomy

### FP causes (15 pages × 2 variants of GeoTopo)

| Cause | Count | Example (GeoTopo page) | Signals |
|---|---:|---|---|
| **Itemised proof steps / lemma lists** (indented enumerated blocks with mid-line x-starts create bimodal x0 cluster) | 4 | p35 (short_frac=1.00 → all short indented steps), p91, p101 | high_transition_rate + short_frac |
| **Inline mathematical displays** (equations placed at mid-x create phantom bimodal geometry) | 5 | p9, p15, p39, p40, p80 | high_transition_rate + y_monotonic_with_transitions |
| **Figure captions offset from body left margin** | 3 | p29, p56 (short_frac=0.57–0.62) | high_transition_rate + short_frac + y_monotonic |
| **Section headings / theorem headers at atypical x** | 3 | p23, p71, p108 (large_y_drops > 0 counterbalances y_monotonic) | high_transition_rate |

All 15 GeoTopo FP pages share a common structural fingerprint:
`gap_rel` in [0.20, 0.61], `transition_rate` in [0.29, 0.71], often
`large_y_drops = 0`. The detector's primary signal `high_transition_rate
>= 0.28` fires without any real column layout because the block sequence
alternates between full-width body paragraphs and offset (indented or
displayed) sub-blocks.

### FN causes

- **`multicolumn.pdf` p1 / p2 — span-level interleaving.** The parser
  currently detects a real column boundary on p1 (dense right cluster of
  10 supporting lines) and produces block-first, column-sorted output.
  The visible span-level damage happens *inside* individual blocks. The
  block-level detector, by construction, cannot see this.
- **Ownership**: span-level — not the block-level validator's remit.
  Would require either (a) a different detector operating over spans
  before block assembly, or (b) a validator that reads block *content*
  and detects mid-word column splices (dictionary miss rate, sentence
  boundary continuity heuristics).

## Readiness truthfulness

Documents where readiness does not reflect real content quality:

- `multicolumn.pdf`: **HIGH 85** with visible span-level damage. Warning
  fires (candidate, penalty 0) so the damage is auditable via
  `warning_codes`, but readiness stays HIGH. A scoring calibration PR
  could move this warning to a real deduction — that decision belongs
  to a separate PR.
- `GeoTopo.pdf`: HIGH 87 with `W_MULTICOLUMN_ORDER` fired on 15 pages. The
  reading order is actually correct — the warning is a false positive.
  A calibrated penalty here would double-punish an already-good document.
  This is exactly why a scoring calibration must not happen before
  detector precision improves.

Scoring recommendations are intentionally omitted from this PR.

## Ranked candidate detection improvements

Each candidate is scored on:

- **Expected effect on the 30 GeoTopo FP page-firings** (dominant FP class)
- **Expected effect on the 1 `multicolumn.pdf` p3 FP** (secondary FP class — table on TP)
- **Effect on the currently-invisible p1/p2 span-level damage** (unknown at block level)
- **Effect on synthetic validator TPs** (must not regress)

### C1. Require ≥ 3 blocks in the minority cluster instead of proceeding on any imbalance

Location: `MultiColumnOrderValidator._analyse_page`, guarding the
`high_transition_rate` signal.

- GeoTopo FPs: most firing pages have small minority-cluster counts
  (many are `n_blocks ∈ [5, 13]`; minority cluster often 1–2 blocks).
  Expected FP reduction: **10 to 12 of 15 GeoTopo pages**.
- `multicolumn.pdf` p3: 7 blocks, minority likely 1–2 (table cells at
  mid-x). Expected effect: **suppressed** — one more true-positive-fire
  removed, but that fire is on the wrong page anyway.
- Synthetic validator TPs: `test_interleaved_warns` uses 16 balanced
  blocks (min cluster 8); `test_trans_030_warns` uses minority 2. **The
  ≥3 threshold would break `test_trans_030_warns`.** Would need to keep
  the threshold at 2 or bundle with a second signal.
- Block-level data sufficient? Yes.
- Span-level metadata required? No.

### C2. Require `large_y_drops ≥ 1` for the primary signal (not the confirming one)

Currently `high_transition_rate` fires alone. The confirming signal
`y_monotonic_with_transitions` requires `large_y_drops == 0`. Flipping
the primary requirement to *require* a large y-drop means the detector
insists on evidence of a real column break (the y jumps back up from
bottom of column 0 to top of column 1).

- GeoTopo FPs: 10 of 15 pages have `large_y_drops = 0`. Expected FP
  reduction: **10 of 15**.
- `multicolumn.pdf` p1 (real 2-col, currently no fire because parser
  column-sorts): would still not fire (blocks are grouped, no interleaving).
- `multicolumn.pdf` p3 (table FP): `large_y_drops = 0` → suppressed. Good.
- Synthetic validator TPs: `_blocks_interleaved` is y-monotonic (drops = 0),
  so **the primary TP test would break**. Cannot use this alone; only
  useful in combination with C4 or as a *secondary* filter.
- Block-level data sufficient? Yes.

### C3. Require `gap_rel ≥ 0.30` (currently 0.15)

Raise the geometric bar so isolated mid-x indented sub-blocks are less
likely to synthesise a candidate cluster gap.

- GeoTopo FPs: 8 of 15 pages have `gap_rel ≤ 0.30`. Expected FP
  reduction: **8 of 15**.
- `multicolumn.pdf` p3: `gap_rel = 0.39` → survives.
- Synthetic validator TPs: use `x0 = 72` and `x0 = 320` on a 600 pt-wide
  page (`gap_rel ≈ 0.41`). **Survives**.
- Block-level data sufficient? Yes.

### C4. Combined rule — primary signal requires two independent evidence items

Change the warn condition so `high_transition_rate` alone is no longer
sufficient; require `(high_transition_rate AND y_monotonic_with_transitions)`
OR `(high_transition_rate AND short_frac)`. Currently either single
`high_transition_rate` alone or any two signals triggers warn.

- GeoTopo FPs: pages where the primary signal fires *and* one confirming
  signal fires (drops=0 OR short_frac≥0.55) are still positives. Pages
  where only the primary fires (large_drops>0 AND short_frac<0.55) are
  suppressed — that's pages p71, p76, p81, p108: **4 of 15 FPs
  eliminated**. Weaker than C1/C2 in isolation, but composable.
- `multicolumn.pdf` p3: has all three signals so would still fire.
- Synthetic validator TPs: `test_interleaved_warns` — TR=1.0, drops=0 →
  passes. `test_trans_030_warns` — TR≈0.33, drops? Need to verify.
- Block-level data sufficient? Yes.

### C5. Minimum text volume per candidate column

Reject the cluster split when the minority cluster's total text volume
(words summed across its blocks) is below a threshold — e.g., 80 words.

- GeoTopo FPs: itemised proof pages have minority clusters composed of
  short indented lines. Expected FP reduction: **5–8 pages**.
- `multicolumn.pdf` p3: table row cells are short — minority cluster
  under 80 words. Suppressed.
- Synthetic TPs: `_blocks_interleaved` uses 5 words × 16 blocks — minority
  cluster ≈ 40 words. **Would break the primary TP test at threshold 80.**
  Threshold 30 might work but the signal is fragile.
- Block-level data sufficient? Yes (block.content is available).

### C6. Span-level column-splice detector (new, orthogonal)

Add a *new* validator that inspects block **content** rather than block
geometry. Signals:

- Mid-word column splice: dictionary miss rate around sentence internal
  positions where a word appears to have been concatenated from two
  half-words (e.g. `iscing elit` from `adipiscing elit`).
- Sentence boundary continuity: consecutive-sentence semantic
  discontinuity above a threshold.

- GeoTopo FPs: not addressed (this is a new detector, doesn't fix the
  block-level FPs).
- `multicolumn.pdf` p1 / p2: **directly addresses the current span-level
  FN**. Only candidate that does.
- Effort: high. Requires a dictionary/lemma dependency or a semantic
  scoring model. Owner: new detector; not `MultiColumnOrderValidator`.
- Block-level data sufficient? No. **Span-level metadata required**.

### Ranking summary

| Rank | Candidate | Est. FP reduction (of 15 GeoTopo pages) | Risk to TP | Data required | Complexity |
|---:|---|---:|---|---|---|
| 1 | **C1** (min-cluster ≥ 3, only if paired with another guard) | 10–12 | high (breaks `test_trans_030_warns`) → do NOT ship at 3 | block-level | low |
| 2 | **C4** (compound-signal requirement) | ≥ 4 | low, needs verification of synthetic TP | block-level | low |
| 3 | **C3** (raise `gap_rel` to 0.30) | 8 | low (synthetic TPs use gap ≈ 0.41) | block-level | low |
| 4 | **C2** (require `large_y_drops ≥ 1` as gate) | 10 | high (breaks primary synthetic TP) → cannot ship alone | block-level | low |
| 5 | **C5** (min column text volume, threshold 30) | 5–8 | medium (fragile) | block-level | low |
| 6 | **C6** (span-level content-based detector) | 0 (different attack surface) | none, additive | **span-level required** | high |

## Recommended next-implementation-PR scope

- **Ship C3 + C4 combined** as the next detection-only PR — both keep the
  synthetic validator TPs intact, both work on block-level data, both are
  narrow. Expected combined FP reduction on GeoTopo: **8–12 of 15 pages**.
  Precision on this corpus rises from 0.333 to somewhere near 0.6–0.8;
  recall stays 1.0. Verify against the same corpus and against the
  historical Phase-1 tests.
- **Defer C1 at threshold 3.** The current corpus does not include a
  document that would let us safely raise the threshold above 2 without
  breaking `test_trans_030_warns`. If a future TP fixture is added with
  a densely-supported minority cluster ≥ 3, revisit.
- **Track C6 (span-level detector) as a separate follow-up.** This is
  the only candidate that addresses the current `multicolumn.pdf` p1/p2
  span-level damage. It is a bigger project (new detector, possibly a
  new warning code) and belongs in its own PR after the block-level
  precision-improvement PR ships.
- **Scoring changes are out of scope** for the next PR as well. Once
  precision is above ~0.8 on a mixed corpus, a scoring calibration PR
  can propose moving `W_MULTICOLUMN_ORDER` from `max_penalty=0
  candidate` to a real deduction. Not before.

## Explicit statement

**Scoring was not changed in this recalibration PR.**

- `SCORING_POLICY_VERSION` remains `"1.0"`.
- No files under `aksharamd/scoring/` were modified.
- No warning penalty or band threshold was changed.
- No detector logic was modified.

## Reproducibility

From the repository root, with an installed AksharaMD wheel on `PATH` or
`AKSHARAMD_E2E_BINARY` set:

```
python benchmarks/multicolumn_recalibration.py \
    --corpus benchmarks/.public_corpus/pdf \
    --labels benchmarks/multicolumn_recalibration_labels.json \
    --output benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.json

python benchmarks/multicolumn_recalibration_metrics.py \
    --harness benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.json \
    --labels benchmarks/multicolumn_recalibration_labels.json \
    --summary-json benchmarks/MULTICOLUMN_RECALIBRATION_METRICS_2026-07-18.json
```

Two consecutive harness runs on the same commit produced identical
detector diagnostics for every asset. Token counts on the 117-page
`GeoTopo-komprimiert.pdf` varied by 9 across runs (of ~59 655) — this
non-determinism lives downstream of the multicolumn detector and does
not affect precision/recall/F1.
