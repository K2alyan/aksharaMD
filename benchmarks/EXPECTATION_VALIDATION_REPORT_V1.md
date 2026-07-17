# Phase 5 Table-Expectation Detector — Validation Report v1

**Date:** 2026-07-13  
**Commit:** `9e66af8467bc8308c362ddd93410215620be985e` (`v0.3.6-7-g9e66af8`)  
**Lock record:** `benchmarks/expectation_detector_lock_v1.json`  
**Data file:** `benchmarks/expectation_validation_run.json`

---

## 1. Complete Suite Result

| Group | Tests | Result |
|---|---|---|
| Table suite (expectation + findings + quality + model + renderer) | 199 | 199 passed |
| Compiler + CLI | 42 | 42 passed |
| Chunkers + scoring | 51 | 51 passed |
| Plugin validators | 108 | 108 passed |
| Non-PDF parsers | 112 | 112 passed |
| PDF parser tests (incl. pdf_regression) | 139 | 139 passed |
| Remaining parsers + misc | 162 | 161 passed, 1 skipped |
| Identity + structure + readiness | 136 | 136 passed |
| E2E + golden + robustness + misc | 129 | 129 passed |
| Index + MCP + eval | 70 | 70 passed |
| **Total** | **1148** | **1147 passed, 1 skipped, 0 failures** |

The single skip is in `test_parsers/test_audio.py` (optional audio dependency).

---

## 2. Corpus Composition

| Category | Count | Ground-truth meaning |
|---|---|---|
| `table` docs (positive pool) | 503 | All pages are from parsebench table category; benchmark expects at least one table |
| `text` negative controls | 50 | Pages from text-only category; no table expected |
| `chart` negative controls | 50 | Pages from chart category; no table expected |
| `layout` negative controls | 50 | Mixed layout pages; may contain tables (harder negatives) |
| **Total compiled** | **653** | — |
| Timeouts (scanned/OCR-heavy, excluded from metrics) | 3 | — |
| Errors | 0 | — |

**Ground-truth breakdown for 503 table docs:**

| Class | Count | Definition |
|---|---|---|
| `missed` | 294 | `tables_predicted=False`, `found_expected > 0` — AksharaMD produced no table at all |
| `partial` | 67 | `tables_predicted=True`, `found_actual < found_expected` — some tables extracted but at least one missed |
| `extracted` | 142 | All expected tables found |

---

## 3. Page-Level Labeling Protocol

Labels were derived from the parsebench evaluation report (`_evaluation_report.json`), which runs a structured table comparison (grits metric) between AksharaMD output and benchmark expected markdown.

Per-page fields recorded:
- `gt_class`: missed / partial / extracted / negative
- `grits_con`: continuous score 0–1 (0 = no match or no table produced)
- `tables_found_expected`: count from benchmark ground truth
- `tables_found_actual`: count from AksharaMD structured TABLE blocks
- `warning_count`: W_TABLE_EXPECTED_NOT_EXTRACTED warnings emitted
- `pages_expected_true`: pages where `expected="true"` in the report
- `page_results`: per-page signal details (risk families, signal names, extracted table count)

---

## 4. Development Metrics

### Strict definition: positive = `gt_class=missed` (complete miss)

| Metric | Value |
|---|---|
| TP | 104 |
| FN | 189 |
| FP | 71 |
| TN | 219 |
| Precision | 0.594 |
| Recall | 0.355 |
| F1 | 0.444 |
| FPR | 0.245 |

### Broad definition: positive = `gt_class=missed|partial`

| Metric | Value |
|---|---|
| TP | 105 |
| FN | 255 |
| FP | 71 |
| TN | 219 |
| Precision | 0.597 |
| Recall | 0.292 |
| F1 | 0.392 |
| FPR | 0.245 |

*Note: FP/TN counts are identical across both definitions because the negative pool (extracted + negative controls) is the same.*

---

## 5. Signal-Family Metrics

| Family | Pos fire rate | Neg fire rate | Discrimination |
|---|---|---|---|
| `parser` | 60.8% | 52.4% | Weak (similar rates on pos/neg) |
| `text` | 58.3% | 51.0% | Weak (similar rates on pos/neg) |
| `content` (leader dots) | 4.7% | 0.7% | Strong (6.9× ratio) but rare |
| `archetype` (doc_table_heavy) | 0.0% | 0.0% | Not firing (doc classification rarely = table_heavy) |

**Key observation:** `parser` and `text` families fire at nearly equal rates on positive and negative docs. The `content` family (dot-leader detection) has strong discrimination but fires on only ~5% of positive pages.

---

## 6. Detection Rates by Category

| Category | Warned | Total | Warning rate |
|---|---|---|---|
| `missed` (table, complete miss) | 104 | 294 | 35.4% |
| `partial` (table, partial miss) | 1 | 67 | 1.5% |
| `extracted` (table, success) | 5 | 142 | 3.5% |
| `chart` (negative control) | 37 | 50 | **74.0%** |
| `layout` (negative control) | 19 | 50 | 38.0% |
| `text` (negative control) | 10 | 48 | 20.8% |

---

## 7. False-Positive Analysis

### Root cause: CAPTION_NEARBY fires on chart figure captions

All 71 false positives used the same signal-family combination: `(parser, text)`.

The `text` family fires because `_CAPTION_RE` includes `Figure`, `Chart`, and `Exhibit` in addition to `Table`. Pages in the chart category have headings like:

> "Figure 2.2. Government gross debt as a share of GDP"

These match `CAPTION_NEARBY` ("Figure" + whitespace + non-space). Combined with pdfplumber attempting to analyze chart layout content (and rejecting a candidate), two families fire and `expected="true"`.

### False-positive classes observed

| Class | Mechanism | FP Count |
|---|---|---|
| Chart pages with "Figure X" headings | `CAPTION_NEARBY(Figure...)` + `REJECTED_CANDIDATE` | ~37 |
| Multi-column layout pages with rejected candidates + numeric text | `REJECTED_CANDIDATE` + `NUMERIC_COLUMN_ALIGNMENT` | ~19 |
| Text pages with "Table of" or "Figure" references | `CAPTION_NEARBY` + weak parser rejection | ~10 |
| Table pages with successful extraction but borderline signals | Various | 5 |

### Table-of-contents
Not observed as a major FP driver in this corpus (leader-dot threshold of 3 appears to suppress simple TOC entries).

### Financial narrative
Present but the numeric alignment threshold (3+ qualifying lines) may be suppressing some.

### Headers and footers
Not investigated separately.

---

## 8. Results on the Four Known False-Safe Families

| Family | Docs in corpus | Docs warned | Expected-true pages | Total warnings |
|---|---|---|---|---|
| `fqr-retail-blackrock` | 2 | 2 (100%) | 2 | 2 |
| `VRSK.2012` | 1 | 1 (100%) | 1 | 1 |
| `SERFF_CA` | 104 | 14 (13.5%) | 27 | 14 |
| `FBLB-134215544` | 30 | 4 (13.3%) | 5 | 4 |

The two pages used for development (`fqr-retail-blackrock` and `VRSK.2012`) fire correctly. `SERFF_CA` has 104 pages in parsebench; 27 pages reach `expected="true"` (multiple signals fire) but only 14 emit a warning (other 13 have a table already extracted). The `FBLB-134215544` corpus pages mostly extract tables successfully; the 4 warnings are on pages with genuine misses.

**Warning payload completeness:** All emitted warnings include:
- Affected page number
- Expectation status (`"true"`)
- Contributing signal families
- Raw signal values and evidence
- Rejected candidate details (strategy, bbox, rejection reasons)
- Maturity: `experimental`
- Score impact: `0` (no readiness penalty)

---

## 9. Silent False-Safe Rate Before and After

Before Phase 5: all 294 complete-miss pages were silent (no warning).  
After Phase 5: 104 of 294 complete-miss pages produce `W_TABLE_EXPECTED_NOT_EXTRACTED`.

| Status | Before | After |
|---|---|---|
| Silent complete misses | 294 (100%) | 190 (64.5%) |
| Warned complete misses | 0 (0%) | 104 (35.4%) |

The HIGH-band score is **unchanged** (penalty=0). The improvement is:

> silent failure → warned failure (for 35.4% of complete misses)

---

## 10. Scoring Simulations

All three penalty levels produce the same doc counts (the warning is binary per doc):

| Penalty | Docs impacted | True positives impacted | False positives impacted | FP:TP ratio |
|---|---|---|---|---|
| 10 pts | 176 | 105 | 71 | 0.68 |
| 15 pts | 176 | 105 | 71 | 0.68 |
| 20 pts | 176 | 105 | 71 | 0.68 |

**Interpretation:** For every 10 true-positive docs that would correctly drop in score, 6.8 false-positive docs would incorrectly drop. A penalty is **not justified** at current FPR.

---

## 11. Maturity Decision

**Decision: keep `maturity="experimental"`, `score_penalty=0`.**

### Reasons to keep experimental

1. **FPR is 24.5%** — 1 in 4 negative docs gets warned. This is too noisy for a production signal.
2. **Chart pages fire at 74%** — the primary false positive driver is `CAPTION_NEARBY` matching "Figure X" and "Chart Y" captions, which are structurally identical to table captions in the current regex.
3. **Recall is 35.4%** — 64.5% of missed tables are still silent. The detector is useful for the specific case it was designed for (leader-dot financial schedules, pdfplumber rejections) but does not generalize well.
4. **Partial miss recall is 1.5%** — the detector almost never fires for partial misses.
5. **Scoring simulation is unfavorable** — FP:TP ratio of 0.68 means penalties would harm more valid documents than they protect.

### Reasons not to promote

- No diverse positive cases observed beyond the four known families (leader dots, word splits, TOC-style rejections).
- Development and held-out splits are not separate (single corpus run).
- Negative controls are systematically different from real-world false-positive cases (chart docs reliably trigger).

---

## 12. Primary Finding for Next Cycle

**Narrow `_CAPTION_RE` to match only "Table":**

```python
# Current (too broad):
_CAPTION_RE = re.compile(r'(Table|Figure|Fig\.|Chart|Exhibit)\s+\S+', re.IGNORECASE)

# Proposed (next cycle):
_CAPTION_RE = re.compile(r'Table\s+\S+', re.IGNORECASE)
```

This removes the main FP driver (chart pages with "Figure X" headings) and reduces the `text` family's false-positive rate substantially. Re-validate after this change.

**Secondary investigation:**
- The `parser` family fires at 60.8% on positive pages but also 52.4% on negative pages. It is not selective enough as a single signal. Consider requiring the parser signal to carry a specific high-confidence rejection reason (e.g., `dot_leader`, `prose_cells`) rather than any rejection.
- The `archetype` family fires 0 times. Investigate whether `pdf_classification = "table_heavy"` is being assigned to any docs in the corpus.

---

## 13. Remaining Unsupported Missed-Table Patterns

The 189 silently-missed complete-miss pages include cases where:
- The page has structured tabular content but pdfplumber was skipped (char limit) AND there are no dot-leader markers AND no caption text
- The table is rendered as a multi-column flow that looks like prose to all signals
- The table is in a scanned/image PDF (OCR pipeline not running in this validation)
- The table is embedded as a figure/image and not extractable via text-based analysis

These patterns require different detection strategies (visual/layout analysis, multimodal signals) and are out of scope for the current text-based detector.

---

## Summary

| Gate Item | Status |
|---|---|
| Complete repository suite | 1147 passed, 1 skipped, 0 failures |
| Detector lock record | Written (`expectation_detector_lock_v1.json`) |
| Corpus size | 653 docs (503 table + 150 neg controls) |
| Page labeling | Automated from parsebench grits evaluation |
| Development metrics | Precision=0.594, Recall=0.355, F1=0.444, FPR=0.245 |
| Held-out metrics | Not yet — single corpus run |
| Signal-family metrics | `content` discriminates best; `parser`+`text` over-fire on charts |
| Four false-safe families | fqr(2/2), VRSK(1/1), SERFF(14/104), FBLB(4/30) |
| False positive examples | 37 chart-category, ~34 layout/text — all from `parser`+`text` combination |
| Silent false-safe before | 294/294 (100%) |
| Silent false-safe after | 190/294 (64.5%) — 104 now warned |
| Scoring simulation | FP:TP = 0.68 — penalty not justified at current FPR |
| Maturity decision | Keep `experimental`, penalty=0 |
| Primary recommended fix | Narrow `_CAPTION_RE` to "Table" only; re-validate |
| New features | None — gate review before further development |
