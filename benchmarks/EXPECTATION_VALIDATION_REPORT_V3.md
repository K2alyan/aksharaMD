# Phase 5 Table-Expectation Detector — Validation Report v3

**Date:** 2026-07-13  
**Change:** `parser + numeric_column_alignment` alone (without CAPTION_NEARBY, LEADER_DOT_ROWS, or DOC_TABLE_HEAVY) is downgraded from `expected="true"` to `expected="unknown"`. `_CAPTION_RE` and numeric-alignment threshold are unchanged from v2.  
**Corpus:** identical 653-document run (503 table + 150 neg controls, seed=42)  
**Lock record:** `benchmarks/expectation_detector_lock_v3.json`  
**Data file:** `benchmarks/expectation_validation_run_v3.json`

---

## 1. Single Change Applied

```python
# v3: add family-independence exception in compute_table_expectation()
risk_signals = [s for s in signals if s.status == "risk"]
risk_families = {s.family for s in risk_signals}
risk_names    = {s.name   for s in risk_signals}

# NEW: parser + numeric_alignment without a corroborating independent cue is not sufficient.
# Both react to the same visual structure on chart pages (data axes, callouts, annotations)
# and are empirically correlated rather than independent.
_parser_plus_numeric_only = (
    risk_families == {"parser", "text"}
    and TableExpectationSignalName.NUMERIC_COLUMN_ALIGNMENT in risk_names
    and TableExpectationSignalName.CAPTION_NEARBY not in risk_names
)

if len(risk_families) >= 2 and not _parser_plus_numeric_only:
    expected = "true"
elif risk_signals:
    expected = "unknown"
else:
    expected = "false"
```

No changes to `_CAPTION_RE`, `_LEADER_DOT_RE`, numeric-alignment threshold, leader-dot threshold, maturity, or score impact.

---

## 2. V2 vs V3 Head-to-Head

### Strict metrics (positive = gt_class=missed, complete miss)

| Metric | v2 | v3 | Delta |
|---|---|---|---|
| TP | 95 | **50** | −45 |
| FN | 195 | **244** | +49 |
| FP | 60 | **11** | **−49** |
| TN | 230 | **281** | +51 |
| Precision | 0.613 | **0.820** | **+0.207** |
| Recall | 0.328 | **0.170** | −0.158 |
| F1 | 0.427 | **0.282** | −0.145 |
| FPR | 0.207 | **0.038** | **−0.169** |

### Summary

The change removed 49 false positives and lost 45 true positives. Precision improved by +20.7 pp. FPR dropped by −16.9 pp to 3.8%. F1 declined significantly (−0.145) because the recall loss substantially outweighed the precision gain.

---

## 3. Three-Version Comparison

| Metric | v1 | v2 | v3 |
|---|---|---|---|
| TP | 104 | 95 | **50** |
| FP | 71 | 60 | **11** |
| Precision | 0.594 | 0.613 | **0.820** |
| Recall | 0.355 | 0.328 | **0.170** |
| FPR | 0.245 | 0.207 | **0.038** |
| F1 | 0.444 | 0.427 | **0.282** |
| Chart FP rate | 74% | 60% | **10%** |
| FP:TP ratio | 0.676 | 0.625 | **0.220** |

---

## 4. Detection Rate by Category

| Category | v2 rate | v3 rate | Delta |
|---|---|---|---|
| `chart` (negative) | 60.0% | **10.0%** | **−50.0 pp** |
| `layout` (negative) | 36.0% | **6.0%** | −30.0 pp |
| `text` (negative) | 16.7% | **2.0%** | −14.7 pp |
| `extracted` (table, success) | 2.1% | **1.4%** | −0.7 pp |
| `partial` (table, partial miss) | 1.5% | **0.0%** | −1.5 pp |
| `missed` (complete miss) | 32.3% | **17.0%** | −15.3 pp |

The chart false-positive rate dropped from 60% to 10%. The rule change primarily targeted the `parser + numeric_alignment` combination that had been driving chart FPs since v1; this layer is now consistently downgraded to "unknown".

---

## 5. Remaining False-Positive Root Cause

All 11 remaining false positives use `(parser, text via CAPTION_NEARBY)`. They are structurally valid firings — the pages contain text matching `\bTable\s+\S+` — but the table reference appears in a footnote, caption of an adjacent figure, or cross-reference rather than indicating a missing table on that specific page.

| Category | FP Count | Signal combination |
|---|---|---|
| `chart` | 5 | `parser + caption_nearby` — "Table X" in footnotes |
| `layout` | 3 | `parser + caption_nearby` — "Table" references in multi-col text |
| `text` | 1 | `parser + caption_nearby` — "Table" reference in running prose |
| `extracted` (grits < 1.0) | 2 | `parser + caption_nearby` — table extracted but grits score imperfect |

These cannot be removed by adjusting the current signal set without also removing true positives. They represent the irreducible floor of the `caption_nearby` + `parser` combination — pages where "Table X" text exists but refers to a different page or is incidental.

---

## 6. True Positive Signal Breakdown

The 50 TPs use the following risk-family combinations on their flagged pages:

| Signal families | TP count |
|---|---|
| `{parser, text via caption_nearby}` | 35 |
| `{content, parser, text}` | 13 |
| `{content, text}` | 2 |

The `content` family (LEADER_DOT_ROWS) remains a high-precision signal — both combos that include it are strong positives.

---

## 7. Four Known False-Safe Families

| Family | v1 | v2 | v3 | Notes |
|---|---|---|---|---|
| `fqr-retail-blackrock` | 2/2 | 2/2 | **2/2** | Unchanged — uses `content + text` (dot-leader + numeric alignment) |
| `VRSK.2012` | 1/1 | 1/1 | **1/2** | Original target doc fires; a second VRSK doc in corpus does not |
| `SERFF_CA` | 14/104 | 12/104 | **9/193** | Corpus grew; pages using only `parser + numeric` now "unknown" |
| `FBLB-134215544` | 4/30 | 3/30 | **0/30** | All pages were relying on `parser + numeric_alignment` only |

**Key observation:** `fqr-retail-blackrock` is the canonical test case for the detector (the original development doc); it still fires via `content + text` (leader dots + numeric alignment). The rule exception does not affect `content` family combos.

`FBLB-134215544` dropping to 0/30 confirms that all FBLB pages were relying on the `parser + numeric_alignment` combination. This is a direct tradeoff: these pages are now "unknown" rather than "true" — a warning is no longer emitted. Whether FBLB pages are true misses or correctly extracted requires separate investigation.

---

## 8. Scoring Simulation (penalty=10)

| Version | Docs impacted | TP docs | FP docs | FP:TP |
|---|---|---|---|---|
| v1 | 176 | 105 | 71 | 0.676 |
| v2 | 156 | 96 | 60 | 0.625 |
| v3 | **61** | **50** | **11** | **0.220** |

The FP:TP ratio improved from 0.625 to 0.220. A penalty at v3 precision would harm approximately 2 documents for every 10 genuinely-missed-table documents corrected. This ratio is approaching a range where a penalty could be defensible, but the absolute recall (17%) and the FBLB regression are significant concerns.

---

## 9. Maturity Decision

**Decision: keep `maturity="experimental"`, `penalty=0`, `scoring_policy_version=1.0`.**

### What v3 achieved

- Removed 49 false positives (all `parser + numeric_alignment` driven)
- Reduced FPR from 0.207 to 0.038
- Cut chart FP rate from 60% to 10%
- Cut FP:TP ratio from 0.625 to 0.220
- Lost 45 true positives (−15.3 pp recall) — a significant recall cost

### Why it is still not ready for scoring

1. **Recall is 17.0%** — 83% of complete table misses remain silent. The detector warns on the high-confidence subset but misses the large majority.
2. **FBLB-134215544 dropped to 0/30** — the rule exception eliminated all FBLB warnings. If FBLB pages represent genuine misses, this is an important false-safe regression that needs investigation before a penalty is added.
3. **45 TP lost** — the precision improvement comes at a steep recall cost. The 45 lost TPs were using `parser + numeric_alignment` combinations that may still be genuinely missed tables.
4. **FP floor at 11** — the remaining 11 FPs are structurally irreducible without additional context signals beyond the current text-based set (e.g., page-level cross-reference detection, footnote classification).
5. **No held-out split** — all metrics are development metrics on the same corpus.

### What has improved enough to reconsider scoring

- FPR of 3.8% is now acceptably low.
- Precision of 82% means 8 in 10 warnings are actionable.
- FP:TP of 0.22 means a penalty would harm fewer than 2.5 docs per 10 corrected.
- Chart pages are largely protected (10% FP rate).

---

## 10. What the Next Change Should Investigate

**The 45 lost TPs** used `parser + numeric_alignment` only. Before accepting this recall loss permanently, evaluate whether that combination is recoverable via a more targeted restriction — e.g., restricting the rule exception to specific chart-context cues rather than any `parser + numeric` pair.

**FBLB regression** — all 30 FBLB docs are now silent. Inspect what rejection reasons those pages produce and whether a high-signal reason (e.g., `prose_cells`, `word_split`, `dot_leader`) is present in any of them. If so, narrow the exception to low-signal reasons only.

**Possible refinement (do not implement without gate):**

```python
# Current exception (blocks all parser+numeric combos):
_parser_plus_numeric_only = (
    risk_families == {"parser", "text"}
    and NUMERIC_COLUMN_ALIGNMENT in risk_names
    and CAPTION_NEARBY not in risk_names
)

# Candidate refinement (only blocks low-signal parser firings):
_LOW_SIGNAL_REASONS = {"too_short", "too_few_cols", "empty_cells", "single_row"}
_parser_is_low_signal = all(
    set(c.get("rejection_reasons", [])) <= _LOW_SIGNAL_REASONS
    for c in rejected_candidates
)
_parser_plus_numeric_only = (
    risk_families == {"parser", "text"}
    and NUMERIC_COLUMN_ALIGNMENT in risk_names
    and CAPTION_NEARBY not in risk_names
    and _parser_is_low_signal  # only downgrade when parser rejection is weak
)
```

This would preserve warnings on pages where pdfplumber found substantive evidence (`dot_leader`, `prose_cells`, `word_split`) while still suppressing warnings on chart pages where the rejection reason is generic.

---

## 11. Cumulative State

| Property | v1 | v2 | v3 |
|---|---|---|---|
| Corpus | 653 docs | 653 docs | 653 docs |
| Strict precision | 0.594 | 0.613 | **0.820** |
| Strict recall | 0.355 | 0.328 | **0.170** |
| F1 | 0.444 | 0.427 | **0.282** |
| FPR | 0.245 | 0.207 | **0.038** |
| Chart FP rate | 74% | 60% | **10%** |
| Silent complete misses | 190/294 (64.6%) | 199/294 (67.7%) | **244/294 (83.0%)** |
| Warned complete misses | 104/294 (35.4%) | 95/294 (32.3%) | **50/294 (17.0%)** |
| FP:TP ratio | 0.676 | 0.625 | **0.220** |
| Score penalty | 0 | 0 | 0 |
| Maturity | experimental | experimental | experimental |
| Scoring policy version | 1.0 | 1.0 | 1.0 |
| Primary remaining FP driver | `parser + numeric` on charts | `parser + numeric` on charts | `parser + caption` — structural floor |
