# AI Readiness Score — Calibration Verdict (Phase 4)

**Status:** Not certified for unattended production gating. Improved and stable on the development split; two calibration gates remain unmet; four named blind spots documented below.
**Scope of this run:** Development split only. The locked and challenge splits remain sealed and were deliberately not opened (see [Sealed Splits](#7-sealed-splits)).
**Date:** 2026-08-22 · **Pipeline:** main @ `5ea6ceb` · **Scoring policy:** v1.3

---

## 1. What this document is

AksharaMD attaches an AI Readiness Score (0–100) and a quality band — HIGH (≥85) / OK (≥70) / RISKY (≥50) / POOR (<50) — to every compiled document, so a downstream pipeline can decide whether to trust an extraction *before* it reaches a vector store rather than *after* an LLM answers a question wrong.

This verdict reports how trustworthy that score currently is, measured against a human-labeled development corpus. It states plainly what works, what does not, and what closing each remaining gap requires. It is written to be read by three audiences at once: the engineering team maintaining the scorer, a decision-maker deciding whether to rely on it, and an external evaluator deciding whether to adopt it. Where those audiences need different things, the document says so.

It does not certify the scorer for unattended production use. It gives you what you need to decide that for your own situation.

---

## 2. The one metric that matters: the silent failure

A **silent failure** is the only failure mode this calibration treats as serious: a document that was extracted badly but scored HIGH — bad text wearing a green light. It is dangerous precisely because nothing flags it. A RISKY or POOR score on bad text is working as intended; you recheck it. A HIGH score on bad text passes straight through the gate, poisons the store, and surfaces later as a wrong answer with no breadcrumb back to the cause.

Everything below is organized around driving the silent-failure rate down. A second metric, **raw HIGH-band false-safe rate**, measures the same danger from the band's point of view: of everything the scorer marked HIGH, how much should not have been trusted.

---

## 3. Gate outcome (development split)

Two gates define "calibrated enough to trust unattended." Neither is met yet. Both improved materially this phase.

| Rate | Baseline (2026-07-13) | Phase 4 v2 (2026-08-22) | Target | Gate |
|------|-----------------------|-------------------------|--------|------|
| Raw HIGH-band false-safe | 53% (9/17) | **25% (3/12)** | ≤ 10% | **FAIL** |
| Silent-failure | 35% (6/17) | **25% (3/12)** | ≤ 3% | **FAIL** |

Fractions shown to make denominators explicit. Note the baseline denominator (17 HIGH-band docs) differs from Phase 4 v2's (12): fixed documents leave the HIGH band, shrinking both numerator and denominator — see the note below on reading the movement. The baseline 53% here (9/17, gate rate) is unrelated to the 53% classifier-misclassification figure in issue #117 (8/15); the shared number is coincidental.

**How to read the movement honestly.** The improvement is real but smaller than the percentages suggest. When a formerly-silent document is fixed, it leaves the HIGH band entirely — so it exits both the numerator (bad-and-HIGH) and the denominator (everything HIGH) of the rate. The count of genuine silent failures on the dev split fell from 6 to 3; the rate moved less because both sides of the fraction shrank together. Do not read "25%, down from 40%" as a trend converging on the target. Read it as: the fixable-by-current-methods failures have been fixed, and what remains needs new capability, not tuning.

---

## 4. What works

The following held up under the dev-split evaluation and are considered stable:

- The four scoring bands and per-block confidence (EXTRACTED / INFERRED / AMBIGUOUS) behave as designed on the documents the scorer can see correctly.
- Image-only / scanned pages that previously scored HIGH with no warning are now correctly capped. This closed three of the six original silent failures.
- Table-expected-but-not-extracted detection now fires on substantial rejected table candidates, closing the SERFF-class false-safe (a real actuarial table that the extractor dropped while the score stayed HIGH).
- Control documents did not regress. All five known-good controls (`battery`, `2colmercedes`, `docusigned`, `gridofnumbers`, `webprint`) held their band and their exact warning set across every change in this phase. No fix introduced a new false positive on a good document.

---

## 5. What does not work: the four named blind spots

These are the documents the scorer currently gets wrong, stated specifically so any reader can check whether their own corpus resembles them. Severity reflects how dangerous the failure is, not how hard it is to fix.

Note on where these failures actually originate. After per-doc code-level investigation on 2026-08-23 (see the sub-section notes), **all three remaining doc-level residuals originate outside the readiness-scoring logic** — `de` in upstream document authoring (rasterized content before AksharaMD saw the file), `4c` and `simple2` in the PDF parser's block-formation stage. In all three cases the scorer's detectors behave correctly on the blocks they receive; the block sequence is well-formed and multi-column ordering is correct. This is materially relevant to the certified-parser direction, because it makes the residual failures parser-dependent — exactly the class per-parser certification exists to characterize.

### 5.1 `de` — content-bearing rasterized regions on text-classified pages · **Severity: MEDIUM**

Corrected 2026-08-23. Prior versions of this document described `de` as an extraction failure ("text silently dropped, emitted as an `asset://` image reference"). That is empirically wrong. Investigation against `aksharamd/plugins/parsers/pdf.py` (both IMAGE-block emission sites at ~L1166 and ~L2322) confirmed there is no extraction-fallback path in the parser — IMAGE blocks are only ever emitted for genuine embedded raster objects present in the source PDF (or for whole-page raster on image-only pages, which lands in the `scanned` classification and is handled by `W_IMAGE_ONLY_TEXT_BAR_FAIL`).

`de`'s content — patent SEQ ID motif tables — was rasterized into an embedded image object by the authoring tool before AksharaMD received the file. The text lives in pixels, not in the PDF's text layer. AksharaMD faithfully preserved that image object as-is.

The failure class is the same as image-only pages (content that needs a vision path to be readable), but at **region granularity** — a content-bearing image on an otherwise text-native page — rather than whole-page granularity. Existing vision routing (`W_IMAGE_ONLY_TEXT_BAR_FAIL`) covers whole-page image-only documents; it does not extend to content-bearing image regions on pages that are otherwise text-classified.

Closing this requires OCR-ing embedded images to distinguish content-bearing rasterized text from decorative figures. The false-positive boundary is load-bearing: a legitimate figure on an academic paper is also "an image on a text-classified page" and must not be flagged as omission. The distinguishing information — text inside the image — does not exist at the layer scoring runs, so no metadata-based detector on the current pipeline can cleanly separate the cases. Deferred as post-calibration parser/pipeline work.

Severity revised HIGH → MEDIUM: this is a bounded, understood routing gap (region-level vision path is not implemented), not a silent logic failure. The prior HIGH severity reflected an incorrect model of the mechanism.

### 5.2 `simple2` — mid-word block split on a first-letter font-style change · **Severity: MEDIUM**

Corrected 2026-08-23. Prior versions of this document described `simple2` as "span-level multi-column reading order." That framing was wrong twice over: the failure is not multi-column, and the fix is not a detector.

The actual mechanism is a **parser block-formation bug at mid-word font boundaries**. Investigation dump of the compiled output (see commit body for the full block-level receipts):

```
block 0: page=1  x0=85.1  y0=83.9  type=paragraph  content="**O**"
block 1: page=1  x0=96.0  y0=83.9  type=heading    content="VERVIEW"
```

The visual word "OVERVIEW" at the top of page 1 uses a first-letter font style. The parser saw the font change mid-word and split it into two blocks of different types — a bolded "O" as a paragraph, and the rest of the word promoted to a heading. Both blocks are on the same visual line (identical `y0=83.9`, adjacent x-coordinates).

Block ordering between the 9 blocks is correct: the multi-column-order validator computed `gap_rel=0.59, transition_rate=0.12, large_y_drops=1, warn=False`, and an independent cluster analysis confirmed 5 left-column blocks followed by 4 right-column blocks (transitions=1). The detector correctly does NOT warn — the block-level ordering is fine, and the scorer is behaving correctly on the blocks it received.

The bug lives in the parser's span→block promotion stage. Closing it requires a parser rule at block formation: don't split a word into differently-typed blocks on a mid-word font change. This is not a new detector or a new scoring signal.

Severity MEDIUM: the content-fidelity damage is bounded (one word visually fragmented, one spurious heading emitted). Damage does not propagate — only the styled first-letter transition is affected.

### 5.3 `4c` — cross-column span merging within blocks · **Severity: MEDIUM**

Corrected 2026-08-23. Prior versions of this document described `4c` as "span-level multi-column reading order." The reading-order framing is wrong; the actual mechanism is different.

Confirmed against the code: `c4dfe86` (PR #56, closing investigation #54) reordered `4c`'s blocks column-first. Verified on the current compiled output — the 47 positional blocks form a column-first sequence with `transitions=1, longest_same_run=33` (14 left-cluster blocks followed by 33 right-cluster blocks). The multi-column-order validator computed `gap_rel=0.16, transition_rate=0.02, large_y_drops=2, warn=False` and correctly does NOT warn. Block-level reading order is textbook correct.

The actual failure is **cross-column span merging within blocks**. The parser's span-clustering stage merges spans from adjacent columns into a single block when their y-coordinates overlap. Smoking gun — block 15 of the compiled output, verbatim:

```
**of Pharmacy Regula-** *Mike Davis **tory Authori
```

That one block contains three fragments — `"of Pharmacy Regula-"`, `"Mike Davis"`, `"tory Authori"` — from three different columns of the source PDF, joined into a single paragraph because their spans overlap in y. The block-level detector operates on already-formed blocks and correctly reports they're column-ordered; it has no way to see the within-block merge.

Closing this requires span-clustering discipline at block formation: don't merge spans across distinct x-clusters even when their y-coordinates overlap. This is a parser rule, not a detector.

Severity MEDIUM: the damage is more severe per affected block than `simple2` — merged content is genuinely nonsensical to a downstream reader — but bounded to blocks where cross-column content shares a y-band. The mechanism is understood and localized to the parser's span-clustering pass.

### 5.4 Document classifier — cannot distinguish multi-column text from real tables · **Severity: HIGH (systemic)**

This one is broader than a single document and is the most important finding of the phase.

`pdf_classification` mislabels 8 of 15 table-heavy documents. Root cause is structural, not a tuning miss: the classifier's table signal counts only successfully extracted tables, so a table-heavy document whose table failed to extract can never be recognized as table-heavy — blind in exactly the silent-failure case the system exists to catch. The archetype signal designed as the backstop for that case is fed by the same broken input, so the safety net has a hole shaped like the thing it is meant to catch.

We attempted the obvious fix (count rejected table candidates too) and proved it cannot work as scoped: at the parse layer, a multi-column text layout and a real table are the same object — 14 of 15 false positives share rejection reason codes (`word_split`, `too_few_cols`) with genuine tables. The information needed to tell them apart does not exist at the parse layer; it only exists in downstream signals, and using those recreates the circular dependency the fix was meant to break. The separation analysis is preserved as evidence (issue #117).

Why the dev-split table results still pass despite this: each misclassified document happens to be caught by a *different* overlapping page-level detector (leader dots on one, the SERFF substantiality guard on another, caption+numeric on a third). That is coincidental redundancy, not a designed guarantee. On a broader corpus, documents in this class where none of those detectors happen to fire will be silent failures. A proper fix requires exposing finer per-candidate features from the parser (cell fill ratio, numeric-token density within cells, column-width regularity) to separate text-columns from data-columns; deferred as post-calibration parser work.

---

## 6. Recommended ingestion policy — choose for your own risk tolerance

This verdict deliberately does not prescribe a single band-to-action rule, because the right rule depends on how costly a wrong answer is in your setting, and that is yours to weigh, not ours to assume. A team indexing internal blog posts and a team ingesting regulatory filings should not use the same threshold.

What the verdict *does* give you is the honest input to that decision: a HIGH score currently still carries a non-zero chance of the four failures in Section 5. Set your policy accordingly.

**The tradeoff, stated once, plainly:**

- The more you auto-ingest without a human, the more throughput you get and the more exposure you take to the Section 5 blind spots. Auto-ingesting HIGH unattended is fastest and cheapest, and it will let some Section-5 failures through, because the scorer cannot yet see them.
- The more you route to human review, the safer you are and the slower and costlier ingestion becomes. Sending everything below HIGH — or everything, until the gates pass — to a reviewer eliminates most exposure at the cost of a person in the loop.

To choose your own point on that tradeoff, ask:

1. **What does one silent failure cost me?** If a single wrong answer is expensive (legal, medical, financial), weight toward human review, and do not auto-ingest any band unattended until the gates pass. If wrong answers are cheap and recoverable, auto-ingesting HIGH may be acceptable today.
2. **Does my corpus resemble the Section 5 documents?** Rasterized content on text-classified pages (5.1), documents that exercise mid-word font-style boundaries or overlapping-y multi-column layouts that trip the parser's block-formation stage (5.2/5.3), and table-heavy documents (5.4) are where the risk concentrates. A corpus of clean single-column native-text PDFs is far less exposed than one full of scanned actuarial tables or authoring-tool-generated PDFs with rasterized content.
3. **Do I have a human backstop available at all?** If not, the honest posture today is to treat sub-HIGH as "hold" rather than "drop," and to sample-audit HIGH, because unattended full trust is not yet earned.

Whatever rule you pick, record it, and re-evaluate it when the gates pass. The scorer's job is to give you an honest signal; the policy that acts on that signal is a decision this document equips you to make, not one it makes for you.

---

## 7. Sealed splits

The locked and challenge splits were deliberately not opened during this or any prior phase. They exist to provide one honest measurement of how the scorer generalizes to documents it has never been tuned against. That measurement is only meaningful once — the first time the splits are opened, their independence is spent.

Because the development split still fails both gates, opening the sealed splits now would spend that one-time measurement on a run whose outcome is already known to fail. They remain sealed by design. They should be opened only when a development-split run is projected to clear both gates — not before.

This is a feature of the process, not an omission. A calibration verdict that reported sealed-split numbers today would have destroyed the very independence that makes those numbers worth having.

---

## 8. Path to certification

In priority order, what stands between today and an unattended-production-certified score:

1. **Extend vision-routing to content-bearing image regions (5.1, `de`).** Adds region-level coverage to the existing whole-page vision path: OCR embedded images at parse time and, when a large fraction of an image's OCR text is present on a text-classified page, treat the page as needing vision-mode consumption rather than trusting the text layer alone. Complements `W_IMAGE_ONLY_TEXT_BAR_FAIL` (whole-page); the distinguishing information (text inside embedded images) requires new parser-layer surface area, not a signal on existing metadata.
2. **Parser candidate-feature exposure (5.4, classifier).** Systemic. Requires surfacing finer per-candidate features from the parse layer so the classifier can separate text-columns from data-columns without circular dependence on downstream signals. This one is load-bearing for the whole table tier — until it lands, table-tier passes rest on coincidental redundancy.
3. **Parser span-clustering discipline (5.3, `4c`).** Rule at the parser's span→block promotion stage: do not merge spans across distinct x-clusters when their y-coordinates overlap. Fix lives in `aksharamd/plugins/parsers/pdf.py` block formation, not in a new detector. Post-2026-08-23 code investigation confirmed the block-level ordering is already correct after PR #56; the corruption is within blocks.
4. **Parser mid-word font-boundary handling (5.2, `simple2`).** Rule at the same block-formation stage: do not split a word into differently-typed blocks on a mid-word font-style change (drop-cap / first-letter styling). Also lives in `pdf.py`, also not a detector. Separate from (3) — different mechanism, different fix path.

(Items 3 and 4 were previously collapsed into a single "span-level multi-column detection" track. Post-2026-08-23 investigation showed neither failure is multi-column and neither fix is a detector; both are parser block-formation rules.)
4. **Re-run the development split.** Only when 1–3 are projected to bring both gates green.
5. **Open the sealed splits** (locked, then challenge) for the one clean generalization read. If they pass → certify. If not, the gap between dev and sealed performance is itself the next finding.

Until step 5 passes cleanly, the honest external statement is: the AI Readiness Score is a materially improved, stable extraction-reliability signal with four documented blind spots, not yet certified for unattended gating.

---

## Appendix — evidence and provenance

- **Gate numbers:** Phase 4 v2 development-split re-run on main @ `5ea6ceb`.
- **Controls:** `battery`, `2colmercedes`, `docusigned`, `gridofnumbers`, `webprint` — band and warning set unchanged across all Phase 4 changes.
- **Classifier separation analysis (5.4):** rejection-reason overlap table (14/15 false positives share reason codes with real tables); preserved in issue #117.
- **Rejected naive fix (5.4):** branch `wip/117-naive-classifier-fix-DO-NOT-MERGE`, retained for reference, not merged.
- **Deferred design tracks (all confirmed as parser-side or upstream-side after 2026-08-23 code investigation):** `de` region-level vision routing / embedded-image OCR (new; supersedes the earlier "content-omission" framing after code review confirmed the parser has no extraction-fallback path); `4c` parser span-clustering discipline (fix in `pdf.py` block formation — supersedes the earlier "span-level multi-column detection" framing); `simple2` parser mid-word font-boundary handling (also in `pdf.py` block formation — was previously conflated with `4c` in the same span-level track); parser candidate-feature exposure (issue #117).
- **Sealed splits:** locked and challenge — unopened as of this verdict.

This verdict measures extraction reliability on a development corpus. Consistent with the project's stated limits, a high Readiness Score means text was extracted cleanly; it does not guarantee retrieval accuracy or final answer correctness. Run end-to-end retrieval evaluation against your own queries before production deployment.
