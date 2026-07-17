# AksharaMD Readiness-Calibration Dev-Split Diagnostic Report

**Status: DIAGNOSTIC — Not a calibration baseline**
**Go/no-go for locked validation: NO-GO**
**Date: 2026-07-12 (v4: human review complete — 53% HIGH-band false-safe rate confirmed)**
**Corpus: 25-document development split (seed 20260713)**

---

## 1. Summary

The development run completed cleanly (25/25 docs, 0 inference errors), and the
calibration evaluator instrumentation is working. This report reflects the v2 run
after text ground truth was reconstructed from bag-of-sentence annotations
(`benchmarks/calibration_text_gt.jsonl`, 21 rows) and injected into the evaluation.
The `sentence_recall` metric was also fixed to split on paragraph boundaries
(`\n\n`) before applying sentence-ending punctuation splits, correcting an issue where
the entire reconstructed GT was treated as a single long sentence.

Two structural gaps block a definitive answer to the central calibration question —
*Are HIGH-band readiness scores associated with acceptable extraction quality?*

1. **Table metrics measure parser failure, not calibration signal.** Three of four
   table-tier documents produced no table structure at all; the fourth produced
   fragmented cells. GRiTS/TEDS scores near zero reflect AksharaMD extraction
   failures, not a metric alignment problem.
2. **Limited readiness-score variance.** The dev split has only four distinct
   readiness values (79, 83, 85, 87); Spearman correlations are near zero for all
   metrics, which is expected but means the data cannot distinguish whether the
   score tracks quality within the HIGH band.

Three HIGH-band text-content documents are **high-risk false-safe candidates** —
readiness ≥ 85 but empirical rule pass rate below 0.60, corroborated by low sentence
recall and/or elevated unsupported output ratio. These are NOT yet confirmed
false-safes; text GT quality limits definitive classification.

Four table-heavy documents are **confirmed false-safes** — readiness = 85 but zero
table structure produced, with table GT known from ParseBench HTML annotations.

---

## 2. Run Metadata

| Field | Value |
|-------|-------|
| aksharamd version | 0.3.6 (omnimark checkout verified) |
| scoring_formula_version | 1 |
| corpus file | `benchmarks/calibration_corpus.jsonl` |
| text GT file | `benchmarks/calibration_text_gt.jsonl` (21 rows, bag-of-sentence reconstruction) |
| run output | `benchmarks/calibration_dev_run.jsonl` |
| docs processed | 25 / 25 |
| inference errors | 0 |
| evaluator fixes applied | (1) unsupported_output_ratio: None-guard added; (2) sentence_recall + unsupported_output_ratio moved out of expected_markdown gate; (3) sentence_recall metric: paragraph-boundary (`\n\n`) splitting added to _extract_sentences |

### Dev split composition

| Tier | Band | n |
|------|------|---|
| simple_prose | HIGH | 7 |
| simple_prose | OK | 2 |
| multi_column | HIGH | 7 |
| multi_column | OK | 1 |
| light_structure | HIGH | 3 |
| light_structure | OK | 1 |
| table_heavy | HIGH | 4 |
| **Total** | | **25** |

All 25 docs are HIGH or OK band. No RISKY or POOR documents are present in the dev
split — by design (T5 band-boundary docs are in the challenge split, not dev).

---

## 3. Metric Applicability

### Category A — Applicable for all 25 docs

| Metric | n | Notes |
|--------|---|-------|
| `duplication_ratio` | 25 | Computed on actual output only; does not require GT |

### Category B — Applicable for 21 text-content docs only

| Metric | n | Notes |
|--------|---|-------|
| `rule_pass_rate_judge` | 21 | ParseBench rule-annotation GT |
| `content_faithfulness` | 21 | Weighted aggregate of rule categories |
| `normalized_text_correctness` | 21 | Sentence/word bag-of-words rule subset |
| `normalized_order` | 21 | Reading-order rule subset |
| `normalized_text_styling` | 17 | Bold/italic/strikeout rules (doc-dependent) |
| `normalized_title_accuracy` | 14 | Title/heading rules (doc-dependent) |
| `sentence_recall` | 21 | Bag-of-sentence reconstructed GT; 4 table docs remain `html_table_only_expected` |
| `annotation_text_similarity` | 21 | Levenshtein similarity (partial GT only vs reconstructed GT; 4 table docs remain `html_table_only_expected` |
| `unsupported_output_ratio` | 25 | Applicable for all docs with any GT; text docs use reconstructed GT vocabulary |

### Category C — Applicable for 4 table-heavy docs only

| Metric | n | Notes |
|--------|---|-------|
| `grits_con` | 4 | Cell-content accuracy vs GT HTML |
| `teds` | 4 | Tree-edit-distance structure score vs GT HTML |
| `table_record_match` | 4 | Row-level record match vs GT HTML |
| `header_composite_v3` | 4 | Header detection accuracy |

### Category D — Non-applicable for all 25 docs

None. All major calibration metrics now have at least applicable=True for some docs.
The 4 table-heavy docs remain `html_table_only_expected` for sentence_recall and
annotation_text_similarity, and these metrics return applicable=False with note=html_table_only_expected.

**annotation_text_similarity is now applicable for text-content docs** but
values are systematically very low (range: 0.0002–0.0094) because the metric computes
character-level overlap between the full AksharaMD output and the partial reconstructed
GT (30–95 annotated sentences vs full document). This metric is informative only for
outlier detection at this GT quality level, not for threshold enforcement.

---

## 4. Table Tier — Manual Inspection

All four table-heavy dev documents were re-run and their AksharaMD output was
compared to the ParseBench GT HTML tables.

### 4.1 fqr-retail-blackrock-global-allocation-fund-inc\_page8

**GT structure:** Single HTML table, security portfolio listing (Security / Shares /
Value columns, ~30 rows).

**AksharaMD output:** Company names as prose text with leader dots between columns
(`Asahi Kasei Corp. . . . . . . . . .`). No table structure detected. The table was
not extracted.

**Finding:** Parser failure. The table was rendered with leader-dot separators
in the PDF and was not recognized as a table by the extraction rules. GRiTS/TEDS = 0
reflects the parser, not the evaluation metric.

**duplication_ratio = 0.441:** The leader-dot pattern `. . . . . . . .` creates
highly repetitive 4-grams, driving the duplication signal upward.

---

### 4.2 FBLB-134215544\_page15

**GT structure:** 10 HTML tables — insurance liability rate tables with colspan/rowspan
headers.

**AksharaMD output:** One markdown pipe table produced. Cell text is fragmented:
`| Per Hous | ehold/ | Organization |` instead of `| Per Household/Organization |`.
Words are split at column boundaries where the PDF had multiline cells.

**Finding:** Partial parser failure. One table was detected out of ten; cell
content is fragmented due to multiline cell handling. GRiTS scores reflect this
(grits_con = 0.008, teds = 0.006).

`tables_expected = 10, tables_actual = 1, tables_paired = 1` — nine tables were
completely missed.

---

### 4.3 SERFF\_CA\_random\_pages 1\_page1682

**GT structure:** One HTML table — insurance claim frequency data, numeric actuarial
table with column headers (Acc Year, Claim Count, Incd Loss + DCC, etc.).

**AksharaMD output:** Prose text describing the underwriting context; the numeric
table data does not appear in the output at all. The table was missed entirely.

**Finding:** Parser failure. The PDF likely encodes the table as a complex grid
or the content was treated as body text. GRiTS/TEDS = 0.

`unsupported_output_ratio = 0.738` — the highest of any doc. The actual output
contains many tokens not present in the GT vocabulary (prose vs numbers). This is a
correct signal: the output content is wrong even beyond table structure.

---

### 4.4 VRSK.2012.page\_125.pdf\_105195\_page1

**GT structure:** One HTML table — Verisk Analytics financial results by segment with
multi-level column headers (colspan for 2012/2011/2010).

**AksharaMD output:** Financial data embedded in prose, no table structure:
`$954,814 $579,506 $1,534,320 $768,479 $563,361...` mixed with text. The table was
not extracted.

**Finding:** Parser failure. Complex multi-level headers with colspan prevented
table detection. GRiTS/TEDS = 0.

---

### 4.5 Table tier verdict

| doc | tables_GT | tables_predicted | grits_con | root cause |
|-----|----------|-----------------|-----------|------------|
| fqr-retail-blackrock | 1 | 0 | 0.000 | leader-dot format not recognized as table |
| FBLB-134215544 | 10 | 1 | 0.008 | 9 tables missed; 1 fragmented |
| SERFF\_CA | 1 | 0 | 0.000 | numeric table missed entirely |
| VRSK.2012 | 1 | 0 | 0.000 | multi-level colspan headers not handled |

**GRiTS/TEDS near zero is primarily parser failure, not a Markdown-to-HTML
conversion problem.** Three of four tables were not extracted at all. The
`_convert_md_tables_to_html` adapter was irrelevant because no markdown tables
were produced. Before re-running these docs with fixes, verify the HTML conversion
pipeline on the one doc that did produce a markdown table (FBLB).

**The readiness score assigned HIGH to all four table docs (rs = 85) despite
complete table extraction failure.** This is the most significant calibration
finding in the dev split.

---

## 5. Text Tier — Rule-Based Quality Analysis

### 5.1 Available signal

For the 21 text-content documents, ParseBench provides two complementary signal
sources:

1. **Rule-annotation GT** (sentence/word bag-of-words, reading-order, text-styling):
   produces `rule_pass_rate_judge` and derived category scores.
2. **Reconstructed bag-of-sentence GT** (`calibration_text_gt.jsonl`): produces
   `sentence_recall` and `annotation_text_similarity` (Levenshtein). Sentence recall range
   across 21 docs: 0.000–0.857 (median 0.458).

`annotation_text_similarity` values are all < 0.01 due to the partial nature of the reconstructed
GT (annotated sentences only, not full document text) and is useful only for outlier
detection.

### 5.2 Score distribution — rule_pass_rate_judge (n=21)

| range | n | % |
|-------|---|---|
| ≥ 0.95 | 4 | 19% |
| 0.90 – 0.95 | 4 | 19% |
| 0.80 – 0.90 | 7 | 33% |
| 0.70 – 0.80 | 2 | 10% |
| 0.60 – 0.70 | 1 | 5% |
| < 0.60 | 3 | 14% |

### 5.3 Spearman correlations with readiness_score (n=21)

| Metric | rho | p | interpretation |
|--------|-----|---|----------------|
| rule_pass_rate_judge | +0.06 | 0.79 | no reliable correlation |
| sentence_recall | +0.05 | 0.83 | no reliable correlation |
| unsupported_output_ratio | +0.23 | 0.31 | no reliable correlation |
| duplication_ratio | +0.27 | 0.24 | no reliable correlation |

**Interpretation:** Near-zero correlations are expected given only four distinct
readiness values (79, 83, 85, 87). The readiness score does not track per-document
quality within the HIGH band. This does not falsify the score — it means the score
is coarser than per-document quality variation, not that it is wrong in direction.
Calibration requires asking "do HIGH docs pass threshold criteria?" rather than
"does score magnitude track metric magnitude?".

A meaningful correlation test requires a corpus that spans the full readiness band
range (HIGH through POOR), which the dev split does not provide.

Note: an earlier pre-fix run with sentence_recall treating the entire GT as 1-2
sentences showed a spurious rho=0.44 (p=0.046), which was a statistical artifact of
only 2/21 docs having non-zero recall. The corrected metric shows no correlation.

---

## 6. False-Safe Analysis

### 6.1 Confirmed false-safes — 4 table-heavy docs

All four table-heavy docs have readiness = 85 (HIGH) but produced zero table
structure. Table GT is known from ParseBench HTML annotations. See Section 4.

### 6.2 High-risk false-safe candidates — 3 HIGH-band text docs

These three documents have readiness ≥ 85 (HIGH) but empirical rule pass rate below
0.60, corroborated by low sentence recall and/or elevated unsupported output ratio.
Text GT quality (bag-of-sentence reconstruction, partial annotation coverage) limits
definitive confirmation; they are flagged as high-risk, not confirmed.

| doc_id | rs | band | rpj | sr | uor | signals |
|--------|----|------|-----|----|-----|---------|
| text_dense__japanese | 85 | HIGH | 0.236 | 0.129 | 0.925 | 3 of 3 metrics fail |
| text_simple__strikeUnderline | 85 | HIGH | 0.496 | 0.000 | 0.057 | rpj and sr fail; uor OK |
| text_multicolumns__pwc | 87 | HIGH | 0.518 | 0.250 | 0.851 | 3 of 3 metrics fail |

### 6.3 Borderline (0.60 ≤ rpj < 0.75)

| doc_id | rs | band | rpj | sr | uor | note |
|--------|----|------|-----|----|-----|------|
| text_dense__de | 85 | HIGH | 0.623 | 0.500 | 0.382 | uor > 0.30 flag; HIGH band |
| text_multicolumns__elpais | 79 | OK | 0.681 | 0.212 | 0.168 | OK band; low sr |
| text_misc__ikea3 | 79 | OK | 0.724 | 0.444 | 0.073 | OK band; borderline |

### 6.4 Anomalous: gridofnumbers (HIGH, sr=0.000, uor=1.000)

`text_multicolumns__gridofnumbers` (rs=87, HIGH) has rpj=0.891 but sr=0.000 and
uor=1.000. Root cause: the document is a dense grid of multi-digit numbers. The
reconstructed GT sentences are number rows (e.g. `20121234 20121235 20121236...`).
AksharaMD formats the grid differently (whitespace, line breaks, markdown table) so
neither the sentence comparison nor the vocabulary overlap matches. This is a GT
format mismatch for numeric grids, not a false-safe.

### 6.5 Root-cause notes

**text_dense__japanese:** The readiness score sees a cleanly-structured PDF (text
layer present, blocks extracted, tokens counted). It cannot detect that the
extraction rules are Japanese-language and that AksharaMD's reading order and
sentence segmentation fail on Japanese. This is a systematic language gap.

**text_simple__strikeUnderline:** normalized_order = 0.0 despite "simple_prose" tier.
The document is primarily a table of contents; the GT sentences are TOC entries with
page numbers (e.g. `Grievance and External Review Procedures 103 101`). AksharaMD
omits page numbers from headings, so exact string matching fails. The strikethrough/
underline formatting rules also fail (normalized_text_styling = 0.238).

**text_multicolumns__pwc:** Multi-column layout with complex inter-column ordering.
Text styling completely failed (0.0), consistent with PWC branding documents that use
custom fonts not recognized by AksharaMD's style heuristics. Only 8 GT sentences,
25% sentence recall.

---

## 7. Ground Truth Gaps — What Cannot Be Reliably Measured

The calibration design in `READINESS_CALIBRATION.md` includes these criteria:

| Criterion | Required metric | Available? | Notes |
|-----------|----------------|------------|-------|
| annotation_text_similarity (was: text_char_recall) ≥ 0.90 | `annotation_text_similarity` | Applicable (21 docs) | Values 0.0002–0.0094; GT is partial annotation, not full text — threshold not meaningful at this GT quality |
| text_sentence_recall ≥ 0.85 | `sentence_recall` | Applicable (21 docs) | Values 0.000–0.857; max=0.857; threshold ≥0.85 fails 20/21 docs |
| table_grits_con ≥ 0.40 | `grits_con` | Applicable (4 docs) | All ~0; reflects parser failure, not metric issue |
| rule_order_pass_rate ≥ 0.70 | `normalized_order` | Applicable (21 docs) | Yes; usable |
| unsupported_output_ratio ≤ 0.30 | `unsupported_output_ratio` | Applicable (25 docs) | Yes; usable |

**Sentence recall threshold calibration gap:** The reconstructed bag-of-sentence GT
provides applicability for the metric but the ≥0.85 threshold from the calibration
spec cannot be validated — it would fail 20/21 text docs including known-good ones.
The threshold may need to be recalibrated for bag-of-sentence GT quality, or a
verbatim PDF-text reference is needed for high-confidence thresholding.

**Remaining gaps:**
1. **Verbatim text reference absent.** The reconstructed GT is annotated sentences,
   not full document text. `annotation_text_similarity` (Levenshtein) values are < 0.01 for
   all docs; this metric is not usable at current GT quality.
2. **Numeric grid GT mismatch.** `text_multicolumns__gridofnumbers` shows sr=0.000,
   uor=1.000 despite rpj=0.891. The bag-of-sentence GT format is incompatible with
   how AksharaMD formats number grids. Requires separate handling or exclusion from
   sentence_recall evaluation.

---

## 8. Go/No-Go for Locked Validation

**Decision: NO-GO**

### 8.1 Blocking conditions

1. **Table parser failures are systematic.** Three of four table-heavy dev documents
   produced no table structure whatsoever. The readiness score assigned HIGH to all
   four. Before the locked validation can include table-heavy docs, either:
   - the table extraction failures need to be investigated and fixed, or
   - table-heavy docs need to be explicitly excluded from the false-safe criterion
     with a documented rationale.

2. **Three high-risk false-safe candidates in 17 HIGH-band text docs (~18%).** The
   false-safe rate in this sample exceeds the 10% threshold required for locked
   validation. The root causes are heterogeneous (language, formatting, layout) and
   need individual triage. Note: the target is < 10% in the final locked split, not
   this dev sample — but 3/17 is a strong signal that investigation is warranted
   before proceeding.

3. **Sentence recall threshold not calibrated for bag-of-sentence GT.** The maximum
   observed sentence_recall is 0.857 (myctophidae); the calibration spec threshold
   of ≥0.85 cannot be validated — it would classify 20/21 text docs as below threshold
   including known-good ones (webprint=0.811, battery=0.700, letter3=0.769).
   The threshold needs empirical calibration against this GT type before use as a
   go/no-go criterion.

~~Blocking condition 1 from v1 report — "No character-level text recall available" —
is resolved.~~ Text GT reconstructed (`calibration_text_gt.jsonl`), sentence_recall
now applicable for 21/25 docs.

### 8.2 Conditions for proceeding to locked validation

| Condition | Status | What |
|-----------|--------|------|
| Obtain text GT | DONE | `calibration_text_gt.jsonl` produced; sentence_recall applicable for 21/25 docs |
| Table triage | Open | For each of the 4 table-heavy false-safe cases: determine whether the failure is a bug to fix or a known limitation to document |
| Markdown-to-HTML verification | Open | Run FBLB (the one doc with a markdown table) through `_convert_md_tables_to_html` manually; verify output is valid HTML that GRiTS can process |
| False-safe investigation | Open | Investigate text_dense__japanese, text_simple__strikeUnderline, text_multicolumns__pwc; determine if they represent language/format limitations or bugs |
| Sentence recall threshold calibration | Open | Empirically set threshold for sentence_recall with bag-of-sentence GT; current spec threshold (≥0.85) fails 20/21 text docs including known-good ones |

### 8.3 What is NOT blocking

- The calibration evaluator instrumentation is complete and correct.
- The dev run executed cleanly (0 errors, all metrics emitted).
- `duplication_ratio` is reliably computable for all docs and shows meaningful
  variation (range: 0.000 to 0.488).
- Rule-based metrics for text-content docs provide useful directional signal even
  without character-level GT.
- The version gate and corpus freeze (seed 20260713) are in place.

---

## 9. Metric Applicability — Implications for Composite Criterion

The false-safe composite criterion from `READINESS_CALIBRATION.md` Section 4.1:

| Condition | Threshold | Applicable | Empirical range (text, n=21) | Usable? |
|-----------|-----------|-----------|------------------------------|---------|
| annotation_text_similarity | < 0.90 | 21 / 25 | 0.0002–0.0094 | No — partial GT too sparse |
| text_sentence_recall | < 0.85 | 21 / 25 | 0.000–0.857 | Partial — threshold needs recalibration |
| table_grits_con (≥ 2 tables) | < 0.40 | 4 / 25 | all ~0 | Yes — reflects parser failure |
| rule_order_pass_rate | < 0.70 | 21 / 25 | 0.000–1.000 | Yes |
| unsupported_output_ratio | > 0.30 | 25 / 25 | 0.035–1.000 (text); 0.087–0.738 (table) | Yes |

With text GT available, `sentence_recall` and `annotation_text_similarity` now have applicability
for all text-content docs. However, the spec threshold for sentence_recall (≥0.85) is
too high for the partial bag-of-sentence GT — only 1/21 docs reaches it.

Under the current GT quality, the three low-rpj text docs (japanese, strikeUnderline,
pwc) are **"high-risk false-safe candidates"** — corroborated by both rpj and sr, but
not definitively confirmed because the GT doesn't represent the full document.

The four table docs remain **"confirmed false-safes"** — table structure GT is exact
(HTML) and extraction failure is unambiguous.

---

## 10. Recommended Next Steps

**[DONE] Priority 1 — Text GT reconstruction:**
`scripts/reconstruct_text_gt.py` implemented; `calibration_text_gt.jsonl` produced
(21 rows). `sentence_recall` paragraph-boundary fix applied. Dev split re-run with
text GT injected. Sentence recall now applicable for 21/25 docs (range: 0.000–0.857).

**[DONE] Metric rename — annotation_text_similarity:**
`TextSimilarityMetric.name` changed from `text_similarity` to `annotation_text_similarity`.
Added `gt_scope=partial_annotations` and `not_document_level_recall=True` to metadata.
This prevents interpreting the 0.001–0.009 values as document-level character recall.
All 322 tests pass.

**[DONE] Phase A — Human review of 21 text-content docs:**
21-doc review completed 2026-07-12. Results: 3 PASS, 7 PASS_WITH_WARNINGS, 11 FAIL.
HIGH-band false-safe rate: 9/17 = 53%.

Key finding: no single existing metric achieves false-safe rate < 25% at any threshold.
The score needs structural failure signals, not threshold adjustment.

Confirmed failure classes:
1. Image-only / OCR unavailable (letter3, myctophidae, japanese): output is placeholder only
2. Multi-column reading-order failure (3colpres, 4c, simple2, elpais, ikea3, pwc): interleaved text
3. Structure/content loss (de, strikeUnderline, pwc): TOC/table/segment loss

Product dimension note: AksharaMD is a multimodal ingestion package. For image-only
pages where OCR is unavailable, the output includes an `asset://` image reference.
Whether that constitutes a valid fallback depends on whether the image asset is
properly packaged and accessible to a vision-capable model. Three documents (letter3,
myctophidae, japanese) need re-evaluation once image packaging is verified.
Their `text_only_usable=No` labels stand; `multimodal_usable` is TBD.

Files:
- `benchmarks/calibration_text_human_review.jsonl` — labels + multimodal fields
- `benchmarks/READINESS_REGRESSION_LEDGER.md` — 21-doc ledger, warning codes, gate
- `benchmarks/regression_fixtures/text_false_safes.jsonl` — 11 FAIL fixtures
- `benchmarks/TEXT_OUTPUT_REVIEW_RUBRIC.md` — updated with multimodal dimensions

**[IN PROGRESS] Phase B1 — W_IMAGE_ONLY_NO_OCR warning:**
Implemented in `aksharamd/scoring/readiness.py`. Detects when output consists entirely
of OCR-unavailable paragraph placeholders + IMAGE blocks (no real text blocks). Caps
score at 55 (RISKY). 5/5 regression tests pass in
`tests/test_readiness_image_placeholder.py`.

This single signal corrects 3 confirmed false-safes (letter3: 87→55, myctophidae:
87→55, japanese: 85→55). Does not affect PASS or PASS_WITH_WARNINGS documents.

**[PENDING] Phase B2 — W_MULTICOLUMN_ORDER warning:**
6 FAIL docs (3colpres, 4c, simple2, elpais, ikea3, pwc) have multi-column reading
order failure. Signal design TBD. Multi-column detection requires either:
(a) a ParseBench post-processing cap based on normalized_order < threshold, or
(b) a PDF layout signal in AksharaMD readiness.py (layout_heavy + low block coherence).
Do not implement until signal is validated against non-failing multi-column docs
(battery, 2colmercedes both passed despite being multi-column).

**[PENDING] Phase B3 — W_TABLE_MISSING (text variant):**
pwc has table content lost (table_as_prose). W_TABLE_MISSING already designed in
`TABLE_READINESS_DIAGNOSTIC_DESIGN.md`. Now also confirmed needed for text-layer
multi-column docs, not just table-tier docs.

**[PENDING] Phase B4 — de and strikeUnderline:**
text_dense__de: omitted content + segmentation, no clear structural signal yet.
text_simple__strikeUnderline: TOC structure lost. No warnings designed for these yet.
Investigate individually before attempting a generic fix.

**[DESIGN ONLY] Original Phase B — Table readiness diagnostics:**
`benchmarks/TABLE_READINESS_DIAGNOSTIC_DESIGN.md` — design note for detecting table
extraction failure before readiness scoring. Proposes warning codes (W_TABLE_MISSING_LEADER_DOTS,
W_TABLE_MISSING_HIGH_NUMERIC, W_TABLE_GEOMETRY_MISMATCH) and readiness caps.
Four confirmed false-safe regression fixtures defined. Still pending implementation.

**Locked validation gate:**
DO NOT run the 35-document locked split until:
- HIGH-band false-safe rate < 10%
- No image-only extraction failure rated HIGH
- No confirmed table-loss failure rated HIGH
- All PASS/PASS_WITH_WARNINGS docs remain stable
- All regression fixtures pass
Current status: NO-GO (53% HIGH-band false-safe rate).
