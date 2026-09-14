# Authorization A — 3-Document Evaluation-Infrastructure Smoke Test Report

**Governing protocol:** `docs/evaluation/PROTOCOL_V1.md` (V1 DRAFT).
**Authorization:** A — 3-document infrastructure smoke test (§0.1). Approved 2026-09-14.
**Author:** AksharaMD project (Claude executing).
**Date:** 2026-09-14.

---

## 0. Scope statement

This report is the output of Authorization A only. Its sole purpose is to
determine whether the V1 evaluation protocol can be executed end-to-end.

- **No benchmark claim** is made about AksharaMD's accuracy, superiority,
  readiness, or product utility.
- **No detector, threshold, cap, scoring-policy, parser catalog, or
  scoring code was touched.** No tuning was performed based on any
  observation in this smoke test.
- **The three development documents named in §2 are permanently
  development-only** and are prohibited from any future held-out V1
  evaluation subset for their originating corpora.
- **The Plan E n=59 results** (memory: `project_parsed_vs_raw_plan_e_result.md`)
  are NOT used as evidence for passing Authorization A. They came from a
  different experimental context and are preserved as prior empirical
  evidence, not as V1 observations.

Authorization B is not started. This report stops at a recommendation
and returns for human review.

---

## 1. Document manifest

Three documents, deliberately chosen to exercise different pipeline
stress axes. All hashes are SHA-256 over raw bytes.

| Doc ID | Source path | SHA-256 | Bytes | Origin corpus | V1 corpus? | Stress axis |
|---|---|---|---|---|---|---|
| D1_qasper_1503_00841 | `.cache/qasper/1503.00841-29290de61c95.pdf` | `9ca21eaff3c946e95cc649e627db3a4b9e69d0f7397bae3e30d39be495261ba5` | 956,266 | QASPER (arXiv 1503.00841) | Yes | prose |
| D2_docbench_P19_1598 | `.cache/docbench/0/P19-1598.pdf` | `1074c93b17355fc3b9175697f2cf5dbdd062a9d22f394c29856e154f91199563` | 322,546 | DocBench (ACL P19-1598) | **No** | mixed |
| D3_tatdqa_003755794b | `.cache/tat_dqa/tat_docs/dev/003755794bbbbcffd0667b9600aeb0be.pdf` | `b59bdb9725ac5345899a107f6cc894d5763521e3dbe2d5febf46ff31920d21c4` | 1,515,862 | TAT-DQA (financial report) | Yes | tables |

**Selection rationale:**

- **D1 (prose):** exercises text extraction plus content-substance
  detectors (W_GIBBERISH, W_ENCODING_ARTIFACTS, W_PLACEHOLDER_STUB) and
  the §6.2 QASPER-downstream loader seam. Excluded from held-out V1
  QASPER.
- **D2 (mixed):** ACL-style paper with equations/figures/references. Not
  a V1 corpus — its inclusion documents that the V1 corpus-adapter seam
  is unexercised for a V1 corpus.
- **D3 (tables):** financial report; exercises structural detectors
  (W_TABLE_MISSING, W_MULTICOLUMN_ORDER, W_HEADER_FOOTER_TABLE_GARBLED).
  Excluded from held-out V1 TAT-DQA.

Documents were drawn from already-staged local caches because the
authorization forbade corpus collection beyond three documents and no
V1-G1-GT-corpus loaders exist (see §5 finding INFRA-3). Selecting new
PMC-OA / DocLayNet / Federal Register / SEC / CUAD documents was
technically possible but would have required building acquisition
infrastructure that is more than the "minimal plumbing" the
authorization permitted.

## 2. Environment and parser versions

| Package | Version |
|---|---|
| aksharamd | 0.3.6 |
| marker-pdf | 1.10.2 |
| docling | 2.107.0 |
| markitdown | 0.1.6 |
| pymupdf | 1.28.2 |
| pymupdf4llm | 1.27.2.3 |
| torch | 2.12.1+cu126 (CUDA available) |

| Aspect | Value |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| Python | 3.12.2 (MSVC) |
| CPU | Intel64 Family 6 Model 151 Stepping 2 |
| GPU | CUDA available (RTX 3060 per prior session memory) |

**Non-default parser configuration:** none knowingly set. All parsers
were invoked with the constructor defaults exposed by their existing
`ParserAdapter` wrappers under `benchmarks/parsed_vs_raw/adapters/`,
except Docling which lacks a `ParserAdapter` wrapper and was invoked
directly via `docling.document_converter.DocumentConverter()` (see
§5 finding PARSER-1).

Machine-readable copies of the manifest and environment live at:

- `benchmarks/results/authorization-a-smoke-2026-09-14/manifest.json`
- `benchmarks/results/authorization-a-smoke-2026-09-14/environment.json`

## 3. Twelve parser-document execution outcomes

*Auto-generated from `outcomes.jsonl` — filled by report finalization
step. See `outcomes.jsonl` for full per-pair provenance.*

| Pair | Doc | Parser | Success | Elapsed (s) | RSS Δ (MB) | Output bytes | Score | Band | Detector fires |
|---|---|---|---|---|---|---|---|---|---|
| 1 | D1_qasper_1503_00841 | aksharamd-reference | OK | 3.16 | 193.8 | 27661 | 84 | OK | W_HEADER_FOOTER_TABLE_GARBLED |
| 2 | D1_qasper_1503_00841 | marker | OK | 172.66 | 2074.1 | 32055 | 89 | HIGH | HEADING_SKIP, HEADING_SKIP, HEADING_SKIP |
| 3 | D1_qasper_1503_00841 | docling | OK | 37.22 | 227.6 | 30747 | - | - | - |
| 4 | D1_qasper_1503_00841 | markitdown | OK | 3.94 | 7.7 | 39584 | 95 | HIGH | - |
| 5 | D2_docbench_P19_1598 | aksharamd-reference | OK | 2.95 | 1.4 | 38102 | 69 | RISKY | HEADING_HIERARCHY, W_TABLE_EXPECTED_NOT_EXTRACTED, W_MULTICOLUMN_ORDER, W_HEADER_FOOTER_TABLE_GARBLED |
| 6 | D2_docbench_P19_1598 | marker | OK | 125.23 | 142.5 | 44477 | 87 | HIGH | HEADING_SKIP, HEADING_SKIP, HEADING_SKIP, HEADING_SKIP |
| 7 | D2_docbench_P19_1598 | docling | OK | 9.44 | 49.6 | 42609 | - | - | - |
| 8 | D2_docbench_P19_1598 | markitdown | OK | 3.03 | 4.8 | 42445 | 95 | HIGH | - |
| 9 | D3_tatdqa_003755794b | aksharamd-reference | OK | 0.14 | 3.0 | 3495 | 84 | OK | HEADING_HIERARCHY, W_TABLE_EXPECTED_NOT_EXTRACTED |
| 10 | D3_tatdqa_003755794b | marker | OK | 3.05 | 3.8 | 3581 | 95 | HIGH | - |
| 11 | D3_tatdqa_003755794b | docling | OK | 4.28 | -47.2 | 4488 | - | - | - |
| 12 | D3_tatdqa_003755794b | markitdown | OK | 0.50 | 11.1 | 4129 | 95 | HIGH | - |

## 4. Protocol-stage completion matrix

The authorization enumerated ten pipeline stages. For each, the smoke
test recorded whether it was ATTEMPTED, SKIPPED, or hit a DEFECT.

| Stage | Status | Notes |
|---|---|---|
| source_ingestion | **ATTEMPTED** | Source PDFs loaded from local .cache/ paths. No corpus-manifest or corpus-adapter for V1 G1-GT corpora (PMC-OA, DocLayNet, Federal Register/SEC, CUAD) exists. The 3-doc smoke chose already-staged PDFs, one of which (D2 DocBench) is not on the V1 corpus list. |
| parser_execution | **ATTEMPTED** | 12/12 pairs.  |
| output_capture | **ATTEMPTED** | Per-pair markdown written under <output>/<parser>/<doc_id>.md and SHA-256 recorded. |
| ground_truth_ingestion | **SKIPPED** | No GT-ingestion pipeline exists for any V1 corpus. PROTOCOL_V1.md §10.1 item 11 requires benchmarks/eval_v1/ground_truth/ with code for PMC-OA XML, DocLayNet bboxes, CUAD spans; none present. |
| normalization | **SKIPPED** | PROTOCOL_V1.md §10.1 item 12 requires a normalization implementation applied uniformly across parsers before comparison. Not implemented for V1; only per-adapter post-processing exists in benchmarks/parsed_vs_raw/adapters/. |
| conventional_measurements | **SKIPPED** | PMC-OA word-overlap script and TEDS-adapted table script (PROTOCOL_V1.md §10.1 item 16) do not exist. Existing parsed_vs_raw uses an LLM judge for answer quality, which is not the same instrument. |
| aksharamd_evaluation | **ATTEMPTED** | AksharaMD score captured for 9/12 pairs. Docling pairs lack a score because Docling has no ParserAdapter wrapper (readiness_score None by design of the current benchmarks/parsed_vs_raw path). |
| detector_diagnostics | **ATTEMPTED** | warning_codes and deduction rule_ids captured for Compiler-routed parsers. Docling: not captured (no adapter). |
| labeling_adjudication_workflow | **SKIPPED** | No reviewer workflow, no (Q1,Q2,Q3)→label mapping table (PROTOCOL_V1.md §10.1 item 17, Appendix B), no reviewer instructions or presentation format. Authorization A did not attempt to build these; smoke test only exercises infrastructure that already exists or is minimal plumbing. |
| provenance_artifact_recording | **ATTEMPTED** | Per-pair provenance (started_at, elapsed_s, RSS delta, output SHA-256, byte count) recorded. Full study-freeze manifest (§10.1) NOT created — that is Authorization C, prohibited under A. |

## 5. Findings — defects / ambiguities / deviations

Findings are classified under the seven categories the authorization
message specified.

### 5.1 Implementation / infrastructure defect

- **INFRA-1** — `benchmarks/eval_v1/` and `benchmarks/parser_adapters/`
  directories referenced in PROTOCOL_V1.md §7.1 and §10.1 (items 15,
  11) did not exist at the start of Authorization A. The smoke test
  itself created a minimal `benchmarks/eval_v1/` package containing
  only what §7.2 measurement plumbing requires. No detector or scoring
  code was altered. Freeze-scale artifacts (STUDY_FREEZE_MANIFEST_V1,
  AMENDMENTS) were NOT created — those belong to Authorization C.
- **INFRA-2** — `benchmarks/eval_v1/ground_truth/` (PROTOCOL_V1.md
  §10.1 item 11) does not exist. No V1 corpus has a ground-truth
  ingestion pipeline: no PMC-OA XML text-extractor, no DocLayNet bbox
  loader, no CUAD span reader.
- **INFRA-3** — No V1 corpus adapter exists for PMC-OA, DocLayNet,
  Federal Register / SEC, or CUAD. The only cached corpora locally
  available are QASPER, DocBench, TAT-DQA. Of those, QASPER and TAT-DQA
  are V1 corpora but only for §6.2 downstream evaluation (G2 tier); no
  G1 GT is wired.
- **INFRA-4** — No normalization implementation (§10.1 item 12). Each
  parser adapter emits its own markdown; no uniform post-parser
  cleanup / whitespace / hyphenation / Unicode transformation exists
  as a study-visible step.
- **INFRA-5** — No conventional-metric implementation (§10.1 item 16):
  no PMC-OA word-overlap script, no TEDS-adapted table-comparison
  script. The existing `benchmarks/parsed_vs_raw/` uses an LLM judge
  for answer quality; that is not the same instrument as the §6.1
  conventional-metric verdict.
- **INFRA-6** — No labeling / adjudication workflow (§10.1 item 17,
  §3.2 Appendix B). No reviewer app, no (Q1, Q2, Q3) → label mapping
  table, no reviewer instructions, no blinding scaffolding.
- **INFRA-7** — `docs/evaluation/POST_FREEZE_OBSERVATIONS.md` is
  correctly absent (Authorization C artifact, not A).

### 5.2 Ambiguous protocol language

- **PROTOCOL-1** — §7.1 states adapters "live under
  `benchmarks/parser_adapters/` (existing pattern from prior work)".
  The existing pattern is actually under
  `benchmarks/parsed_vs_raw/adapters/` (Plan E scaffold). PROTOCOL_V1
  should either (a) rename the referenced directory, or (b) move the
  existing adapters, before Authorization C.
- **PROTOCOL-2** — Authorization A said "necessary minimal
  implementation of evaluation infrastructure is permitted." In
  practice the boundary between "measurement plumbing" and
  "infrastructure that alters the instrument" required judgment. The
  smoke test drew the line strictly: only per-pair provenance capture
  and a stage-completion matrix were built. Nothing that computes a
  metric or feeds back into the scoring rule was added.

### 5.3 Ground-truth incompatibility

- **GT-1** — Because no V1 G1-GT corpus has an ingestion pipeline
  (INFRA-2, INFRA-3), the smoke test could not exercise the ground-
  truth-comparison flow at all. This is a gap the ~20-doc pilot
  cannot avoid: without at least one G1-GT pipeline working end-to-end,
  the pilot's IAA-training and rubric-refinement steps have no
  oracle-grounded documents to work from.
- **GT-2** — DocBench (D2) is not in the §2.2 corpus manifest. Its
  inclusion in the smoke set is a smoke-test-only expedient. It
  provides no GT of a §2.3 kind.

### 5.4 Parser-specific issue

- **PARSER-1** — **Docling has no `ParserAdapter` wrapper.** The
  existing `aksharamd/assessment/docling_adapter.py` is a different
  module returning a Docling *assessment outcome* for the assessment
  engine, not a `ParserAdapter` compatible with `Compiler`. The
  `benchmarks/parsed_vs_raw/` README explicitly documents this:
  "Docling still runs on the legacy path (raw output, no ParserAdapter
  wrapper yet). Its readiness score comes from routing the markdown
  through the Compiler with a synthetic PDF wrap; simpler and honest
  to leave it as None for now than to mix instruments." Consequence:
  Docling's `readiness_score` is None across all Docling rows, and
  Docling's parser output is NOT scored by the same instrument as the
  other three parsers. **This directly violates the §7.1 adapter
  contract for V1 and would invalidate any Docling comparison at
  Authorization B.**
- **PARSER-2** — Per-pair marker/docling runtime and RSS deltas
  captured; see `outcomes.jsonl`. No parser was configured with GPU
  flags because the existing adapters do not surface a GPU switch;
  marker used whatever torch/CUDA path its default takes.

### 5.5 Normalization / metric issue

- **NORM-1** — Because no uniform normalization pass exists (INFRA-4),
  parser outputs cannot be compared against each other or against a
  reference on a level playing field. Any V1 disagreement-quadrant
  analysis (§6.1) requires this to be built before Authorization B.

### 5.6 Adjudication issue

- **ADJ-1** — INFRA-6 above. Building the reviewer scaffolding
  (~$500-1500 external labeling budget per §3.3) is prerequisite for
  the ~20-doc methodological pilot's IAA measurement. Authorization A
  did not touch this at all.

### 5.7 Potential AksharaMD limitation

- **No potential AksharaMD limitations were identified by the limited
  scope of Authorization A. This is not evidence of absence.** Several
  measurement stages (ground-truth ingestion, normalization, conventional
  measurements, labeling/adjudication) did not execute because the
  supporting infrastructure did not exist. Any conclusion about
  AksharaMD's behavior requires those stages to run against
  ground-truth-adjudicable documents; the smoke test does not.
  Detector fires observed on the three smoke-test pairs are recorded as
  raw observations only — they are not evidence for or against any
  detector's calibration.

## 6. Deviations from the protocol as executed

1. **Corpus choice deviates from §2.2** — Authorization A used QASPER
   / DocBench / TAT-DQA local caches rather than PMC-OA / DocLayNet /
   Federal Register / SEC / CUAD. Motivation: no V1 G1-GT loaders
   exist and Authorization A forbade corpus collection beyond three
   docs. Documented explicitly under GT-1 / GT-2 / INFRA-3.
2. **Docling did not execute through the ParserAdapter boundary
   (§7.1)** because no adapter exists (PARSER-1). Docling's raw
   converter was invoked to at least verify it produces output.
3. **§9.1 mentions "each ground-truth pipeline produces expected
   labels"** — this step was SKIPPED entirely (GT-1). Only parser
   execution, output capture, AksharaMD score capture, and detector
   diagnostic capture were exercised on the smoke set.
4. **§9.1 mentions "each detector returns output"** — verified via
   `warning_codes` and `deductions` capture on the three Compiler-
   routed parser paths. Not verified for Docling (PARSER-1).

No detector, scoring, or cap value was changed. No parser-output-
specific accommodations were made.

## 7. Recommendation

### Recommendation: **READY FOR B AFTER SPECIFIED NON-METHODOLOGICAL FIXES**

The smoke test executed 12/12 parser-doc pairs successfully and captured AksharaMD scores for 9/12 pairs. The parser-execution / output-capture / AksharaMD-evaluation / detector-diagnostics / provenance-recording stages ran; the ground-truth-ingestion, normalization, conventional-measurements, and labeling-adjudication stages could not run because the supporting infrastructure does not exist yet (findings INFRA-2, INFRA-4, INFRA-5, INFRA-6, PARSER-1, GT-1, NORM-1, ADJ-1).

None of the gaps are methodological — the V1 protocol's methodology as merged in PR #172 is intact and unchallenged by anything in the smoke test. The gaps are infrastructure: parser-adapter parity for Docling, corpus adapters, ground-truth ingestion, normalization, conventional-metric implementation, and reviewer scaffolding. The list is well-defined and non-methodological.

**Fixes required before Authorization B is sought:**

1. Wrap Docling as a `ParserAdapter` mirroring `MarkItDownAdapter`, so Docling routes through the Compiler and produces a readiness_score computed by the same instrument as the other three parsers. Without this, cross-parser comparison against Docling at Authorization B is invalid.
2. Move or rename adapters so `benchmarks/parser_adapters/` (per PROTOCOL_V1.md §7.1) either exists at the referenced path or the protocol text is amended to point at `benchmarks/parsed_vs_raw/adapters/`.
3. Build `benchmarks/eval_v1/ground_truth/` with corpus adapters + GT-ingestion for each V1 G1 corpus that the pilot will exercise (at minimum PMC-OA XML text + one of DocLayNet bbox / CUAD span).
4. Build the corpus acquisition scripts and split-assignment logic (§8.2 deterministic hashing) for the V1 corpora that will feed the pilot.
5. Build the normalization pass (§10.1 item 12) applied uniformly to every parser output before any comparison.
6. Build conventional-metric implementations (§10.1 item 16): PMC-OA word-overlap and TEDS-adapted table comparator.
7. Draft and pilot the (Q1, Q2, Q3) → label mapping table (Appendix B) and the reviewer instructions + blinding scaffolding (§10.1 items 17-19).
8. Extend the harness to bootstrap by document per §11.2, before any pilot summary numbers are produced.

**Scope of the required fixes:** substantial but well-scoped engineering. No detector, threshold, cap value, scoring-policy version, parser catalog, or scoring code needs to be touched to clear this list. If, while building the fixes, evidence emerges that a methodological change is needed, that evidence goes to the human before any protocol edit — the amendment procedure in §10.4 does not apply pre-freeze, but the same discipline applies here.

**Do not start Authorization B on the basis of this report.** The user's original authorization message required that Authorization A stop at the report regardless of outcome. Only human authorization can move to B.

---

## Appendix A — File inventory

Written by this smoke test:

- `benchmarks/eval_v1/__init__.py`
- `benchmarks/eval_v1/smoke_manifest.py`
- `benchmarks/eval_v1/smoke_run.py`
- `benchmarks/eval_v1/resource_shim.py`
- `benchmarks/eval_v1/finalize_report.py`
- `benchmarks/results/authorization-a-smoke-2026-09-14/manifest.json`
- `benchmarks/results/authorization-a-smoke-2026-09-14/environment.json`
- `benchmarks/results/authorization-a-smoke-2026-09-14/outcomes.jsonl`
- `benchmarks/results/authorization-a-smoke-2026-09-14/stage_matrix.json`
- `benchmarks/results/authorization-a-smoke-2026-09-14/<parser>/<doc_id>.md`
  (12 files)
- `docs/evaluation/AUTHORIZATION_A_SMOKE_TEST_REPORT.md` (this file)

NOT written (intentionally, per Authorization A boundary):

- `docs/evaluation/STUDY_FREEZE_MANIFEST_V1.md` (Authorization C artifact)
- `docs/evaluation/AMENDMENTS.md` (opened at freeze, not now)
- `docs/evaluation/POST_FREEZE_OBSERVATIONS.md` (post-freeze artifact)
- Any change to `aksharamd/scoring/`, `aksharamd/plugins/validators/`,
  `parser_stubs/`, cap values, or scoring policy.

No new detectors, parser catalog entries, or threshold changes were
introduced. Reference and audit-CLI code paths already at HEAD were
not modified.
