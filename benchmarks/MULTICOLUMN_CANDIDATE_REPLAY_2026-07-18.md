# Multicolumn Candidate Replay — 2026-07-18 (Issue #50, phase 2)

**Commit under evaluation:** `c4dfe86bb391727b5eef9ddd28bfd215d1c554c2`
(post-Issue-#54).

**Scope:** offline replay of candidate detector rules over the previously
captured per-page diagnostics from `MULTICOLUMN_RECALIBRATION_2026-07-18.json`.
**No production code was executed or modified.** No wheel install, no CLI
invocation, no changes to `aksharamd/`, `SCORING_POLICY`, `SCORING_POLICY_VERSION`,
warning penalties, or band thresholds. The replay evaluates the *exact*
Boolean rule under study, without incidental variation from re-running
the parser.

Companion artefacts:

- `benchmarks/multicolumn_candidate_replay.py` — replay script
- `benchmarks/MULTICOLUMN_CANDIDATE_REPLAY_2026-07-18.json` — machine-readable
  per-page decisions under every candidate rule + changed-decision lists

## Confidence limitations — read first

- **The public corpus contains only one labelled positive document.** All
  metrics that involve recall are dominated by a single data point.
- **That document's warning fires on p3 (a single-column table), not on
  p1/p2 where the actual span-level damage is.** Document-level recall of
  1.000 is coincidental; it is not evidence of page-level or defect-level
  recall.
- **The external ParseBench corpus (`ikea3`, `elpais`, `simple2`,
  `3colpres`, `eastbaytimes`, `battery`, `2colmercedes`, `text_dense__de`,
  `letter3`, `myctophidae`, `strikeUnderline`, Japanese case) is
  unavailable in this repository.** Historical Phase-1 numbers (P=1.00,
  R=0.33) are retained for context but are not directly comparable to
  this run.
- **The two GeoTopo variants are the same source content.** Precision
  improvements measured against them are measuring one document twice.
- **Span-level defects are outside the block-level detector's
  observability by design.** No span-level recall is computed; the p1/p2
  damage on `multicolumn.pdf` remains invisible to this detector class
  under every rule below.

**No detector implementation should be treated as calibrated from this
one document. Corpus expansion with additional mixed-layout PDFs is a
prerequisite for a production precision claim.**

## Rule formalisation (pinned)

All rules operate on the per-page diagnostics dict already produced by
`MultiColumnOrderValidator._analyse_page`:

- `HTR` (high transition rate) ≡ `transition_rate ≥ 0.28`
- `YMT` (y-monotonic with transitions) ≡ `large_y_drops == 0` AND `transition_rate ≥ 0.25`
- `SF`  (short-fragment supporting signal) ≡ `short_frac ≥ 0.55` AND `transition_rate ≥ 0.20`
- `baseline_gap_gate` ≡ `gap_rel ≥ 0.15` AND `gap_size ≥ 60`
- `C3_gap_gate` ≡ `gap_rel ≥ 0.30` AND `gap_size ≥ 60`

Warn conditions:

| Rule | Boolean expression |
|---|---|
| **baseline** (current shipped detector) | `baseline_gap_gate AND (HTR OR |{HTR,YMT,SF} fires| ≥ 2)` |
| **C3** | `C3_gap_gate AND (HTR OR |{HTR,YMT,SF} fires| ≥ 2)` |
| **C4** | `baseline_gap_gate AND HTR AND (YMT OR SF)` |
| **C3+C4** | `C3_gap_gate AND HTR AND (YMT OR SF)` |

Document-level decision (all rules): warn iff at least one page on the
document warns under that rule.

## Document-level metrics

| Rule | TP | FP | TN | FN | Precision | Recall | FPR | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 2 | 19 | 0 | 0.333 | 1.000 | 0.095 | 0.500 |
| C3 | 1 | 2 | 19 | 0 | 0.333 | 1.000 | 0.095 | 0.500 |
| C4 | 1 | 2 | 19 | 0 | 0.333 | 1.000 | 0.095 | 0.500 |
| C3+C4 | 1 | 2 | 19 | 0 | 0.333 | 1.000 | 0.095 | 0.500 |

**Key finding: none of the candidates change any document-level
decision.** GeoTopo still fires on at least one page under every rule, so
the document-level FP verdict on both GeoTopo variants persists. The
labelled TP (`multicolumn.pdf` p3) also persists — desirable — but the
document-level numbers do not move.

## Page-level firing counts

Page-level totals are the useful comparison surface:

| Rule | Total page firings | On TN documents | On TP document | On excluded |
|---|---:|---:|---:|---:|
| baseline | 33 | 32 | 1 | 0 |
| C3 | 21 | 20 | 1 | 0 |
| C4 | 23 | 22 | 1 | 0 |
| **C3+C4** | **17** | **16** | **1** | **0** |

- **C3 alone** silences 12 of 32 TN-document page firings (37.5% reduction).
- **C4 alone** silences 10 of 32 (31.3%).
- **C3+C4** silences 16 of 32 (50.0%) — the largest reduction — while
  keeping the single TP-document firing on `multicolumn.pdf` p3.

The warning message emitted by the validator reports the affected page
list. C3+C4 changes that list from "16 pages" to "8 pages" per GeoTopo
variant, which is a real auditability improvement even though the
document-level verdict is unchanged.

## Changed document decisions vs baseline

- **C3**: 0 documents.
- **C4**: 0 documents.
- **C3+C4**: 0 documents.

## Changed page decisions vs baseline

- **C3**: 12 page decisions silenced (all on GeoTopo variants — 6 per copy).
- **C4**: 10 page decisions silenced (all on GeoTopo variants — 5 per copy).
- **C3+C4**: 16 page decisions silenced (all on GeoTopo variants — 8 per copy).

Per-page detail is in
`MULTICOLUMN_CANDIDATE_REPLAY_2026-07-18.json` under
`changed_page_decisions_vs_baseline`. Example (GeoTopo.pdf p9 under
C3+C4): baseline `warn=True` because HTR (TR=0.50) alone triggers; C3+C4
`warn=False` because `gap_rel=0.20 < 0.30` closes the C3 gate.

## GeoTopo per-page firings under each rule

| Rule | Pages that fire on GeoTopo |
|---|---|
| baseline | 9, 15, 23, 29, 35, 38, 39, 40, 56, 71, 76, 80, 81, 91, 101, 108 (16 pages) |
| C3 | 15, 23, 29, 35, 40, 56, 80, 81, 91, 108 (10 pages) |
| C4 | 9, 15, 23, 29, 35, 39, 40, 56, 80, 91, 101 (11 pages) |
| **C3+C4** | **15, 23, 29, 35, 40, 56, 80, 91 (8 pages)** |

`GeoTopo-komprimiert.pdf` is a compression variant of the same source
content and produces identical page firings under every rule.

## Multicolumn.pdf firings under each rule

| Rule | Pages that fire |
|---|---|
| baseline | 3 |
| C3 | 3 |
| C4 | 3 |
| C3+C4 | 3 |

The labelled TP fires on p3 under every rule. **Important**: p3 is a
single-column table page. The actual span-level damage on p1/p2 fires on
no page under any block-level rule; those pages are outside detector
observability by design.

## Acceptance criteria checklist

| Criterion | baseline | C3 | C4 | C3+C4 |
|---|---|---|---|---|
| Materially reduces GeoTopo FPs (page level) | reference | -6/16 per variant (-37.5%) | -5/16 per variant (-31.3%) | **-8/16 per variant (-50%)** |
| Materially reduces GeoTopo FPs (doc level) | reference | 0 | 0 | 0 |
| Preserves labelled block-level TP (`multicolumn.pdf` p3) | ✓ | ✓ | ✓ | ✓ |
| Preserves `test_trans_030_warns` (LLRLLLRLL, 10 blocks) | ✓ | ✓ | ✓ | ✓ |
| Preserves `test_interleaved_warns` (16 alternating blocks) | ✓ | ✓ | ✓ | ✓ |
| Does not depend on the one positive document alone | ✗ (only one positive available) | ✗ | ✗ | ✗ |
| Every changed decision listed explicitly | reference | 12 pages listed | 10 pages listed | 16 pages listed |
| No scoring behaviour changes | ✓ | ✓ | ✓ | ✓ |
| No score or band recommendation included | ✓ | ✓ | ✓ | ✓ |

### Verification of the synthetic-TP preservation

The tests `test_interleaved_warns` and `test_trans_030_warns` do not
invoke the harness; they call `_analyse_page` directly with hand-crafted
blocks. Their diagnostics under each rule:

**`test_interleaved_warns`** (16 blocks alternating L/R at `x0 ∈ {72,
320}`, y-step 20):
- `gap_rel` ≈ 0.413 → passes both baseline and C3 gap gates
- `transition_rate` = 1.0 → HTR True
- `large_y_drops` = 0 → YMT True
- warn under baseline: True (HTR)
- warn under C3: True (gate passes, HTR)
- warn under C4: True (HTR AND YMT)
- warn under C3+C4: True

**`test_trans_030_warns`** (10 blocks LLRLLLRLL at `x0 ∈ {72, 320}`,
y-step 20, 5-word paragraphs):
- `gap_rel` ≈ 0.413 → passes both gates
- `transition_rate` ≈ 0.333 → HTR True (0.333 ≥ 0.28)
- `large_y_drops` = 0 → YMT True (0 == 0 AND 0.333 ≥ 0.25)
- `short_frac` ≈ 1.0 (5-word blocks are below 8-word threshold) → SF True (1.0 ≥ 0.55 AND 0.333 ≥ 0.20)
- warn under baseline: True (HTR)
- warn under C3: True (gate passes, HTR)
- warn under C4: True (HTR AND YMT/SF)
- warn under C3+C4: True

Both synthetic TPs pass under every candidate rule.

## Interpretation

C3+C4 gives the strongest page-level noise reduction (50%) while
preserving both the labelled TP and the synthetic validator TPs. **But
it does not change any document-level decision.** For document-level
precision to improve, GeoTopo would need every one of its 8-remaining
C3+C4-firing pages to be silenced — that requires a stronger rule
(e.g., C1 at threshold 3, which breaks `test_trans_030_warns` and is not
recommended) or a corpus expansion that provides a mixed-layout ground
truth against which stricter rules can be calibrated without
hand-tuning to the two GeoTopo variants.

**The page-level noise reduction is worth shipping** — the validator
message currently claims "16 pages", C3+C4 changes that to "8 pages" per
GeoTopo variant, which is a real auditability improvement for readers.
But precision claims should still be qualified by the corpus-size
limitation.

## Recommendation for the next implementation PR

Ship **C3 + C4 combined** as the detector-only rule change, with three
conditions:

1. Ship exactly the two rule changes above — no other detector logic
   changes bundled.
2. Update the docstring in `aksharamd/plugins/validators/multicolumn.py`
   to state the pinned rule and cite this replay report.
3. Extend `tests/test_plugins/test_multicolumn_validator.py` with the
   explicit synthetic-TP preservation cases documented here, so that
   any future change that regresses `test_trans_030_warns` or
   `test_interleaved_warns` under C3+C4 fails loudly.

**Do not treat this as a calibrated precision improvement at document
level.** The correct summary of what shipping C3+C4 accomplishes is:

- Same document-level P/R/F1 as today.
- 50% reduction in per-page firing noise on the dominant FP class in the
  public corpus.
- No regression on the labelled TP or the synthetic validator TPs.
- Scoring surface unchanged.

Any real precision claim requires corpus expansion (mixed-layout PDFs,
external ParseBench binaries) and a separate re-run of Phase 1.

## Reproducibility

From the repository root:

```
python benchmarks/multicolumn_candidate_replay.py \
    --harness benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.json \
    --labels benchmarks/multicolumn_recalibration_labels.json \
    --output benchmarks/MULTICOLUMN_CANDIDATE_REPLAY_2026-07-18.json
```

The replay is deterministic by construction — it reads previously
captured diagnostics and applies pinned Boolean expressions, no wheel
install or CLI invocation involved.
