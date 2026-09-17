# Study Freeze Manifest V1
## AksharaMD Validation Study — B1a-7c

**Status:** AUTHORIZED — Study Freeze V1 (2026-09-16)  
**Prepared:** 2026-09-16  
**Supersedes:** B1a-7c.2 specification draft (pre-correction)  
**Prerequisite:** B1a-7b.2 apparatus COMPLETE (Attempt #5 PASS, 2026-09-16)

---

## 0. Purpose and scope

This document is the proposed study freeze for AksharaMD validation. Once authorized, it becomes the binding preregistered design. Nothing in it may be changed after freeze except by explicit amendment with documented rationale.

**Scope:** Validation of AksharaMD readiness scores and detectors against:
1. Existing labeled corpora (Track A — objective fidelity)
2. Human usability judgment (Track B — usability validity, adaptive design)
3. Downstream QA task performance (Track C — consequential validity)

**Not in scope:** New parser development, corpus annotation at scale, or any result that requires parser execution before this document is frozen.

**Execution authorization:** Track A and Track C execution are authorized upon freeze. Track B execution is authorized only after the Track B Allocation Manifest is generated mechanically from Track A results per §5.

---

## 1. AksharaMD scoring architecture (reference snapshot)

All claims below are claims about the production system at the time of execution. The scoring architecture is:

- **Policy version:** `SCORING_POLICY_VERSION = "1.10"` (frozen; no scoring code changes permitted after freeze)
- **Format baseline:** PDF = 87
- **Score bands:** HIGH ≥ 85 | OK 70–84 | RISKY 50–69 | POOR < 50
- **Scoring logic:** Additive penalties + sequential score caps; no weighted normalization

**Candidate-tier detectors (cap at 69 — RISKY):**

| Detector ID | Description |
|---|---|
| `W_MULTICOLUMN_ORDER` | Reading-order disruption from multi-column layout |
| `W_TABLE_MISSING` | Table present in source but absent from parsed output |
| `W_ENCODING_ARTIFACTS` | Encoding errors producing garbled or unreadable characters |
| `W_IMAGE_ONLY_TEXT_BAR_FAIL` | Text rendered as image, inaccessible to text extraction |
| `W_DROPPED_CONTENT` | Text blocks present in source not recovered in output |

**Experimental-tier detectors (cap at 84 — top of OK):**

| Detector ID | Description |
|---|---|
| `W_HEADER_FOOTER_TABLE_GARBLED` | Header/footer content misplaced into body text |
| `W_TABLE_EXPECTED_NOT_EXTRACTED` | Table structure expected but not structurally recovered |
| `W_PLACEHOLDER_STUB` | Figure or table replaced by stub text with no content |
| `W_GIBBERISH` | Output contains high density of non-semantic character sequences |

**Stable penalty detectors** (no cap, self-verifying or objectively measurable): `PARSE_ERRORS`, `MISSING_PAGE`, `NEAR_EMPTY_OUTPUT`, `LOW_TEXT_DENSITY`, `GLYPH_ARTIFACTS`, `REPEATED_CONTENT`, `TOKEN_BLOAT`, `NO_HEADINGS_MULTIPAGE`, `COL_GENERIC_TABLES`, `IMAGE_PLACEHOLDER_NO_FALLBACK`, `NO_TEXT_IN_IMAGE`.

**Suppression:** `OCR_REQUIRED` and `OCR_ATTEMPTED_SPARSE` suppress `NEAR_EMPTY_OUTPUT` and `LOW_TEXT_DENSITY`.

---

## 2. Parser matrix

All four parsers in the proven apparatus are used for all tracks:

| Parser ID | Type | GPU required |
|---|---|---|
| `aksharamd-reference` | AksharaMD built-in (PDF text layer) | No |
| `marker` | VLM-based | Yes (always) |
| `docling` | VLM-based | Yes (always) |
| `markitdown` | PDF text layer | No |

GPU availability must be confirmed before execution of any run involving Marker or Docling. This applies regardless of which corpus the PDFs come from.

---

## 3. Corpus stack and licenses

| Corpus | License | Track | Role |
|---|---|---|---|
| olmOCR-Bench | ODC-BY | A | Primary assertion-level GT for text/ordering/encoding/image-text failures |
| DocLayNet | CDLA-Permissive | A | Table detection and structural-association GT |
| FinTabNet.c | CDLA-Permissive-2.0 | A | Table cell fidelity GT (TEDS metric) — **NOT_EXECUTABLE_V1** (source PDFs not available; see §6.3) |
| OmniDocBench | Research-only | — | Excluded from Track A (pre-execution corpus-format incompatibility; see §6.4); evaluator code retained as historical reference |
| DEV corpus (B1a-7b.2 frozen) | Internal | B | Human usability review surface |
| QASPER | CC-BY | C | Scientific QA downstream consequence |
| MMLongBench-Doc | Research-use | C | Long mixed-domain document QA |
| TAT-DQA | CC-BY | C | Financial table QA |

OmniDocBench is excluded from Track A execution: the v1.5 corpus consists of JPEG and PNG images only; no PDFs are present, and all four frozen parsers require PDF input. This was discovered pre-execution (see §6.4). The evaluator code commit is retained as a historical reference in §8. No OmniDocBench results will be produced in V1.

FinTabNet.c is NOT_EXECUTABLE_V1: the HF distribution (`bsmock/FinTabNet.c`) contains corrected annotations and structure XML but not the source page PDFs required by the four frozen parsers. The original IBM distribution (which contained the source PDFs) is no longer available, and the only identified alternative (a community Kaggle mirror) cannot be cryptographically or otherwise sufficiently verified against the original distributed bytes. This was determined by pre-execution provenance investigation before any parser execution (see §6.3). The TEDS endpoint is NOT_MEASURED_V1. The frozen 500-table selection is retained as provenance evidence. This affects the evidentiary scope of V1: the table-cell-fidelity/TEDS-specific validation component originally assigned to FinTabNet.c is not evaluated in V1.

---

## 4. Preregistered claims

Four claims are preregistered. Each claim has a **statistical evidence criterion** (objectively reported) and a separate **product acceptance criterion** (requires documented rationale for the minimum practically important effect, applied after execution).

### Claim 1 — Detector agreement (Track A)

**Question:** Does AksharaMD's detector agree with an independent benchmark evaluator's determination that a given parser output failed a fidelity assertion?

**Unit of analysis:** Parser × document (not document alone). A "positive" is a (parser, document) pair for which the benchmark evaluator independently flags a fidelity failure in the relevant failure mode. A "negative" is a (parser, document) pair for which the benchmark evaluator finds no such failure.

**Why this framing matters:** AksharaMD receives a parsed output and judges it. Whether the source document contains a table is a property of the source; whether that table was correctly recovered is a property of the (parser, document) pair. GT labels must therefore be assigned at the parser × document level. Using olmOCR-Bench assertion pass/fail as the GT label is valid because the assertion evaluates the parser's output against the source — it is not circular.

**Statistical evidence criterion:** For each detector listed in §3.1, report:
- Precision (PPV) and 95% Wilson CI
- Recall (sensitivity) and 95% Wilson CI  
- False-positive rate and 95% CI
- Effect estimate is the observed precision and recall; CI width is the primary evidence of precision

**Product acceptance criterion:** TBD after execution. The minimum practically important precision and recall will be documented with rationale before the acceptance decision is made. No threshold is preregistered because no threshold has been justified.

**Framing note:** This is not a claim that AksharaMD replicates olmOCR-Bench's evaluator. It is a claim that AksharaMD's detector fires on (parser, document) pairs that an independent evaluator also flags, and does not fire on pairs the evaluator clears.

### Claim 2 — Fidelity-score monotonicity (Track A)

**Question:** When objective fidelity degrades (as measured by benchmark metric), does the AksharaMD readiness score move in the expected direction and by a meaningful magnitude?

**Unit of analysis:** Parser × document (same as Claim 1; readiness score is per parse output).

**Statistical evidence criterion:** Report Spearman ρ between AksharaMD readiness score and benchmark metric (assertion pass-rate for olmOCR-Bench, structural association accuracy for DocLayNet) with 95% bootstrap CI. TEDS (FinTabNet.c) is NOT_MEASURED_V1; CDM (OmniDocBench) is NOT_MEASURED_V1. The evidentiary scope of this claim in V1 is limited to the two executable Track A corpora. Report separately for:
- Detector validation sub-question (Claim 1): does the detector fire correctly?
- Score validation sub-question (Claim 2): when it fires, does the aggregate score move appropriately?

These two sub-questions must be analyzed and reported separately. A detector may have excellent precision/recall while the aggregate readiness score is miscalibrated — and vice versa.

**Product acceptance criterion:** TBD after execution, with documented rationale.

### Claim 3 — Usability validity (Track B)

**Question:** When a human reviewer judges a parsed output as usable for knowledge-work tasks, does the AksharaMD readiness score agree (and vice versa)?

**Operationalized:** P(reviewer judges "usable" | readiness in HIGH band) and P(reviewer judges "not usable" | readiness in POOR band). False-safe rate: P(reviewer judges "not usable" | readiness ≥ 70).

**Statistical evidence criterion:** Proportion estimates with 95% Wilson CI per band. False-safe rate with 95% CI. Sample size derived per §5 (adaptive-design rule).

**Product acceptance criterion:** TBD after execution, with documented rationale.

### Claim 4 — Consequential validity (Track C)

**Question:** Does a lower AksharaMD readiness score predict larger degradation in downstream LLM QA performance when parsed output is substituted for reference output?

**Task degradation definition:** `degradation = EM(reference) − EM(parsed)` (positive = parser hurt performance, zero = parity, negative = parser improved performance). This sign convention is used throughout. Do not invert.

**Statistical evidence criterion:** Spearman ρ(readiness score, task degradation) with 95% bootstrap CI, reported separately per corpus (QASPER, MMLongBench-Doc, TAT-DQA) and pooled. Expected direction: negative ρ (higher readiness → lower degradation). If ρ is positive, that constitutes evidence against the scoring system.

**Existing evidence:** r = 0.64 on tables in the parsed-vs-raw Plan E pilot (QASPER, n=25, 4-arm). This is prior evidence only; Track C re-establishes it with preregistered endpoints and extends to MMLongBench-Doc and TAT-DQA.

**Product acceptance criterion:** TBD after execution, with documented rationale.

---

## 5. Two-stage preregistration

### Stage 1 (frozen now, before any execution)

- Tracks A and C: fully specified in this document. All corpus choices, sample sizes, metrics, and analysis plans are frozen.
- Track B adaptive-design rule: frozen as a mechanical formula in §5.1. The rule is applied after Track A; it does not require human judgment.

### Stage 2 (generated mechanically after Track A)

- Track B Allocation Manifest: generated by applying the §5.1 formula to the Track A score distribution. No human discretion is applied at Stage 2. The manifest is appended to this document as an exhibit before any human review work begins.

### 5.1 Track B adaptive-design rule (frozen)

The Track B statistical target per readiness band is:

```
n_target = ceil( (z_{α/2}² × p_b × (1 − p_b)) / h² )
```

Where:
- `z_{α/2} = 1.96` (95% CI, two-sided)
- `p_b = 0.5` (conservative, maximum-variance assumption)
- `h` = target half-width of 95% CI = **0.08** (±8 percentage points)
- Result: `n_target = ceil(1.96² × 0.25 / 0.08²) = ceil(150.1) = 151`

**N semantics:** `n_target = 151` is the number of **non-abstained adjudicated labels** required per band, not the number of raw parser-document pairs recruited. A reviewer abstains when they cannot render a usable/not-usable judgment on a given artifact. Abstained artifacts do not count toward `n_target`. See §5.2 for the oversampling rule that translates `n_target` into a recruitment count.

**Pair selection rule:** For each band, select pairs by ascending sort of `SHA256(canonical_pair_id || freeze_seed)`, where `freeze_seed` is recorded in §8. No human curation after the sort.

**Review protocol:** Reviewer contract and review surface are as specified in `REVIEWER_CONTRACT_B1_V1.md`. The review surface presented to each reviewer is the blinded `reviewer_artifact` produced by the B1a-7b.2 apparatus. Parser identity is not revealed during review.

### 5.2 Oversampling rule and sparse-band handling (frozen)

**Frozen abstention rate assumption:** 15%. This is conservative for expert reviewers on the defined review surface. The recruitment count per band is derived from `n_target`:

```
n_recruit = ceil(n_target / (1 − 0.15)) = ceil(151 / 0.85) = ceil(177.6) = 178 parser-document pairs per band
```

**If actual abstention ≤ 15%:** The 151 non-abstained label target will be met or exceeded at the recruited n.

**If actual abstention > 15%:** Report the achieved non-abstained n and the achievable CI half-width computed as `h_actual = sqrt(1.96² × 0.25 / n_actual)`. Do not back-fill with additional pairs drawn post-hoc. Back-filling introduces human discretion over which pairs enter the analysis after partial review is complete and is prohibited.

**Sparse-band rule:** A band is sparse if it contains fewer than `n_recruit = 178` eligible parser-document pairs. For a sparse band:

1. Recruit all eligible pairs in the band.
2. Do not recruit from other bands to compensate; do not reallocate unused quota to any other band.
3. Report the achievable CI half-width at the actual non-abstained n.
4. Flag the band as "below recruitment target" in the Track B Allocation Manifest.

**Underpowered band threshold:** A band with fewer than 20 eligible pairs is flagged as "underpowered." No Wilson CI is reported for it; data are reported descriptively only.

---

## 6. Track A — sample sizes and stratification

### 6.1 olmOCR-Bench

**Run in full.** 1,403 PDFs, 7,010 unit-test assertions. The assertion structure (per-PDF pass/fail per failure mode) is uniquely valuable and cannot be recovered from a sample. The corpus is small enough that full execution is feasible.

Each assertion maps to exactly one failure mode. Mapping is preregistered in §7.

### 6.2 DocLayNet

**Stratified sample, not full corpus.** Rationale: the full val split is ~1,000 documents with ~5 pages per document on average. The full corpus exceeds what is needed.

**Sampling math (corrected):**
- ICC (intra-document correlation for table detection rate) = 0.30 (conservative)
- Mean cluster size m̄ = 5 pages per document
- Design effect: `DE = 1 + (m̄ − 1) × ICC = 1 + 4 × 0.30 = 2.2`
- Target CI: ±4 percentage points at 95% confidence
- Simple-random effective N: `n_eff = (1.96² × 0.5 × 0.5) / 0.04² ≈ 600 pages`
- Raw pages required: `n_raw = n_eff × DE = 600 × 2.2 = 1,320 pages`
- Documents required: `1,320 / 5 ≈ 264 documents`

Clustering INCREASES the pages required (not decreases). The formula is `n_raw = n_eff × DE`, not `n_eff / DE`.

The full val split (~1,000 documents, ~5,000 pages) is more than adequate. Draw a deterministic stratified sample of **280 pages** (rounded up from 264 for headroom), stratified by document category (financial, scientific, etc.) proportional to category prevalence in the eligible pool. Selection seed recorded in §8.

**Implementation note (B1a-8):** The selection operates at the page_hash level (the canonical DocLayNet unit). The 280 selected pages come from 53 unique source documents; multiple pages from the same document may be selected. The cluster-correction reasoning above motivates the sample-size floor; it is not a guarantee that the implementation achieves document-level cluster sampling. See STAGE1_CORPUS_SNAPSHOT_MANIFEST_V1.md §2 for the full selection record.

### 6.3 FinTabNet.c

**Stratified sample.** Target: 500 tables with deterministic stratified selection by table complexity tier. TEDS computed per table. Selection seed recorded in §8.

**Pre-execution corpus-capability correction (B1a-8b, 2026-09-16):** The original three-tier design (simple / compound / multi-page spanning) was reduced to two tiers. Inspection of the FinTabNet.c V1 ground truth (PDF_Annotations JSON and Structure XML) confirmed that neither file format contains any field identifying cells that span across page boundaries. Multi-page spanning cannot be determined mechanically from the V1 annotations; inventing a heuristic proxy would introduce an unmeasurable classification error. The tier is therefore removed from the stratification design as a pre-execution corpus-capability correction. This is not a methodological redesign: the study freezes 500 tables with deterministic stratified selection; only the number of strata changes from three to two.

**Frozen tier definitions (locked B1a-8b):**

| Tier | Definition |
|---|---|
| SIMPLE | Every cell has `len(row_nums) == 1` and `len(column_nums) == 1`. No cell spans multiple rows or columns. |
| COMPOUND | At least one cell has `len(row_nums) > 1` or `len(column_nums) > 1`. |
| ~~MULTI-PAGE SPANNING~~ | ~~Removed: not measurable from FinTabNet.c V1 ground truth.~~ |

Classification is fail-closed: missing or non-list `row_nums`/`column_nums` on any cell raises a `ValueError`; the classifier never silently defaults a malformed annotation to SIMPLE.

**Proportional allocation (frozen):** Val-split eligible pool (post-exclusion): 3,714 SIMPLE, 5,936 COMPOUND (9,650 total). Largest-remainder allocation to 500 slots: **SIMPLE = 192, COMPOUND = 308**. Expected: SIMPLE 3714/9650 × 500 ≈ 192.44 → floor 192; COMPOUND 5936/9650 × 500 ≈ 307.56 → floor 307; remainder 1 → COMPOUND (larger fractional part). Sum = 500. Within each tier: rank by `SHA-256(structure_id || freeze_seed)` ascending; take the first N.

**Corpus confirmed pre-execution:** FinTabNet.c (`bsmock/FinTabNet.c`, HF revision `e5673a90b98d02c4832f9e836d72762f0e8933a0`, license: CDLA-Permissive-2.0). PubTabNet is excluded: the dataset consists of table images, not source PDFs, and parsers require PDF input. Version pinned in §8.

**NOT_EXECUTABLE_V1 — source-PDF provenance investigation (B1a-8c, 2026-09-16):**

Pre-execution inspection confirmed that the `bsmock/FinTabNet.c` HF distribution contains only JSON annotation files (`FinTabNet.c-PDF_Annotations.tar.gz`, 77,437 files) and XML structure files (`FinTabNet.c-Structure.tar.gz`). No source PDF files are present.

The annotation JSON fields `pdf_folder` (e.g., `SBUX/2017/`) and `pdf_file_name` (e.g., `page_23.pdf`) reference page-level PDFs from the original IBM Research FinTabNet dataset (WACV 2021, arXiv 2005.00589), which was hosted on IBM's Data Asset Exchange (DAX). IBM DAX has since been deprecated; the CDN endpoint is unreachable. No authoritative IBM-published archive or DOI exists. The only identified working source is a community Kaggle mirror referenced by NVIDIA's Nemotron dataset documentation, but this mirror cannot be cryptographically verified against the original IBM-distributed bytes — no hash, EDGAR accession number, or other identifier is embedded in the FinTabNet.c annotations to establish a verifiable provenance bridge.

Decision (authorized 2026-09-16): the provenance standard established for this study cannot be met for the FinTabNet.c source PDFs. Parser execution is not performed. The TEDS endpoint is **NOT_MEASURED_V1**. This is not a parser failure, a missing-data outcome, or a zero result — it is a pre-execution instrument limitation.

**What is retained:** The frozen HF annotation revision (`e5673a90`), both downloaded and byte-verified archives, the 9,650-table eligible population, the 500-table selection (192 SIMPLE / 308 COMPOUND, SHA-256 `f8ec903a…`), and this investigation record are all preserved as provenance evidence.

**Impact on evidentiary scope:** The table-cell-fidelity/TEDS-specific validation component originally assigned to FinTabNet.c in Claim 2 is not evaluated in V1. The final report must explicitly identify this component as not measured. This is not remedied by substituting another corpus or metric (Option C is not authorized); see §3 and §14.

### 6.4 OmniDocBench — excluded from Track A (pre-execution incompatibility)

**Excluded from Track A.** OmniDocBench v1.5 (`lllcho/OmniDocBench`, HF revision `91fe284bbfacfa687959ae3eb00846ca852aa907`) was inspected pre-execution and found to consist entirely of rasterized images: 981 JPEGs and 377 PNGs. No PDFs are present in the distribution. The four parsers in the frozen apparatus (`aksharamd-reference`, `marker`, `docling`, `markitdown`) all require PDF input; they cannot operate on rasterized images.

This is a corpus-format incompatibility, not a quality judgment about OmniDocBench. The incompatibility was discovered before any parser execution, so it does not affect the preregistered analysis plan for Tracks A and C. The OmniDocBench evaluation code (evaluator commit `193627ae`) is retained as a historical reference in §8.

**Consequences for V1:** The CDM metric and the multi-metric research-deliverable evaluation originally scoped to OmniDocBench are not available in V1. See §7 for the revised detector-to-GT mapping that removes OmniDocBench as a GT source.

---

## 7. Detector-to-GT mapping

The following table maps each AksharaMD detector ID to its ground truth source, the GT strength, and the unit of analysis. This mapping is preregistered and replaces any conceptual claim about failure-mode coverage.

| AksharaMD Detector ID | Tier | Corpus | GT source | GT strength | Metric | Unit |
|---|---|---|---|---|---|---|
| `W_TABLE_MISSING` | Candidate (69) | DocLayNet | Table bounding-box annotations | Direct: table presence confirmed by human annotators independent of any parser | Table detection recall | Parser × document |
| `W_MULTICOLUMN_ORDER` | Candidate (69) | olmOCR-Bench | Reading-order assertion pass/fail | Direct: assertion evaluates parser output ordering against source order | Agreement rate with assertion evaluator | Parser × document |
| `W_ENCODING_ARTIFACTS` | Candidate (69) | olmOCR-Bench | Character/encoding assertions | Direct: assertions evaluate character-level encoding correctness against source content | Agreement rate with assertion evaluator | Parser × document |
| `W_IMAGE_ONLY_TEXT_BAR_FAIL` | Candidate (69) | olmOCR-Bench | Image-text detection assertions | Direct: assertion evaluates whether text-as-image content was recovered | Agreement rate with assertion evaluator | Parser × document |
| `W_DROPPED_CONTENT` | Candidate (69) | olmOCR-Bench | Text omission/addition assertions | Direct: assertion evaluates completeness of recovered text | Agreement rate with assertion evaluator | Parser × document |
| `W_HEADER_FOOTER_TABLE_GARBLED` | Experimental (84) | DocLayNet | Structural-region association annotations | Indirect: header/footer regions labeled; garbling inferred from content-to-region mismatch | Structural association accuracy | Parser × document |
| `W_TABLE_EXPECTED_NOT_EXTRACTED` | Experimental (84) | FinTabNet.c — **NOT_MEASURED_V1** | Table cell content annotations (TEDS) | Direct: TEDS measures structural and content recovery against human-annotated GT | NOT_MEASURED_V1: FinTabNet.c source PDFs unavailable; see §6.3 | Parser × document |
| `W_PLACEHOLDER_STUB` | Experimental (84) | — | No defensible external GT in V1 (OmniDocBench excluded — see §6.4) | — | Experimental-tier detector; calibrated internally; no external GT validation available in V1 | Parser × document |
| `W_GIBBERISH` | Experimental (84) | olmOCR-Bench | Character quality assertions | Indirect: character-level quality assertions; gibberish detection is a related but not identical concept | Agreement rate with character-quality assertions | Parser × document |

**Stable penalty detectors** (`PARSE_ERRORS`, `MISSING_PAGE`, `NEAR_EMPTY_OUTPUT`, `LOW_TEXT_DENSITY`, etc.) are not included in Track A detector validation. These are either self-verifying (page count comparison), objectively measurable (token density), or already calibrated. They are used as covariates in Track A score regression but not as primary detection targets.

**GT strength definitions:**
- **Direct:** The GT annotation measures exactly what the detector claims to detect.
- **Indirect:** The GT annotation measures a neighboring concept from which the detector's target can be partially inferred; agreement rate is interpretable but with acknowledged gap.

---

## 8. Freeze parameters

The following values are recorded at freeze time and must not change after freeze. They seed all deterministic sampling and selection operations.

| Parameter | Value | When recorded |
|---|---|---|
| Freeze date | 2026-09-16 | At freeze |
| Freeze seed (hex, 32 bytes) | `6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa` | At freeze |
| SCORING_POLICY_VERSION | `"1.10"` | At freeze |
| Track C LLM model ID | `claude-sonnet-4-6` | At freeze |
| Track C competitor arm | `none` | At freeze |
| Track C LLM prompt SHA256 (canonical LF) | `2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601` | Stage 1 prerequisite filled — `docs/evaluation/TRACK_C_PROMPT_V1.txt` |
| olmOCR-Bench dataset revision | HF `allenai/olmOCR-bench` rev `54a96a6fb6a2bd3b297e59869491db4d3625b711`; 1,412 files verified | Pre-execution (corpus confirmed) |
| DocLayNet split + version | `docling-project/DocLayNet-v1.2` HF rev `0daf93102e2efce76c3e11a274a5e0d0969391d3`, split=validation, 7 shards | Pre-execution (corpus confirmed) |
| FinTabNet.c version | `bsmock/FinTabNet.c` HF rev `e5673a90b98d02c4832f9e836d72762f0e8933a0`, CDLA-Permissive-2.0. **NOT_EXECUTABLE_V1**: annotation archives downloaded and byte-verified; source PDFs not available from a verifiable authoritative distribution; TEDS endpoint NOT_MEASURED_V1. Frozen 500-table selection retained as provenance evidence. See §6.3. | Pre-execution (corpus confirmed; execution status: NOT_EXECUTABLE_V1) |
| OmniDocBench | EXCLUDED from Track A. Dataset: `lllcho/OmniDocBench` v1.5 HF rev `91fe284bbfacfa687959ae3eb00846ca852aa907` (image-only; no PDFs). Evaluator code: commit `193627ae` (historical ref only). See §6.4. | Pre-execution (incompatibility discovered) |
| Track A apparatus run directory | TO BE RECORDED AT STAGE 1 START | Before any Stage 1 results |
| Track C execution manifest path | TO BE RECORDED AT STAGE 1 START | Before any Stage 1 results |

The freeze seed was generated at freeze authorization by `os.urandom(32).hex()`. The Track C prompt is committed at `docs/evaluation/TRACK_C_PROMPT_V1.txt`; its canonical LF SHA-256 is recorded above (content normalized to LF line endings before hashing; equals the git blob SHA regardless of host `core.autocrlf` setting). This fills the Stage 1 prerequisite that was explicitly left open in the original freeze commit. It does not constitute a methodological change to V1.

---

## 9. Track C specification

### 9.1 Task and corpus

| Corpus | Task type | Metric | N (documents) |
|---|---|---|---|
| QASPER | Scientific paper QA | EM and F1 | 25 documents (matching Plan E pilot sample) + 25 new for n=50 |
| MMLongBench-Doc | Long mixed-domain QA | EM and F1 | 30 documents, stratified by domain |
| TAT-DQA | Financial table QA | EM and F1 | 30 documents |

### 9.2 LLM and prompt specification

**Model:** Claude claude-sonnet-4-6 (or equivalent Sonnet-tier at execution time; model ID recorded in execution record)  
**Prompt:** Identical across all arms (reference / parsed / competitor). Prompt frozen at freeze time; hash recorded in §8.  
**Temperature:** 0.0  
**Context window:** Full document text provided; no chunking unless document exceeds context limit (in which case: truncate at equal character count across all arms for that document).

### 9.3 Arms

- **Reference arm:** `aksharamd-reference` parser output (AksharaMD built-in PDF text layer)
- **Parsed arms:** `marker`, `docling`, `markitdown`
- **Competitor arm:** A competitor arm may be added only if declared at freeze time and recorded in §8 before Stage 1 execution begins. No competitor arm may be added after Stage 1 has produced any results. If no competitor arm is declared at freeze, the field in §8 is recorded as "none" and no competitor arm is added.

### 9.4 Analysis

For each (parser, document, corpus):
```
degradation = EM(reference arm) − EM(parsed arm)
```

Positive degradation = the parsed output hurt QA performance relative to reference.  
Zero = parity with reference.  
Negative = the parsed output improved on reference (record but do not discount).

Primary analysis: Spearman ρ(AksharaMD readiness score, task degradation) with 95% bootstrap CI (10,000 resamples). Reported:
- Per corpus
- Pooled across corpora
- Stratified by readiness band (HIGH / OK / RISKY / POOR)

Expected direction: ρ < 0 (higher readiness → lower degradation). If ρ > 0, this is evidence of miscalibration and must be reported as such.

Secondary analysis: ΔEM by readiness band (median degradation per band with 95% CI). A well-calibrated scoring system should show: median degradation(POOR) > median degradation(RISKY) > median degradation(OK) > median degradation(HIGH).

---

## 10. Statistical analysis plan

### 10.1 Separation of detector validation and score validation

These are different questions and must be reported separately in every analysis section.

- **Detector validation (Claim 1):** Does the detector fire on the right (parser, document) pairs? Measured by precision, recall, FPR against GT labels.
- **Score validation (Claim 2):** When objective fidelity degrades, does the readiness score move appropriately? Measured by Spearman ρ between readiness score and benchmark metric.

A high-precision detector with a miscalibrated aggregate score, and a well-calibrated score with a noisy underlying detector, are both informative but distinct failure modes.

### 10.2 CI method

- Proportions (precision, recall, FPR): Wilson score interval
- Spearman ρ: 95% bootstrap CI, 10,000 resamples
- TEDS distributions: Mann-Whitney U with effect size (rank-biserial r)

### 10.3 Multiple comparison policy

No correction for multiple comparisons across detectors. Each detector is a separate preregistered claim with its own CI. Bonferroni correction is not applied; instead, the full CI for each detector is reported and interpreted independently. This is preregistered.

### 10.4 Missing data

If a (parser, document) pair produces a DEFECT exit status (per the B1a-7b.2 apparatus), it is excluded from analysis for that parser and noted in the execution record. If DEFECT rate exceeds 20% for any parser, that parser's Track C results are flagged as potentially unrepresentative.

---

## 11. What constitutes preregistered success / failure

The following criteria are observable and do not require post-hoc judgment. They distinguish evidence from acceptance decisions.

**Evidence criteria (preregistered — observable after execution):**

| Criterion | Observable threshold |
|---|---|
| Track A detector validation: precision CI lower bound | Report for each detector; no threshold preregistered |
| Track A score validation: Spearman ρ direction | ρ must be positive (higher readiness ↔ higher fidelity) for score validation to be coherent |
| Track C degradation direction | ρ(readiness, degradation) must be negative; a positive ρ is falsifying |
| Track C monotonicity by band | Median degradation(POOR) > median degradation(OK) is the minimal directional prediction |

**Acceptance decision (post-execution — requires documented rationale):**

After observing the evidence, the acceptance decision requires stating the minimum practically important effect with justification. The acceptance decision is not preregistered here because no threshold has been justified from first principles. Justification must be documented before the acceptance or rejection decision is recorded.

**What the study cannot prove:** Absence of evidence is not evidence of absence. A wide CI on a detector's precision does not mean the detector is uncalibrated; it means the sample was insufficient to estimate precision tightly. Increase N in a follow-on study.

---

## 12. Execution sequence

1. Generate freeze seed; record in §8
2. Record SCORING_POLICY_VERSION; confirm no scoring code changes after this point
3. **Track A execution:** run all four parsers on olmOCR-Bench (full corpus, 1,403 PDFs) and DocLayNet val (n=280 pages). FinTabNet.c is NOT_EXECUTABLE_V1 (source PDFs unavailable; see §6.3). OmniDocBench excluded (see §6.4).
4. **Track C execution:** run all four parsers on QASPER (n=50), MMLongBench-Doc (n=30), TAT-DQA (n=30) with frozen LLM and prompt
5. **Generate Track B Allocation Manifest** mechanically from Track A score distribution: apply §5.1 formula for n_target, §5.2 formula for n_recruit and sparse-band handling; append as Exhibit B to this document before any review begins
6. **Track B execution:** human review of blinded reviewer artifacts; n_recruit pairs per band per §5.2; abstained labels do not count toward n_target
7. **Analysis:** report all four claims per §10
8. **Acceptance decision:** document minimum practically important effect with rationale; apply to observed CIs

Tracks A and C may run concurrently. Track B cannot begin until the Track B Allocation Manifest is generated from Track A results.

---

## 13. TBD audit — resolution path for every open parameter

Every open parameter in this document is either (a) resolved before Stage 1 execution or (b) governed by a frozen mechanical Stage-2 rule. No TBD requires human judgment at execution time.

| Open parameter | Location | Resolution class | Resolution rule |
|---|---|---|---|
| Freeze seed | §8 | (a) RECORDED | `6c270ac293b348ca27279bdd012375aa6c707be494085e70781637a99a6322fa` |
| SCORING_POLICY_VERSION | §8 | (a) RECORDED | `"1.10"` — no scoring changes permitted after freeze |
| Track C LLM model ID | §8 | (a) RECORDED | `claude-sonnet-4-6` |
| Track C competitor arm | §8 | (a) RECORDED | `none` |
| Track C LLM prompt SHA256 | §8 | (a) RECORDED | `2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601` — `TRACK_C_PROMPT_V1.txt` |
| Corpus versions | §8 | (a) RECORDED | All corpora pinned pre-execution: olmOCR-Bench (HF rev `54a96a6`; executable), DocLayNet val (HF rev `0daf931`; executable), FinTabNet.c (HF rev `e5673a9`; NOT_EXECUTABLE_V1 — see §6.3), OmniDocBench (excluded; see §6.4) |
| Track A apparatus run directory | §8 | (a) Before Stage 1 results | Recorded at Stage 1 start |
| Track C execution manifest | §8 | (a) Before Stage 1 results | Recorded at Stage 1 start |
| Track B allocation (which pairs) | §5.1–5.2 | (b) Mechanical Stage-2 rule | SHA256-sort on frozen seed; no discretion |
| Track B n_recruit per band | §5.2 | (b) Mechanical Stage-2 rule | ceil(n_actual_pairs_in_band / 0.85), capped at 178 |
| Track B achievable CI when sparse | §5.2 | (b) Mechanical Stage-2 rule | h_actual = sqrt(1.96² × 0.25 / n_actual_noabs) |
| Product acceptance criteria (all four claims) | §4, §11 | Post-execution | Not a TBD that blocks execution; rationale documented before acceptance decision, per §11 |

**No open parameter requires discretion during execution.** Parameters in class (a) are locked before Stage 1 begins. Parameters in class (b) are computed mechanically from Stage 1 outputs using formulas frozen in this document. Product acceptance criteria are post-execution decisions, not execution-time parameters.

---

## 14. What does not follow automatically from a freeze

- "Study Freeze" does not authorize corpus downloads above the stated sample sizes.
- "Study Freeze" does not authorize annotation or labeling work beyond what the apparatus produces.
- OmniDocBench is excluded from Track A execution entirely (pre-execution corpus-format incompatibility; see §6.4). No OmniDocBench results will be produced in V1. The evaluator code commit is retained as a historical reference in §8.
- FinTabNet.c is NOT_EXECUTABLE_V1 (pre-execution source-PDF provenance limitation; see §6.3). The TEDS endpoint is NOT_MEASURED_V1. No replacement corpus or metric is authorized. The frozen 500-table selection is retained as provenance evidence. The table-cell-fidelity/TEDS-specific validation component of Claim 2 is not evaluated in V1; the final report must identify it explicitly as not measured, not as zero or missing-at-random.
- Track B acceptance does not follow from Track A results — it requires a separate acceptance decision per §11.
- No result from this study is a product claim until the acceptance decision (§11) is documented.

---

## 15. Amendment record

### B1a-8d — Track C prompt SHA provenance correction (2026-09-16, pre-execution)

**Nature:** Provenance-recording correction.  The prompt content is unchanged.

**Discovery:** During Stage 1 execution-manifest construction, `verify_study_freeze.py` was extended to compute the SHA-256 of `docs/evaluation/TRACK_C_PROMPT_V1.txt` and compare it against the §8 pin.  The computed SHA did not match the value recorded in §8 and §13.

**Investigation:** Three byte-level variants were checked against the pin `af772e60f96c70a6601705eae49c039dd492b0050c0973821fe7575354c15b24`:

| Variant | SHA-256 | Matches pin? |
|---|---|---|
| Git blob (pure LF, 301 bytes — what git stores regardless of host checkout settings) | `2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601` | No |
| LF-normalized (= git blob on this repo) | `2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601` | No |
| Raw on-disk bytes on Windows with `core.autocrlf=true` (CRLF, 309 bytes) | `beea2600374ed1cda3dde4b1375e64e04315acb98893ef65066c96ec667cb203` | No |

No byte-level representation of the committed file produces the originally recorded SHA.  The SHA `af772e60...` does not correspond to any derivable encoding of `TRACK_C_PROMPT_V1.txt` as stored in git at commit `67f54c4` or at any subsequent commit.  The file has been byte-identical since its introduction.

**Root cause:** The SHA was computed or transcribed incorrectly when both the prompt file and the freeze manifest were updated together in commit `67f54c4`.  No evidence exists that `af772e60...` corresponds to an intentionally frozen alternative prompt.

**Decision:** Correct the recorded SHA to match the actual committed artifact.  This is a provenance-recording correction, not a methodological change.  The prompt content — the artifact that governs all Track C evaluations — is unchanged.

**Hashing convention (frozen by this amendment):** The pin is the SHA-256 of the LF-normalized content of `TRACK_C_PROMPT_V1.txt` (all `\r\n` and lone `\r` replaced with `\n` before hashing).  This equals the git blob SHA (301 bytes, pure LF) and is stable across host platforms regardless of `core.autocrlf` setting.  On Windows with `core.autocrlf=true`, the on-disk file is checked out with CRLF (309 bytes, SHA `beea2600...`); the verifier normalizes to LF before computing the hash, so it passes on both Windows and Linux CI.

**Pre-execution status:** No Stage 1 parser or Track C LLM execution had occurred before this correction.  No results were observed before the discrepancy was detected and resolved.  The correction was made at the execution-manifest construction gate, which is the intended pre-execution integrity checkpoint.

**Fields corrected:**

| Location | Old value | New value |
|---|---|---|
| §8 parameter table — "Track C LLM prompt SHA256" value | `af772e60f96c70a6601705eae49c039dd492b0050c0973821fe7575354c15b24` | `2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601` |
| §8 parameter table — "Track C LLM prompt SHA256" field label | unchanged | `(canonical LF)` retained — this is accurate: the pin equals the git blob SHA (LF-normalized content) |
| §8 narrative | `its canonical LF SHA-256 is recorded above` | updated to clarify that normalization equals the git blob SHA and is platform-stable |
| §13 TBD audit table — "Track C LLM prompt SHA256" value | `af772e60f96c70a6601705eae49c039dd492b0050c0973821fe7575354c15b24` | `2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601` |

**Verifier hardening:** `benchmarks/eval_v1/verify_study_freeze.py` §7 (added in this correction) now computes the SHA-256 of `TRACK_C_PROMPT_V1.txt` raw bytes and compares it against the pinned value.  A mismatch fails the freeze verifier with a non-zero exit code.  An automated regression test (`tests/test_verify_study_freeze.py`) was added to prevent silent drift.
