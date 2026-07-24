# Layout Complexity v1 — Empirical Evidence Snapshot

Snapshot of the run this repo produced on 2026-07-24 against the five
public arXiv preprints pinned in `science_corpus.lock.json`. Full
artifacts are regenerated on demand at
`benchmarks/ocr_auto_calibration/results/layout_complexity_v1/` (the
`results/` tree is git-ignored per project convention); this file is
the tracked, human-readable summary a reviewer can consult without
re-running.

Regenerate with:

```bash
python -m benchmarks.ocr_auto_calibration.run_layout_complexity_evidence \
    --hydrate \
    --out benchmarks/ocr_auto_calibration/results/layout_complexity_v1
```

Evidence only. No production routing decision is derived from this
snapshot. See `docs/adr/ocr-auto-policy-v1.md` for the current
routing policy.

Policy version: `1` | Analysis version: `1` | Extractor version: `1`

## Corpus hydration

All five arXiv preprints fetched at their pinned `arxiv_id` +
`arxiv_version`. Bytes were written to the per-user cache root
OUTSIDE the repo tree (Windows `%LOCALAPPDATA%\aksharamd\science_corpus\`).

Observed sha256 (first 12 hex chars) and byte size at capture time:

| doc                              | sha256[:12]    | size (bytes) |
|----------------------------------|----------------|-------------:|
| `attention_1706_03762_v7`        | `bdfaa68d8984` |    2,215,244 |
| `resnet_1512_03385_v1`           | `1e0651b6810e` |      819,383 |
| `bert_1810_04805_v2`             | `5692a5514787` |      775,166 |
| `ddpm_2006_11239_v2`             | `aee5e07a802e` |   10,267,274 |
| `clip_2103_00020_v1`             | `6478b6e571a7` |    6,813,639 |

These observations have NOT been promoted into
`science_corpus.lock.json` (`expected_sha256` remains `null` for every
asset). Promotion is a separate authorised step per the lockfile's
`future_authorised_steps`.

## Layout complexity vs OCR difficulty

| doc                        | pages | chars   | ocr_pages | ocr_frac | score | band    | signals |
|----------------------------|------:|--------:|----------:|---------:|------:|---------|---------|
| `attention_1706_03762_v7`  |    15 |  37,379 |         0 |    0.000 |  83.0 | complex | multi_column, table, figure_caption, fragmented_text |
| `resnet_1512_03385_v1`     |    12 |  53,934 |         0 |    0.000 |  90.0 | complex | multi_column, table, figure_caption, fragmented_text |
| `bert_1810_04805_v2`       |    16 |  57,268 |         0 |    0.000 |  85.0 | complex | multi_column, table, figure_caption, fragmented_text |
| `ddpm_2006_11239_v2`       |    25 |  50,670 |         0 |    0.000 |  72.0 | complex | multi_column, table, figure_caption, fragmented_text, mixed_content |
| `clip_2103_00020_v1`       |    48 | 209,105 |         0 |    0.000 | 100.0 | complex | multi_column, table, figure_caption, fragmented_text, mixed_content |

**Bands**: simple=0, moderate=0, complex=5. **Native-text-dominant**: 5/5.

**Score saturation** (CLIP at 100.0): the sum-of-caps saturates the
100-point scale on documents that fire five signals across dozens of
pages. This is the score cap doing its job — no single signal or run
of pages dominates; the score simply reports "as complex as the
policy can measure".

## False-positive candidates (layout complex, OCR simple)

Threshold: `ocr_required_fraction <= 0.10` AND
`page_char_count_total >= 2,000`. Documents considered: 5. Excluded
as too-short: 0.

**Every single one of the five papers is a false-positive candidate.**
This is the exact scientific-corpus caveat the milestone spec called
out ahead of the run — reproducible arXiv preprints span layout /
figure / table / math diversity but are native-text; a routing rule
that consulted layout complexity alone would send all five to UOC
without OCR benefit.

Interpretation for Commit 4 (Auto Policy v2):

* Layout complexity is a **feature**, not a routing input on its own.
* Auto Policy v1 already gates UOC on `ocr_required_page_count`
  and `ocr_required_fraction`. The correct role for layout complexity
  in v2 is as a **secondary tiebreaker** or **additional guard** on
  the OCR-required path — never as the primary trigger.
* A native-text arXiv paper correctly classifies as `complex` layout.
  That is the evaluator behaving as designed, not a defect.

## Rejected-table-candidate as a UOC-benefit predictor

| doc                        | rejected_table_candidate_total | ocr_required_fraction |
|----------------------------|-------------------------------:|----------------------:|
| `attention_1706_03762_v7`  |                              0 |                 0.000 |
| `resnet_1512_03385_v1`     |                              0 |                 0.000 |
| `bert_1810_04805_v2`       |                              0 |                 0.000 |
| `ddpm_2006_11239_v2`       |                              0 |                 0.000 |
| `clip_2103_00020_v1`       |                              0 |                 0.000 |

**Pearson r undefined** (constant series: both variables are 0 across
the corpus).

Interpretation: on native-text arXiv preprints, the parser's table
quality gate rejected no candidates — so the signal produced no
evidence here. This does NOT confirm or refute the hypothesis that
`rejected_table_candidate_count` predicts UOC benefit; the corpus is
the wrong instrument. A definitive evaluation requires a corpus with
non-trivial rejected-candidate counts AND labeled OCR-treatment
outcomes (UOC-vs-Tesseract structural-gain deltas). Both are out of
scope for this evidence commit.

The Commit 2 conservative caps on `rejected_table_candidate`
(per-page cap 5, document cap 15 points out of 100) remain justified
pending that later run.

## Runtime cost (informational)

Both parse and evaluate times measured with `time.perf_counter`.

The evaluate step (pure Python over the neutral feature model)
completed in well under a millisecond on every document. Parse time
scales with page count and PyMuPDF's font-decoder cost; the CLIP paper
(48 pages, 209 K chars) parsed in roughly a couple of seconds on the
test machine and evaluate finished in single-digit milliseconds.
Layout-complexity classification is negligible compared to any OCR
run.

## Bottom line

1. The layout-complexity evaluator behaves as designed on real
   scientific PDFs: multi-column density + tables + figure captions
   drive high scores in the `complex` band.
2. Layout complexity DOES NOT track OCR difficulty on this corpus.
   Every paper is native-text and needs no OCR; every paper is
   `complex`. This is the false-positive risk pinned by the milestone
   caveat, now confirmed with numbers.
3. `rejected_table_candidate_count` is silent on this corpus. Its
   predictive value for UOC benefit remains unmeasured; the
   conservative cap in Commit 2 stays in place.
4. Auto Policy v2 (Commit 4) must not route on layout complexity
   alone. The OCR-required-page signal remains the primary gate;
   layout complexity is at most a supporting feature.
