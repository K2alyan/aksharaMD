# Phase 0 Inventory — USP Calibration

**Status:** DRAFT
**Date:** 2026-08-20
**Purpose:** Enumerate existing corpora, ground-truth labels, harnesses, metrics, and calibration state before starting Phase 1 (claim spec).

---

## 1. Executive summary — headline finding

**The calibration effort described in the plan is not greenfield.** A frozen calibration contract (`benchmarks/READINESS_CALIBRATION.md`, v1.0, 2026-07-13) already defines the corpus, splits, metrics, and hypothesis tests. A 25-doc dev split was run 2026-07-12 and produced a **NO-GO** verdict:

- **HIGH-band false-safe rate: 53%** (9/17 text-content documents), against a 10% ceiling for locked-run go-ahead.
- 4/4 table-heavy docs were confirmed false-safes (rated HIGH but produced zero table structure).
- 3/17 text docs were high-risk false-safe candidates (Japanese language failure, TOC/formatting loss, multi-column reading-order failure).

Between 2026-07-12 and today (2026-08-20, ~39 days), the team executed the OCR-backend series (PRs #72–#103) — necessary work, but **not calibration work**. Of the remediation phases the dev report identified (B1–B4), only **B1 (`W_IMAGE_ONLY_NO_OCR` warning)** landed. B2/B3/B4 are open.

The locked-validation split (35 docs) and the challenge split (15 docs) have never been run. The USP claim in the roadmap ("documents scoring 85+ recovered ≥95% of labeled text in 92% of the corpus") is therefore **still empirically undefended** — and the last measured evidence points the opposite direction.

This inverts the plan I proposed in the earlier discussion. The gap is not "no corpus, no harness"; it is "the harness ran once, showed the score is not defensible in its current shape, and the fixes stalled."

---

## 2. Existing corpora

| Corpus | Location | Size | Ground truth | Status |
|--------|----------|------|--------------|--------|
| **ParseBench text_content** (upstream) | `C:\Users\kalya\parsebench\data\test\` | ~506 docs | Rule-based annotations (text, headings, tables, styling, order) | Fetched, checksummed |
| **Calibration corpus (5-tier stratified)** | `benchmarks/calibration_corpus.jsonl` (referenced) | 75 docs (25 dev + 35 locked + 15 challenge) | Inherits from ParseBench | Frozen 2026-07-13 (seed 20260713) |
| **Reconstructed text GT** | `benchmarks/calibration_text_gt.jsonl` (referenced) | 21 rows | Bag-of-sentence, partial annotation only | Built by `scripts/reconstruct_text_gt.py` |
| **Human review labels** | `benchmarks/calibration_text_human_review.jsonl` (referenced) | 21 rows | 3 PASS, 7 PASS_WITH_WARNINGS, 11 FAIL + multimodal dimensions | Complete 2026-07-12 |
| **PDF Benchmark v1 baseline** | `benchmarks/PDF_BENCHMARK_V1_BASELINE_2026-07-19.json` | Full ParseBench + Public | Native + rule-based | Reference baseline |
| **PDF Benchmark v1 UnlimitedOCR passes** | `benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20*.json` | 45 assets, 2 passes | — | Reference, deterministic re-run pair |
| **OCR Auto Calibration corpus** | `benchmarks/ocr_auto_calibration/` | 21 docs, 63 runs | — | Policy v1 conformance evidence |
| **Layout Complexity science corpus** | `benchmarks/ocr_auto_calibration/LAYOUT_COMPLEXITY_V1_EVIDENCE.md` | 5 arXiv papers | — | All 5 landed `complex`, `ocr_frac=0` |
| **Public corpus** | `benchmarks/public_corpus_manifest.json`, `benchmarks/build_public_corpus.py` | Varies | — | Fetch/build harness |
| **KV eval corpus** | `benchmarks/kv_eval/` (`ground_truth.py`, `corpus.py`) | Dev split | `KeyValueGroundTruth` model | Held-out prerequisites not yet met |
| **QA pilot / held-out** | `benchmarks/document_package/qa_pilot/`, `HeldOutRunLock` schema | Corpus + questions | `AnswerKey`, `GradingMethod` | Runner + grader present |
| **MGAM eval** | `benchmarks/mgam_eval/` | — | — | Directory exists, contents not surveyed |
| **Advanced-fidelity baseline** | `benchmarks/ADVANCED_FIDELITY_2026-07-18.json/md` | — | Fidelity metrics | Baseline artifact |

---

## 3. Existing ground-truth infrastructure

- **`benchmarks/kv_eval/ground_truth.py`** — `KeyValueGroundTruth` model, labels with resolvable and missing PDF paths.
- **`benchmarks/document_package/qa_pilot/schema.py`** — `AnswerKey`, `GradingMethod`, `QuestionType`, `EvaluationCorrection`, `PilotGradeResult`, `HeldOutRunLock`, `QAPilotLock`.
- **`benchmarks/document_package/qa_pilot/grading.py:121`** — `grade_answer()`; single-choice, multiple-choice, numerical, free-text grading paths.
- **Rule GT (upstream, ParseBench)** — text, order, headings, styling, tables.
- **HTML table GT (upstream, ParseBench)** — used for GRiTS/TEDS.
- **Reconstructed text GT** — bag-of-sentence, partial; known-limited (see §7 of dev report).

---

## 4. Existing metrics (already implemented and wired)

| Dimension | Metric | Where | Status |
|-----------|--------|-------|--------|
| Text fidelity | `annotation_text_similarity` (Levenshtein, autoevals) | ParseBench adapter | Built; values 0.001–0.009 on current GT — not usable at partial-GT quality |
| Text fidelity | `sentence_recall` (bag-of-sentence, `\n\n` paragraph split) | ParseBench adapter | Built; threshold uncalibrated (≥0.85 spec vs 0.857 max observed) |
| Reading order | `rule_order_pass_rate` / `normalized_order` | ParseBench rule-based | Built, usable |
| Heading structure | `heading_accuracy` / `header_composite_v3` | ParseBench | Built |
| Table cells | `grits_con` | ParseBench | Built; near-zero because parser missed tables |
| Table structure | `teds` | ParseBench | Built; same as above |
| Table rows | `table_record_match` | ParseBench | Built |
| Hallucination proxy | `unsupported_output_ratio` | ParseBench `unsupported_output_metric.py` | Built, provisional; always requires manual validation |
| Duplication | `duplication_ratio` (4-gram, GT-excluding) | ParseBench `duplication_metric.py` | Built, usable |
| Downstream QA | `qa_answer_match` | ParseBench `qa/answer_comparison.py` | Existing metric; not wired to text pipeline; deferred |

**Composite metrics observed in the dev report:** `rule_pass_rate_judge`, `content_faithfulness`, `normalized_text_correctness`, `normalized_text_styling`, `normalized_title_accuracy`.

---

## 5. Existing harnesses

- `benchmarks/parsebench_recalibration.py` — recalibration runner (multicolumn work landed via this path).
- `benchmarks/parsebench_recalibration_metrics.py` — page/observable eligibility, confusion matrix.
- `benchmarks/parsebench_fetch.py` — deterministic fetch with checksums (`parsebench_assets.lock.checksums.json`, `parsebench_assets.lock.json`).
- `benchmarks/pdf_benchmark_v1.py` — full PDF benchmark harness with `RunResult`, `Asset`, aggregation, corpus counts, dollar accounting.
- `benchmarks/pdf_benchmark_v1_parity_audit.py` — parity checking.
- `benchmarks/ocr_auto_calibration/harness.py` — OCR auto-policy calibration.
- `benchmarks/document_package/harness.py` — document-package benchmark; `run_document()`, `run_corpus()`, `serialize_baseline_a()`, `compute_token_savings_attribution()`, anomaly detection, category summaries.
- `benchmarks/document_package/qa_pilot/runner.py` — QA pilot: `run_pilot()`, `run_held_out_pilot()`, `regrade_stored_outputs()`, `regrade_stored_held_out_outputs()`.
- `benchmarks/document_package/qa_pilot/ablation.py` — `run_table_ablation()`.
- `benchmarks/multicolumn_candidate_replay.py` — multicolumn candidate scoring.
- `benchmarks/sidebar_multicolumn_*` — sidebar-fixture calibration path (PRs #65–67).
- `benchmarks/readiness_gate/` — `run_benchmark.py`, corpus subdirectory, README.

**Adapter:** `aksharamd_calibration` pipeline is registered in the ParseBench sibling repo (`pipelines/parse.py`). `make_calibration_evaluator()` wires all metrics. `RunStat` for `readiness_score` and `readiness_band` is a dependency for T5 selection (§10 of contract).

---

## 6. Current readiness score behavior

- **Formula version:** `scoring_formula_version = 1` (per dev report). Implemented in `aksharamd/scoring/readiness.py:579` (`compute_readiness_score`).
- **Structured result:** `ReadinessResult`, `ReadinessEvidence`, `DeductionRecord` at `aksharamd/scoring/models.py`.
- **Bands:** HIGH ≥85, OK 70–84, RISKY 50–69, POOR <50. Mapping at `aksharamd/models/manifest.py:16` (`_quality_band`).
- **Format baselines:** documented `docs/readiness-score.md` (95 for markdown/text, 87 for PDF text-layer, 78–80 for PPTX/EPUB, 63 for RTF, 62–65 for legacy Office, etc.).
- **Deductions (existing):** parse errors (−12 each, cap −30), missing pages (−4/page, cap −38), `OCR_REQUIRED` (cap −40, suppresses `NEAR_EMPTY_OUTPUT` and `LOW_TEXT_DENSITY`), `NEAR_EMPTY_OUTPUT` (−25), `LOW_TEXT_DENSITY` (−20), `GLYPH_ARTIFACTS` (−25), `REPEATED_CONTENT` (−8), `TOKEN_BLOAT` (−8), `LARGE_BLOCK` (cap −10), missing headings on multi-page (−6), auto-generated table columns (cap −5).
- **Table-specific:** `compute_table_quality` (`aksharamd/scoring/table_quality.py:501`), `compute_table_expectation` (`aksharamd/scoring/table_expectation.py:259`), `TableFinding` and `aggregate_findings()` / `risk_findings()`.
- **KV detection:** `KeyValueDetectionProfile`, `detect_key_value_entries` — held-out prerequisites not met.
- **Recently added post-dev-review:**
  - `W_IMAGE_ONLY_NO_OCR` (Phase B1) — caps score at 55; fixes 3 confirmed image-only false-safes.
  - Other candidate warnings (`W_PARSE_FALLBACK`, `W_PDF_ATTACHMENT_IGNORED`) — informational, 0 penalty currently.

---

## 7. Calibration state — where the effort stalled

| Milestone | Status | Date |
|-----------|--------|------|
| Contract v1.0 frozen | DONE | 2026-07-13 |
| Adapter emits `readiness_score` RunStat | DONE (assumed — pilot passed) | ~2026-07-11 |
| Score-only pass on ParseBench full corpus | DONE (assumed — feeds T5) | pre-2026-07-12 |
| Corpus finalization (`calibration_corpus.jsonl`) | DONE | pre-2026-07-12 |
| 5-doc instrumentation pilot | DONE | pre-2026-07-12 |
| Dev split (25 docs) run | DONE — **NO-GO** verdict | 2026-07-12 |
| Dev split v2 (with reconstructed text GT) | DONE | 2026-07-12 |
| Human review of 21 text docs | DONE — 3 PASS / 7 PASS_WITH_WARNINGS / 11 FAIL | 2026-07-12 |
| Phase B1 — `W_IMAGE_ONLY_NO_OCR` | DONE, in aksharaMD main | ~2026-07-12 |
| Phase B2 — `W_MULTICOLUMN_ORDER` | DONE, in aksharaMD main (v2 threshold, precision 100% / recall 40% at threshold 0.28) | ~2026-07-13; recalibrated PR #65 on 2026-07-19 |
| Phase B3 — `W_HEADER_FOOTER_TABLE_GARBLED` (text-layer table loss substitute) | DONE, in aksharaMD main (`HeaderFooterTableValidator`) | ~2026-07-13 |
| Phase B3 — `W_TABLE_MISSING` (original design) | NOT SHIPPED — superseded by `W_HEADER_FOOTER_TABLE_GARBLED` for pwc-class docs; original design still open for leader-dots / high-numeric / geometry-mismatch classes | — |
| Phase B4 — de + strikeUnderline structural signals | **OPEN** — root causes identified (heading page-number omission for strikeUnderline; segmentation/omission for de) but no signal designed | — |
| Rescore-with-new-warnings evaluation | DONE — `RESCORE_REPORT_V1.md` 2026-07-13 | 2026-07-13 |
| Sentence-recall threshold recalibration for bag-of-sentence GT | **OPEN** | — |
| Table-tier triage (fix or explicit exclusion) | **OPEN** | — |
| Dev split re-run after B2/B3/B4 | **BLOCKED** on B2/B3/B4 | — |
| Locked validation run (35 docs) | **BLOCKED** — will not run until dev HIGH-band false-safe rate <10% | — |
| Challenge split run (15 docs) | **BLOCKED** — same gate | — |
| Calibration report per §8 of contract | **BLOCKED** — same gate | — |
| Roadmap USP claim ("85+ recovers 95% in 92%") | **UNDEFENDED** | — |

**Gap between the plan I proposed and reality:** I proposed building a fresh corpus in Phase 2 and a fresh harness in Phase 3. Both exist (in the sibling parsebench repo). B1, B2, and a variant of B3 have all shipped to aksharaMD main since the dev report was written. The `RESCORE_REPORT_V1` from 2026-07-13 shows the outcome:

- **Raw HIGH-band false-safe rate unchanged at 53% (9/17)** — because the new warnings *alert* but do not *cap the score* (except `W_IMAGE_ONLY_NO_OCR` which caps at 55).
- **Silent false-safe rate dropped to 35% (6/17)** — the docs that FAIL and emit no alerting warning, i.e. the ones a user would have no signal for.
- **Gate status:** still BLOCKED.

The real remaining work has two clear shapes:

1. **Score-formula shape**: decide whether the newly-added warnings (`W_MULTICOLUMN_ORDER`, `W_HEADER_FOOTER_TABLE_GARBLED`) should carry score caps so the raw false-safe rate falls to <10%, or whether the false-safe definition should account for "alerting warning fired." This is a scoring policy decision, not a technical unknown.
2. **Coverage shape**: close the recall gap on `W_MULTICOLUMN_ORDER` (currently 40%) and design signals for the two remaining silent-failure classes (de, strikeUnderline).

Neither is greenfield. Both are targeted follow-ons to work that has already happened.

---

## 8. Confirmed failure modes from the dev report

These are the classes of extraction failure that currently score HIGH but should not:

1. **Image-only / OCR-unavailable pages** — output is placeholder-only. FIXED by Phase B1 (`W_IMAGE_ONLY_NO_OCR`).
2. **Multi-column reading-order failure** — 6 docs (3colpres, 4c, simple2, elpais, ikea3, pwc) with interleaved-text output. Not fixed. Signal design open.
3. **Structural table loss (text-layer)** — pwc, plus the 4 table-tier docs. Not fixed. `W_TABLE_MISSING` designed in `benchmarks/TABLE_READINESS_DIAGNOSTIC_DESIGN.md`, not implemented.
4. **TOC / heading-with-page-number loss** — strikeUnderline. Not fixed. No signal designed.
5. **Segment/content loss** — de. Not fixed. No signal designed.
6. **Language-heuristic gap** — japanese. Fixed by Phase B1 as image-only side effect, but the underlying language-detection gap remains.
7. **Numeric-grid GT mismatch** — gridofnumbers (metric artifact, not a scoring failure). Requires GT-side handling.

Note (from dev report §10): "no single existing metric achieves false-safe rate <25% at any threshold." The fix path is **new structural signals**, not threshold-tightening on existing metrics.

---

## 9. Gaps that need new work

None of these are corpus gaps. All are score-signal gaps, score-policy gaps, or metric-quality gaps.

### 9.1 Score-policy (single biggest lever)
`RESCORE_REPORT_V1` shows the raw false-safe rate stays at 53% even after `W_MULTICOLUMN_ORDER` and `W_HEADER_FOOTER_TABLE_GARBLED` ship, because those warnings are informational-only. Two policy choices:

(a) Attach a score cap or penalty to alerting warnings so they push the doc out of HIGH into OK/RISKY. Simplest fix; strongest guarantee.
(b) Redefine the false-safe metric to count only silent-failure documents. Weaker guarantee; matches how the score is actually consumed (band + notes + warnings).

Recommend (a). The USP claim is "the score means something"; a HIGH-band that hides a known warning is a weaker USP than a HIGH-band that guarantees no known-issue warnings fired.

### 9.2 Score-signal recall (blocks 6 remaining silent failures)
- `W_MULTICOLUMN_ORDER` recall at 40%. Three false negatives: ikea3, elpais, simple2 — dev report §5.3 hints at heading-hierarchy or column-boundary variants these don't trigger.
- `text_dense__de` — segmentation/omission failure. No signal designed.
- `text_simple__strikeUnderline` — TOC + heading-page-number omission. No signal designed.
- Language-detection heuristic gap for Japanese (currently absorbed by `W_IMAGE_ONLY_NO_OCR` as an image-only side effect).

### 9.2 Metric quality (blocks meaningful threshold enforcement)
- `annotation_text_similarity` — partial GT makes values ~0.001–0.009. Either build verbatim-text GT (expensive) or drop the metric from the composite criterion.
- `sentence_recall` threshold — spec is ≥0.85; max observed is 0.857. Threshold needs empirical recalibration against bag-of-sentence GT.
- `qa_answer_match` — wired for QA pilot but not wired to ParseBench text pipeline. Deferred by the contract.

### 9.3 Scope questions
- Table-tier docs currently confirmed as false-safes — is the plan to fix the table parser, or to explicitly exclude table-heavy docs from the false-safe criterion until table extraction lands? Contract §8.1 requires a decision here before the locked run.
- Whether the "multimodal_usable" dimension (dev report §10, product footnote) — image-only pages with `asset://` references and vision-capable downstream models — counts as PASS. This decides whether `W_IMAGE_ONLY_NO_OCR` is a cap-at-55 or a cap-at-70.

---

## 10. Where the calibration artifacts live (verified)

All calibration corpus/label artifacts live in the **sibling ParseBench repo** at `C:\Users\kalya\parsebench\`, not in this repo. This is by design — the ParseBench adapter runs in that repo and the run commands in the frozen contract explicitly `cd` there.

Verified present:

- `C:\Users\kalya\parsebench\benchmarks\calibration_corpus.jsonl`
- `C:\Users\kalya\parsebench\benchmarks\calibration_text_gt.jsonl`
- `C:\Users\kalya\parsebench\benchmarks\calibration_text_human_review.jsonl`
- `C:\Users\kalya\parsebench\benchmarks\calibration_dev_run.jsonl` (dev run output)
- `C:\Users\kalya\parsebench\benchmarks\calibration_score_pass.jsonl` (score-only pass output)
- `C:\Users\kalya\parsebench\benchmarks\READINESS_REGRESSION_LEDGER.md`
- `C:\Users\kalya\parsebench\benchmarks\TEXT_OUTPUT_REVIEW_RUBRIC.md`
- `C:\Users\kalya\parsebench\benchmarks\TABLE_READINESS_DIAGNOSTIC_DESIGN.md`
- `C:\Users\kalya\parsebench\benchmarks\regression_fixtures\` (dir)
- `C:\Users\kalya\parsebench\scripts\reconstruct_text_gt.py`
- `C:\Users\kalya\parsebench\benchmarks\CALIBRATION_TEXT_REVIEW_WORKSHEET.md`
- `C:\Users\kalya\parsebench\benchmarks\HEADER_FOOTER_TABLE_REPORT.md`
- `C:\Users\kalya\parsebench\benchmarks\MULTICOLUMN_OBSERVATION_REPORT.md` and `_V2.md`
- `C:\Users\kalya\parsebench\benchmarks\RESCORE_REPORT_V1.md` (2026-07-13)
- `C:\Users\kalya\parsebench\benchmarks\rescore_high_band_v1.jsonl`
- `C:\Users\kalya\parsebench\benchmarks\multicolumn_observation_v1.jsonl` and `_v2.jsonl`
- `C:\Users\kalya\parsebench\benchmarks\header_footer_table_observation_v1.jsonl`
- `C:\Users\kalya\parsebench\benchmarks\review_bundles\` (dir)

Locked-run and challenge-run output files (`calibration_locked_run.jsonl`, no equivalent for challenge) are **not present** — those splits have never been run.

**Consequence:** any calibration work has to touch both repos. Score-signal changes ship in aksharaMD; corpus/GT changes and eval runs happen in parsebench. Version-locking must span both.

---

## 11. Recommended plan revision

The 5-phase plan I proposed earlier assumed a greenfield build. Given this inventory, the accurate plan is:

- **Phase 1 — Ratify the claim.** Adopt the frozen contract's §4.1 HIGH-band claim as the operational USP claim ("`text_char_recall` ≥ 0.90 in ≥92% of HIGH-band docs" or the sentence-recall equivalent given GT quality). Decide on the score-policy question in §9.1 above. Write `docs/calibration/USP_CLAIM_V1.md` as a short pointer document that defers to the contract and locks the policy answer. **1–2 days.**
- **Phase 2 — Score-policy landing.** Attach the appropriate score caps or penalties to `W_MULTICOLUMN_ORDER` and `W_HEADER_FOOTER_TABLE_GARBLED` (per §9.1 choice). Bump `SCORING_POLICY_VERSION`. Add regression tests. **3–5 days.**
- **Phase 3 — Silent-failure recall.** Design and implement signals for the three remaining silent-failure classes (multi-column recall gap, `de`, `strikeUnderline`). Ship at same time or in a follow-up. **1–1.5 weeks.**
- **Phase 4 — Dev-split re-run and locked-split run.** In parsebench: re-run `calibration_dev_run` on the updated aksharaMD, expect HIGH-band raw false-safe rate <10%. If clean, open the 35-doc locked split. Then the 15-doc challenge split. Write `docs/calibration/READINESS_CALIBRATION_REPORT.md` per contract §8. **3–5 days.**
- **Phase 5 — Publish the defensible claim.** Update README with the exact number backed by locked+challenge results. Wire the dev-split re-run as a CI regression job that runs on `SCORING_POLICY_VERSION` bump. **2 days.**

**Total: ~3 weeks of engineering, not 4.** The savings come from not rebuilding the corpus.

---

## 12. Open questions for Phase 1

1. Do we ratify the contract's HIGH-band claim ("`text_char_recall` ≥ 0.90 in ≥92% of HIGH-band docs") as the operational USP claim, or write a narrower claim we can back with the current (bag-of-sentence) GT?
2. On multi-column and table-tier failures — do we fix them (rewrite parser paths) or add score signals that route them to OK/RISKY (score-only fix)? The score-only fix is faster; the parser fix is more valuable.
3. Do we accept the `multimodal_usable` product dimension as a valid PASS state, or hold every image-only doc to a text-only bar?
4. Do we treat the frozen contract as immutable and write a v2 if we need to change it, or amend it? (Contract §9 says the corpus/methodology is frozen; the score formula is not.)
