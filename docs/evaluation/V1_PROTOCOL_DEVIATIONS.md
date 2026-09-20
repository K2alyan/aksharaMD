# V1 Protocol Deviations and Scope Limitations

This document records every deviation from the original preregistered protocol
(STUDY_FREEZE_MANIFEST_V1.md) that was identified during or after execution.
It is a study artifact, not a retrospective justification. Each entry records
what was planned, what was implemented, why the deviation occurred, and what
the evidentiary consequence is.

Entries are append-only and dated. No entry may be removed or revised; add a
correction entry if a prior entry is wrong.

---

## D-001 — Track C primary metric: EM retained; token F1 / numeric EM exploratory

**Date:** 2026-09-20

### Planned (freeze §4, line 144/351)

```
degradation = EM(reference) − EM(parsed)
```

Spearman ρ(readiness_score, EM degradation) is the V1 primary endpoint.

### Implemented

Phase 2 computes two metrics per QA pair:

- `em_score`: string exact-match (frozen primary endpoint, per freeze)
- `primary_score`: corpus-appropriate metric (exploratory; added post-freeze):
  - QASPER: max-annotator token F1 (standard QASPER evaluation metric)
  - TAT-DQA: numeric-normalized EM with scale handling

### Why the exploratory metric was added

String EM returns ≈ 0 for all parsers on QASPER extractive/abstractive
questions, because natural-language answers almost never match exactly.
This makes EM a degenerate discriminator for QASPER. The issue was
identified after Phase 1 execution but before Phase 2 LLM evaluation.

### Evidentiary consequence

- **V1 primary analysis**: Spearman ρ on `em_score` degradation. If EM is
  degenerate (near-zero variance across parsers), Claim 4 is recorded as
  "NOT ESTABLISHED" under V1.
- **V1 exploratory analysis**: Spearman ρ on `primary_score` degradation,
  labeled exploratory. Results are descriptive only; they do not constitute
  evidence for Claim 4 in V1.
- **V2 action**: Preregister corpus-appropriate metrics (token F1 / numeric EM)
  as primary endpoints before any further LLM evaluation.

---

## D-002 — Track C scoring: Markdown-output-only, baseline 95 (not PDF baseline 87)

**Date:** 2026-09-20

### Planned (freeze §1)

Freeze §1 specifies:

```
Format baseline: PDF = 87
Scoring logic: Additive penalties + sequential score caps; no weighted normalization
```

The implied contract is that AksharaMD scores the parsed output in the context
of the original source PDF (baseline 87), activating source/geometry detectors
that compare parsed content against PDF content.

### Implemented

Phase 3 readiness scoring feeds the parser's Markdown output to the AksharaMD
Compiler as a `.md` file. The Compiler routes `.md` input through its Markdown
parser with format baseline **95** (not 87). Source/geometry detectors that
require an original source PDF — specifically `W_TABLE_MISSING` and
`W_DROPPED_CONTENT` when used in source-comparison mode — are **not activated**.

Affected call sites (all V1, all Markdown-only):
- `benchmarks/eval_v1/stage1/run_track_c.py` — `_score_markdown()`
- `benchmarks/eval_v1/stage2/score_olmocr.py` — `_run_aksharamd_scoring()`
- `benchmarks/eval_v1/stage1/run_track_a_doclaynet.py` — `_run_readiness_scorer()`

### Why this occurred

Resolution B (source+candidate scoring: supply both the original PDF and the
parsed Markdown to the Compiler) was not implemented before Phase 1 execution.
The Markdown-only path was the path of least resistance. The discrepancy was
identified during post-execution review.

### Evidentiary consequence

- All observed readiness scores in V1 are on the **Markdown-output scale
  (baseline 95)**, not the PDF scale (baseline 87).
- Detectors that measure content loss relative to the source PDF are
  **UNMEASURED in V1**. Any correlation between readiness score and downstream
  QA quality therefore cannot be attributed to source-comparison signals.
- The freeze band boundaries (HIGH ≥ 85 | OK 70–84 | RISKY 50–69 | POOR < 50)
  remain unchanged in V1, but they apply to the Markdown baseline, meaning the
  practical score range is compressed near the top (most parsers score 87–95
  on clean academic PDFs).
- **V2 action**: Implement source+candidate scoring (supply original PDF to the
  Compiler alongside parsed Markdown) before any further Track C or olmOCR
  Stage 2 execution.

---

## D-003 — Band thresholds corrected after initial implementation

**Date:** 2026-09-20

### Planned (freeze §1)

```
HIGH ≥ 85 | OK 70–84 | RISKY 50–69 | POOR < 50
```

### Implemented (original, incorrect)

```python
BAND_HIGH = 0.85
BAND_OK   = 0.65   # WRONG — should be 0.70
BAND_RISKY = 0.40  # WRONG — should be 0.50
```

This error appeared in both `aggregate_olmocr.py` and `score_olmocr.py`.

### Corrected on 2026-09-20

Thresholds corrected to match the freeze:
```python
BAND_HIGH  = 0.85
BAND_OK    = 0.70
BAND_RISKY = 0.50
```

### Evidentiary consequence

Any Track B allocation generated before this correction used wrong band
boundaries. The allocation must be regenerated from scratch after complete,
valid Stage 2 olmOCR scoring using the corrected thresholds.

---

## D-004 — olmOCR Spearman implementation corrected

**Date:** 2026-09-20

### Defect

`aggregate_olmocr.py` used the simplified d² shortcut formula:
```
ρ = 1 − 6Σd² / (n(n²−1))
```
This formula produces incorrect results when ties are present. A constant
predictor (all readiness scores identical) returned ρ = 0.5 instead of NaN.
Readiness scores are discrete integers (0–100), so ties are common.

### Corrected on 2026-09-20

Replaced with Pearson correlation on ranks (tie-safe), matching the
implementation in `aggregate_track_c.py`.

### Evidentiary consequence

Any previously published Spearman ρ values from `aggregate_olmocr.py` must
be recomputed. The direction of the bias depends on the tie pattern; the
magnitude is unknown without recomputation.

---

## D-005 — Numeric EM string-fallback corrected

**Date:** 2026-09-20

### Defect

`_compute_numeric_em` in `run_track_c.py` fell through to string EM after
failed numeric comparison. Because `_normalize_for_em` strips all
`string.punctuation` characters (including `-` and `.`), this caused:

- `−5` ≡ `5` → 1.0 (minus sign stripped)
- `1.2` ≡ `12` → 1.0 (decimal point stripped)

### Corrected on 2026-09-20

String EM fallback is now only attempted when both prediction and gold fail
to parse as numbers (i.e., non-numeric text like month names). When both
parse as numbers but differ, the result is 0.0 with no fallback.

### Evidentiary consequence

TAT-DQA numeric scores from any evaluation run using the defective scorer
are unreliable and must be recomputed. No paid Phase 2 results existed at
the time of correction (all Phase 2 records had `llm_evaluated=False`).

---

## Planned V2 actions summary

| Item | V2 action |
|------|-----------|
| Token F1 / numeric EM | Preregister as primary endpoints before execution |
| Source+candidate scoring | Implement and validate before Phase 1 |
| Band boundary validation | Add automated test against freeze manifest values |
| Spearman implementation | Add test for constant and tied predictors |
