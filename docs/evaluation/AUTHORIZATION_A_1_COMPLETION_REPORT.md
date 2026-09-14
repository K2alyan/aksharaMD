# Authorization A.1 — Evaluation Infrastructure Completion Report

**Governing protocol:** `docs/evaluation/PROTOCOL_V1.md` (V1 DRAFT).
**Authorization:** A.1 — evaluation-infrastructure completion (staged as A.1a → A.1b → A.1c).
**Authorized:** 2026-09-14 by human review of the Authorization A smoke-test report.
**Completed:** 2026-09-14.

---

## 0. Scope statement

Authorization A.1 was permission to complete the non-methodological
measurement plumbing that Authorization A proved was missing. It
finished when the same three development documents from Authorization A
traversed every applicable V1 evaluation stage end-to-end, with every
non-EXECUTED cell carrying an explicit machine-readable reason.

- **No detector, threshold, cap value, scoring policy, parser catalog,
  or scoring code was modified** during A.1.
- **No live LLM answer, LLM judge, or downstream-RAGAS call** was
  issued. Those stages exist as infrastructure and truthfully report
  `INFRASTRUCTURE_READY_NOT_EXECUTED`.
- **The Appendix B severity mapping** was NOT invented for the 60
  off-diagonal combinations. Only the four diagonal rows Appendix B
  explicitly specifies are loaded. Any other combination resolves to
  `UNRESOLVED_MAPPING`; the adjudication stage returns
  `REQUIRES_REVIEW` rather than fabricate a label.
- **No corpus adapters were speculatively stubbed** for PMC-OA,
  DocLayNet, Federal Register/SEC, or CUAD. Only D1/D2/D3's corpora
  have concrete adapters. The `V1CorpusAdapter` ABC is the extension
  surface; the four remaining §2.2 corpora get adapters when they are
  actually exercised.
- **No pilot or held-out document population was selected.** The
  split-assignment logic ships (`assign_partition`, `hash_document`,
  `validate_no_overlap`, `record_provenance`) but no corpus is
  enumerated.
- **The same three A-era documents** were reused. D2 (DocBench, not on
  the V1 manifest) was deliberately kept so the machinery had to
  honestly report `NOT_APPLICABLE` for a non-V1 corpus's V1-GT stages.

Authorization B is not started.

## 1. Staging and acceptance

| Stage | Deliverable | Status | Artifacts |
|---|---|---|---|
| A.1a | Framework skeleton + Docling `ParserAdapter` + 3-doc rerun | Complete | `benchmarks/eval_v1/{stages,corpus_adapter,normalization}.py`, `benchmarks/parsed_vs_raw/adapters/docling_adapter.py`, `benchmarks/results/authorization-a1a-rerun-2026-09-14/` |
| A.1b | Non-LLM measurement plumbing + 3-doc rerun | Complete | `benchmarks/eval_v1/{normalization,conventional_metrics}.py`, `benchmarks/eval_v1/adapters/*.py`, `benchmarks/results/authorization-a1b-rerun-2026-09-14/` |
| A.1c | Adjudication + analysis + split logic + final rerun | Complete (this report) | `benchmarks/eval_v1/{adjudication,statistics,corpus_split,analysis_record}.py`, `benchmarks/eval_v1/mapping.v0.json`, `benchmarks/results/authorization-a1c-final-2026-09-14/` |

## 2. Final stage matrix (3 docs × 4 parsers × N stages)

| Stage | D1_qasper_1503_00841/aksharamd-reference | D1_qasper_1503_00841/marker | D1_qasper_1503_00841/docling | D1_qasper_1503_00841/markitdown | D2_docbench_P19_1598/aksharamd-reference | D2_docbench_P19_1598/marker | D2_docbench_P19_1598/docling | D2_docbench_P19_1598/markitdown | D3_tatdqa_003755794b/aksharamd-reference | D3_tatdqa_003755794b/marker | D3_tatdqa_003755794b/docling | D3_tatdqa_003755794b/markitdown |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| source_ingestion | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| parser_execution | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| output_capture | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| normalization | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| ground_truth_ingestion | EXEC | EXEC | EXEC | EXEC | N/A | N/A | N/A | N/A | EXEC | EXEC | EXEC | EXEC |
| conventional_measurement | EXEC | EXEC | EXEC | EXEC | N/A | N/A | N/A | N/A | EXEC | EXEC | EXEC | EXEC |
| llm_answer_judge | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE |
| downstream_rag | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE | IR-NE |
| aksharamd_evaluation | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| detector_diagnostics | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| adjudication | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| provenance_recording | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |
| analysis_record | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC | EXEC |

Legend: EXEC = EXECUTED · N/A = NOT_APPLICABLE · DEFECT = DEFECT · REVIEW = REQUIRES_REVIEW · IR-NE = INFRASTRUCTURE_READY_NOT_EXECUTED.

## 3. Per-stage acceptance detail

| Stage | Status counts | Non-EXECUTED reasons (unique) |
|---|---|---|
| source_ingestion | EXECUTED=12 | — |
| parser_execution | EXECUTED=12 | — |
| output_capture | EXECUTED=12 | — |
| normalization | EXECUTED=12 | — |
| ground_truth_ingestion | EXECUTED=8, NOT_APPLICABLE=4 | **N/A**: DocBench is not on the V1 corpus manifest (PROTOCOL_V1.md §2.2). |
| conventional_measurement | EXECUTED=8, NOT_APPLICABLE=4 | **N/A**: no V1 ground truth available for this corpus/document. |
| llm_answer_judge | INFRASTRUCTURE_READY_NOT_EXECUTED=12 | **IR-NE**: live model execution is outside Authorization A.1 — provider, model, version, prompts, decoding parameters, retries, judge rubric, response parsing, nondeterminism handling, and cost accounting must be frozen before this stage can run. |
| downstream_rag | INFRASTRUCTURE_READY_NOT_EXECUTED=12 | **IR-NE**: the downstream-RAG demonstration (§6.2) is a separate product-utility experiment; live execution is not authorized under A.1. |
| aksharamd_evaluation | EXECUTED=12 | — |
| detector_diagnostics | EXECUTED=12 | — |
| adjudication | EXECUTED=12 | — |
| provenance_recording | EXECUTED=12 | — |
| analysis_record | EXECUTED=12 | — |

**Zero `DEFECT` cells — A.1's acceptance criterion is met.**

## 4. Appendix B mapping — coverage & unresolved combinations

Appendix B loads **4 of 64** possible (Q1, Q2, Q3) combinations (60 unresolved). `mapping_frozen: false` in `benchmarks/eval_v1/mapping.v0.json`. Any unresolved combination that a future reviewer submits will cause the adjudication mapping step to return `REQUIRES_REVIEW`, not a fabricated label.

**Mapped (from Appendix B verbatim):**

| Q1 (Coverage) | Q2 (Fidelity) | Q3 (Usability) | Label |
|---|---|---|---|
| mostly | faithful | usable-with-caveats | MINOR |
| no | mostly stub or junk | wrong | CATASTROPHIC |
| partially | minor issues | degraded | MAJOR |
| yes | faithful | usable | GOOD |

**Unresolved combinations (60 of 64):** listed in full below; each will return `REQUIRES_REVIEW` if a reviewer submits it. Filling any of these requires methodological authorization outside A.1.

<details><summary>Full unresolved list</summary>

| Q1 | Q2 | Q3 |
|---|---|---|
| mostly | faithful | degraded |
| mostly | faithful | usable |
| mostly | faithful | wrong |
| mostly | minor issues | degraded |
| mostly | minor issues | usable |
| mostly | minor issues | usable-with-caveats |
| mostly | minor issues | wrong |
| mostly | mostly stub or junk | degraded |
| mostly | mostly stub or junk | usable |
| mostly | mostly stub or junk | usable-with-caveats |
| mostly | mostly stub or junk | wrong |
| mostly | significant corruption | degraded |
| mostly | significant corruption | usable |
| mostly | significant corruption | usable-with-caveats |
| mostly | significant corruption | wrong |
| no | faithful | degraded |
| no | faithful | usable |
| no | faithful | usable-with-caveats |
| no | faithful | wrong |
| no | minor issues | degraded |
| no | minor issues | usable |
| no | minor issues | usable-with-caveats |
| no | minor issues | wrong |
| no | mostly stub or junk | degraded |
| no | mostly stub or junk | usable |
| no | mostly stub or junk | usable-with-caveats |
| no | significant corruption | degraded |
| no | significant corruption | usable |
| no | significant corruption | usable-with-caveats |
| no | significant corruption | wrong |
| partially | faithful | degraded |
| partially | faithful | usable |
| partially | faithful | usable-with-caveats |
| partially | faithful | wrong |
| partially | minor issues | usable |
| partially | minor issues | usable-with-caveats |
| partially | minor issues | wrong |
| partially | mostly stub or junk | degraded |
| partially | mostly stub or junk | usable |
| partially | mostly stub or junk | usable-with-caveats |
| partially | mostly stub or junk | wrong |
| partially | significant corruption | degraded |
| partially | significant corruption | usable |
| partially | significant corruption | usable-with-caveats |
| partially | significant corruption | wrong |
| yes | faithful | degraded |
| yes | faithful | usable-with-caveats |
| yes | faithful | wrong |
| yes | minor issues | degraded |
| yes | minor issues | usable |
| yes | minor issues | usable-with-caveats |
| yes | minor issues | wrong |
| yes | mostly stub or junk | degraded |
| yes | mostly stub or junk | usable |
| yes | mostly stub or junk | usable-with-caveats |
| yes | mostly stub or junk | wrong |
| yes | significant corruption | degraded |
| yes | significant corruption | usable |
| yes | significant corruption | usable-with-caveats |
| yes | significant corruption | wrong |

</details>

## 5. Files shipped by A.1

**Framework and adapters:**
- `benchmarks/eval_v1/stages.py` — 5-state status vocabulary + `StageResult` invariants
- `benchmarks/eval_v1/corpus_adapter.py` — `V1CorpusAdapter` ABC + `CorpusCapabilities`
- `benchmarks/eval_v1/adapters/qasper_v1.py` — QASPER concrete adapter with QA-pair GT
- `benchmarks/eval_v1/adapters/tat_dqa_v1.py` — TAT-DQA concrete adapter with QA-pair GT
- `benchmarks/eval_v1/adapters/docbench_non_v1.py` — DocBench non-V1 NOT_APPLICABLE demonstrator
- `benchmarks/parsed_vs_raw/adapters/docling_adapter.py` — Docling `ParserAdapter` (blocker cleared)

**Measurement plumbing:**
- `benchmarks/eval_v1/normalization.py` — `UnicodeWhitespaceNormalizer`, `NORMALIZATION_VERSION="1"`
- `benchmarks/eval_v1/conventional_metrics.py` — non-LLM `word_overlap` + `number_overlap`; LLM stage stubs
- `benchmarks/eval_v1/statistics.py` — `bootstrap_by_document`; `per_observation_bootstrap_forbidden` trap
- `benchmarks/eval_v1/corpus_split.py` — `assign_partition` (SHA-256 mod 100), `hash_document`, `validate_no_overlap`, `record_provenance`

**Adjudication and analysis:**
- `benchmarks/eval_v1/mapping.v0.json` — Appendix B diagonal rows only, `mapping_frozen: false`
- `benchmarks/eval_v1/adjudication.py` — `SeverityMapper`, `prepare_reviewer_artifact`, blinding scheme, `UNRESOLVED_MAPPING`
- `benchmarks/eval_v1/analysis_record.py` — canonical per-pair analysis record schema (`v0.1`)

**Runner:**
- `benchmarks/eval_v1/smoke_run_v2.py` — per-stage `StageResult` emission

## 6. Files NOT shipped (intentional per A.1 boundary)

- `benchmarks/eval_v1/adapters/{pmc_oa,doclaynet,federal_register,cuad}.py` — speculative
- `docs/evaluation/STUDY_FREEZE_MANIFEST_V1.md` — Authorization C artifact
- `docs/evaluation/AMENDMENTS.md` — post-freeze artifact
- `docs/evaluation/POST_FREEZE_OBSERVATIONS.md` — post-freeze artifact
- Any `CORPUS_MANIFEST.md`, pilot document list, or held-out document list — B/C territory
- Additional Appendix B mapping rows beyond the four Appendix B specifies

## 7. What remains before Authorization B could be sought

Framed as items that must exist or be decided **before** B is invoked;
none of these are authorized under A.1 to resolve unilaterally.

1. **Appendix B mapping completion.** 60 off-diagonal (Q1, Q2, Q3)
   combinations are currently `UNRESOLVED_MAPPING`. A methodological
   decision by the human authorizer is required to fill them (or to
   decide that reviewers are prohibited from selecting off-diagonal
   combinations, or that off-diagonals bounce to a third adjudicator).
2. **Corpus adapters for the V1 corpora B will actually pull from.**
   Concrete adapters for PMC-OA (G1 text), DocLayNet (G1 layout), a
   clean-native FPR baseline (Federal Register or SEC), and CUAD (G1
   clause span) do not yet exist. Which corpora B pulls from is a
   design decision reserved for B's authorization.
3. **Live-LLM instrument freeze.** Provider, model, version, prompts,
   decoding parameters, retries, timeout behavior, judge rubric,
   response parsing, nondeterminism handling, and cost accounting must
   be settled before the `llm_answer_judge` and `downstream_rag` stages
   can execute.
4. **Reviewer sourcing and budget commitment.** Per PROTOCOL_V1.md
   §3.3, held-out labeling requires blinded external reviewers.
   A.1 shipped the artifact-preparation machinery; the reviewer contract
   itself is B territory.
5. **Pilot corpus population.** Which ~20 documents populate the
   methodological pilot is B's first substantive decision. A.1 has not
   selected any pilot document.

## 8. Reruns are archived

- Authorization A original: `benchmarks/results/authorization-a-smoke-2026-09-14/`
- A.1a rerun: `benchmarks/results/authorization-a1a-rerun-2026-09-14/`
- A.1b rerun: `benchmarks/results/authorization-a1b-rerun-2026-09-14/`
- A.1c final: `benchmarks/results/authorization-a1c-final-2026-09-14/`

Every run's `manifest.json`, `environment.json`, `pair_summaries.jsonl`
(or `outcomes.jsonl` for A/A.1a), and `stage_matrix.json` are preserved
as archival provenance.

## 9. Reminder from Authorization A that still applies

> No potential AksharaMD limitations were identified by the limited
> scope of Authorization A. This is not evidence of absence.

The same applies to A.1. A.1 was infrastructure construction. The 12
`aksharamd_evaluation` outputs the rerun captured are raw observations
only. Product decisions about detector calibration, cap thresholds, or
scoring policy MUST NOT be drawn from A.1 results.
