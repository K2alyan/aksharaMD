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

## 0.1 What merging this protocol does and does not authorize

Merging `PROTOCOL_V1.md` to `develop` is approval of **the methodology only**. It is not authorization to execute the held-out benchmark.

Downstream execution is **staged and each stage requires a separate authorization** against this frozen protocol:

1. **Authorization A — 3-document infrastructure smoke test.** Verifies every adapter runs, every ground-truth pipeline produces expected labels, every detector returns output. No results claim.
2. **Authorization B — ~20-document methodological pilot.** Calibrate the rubric, measure inter-annotator agreement, refine metrics, discover pathological runtime and normalization bugs. No V1 claims yet.
3. **Authorization C — freeze (§10).** Once pilot exit criteria (§9.2) are satisfied, freeze the study via the Study Freeze Manifest (§10.1). No further protocol changes without an amendment (§10.4).
4. **Authorization D — held-out execution.** Run the frozen protocol on the held-out corpus. Only after this stage is any L1-L3 claim in §1.2 supportable.

The next authorization to seek after merging this protocol is Authorization A only.

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
| **Score reflects extraction quality** (L2) | Held-out adjudicated severity labels vs score, per document (calibration set establishes/tests the relationship; held-out corpus independently verifies it under the frozen scoring rule) | Weak (Spearman < 0.5) or non-monotonic score/severity relationship on the **held-out** set (rationale §12.3) |
| **Cap thresholds correspond to meaningful quality steps** (L2) | Distribution of adjudicated severity labels above/below each cap value on the **held-out** set (calibration set may inform threshold selection pre-freeze; held-out is the validation) | ≥ 30% of documents scored just below a cap have severity indistinguishable from those just above **on the held-out set** (rationale §12.4). Failure here fails V1 — no post-hoc cap tuning is permitted. |
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
| **DocLayNet** (IBM layout annotations on ~80k pages) | Layout bboxes + block types (region-level) | Structural region localization: table-region presence + geometry, header/footer-region presence + geometry, block-category assignment | Layout truth ≠ text-content truth. Cannot adjudicate W_DROPPED_CONTENT directly. Reading order is NOT directly labelled (see §2.4 caveat 4). Does NOT supply table-cell structure — no rows/columns/headers — and therefore does NOT support TEDS (see §2.4 caveat 5). | G1 for what it directly annotates (see §2.4 caveats 4–5) |
| **Federal Register / SEC filings** (native-authored PDFs) | Source authorship known; typography clean | Baseline / FPR — the study's "no failure expected" corpus for the three new content-axis detectors | Text-authoritative comparison not always available; treat primarily as a false-positive-rate corpus. | G2 (via human adjudication of any flagged case) |
| **CUAD** (contract clause span annotations) | Character-offset spans for clause types | Localization; dropped-content within clause spans | Only annotated for legal contract clauses; not general-purpose | G1 for clause-level content presence |
| **ParseBench / RealDoc-Bench** (LlamaIndex, existing evaluation harnesses) | Rule-based scoring, ~2k pages, 167k rules | Cross-check against existing conventional metrics | Represents a *conventional* metric; useful for §6.1 disagreement analysis, not as ground truth. | Not GT — external comparator. |
| **QASPER** (arXiv papers with QA pairs) | QA pairs with reference answers | Downstream RAG demonstration (§6.2) | Answer quality depends on many upstream factors besides extraction | G2 for answer-quality adjudication |
| **TAT-DQA** (financial tables with QA pairs) | QA pairs on tabular content | Downstream RAG demonstration for table-heavy content | Table-focused; not representative of prose | G2 for answer-quality adjudication |

**RESOLVED (Decision 2.2.a):** RealDocBench (arXiv 2606.07401) is **deferred to V2**. V1 already has enough moving parts and G1 coverage from PMC-OA (textual) and DocLayNet (structural) and CUAD (span). RealDocBench is added later as external replication / generalization evidence.

### 2.3 Ground-truth × detector matrix

This matrix is the single most important artifact of §2. It specifies **for every detector, which corpora can supply which strength of evidence.** No detector may be evaluated against a corpus row where no cell is marked.

| Detector | PMC-OA (G1 text) | DocLayNet (G1 layout) | CUAD (G1 span) | Federal Register / SEC (G2 clean) | QASPER / TAT-DQA (G2 downstream) |
|---|---|---|---|---|---|
| W_DROPPED_CONTENT | **G1** — word-set overlap vs XML | — | G1 — clause-presence check | G2 — FPR baseline (must not fire) | G2 — indirect via answer failure |
| W_GIBBERISH | G2 — human review of any fires | — | — | **G2 — FPR baseline** | — |
| W_PLACEHOLDER_STUB | G2 — human review of any fires | — | G2 — placeholder in place of clause | **G2 — FPR baseline** | — |
| W_ENCODING_ARTIFACTS | G2 — human review; can also cross-check mojibake byte patterns which are directly observable | — | — | G2 — FPR baseline | — |
| W_TABLE_MISSING | — | **G1 — table-region bbox present but no MD table** (direct: table-region annotation) | — | G2 — FPR baseline | — |
| W_MULTICOLUMN_ORDER (existing) | — | Geometric proxy from block-bbox sequence (**not** direct G1: reading order is not labelled — see §2.4 caveat 4) | — | G2 — FPR baseline | — |
| W_HEADER_FOOTER_TABLE_GARBLED (existing) | — | Geometric proxy: table-region bbox adjacent to page-header / page-footer regions (**not** direct G1: "garbled" is a derived judgement) | — | G2 — FPR baseline | — |
| Aggregate score | — | — | — | — | G2 — adjudicated severity + downstream correlation |

**Reads:** rows are detectors; columns are corpora. Cells state the strongest evidence available. Empty cells mean the corpus cannot adjudicate that detector.

### 2.4 Ground-truth mapping caveats

Five specific caveats must be documented in any results derived from this matrix:

1. **PMC-OA XML text is authoritative for textual content, NOT for layout or rendering.** The comparison metric must be word-set (or n-gram-set) overlap, not character-perfect equality. Different valid representations of the same text (e.g., hyphenation, whitespace normalization, section reordering) do not count as omissions.
2. **DocLayNet bboxes are page-anchored.** A parser that produces valid content but attaches it to a different page cannot be adjudicated as "correct" via DocLayNet alone. Combine with PMC-OA style word-set truth where the same document appears in both.
3. **FPR-baseline corpora must be *demonstrably clean* to the extent feasible.** Federal Register PDFs are natively authored, but if a detector fires on a Federal Register document, the finding must be human-adjudicated before it is counted as an FPR event. It might be a genuine detection.
4. **DocLayNet does NOT directly annotate reading order.** DocLayNet supplies per-page region bounding boxes and block-category labels (`Text`, `Title`, `List-item`, `Table`, `Picture`, `Caption`, `Section-header`, `Page-header`, `Page-footer`, `Footnote`, `Formula`). Reading order across those blocks is a geometric inference (left-to-right within columns, top-to-bottom across columns). Any use of DocLayNet to adjudicate `W_MULTICOLUMN_ORDER` or similar order-sensitive detectors is a **derived criterion built on a geometric proxy**, not a direct G1 comparison. Reports based on this criterion must state so explicitly and must not describe the resulting evidence as "G1 validated reading order."
5. **DocLayNet does NOT supply table-cell structure.** The `Table` category is a region-only bounding box: no rows, no columns, no headers, no cell grid. TEDS (Tree-Edit-Distance-based Similarity) requires a cell-level HTML/DOM oracle, and therefore **cannot be computed from DocLayNet**. §6.1 uses **table-region localization** (IoU-style geometric agreement between parser-reported table regions and DocLayNet's `Table` bboxes) as the DocLayNet-side metric. Any TEDS-style adjudication would require a different corpus (PubTables-1M / FinTabNet / PubTabNet), which is out of V1 scope by Decision 2.2.a and remains deferred to V2.

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

**RESOLVED (Decision 3.1.a):** **Four-level scheme approved** as documented (GOOD / MINOR / MAJOR / CATASTROPHIC). Reasoning: matches the RISKY/OK/HIGH bands already used internally in AksharaMD so results are directly interpretable against existing cap thresholds; three levels loses cap-84-vs-cap-69 distinguishability; five levels creates false precision and multiplies reviewer disagreement without adding actionable resolution.

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
- **RESOLVED (Decision 3.3.a):** **Hybrid sourcing approved with stricter held-out rules.**
  - Protocol authors label the ~20-doc pilot to refine the rubric. Authors' pilot labels do NOT contribute to any held-out result.
  - Held-out labeling is done by **independent external reviewers** blinded to (a) parser identity, (b) AksharaMD score, (c) AksharaMD warning codes, and (d) any other detector output, wherever operationally feasible.
  - Where a specific adjudication task requires visibility into AksharaMD's flag (e.g., the localization actionability question in §6.3 requires seeing the diagnostic location), the reviewer is blinded to the *conclusion* — they answer "can this flag be mapped to a specific region?" without seeing whether AksharaMD scored the document high or low overall.
  - Held-out disagreement adjudication (§6.1) is the highest-stakes reviewer task and must run under full blinding: reviewer sees the parser output and source PDF and answers the prewritten questions independently of any tool's verdict.
  - Budget: ~$500-1500 for external labeling committed up front.

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

### 4.5 W_TABLE_MISSING, W_MULTICOLUMN_ORDER, W_HEADER_FOOTER_TABLE_GARBLED (existing detectors) — DocLayNet-based adjudication

- **Method:** Use DocLayNet's per-page region bounding boxes and block-category labels as the ground truth surface.
  - **W_TABLE_MISSING** is adjudicated as **direct G1**: a page has a `Table` region iff DocLayNet annotates one; the detector should fire iff the parser's markdown does not capture a corresponding table.
  - **W_MULTICOLUMN_ORDER** is adjudicated as a **derived criterion** built on a geometric proxy — DocLayNet does not directly label reading order (see §2.4 caveat 4). Expected order is inferred from block bboxes (left-to-right within columns, top-to-bottom across columns); disagreement between the inferred order and the parser output is the fired-vs-not signal. Reports must describe this as "geometric-proxy adjudication," not as "G1 reading order."
  - **W_HEADER_FOOTER_TABLE_GARBLED** is adjudicated as a **derived criterion**: DocLayNet directly labels `Page-header`, `Page-footer`, and `Table` regions, but "garbled" is a downstream judgement over the parser's markdown given the geometric adjacency of those regions. Reports must describe this as "geometric-proxy adjudication," not as "G1 garbling."
- **Table geometry** is limited to **table-region localization** (IoU-style geometric agreement). DocLayNet does not supply cell structure and therefore does not support TEDS — see §2.4 caveat 5.
- These are **existing** detectors carried into the study. Their inclusion is important because (a) score-validity depends on all detectors, not just the new ones, and (b) evaluating only the new detectors would be a coverage lie.
- **Predeclared thresholds:** the existing published maturity ratings (candidate / experimental) are the current claim; the V1 study either confirms them or motivates a downgrade.

### 4.6 What is deliberately NOT evaluated in V1

- **W_DETECTOR_TIMEOUT** — measurement-and-warn only; no product claim depends on it. Documented as such.
- **Informational rules** (AUTO_OCR_BACKEND_SELECTED, W_PDF_ATTACHMENT_IGNORED, etc.) — no penalty, no claim, out of scope.
- **The bundled reference parser as a product** — the study compares parsers to establish AksharaMD's independence from parser choice, not to rank the reference parser against competitors. Any per-parser ranking is a downstream artifact, not the study's target.

---

## 5. Score validity

Score validity is measured in **two distinct phases:**

- **Phase A — Calibration (pre-freeze).** On the calibration corpus, establish and confirm the scoring rule's behavior. This phase is where any parameter selection or threshold confirmation happens. Adjustments to the scoring implementation, if any, are made here and must be committed **before** the freeze.
- **Phase B — Held-out validation (post-freeze).** On the held-out corpus, using the frozen scoring rule, re-compute the same severity correlation and cap-distinguishability tests. This is the actual validation. **If held-out score validity fails, it fails V1.** No post-hoc scoring changes are permitted. The failure becomes a candidate finding for V2 protocol amendment.

The calibration corpus is where you learn what the scoring rule is; the held-out corpus is where you verify that the frozen scoring rule generalizes. Both are required for the L2 claim.

### 5.1 Phase A — Calibration analysis (pre-freeze)

- **Method:** For each `(document, parser)` in the **calibration** set, obtain the human severity label (§3) and the AksharaMD score. Compute Spearman rank correlation. Also compute per-axis correlations (structural_score vs Q1; content_score vs Q2).
- **Also:** sort calibration pairs by score and inspect the distribution of severity labels above/below each cap value (currently 69, 84). If the current caps do not correspond to visible severity steps in the calibration data, propose new cap values here — *before* the freeze — with documented rationale. Post-freeze cap changes are prohibited.
- **Output:** the frozen scoring rule (with any calibration-informed adjustments) plus an internal calibration report documenting the observed correlations. The calibration report itself is not a V1 claim — it is the input to the held-out validation.

### 5.2 Phase B — Held-out validation (post-freeze, frozen scoring rule)

- **Method:** On the **held-out** corpus, using the scoring rule from the freeze, compute:
  - Spearman rank correlation between AksharaMD score and human severity label.
  - Per-axis Spearman (structural_score vs Q1 aspects, content_score vs Q2 aspects).
  - Distribution of severity labels above and below each cap value.
- **Predeclared thresholds (rationales in §12):**
  - Overall Spearman ≥ 0.5 (rationale §12.3).
  - Per-axis Spearman ≥ 0.4 where the axis is applicable.
  - For each cap value: severity distributions above vs below the cap distinguishable at Mann–Whitney U p < 0.05 with ≥ 20 documents on each side.
  - ≥ 30% of documents just below a cap must NOT be severity-indistinguishable from those just above (rationale §12.4).
- **Failure handling:** if any held-out score-validity threshold fails, **the L2 claim fails V1.** No post-hoc adjustments to the scoring rule, caps, or threshold values. The observation is recorded in `POST_FREEZE_OBSERVATIONS.md` (§10.3) as evidence for the V2 protocol amendment.

### 5.3 Cap-attribution honesty

- Score-capped deductions (`IMAGE_PLACEHOLDER_NO_FALLBACK`, `W_*` cap rules) contribute their realized penalty to the axis score in the current implementation. This is documented as a known imprecision in `aksharamd/scoring/models.py` and PR #159.
- **The V1 study measures the current implementation as shipped.** If cap-attribution imprecision degrades held-out score validity below the §5.2 thresholds, that becomes evidence for a V2 refinement — not a reason to change the implementation mid-study.

---

## 6. Product utility experiments

Product utility is the L3 claim — the most important one for the product story. Two independent experiments:

### 6.1 Bidirectional disagreement analysis

- **Method:** For every `(document, parser)` in the held-out set, compute (a) AksharaMD's verdict (BAD if score < 70 or any capping W_ fires; else GOOD) and (b) a conventional-extraction-metric verdict (per-corpus: PMC-OA word-overlap for PMC-OA; **table-region localization** — IoU-style geometric agreement between parser-reported table regions and DocLayNet's `Table` bboxes — for DocLayNet tables (see §2.4 caveat 5: DocLayNet does not supply cell structure, so TEDS is not applicable); per ParseBench's own rubric for ParseBench pages).
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

**RESOLVED (Decision 7.a):** **Four parsers approved for V1** as documented. `mineru` and `unlimited_ocr` deferred to V2. Rationale: V1's four parsers already span three distinct architectural families (direct PDF text-layer, VLM+layout ×2, broad-format converter), which is sufficient for the L3 disagreement claim without multiplying compute and setup costs. V2 broadening can be motivated by V1 evidence.

**RESOLVED (Decision 7.b):** **Cloud parsers (LlamaParse, Reducto) excluded from V1.** Reproducibility concerns (API drift, key management, revocable service risk, per-call cost) complicate the first validation study unnecessarily. If we ever add commercial parsers, the study will need a separate ethics + reproducibility clause; not V1 scope.

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

**RESOLVED (Decision 8.3.a):** **~200 documents approved provisionally, NOT as a hard N.** The final held-out N is determined by the failure-event-count requirement below (§8.4). The ~200 figure is a working estimate for compute and labeling budget only. If the pilot reveals that natural-prevalence event counts are lower than assumed, N grows accordingly; if enrichment (see §8.4) sufficiently supplies event counts, N may stay at ~200 with clear enrichment disclosure. **The statistical plan (§11.4), not budget convenience, decides the final number.**

### 8.4 Failure-event count vs document count

Document count is NOT the correct statistical estimand for detector recall. Detector recall requires *failure-event count*: the number of documents in the corpus where the failure class actually occurred.

**A corpus of 200 pristine PDFs would tell us essentially nothing about catastrophic-failure recall.** The corpus mix must include documents where each failure class is expected to occur, at rates sufficient to compute per-detector recall with reasonable confidence intervals.

**RESOLVED (Decision 8.4.a):** **Enrichment approved with mandatory separation.** The held-out corpus is partitioned into two subsets that are analyzed and reported **separately, never combined into a single headline number:**

1. **Naturalistic subset** — random sampling from the candidate corpora. Answers the question *"How does AksharaMD behave on documents encountered without deliberately selecting failures?"* Aggregate FPR, natural-prevalence detector-fire rates, and precision-on-flagged-cases on this subset alone.
2. **Challenge subset** — enrichment with known-failure cases drawn from prior AksharaMD-flagged runs and from documents where reference-corpus GT indicates catastrophic events. Answers *"When meaningful extraction failures actually occur, how good is AksharaMD at detecting them?"* Recall, precision on the enriched cases.

The two subsets carry distinct estimand columns in every reported table (see §11.1). Aggregate metrics that mix both are **prohibited** — they would silently smuggle enrichment bias into a naturalistic-sounding claim. Enrichment must be transparently disclosed: source of enriched documents, size of the enrichment, and its rationale.

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

**Two things must be frozen: AksharaMD *and* the entire evaluation machinery.** Otherwise the goalposts can move without any AksharaMD modification — e.g., a change to XML preprocessing, label mapping, exclusion logic, or conventional-metric implementation shifts what is measured while AksharaMD stays untouched.

### 10.1 Study Freeze Manifest

At freeze time, the following are captured in a single reproducibility anchor at `docs/evaluation/STUDY_FREEZE_MANIFEST_V1.md`. **This file does not exist yet — it is a future artifact, produced at freeze time (Authorization C, §0.1).** No empty scaffold is created now; the manifest is meaningful only when the values it captures are settled. The manifest either contains or references (with SHA-256 hashes) every item below. It is the artifact the paper cites as the reproducibility bundle.

**AksharaMD side:**

1. **Git tag** — `benchmark-v1-frozen` — created on develop, points at a specific commit.
2. **AksharaMD implementation** — all detectors, thresholds, scoring policy, cap values, priority ordering.
3. **Detector constants** — every numerical threshold in every validator, listed by name and value.
4. **Score caps** — every cap value (currently 69, 84) with its rule id.
5. **Parser-stub catalog contents** — `parser_stubs/*.json` at frozen commit, hashed.

**Parser side:**

6. **Parser versions** — exact package versions for each of the four V1 parsers, plus dependency lockfile.
7. **Parser configuration** — every non-default flag, environment variable, or setting passed to each parser.

**Corpus side:**

8. **Corpus manifest + hashes** — every document with SHA-256, source URL, acquisition instructions.
9. **Document partition assignment** — dev/cal/held-out labeling per document, published in `CORPUS_MANIFEST.md`.
10. **Naturalistic / challenge subset partition** — which held-out documents are enrichment (§8.4) and which are naturalistic, hashed.

**Ground-truth pipeline:**

11. **Ground-truth transformation procedures** — code for extracting XML text, DocLayNet bboxes, CUAD spans; committed under `benchmarks/eval_v1/ground_truth/`, hashed.
12. **Normalization implementation** — every whitespace / hyphenation / Unicode / case-folding transformation applied before comparison, on both sides of any comparison.
13. **Parser-output cleanup** — any post-parser normalization (e.g., stripping frontmatter, trimming trailing whitespace) applied uniformly across all parsers.

**Metric side:**

14. **Metric definitions** — every computation formula, published in Appendix C, committed as executable code.
15. **Evaluation scripts** — end-to-end analysis pipeline from raw run outputs to reported numbers, under `benchmarks/eval_v1/analysis/`.
16. **Conventional-metric implementations** — reference implementations of the "conventional" metrics used in the §6.1 disagreement analysis (e.g., PMC-OA word-overlap script; DocLayNet table-region-localization IoU script — NOT TEDS, per §2.4 caveat 5).

**Adjudication side:**

17. **Failure-label mapping table** — the deterministic (Q1, Q2, Q3) → label table (Appendix B).
18. **Reviewer instructions** — verbatim question wording, presentation format, blinding rules.
19. **Adjudication rubric versions** — every rubric a reviewer sees during held-out labeling, versioned.

**Analysis-plan side:**

20. **Exclusion rules** — the pre-declared exclusion criteria (§14) with any pilot-informed extensions.
21. **Statistical analysis plan** — every estimand, CI method, correction procedure from §11.
22. **Predeclared thresholds** — every number and rationale in §12.

Any item on this list that changes between the freeze and the held-out run **invalidates the current held-out run and requires a full rerun.** The manifest is the operational definition of "frozen."

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

- Post-freeze, any change to the protocol or implementation becomes a **published amendment** at `docs/evaluation/AMENDMENTS.md`. **This file does not exist yet — it is a future artifact, opened at freeze time and appended to as amendments are proposed.** No empty scaffold is created now; the file is meaningful only when there is an amendment to record.
- Amendments cannot alter the interpretation of already-run held-out data. They apply only to future runs (V2 or later).
- Every amendment includes: (a) the observation that motivated it, (b) the specific change, (c) which prior claim (if any) is affected, (d) whether the affected claim requires a re-run to remain valid.

---

## 11. Statistical analysis plan

### 11.1 Estimands

Every estimand is computed **separately** for the naturalistic subset and the challenge subset (§8.4). Combined-subset numbers are prohibited in headline reporting; they silently smuggle enrichment bias.

The primary estimands are:

- **Detector precision** = TP / (TP + FP) per detector, **per subset (naturalistic vs challenge)**, per corpus.
- **Detector recall** = TP / (TP + FN) per detector, **on the challenge subset** (recall requires known failure events, so recall on the naturalistic subset alone would be too underpowered to interpret).
- **Detector FPR** = FP / (FP + TN) per detector, **on the naturalistic subset only** (FPR on enriched corpora is meaningless because enrichment intentionally raises the failure rate).
- **Score/severity Spearman ρ** overall and per axis, on the held-out set, computed per subset and reported side by side.
- **Cap-value distinguishability** — Mann–Whitney U on severity labels above vs. below each cap, held-out only.
- **Disagreement rate** — proportion of `(document, parser)` in each 2×2 cell, per subset.
- **Downstream RAGAS correlation** — Spearman ρ between AksharaMD score and RAGAS answer-quality delta.
- **Localization actionability rate** — proportion of flags reviewers can map to a specific region within 60s.

### 11.2 Document-level clustering and paired-parser structure

**Critical:** parser outputs on the same document are not independent. Four parsers processing the same PDF share the document's underlying difficulty — a horrible scanned PDF may make all four fail. `200 documents × 4 parsers = 800` observations are therefore **not 800 independent samples**.

The analysis must account for this in three ways:

1. **Bootstrap by document, not by observation.** Resample documents with replacement; for each resampled document, take all four parser outputs together. Compute the estimand on each bootstrap sample; take percentile CIs across resamples. This preserves the clustering structure. Analysis code must implement bootstrap-by-document; naive per-observation bootstrap is prohibited.
2. **Paired parser comparisons.** When comparing parsers (e.g., disagreement rates, per-parser fidelity), use paired tests over documents (Wilcoxon signed-rank, or paired-percentile bootstrap). This exploits the shared-document structure and *strengthens* parser comparisons because both parsers were tested on identical inputs.
3. **Mixed-effects modeling (optional, for exploratory questions).** For estimands where document and parser effects are both of interest — e.g., "how much of the score variance is explained by document difficulty vs. parser identity?" — a linear mixed-effects model with document as a random effect is appropriate. Predeclare which questions require this before running.

**Failure to account for clustering inflates confidence and can produce apparent significance where none exists.** No headline number is reported without a bootstrap-by-document CI.

### 11.3 Confidence intervals and uncertainty

- Bootstrap 95% CIs (10 000 resamples), **resampling by document** per §11.2.
- Report `n` for every ratio (both document count and observation count where they differ).
- No p-values as pass/fail thresholds. Use CI-based interpretation.

### 11.4 Multiple comparisons

- Where multiple detectors × multiple corpora × multiple thresholds are compared, apply Benjamini-Hochberg correction for any exploratory findings.
- Predeclared criteria (§12) are NOT corrected — they are single pre-registered tests each.

### 11.5 Sample size rationale (worked example — silent-failure disagreement)

For the L3 "silent-failure" claim to be defensible, the (Conv=GOOD / AksharaMD=BAD) cell must be non-trivial in size.

- Assume disagreement occurs on ~20% of `(document, parser)` pairs (informed by Plan E pilot data: parsers score in different bands on the same documents).
- Assume the silent-failure cell is ~half of the disagreement (~10%). The other half is the benchmark-false-alarm cell.
- To estimate the adjudicated-genuine rate in the silent-failure cell with ±0.15 CI half-width at 95%, we need ≥ ~40 cases per parser in that cell.
- 40 cases / 10% cell rate → ~400 (document × parser) pairs per parser.
- 4 parsers × ~100 documents = 400 pairs per parser (assuming each document runs through each parser). Suggests ~100 held-out documents. Doubling to ~200 provides tighter per-cell CIs and buffer for exclusions.

**Working estimate:** ~200 held-out documents. Documented as ± ~0.10 CI half-width on the primary disagreement estimand.

**However, the final N is determined by event counts, not by this rough calculation.** Per §8.4 the corpus is partitioned into naturalistic and challenge subsets, each with its own reporting. Per §11.2 the analysis bootstraps by document, so the effective independent-sample count is document count, not (document × parser) count. The pilot must confirm:

- The naturalistic subset produces at least ~40 documents per parser landing in the disagreement quadrants.
- The challenge subset produces at least ~15 failure-event documents per detector for recall estimation.

If either count is short after the pilot, expand N before the freeze. **N is a consequence of the statistical plan, not an input to it.** Post-freeze N changes are prohibited.

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

### 15.1 Required closing structure of the Results document

The eventual `docs/evaluation/RESULTS_V1.md` (produced post-held-out-run) must **literally end** with the following three explicit lists, unpadded:

```
CLAIMS EARNED
✓ L1 — <detector name>: <estimand> = <value> [<CI>]; predeclared threshold met (§12.<n>).
✓ L2 — <specific claim>: <estimand> = <value> [<CI>]; predeclared threshold met (§12.<n>).
✓ L3 — <specific claim>: <estimand> = <value> [<CI>]; predeclared threshold met (§12.<n>).

CLAIMS NOT EARNED (studied, did not meet threshold — no public claim will be made)
✗ L3 — <specific claim>: <estimand> = <value> [<CI>]; predeclared threshold NOT met (§12.<n>).

CLAIMS FALSIFIED (studied and actively contradicted)
✗ <specific claim>: <estimand> = <value> [<CI>]; direction reversed / null hypothesis maintained (§12.<n>).

CLAIMS NOT ADDRESSED IN V1
- <specific claim>: study not powered / corpus not present / deferred to V2.
```

This is unusually transparent for an OSS benchmark and is a deliberate design choice: **the report shows the receipt rather than merely reporting a number.** Any subsequent public communication about AksharaMD must trace back to a specific "CLAIMS EARNED" entry. Marketing copy that references a "CLAIMS NOT EARNED" or "CLAIMS FALSIFIED" line is prohibited.

---

## Appendices

### Appendix A — Corpus inventory detail

*[To be produced during Milestone 1 pilot. Placeholder: per-corpus documents, splits, acquisition instructions, hashes.]*

### Appendix B — (Q1, Q2, Q3) → label mapping table

**Status:** becomes frozen for Authorization B upon merge of the
Appendix B amendment PR. All 64 combinations resolve to a single label
under the rule defined below. The machine-readable form lives at
`benchmarks/eval_v1/mapping.v1.json` (`mapping_frozen: true` post-merge).
`benchmarks/eval_v1/mapping.v0.json` is preserved on disk as historical
evidence of the pre-amendment state (4 diagonal rows only, everything
else `UNRESOLVED_MAPPING`).

#### B.1 Per-question severity ranks (justifications)

Each of Q1/Q2/Q3 has a natural ordinal severity ranking derived from
the value phrasing itself. Ranks are 0 (best) to 3 (worst) so a single
aggregation function can span all three dimensions.

**Q1 — Coverage.** *"Does the markdown appear to contain substantially
all of the source's textual content?"*

| Value | Rank | Justification |
|---|---|---|
| yes | 0 | Complete coverage — no observable content loss. |
| mostly | 1 | Small gaps — a fraction of content is missing but the bulk is preserved. Aligns with §3.1's MINOR word-preservation band (85–95%). |
| partially | 2 | Significant loss — a substantial share of content is missing. Aligns with §3.1's MAJOR band (50–85%). |
| no | 3 | Largely absent — most of the source's textual content is not present. Aligns with §3.1's CATASTROPHIC band (< 50%). |

**Q2 — Fidelity.** *"Where content is present, is it recognizably
faithful to the source, or are there stubs, gibberish, or corruptions?"*

| Value | Rank | Justification |
|---|---|---|
| faithful | 0 | Faithful — no observable corruption. |
| minor issues | 1 | Local corruption — small pockets of gibberish, stubs, or artifacts, but overall structure survives. |
| significant corruption | 2 | Widespread corruption — corruption is common enough to impair use, though some content is still faithful. |
| mostly stub or junk | 3 | Predominantly non-content — §3.1's "extensive placeholder stubs in body" / "whole sections replaced with junk." |

**Q3 — Downstream usability.** *"If this markdown were the sole input
to a RAG query system for this document, would answers to typical
questions be usable, degraded, or wrong?"*

| Value | Rank | Justification |
|---|---|---|
| usable | 0 | RAG answers usable — no operational impact. |
| usable-with-caveats | 1 | RAG answers usable but with modest degradation — §3.1's MINOR "RAG answers derivable with modest degradation." |
| degraded | 2 | RAG answers noticeably worse — §3.1's MAJOR "downstream user will notice the impact." |
| wrong | 3 | RAG answers wrong or misleading — §3.1's CATASTROPHIC "RAG or LLM ingestion will produce wrong or misleading answers." |

Ranks are the position of each value in the ordered value list, i.e.
`Q1_VALUES = ("yes", "mostly", "partially", "no")` with ranks 0..3, and
similarly for Q2/Q3.

#### B.2 Aggregation rule (proposed)

> **`label = LABELS[max(rank(q1), rank(q2), rank(q3))]`**
> where `LABELS = ("GOOD", "MINOR", "MAJOR", "CATASTROPHIC")`.

**Rationale.** §3.1 defines the label bands as *the most severe
independently sufficient failure condition*. CATASTROPHIC is defined
with an OR — "< 50% of source words preserved **OR** extensive
placeholder stubs in body **OR** whole sections replaced with junk."
MAJOR and MINOR similarly enumerate ORs across the coverage, fidelity,
and usability axes. The `max` aggregation captures this OR semantic
exactly: any single dimension that reaches a given severity is
sufficient to assign that severity, and no two "good" dimensions can
offset one "bad" one.

**This aggregation rule is proposed for review in this PR.** It is not
pre-approved methodology. Alternatives that were considered and
rejected:

| Alternative | Rejected because |
|---|---|
| **Mean rank** (`round(mean(Q1, Q2, Q3))`) | Two "good" dimensions offset one "bad" dimension. E.g. `(yes, faithful, wrong)` → mean 1 → MINOR, but Q3="wrong" alone is CATASTROPHIC under §3.1. |
| **Q3-dominant** (`severity = Q3_rank`) | Ignores Q1 and Q2 entirely. `(no, mostly stub or junk, usable)` → GOOD despite catastrophic coverage/fidelity, because a particular reviewer's question set happened to be answerable from a fragment. |
| **Weighted sum** (e.g. `0.5·Q1 + 0.3·Q2 + 0.2·Q3`) | Weights themselves would need methodological justification we do not currently have; introduces arbitrariness the OR-semantic of §3.1 does not require. |
| **Lexicographic** (Q1, then Q2, then Q3) | Same asymmetry problem as Q3-dominant applied in a different direction; also asymmetric across dimensions §3.1 treats symmetrically. |

#### B.3 Coherence checks the amendment must pass

1. **Diagonal preservation.** The four rows Appendix B specified before
   the amendment must retain their labels:
   - (yes, faithful, usable) → max(0,0,0) = 0 → **GOOD** ✓
   - (mostly, faithful, usable-with-caveats) → max(1,0,1) = 1 → **MINOR** ✓
   - (partially, minor issues, degraded) → max(2,1,2) = 2 → **MAJOR** ✓
   - (no, mostly stub or junk, wrong) → max(3,3,3) = 3 → **CATASTROPHIC** ✓
2. **Full coverage.** All 4×4×4 = 64 combinations map to a label; no
   `UNRESOLVED_MAPPING` remains under v1.
3. **Monotonicity.** For any `(q1, q2, q3)`, replacing any single
   component with a higher-rank value cannot decrease the resulting
   severity. Follows mathematically from the monotonicity of `max`;
   asserted as a regression test.

All three checks are asserted by `tests/test_eval_v1.py`.

#### B.4 Full 64-combination mapping table

The mapping is:

| Q1 | Q2 | Q3 | Label |
|---|---|---|---|
| yes | faithful | usable | GOOD |
| yes | faithful | usable-with-caveats | MINOR |
| yes | faithful | degraded | MAJOR |
| yes | faithful | wrong | CATASTROPHIC |
| yes | minor issues | usable | MINOR |
| yes | minor issues | usable-with-caveats | MINOR |
| yes | minor issues | degraded | MAJOR |
| yes | minor issues | wrong | CATASTROPHIC |
| yes | significant corruption | usable | MAJOR |
| yes | significant corruption | usable-with-caveats | MAJOR |
| yes | significant corruption | degraded | MAJOR |
| yes | significant corruption | wrong | CATASTROPHIC |
| yes | mostly stub or junk | usable | CATASTROPHIC |
| yes | mostly stub or junk | usable-with-caveats | CATASTROPHIC |
| yes | mostly stub or junk | degraded | CATASTROPHIC |
| yes | mostly stub or junk | wrong | CATASTROPHIC |
| mostly | faithful | usable | MINOR |
| mostly | faithful | usable-with-caveats | MINOR |
| mostly | faithful | degraded | MAJOR |
| mostly | faithful | wrong | CATASTROPHIC |
| mostly | minor issues | usable | MINOR |
| mostly | minor issues | usable-with-caveats | MINOR |
| mostly | minor issues | degraded | MAJOR |
| mostly | minor issues | wrong | CATASTROPHIC |
| mostly | significant corruption | usable | MAJOR |
| mostly | significant corruption | usable-with-caveats | MAJOR |
| mostly | significant corruption | degraded | MAJOR |
| mostly | significant corruption | wrong | CATASTROPHIC |
| mostly | mostly stub or junk | usable | CATASTROPHIC |
| mostly | mostly stub or junk | usable-with-caveats | CATASTROPHIC |
| mostly | mostly stub or junk | degraded | CATASTROPHIC |
| mostly | mostly stub or junk | wrong | CATASTROPHIC |
| partially | faithful | usable | MAJOR |
| partially | faithful | usable-with-caveats | MAJOR |
| partially | faithful | degraded | MAJOR |
| partially | faithful | wrong | CATASTROPHIC |
| partially | minor issues | usable | MAJOR |
| partially | minor issues | usable-with-caveats | MAJOR |
| partially | minor issues | degraded | MAJOR |
| partially | minor issues | wrong | CATASTROPHIC |
| partially | significant corruption | usable | MAJOR |
| partially | significant corruption | usable-with-caveats | MAJOR |
| partially | significant corruption | degraded | MAJOR |
| partially | significant corruption | wrong | CATASTROPHIC |
| partially | mostly stub or junk | usable | CATASTROPHIC |
| partially | mostly stub or junk | usable-with-caveats | CATASTROPHIC |
| partially | mostly stub or junk | degraded | CATASTROPHIC |
| partially | mostly stub or junk | wrong | CATASTROPHIC |
| no | faithful | usable | CATASTROPHIC |
| no | faithful | usable-with-caveats | CATASTROPHIC |
| no | faithful | degraded | CATASTROPHIC |
| no | faithful | wrong | CATASTROPHIC |
| no | minor issues | usable | CATASTROPHIC |
| no | minor issues | usable-with-caveats | CATASTROPHIC |
| no | minor issues | degraded | CATASTROPHIC |
| no | minor issues | wrong | CATASTROPHIC |
| no | significant corruption | usable | CATASTROPHIC |
| no | significant corruption | usable-with-caveats | CATASTROPHIC |
| no | significant corruption | degraded | CATASTROPHIC |
| no | significant corruption | wrong | CATASTROPHIC |
| no | mostly stub or junk | usable | CATASTROPHIC |
| no | mostly stub or junk | usable-with-caveats | CATASTROPHIC |
| no | mostly stub or junk | degraded | CATASTROPHIC |
| no | mostly stub or junk | wrong | CATASTROPHIC |

The table and `benchmarks/eval_v1/mapping.v1.json` are generated from
the same aggregation rule and asserted equal by a regression test. If a
future amendment changes the rule or any row, both artifacts must
change together and the v1 file becomes historical.

### Appendix C — Metric formulas

*[To be produced during pilot. All formulas for precision, recall, FPR, Spearman ρ, Mann–Whitney U, bootstrap CI, per-corpus word-overlap, DocLayNet table-region-localization IoU. TEDS is NOT part of V1 — DocLayNet does not supply cell structure; see §2.4 caveat 5.]*

### Appendix D — Sample-size worked calculations

*[Extended version of §11.4 including per-detector recall-power calculations and per-cell disagreement power.]*

### Appendix E — Reproducibility checklist

*[Line-item checklist an external reviewer signs off on before results are published. Standard categories: data, code, environment, analysis, review materials.]*

### Appendix F — Reviewer training materials draft

*[To be produced during pilot. 20 pilot documents with gold-standard labels for reviewer calibration.]*

### Appendix G — REVIEW DECISION queue — ALL RESOLVED

All seven V1 review decisions are resolved. In-doc references at their respective sections carry `RESOLVED (Decision N.n.x):` headers with the applied ruling.

| # | Section | Decision | Resolution |
|---|---|---|---|
| 1 | §2.2 | Include RealDocBench? | **Defer to V2.** External replication / generalization. |
| 2 | §3.1 | Failure taxonomy level count | **4 levels approved** (GOOD / MINOR / MAJOR / CATASTROPHIC). |
| 3 | §3.3 | Reviewer sourcing | **Hybrid approved with stricter held-out rules.** Authors pilot only; externals held-out with full blinding to parser identity, score, and warning codes wherever operationally feasible. |
| 4 | §7 | V1 parser slate size | **4 parsers approved.** `mineru` and `unlimited_ocr` deferred to V2. |
| 5 | §7.b | Cloud parsers in V1? | **Excluded.** Reproducibility and API-drift concerns. |
| 6 | §8.3 | Total held-out corpus size | **~200 provisional, NOT a hard N.** Final N determined by the event-count calculation (§11.5), not by budget convenience. |
| 7 | §8.4 | Failure-event enrichment strategy | **Enrichment approved with mandatory separation.** Naturalistic subset and challenge subset are analyzed and reported separately. Aggregate metrics that combine them are prohibited. |

No open decisions remain. Milestone 1 pilot can be authorized under Authorization A (§0.1) once this protocol is merged.

---

## Signature block

**Protocol version:** V1 DRAFT
**Frozen implementation tag (post-pilot):** *`benchmark-v1-frozen` — TBD at freeze time*
**Approved by:** *[project owner]*
**Reviewer sign-off:** *[external reviewer, at freeze time]*

**Author stance restated:**
> A successful study can produce conclusions like *"W_GIBBERISH contributes essentially nothing on real corpora,"* *"the cap-at-84 threshold is wrong,"* or *"the general 'silent-failure detector' framing is too broad for V1."* Any protocol that cannot state up front what would invalidate its claims has not designed a validation study.
