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

**Implementation note — rule-based vs. spatial attribution:**
ParseBench's `attribution/core.py` computes reading-order metrics from bounding-box layout — it requires per-block spatial coordinates that are not available in text-only pipeline output. For this calibration, reading order is measured via the rule-based `order` rules already wired in `ParseEvaluator` (the `enable_rule_based=True` path). These rules check whether adjacent block sequences in the AksharaMD output match the expected ground-truth order, using string-level matching rather than spatial IoA.

Spatial reading-order metrics (adjacent accuracy, pairwise accuracy from `attribution/core.py`) are deferred to a layout-aware evaluation pass and are **not** collected in this calibration run.

**Metric:** Rule-based order accuracy — fraction of `order` rules that pass. An `order` rule passes if the specified text sequence appears in the AksharaMD output in the correct relative order.

**Reported as:** `rule_order_pass_rate` (computed by the existing `RuleBasedMetric` over `order`-type rules).

**Status:** Existing in ParseBench rule-based evaluator. Enabled via `enable_rule_based=True` in `make_calibration_evaluator()`. No additional adapter work required.

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

### 3.6 Unsupported output (provisional hallucination proxy)

**What it measures:** Fraction of meaningful tokens in AksharaMD output that are not supported by the ground-truth vocabulary.

**IMPORTANT — this is a lexical proxy, not a hallucination detector.** It cannot distinguish between (a) correct novel phrasing, (b) legitimate paraphrase, and (c) genuine hallucination. A high ratio indicates that the output contains many words absent from the ground-truth document, which warrants manual review but is not conclusive evidence of hallucination.

**Metric:**
```
unsupported_output_ratio = genuinely_unsupported_tokens / meaningful_tokens

where:
  meaningful_tokens = total_tokens − formatting_only_tokens
  formatting_only   = punctuation, markdown, pure-numeric tokens
  supported         = token appears in GT vocabulary OR in a GT bigram pair
  genuinely_unsupported = meaningful − supported
```

Output range: [0, 1]. 0 = all meaningful tokens supported; 1 = no meaningful tokens supported. When `meaningful_tokens = 0` (output is pure formatting), returns 0.0.

**Reported as:** `unsupported_output_ratio`. Metadata always includes `note: "proxy_metric_requires_manual_validation"`.

**Status:** Built (`unsupported_output_metric.py`). Enabled in `make_calibration_evaluator()`. Results must be cross-checked manually before being used to classify a document as hallucinated.

### 3.7 Duplication and noise

**What it measures:** Fraction of output that is repeated or is junk (glyph artifacts, OCR noise).

**Metric:** Duplication ratio — fraction of 4-gram instances that are extra repetitions, excluding repetitions already present in the ground truth:
```
dup_ratio = extra_repeated_instances / total_ngrams

where:
  extra_repeated_instances = sum over repeated 4-grams
                             of (count − 1), for 4-grams NOT in the GT 4-gram set
  total_ngrams             = |output tokens| − 3
```
The GT exclusion prevents penalising legitimate repetition that mirrors the source document (e.g. repeated headers). When `total_ngrams < 4`, returns 0.0 with `note: "too_few_tokens"`.

Junk/glyph detection is out of scope (no ground-truth noise labels in ParseBench).

**Status:** Built (`duplication_metric.py`). Enabled in `make_calibration_evaluator()`.

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

**Composite false-safe criterion for HIGH band (material failure):**
A HIGH-band document is classified as a material false-safe if ANY of the following conditions holds:

| Condition | Threshold |
|-----------|-----------|
| `text_char_recall` below threshold | < 0.90 |
| `text_sentence_recall` below threshold (where applicable) | < 0.85 |
| `table_grits_con` below threshold (docs with ≥ 2 expected tables) | < 0.40 |
| `rule_order_pass_rate` below threshold (docs with order rules) | < 0.70 |
| `unsupported_output_ratio` above threshold (proxy; requires manual review) | > 0.30 |

A document that fails any single condition is counted as a material false-safe for the HIGH-band hypothesis test, regardless of performance on the other dimensions. This is stricter than the primary false-safe definition in Section 5, which uses only `text_char_recall`. The composite criterion is reported as a secondary finding alongside the primary false-safe rate.

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

| Dimension | Metric | Status |
|-----------|--------|--------|
| Text fidelity | `text_char_recall` (Levenshtein) | Built; enabled via `enable_text_similarity=True` |
| Text fidelity | `text_sentence_recall` | **Built** (`sentence_recall_metric.py`); enabled via `enable_sentence_recall=True` |
| Reading order | `rule_order_pass_rate` (rule-based `order` rules) | Built; enabled via `enable_rule_based=True` |
| Reading order | Spatial adjacent/pairwise accuracy | Deferred — requires bounding boxes; not applicable to text-only eval |
| Heading structure | `heading_accuracy` | Built; enabled via `enable_header_accuracy=True` |
| Table fidelity | `table_grits_con` | Built; enabled via `enable_grits=True` |
| Table fidelity | `teds` | Built; enabled via `enable_teds=True` |
| Visual coverage | — | Deferred — out of scope for text-content corpus |
| Unsupported output | `unsupported_output_ratio` (provisional proxy) | **Built** (`unsupported_output_metric.py`); enabled via `enable_unsupported_output=True` |
| Duplication | `duplication_ratio` (4-gram, GT-excluding) | **Built** (`duplication_metric.py`); enabled via `enable_duplication=True` |
| QA accuracy | `qa_answer_match` | Existing; not wired — deferred to post-calibration |

All metrics required for the dev-split run are now built and enabled in `make_calibration_evaluator()`. QA accuracy is excluded from this calibration run.

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

---

## 11. Per-metric reference

Each metric's full contract: inputs, normalization, applicability gate, denominator, output range, and failure behavior.

### 11.1 `text_char_recall`

| Property | Value |
|----------|-------|
| Input — expected | Ground-truth markdown string |
| Input — actual | AksharaMD output markdown string |
| Normalization | Levenshtein edit distance (autoevals), normalized to [0, 1] |
| Applicability | Always applicable |
| Denominator | Length of expected string |
| Output range | [0, 1] |
| Failure behavior | Empty expected → 0.0; empty actual → 0.0 |

### 11.2 `text_sentence_recall`

| Property | Value |
|----------|-------|
| Input — expected | Ground-truth markdown string |
| Input — actual | AksharaMD output markdown string |
| Normalization | Strip markdown (`[#*_\|~\`<>\[\]()\n]`), split on `.?!` boundaries, NFC-normalize, lowercase, collapse whitespace |
| Applicability | `metadata["applicable"] = False` when expected is empty or contains no extractable prose sentences (e.g. pure table HTML). Filter: table rows (`\|` in original line), segments < 10 chars. |
| Denominator | Number of prose sentences extracted from expected |
| Output range | [0, 1] |
| Failure behavior | Empty expected → value=0.0, applicable=False; no extractable sentences → value=1.0, applicable=False |
| Implementation | `sentence_recall_metric.py` |

### 11.3 `rule_order_pass_rate`

| Property | Value |
|----------|-------|
| Input — expected | ParseBench `order`-type rules (text sequences that must appear in order) |
| Input — actual | AksharaMD output markdown string |
| Normalization | Rule-based string matching (existing ParseBench logic) |
| Applicability | Only for documents with `order`-type rules in test case |
| Denominator | Number of order rules defined for the document |
| Output range | [0, 1] |
| Failure behavior | No order rules → metric absent from results |
| Note | This is the text-pipeline substitute for spatial reading-order metrics, which require bounding boxes not available in text-only output |

### 11.4 `heading_accuracy`

| Property | Value |
|----------|-------|
| Input — expected | ParseBench heading rules (text + level) |
| Input — actual | AksharaMD output markdown headings |
| Normalization | Heading text similarity + level correctness + order |
| Applicability | Only for documents with heading rules |
| Denominator | Number of expected headings |
| Output range | [0, 1] |
| Failure behavior | No heading rules → metric absent |

### 11.5 `table_grits_con`

| Property | Value |
|----------|-------|
| Input — expected | HTML tables in ground-truth markdown |
| Input — actual | HTML tables in AksharaMD output |
| Normalization | Cell text via LCS similarity; Hungarian algorithm row/column matching |
| Applicability | Only for documents with ≥ 1 expected table |
| Denominator | F-score over matched cells |
| Output range | [0, 1] |
| Failure behavior | No tables in expected → metric absent (`tables_expected = 0`) |

### 11.6 `duplication_ratio`

| Property | Value |
|----------|-------|
| Input — expected | Ground-truth markdown string (defines expected n-gram set) |
| Input — actual | AksharaMD output markdown string |
| Normalization | Strip markdown, lowercase, tokenize on whitespace; build 4-gram counts |
| Applicability | Always; but `metadata["note"] = "too_few_tokens"` when output has < 4 tokens |
| Denominator | Total 4-grams in output (`len(tokens) - 3`) |
| Output range | [0, 1] |
| Failure behavior | `total_ngrams < 4` → 0.0 with too_few_tokens note; empty actual → 0.0 |
| GT exclusion | 4-grams present in expected are excluded from the "repeated" count — prevents penalising legitimate source repetition |
| Implementation | `duplication_metric.py` |

### 11.7 `unsupported_output_ratio` — PROVISIONAL LEXICAL PROXY

| Property | Value |
|----------|-------|
| Input — expected | Ground-truth markdown string (defines supported vocabulary + bigrams) |
| Input — actual | AksharaMD output markdown string |
| Normalization | Strip markdown (`[#*_\|~\`<>]`), lowercase, split on whitespace |
| Applicability | Always; but `metadata["note"]` always includes `"proxy_metric_requires_manual_validation"` |
| Denominator | Meaningful tokens (total − formatting_only) |
| Output range | [0, 1] |
| Failure behavior | `meaningful_tokens = 0` → 0.0 with `"no_meaningful_tokens"` note |
| Formatting-only tokens | Punctuation, markdown symbols, pure-numeric tokens (`[\d.,%-]+`) |
| Supported tokens | Token in GT unigram vocabulary OR appears in a GT bigram pair |
| **CRITICAL LIMITATION** | Cannot distinguish correct novel phrasing from hallucination. A high ratio is a signal for manual review, not a classification. Do not report this metric as a hallucination rate in any output or summary. |
| Implementation | `unsupported_output_metric.py` |

---

## 12. How to run the calibration

### 12.1 Prerequisites

1. AksharaMD is installed in the parsebench virtual environment: `pip install -e /path/to/omnimark`
2. ParseBench test data is present at `C:\Users\kalya\parsebench\data\test\`
3. The `aksharamd_calibration` pipeline is registered (already done in `pipelines/parse.py`)
4. `make_calibration_evaluator()` is imported and wired in the runner (already done)

### 12.2 Five-document instrumentation pilot

Purpose: verify that the adapter emits metadata correctly, all three new metrics compute without errors, and the calibration evaluator wiring is end-to-end correct. Run this before any corpus-level evaluation.

```bash
cd C:\Users\kalya\parsebench
python -m pytest tests/test_calibration_instrumentation.py -v --tb=short
```

Expected: all tests pass. If any test fails, fix the underlying issue before proceeding. Do not proceed to the dev split with failing pilot tests.

The five pilot documents are:
| Test ID | File | Type |
|---------|------|------|
| `simple_prose` | `text/text_simple__edited.pdf` | Clean prose |
| `multi_rule_text` | `text/text_simple__results.pdf` | Multi-rule text |
| `table_heavy` | `table/222876fb_page22.pdf` | Table-heavy |
| `table_insurance` | `table/SERFF_Interstate_random_pages 1_page276.pdf` | Insurance table |
| `ocr_boundary` | `text/text_ocr__p4013.pdf` | OCR-scanned |

### 12.3 Twenty-five document development baseline

Purpose: first calibration pass on the dev split. Results are inspectable and may prompt methodological corrections before the locked run.

```bash
cd C:\Users\kalya\parsebench
python -m parse_bench run \
    --pipeline aksharamd_calibration \
    --split dev \
    --corpus benchmarks/calibration_corpus.jsonl \
    --evaluator calibration \
    --output benchmarks/results/calibration_dev_run.jsonl
```

After the run, inspect:
- NaN or Inf rates per metric (should be 0 for all built metrics)
- `applicable=False` frequency for `text_sentence_recall` (expected high for table-heavy docs)
- Per-band document counts (must have ≥ 3 documents per band for meaningful statistics)
- Any pilot-level anomalies: `duplication_ratio > 0.5`, `unsupported_output_ratio > 0.4`

The dev run is not final. If you find a metric is systematically misconfigured, fix it and re-run the dev split before opening the locked set.

### 12.4 Locked validation run

Run only after dev split results have been reviewed and approved. Do not open the locked results until the run completes.

```bash
cd C:\Users\kalya\parsebench
python -m parse_bench run \
    --pipeline aksharamd_calibration \
    --split locked \
    --corpus benchmarks/calibration_corpus.jsonl \
    --evaluator calibration \
    --output benchmarks/results/calibration_locked_run.jsonl
```

After the locked run, generate the calibration report per Section 8. The locked run results are final — do not re-run with adjusted parameters.
