# V1 claims closure: unresolved results retained

**2026-09-22.** This closes the reporting gap for issue #220; it does not
declare V1 scientifically complete or establish product acceptance. Claim numbers
and endpoints follow [the frozen manifest, section 4](STUDY_FREEZE_MANIFEST_V1.md).
Original benchmark outputs and historical deviation entries are preserved.
No parser, scorer, LLM evaluation, or benchmark aggregation was rerun for this report.

| Frozen claim | Disposition | Evidence boundary |
|---|---|---|
| 1: detector agreement, Track A | **PROVISIONAL_LEGACY_V1** | olmOCR summaries use legacy scored records; strict provenance completeness fails. The implementation selects only `aksharamd-reference`, not all parsers. |
| 2: fidelity-score monotonicity, Track A | **PROVISIONAL_LEGACY_V1**, with unavailable components below | olmOCR assertion pass-rate correlations are provisional. DocLayNet structural association accuracy was not measured by the output-presence proxy. |
| 3: usability validity, Track B | **UNMEASURED** | No authorized allocation or human usability outcomes in the inspected evidence. Band usability and false-safe rate are not estimated. |
| 4: consequential validity, Track C | **NOT_ESTABLISHED** | Frozen EM-degradation analysis does not establish predictive validity; exploratory endpoints cannot replace it. |

Claim 3 remains human usability: in particular, false-safe rate is
`P(reviewer judges not usable | readiness >= 70)`. DocLayNet extraction is not
Claim 3. No claim here licenses semantic-preservation or safe-auto-ingestion promises.

## Evidence identity and reproducibility

[V1_CLOSURE_EVIDENCE.json](V1_CLOSURE_EVIDENCE.json) commits selected aggregate
fields, original local artifact paths, and SHA-256 hashes. It contains the counts,
intervals, and correlations cited here without distributing document corpora.
[extract_v1_closure_evidence.py](extract_v1_closure_evidence.py) reproduces that
selection from the original artifacts into a **new** file. Those full artifacts
are local research evidence, not included in a clean checkout; this is an auditable
transcription, not an independently replayable corpus release. The selector section
is a retained reconciliation result, not a new selector execution.

The inspected implementation is commit
`76d5e89cd35309e8a375e844aaa84e3b1b6a21d4`. Current-byte hashes and the inspection
commit do not prove missing historical producer commits, parser versions,
configurations, or generation receipts. A top-level v3 contract name does not
upgrade legacy contained records. The olmOCR timestamp `2026-09-22T03:22:47.166239+00:00`
is September 21 at 20:22 PDT; both date labels refer to the same instant.

## Claims 1 and 2: olmOCR results remain provisional

The September 22 aggregate reports 5,876 raw result files, 264 duplicate files
removed, **5,612 unique pairs**, and **5,587 SCORED rows**. Strict audit reports
5,612 expected and observed pairs, **21 terminal and 5,591 nonterminal**, with
zero missing or unexpected pairs. SCORED and strict-terminal are different
classifications; 21 is not the number remaining to anchor. The historical D-006
count of 6,904 cannot be substituted for this aggregate's unique scored denominator.

The six reference-parser detector summaries have zero observed fires. Their
`n_in_scope` counts are 231 (multicolumn), 95 (encoding), 154 (dropped content),
263 (header/footer/table), 9 (gibberish), and 187 (table missing). Precision is
undefined with no fires; gibberish recall is also undefined with no positive GT.
The retained JSON provides confusion counts and Wilson intervals. These category
and assertion mappings are imperfect failure-mode labels; this is not all-parser
detection performance or a source-aware detector validation.

Provisional Claim 2 pooled Spearman correlations with assertion pass-rate are:

| Parser | Scored pairs | rho | 95% bootstrap CI |
|---|---:|---:|---|
| aksharamd-reference | 1,393 | 0.095101 | [0.041034, 0.148773] |
| Docling | 1,403 | 0.037242 | [-0.012459, 0.083468] |
| Marker | 1,403 | -0.055391 | [-0.103192, -0.005353] |
| MarkItDown | 1,388 | -0.007944 | [-0.059722, 0.041079] |

These correlations do not establish meaningful product performance. Legacy
provenance, deduplication, assertion inventory, detector/task alignment, and
Markdown-only scoring all constrain interpretation. The allocation path is null,
so Track B remains withheld. See the appended D-007 correction in
[the deviation ledger](V1_PROTOCOL_DEVIATIONS.md).

## DocLayNet: separate Track A output-presence evidence

The September 20 aggregate contains 1,120 records for 280 selected pages and four
parsers: 1,116 executed and four Marker defects. The runner's `_gt_has_table`
accepts **Caption (1) OR Table (9)**. Its output test looks for Markdown pipe-row
or HTML table syntax. This measures whether table-like output exists on selected
source-positive pages; it does not measure all-table recovery, cell fidelity,
correct associations, or faithful extraction.

| Parser | Table-like output / executed GT-positive pages | Output-presence rate |
|---|---:|---:|
| Docling | 237 / 280 | 84.64% |
| Marker | 226 / 276 | 81.88% |
| MarkItDown | 178 / 280 | 63.57% |
| aksharamd-reference | 100 / 280 | 35.71% |

The file calls these rates `recall`; this report uses the narrower interpretation.
All executed pages are GT-positive under that proxy. There are no negative pages,
so output-presence FPR is **unmeasured**, not zero. Caption-only page prevalence
was not separately audited. Marker has a different denominator because defects
were excluded; these are not matched successful-execution comparisons.

`W_TABLE_MISSING` and `W_HEADER_FOOTER_TABLE_GARBLED` each fired zero times in
this Markdown-only experiment. Source-based detection remains unmeasured; zero
fires do not validate detector correctness or prove a source-aware detector has
zero recall. The existing detector validator also omits fired warnings on
GT-positive outputs containing table syntax from its false-positive count.
Its reported fields cannot support a future detector-precision claim unchanged.

## Claim 4 and evaluator limitations

The September 21 Track C aggregate uses the frozen
`EM(aksharamd-reference) - EM(parser)` degradation endpoint. It contains **54
documents / 158 nonreference parser-document comparisons**: QASPER 24 / 68 and
TAT-DQA 30 / 90. Pooled rho is **0.038641**, with 95% bootstrap interval
**[-0.064763, 0.123049]**, using 2,000 resamples. Expected direction was negative.
The near-zero EM outcome and compressed Markdown readiness scores constrain
interpretation. The recorded verdict is **NOT_ESTABLISHED**, not proof that
every possible fidelity signal is useless.

The separate September 20 aggregate used 10,000 bootstrap resamples and reported
a slightly different interval. This report consistently uses September 21 fields;
it does not splice Monte Carlo intervals across artifacts. Token F1/numeric EM
were added as exploratory metrics (D-001), not frozen primary endpoints. Markdown
scoring used baseline 95 rather than source-PDF baseline 87 (D-002). The
exploratory analysis covers 55 documents / 184 comparisons and uses corpus gold
as the QASPER reference, so it has a different population and estimand.

Numeric EM expects a whole-string numeric answer while the cached-answer prompt
does not require bare-number output. Explanations containing correct numbers can
therefore receive zero. D-005's sign/decimal fix does not resolve this format
mismatch. Evaluator zeros cannot all be attributed to parser failures, nor can
all failed numeric parses be awarded credit. Parser evidence, reasoning,
formatting, and answer correctness require separate adjudication before any
exploratory rescore. QASPER pilot reuse and mixed exploratory corpus metrics also
preclude treating those results as a fresh generalization study.

## Unavailable endpoints

- **DocLayNet structural association accuracy: unmeasured.** Output syntax is
  not a substitute for the frozen Claim 2 endpoint.
- **FinTabNet.c TEDS: NOT_MEASURED_V1.** Source PDFs could not meet the frozen
  provenance standard; the corpus is NOT_EXECUTABLE_V1. This is neither zero
  performance nor random missingness.
- **OmniDocBench CDM: NOT_MEASURED_V1.** Its image-only corpus was incompatible
  with the frozen PDF parser inputs and excluded before execution.
- **MMLongBench-Doc: NOT_EXECUTABLE.** No V1 adapter exists in the Track C record.
- **Track B usability and false-safe rate: UNMEASURED.** Track A results do not
  substitute for human review or an allocation/acceptance decision.

## Separately versioned follow-up, not an automatic rerun

For #218, retain original V1 JSON and D-002. Any future experiment must write
separately versioned outputs and declare its target detector, source/candidate
API, population, failure-mode labels, and acceptance rule before execution.
Nonzero activation is an instrumentation check, not validation. Independent
parser-document failure positives and negatives must define precision
`TP/(TP+FP)`, recall `TP/(TP+FN)`, and FPR `FP/(FP+TN)`, with intervals,
abstentions, defects, and coverage reported. Warnings on correctly recovered
GT-positive tables belong among potential false positives; source presence is
not an output-correctness label. Do not reuse the Caption-or-Table/output-syntax
proxy as structural truth. A bounded validation design and decision are required
before running such work; this closure authorizes no rerun or product build.

For #213, retain the historical V2 narrative but distinguish its two oracle
definitions on the **same 55 documents / 239 assessed arms**:

| Oracle | Selector top-one | Mean regret | Uniform-random expected top-one |
|---|---:|---:|---:|
| Eligible parsers only | 15/55 = 0.272727 | 0.030557 | 0.337879 |
| All cached arms, including corpus_gold | 12/55 = 0.218182 | 0.035976 | 0.289394 |

The PR's rounded 0.273/0.0306 describes the parser-only definition; it must not be
combined with the all-arm baseline or described as the all-arm result. Adding
gold changes the oracle, not the document population. Selection ranks text
retention, not a semantic-preservation scalar. Both analyses remain exploratory;
neither establishes automatic parser selection or rescues frozen Claim 4.
