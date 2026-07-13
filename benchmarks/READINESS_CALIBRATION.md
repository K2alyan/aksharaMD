# AksharaMD Readiness Score — Calibration Contract

**Status: FROZEN 2026-07-13**
**Version: 1.0**

This document defines the methodology for calibrating AksharaMD's readiness score (0–100) against empirical document quality as measured by ParseBench metrics. It is a specification, not a report. Numbers are filled in after the baseline run; the methodology cannot change once the run begins.

---

## 1. Calibration question

AksharaMD emits a readiness score (0–100) and classifies every compiled document into one of four bands:

| Band | Score range | Default role |
|------|-------------|--------------|
| HIGH | ≥ 85 | Accept without review |
| OK | 70–84 | Accept with optional spot-check |
| RISKY | 50–69 | Flag for review before indexing |
| POOR | < 50 | Route to quarantine |

The calibration question is: **do these bands accurately predict empirical extraction quality?**

A band is well-calibrated when documents in it show the expected quality range on independently measured metrics. Two failure modes matter:

- **False-safe**: readiness ≥ 70 but empirical quality is poor — the parser claims success while meaningful content is missing. A false-safe document enters the index silently corrupted.
- **False-risky**: readiness < 70 but empirical quality is good — the parser is overly cautious, wasting downstream review capacity or quarantining usable documents.

The calibration run does not change the score formula. It measures false-safe and false-risky rates against a frozen corpus so that future threshold adjustments are evidence-based.

---

## 2. Corpus specification

### 2.1 Source

Calibration documents are drawn from the ParseBench dataset (llamaindex/ParseBench, HuggingFace). ParseBench provides both source PDFs and human-verified ground truth annotations covering text content, inline formatting, tables, and charts. Documents in the calibration corpus must have valid ground truth in at least one of the four annotated categories.

### 2.2 Size and stratification

Target: **75 documents** (minimum 50, maximum 100).

Stratify across five tiers:

| Tier | Description | Target count |
|------|-------------|-------------|
| T1 — simple prose | Text-only or text + headings; no tables | 15 |
| T2 — light structure | Headings + lists + occasional inline table (1–3 tables) | 15 |
| T3 — table-heavy | ≥ 4 tables; tables are semantically load-bearing | 15 |
| T4 — multi-column | Two or more text columns on any page | 15 |
| T5 — boundary | Readiness score 60–79 on the initial AksharaMD run; tests both sides of the OK/RISKY boundary | 15 |

Tier T5 is populated after the initial full-dataset run described in Section 6.1 (score-only pass) and before the evaluation run. Documents appearing in T1–T4 cannot also appear in T5.

### 2.3 Splits

| Split | Size | Purpose |
|-------|------|---------|
| Dev | 25 docs | Inspect freely; use to catch methodological errors before locked run |
| Locked validation | 35 docs | Final numbers; do not inspect until the dev run passes review |
| Challenge | 15 docs | Drawn from T5 only; adversarial cases at the OK/RISKY boundary |

Dev and locked-validation documents are assigned at corpus construction time by deterministic shuffle (seed = 20260713). Challenge documents are the 15 T5 documents with readiness scores closest to the 70-point boundary (7 above, 8 below, or vice versa as availability permits).

### 2.4 Selection procedure

1. Start with all ParseBench text-category documents (text_content.jsonl, n ≈ 506).
2. Exclude documents with fewer than 200 characters of ground-truth text (empty-shell PDFs).
3. Classify remaining documents into T1–T4 using ParseBench metadata. If metadata is insufficient, classify by inspecting the ground-truth block counts.
4. Sample deterministically from each tier (seed = 20260713) until the tier target is met or the tier is exhausted.
5. After the score-only pass (Section 6.1), assign T5 documents.
6. Apply the dev/locked-validation/challenge split.

---

## 3. Evaluation dimensions and metric formulas

Metrics are computed per document. Each metric produces a value in [0.0, 1.0] unless noted.

### 3.1 Text fidelity

**What it measures:** Fraction of ground-truth text content recovered in AksharaMD output.

**Primary metric — character-level recall (Levenshtein):**
Provided by ParseBench `text_similarity` metric (autoevals Levenshtein, score normalized to [0, 1]). This is the existing metric.

**Secondary metric — sentence-level recall:**
For each sentence in the ground-truth text (split at `.`, `?`, `!`, minimum 10 characters):
```
sentence_recall = |sentences found in output| / |sentences in ground truth|
```
"Found" means the sentence (after stripping whitespace and normalising Unicode) appears as a substring of the AksharaMD output. This metric needs to be built; see Section 7.

**Reported as:** `text_char_recall` (existing), `text_sentence_recall` (to build).

### 3.2 Reading order

**What it measures:** Whether AksharaMD outputs blocks in the reading order a human would follow.

**Metrics:** `reading_order_adjacent_accuracy` and `reading_order_pairwise_accuracy` from ParseBench attribution/core.py.

- Adjacent accuracy: fraction of consecutive ground-truth block pairs where the predicted order is preserved.
- Pairwise accuracy: fraction of all ground-truth block pairs where predicted order is preserved.

IoA threshold: 0.3 (default). Ground-truth blocks of type formula, caption, or explicit-only are excluded.

**Status:** Existing in ParseBench. Currently not collected for the AksharaMD pipeline. Must be enabled in the adapter; see Section 7.

### 3.3 Heading structure

**What it measures:** Whether headings are detected at the correct level and in the correct order.

**Metric:** `heading_accuracy` from ParseBench `header_accuracy_metric.py`.

The metric checks: heading text similarity, heading level correctness, heading order, and hierarchy depth (H1 → H2 → H3 without skipping). Returns a composite score in [0, 1].

**Status:** Existing in ParseBench. Must be enabled in adapter.

### 3.4 Table fidelity

**What it measures:** How accurately AksharaMD reconstructs table cell content.

**Primary metric — GriTS Con:**
```
GriTS_Con = F-score over cell text similarity
          = 2 * precision_con * recall_con / (precision_con + recall_con)
```
Cell text similarity uses LCS: `2 * |LCS(s1, s2)| / (|s1| + |s2|)`. Rows and columns are matched by Hungarian algorithm. Documents with no tables in ground truth receive `table_grits_con = NaN` and are excluded from table-dimension aggregates.

**Secondary metric — TEDS:**
Tree Edit Distance Similarity (full content). Used as a cross-check on GriTS.

**Status:** Both metrics exist in ParseBench. GriTS is already collected for the AksharaMD pipeline.

### 3.5 Visual coverage

**What it measures:** Whether figures, charts, and images are captured or described.

**Status:** Not in scope for this calibration run. ParseBench corpus is entirely text-layer PDFs with no meaningful figure ground truth in the text_content split. This dimension is deferred to a scanned-document calibration corpus. Marked as `visual_coverage = NaN` for all documents in this run.

### 3.6 Hallucination rate

**What it measures:** Fraction of AksharaMD output tokens that have no counterpart in the source document.

**Metric:** Local Attribution Precision (LAP) from ParseBench attribution/core.py. For each predicted block, LAP checks whether its text tokens appear in spatially overlapping ground-truth elements. Low LAP = high hallucination.

```
LAP = |tokens attributed to GT| / |total tokens in predicted output|
```

For text-only evaluation (no bounding boxes), attribution falls back to string containment: a token is "attributed" if the predicted block's text is a substring of the ground-truth document.

**Status:** LAP exists in ParseBench for layout-aware evaluation. String-containment fallback needs to be built for the text-only case; see Section 7.

### 3.7 Duplication and noise

**What it measures:** Fraction of output that is repeated or is junk (glyph artifacts, OCR noise).

**Metric:** Duplication ratio — fraction of 4-grams in the output that appear more than once:
```
dup_ratio = |repeated 4-grams| / |total 4-grams|
```
Junk detection is out of scope for this calibration (no ground-truth junk labels in ParseBench). Duplication ratio is computed directly from AksharaMD output with no ground-truth comparison.

**Status:** Needs to be built; see Section 7.

### 3.8 Downstream QA accuracy

**What it measures:** Whether the text extracted by AksharaMD is sufficient to answer factual questions about the document.

**Metric:** `qa_answer_match` from ParseBench `qa/answer_comparison.py`. For each question, a QA model answers using the AksharaMD-compiled text as context; the answer is compared to the ground-truth answer using the existing comparison logic (single-choice, multiple-choice, numerical, free-text).

**Note:** QA evaluation requires a separate QA model call per question and is not part of ParseBench's text-content evaluation. QA ground truth comes from the `text_content.jsonl` rules where the question field is populated. Documents without QA rules are excluded from this dimension.

**Status:** Existing metric in ParseBench. Not yet wired for the text pipeline; needs adapter work; see Section 7.

---

## 4. Score-band hypotheses

These are the claims to test. They are starting hypotheses, not assertions. If the data refutes them, the score formula is revised in a subsequent version; the methodology here is not changed.

### 4.1 HIGH band (≥ 85)

Hypothesis: documents in the HIGH band were processed with high fidelity.

| Metric | Threshold | Target pass rate |
|--------|-----------|-----------------|
| `text_char_recall` | ≥ 0.90 | ≥ 92% of HIGH docs |
| `text_sentence_recall` | ≥ 0.85 | ≥ 90% of HIGH docs |
| `reading_order_adjacent_accuracy` | ≥ 0.85 | ≥ 88% of HIGH docs |
| `table_grits_con` (docs with tables) | ≥ 0.70 | ≥ 80% of HIGH docs with tables |
| `heading_accuracy` (docs with headings) | ≥ 0.75 | ≥ 85% of HIGH docs with headings |
| `qa_answer_match` (docs with QA rules) | ≥ 0.80 | ≥ 85% of HIGH docs with QA rules |

**Target calibration claim (from roadmap):** "Documents scoring 85+ recovered at least 95% of labeled text in 92% of the evaluation corpus." Operationalised here as `text_char_recall ≥ 0.90` in ≥ 92% of HIGH-band documents.

### 4.2 OK band (70–84)

Hypothesis: documents in the OK band are usable but may have minor gaps.

| Metric | Threshold | Target pass rate |
|--------|-----------|-----------------|
| `text_char_recall` | ≥ 0.75 | ≥ 80% of OK docs |
| `text_sentence_recall` | ≥ 0.65 | ≥ 78% of OK docs |
| `reading_order_adjacent_accuracy` | ≥ 0.70 | ≥ 75% of OK docs |
| `table_grits_con` (docs with tables) | ≥ 0.50 | ≥ 70% of OK docs with tables |

### 4.3 RISKY band (50–69)

Hypothesis: documents in the RISKY band show measurable degradation.

| Metric | Expected |
|--------|----------|
| `text_char_recall` | Mean < 0.75; ≥ 30% of docs below 0.60 |
| `reading_order_adjacent_accuracy` | Mean < 0.70 |
| `table_grits_con` | Mean < 0.50 |

No pass-rate threshold is defined for RISKY. The calibration goal is to confirm that RISKY documents are meaningfully worse than OK, not to set acceptance criteria.

### 4.4 POOR band (< 50)

Hypothesis: documents in the POOR band have severe extraction failures.

| Metric | Expected |
|--------|----------|
| `text_char_recall` | Mean < 0.50; ≥ 50% of docs below 0.40 |
| `qa_answer_match` | Mean < 0.50 |

---

## 5. False-safe and false-risky definitions

These are the operational definitions used in the calibration report.

**False-safe:** readiness ≥ 70 (OK or HIGH) AND `text_char_recall` < 0.60.

Rationale: a document accepted for indexing should have recovered at least 60% of its character content. Below this threshold, retrieval on the document is likely unreliable.

**False-risky:** readiness < 70 (RISKY or POOR) AND `text_char_recall` ≥ 0.85 AND `table_grits_con` ≥ 0.60 (or no tables in document).

Rationale: a document that recovers ≥ 85% of character content and has no major table failures is usable for indexing. Routing it to quarantine is an unnecessary cost.

**Secondary false-safe (table-specific):** readiness ≥ 85 (HIGH) AND `table_grits_con` < 0.40 (for documents with ≥ 2 expected tables).

Rationale: the HIGH band should not apply to documents where the primary structure — tables — was largely lost.

---

## 6. Run procedure

### 6.1 Score-only pass (before corpus finalization)

Run AksharaMD on the full ParseBench text corpus (≈ 506 documents) and collect readiness scores without running ParseBench evaluation metrics. This pass informs T5 corpus selection (Section 2.2) and must complete before the evaluation run.

Command:
```bash
cd C:\Users\kalya\parsebench
PYTHONIOENCODING=utf-8 parse-bench run aksharamd_parse --dataset text_content --score-only
```

The `--score-only` flag (or equivalent) must emit the AksharaMD readiness score per document into the results JSONL. Implementation detail: the adapter must capture `ctx.readiness_score` and emit it as a `RunStat`.

### 6.2 Corpus finalization

After the score-only pass:
1. Classify documents into T1–T4 (Section 2.4).
2. Select T5 documents (15 closest to the 70-point boundary).
3. Apply the dev/locked-validation/challenge split (seed = 20260713).
4. Freeze the corpus manifest: `benchmarks/calibration_corpus.jsonl`.

The corpus manifest cannot change after this point.

### 6.3 Evaluation run (dev split)

Run ParseBench evaluation on the 25 dev documents with all enabled metrics (Sections 3.1–3.4, 3.6–3.8). Review results for methodological errors (metric crashes, missing scores, unexpected NaN distributions) before proceeding to the locked run.

### 6.4 Evaluation run (locked validation + challenge)

After dev review is approved, run the evaluation on the remaining 50 documents. Results are final.

### 6.5 Report

Produce a calibration report (see Section 8) from the combined 75-document corpus. Do not modify the score formula or thresholds based on what the locked run shows — that is the job of the next version.

---

## 7. Metric implementation status

| Dimension | Metric | Status | Action required |
|-----------|--------|--------|----------------|
| Text fidelity | `text_char_recall` (Levenshtein) | Existing in ParseBench | Enable in adapter |
| Text fidelity | `text_sentence_recall` | **Missing** | Build sentence-level recall function |
| Reading order | `reading_order_adjacent_accuracy` | Existing in ParseBench | Enable in adapter |
| Reading order | `reading_order_pairwise_accuracy` | Existing in ParseBench | Enable in adapter |
| Heading structure | `heading_accuracy` | Existing in ParseBench | Enable in adapter |
| Table fidelity | `table_grits_con` | Existing; already collected | No action |
| Table fidelity | `teds` | Existing in ParseBench | Enable as cross-check |
| Visual coverage | — | Deferred | Out of scope |
| Hallucination | LAP (string fallback) | Existing (layout); fallback **missing** | Build string-containment fallback |
| Duplication | `dup_ratio` (4-gram) | **Missing** | Build 4-gram duplication counter |
| QA accuracy | `qa_answer_match` | Existing in ParseBench | Wire to text pipeline in adapter |

Metrics marked **Missing** must be built before the evaluation run begins. Metrics marked "Enable in adapter" require adapter configuration changes only, not new code.

---

## 8. Calibration report format

The report (`benchmarks/READINESS_CALIBRATION_REPORT.md`) must contain:

1. **Corpus summary table** — document count per tier, per band, and per split.

2. **Per-band empirical distributions** — for each metric, the mean, median, p10, and p90 value within each readiness band. One row per metric, four band columns.

3. **Hypothesis test results** — for each hypothesis in Section 4, the observed pass rate versus the target pass rate. Mark as PASS / FAIL / INCONCLUSIVE (fewer than 5 documents in band).

4. **False-safe list** — for each false-safe document: document ID, readiness score, `text_char_recall`, tier, and a one-line description of the failure mode (missing pages, OCR failure, complex layout, etc.).

5. **False-risky list** — same schema as false-safe.

6. **Secondary false-safe list** — HIGH-band documents with `table_grits_con` < 0.40.

7. **Calibration verdict** — one of:
   - WELL-CALIBRATED: ≤ 5% false-safe rate and ≤ 10% false-risky rate across the full corpus.
   - NEEDS-THRESHOLD-ADJUSTMENT: false-safe or false-risky rate exceeds the above limits.
   - NEEDS-SCORE-REVISION: systematic failure in a specific document type or signal that requires changing the scoring formula.

8. **Recommended next actions** — concrete, specific. Not "improve tables" but "adjust the TABLE_MISSING_CELLS penalty weight from −8 to −15 for documents where `table_grits_con` < 0.40."

---

## 9. What is frozen by this document

The following are locked and cannot change during or after the baseline run:

- Corpus selection procedure (Section 2)
- Metric formulas (Section 3)
- Score-band hypotheses and thresholds (Section 4)
- False-safe and false-risky definitions (Section 5)
- Run procedure (Section 6)
- Report format (Section 8)

The following are **not** frozen and may change:

- The AksharaMD readiness score formula and band thresholds (changed in response to calibration findings, in a subsequent version)
- The set of document parsers or improvements (do not modify `pdf.py` or any parser between the score-only pass and the evaluation run)
- The ParseBench metrics implementation for metrics marked "to build" (must be finalized before the evaluation run but are not frozen by this document)

---

## 10. Dependency: adapter readiness score capture

The AksharaMD ParseBench adapter must emit the readiness score as a `RunStat` for every processed document:

```python
RunStat(name="readiness_score", value=float(ctx.readiness_score), unit="score")
RunStat(name="readiness_band", value=band_to_int(ctx.readiness_band), unit="band")
```

where `band_to_int` maps `{"HIGH": 3, "OK": 2, "RISKY": 1, "POOR": 0}`.

Without this, T5 selection (Section 2.2) and the per-band hypothesis tests (Section 4) cannot be computed. This is a blocker for the score-only pass.
