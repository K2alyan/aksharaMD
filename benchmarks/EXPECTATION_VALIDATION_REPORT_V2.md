# Phase 5 Table-Expectation Detector — Validation Report v2

**Date:** 2026-07-13  
**Change:** `_CAPTION_RE` narrowed from `(Table|Figure|Fig\.|Chart|Exhibit)\s+\S+` to `\bTable\s+\S+`  
**Corpus:** identical 653-document run (503 table + 150 neg controls, seed=42)  
**Lock record:** `benchmarks/expectation_detector_lock_v1.json` (updated below)  
**Data file:** `benchmarks/expectation_validation_run_v2.json`

---

## 1. Single Change Applied

```python
# v1 (too broad — fired on chart figure captions):
_CAPTION_RE = re.compile(r'(Table|Figure|Fig\.|Chart|Exhibit)\s+\S+', re.IGNORECASE)

# v2 (Table only):
_CAPTION_RE = re.compile(r'\bTable\s+\S+', re.IGNORECASE)
```

No other changes. Two-family independence rule, leader-dot logic, numeric-alignment threshold, parser signals, maturity, and score impact are all unchanged.

---

## 2. V1 vs V2 Head-to-Head

### Strict metrics (positive = gt_class=missed, complete miss)

| Metric | v1 | v2 | Delta |
|---|---|---|---|
| TP | 104 | 95 | −9 |
| FN | 189 | 195 | +6 |
| FP | 71 | 60 | **−11** |
| TN | 219 | 230 | +11 |
| Precision | 0.594 | **0.613** | +0.019 |
| Recall | 0.355 | 0.328 | −0.027 |
| F1 | 0.444 | 0.427 | −0.017 |
| FPR | 0.245 | **0.207** | **−0.038** |

### Summary

The change removed 11 false positives and lost 9 true positives. Precision improved by +1.9 pp. FPR dropped by −3.8 pp. F1 declined marginally (−0.017) because the recall loss slightly outweighed the precision gain.

---

## 3. Detection Rate by Category

| Category | v1 rate | v2 rate | Delta |
|---|---|---|---|
| `chart` (negative) | 74.0% | **60.0%** | **−14.0 pp** |
| `layout` (negative) | 38.0% | 36.0% | −2.0 pp |
| `text` (negative) | 20.8% | 16.7% | −4.2 pp |
| `table` (positive pool) | 21.9% | 20.0% | −1.9 pp |
| `missed` (complete miss) | 35.4% | 32.3% | −3.1 pp |

The chart false-positive rate fell from 74% to 60%. It remains high. The remaining 30 chart false positives are now driven by `parser + numeric_column_alignment` — chart pages with numerical data axes generate 3+ qualifying lines and a pdfplumber rejection, satisfying the two-family rule without any caption signal.

---

## 4. Remaining False-Positive Root Cause

After the caption fix, all 30 remaining chart FPs use `(parser, text)` via `numeric_column_alignment`:

- Chart pages often display axis labels or data callouts as individual text runs
- pdfplumber interprets the chart bounding box as a table candidate and rejects it
- Multiple numeric-labeled lines (e.g., year-value pairs along axes) sum to ≥3 qualifying lines
- Two families fire → `expected="true"`

Five remaining chart FPs still include `caption_nearby` — these are chart pages that also happen to contain "Table X" text (probably in footnotes or adjacent figure descriptions).

---

## 5. Four Known False-Safe Families

| Family | v1 | v2 | Status |
|---|---|---|---|
| `fqr-retail-blackrock` | 2/2 | 2/2 | Unchanged |
| `VRSK.2012` | 1/1 | 1/1 | Unchanged |
| `SERFF_CA` | 14/104 | 12/104 | −2 (pages that relied on figure captions) |
| `FBLB-134215544` | 4/30 | 3/30 | −1 |

The two original development pages still fire. Two SERFF_CA pages and one FBLB page that relied on figure/exhibit captions no longer fire — those were marginal warnings that are correctly removed.

---

## 6. Scoring Simulation (penalty=10)

| Version | Impacted | TP | FP | FP:TP |
|---|---|---|---|---|
| v1 | 176 | 105 | 71 | 0.676 |
| v2 | 156 | 96 | 60 | **0.625** |

The FP:TP ratio improved from 0.676 to 0.625. A penalty would still harm approximately 6 valid documents for every 10 genuinely-missed-table documents corrected. Not yet acceptable.

---

## 7. Maturity Decision

**Decision: keep `maturity="experimental"`, `penalty=0`, `scoring_policy_version=1.0`.**

### What v2 achieved

- Removed 11 false positives (all chart-caption-driven)
- Reduced FPR from 0.245 to 0.207
- Cut chart FP rate from 74% to 60%
- Lost 9 true positives (3.1 pp recall loss) — acceptable tradeoff
- The four original false-safe families remain correctly detected

### Why it is still not ready for scoring

1. **Chart FP rate is 60%** — majority driven by `parser + numeric_column_alignment`. The caption fix removed one layer; the numeric-alignment + chart-rejection combination is now the dominant driver.
2. **Overall FPR is 20.7%** — 1 in 5 negative docs still gets warned.
3. **FP:TP ratio of 0.625** — not a favorable basis for a production penalty.
4. **Recall is 32.8%** — two-thirds of complete table misses remain silent.
5. No held-out split.

---

## 8. What the Next Change Should Investigate

The remaining 30 chart false positives all use `parser + numeric_column_alignment`. Candidate investigations (do not implement until after reviewing these results):

**A. Require the parser signal to carry a substantive rejection reason.**  
Currently `REJECTED_CANDIDATE` fires if any candidate is rejected for any reason (`too_short`, `too_few_cols`, `word_split`, etc.). Restricting to high-signal reasons (`dot_leader`, `prose_cells`, `word_split`) would reduce parser family firing rate on chart pages without affecting the financial-schedule cases it was designed for.

**B. Raise the numeric-alignment threshold.**  
Increasing from 3 qualifying lines to 5 would suppress chart-axis data. Risk: may also reduce recall on genuine financial tables with few rows.

**C. Investigate whether `REJECTED_CANDIDATE` is always necessary.**  
The two-family rule requires any two families. On chart pages, `parser` fires almost always (pdfplumber tries to analyze charts). This makes `parser + anything` = `expected="true"` for almost all chart pages. Options: require `content` or `archetype` to be one of the two families, or require `parser + content` specifically.

These are hypotheses only. Do not implement until the next validation gate is scoped.

---

## 9. Cumulative State

| Property | Value |
|---|---|
| Corpus | 653 docs (503 table + 150 neg controls) |
| Strict precision | 0.613 |
| Strict recall | 0.328 |
| FPR | 0.207 |
| Chart FP rate | 60% |
| Silent complete misses | 199/294 (67.7%) |
| Warned complete misses | 95/294 (32.3%) |
| Score penalty | 0 |
| Maturity | experimental |
| Scoring policy version | 1.0 |
| Primary remaining FP driver | `parser + numeric_column_alignment` on chart pages |
