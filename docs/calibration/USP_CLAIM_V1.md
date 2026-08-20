# USP Claim v1 — Draft Spec

**Status:** RATIFIED — all §5 decisions locked
**Date:** 2026-08-20
**Ratified:** 2026-08-20 by k2alyan
**Related:**
- `docs/calibration/PLAN.md` — parent plan
- `docs/calibration/PHASE_0_INVENTORY.md` — verified starting state
- `C:\Users\kalya\parsebench\benchmarks\READINESS_CALIBRATION.md` — frozen contract (v1.0, 2026-07-13)
- `C:\Users\kalya\parsebench\benchmarks\RESCORE_REPORT_V1.md` — 2026-07-13 measured state

This document defines the exact claim we will defend, the metrics and thresholds that back it, the success bar for locked+challenge validation, and the four policy decisions Phase 1 has to close.

Once §5 decisions are locked, this document is frozen for the remainder of the calibration cycle.

---

## 1. The claim, in one sentence

**Draft:** "Documents that AksharaMD scores HIGH (readiness ≥ 85) emit no known-issue warning AND recover the document's structured text at high fidelity — measurably, on a stratified 75-document benchmark drawn from ParseBench."

The claim is deliberately dual: (a) the score means the extraction is faithful, AND (b) HIGH-band documents carry no unaddressed alerting warning. Falling back to only (a) is the "narrow claim" fallback if Phase 3 signal work fails to close the silent-failure gap.

---

## 2. The measurement — what "high fidelity" means

Adopted from the frozen contract §4.1 with two changes forced by measured GT quality (see `RESCORE_REPORT_V1` and dev report §7).

### 2.1 Primary metrics — HIGH-band hypothesis (contract §4.1, ratified)

A HIGH-band document (readiness ≥ 85) passes the primary hypothesis if all applicable primary metrics meet threshold. The claim strength is measured as **fraction of HIGH-band documents that pass all applicable primary metrics**.

| Metric | Threshold | Applicability |
|--------|-----------|---------------|
| `text_char_recall` (Levenshtein against verbatim text) | ≥ 0.90 | Deferred — see §2.3 |
| `sentence_recall` (bag-of-sentence GT, paragraph-split) | ≥ **empirical Phase 1 threshold** (see §2.4) | Applicable to text-content docs with reconstructed GT (21 of 75 in current corpus) |
| `rule_order_pass_rate` (rule-based reading order) | ≥ 0.70 | Applicable to docs with `order` rules |
| `heading_accuracy` | ≥ 0.75 | Applicable to docs with heading rules |
| `table_grits_con` (GRiTS cell F-score) | ≥ 0.40 | Applicable to docs with ≥ 2 expected tables |
| `unsupported_output_ratio` (lexical proxy) | ≤ 0.30 | Always applicable; proxy — requires manual review of any failing doc |

### 2.2 Composite false-safe condition (contract §4.1 as-is)

A HIGH-band document is a **material false-safe** if any applicable primary metric fails threshold. This is stricter than the contract's operational false-safe definition (contract §5, which uses only `text_char_recall < 0.60`) and matches the reporting format demanded by contract §8.4.

### 2.3 Metric substitution: `text_char_recall` deferred

Contract §4.1 lists `text_char_recall ≥ 0.90` as the headline HIGH-band threshold. The dev report §7 documents that current GT quality (partial bag-of-sentence, not verbatim document text) makes `annotation_text_similarity` (the ParseBench Levenshtein metric) produce values in 0.001–0.009 — the metric is not usable at this GT quality.

**Decision:** for this calibration cycle, drop `text_char_recall` from the HIGH-band composite condition. Rely on `sentence_recall` for text fidelity. Rebuilding verbatim GT is out of scope (see PLAN.md anti-goals).

**Reversal condition:** if a future GT rebuild produces verbatim text, `text_char_recall ≥ 0.90` is re-introduced at the next contract version.

### 2.4 `sentence_recall` threshold — Phase 1 recalibration

Contract spec threshold: ≥ 0.85.
Dev-run observed range on 21 text-content docs: 0.000–0.857 (median 0.458).
Applying 0.85 threshold: fails 20/21 including all known-good docs. Threshold not usable.

**Decision to close in Phase 1:** set an empirical threshold based on separation between human-labeled PASS and FAIL docs on the dev split. Candidate approach: threshold = midpoint between PASS lowest and FAIL highest, rounded down. If that separation does not exist cleanly, threshold is the FAIL 90th percentile.

Actual number determined in Phase 1 execution. This document is frozen once that number lands.

### 2.5 Warning-based supplementary criterion (Option A from PLAN.md §Phase 2)

Any HIGH-band document that emits an alerting warning (`W_MULTICOLUMN_ORDER`, `W_HEADER_FOOTER_TABLE_GARBLED`, `W_IMAGE_ONLY_NO_OCR`, `W_TABLE_MISSING`, or subsequent additions) is score-capped out of HIGH before ingestion policy evaluates the band. This means the "HIGH-band false-safe" set can only contain documents where the score is HIGH AND no alerting warning fired — i.e. the silent failures.

**This is the score-policy shape assumed by this claim.** Phase 2 implements the cap. If Phase 2 lands Option B instead (redefinition rather than capping), this section is rewritten.

---

## 3. Success bar for locked+challenge validation

Contract §8.7 offers three verdicts: WELL-CALIBRATED, NEEDS-THRESHOLD-ADJUSTMENT, NEEDS-SCORE-REVISION.

**Ship the claim publicly** if the locked-validation and challenge splits combined yield:

| Rate | Threshold | Justification |
|------|-----------|--------------|
| HIGH-band raw false-safe rate | ≤ 8% | Slightly stricter than the contract's ≤10% dev gate; small buffer against locked-vs-dev variance |
| HIGH-band false-risky rate | ≤ 10% | Contract default |
| Silent-failure rate within HIGH band | ≤ 3% | Post-Phase-2 target — silent failures should be the rare exception, not the common case |

Any of the following triggers a **narrower published claim** instead of the full one:
- False-safe rate 8–15% → publish the claim scoped to non-table-tier documents.
- False-safe rate >15% → do not ship the claim; return to Phase 3.

---

## 4. Corpus and splits (contract §2, ratified)

No changes. The 75-doc, 5-tier stratified corpus with the (25 dev + 35 locked + 15 challenge) split, seed 20260713, is inherited from the frozen contract. Located at `C:\Users\kalya\parsebench\benchmarks\calibration_corpus.jsonl`.

---

## 5. Ratified decisions (2026-08-20)

All four decisions locked per recommendation. Recorded in-line below.

### 5.1 Score-policy shape
- **Option A (recommended):** Alerting warnings score-cap the document out of HIGH.
- **Option B:** False-safe metric is redefined to exclude documents where an alerting warning fired.

**Impact:** Option A is stronger and simpler to explain to users. Option B is closer to what today's score means but weaker as a USP claim.

Recommendation: A.
**Decision: A — alerting warnings score-cap the document out of HIGH.**

### 5.2 Multimodal-usable dimension
The three FAIL image-only docs (letter3, myctophidae, japanese) produce output containing valid `asset://` image references. If a vision-capable downstream LLM can use them, the document is *effectively* usable in multimodal mode. Do we let them PASS?

- **Option A:** No. Text-only bar. FAIL means FAIL. Simplifies the story.
- **Option B:** Yes, gated on `--multimodal-target` flag or similar. Doubles the state space.

Recommendation: A for v1; revisit if multimodal becomes the primary consumption pattern.
**Decision: A — text-only bar for v1. Image-only docs with valid `asset://` refs still FAIL. Multimodal-target mode deferred.**

### 5.3 Table-tier false-safes
The four table-heavy dev docs are confirmed false-safes because AksharaMD's table parser missed the tables entirely. Table parser rewrites are out of this milestone.

- **Option A:** Attach a score cap via `W_TABLE_MISSING` (or table-quality signal) so they drop out of HIGH. Reduces measured false-safe rate. Cost: one signal implementation.
- **Option B:** Exclude table-heavy documents from the false-safe criterion in the report, with a documented rationale ("table extraction is a known parser limitation; the readiness score claim applies to non-table-heavy documents"). Contract-compliant per §8.1.

Recommendation: A. It matches the score-policy shape from §5.1 and doesn't scope the USP claim.
**Decision: A — implement `W_TABLE_MISSING` (or equivalent table-quality signal) as an alerting warning that score-caps out of HIGH. Ship in the same Phase 2 PR as the §5.1 caps.**

### 5.4 Ratify contract §4.1 as-is, or write a scoped variant?
The claim in §1 references contract §4.1 with two modifications (drop `text_char_recall`, recalibrate `sentence_recall` threshold). Do we consider this the same claim or a v2?

- **Option A:** Same claim, documented modifications. `SCORING_POLICY_VERSION` stays at whatever Phase 2 sets it to. Contract v1.0 remains authoritative.
- **Option B:** Write a `USP_CLAIM_V2.md`; leave §4.1 as historical.

Recommendation: A. Modifications are documented in §2.3 and §2.4; anyone reading knows what changed.
**Decision: A — same claim, documented modifications. Contract v1.0 remains authoritative. Phase 2's `SCORING_POLICY_VERSION` bump reflects the score-formula change, not a claim rewrite.**

---

## 6. What sign-off unblocks

Signing this document (all §5 decisions written down) unblocks:

- Phase 2: score-policy landing implements §5.1.
- Phase 3: silent-failure recall targets the 6 dev-split silent-fail docs.
- Phase 4: dev-split re-run measures against §3 thresholds.
- Phase 5: README carries the number from §3, with the methodology in §2.

---

## 7. What sign-off freezes

Per contract §9 and analogously here:

**Frozen after sign-off:**
- The claim wording (§1).
- The metric set and thresholds (§2), except the `sentence_recall` threshold value, which is filled in during Phase 1 execution.
- Success bar and verdict thresholds (§3).
- Corpus + split (§4).
- The four policy decisions (§5).

**Not frozen (may change during the cycle):**
- The score formula and code that implements the caps.
- The specific signals added in Phase 3.
- The README wording that publishes the number.
