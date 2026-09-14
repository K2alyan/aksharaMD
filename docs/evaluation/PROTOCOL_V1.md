# AksharaMD Evaluation Protocol V1

**Status:** DRAFT V1 — pre-freeze, pre-execution.
**Purpose:** Independent validation of AksharaMD as an extraction auditor.
**Audience:** Any technically competent reviewer able to execute the study without inventing methodological decisions.
**Authoring stance:** This protocol is designed to **falsify** AksharaMD's claims as rigorously as it is designed to support them. A successful study is one that produces defensible conclusions — including conclusions like *"W_GIBBERISH contributes essentially nothing on real corpora,"* *"the cap-at-84 threshold is wrong,"* or *"the general 'silent-failure detector' framing is too broad for V1."* If we cannot state up front what result would invalidate a claim, we cannot claim to have validated it.

---

## 0. Non-goals

This protocol does not:

- Prescribe scoring-policy changes, cap adjustments, or new detectors. Any such change is post-freeze and follows the amendment procedure in §10.
- Define the AksharaMD product-launch materials (whitepaper, leaderboard, README rewrite). Those are downstream of the evaluation, not part of it.
- Attempt to prove AksharaMD is superior to any existing benchmark or tool. The comparative claim we care about is *complementarity* (§6.1), not aggregate superiority.
- Establish per-parser recommendations or a public parser leaderboard. If the data supports one, it is a downstream artifact of the study; the study is not designed around it.

---

## 1. Study design overview

### 1.1 Three separate validity questions

The study evaluates AksharaMD across three distinct validity types. **Every measurement in this protocol belongs to exactly one.** No measurement may be used to support a claim of a different validity type.

| # | Question | Success looks like | Failure looks like |
|---|---|---|---|
| **1. Detector validity** | When a detector fires, did the failure it claims actually happen? | Precision, recall, and FPR meet predeclared thresholds on adjudicable failure classes. | A detector fires on cases independent reviewers agree are fine; or misses cases reviewers agree are broken. |
| **2. Score validity** | Does the AksharaMD score correspond to actual extraction severity? | Monotonic score/severity relationship; cap-value thresholds correspond to meaningful quality steps. | Score does not track severity; caps activate at inconsistent severity levels. |
| **3. Product utility** | Does AksharaMD tell users something useful they cannot get from existing evaluation? | Bidirectional disagreement produces adjudicated novel-information cases; localization is actionable; downstream failures are explained. | AksharaMD warnings duplicate conventional metrics or fail to explain downstream problems. |

A detector could achieve strong **detector validity** while providing no **product utility** if its findings are already available through simpler means. A detector could provide strong **product utility** with modest aggregate detector-validity numbers if the failures it uniquely catches are catastrophic and other tools miss them entirely. These are separate claims; the protocol keeps them separated throughout.

### 1.2 Claims ladder

The study may earn some levels and not others. That is an acceptable outcome. Language used publicly about AksharaMD after the study **must** match the highest level actually earned.

| Level | Type of claim | Requires |
|---|---|---|
| **L0** | Observed behavior — "on document X, W_DROPPED_CONTENT fired." | No study. Trivially true from any run. |
| **L1** | Validated detector — "W_DROPPED_CONTENT correctly identifies catastrophic omissions with recall ≥ X on the held-out set." | Detector-validity findings against oracle-grounded ground truth or human adjudication (§4). |
| **L2** | Validated score — "an AksharaMD score below 70 is a materially worse extraction than a score above 90 on comparable documents." | Score-validity findings against severity labels (§5). |
| **L3** | Comparative / product utility — "AksharaMD identifies failures that conventional aggregate metrics miss, and diagnoses valid outputs those metrics penalize." | Bidirectional disagreement adjudication (§6). |
| **L4** | Broad generalization — "AksharaMD is *the* extraction auditor for the RAG stack." | Cross-corpus, cross-parser, cross-document-type generalization that this V1 study does not attempt. |

**A single-study V1 will not earn L4.** L4 requires longitudinal use, external validation, and adoption evidence outside the scope of any protocol document.

### 1.3 Claim → evidence → falsification matrix

This is the master table for the study. Every public claim we plan to make must appear here with (a) the evidence required, (b) the threshold that must be met, and (c) the pre-declared observation that would prevent the claim.

| Proposed claim (level) | Evidence required | Falsification (predeclared) |
|---|---|---|
| **Detects catastrophic silent omissions** (L1) | Held-out oracle-grounded dropped-content evaluation on PMC-OA subset | Recall < 0.70 on documents where source XML shows > 30% word loss (rationale §12.1) |
| **Low false-alarm burden on clean documents** (L1) | Clean-native held-out corpus (Federal Register / SEC subset) | FPR > 0.05 across the three new content-axis detectors combined (rationale §12.2) |
| **Score reflects extraction quality** (L2) | Adjudicated severity labels vs score, per document | Weak (Spearman < 0.5) or non-monotonic score/severity relationship on the calibration set (rationale §12.3) |
| **Cap thresholds correspond to meaningful quality steps** (L2) | Distribution of adjudicated severity labels above/below each cap value | ≥ 30% of documents scored just below a cap have severity indistinguishable from those just above (rationale §12.4) |
| **Adds information beyond conventional metrics** (L3) | Adjudicated disagreement quadrants (§6.1) with an external metric | The novel-information cells (Conv=GOOD/AKS=BAD and Conv=BAD/AKS=GOOD) together < 15% of the disagreement quadrant, adjudicated as genuine and useful < 50% of the time (rationale §12.5) |
| **Localizes extraction problems** (L3) | Diagnostic-location adjudication on the disagreement quadrant | ≥ 30% of "silent-failure" flags produce localization that reviewers cannot map to a specific offending region within reasonable effort (rationale §12.6) |
| **Helps explain downstream RAG failures** (L3) | Controlled RAG demonstration (§6.2) | Extraction warnings correlate at Spearman < 0.3 with downstream QA-answer failures, or a placebo control (random flags at the same rate) explains equally well (rationale §12.7) |
| **The extraction-auditing layer nobody else provides** (L3 → L4) | All L3 items achieved AND at least two independent reviewers agree the tool surfaces classes of failures RAGAS/faithfulness alone cannot | The independent-review conclusion is that RAGAS post-hoc + a spot-check gives equivalent operational value at lower cost (rationale §12.8) |

The **rationales for every numerical threshold** live in §12, together with the explicit statement that each threshold is a product-design judgment (with defensible reasoning) rather than an empirical fact.

---

## 2. Corpora and ground truth

### 2.1 Ground-truth strength classification

AksharaMD detectors' claims are supportable only against ground truth of sufficient strength. This section names the three strength tiers explicitly so no measurement in the study can be misrepresented as evidence of a stronger validity type than the data supports.

| Tier | Name | Meaning | Example |
|---|---|---|---|
| **G1** | Oracle-grounded | An artifact external to AksharaMD unambiguously fixes what the correct output must contain. | PMC-OA article's XML full text (authoritative for textual content). |
| **G2** | Human-adjudicated criterion | No external oracle, but independent trained reviewers can answer a prewritten, detector-neutral question. Inter-annotator agreement is measured. | Two blinded reviewers see a flagged region and answer: *"Does this region contain a parser-generated substitute for source content that materially impairs use?"* |
| **G3** | Detector-defined behavior | The definition of the phenomenon *is* the detector rule. Cannot be independently validated; can only be measured for reproducibility and human criterion validity (G2). | Pattern-based `W_PLACEHOLDER_STUB` — the regex catalog defines what a "placeholder stub" is. |

Every detector's validity claims must be labeled with the tier of evidence that supports them (§4). **A G3 detector cannot earn an "oracle-grounded validity" claim under any circumstances.** It may earn a G2 human-adjudicated criterion claim, which is weaker but still meaningful — and honest.

### 2.2 Corpus manifest

Each candidate corpus is listed with (a) what kind of ground truth it provides, (b) which detectors it can adjudicate, (c) known limitations, and (d) which validity tier it supports.

| Corpus | GT type | Adjudicates | Limits | Tier |
|---|---|---|---|---|
| **PMC-OA** (PubMed Central Open Access XML + PDF pairs) | Full-text XML | W_DROPPED_CONTENT (word-set overlap), gibberish (against ground-truth prose vocabulary) | XML/PDF are separately authored; pixel-perfect equality is NOT a valid criterion. Ground truth is *textual content*, not layout or rendering. | G1 for textual omission; G3 for layout |
| **DocLayNet** (IBM layout annotations on ~80k pages) | Layout bboxes + block types | Structural detectors (W_MULTICOLUMN_ORDER, W_TABLE_MISSING, W_HEADER_FOOTER_TABLE_GARBLED), table geometry | Layout truth ≠ text-content truth. Cannot adjudicate W_DROPPED_CONTENT directly. | G1 for structural detectors |
| **Federal Register / SEC filings** (native-authored PDFs) | Source authorship known; typography clean | Baseline / FPR — the study's "no failure expected" corpus for the three new content-axis detectors | Text-authoritative comparison not always available; treat primarily as a false-positive-rate corpus. | G2 (via human adjudication of any flagged case) |
| **CUAD** (contract clause span annotations) | Character-offset spans for clause types | Localization; dropped-content within clause spans | Only annotated for legal contract clauses; not general-purpose | G1 for clause-level content presence |
| **ParseBench / RealDoc-Bench** (LlamaIndex, existing evaluation harnesses) | Rule-based scoring, ~2k pages, 167k rules | Cross-check against existing conventional metrics | Represents a *conventional* metric; useful for §6.1 disagreement analysis, not as ground truth. | Not GT — external comparator. |
| **QASPER** (arXiv papers with QA pairs) | QA pairs with reference answers | Downstream RAG demonstration (§6.2) | Answer quality depends on many upstream factors besides extraction | G2 for answer-quality adjudication |
| **TAT-DQA** (financial tables with QA pairs) | QA pairs on tabular content | Downstream RAG demonstration for table-heavy content | Table-focused; not representative of prose | G2 for answer-quality adjudication |

**REVIEW DECISION 2.2.a:** Include RealDocBench (arXiv 2606.07401) as a fifth ground-truth corpus for regulated-doc field-level QA? Adds ~200 documents in insurance/finance/gov domains with field-level annotations. **Alternatives:** (i) include as G1 for field-level content presence; (ii) defer to V2 because setup is nontrivial; (iii) include only in the development set to guide detector calibration. **Recommendation:** (ii) defer to V2 to keep V1 focused; V1 already has G1 coverage from PMC-OA (textual) and DocLayNet (structural) and CUAD (span).

### 2.3 Ground-truth × detector matrix

This matrix is the single most important artifact of §2. It specifies **for every detector, which corpora can supply which strength of evidence.** No detector may be evaluated against a corpus row where no cell is marked.

| Detector | PMC-OA (G1 text) | DocLayNet (G1 layout) | CUAD (G1 span) | Federal Register / SEC (G2 clean) | QASPER / TAT-DQA (G2 downstream) |
|---|---|---|---|---|---|
| W_DROPPED_CONTENT | **G1** — word-set overlap vs XML | — | G1 — clause-presence check | G2 — FPR baseline (must not fire) | G2 — indirect via answer failure |
| W_GIBBERISH | G2 — human review of any fires | — | — | **G2 — FPR baseline** | — |
| W_PLACEHOLDER_STUB | G2 — human review of any fires | — | G2 — placeholder in place of clause | **G2 — FPR baseline** | — |
| W_ENCODING_ARTIFACTS | G2 — human review; can also cross-check mojibake byte patterns which are directly observable | — | — | G2 — FPR baseline | — |
| W_TABLE_MISSING | — | **G1 — table bbox present but no MD table** | — | G2 — FPR baseline | — |
| W_MULTICOLUMN_ORDER (existing) | — | **G1 — reading-order via bbox sequence** | — | G2 — FPR baseline | — |
| W_HEADER_FOOTER_TABLE_GARBLED (existing) | — | G1 — table near page furniture | — | G2 — FPR baseline | — |
| Aggregate score | — | — | — | — | G2 — adjudicated severity + downstream correlation |

**Reads:** rows are detectors; columns are corpora. Cells state the strongest evidence available. Empty cells mean the corpus cannot adjudicate that detector.

### 2.4 Ground-truth mapping caveats

Three specific caveats must be documented in any results derived from this matrix:

1. **PMC-OA XML text is authoritative for textual content, NOT for layout or rendering.** The comparison metric must be word-set (or n-gram-set) overlap, not character-perfect equality. Different valid representations of the same text (e.g., hyphenation, whitespace normalization, section reordering) do not count as omissions.
2. **DocLayNet bboxes are page-anchored.** A parser that produces valid content but attaches it to a different page cannot be adjudicated as "correct" via DocLayNet alone. Combine with PMC-OA style word-set truth where the same document appears in both.
3. **FPR-baseline corpora must be *demonstrably clean* to the extent feasible.** Federal Register PDFs are natively authored, but if a detector fires on a Federal Register document, the finding must be human-adjudicated before it is counted as an FPR event. It might be a genuine detection.

---

## 3. Failure taxonomy and labeling

### 3.1 Failure classes (detector-independent)

Four labels, defined **before** any parser is run. Labels are the ground-truth annotation for each `(document, parser)` pair.

| Label | Meaning | Concrete rule |
|---|---|---|
| **GOOD** | The extraction is fit for downstream use without human review. | ≥ 95% of source words preserved (per G1 evidence where available); no placeholder/stub markers in body; no reviewer-flagged corruptions; RAG answers derivable from output. |
| **MINOR** | The extraction has known issues but is largely usable. Downstream user can work around them. | 85–95% of source words preserved; minor tables missing; minor page-furniture bleed; RAG answers derivable with modest degradation. |
| **MAJOR** | The extraction is significantly compromised. Downstream user will notice the impact. | 50–85% of source words preserved; substantial tables or sections lost; noticeable gibberish or stubs. |
| **CATASTROPHIC** | The extraction is functionally broken. Downstream RAG or LLM ingestion will produce wrong or misleading answers. | < 50% of source words preserved (matches W_DROPPED_CONTENT threshold at cap 69); OR extensive placeholder stubs in body; OR whole sections replaced with junk. |

**REVIEW DECISION 3.1.a:** four-level scheme (GOOD/MINOR/MAJOR/CATASTROPHIC) vs. three-level (GOOD/DEGRADED/BROKEN) vs. five-level with an OK band between MINOR and MAJOR. **Alternatives** cited in the Meurs-DeFilippis IAA literature; four labels are the most common in extraction-QA IAA studies. **Recommendation:** four levels as above. Reasoning: matches the RISKY/OK/HIGH bands already used internally in AksharaMD, so results are directly interpretable against existing cap thresholds; three levels loses cap-84-vs-cap-69 distinguishability; five levels multiplies reviewer disagreement without adding actionable resolution.

### 3.2 Labeling rubric

Reviewers assign the label per `(document, parser)` pair using the following procedure:

1. Reviewer sees the source PDF page thumbnails on one side and the parser's markdown output on the other. Reviewer does NOT see AksharaMD's diagnostics, score, or any warning codes. Blinded to detector output.
2. Reviewer answers three prewritten questions:
   - Q1 — **Coverage:** Does the markdown appear to contain substantially all of the source's textual content? (yes / mostly / partially / no)
   - Q2 — **Fidelity:** Where content is present, is it recognizably faithful to the source, or are there stubs, gibberish, or corruptions? (faithful / minor issues / significant corruption / mostly stub or junk)
   - Q3 — **Downstream usability:** If this markdown were the sole input to a RAG query system for this document, would answers to typical questions be usable, degraded, or wrong? (usable / usable-with-caveats / degraded / wrong)
3. Label is assigned deterministically from the (Q1, Q2, Q3) tuple using a published mapping table (Appendix B).

The deterministic mapping table is a critical protocol artifact: without it, reviewer judgment is elastic and the failure taxonomy loses meaning.

### 3.3 Reviewer selection, training, and inter-annotator agreement

- **Two independent reviewers** per document-parser pair. Reviewers work from separate machines, not co-located, and do not discuss labels before submission.
- **Tie-breaker rule:** If the two labels differ by ≤ 1 level (e.g., MINOR vs. MAJOR), take the lower-quality label conservatively. If they differ by ≥ 2 levels, a third adjudicator resolves.
- **Training:** Each reviewer completes a 20-example training set (drawn from the development corpus, NOT the held-out corpus) and must achieve ≥ 80% agreement with a gold-standard label set before scoring counts.
- **Inter-annotator agreement (IAA) target:** Cohen's κ ≥ 0.7 across the pilot set. If IAA falls below 0.7, the rubric must be refined and reviewers retrained before the full held-out run.
- **REVIEW DECISION 3.3.a:** Who are the reviewers? **Alternatives:** (i) protocol authors as V1 reviewers, with a public statement about the conflict of interest; (ii) recruited external reviewers via a domain-appropriate crowd platform; (iii) hybrid — protocol authors do development-set labeling, external reviewers do held-out labeling. **Recommendation:** (iii) hybrid. Protocol authors label the ~20-doc pilot to refine the rubric; external reviewers label the held-out set to remove the conflict of interest. Requires small budget (~$500-1500) for external labeling; commit up front.

### 3.4 Blinding and order randomization

- Reviewers do not see the parser identity when labeling. Files are presented under opaque hashes.
- Reviewers do not see AksharaMD's output.
- Document order is randomized per reviewer to avoid position bias.
- Each `(document, parser)` pair is a separate labeling task; no cross-parser comparison happens during labeling.

---

## 4. Detector evaluation methodology

Each detector is evaluated according to the strongest tier of evidence available for it (from §2.3). The claim that can be made from the evaluation is bounded by that tier: **G1 → detector validity (oracle-grounded); G2 → detector validity (criterion-validated); G3 → reproducibility + G2 criterion validity only.**

### 4.1 W_DROPPED_CONTENT — G1 oracle-grounded

- **Method:** For each `(document, parser)` in PMC-OA and CUAD subsets, compute the ground-truth word-set from the XML/annotation. Compute word-set overlap for the parser's markdown output. Assign the `W_DROPPED_CONTENT` label independently by AksharaMD (fires if ratio < 0.5).
- **Metrics:** Precision, recall, F1 against the ground-truth "catastrophic" label (source-words-lost > 30% per PMC-OA XML).
- **Predeclared:** recall ≥ 0.70 on catastrophic-omission cases (rationale §12.1).

### 4.2 W_GIBBERISH — G2 human-adjudicated

- **Method:** Every document-parser where `W_GIBBERISH` fires goes to human adjudication. Reviewers see the flagged span in context and answer a prewritten question about whether the region contains parser-generated garbage that impairs use.
- **Also measure FPR** on the clean-native corpus (Federal Register / SEC): count firings per 100 clean documents.
- **Metrics:** Precision (agreement rate on flagged cases). FPR on clean corpus. No independent recall claim — G2 does not support one because reviewers cannot easily search for gibberish that AksharaMD missed.
- **Predeclared:** precision ≥ 0.80 on flagged cases; FPR ≤ 0.05 on clean-native corpus (rationale §12.2).

### 4.3 W_PLACEHOLDER_STUB — G2 human-adjudicated (some G3 caveats)

- **Method:** Same as W_GIBBERISH. Additionally, adjudicators answer a follow-up question: *"Does this flag correspond to a parser-generated substitute for source content, and does that substitution materially impair use?"* This is stronger than "the regex matched" (G3) because it involves an independent judgment.
- **Metrics:** Precision on flagged cases. FPR on clean-native corpus. G3 caveat documented in the write-up: the definition of "placeholder stub" is partially detector-defined; independent adjudication measures whether the detector's definition matches human intuition, not whether the definition is objectively correct.
- **Predeclared:** precision ≥ 0.80 on flagged cases where adjudicators answer "yes" to the follow-up (rationale §12.2).

### 4.4 W_ENCODING_ARTIFACTS — G2 with observable byte-pattern cross-check

- **Method:** Same as W_GIBBERISH. Plus a directly observable cross-check: replacement characters (U+FFFD) and known mojibake byte patterns are counted mechanically. Detector firings are separated into (a) fires with observable mojibake presence and (b) fires without.
- Category (a) is G1-adjacent — the byte patterns are objectively present or not. Category (b) requires G2 adjudication.
- **Metrics:** Category-(a) precision (near-perfect expected). Category-(b) adjudicated precision. FPR on clean-native corpus.

### 4.5 W_TABLE_MISSING, W_MULTICOLUMN_ORDER, W_HEADER_FOOTER_TABLE_GARBLED (existing detectors) — G1 layout-oracle

- **Method:** Use DocLayNet's layout bboxes as the ground truth. For each firing, check whether the corresponding page has a table/multicolumn/header-footer region per DocLayNet, and whether the markdown captures it.
- These are **existing** detectors carried into the study. Their inclusion is important because (a) score-validity depends on all detectors, not just the new ones, and (b) evaluating only the new detectors would be a coverage lie.
- **Predeclared thresholds:** the existing published maturity ratings (candidate / experimental) are the current claim; the V1 study either confirms them or motivates a downgrade.

### 4.6 What is deliberately NOT evaluated in V1

- **W_DETECTOR_TIMEOUT** — measurement-and-warn only; no product claim depends on it. Documented as such.
- **Informational rules** (AUTO_OCR_BACKEND_SELECTED, W_PDF_ATTACHMENT_IGNORED, etc.) — no penalty, no claim, out of scope.
- **The bundled reference parser as a product** — the study compares parsers to establish AksharaMD's independence from parser choice, not to rank the reference parser against competitors. Any per-parser ranking is a downstream artifact, not the study's target.

---

## 5. Score validity

Score validity is measured on the calibration corpus **only** — the held-out corpus is used exclusively for detector-validity and product-utility measurements after the freeze (§10). Otherwise the held-out data would inform the score-calibration process and the frozen thresholds would be over-fit.

### 5.1 Monotonicity and severity correlation

- **Method:** For each `(document, parser)` in the calibration set, obtain the human severity label (§3) and the AksharaMD score. Compute Spearman rank correlation.
- **Also compute** correlation between `structural_score` and structure-related severity aspects (from Q1 in §3.2); and between `content_score` and content-related severity aspects (Q2 in §3.2). Two-axis-receipt validity depends on this decomposition making sense.
- **Predeclared:** overall Spearman ≥ 0.5; per-axis Spearman ≥ 0.4 on the corpus subset where the axis is applicable (rationale §12.3).

### 5.2 Cap-value validation

- **Method:** Sort calibration `(document, parser)` pairs by score. For each cap value (currently 69, 84), compute the distribution of adjudicated severity labels for documents scoring just below vs. just above the cap.
- **Predeclared:** for each cap value, the severity distribution above and below the cap must be distinguishable at Mann–Whitney U p < 0.05 with ≥ 20 documents in each side of the boundary. If not, the cap value is a candidate for revision in V2.
- **Predeclared:** ≥ 30% of documents just below a cap should NOT be severity-indistinguishable from those just above (rationale §12.4). Failure here means the cap does not correspond to a meaningful quality step.

### 5.3 Cap-attribution honesty

- Score-capped deductions (`IMAGE_PLACEHOLDER_NO_FALLBACK`, `W_*` cap rules) contribute their realized penalty to the axis score in the current implementation. This is documented as a known imprecision in `aksharamd/scoring/models.py` and PR #159.
- **The V1 study measures the current implementation as shipped.** If cap-attribution imprecision degrades score validity below the §5.1 threshold, that becomes evidence for a V2 refinement — not a reason to change the implementation mid-study.

---

## 6. Product utility experiments

Product utility is the L3 claim — the most important one for the product story. Two independent experiments:

### 6.1 Bidirectional disagreement analysis

- **Method:** For every `(document, parser)` in the held-out set, compute (a) AksharaMD's verdict (BAD if score < 70 or any capping W_ fires; else GOOD) and (b) a conventional-extraction-metric verdict (per-corpus: PMC-OA word-overlap for PMC-OA, TEDS for DocLayNet tables, per ParseBench's own rubric for ParseBench pages).
- Populate the 2×2:

| | AksharaMD GOOD | AksharaMD BAD |
|---|---|---|
| **Conventional GOOD** | agreement (uninteresting) | **candidate silent failure** |
| **Conventional BAD** | **candidate benchmark false alarm / valid alternate representation** | agreement |

- **The disagreement cells** are the study's main product-utility evidence. They receive full human adjudication (§3), independently of the labels used for detector validity, with the specific question: *"Was AksharaMD correct about this document, was the conventional metric correct, or is this a case where neither framing captures the reality?"*
- **Predeclared:** the two novel-information cells together must exceed 15% of the total held-out corpus AND adjudicators must agree AksharaMD provides genuine useful additional information (not merely disagreement) in ≥ 50% of those cases (rationale §12.5).

### 6.2 Downstream RAG demonstration

**Distinct** from §6.1. This experiment addresses whether AksharaMD's extraction-layer signals *explain* downstream RAG failures. RAGAS is used as the downstream evaluator here, not as ground truth for the extraction layer.

- **Method:** Take QASPER + TAT-DQA (both have QA pairs). Parse each with each of the four V1 parsers (§7). For each `(document, parser)`, run RAGAS answer-quality evaluation via a fixed pipeline (retriever, reader, judge). Also compute AksharaMD score for each `(document, parser)`.
- **Correlate:** does AksharaMD's score predict the *delta* in RAGAS answer quality across parsers on the same document?
- **Placebo control:** compare against a random flag (same firing rate, uniformly distributed). If the placebo explains RAG failures equally well, AksharaMD's signal is not informative.
- **Predeclared:** Spearman correlation between AksharaMD score and RAGAS answer-quality delta ≥ 0.3, AND at least 2× the correlation of the placebo control (rationale §12.7).

### 6.3 Localization quality

- **Method:** For every fired W_ rule on the held-out set, take the diagnostic (page, region, snippet) it produces. Reviewers see the flag + diagnostic + source PDF and answer: *"Can you map this flag to a specific offending region in the source within 60 seconds of effort?"*
- **Predeclared:** ≥ 70% of flags produce actionable localization by this measure (rationale §12.6).

---

## 7. Parser slate (V1)

Four parsers. Version-pinned in the frozen `benchmark-v1-frozen` tag.

| Parser ID | Package | Family | Runtime characteristics |
|---|---|---|---|
| `reference` | Bundled PyMuPDF-based (aksharamd) | Direct PDF text-layer extraction | Fast, CPU-only |
| `marker` | `marker-pdf` (extras=vision) | VLM + layout model | GPU-preferred; ~1-3 min/doc on GPU, longer on CPU |
| `docling` | `docling` | VLM + layout model | Heavy dependencies; some known memory issues on large PDFs |
| `markitdown` | `markitdown` (Microsoft) | Broad-format converter | Fast, CPU-only |

**REVIEW DECISION 7.a:** Add `mineru` and/or `unlimited_ocr` (bundled OCR backend) to V1? **Alternatives:** (i) add both — three OCR-heavy parsers plus reference gives strongest architectural coverage but multiplies compute; (ii) defer both to V2 — V1 stays with the four above; (iii) add `mineru` only. **Recommendation:** (ii) defer both. Rationale: `mineru` requires 14 GB VRAM and complex install; `unlimited_ocr` is architecturally similar; V1's four parsers already span three distinct families (direct, VLM+layout ×2, broad-format converter), which is sufficient for the L3 disagreement claim.

**REVIEW DECISION 7.b:** Cloud parsers (`LlamaParse`, `Reducto`) in V1? **Alternatives:** (i) yes — the story is stronger if commercial parsers are in the set; (ii) no — cost, API-key management, and revocable-service risk (a parser could become unavailable during the run) make the study less reproducible. **Recommendation:** (ii) no in V1. If we ever add commercial parsers, the study needs a separate ethics + reproducibility clause; not V1 scope.

### 7.1 Parser adapter contract

- Each parser is invoked via an `aksharamd.parser_contract.ParserAdapter` implementation.
- Adapters live under `benchmarks/parser_adapters/` (existing pattern from prior work).
- Each adapter must: (a) record its version to the manifest; (b) record its runtime; (c) fail deterministically (no partial output) on error.

### 7.2 Runtime and cost measurement

- Wall-clock time recorded per `(document, parser)`.
- Peak RSS memory recorded via psutil.
- GPU time (if applicable) recorded per invocation.
- Total cost is presented per parser as (wall-clock time × hourly rate for the required hardware tier). Hourly rates published in the results, not baked into judgments.

---

## 8. Corpus partitioning

### 8.1 Development / calibration / held-out split

- **Development** (~10% of total): initial infrastructure smoke, adapter debugging, protocol iteration.
- **Calibration** (~20% of total): score-validity measurements (§5); rubric refinement (§3.3); IAA-training set.
- **Held-out** (~70% of total): detector-validity (§4), product utility (§6). **Never used to tune anything.**

### 8.2 Selection procedure

- Documents are drawn from each corpus using deterministic hashing (SHA-256 of document ID mod 100) to assign to dev/cal/held-out. This is documented per corpus so the split is reproducible.
- Within each split, stratification: at least 25% of each split from each candidate corpus (PMC-OA, DocLayNet, Federal Register/SEC, CUAD).
- The dev/cal/held-out assignments are published in `docs/evaluation/CORPUS_MANIFEST.md` at freeze time and are immutable thereafter.

### 8.3 Sample size — held-out target

**REVIEW DECISION 8.3.a:** Total held-out corpus size. **Alternatives:** (i) ~100 docs (fast, tight statistics on aggregate but noisy per-cell); (ii) ~200 docs (balances tightness and coverage); (iii) ~400 docs (best per-cell power but doubles labeling cost and compute). **Recommendation:** (ii) ~200 docs at ~50 per candidate corpus. Rationale: for the disagreement 2×2 to have useful power in the "silent failure" cell, we need at least ~20 documents there; assuming disagreement occurs on ~20% of documents and the silent-failure cell is ~half of that, ~200 documents gives ~20 silent-failure cases per parser. See §11 for the worked calculation.

### 8.4 Failure-event count vs document count

Document count is NOT the correct statistical estimand for detector recall. Detector recall requires *failure-event count*: the number of documents in the corpus where the failure class actually occurred.

**A corpus of 200 pristine PDFs would tell us essentially nothing about catastrophic-failure recall.** The corpus mix must include documents where each failure class is expected to occur, at rates sufficient to compute per-detector recall with reasonable confidence intervals.

**REVIEW DECISION 8.4.a:** How to ensure per-class event counts? **Alternatives:** (i) purposive sampling — deliberately include corpora / parsers known to produce failures (e.g., docling on very large PDFs; markitdown on complex tables); (ii) enrichment — start with a random sample and add known-failure cases from prior benchmarks; (iii) accept whatever occurs naturally. **Recommendation:** (ii) enrichment. Add a known-failure supplement drawn from prior AksharaMD-flagged cases (transparently disclosed in results) to guarantee each detector has ≥ 15 events for recall estimation. This is enrichment sampling, not selection bias, provided it is disclosed and the aggregate rate is not claimed as representative.

---

## 9. Staged execution

### 9.1 Sequence

The freeze mechanics (§10) apply only after the pilot completes. Before that, iteration is expected and encouraged.

```
    Protocol design (this document — DRAFT V1)
          ↓
    3-document infrastructure smoke test
    (verify each adapter runs, each detector returns output,
     each ground-truth pipeline produces expected labels)
          ↓
    ~20-document methodological pilot
    (calibrate rubric; measure IAA; confirm metrics behave;
     find pathological runtime, ground-truth ambiguity,
     normalization bugs)
          ↓
    Fix protocol / implementation issues
    (may include: refining §3 rubric; adjusting §4 methods;
     amending §5 calibration procedure; adding exclusion rules)
          ↓
    ════════════════════════════════════════════
    FREEZE — see §10 for operational specifics
    ════════════════════════════════════════════
          ↓
    Full held-out evaluation corpus run
    (no detector tuning, no rubric changes, no metric changes)
          ↓
    Analysis + results
          ↓
    Leaderboard + whitepaper + README rewrite
    (post-hoc claims must trace to §1.3 matrix and §12 criteria)
```

### 9.2 Pilot exit criteria

The pilot completes and the freeze proceeds only when:

- All parser adapters run to completion on the pilot set.
- Every corpus's ground-truth pipeline produces expected outputs (spot-checked by author).
- Reviewer IAA on the pilot's labels reaches Cohen's κ ≥ 0.7.
- No unresolved REVIEW DECISION remains unaddressed.
- The pre-registered protocol amendments log records every change made during the pilot phase.

If any pilot exit criterion fails, do NOT proceed to freeze. Document the failure, extend the pilot, and re-attempt exit.

---

## 10. Freeze mechanics (operational)

The freeze is the single most important methodological guardrail in this protocol. If the freeze fails operationally, the study reduces to a development-set report and cannot support any L1-L3 claim.

### 10.1 What gets frozen

At freeze time, the following are pinned:

1. **Git tag** — `benchmark-v1-frozen` — created on develop, points at a specific commit.
2. **AksharaMD implementation** — all detectors, thresholds, scoring policy, cap values, priority ordering, catalog contents.
3. **Ground-truth pipelines** — code for extracting XML text, DocLayNet bboxes, CUAD spans.
4. **Labels** — the deterministic (Q1, Q2, Q3) → label mapping table (Appendix B).
5. **Corpus assignments** — dev/cal/held-out per document, published in `CORPUS_MANIFEST.md`.
6. **Parser versions** — exact package versions for each adapter, published in `PROTOCOL_V1.md` §7.
7. **Metric definitions** — every computation formula, published in Appendix C.
8. **Predeclared criteria** — every number and rationale in §12.

### 10.2 Execution rules during held-out run

- The held-out run must be executed from a checkout of the `benchmark-v1-frozen` tag. No local modifications.
- If any change to AksharaMD is made during held-out execution, the current run is invalidated. A full rerun is required.
- Cherry-picking of documents or per-document tweaks is prohibited. Every document in the held-out set contributes.
- Runtime failures (adapter crash, timeout, resource exhaustion) are recorded as failures — the document is not silently dropped. Exclusion criteria (§14) are pre-declared.

### 10.3 Post-freeze observation log

- A file at `docs/evaluation/POST_FREEZE_OBSERVATIONS.md` is opened at freeze and appended to during held-out execution.
- Every unexpected observation goes into this log: crashes, memory blow-ups, weird detector fires, reviewer disagreements, cases the protocol did not anticipate.
- Observations do NOT trigger detector changes during the held-out run. They are candidates for the V2 protocol amendment (§10.4).

### 10.4 Amendment procedure

- Post-freeze, any change to the protocol or implementation becomes a **published amendment** at `docs/evaluation/AMENDMENTS.md`.
- Amendments cannot alter the interpretation of already-run held-out data. They apply only to future runs (V2 or later).
- Every amendment includes: (a) the observation that motivated it, (b) the specific change, (c) which prior claim (if any) is affected, (d) whether the affected claim requires a re-run to remain valid.

---

## 11. Statistical analysis plan

### 11.1 Estimands

The primary estimands are:

- **Detector precision** = TP / (TP + FP) per detector, per corpus.
- **Detector recall** = TP / (TP + FN) per detector, per corpus, on documents where the failure class occurred.
- **Detector FPR** = FP / (FP + TN) per detector, on documents where the failure class did not occur (clean corpus).
- **Score/severity Spearman ρ** overall and per axis.
- **Cap-value distinguishability** — Mann–Whitney U on severity labels above vs. below each cap.
- **Disagreement rate** — proportion of `(document, parser)` in each 2×2 cell.
- **Downstream RAGAS correlation** — Spearman ρ between AksharaMD score and RAGAS answer-quality delta.
- **Localization actionability rate** — proportion of flags reviewers can map to a specific region within 60s.

### 11.2 Confidence intervals and uncertainty

- Bootstrap 95% CIs (10 000 resamples) for every point estimate.
- Report `n` for every ratio; do not report a bare percentage without denominator.
- No p-values as pass/fail thresholds. Use CI-based interpretation.

### 11.3 Multiple comparisons

- Where multiple detectors × multiple corpora × multiple thresholds are compared, apply Benjamini-Hochberg correction for any exploratory findings.
- Predeclared criteria (§12) are NOT corrected — they are single pre-registered tests each.

### 11.4 Sample size rationale (worked example — silent-failure disagreement)

For the L3 "silent-failure" claim to be defensible, the (Conv=GOOD / AksharaMD=BAD) cell must be non-trivial in size.

- Assume disagreement occurs on ~20% of `(document, parser)` pairs (informed by Plan E pilot data: parsers score in different bands on the same documents).
- Assume the silent-failure cell is ~half of the disagreement (~10%). The other half is the benchmark-false-alarm cell.
- To estimate the adjudicated-genuine rate in the silent-failure cell with ±0.15 CI half-width at 95%, we need ≥ ~40 cases per parser in that cell.
- 40 cases / 10% cell rate → ~400 (document × parser) pairs per parser.
- 4 parsers × ~100 documents = 400 pairs per parser (assuming each document runs through each parser). Suggests ~100 held-out documents. Doubling to ~200 provides tighter per-cell CIs and buffer for exclusions.

**Recommendation:** ~200 held-out documents. Documented as ± ~0.10 CI half-width on the primary disagreement estimand. Adjust for V2 based on observed cell rates.

---

## 12. Predeclared success / failure criteria (with rationales)

Every numerical threshold in this section is a **product-design judgment**. It is not derived from an empirical prior. The rationale for each threshold states the reasoning; it does not disguise the judgment as fact.

### 12.1 W_DROPPED_CONTENT recall on catastrophic omissions

**Threshold:** recall ≥ 0.70 on documents where G1 evidence shows > 30% source-word loss.

**Rationale:** ≥ 0.70 was selected as the minimum product-utility threshold because missing more than 3 of every 10 independently established catastrophic omissions would make the general "silent-failure detector" claim too broad for V1. A user relying on the score to route documents for review would encounter a materially uncorrected silent failure at least 30% of the time, which contradicts the framing. Below 0.70, the claim must be narrowed (e.g., "silent-failure detector for a subset of dropped-content failures") or the detector must be improved and re-evaluated.

### 12.2 FPR ≤ 0.05 on clean-native corpus (three new content detectors combined)

**Threshold:** across W_PLACEHOLDER_STUB + W_GIBBERISH + W_ENCODING_ARTIFACTS combined, no more than 5 firings per 100 documents on the Federal Register / SEC clean corpus.

**Rationale:** for the detector to be usable as a routing signal, false-positive rate on clean documents must be low enough that operators can trust a firing as meaningful. 0.05 is a product-design judgment: below this rate, an operator can act on every firing; above this rate, the tool trains operators to ignore its own signals. Any FPR observation ≥ 0.05 requires an audit before the "low-noise" claim is made.

### 12.3 Overall score/severity Spearman ρ ≥ 0.5

**Threshold:** on the calibration set, Spearman rank correlation between AksharaMD score and human severity label ≥ 0.5.

**Rationale:** 0.5 is a moderate-effect threshold. Below 0.5, the score cannot be meaningfully compared across documents (a score of 60 vs. 85 would carry little information). Above 0.5, users can reasonably interpret score deltas as directionally accurate. This is a product-design judgment about the minimum ordinal utility a "quality score" must have to be worth exposing.

### 12.4 Cap-value distinguishability

**Threshold:** ≥ 30% of documents scoring just below a cap must have severity distinguishable from those scoring just above.

**Rationale:** if the two populations are indistinguishable, the cap does not correspond to a meaningful quality step and the risk-band framing (HIGH / OK / RISKY) is arbitrary. 30% is a modest expected-difference threshold; below it, the cap boundary is a coincidence, not a signal.

### 12.5 Disagreement quadrant informativeness

**Threshold:** the two novel-information cells together must exceed 15% of the held-out corpus AND adjudicators must judge AksharaMD as providing genuinely useful additional information in ≥ 50% of those cases.

**Rationale:** if disagreement is < 15% of pairs, AksharaMD's signal is duplicative of conventional metrics — no L3 claim is supportable. If the useful-information rate in the disagreement cells is < 50%, AksharaMD is disagreeing without adding value — adjudicators would say the conventional metric was correct at least as often. Both conditions must hold; either alone is insufficient.

### 12.6 Localization actionability

**Threshold:** ≥ 70% of flags produce reviewer-actionable localization within 60 seconds of effort.

**Rationale:** the localization claim is the concrete difference between AksharaMD's diagnostic and a bare score. Below 70%, users cannot act on the flags reliably; the diagnostic becomes decorative. 60s is the effort ceiling below which localization is genuinely faster than a manual re-parse — the operational alternative.

### 12.7 Downstream RAG correlation

**Threshold:** Spearman correlation between AksharaMD score and RAGAS answer-quality delta ≥ 0.3 AND at least 2× the placebo control correlation.

**Rationale:** ≥ 0.3 is a modest positive correlation; below it, the extraction-layer signal is not usefully predictive of downstream failure. The 2× placebo comparison rules out the "any signal correlates a little" objection. Together, they support the "helps explain downstream RAG failure" L3 claim without overstating.

### 12.8 The extraction-auditing-layer claim (L3 → L4 boundary)

**Threshold:** all L3 items met AND at least two independent reviewers agree that RAGAS post-hoc + spot-check does not provide equivalent operational value.

**Rationale:** even if AksharaMD passes every quantitative bar, the strong "auditing layer nobody else provides" claim requires an independent judgment that the operational alternative is worse. If a reviewer independently concludes RAGAS-only is equally usable, the claim reduces to "one available tool among several" rather than "the missing layer."

---

## 13. Reproducibility requirements

The study is reproducible when a competent external reviewer can, from this protocol plus the frozen commit, execute the study and arrive at the same numerical results within stated CIs.

### 13.1 Data

- Corpus manifests published as JSON files with document IDs, source URLs, SHA-256 hashes, split assignment, and any acquisition metadata.
- Where copyright-restricted (e.g., some SEC PDFs are freely available; some CUAD contracts require registration), acquisition instructions are documented, not the data itself.
- PMC-OA subset selection published as XML citation list.

### 13.2 Code

- The `benchmark-v1-frozen` tag is the reproducible artifact.
- Analysis scripts (metric computation, plot generation) published under `benchmarks/eval_v1/` with a `README.md` including entry points.
- Random seeds for any stochastic step (bootstrap CI, document ordering) published in the config.

### 13.3 Compute environment

- Hardware requirements published per parser (e.g., "marker on 24 GB VRAM").
- Software environment via `uv.lock` or equivalent pinned lockfile.
- Container images (if used) tagged with the frozen commit's tag.

### 13.4 Reviewer materials

- The (Q1, Q2, Q3) → label mapping table (Appendix B).
- Reviewer training set (drawn from calibration corpus) published.
- Reviewer per-document rubric published.

### 13.5 Analysis reproducibility

- Every reported number must trace to a script that produces it from the raw run outputs.
- Every figure must trace to a plotting script.
- No hand-tweaked plots. No "adjusted" numbers.

---

## 14. Exclusion criteria (pre-declared)

Documents are excluded from the held-out results only under the following pre-declared criteria. Post-hoc exclusion is prohibited.

- Corrupt PDF (fails to open in PyMuPDF).
- Document exceeds the AksharaMD per-file size cap (500 MB default).
- Parser adapter reports a deterministic failure (e.g., timeout, out-of-memory) that reproduces on a second run — recorded as a parser-side failure, not an AksharaMD claim.
- Documents identified during ground-truth pipeline validation as having ambiguous or missing G1 evidence (e.g., PMC-OA article where XML full text is truncated).

Excluded documents are enumerated in the results with the exclusion reason. Exclusion rate is reported as a metric — a high exclusion rate on the held-out corpus is itself a study limitation.

---

## 15. What the study can and cannot claim afterward

Depending on which predeclared criteria are met, the following table maps outcomes to publishable claim language.

| L3 disagreement met? | L3 downstream met? | L2 score met? | L1 detectors met? | Language earned |
|---|---|---|---|---|
| ✓ | ✓ | ✓ | ✓ | "AksharaMD is an independent extraction-auditing layer that (i) validates dropped-content signals against ground truth, (ii) tracks extraction severity, and (iii) surfaces failures conventional aggregate metrics miss and explains downstream RAG failures conventional metrics cannot localize." |
| ✓ | ✗ | ✓ | ✓ | "AksharaMD is an independent extraction-auditor that identifies silent failures conventional metrics miss; V1 does not establish whether these failures explain downstream RAG behavior." |
| ✗ | ✗ | ✓ | ✓ | "AksharaMD's detectors and score are validated on the held-out corpus, but V1 does not establish product utility beyond conventional extraction metrics." |
| ✗ | ✗ | ✗ | Some ✓ | Only specific validated detectors may be claimed. General "readiness score" framing must be dropped. |
| Multiple failures | | | | Report negative findings honestly. V1 findings direct V2 protocol design; no L3 or higher public claim is made. |

**A study earning L1 detector validity without L3 product utility is still a success.** It tells us the detectors work but the framing is too broad. That is a legitimate scientific finding and directs the roadmap.

**A study where the "silent-failure detector" framing is not earned publishes a paper titled honestly** — for instance, *"An independent extraction-quality signal for PDF-derived RAG pipelines: V1 validation of a per-document scoring approach."* The framing is scaled to the evidence, not the other way around.

---

## Appendices

### Appendix A — Corpus inventory detail

*[To be produced during Milestone 1 pilot. Placeholder: per-corpus documents, splits, acquisition instructions, hashes.]*

### Appendix B — (Q1, Q2, Q3) → label mapping table

*[Deterministic mapping; must be drafted and reviewer-tested during the pilot phase. Placeholder shape:]*

| Q1 (Coverage) | Q2 (Fidelity) | Q3 (Usability) | Label |
|---|---|---|---|
| yes | faithful | usable | GOOD |
| mostly | faithful | usable-with-caveats | MINOR |
| partially | minor issues | degraded | MAJOR |
| no | mostly stub or junk | wrong | CATASTROPHIC |
| *[all remaining combinations enumerated during pilot]* | | | |

### Appendix C — Metric formulas

*[To be produced during pilot. All formulas for precision, recall, FPR, Spearman ρ, Mann–Whitney U, bootstrap CI, per-corpus word-overlap, TEDS as adapted for our purposes.]*

### Appendix D — Sample-size worked calculations

*[Extended version of §11.4 including per-detector recall-power calculations and per-cell disagreement power.]*

### Appendix E — Reproducibility checklist

*[Line-item checklist an external reviewer signs off on before results are published. Standard categories: data, code, environment, analysis, review materials.]*

### Appendix F — Reviewer training materials draft

*[To be produced during pilot. 20 pilot documents with gold-standard labels for reviewer calibration.]*

### Appendix G — REVIEW DECISION queue

The following decisions in this document remain unresolved and require answers before Milestone 1 pilot can begin.

| # | Section | Decision |
|---|---|---|
| 1 | §2.2 | Include RealDocBench? (recommended: defer to V2) |
| 2 | §3.1 | Failure taxonomy level count (recommended: 4-level as documented) |
| 3 | §3.3 | Reviewer sourcing (recommended: hybrid — authors pilot, externals held-out) |
| 4 | §7 | V1 parser slate size (recommended: 4 as documented, defer mineru / unlimited_ocr / cloud) |
| 5 | §7.b | Cloud parsers in V1? (recommended: no) |
| 6 | §8.3 | Total held-out corpus size (recommended: ~200) |
| 7 | §8.4 | Failure-event enrichment strategy (recommended: enrichment with disclosure) |

---

## Signature block

**Protocol version:** V1 DRAFT
**Frozen implementation tag (post-pilot):** *`benchmark-v1-frozen` — TBD at freeze time*
**Approved by:** *[project owner]*
**Reviewer sign-off:** *[external reviewer, at freeze time]*

**Author stance restated:**
> A successful study can produce conclusions like *"W_GIBBERISH contributes essentially nothing on real corpora,"* *"the cap-at-84 threshold is wrong,"* or *"the general 'silent-failure detector' framing is too broad for V1."* Any protocol that cannot state up front what would invalidate its claims has not designed a validation study.
