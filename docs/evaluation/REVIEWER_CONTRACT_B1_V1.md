# B1 Reviewer Contract, V1

Status: **DRAFT — B1a-7a, docs only. No labeling UI, no reviewer recruitment, no adjudication runs are authorized by this document.**

Scope: This document specifies, in one place, what a reviewer is asked to do, what they see, what they do not see, how their answers are combined into a severity label, how disagreements are handled, and what remains explicitly unresolved. It is derived from PROTOCOL_V1.md §§2.1–2.4, §3.2–§3.4, §4, and Appendix B; from `benchmarks/eval_v1/adjudication.py` (question wording, blinding key); and from `benchmarks/eval_v1/mapping.v1.json` (the frozen 64-row severity mapping validated by the B1a evidence chain).

If any part of this contract conflicts with PROTOCOL_V1.md, the protocol wins and this document must be brought into sync.

---

## 1. Purpose and non-goals

**Purpose.** Define the labeling contract used by B1 human reviewers so that:

- The severity label assigned to a `(document, parser)` pair is a function of prewritten questions and a frozen mapping table, not of reviewer judgment about "how bad it looks."
- Every reviewer sees the same source material, the same extracted material, and the same three questions, in a form blinded to any signal that would leak the study's own hypothesis.
- Corpus-specific ground-truth strength (G1 textual, G1 structural, G2 adjudication-only) is presented honestly rather than flattened into a single "truth" surface.
- Disagreements are resolved by a preregistered procedure, not by discussion between reviewers after the fact.
- Post-hoc rubric changes are prohibited; the mapping's cryptographic hash is the anchor.

**Non-goals of this document.**

- It does not specify the labeling UI or its implementation. That is B1a-7b.
- It does not recruit reviewers, procure a labeling platform, or commit budget. That is out of B1a-7a.
- It does not authorize any B1 held-out run. That requires B1a-7c and separate go-ahead.
- It does not redefine detectors, thresholds, or ground-truth tiers. Those live in PROTOCOL_V1.md.

---

## 2. Frozen references (identifiers and hashes)

The reviewer contract binds to the following artifacts by identifier and content hash. A change in any of these hashes invalidates every label produced under this contract.

| Artifact | Location | Identifier | Content hash |
|---|---|---|---|
| Question set | `benchmarks/eval_v1/adjudication.py` :: `REVIEWER_QUESTIONS` | Q1_coverage / Q2_fidelity / Q3_downstream_usability | verbatim strings reproduced in §5 below |
| Severity mapping | `benchmarks/eval_v1/mapping.v1.json` | `mapping_id = "appendix_b_v1"`, `version = "v1"`, `mapping_frozen = true` | canonical-JSON SHA-256 `99248be55e23f3a8da914cda07f465c2ff529ab451c47e4f9db7232338cf08a9`; raw-bytes SHA-256 `f4c634455c422d95083a2e3599c2eddb48a0cee09beaf041532be878eeece4cb` |
| Blinding key | `benchmarks/eval_v1/adjudication.py` :: `_blind_parser` | `sha256(parser_name)[:16]` | function is normative; if parser namespace changes, the mapping from hash → parser must be regenerated under the new namespace and archived |
| Protocol document | `docs/evaluation/PROTOCOL_V1.md` | PROTOCOL_V1 | this contract is derivative; sections §2.1–2.4, §3.2–§3.4, §4 govern |

The mapping SHAs above are the **canonical-JSON** SHA (structural anchor, independent of formatting) and the **raw-bytes** SHA (file-integrity anchor). Both must match at label time; if either drifts, no label produced under this contract is valid.

---

## 3. Reviewer population, affiliation, and independence

### 3.1 Population

Two independent reviewers per `(document, parser)` pair. The B1 held-out run uses **external** reviewers, blinded per §4. Project-affiliated reviewers may label only the ~20-example DEV training/pilot set (§3.3 of the protocol) and their pilot labels are **not** eligible to contribute to any held-out result.

**Unresolved:** whether the B1 pilot itself (as distinct from the DEV training set and from the eventual held-out run) is labeled by project-affiliated reviewers, by external reviewers, or by both. This decision must be made explicitly before B1a-7c study-freeze readiness, not silently at platform-signup time.

### 3.2 Affiliation disclosure

Each label emitted under this contract carries a `reviewer_affiliation` field with one of exactly two values:

- `project_affiliated` — the reviewer has read, contributed to, or reviewed AksharaMD source code, protocol, or scoring policy, or is otherwise informed about the study's hypothesis at greater than a public-README level.
- `external_independent` — the reviewer has none of the above and has been recruited through an arms-length channel.

Affiliation is self-attested at reviewer onboarding and re-attested per session. The IAA and validity calculations in §9 partition results by affiliation and never merge them.

### 3.3 Independence

- Reviewers work on separate machines. No co-located sessions.
- Reviewers do not discuss any specific pair before both have submitted their labels for that pair.
- Reviewers may not solicit or receive hints, spoilers, or "sanity checks" from any project contributor between onboarding and label submission.

**Unresolved:** the mechanism by which reviewer independence is *verified* rather than merely attested (e.g., timestamped session logs, distinct network origins, questionnaire spot-checks). B1a-7b must state whether this is enforced, sampled, or trusted.

---

## 4. Blinding

At label time the reviewer sees:

- The source PDF (see §6 for exact rendering).
- The extracted markdown for one parser (see §6).
- The three prewritten questions (§5) and the four answer values per question.

At label time the reviewer does **not** see, on any surface:

- Parser identity. The parser is presented under an opaque hash `sha256(parser_name)[:16]` computed by `_blind_parser` in `adjudication.py`.
- AksharaMD's readiness score, severity band, or any warning-code list.
- Any AksharaMD-emitted diagnostic, marker, or annotation embedded in the extracted markdown.
- The severity label AksharaMD would predict.
- Any other parser's output for the same document (labeling is strictly per `(document, parser)`; no cross-parser comparison at label time).
- Any benchmark score attached to the parser under any other evaluation.

Document identity is blinded where operationally feasible; PMC accession IDs, DocLayNet page hashes, and Federal Register document numbers are stripped from the reviewer surface when they are removable without impairing the reviewer's ability to compare source and extraction. Where document identity cannot be practically stripped (e.g., a title page is unavoidably in the rendered PDF), that must be recorded per pair, not silently permitted.

**Order randomization.** Document order is randomized per reviewer to avoid position bias. Randomization is seeded from a session-scoped RNG and the seed is stored with the label session.

---

## 5. The three questions (verbatim)

The reviewer answers three prewritten questions. The wording is normative and must be presented character-for-character as in `benchmarks/eval_v1/adjudication.py :: REVIEWER_QUESTIONS`:

**Q1 — Coverage:**
> Does the markdown appear to contain substantially all of the source's textual content? Answer: yes / mostly / partially / no.

**Q2 — Fidelity:**
> Where content is present, is it recognizably faithful to the source, or are there stubs, gibberish, or corruptions? Answer: faithful / minor issues / significant corruption / mostly stub or junk.

**Q3 — Downstream usability:**
> If this markdown were the sole input to a RAG query system for this document, would answers to typical questions be usable, degraded, or wrong? Answer: usable / usable-with-caveats / degraded / wrong.

The answer values are ordinal: index 0 is best, index 3 is worst.

- Q1 values (ordered best → worst): `yes`, `mostly`, `partially`, `no`.
- Q2 values (ordered best → worst): `faithful`, `minor issues`, `significant corruption`, `mostly stub or junk`.
- Q3 values (ordered best → worst): `usable`, `usable-with-caveats`, `degraded`, `wrong`.

Any reordering, rewording, or value substitution invalidates the label and requires a new mapping under a new `mapping_id`, not a patch of the current one.

### 5.1 Per-value examples

The examples below are calibration anchors for reviewer training. They are illustrative, not exhaustive. Reviewers are trained on the DEV set (§3.3 of the protocol) until they reach ≥ 80% agreement with a gold-standard label set before their scores count.

**Q1 — Coverage:**

- **yes.** Every heading, every body paragraph, every table caption, every figure caption, and every list item present in the source is present in the markdown. A missing running header or a missing page number does not disqualify "yes."
- **mostly.** One or two body paragraphs missing, or a footnote block missing, or one figure caption dropped. The reader would notice on careful comparison but not on skim.
- **partially.** A visible fraction (roughly 25%–75%) of body content is missing: e.g., an entire section, or a table missing, or a multi-page appendix truncated.
- **no.** Most of the source content is not in the markdown. Cover-and-toc-only extraction, or output truncated after the first page, or a document reduced to bullet points that omit the substance.

**Q2 — Fidelity:**

- **faithful.** Where content is present, it reads as the source reads. Word choice, sentence structure, and numeric values match. Ligatures resolved correctly. Table cells align to the right columns.
- **minor issues.** Occasional ligature glitches (`ﬁ` → `fi` failed), rare mis-hyphenation across line breaks, a footnote marker rendered as a stray digit. A reader can reconstruct the source without looking at it.
- **significant corruption.** Whole sentences with mangled word order, table cells swapped between columns, encoding artifacts (`—` → `â€"`, `→` → `?`) recurring throughout. A reader cannot trust the transcription without cross-checking.
- **mostly stub or junk.** Body replaced with `[OMITTED]`, `<TABLE>`, `...`, or by a parser-generated summary; large runs of `�`; or output that is a template of section headings with no substance.

**Q3 — Downstream usability:**

- **usable.** A RAG system prompted with typical questions ("What does §2 say about X?", "What is the value in row 4 column 3 of Table 2?") would answer correctly using this markdown alone.
- **usable-with-caveats.** Typical questions are answerable, but questions about a specific missing figure caption or a specific column of a lost table would fail. A user would notice degradation on a minority of queries.
- **degraded.** Many typical questions produce wrong or incomplete answers; the RAG system would need to be routed to a different extraction to be useful.
- **wrong.** A RAG system answering from this markdown would confidently produce incorrect answers on the majority of typical questions.

Reviewers do not calibrate examples against each other. Each example set is anchored in the source; the reviewer's task is not to compare parsers but to answer three questions about one `(document, parser)` pair.

---

## 6. What the reviewer sees per pair

The reviewer's surface for a single `(document, parser)` pair contains, and only contains:

1. The source PDF, rendered as page images at a resolution sufficient for a reader to distinguish body text from figure captions and to read table cell contents at 100% zoom.
2. The extracted markdown for one parser, rendered as text with no parser-identity chrome, no score, no warning banners, and no AksharaMD annotations. The markdown is displayed both as rendered markdown and as raw text; the reviewer may switch between them but neither displays parser identity.
3. A pair identifier (opaque hash), the three questions, and the four answer buttons per question.
4. An abstention control (see §7.1) and an ambiguity flag (see §7.2).
5. A timing surface (see §8).

The reviewer does **not** see:

- Parser identity (the `sha256(parser_name)[:16]` hash is stored in the label record but is not shown to the reviewer during the labeling task, unless a specific adjudication task under §4.3 of the protocol requires it).
- Any label the other reviewer has emitted (labels are gated per pair per reviewer).
- Any previous labels the same reviewer emitted for the same document under a different parser (per-reviewer randomization prevents this from being available on the same screen).

**Unresolved:** whether reviewers may access the source PDF's document identity metadata (title, author, DOI, docket number) when it is embedded in the rendered PDF itself. Stripping title pages is not always sensible. B1a-7b must decide whether embedded titles are (a) permitted, (b) redacted at render time, or (c) permitted with a per-pair marker.

---

## 7. Abstention and ambiguity

### 7.1 Abstention

A reviewer may abstain on a `(document, parser)` pair. Abstention is one of exactly the following reasons, recorded verbatim in the label record:

- `source_illegible` — the source PDF is unreadable at the resolution provided (e.g., a scanned document rendered at insufficient DPI, or a page that failed to render).
- `extraction_indeterminate` — the markdown is present but the reviewer cannot compare it to the source (e.g., markdown is in a language the reviewer does not read and no scoped section is comparable).
- `reviewer_conflict_of_interest` — the reviewer has recognized the document or parser and cannot uphold blinding.
- `technical_failure` — a labeling-platform failure prevented the reviewer from completing the pair.

An abstention is **not** a label. It removes the pair from that reviewer's count and requires a replacement reviewer. If both reviewers abstain, the pair is escalated to the adjudicator (§9.3) with an explicit note that no primary labels exist.

**Unresolved:** whether the pair is retained in the study population when both reviewers abstain, or excluded. B1a-7c must decide, before study freeze, whether "both abstained" pairs count against the sample size or are replaced.

### 7.2 Ambiguity flag

Distinct from abstention: a reviewer may submit a label **and** raise an ambiguity flag, indicating that they were forced to choose between two adjacent answer values on one or more questions and are not confident in the choice. Ambiguity flags do not change the label produced; they are recorded and used in the sensitivity analysis described in §9.4. The reviewer is not asked to explain the ambiguity in free text at label time (free-text explanations are prohibited on the primary label surface to reduce blinding leakage).

---

## 8. Timing collection

Every label event carries the following timing fields:

- `session_id` — opaque per-labeling-session identifier.
- `pair_first_shown_at` — timestamp at which the pair was rendered to the reviewer.
- `first_interaction_at` — timestamp of the reviewer's first click or keypress on the pair surface.
- `label_submitted_at` — timestamp at which the reviewer submitted final answers.
- `time_on_pair_seconds` — `label_submitted_at − pair_first_shown_at`, monotonic per pair.

Purpose: to detect pairs completed too quickly to have been read (pathological floor cases) and pairs completed too slowly to have been done in a single session (probable multi-session or unblinded lookup). No individual label is rejected on timing alone; §9.4 describes how outlier timing feeds sensitivity analysis.

**Unresolved:** the specific floor and ceiling for "acceptable" `time_on_pair_seconds`. These must be set in B1a-7c based on DEV-set observed distributions, not chosen by hand.

---

## 9. Disagreement handling, IAA, and adjudication

### 9.1 Deterministic label derivation

Given a reviewer's (Q1, Q2, Q3) answers, the severity label is derived by:

    label = severity_labels[max(q1_rank, q2_rank, q3_rank)]

where each `qN_rank` is the position (0..3) of the answer value in that question's ordered value list, and `severity_labels = ["GOOD", "MINOR", "MAJOR", "CATASTROPHIC"]`.

The full 64-row (`4 × 4 × 4`) truth table is `benchmarks/eval_v1/mapping.v1.json` (`mapping_id = "appendix_b_v1"`, `version = "v1"`, `mapping_frozen = true`). Its canonical-JSON SHA-256 is `99248be55e23f3a8da914cda07f465c2ff529ab451c47e4f9db7232338cf08a9`. The derivation is not a heuristic; it is a table lookup, and the table is frozen.

Distribution across the 64 rows: 1 GOOD, 7 MINOR, 19 MAJOR, 37 CATASTROPHIC.

### 9.2 Pair-level reconciliation (two-reviewer rule)

For each pair, compute the two reviewer labels independently via §9.1.

- If the two labels are identical → that label is the pair label.
- If the two labels differ **at all** (≥ 1 severity level, in either direction) → the pair is escalated to a blinded third adjudicator (§9.3).

The reconciliation rule does **not** mechanically select the worse (or better) of two disagreeing primary labels. Selecting the more severe label on adjacent disagreement would introduce a systematic one-way bias into the human criterion layer that is not established in PROTOCOL_V1 or Appendix B. Instead, every disagreement — including one-level disagreements — is resolved by an independent third reviewer under the same blinding.

Primary labels are always retained independently in the label record. They are the input to IAA (§9.4) and to any sensitivity analysis; they are never overwritten or discarded when a pair goes to adjudication.

### 9.3 Third-adjudicator procedure

Escalated pairs are sent to a single trained adjudicator (not one of the two primary reviewers) under the same blinding as §4. The adjudicator answers the same three questions from §5 independently. The adjudicator's derived label is the pair label. The two primary labels are retained in the label record but are not used to compute the pair label.

The adjudicator does not see either primary reviewer's answers before submitting their own.

Because §9.2 escalates every non-identical disagreement, the adjudication caseload is a study-cost parameter, not a rare edge case. B1a-7c must budget for it explicitly.

### 9.4 Inter-annotator agreement (IAA)

Cohen's κ is computed on the per-reviewer derived label (from §9.1) — not on the raw Q1/Q2/Q3 answers, and not on the reconciled pair label. κ is reported:

- On the four-way severity label (GOOD / MINOR / MAJOR / CATASTROPHIC), weighted by adjacent-severity ordinal distance.
- Overall across the held-out corpus.
- Partitioned by corpus (PMC-OA, DocLayNet, Federal Register).
- Partitioned by reviewer affiliation (project-affiliated vs. external-independent), never merged.

**Target:** κ ≥ 0.7 on the pilot set. If pilot κ < 0.7, the rubric is not "adjusted per-pair"; the training material is refined, reviewers are retrained, and the pilot is re-run. The mapping table itself is not touched.

Ambiguity flags (§7.2) and time-on-pair outliers (§8) do not enter the κ calculation directly. They feed a **sensitivity analysis** that recomputes κ, corpus-conditional agreement, and the fraction of severe-label pairs after excluding flagged / outlier pairs. If the sensitivity analysis materially changes the study's conclusions, that is disclosed in the report; the primary numbers are still those computed over the full non-abstained set.

---

## 10. Corpus-specific ground-truth presentation

The three B1 corpora carry different strengths of ground truth. The reviewer-facing surface must not present them as interchangeable.

### 10.1 PMC-OA — textual G1

For a PMC-OA `(document, parser)` pair, the reviewer sees the source PDF and the parser's markdown. The **textual G1 anchor** for this document is the canonical body text derived from JATS-XML via the same transformation used by the V2 population's `apply_text_oracle_filters` predicate. This anchor is what the mechanical detector `W_DROPPED_CONTENT` is measured against (protocol §4.1) and it is what the study's textual-omission claim rests on.

The reviewer surface **may** display the JATS-derived canonical body text as a third pane alongside the source PDF and the parser markdown, presented as *"authoritative body text from the article's XML — use as reference for Q1/Q2 where content is textual."* This is a G1 surface: it is not a reviewer's opinion of what the source says, it is what the source says.

The reviewer must be told, in on-screen instructions, that:

- The JATS body text is authoritative for **textual content only**. It is not authoritative for layout, figure rendering, table cell layout, or reading order.
- Q1 and Q2 for PMC-OA should be answered against the JATS anchor where content is textual. Q3 remains a judgment about downstream usability.

**Unresolved:** whether the JATS pane is (a) always shown, (b) shown on demand, or (c) reserved for adjudication only. B1a-7b must decide and record the decision, not choose silently.

### 10.2 DocLayNet — structural G1

For a DocLayNet `(document, parser)` pair, the reviewer sees the source PDF page and the parser's markdown for that page. The **structural G1 anchor** is DocLayNet's per-page region annotation: page-header, page-footer, table region, figure region, list, section-header, and body-text regions, each with a bounding box.

The reviewer surface **may** overlay DocLayNet region bboxes on the source PDF pane, color-coded by category. This is a G1 surface for structural presence and localization: a `Table` region either is annotated at those coordinates or is not.

The reviewer must be told:

- DocLayNet is authoritative for **structural presence and geometric localization** only. It is **not authoritative** for reading order (PROTOCOL §2.4 caveat 4) and it does **not** annotate table cell structure (PROTOCOL §2.4 caveat 5 — TEDS is not supported by this evidence).
- Q1 and Q2 for DocLayNet should be answered against structural presence: a missing `Table` region in the markdown when DocLayNet annotates one is a coverage miss; a garbled table where DocLayNet places the table region is a fidelity miss. Reading-order judgments are geometric-proxy adjudications (PROTOCOL §4.5), not G1.

### 10.3 Federal Register — G2 adjudication support only

For a Federal Register `(document, parser)` pair, the reviewer sees the source PDF and the parser's markdown. There is **no G1 anchor**. The Federal Register corpus is the study's **false-positive-rate baseline**: on clean natively-authored PDFs, the three new content-axis detectors should not fire. Its role at label time is different.

Where a Federal Register pair enters human adjudication (e.g., because a detector fired and the study needs to know whether the firing is a true detection or an FPR event), the reviewer answers the same Q1/Q2/Q3 from §5. This is a **G2 adjudicated criterion**, not a G1 verification. The reviewer's judgment is the ground truth for that pair.

The reviewer-facing surface for Federal Register **must** display, in on-screen instructions:

> This document has no external oracle. Your Q1/Q2/Q3 answers are the criterion of record for this pair. There is no independent reference against which to check your judgment.

The report writeup **must** describe Federal Register labels as G2 adjudicated and must not equate them with PMC-OA textual labels or DocLayNet structural labels. The UI is prohibited from presenting the three corpora under a single "ground truth" surface; a mixed corpus view must display per-pair the corpus and its ground-truth tier.

**Unresolved:** whether a Federal Register pair with no detector firings is presented to reviewers at all in B1 (as a "healthy-baseline calibration" pair) or only detector-firing pairs are adjudicated. B1a-7c must resolve this before study freeze.

---

## 11. Post-hoc rubric-change prohibition

Changes made **after** any B1 label is emitted under this contract fall into two disjoint version domains. `mapping_id` identifies the Q1/Q2/Q3 → severity mapping; it is not a proxy for reviewer operations. Confusing the two would either force a mapping bump for every operational tweak (weakening the mapping identity as an integrity anchor) or allow operational drift to happen silently under the same `mapping_id` (weakening the reviewer-operations record). Both are prohibited.

### 11.1 Changes that require a new `mapping_id`

The following change the mapping itself and require a new `mapping_id` (not a `version` bump under `appendix_b_v1`):

- Changing the wording of Q1, Q2, or Q3.
- Changing the answer values or their ordering (per question).
- Changing the derivation formula in §9.1 (`label = severity_labels[max(q1_rank, q2_rank, q3_rank)]`).
- Changing any cell of the 64-row mapping table.

A change of this kind invalidates every previously emitted label mechanically: the (Q1, Q2, Q3) → label function is not the same function anymore.

### 11.2 Changes that require a new reviewer-contract version

The following change reviewer operations while leaving the (Q1, Q2, Q3) → label mapping identical. They require a new **reviewer-contract version** (this document, bumped) and **not** a new `mapping_id`:

- Changing the reconciliation rule in §9.2 or the adjudication procedure in §9.3.
- Changing the blinding surface in §4 (either to reveal previously-hidden signals or to hide previously-visible ones).
- Changing the population, affiliation, or independence rules in §3.
- Changing the source/extraction surface in §6.
- Changing abstention semantics (§7.1) or ambiguity-flag semantics (§7.2).
- Changing timing collection or the fields recorded per label in §12.
- Changing corpus-specific ground-truth presentation in §10 (e.g., turning the PMC JATS pane from on-demand to always-shown after labels have been emitted under the earlier policy).
- Retroactively re-partitioning labels by affiliation, corpus, or time such that some labels are silently dropped.

### 11.3 Consequences shared by both change classes

Any change under §11.1 or §11.2 requires all of:

1. A new artifact identifier: a new `mapping_id` for §11.1 changes, or a new reviewer-contract version for §11.2 changes.
2. Re-labeling of the affected pairs under the new artifact.
3. An explicit note in the report distinguishing labels emitted under the old artifact from labels emitted under the new artifact. The two sets are **not** merged.

The mapping's cryptographic anchors — raw-bytes SHA `f4c634455c422d95083a2e3599c2eddb48a0cee09beaf041532be878eeece4cb` and canonical-JSON SHA `99248be55e23f3a8da914cda07f465c2ff529ab451c47e4f9db7232338cf08a9` — are recorded in every label record. A drift in either SHA at label time is a fail-closed condition; no label may be emitted against a mismatched mapping. This anchor secures §11.1 changes. §11.2 changes are secured by the `reviewer_contract_version` field recorded on each label (see §12), which is the reviewer-contract counterpart to the mapping SHAs.

---

## 12. Label record schema (informative)

Each emitted label carries at least:

- `pair_id` — opaque per-pair identifier.
- `canonical_id` — document canonical id (as in the manifest).
- `corpus` — one of `pmc_oa`, `doclaynet`, `federal_register`.
- `blinded_parser_hash` — `sha256(parser_name)[:16]`.
- `reviewer_id` — opaque per-reviewer identifier.
- `reviewer_affiliation` — `project_affiliated` | `external_independent`.
- `session_id` — per-session identifier.
- `q1`, `q2`, `q3` — answer values from §5.
- `derived_label` — computed per §9.1.
- `ambiguity_flag` — boolean, per §7.2.
- `abstention_reason` — one of the four values in §7.1, or `null`.
- `pair_first_shown_at`, `first_interaction_at`, `label_submitted_at`, `time_on_pair_seconds` — per §8.
- `mapping_id` — `appendix_b_v1`.
- `mapping_version` — `v1`.
- `mapping_canonical_sha256` — `99248be55e23f3a8da914cda07f465c2ff529ab451c47e4f9db7232338cf08a9`.
- `mapping_raw_sha256` — `f4c634455c422d95083a2e3599c2eddb48a0cee09beaf041532be878eeece4cb`.
- `question_set_source_sha256` — SHA-256 of the `adjudication.py` bytes at label time.
- `reviewer_contract_version` — semantic version of this document under which the label was emitted (initial value: `v1`). Bumped for §11.2 changes; not bumped for §11.1 changes (those bump `mapping_id` instead).

The exact record schema is specified by B1a-7b (labeling-execution contract); this section fixes the minimum fields required for any label produced under this contract to be admissible.

---

## 13. Explicit unresolved decisions

Collected from earlier sections for the reviewer of this document:

1. **§3.1** — Whether the B1 pilot (distinct from the DEV training set and the held-out run) is labeled by project-affiliated reviewers, external reviewers, or both.
2. **§3.3** — Whether reviewer independence is *verified* rather than merely attested, and if so how.
3. **§6** — Whether reviewers may access document identity metadata embedded in the source PDF (title pages, author blocks), or whether these are redacted at render time.
4. **§7.1** — Whether pairs where both reviewers abstain are excluded from the study population or replaced.
5. **§8** — The floor and ceiling for acceptable `time_on_pair_seconds`, to be set from DEV-set distributions in B1a-7c.
6. **§10.1** — Whether the JATS body-text pane for PMC-OA is always shown, shown on demand, or reserved for adjudication only.
7. **§10.3** — Whether Federal Register pairs with no detector firings are reviewed as healthy-baseline calibration pairs, or only detector-firing pairs are adjudicated.

Each of the above is a decision this contract deliberately does not make. B1a-7b and B1a-7c must resolve them explicitly and in writing before any B1 label is emitted.

---

## 14. Change log

- **v1 (this document, B1a-7a).** Initial draft. Docs-only. No labeling UI, no reviewer recruitment, no adjudication runs are authorized by this document.
