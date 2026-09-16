# B1 Parser Execution Contract, V1

Status: **DRAFT — B1a-7b.1, docs/config only. No parser executions, no infrastructure-smoke runs, no held-out study runs are authorized by this document.**

Scope: This document specifies, in one place, what "running the four V1 parsers" means before B1a-7b.2 executes even the 12-run infrastructure smoke. It fixes the exact instruments (packages, versions, models, install sources), the runtime environment, the hardware and network policy, the invocation surface, the timeout and failure semantics, the timing/resource capture surface, the stdout/stderr policy, the output-file schema, the normalization handoff, and the analysis-record shape produced per `(document, parser)` execution.

This document also resolves the three §13 items from `REVIEWER_CONTRACT_B1_V1.md` that affect infrastructure (document-identity rendering, reviewer-independence enforcement, PMC JATS pane visibility). The other four §13 items remain deferred to B1a-7c.

If any part of this contract conflicts with `PROTOCOL_V1.md` §7 or with `REVIEWER_CONTRACT_B1_V1.md`, the protocol/reviewer contracts win and this document must be brought into sync.

---

## 1. Purpose and non-goals

**Purpose.**

- Fix, before any parser executes, every degree of freedom that could otherwise silently change the output: package version, model version, install source, runtime, hardware profile, network policy, invocation flags, timeout, retry policy, and crash semantics.
- Make parser execution auditable: every `(document, parser)` invocation must produce a machine-readable execution record whose bytes are content-hashed and whose fields are sufficient to reconstruct the run.
- Make parser failure a **first-class outcome**. A parser that crashes, hangs, or produces empty output is recorded as such; it is never silently dropped from the study, and it is never confused with "no ground truth applied."
- Prevent the study from smuggling in a hidden instrument by settling into a single "reference" set of installed packages, and then locking that set for the duration of B1a-7b/B1a-7c/B1.

**Non-goals of this document.**

- It does not run any parser. All executions are deferred to B1a-7b.2.
- It does not select which documents get the 12-run smoke. That is B1a-7b.2 scope.
- It does not decide the analysis of parser output for quality — no readiness scoring, no per-parser ranking, no severity claim. Those belong to B1 proper.
- It does not resolve the four remaining `REVIEWER_CONTRACT_B1_V1.md` §13 decisions (abstention replacement, timing floor/ceiling, FR healthy-baseline sampling, pilot reviewer sourcing).

---

## 2. Frozen references

The parser execution contract binds to the following artifacts. A change in any of these hashes or identifiers invalidates every parser-execution record produced under this contract.

| Artifact | Location | Identifier |
|---|---|---|
| Protocol document | `docs/evaluation/PROTOCOL_V1.md` | authoritative — §7 defines the parser slate |
| Reviewer contract | `docs/evaluation/REVIEWER_CONTRACT_B1_V1.md` | `reviewer_contract_version = v1` — normative for the reviewer handoff (§10) |
| Selection manifest | `docs/evaluation/DEV_PILOT_MANIFEST_V2.json` | `selection_manifest_version = 2`, `sha256 = 739fdbdb...` |
| Severity mapping | `benchmarks/eval_v1/mapping.v1.json` | `mapping_id = appendix_b_v1`, `version = v1`, canonical SHA `99248be5...f08a9` |
| Reference parser | `aksharamd` package, `_compile_pdf_bytes()` compilation path | pinned via `aksharamd` package version below |
| External adapters | `benchmarks/parsed_vs_raw/adapters/{marker,docling,markitdown}_adapter.py` | source path + Python-module content hash recorded per run |
| Stage-status vocabulary | `benchmarks/eval_v1/stages.py :: StageStatus` | `EXECUTED / NOT_APPLICABLE / DEFECT / REQUIRES_REVIEW / INFRASTRUCTURE_READY_NOT_EXECUTED` |
| Normalization | `benchmarks/eval_v1/` normalization stage | `normalization_version = 2` |

---

## 3. Runtime environment

All B1 parser executions must run under the following environment. Deviation invalidates the run.

### 3.1 OS and Python

- **OS.** Windows 11 (10.0.26200 SP0) on the primary study host. Cross-platform execution (Linux CI, macOS) is permitted for smoke and reproducibility but every held-out run must record its platform string. The primary study host is what B1a-7c freezes.
- **Python.** CPython 3.12.2, x86_64. No alternative interpreter (PyPy, GraalPy). The interpreter's `sys.version` string is recorded per run.

### 3.2 Pinned package versions

Recorded at study time in `environment.json` alongside every run. Current pins (from `benchmarks/results/appendix-b-v1-validation-2026-09-14/environment.json`, which the freeze inherits from):

| Package | Version | Role |
|---|---|---|
| `aksharamd` | `0.3.6` | reference parser (bundled PyMuPDF-based compilation path) |
| `marker-pdf` | `1.10.2` | `marker` external adapter |
| `docling` | `2.107.0` | `docling` external adapter |
| `markitdown` | `0.1.6` | `markitdown` external adapter |
| `pymupdf` | `1.28.2` | PDF I/O for reference parser and adapters that consume raw text |
| `pymupdf4llm` | `1.27.2.3` | text-layer extraction utilities used by reference parser |
| `torch` | `2.12.1+cu126` | VLM/layout model runtime for `marker` and `docling` |

The B1a-7c freeze will additionally record, for every package above, the source-artifact SHA (wheel hash or `pip install --report` hash). Version strings alone are not sufficient to prove reproducibility; the hash is what closes the loop.

### 3.3 Hardware policy

- **CPU.** Primary study host. `psutil.cpu_count(logical=False)` is recorded per run.
- **GPU.** CUDA 12.6 (`torch.cuda.get_device_name(0)`, `torch.version.cuda`, `torch.cuda.is_available()` all recorded per run). CUDA-capable GPU is **required** for `marker` and `docling`; the contract does not permit CPU-only fallback for those two parsers because their outputs differ materially on CPU. If CUDA is unavailable at execution time, the run for those parsers is recorded as `DEFECT` (`reason = "cuda_unavailable"`) rather than silently degraded.
- **Memory.** No explicit ceiling in B1a-7b.1. Peak RSS is captured per run (§7); pathological memory blowup on any specific `(document, parser)` is a study-cost observation, not a fail-closed condition.

### 3.4 Network policy

- **Offline required at parser execution.** All model weights and support files must be pre-downloaded before the study run and referenced from local paths. The runtime must set `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and `DOCLING_ARTIFACTS_OFFLINE=1` (where the adapter honors it) before invoking any parser.
- **Reason.** A held-out run over ~200 documents must not be at the mercy of a remote CDN, an upstream model repo removal, or a transient DNS failure. Any network-dependency at execution time is an unrecorded degree of freedom.
- **Enforcement.** The B1a-7b.2 smoke will explicitly execute with the network firewall configured to block egress from the parser process. If any parser silently reaches out to the network, this surfaces at smoke time, not at held-out time.

---

## 4. The four parser instruments

Four parsers per PROTOCOL_V1 §7 Decision 7.a. `mineru` and `unlimited_ocr` are V2. Cloud parsers (LlamaParse, Reducto) are excluded per Decision 7.b.

### 4.1 `aksharamd-reference`

- **Family.** Direct PDF text-layer extraction. CPU-only.
- **Package.** `aksharamd` `0.3.6`, invoked via `_compile_pdf_bytes()` in `benchmarks/eval_v1/smoke_run_v2.py`.
- **Model.** None (deterministic PDF text-layer extraction via PyMuPDF).
- **Install source.** Local editable install of the repository at the frozen git commit (recorded per run as `git rev-parse HEAD`). Non-editable install is permitted but only when the wheel is built from the same frozen commit.
- **Hardware.** CPU-only. GPU presence does not affect the reference parser's output; the contract still records GPU state so cross-parser environment records are uniform.
- **Command.** In-process Python call — not a subprocess. Invocation surface: `_compile_pdf_bytes(pdf_bytes: bytes, *, context: CompilationContext) -> CompilationResult`.
- **Config.** All aksharamd compilation defaults; no CLI overrides. If B1a-7b.2 surfaces a configuration surface that materially affects output (e.g., OCR backend policy), that surface must be locked here in a follow-up before any held-out run.
- **Timeout.** 120 seconds wall-clock per document. On timeout the run is recorded as `DEFECT` (`reason = "reference_parser_timeout_120s"`).
- **Retry.** None. First-attempt outcome is definitive.
- **Crash semantics.** Uncaught Python exception → `DEFECT` (`reason = "reference_parser_exception:<ExceptionClassName>"`, full traceback in payload). Segfault (extremely rare via PyMuPDF) → `DEFECT` (`reason = "reference_parser_native_crash"`).

### 4.2 `marker`

- **Family.** VLM + layout model. GPU-required (§3.3).
- **Package.** `marker-pdf` `1.10.2`.
- **Model.** Bundled Marker layout + OCR models. Model weights hash and version string recorded per run (`marker.__version__` plus per-model artifact hash from the local cache).
- **Install source.** PyPI wheel installed with `--only-binary=:all:` and recorded wheel hash. Model weights pre-downloaded and cached under a study-controlled directory (path recorded per run).
- **Hardware.** CUDA GPU required. CUDA version recorded. Batch size fixed to `1` for reproducibility (Marker's default is auto-tuning based on VRAM, which introduces cross-run variability; the contract disables it).
- **Command.** In-process Python call via `MarkerAdapter.compile(...)` in `benchmarks/parsed_vs_raw/adapters/marker_adapter.py`.
- **Config.** Deterministic mode: fixed batch size 1, `torch.manual_seed(0)`, `torch.cuda.manual_seed_all(0)`, `torch.use_deterministic_algorithms(True, warn_only=True)`, `CUBLAS_WORKSPACE_CONFIG=:4096:8`. The contract acknowledges that VLM outputs are not bit-exact reproducible across GPU-driver versions; the seed and determinism flags are best-effort. The CUDA driver version and GPU model are recorded per run so any variance is at least explainable.
- **Timeout.** 600 seconds wall-clock per document (marker is the slowest of the four on GPU). On timeout → `DEFECT` (`reason = "marker_timeout_600s"`).
- **Retry.** None.
- **Crash semantics.** As reference parser. CUDA OOM specifically → `DEFECT` (`reason = "marker_cuda_oom"`, peak-VRAM in payload).

### 4.3 `docling`

- **Family.** VLM + layout model. GPU-required (§3.3).
- **Package.** `docling` `2.107.0`.
- **Model.** Bundled Docling models (layout, structure, OCR fallback). Model version strings and artifact hashes recorded per run.
- **Install source.** PyPI wheel + pre-downloaded model artifacts under a study-controlled directory.
- **Hardware.** CUDA GPU required. Same CUDA-version recording as `marker`.
- **Command.** In-process Python call via `DoclingAdapter.compile(...)` in `benchmarks/parsed_vs_raw/adapters/docling_adapter.py`.
- **Config.** Deterministic mode as `marker`. Docling has known memory issues on large PDFs (PROTOCOL_V1 §7 note); the contract does not raise the timeout to accommodate them — a Docling OOM on a large PDF is a valid recorded outcome, not a re-run condition.
- **Timeout.** 600 seconds wall-clock per document. On timeout → `DEFECT` (`reason = "docling_timeout_600s"`).
- **Retry.** None.
- **Crash semantics.** As `marker`. Docling-specific "empty output despite success" (documented behavior on some PDF structures) is **not** `DEFECT` — it is `EXECUTED` with a zero-length markdown output. That is a real behavior of the instrument and the study needs to observe it, not paper over it.

### 4.4 `markitdown`

- **Family.** Broad-format converter. CPU-only.
- **Package.** `markitdown` `0.1.6` (Microsoft).
- **Model.** None (deterministic conversion pipeline; internally uses format-specific libraries).
- **Install source.** PyPI wheel with recorded hash.
- **Hardware.** CPU-only.
- **Command.** In-process Python call via `MarkItDownAdapter.compile(...)` in `benchmarks/parsed_vs_raw/adapters/markitdown_adapter.py`.
- **Config.** No LLM-augmentation. MarkItDown supports an optional LLM-based description enrichment path that would introduce a moving instrument (external LLM, API cost, non-determinism). The contract disables it: `MarkItDown(llm_client=None)`.
- **Timeout.** 120 seconds wall-clock per document.
- **Retry.** None.
- **Crash semantics.** As reference parser.

---

## 5. Invocation contract

Every parser invocation, regardless of family, follows the same outer contract.

### 5.1 Input

- One PDF file, read into memory as `bytes` from a path under the run's local acquisition tree.
- Canonical ID and corpus label from the selection manifest.
- No metadata about the parser's "expected" quality on this document.

### 5.2 Output

Each invocation produces an `AnalysisRecord` JSON file at a study-controlled path. The record must be produced whether the parser executed successfully, timed out, crashed, or was skipped for a lock-related reason. See §8 for the schema.

### 5.3 Isolation

- One `(document, parser)` invocation per process. Reference and MarkItDown may be batched in-process (they are cheap and deterministic). Marker and Docling **must be single-invocation per Python process** to avoid warm-cache reproducibility drift.
- No shared mutable state between invocations. Between marker/docling invocations the process exits (or a fresh subprocess is spawned).
- Working directory is a per-invocation temporary directory. No parser may write to the shared filesystem outside its own scratch directory and the study output path.

### 5.4 Wall-clock and monotonic clock

- Every invocation records `pair_started_at` (UTC ISO 8601) and `pair_finished_at` (UTC ISO 8601) from the system clock, plus `wall_clock_seconds` from `time.monotonic()` deltas. The monotonic delta is the authoritative duration.

---

## 6. Failure semantics — parser failure is a valid recorded outcome

**Never silently dropped.** A `(document, parser)` pair that fails still produces an `AnalysisRecord`. The record's stage-matrix entry for the parser-execution stage is `DEFECT` with a coded reason (§4 per-parser) or `EXECUTED` with an empty-output payload where the parser genuinely produced nothing (as in Docling's documented empty-output case).

**Coded reason strings** are the only permitted `DEFECT.reason` values, per parser:

- `reference_parser_timeout_120s`, `reference_parser_exception:<Class>`, `reference_parser_native_crash`
- `marker_timeout_600s`, `marker_exception:<Class>`, `marker_cuda_oom`, `marker_cuda_unavailable`
- `docling_timeout_600s`, `docling_exception:<Class>`, `docling_cuda_oom`, `docling_cuda_unavailable`
- `markitdown_timeout_120s`, `markitdown_exception:<Class>`
- `parser_binary_hash_mismatch:<parser>` — parser wheel or model artifact hash does not match the pinned value
- `environment_drift:<field>` — the run's environment record differs from the pinned environment in a field that must not drift (Python version, package version, CUDA version)

Any `DEFECT.reason` that is not on this list is itself a defect of the harness and must be surfaced by B1a-7b.2 smoke.

**Empty output.** If the parser executes to completion and returns markdown of length 0, the stage is `EXECUTED` and the record captures the zero length. The reviewer contract already accepts that a reviewer may answer `no` / `mostly stub or junk` / `wrong` on empty output — no special-casing is required at the parser layer.

**No auto-retry.** The contract prohibits retrying a failed parser invocation as a way to "fix" a flaky result. If a run's overall failure rate on the smoke exceeds a documented threshold (deferred to B1a-7c) the harness itself is unreliable and B1a-7b.2 must be re-run under a fixed harness, not re-run under the same harness in the hope of different numbers.

---

## 7. Timing and resource capture

Per invocation, the following fields are captured and included in the `AnalysisRecord`:

- `wall_clock_seconds` — `time.monotonic()` delta between adapter enter and adapter exit.
- `cpu_seconds_user`, `cpu_seconds_system` — via `psutil.Process.cpu_times()` deltas on the invocation process (and children, for subprocess-based parsers if any).
- `peak_rss_bytes` — via `psutil.Process.memory_info()` sampled at 1 Hz during the invocation, maximum recorded.
- `peak_vram_bytes` — via `torch.cuda.max_memory_allocated()` (per-device, device 0) reset before invocation and read at the end. `null` for CPU-only parsers.
- `cuda_events` — count and total-milliseconds for CUDA synchronization events, or `null` for CPU-only parsers.
- `output_bytes` — length of the parser's raw markdown output (before normalization).
- `stdout_bytes`, `stderr_bytes` — length of captured stdout/stderr (§9).
- `exit_status` — `EXECUTED` / `DEFECT` with coded reason.

Timing and resource capture is a study-observation surface. It is not an accept/reject gate at the parser layer.

---

## 8. Output-file schema

Each `(document, parser)` invocation produces exactly the following files under `<run-dir>/<parser>/<canonical_id>/`:

- `raw_output.md` — the parser's markdown output, byte-for-byte as returned. Zero-length permitted (§6).
- `raw_output.meta.json` — parser-specific metadata returned by the adapter (layout regions, per-page markers, per-table cells) if the adapter surfaces it. Free-form but must be JSON-serializable.
- `stdout.txt`, `stderr.txt` — captured stdout/stderr (§9). Zero-length permitted.
- `execution_record.json` — the canonical execution record for this invocation. Fields listed below.
- `analysis_record.json` — the `AnalysisRecord` composed from `execution_record.json` + downstream stages (normalization + reviewer-artifact preparation). See §10.

`execution_record.json` fields (minimum):

- `pair_id` — opaque per-invocation identifier: `sha256(canonical_id || parser_id || run_id)[:16]`.
- `canonical_id`, `corpus`, `parser_id` — from the selection manifest and this contract.
- `parser_package_version`, `parser_package_source_sha256` — pinned per §3.2.
- `parser_model_version`, `parser_model_artifact_sha256` — for `marker` and `docling`; `null` for `aksharamd-reference` and `markitdown`.
- `python_version`, `platform_string`, `git_commit`, `cuda_version`, `cuda_device_name` — environment record.
- `pair_started_at`, `pair_finished_at`, `wall_clock_seconds`, `cpu_seconds_user`, `cpu_seconds_system`, `peak_rss_bytes`, `peak_vram_bytes`, `cuda_events` — timing/resource (§7).
- `output_bytes`, `output_sha256` — content-hash of `raw_output.md`.
- `stdout_bytes`, `stdout_sha256`, `stderr_bytes`, `stderr_sha256` — content-hashes of captured streams.
- `exit_status` — `EXECUTED` or `DEFECT`.
- `defect_reason` — coded string from §6, or `null`.
- `normalization_version` — `"2"`.
- `parser_execution_contract_version` — `"v1"` (this document).

Every field is required; `null` is only permitted where explicitly noted above.

---

## 9. Stdout/stderr capture

- Every parser invocation runs with stdout and stderr redirected to per-invocation files (`stdout.txt`, `stderr.txt`).
- Nothing is echoed to the operator console during held-out execution; the study is meant to be batch-executed.
- Byte-lengths and SHA-256 hashes of the captured streams are recorded in `execution_record.json` (§8).
- Contents are retained but are **not** shown to the reviewer (they can leak parser identity via banner strings and version prints).
- The study report may cite representative stdout/stderr excerpts when discussing a specific failure mode, but only after they have been reviewed for identity leakage.

---

## 10. Normalization handoff and reviewer artifact

### 10.1 Normalization

After parser execution the raw markdown is passed through the normalization stage documented in `benchmarks/eval_v1/` at `normalization_version = 2`. Normalization is intentionally minimal — whitespace canonicalization, trailing-newline handling, and Unicode NFC. It **must not**:

- rewrite tables, headings, or list markers.
- infer structure the parser did not produce.
- replace glyphs beyond NFC normalization.

The normalized output is written as `normalized_output.md` alongside `raw_output.md`. Both are retained; the reviewer artifact is built from the normalized version (§10.2), and the raw version is retained for auditability.

### 10.2 Reviewer artifact

`prepare_reviewer_artifact()` in `benchmarks/eval_v1/adjudication.py` composes the blinded reviewer surface described in `REVIEWER_CONTRACT_B1_V1.md` §6. The artifact contains:

- The source PDF (rendered at the resolution specified by the reviewer contract).
- The normalized markdown for one parser, presented without parser-identity chrome.
- The `blinded_parser_hash = sha256(parser_id)[:16]`.
- The pair identifier (opaque hash).
- No AksharaMD score, no warning list, no detector output, no other-parser output.

The `analysis_record.json` produced per invocation links the execution record (§8) to the reviewer artifact by pair ID.

---

## 11. Resolved reviewer-contract §13 items (infra-affecting)

The `REVIEWER_CONTRACT_B1_V1.md` §13 leaves seven decisions explicitly unresolved. B1a-7b.1 resolves three of them because they affect the labeling infrastructure that B1a-7b.2 must build.

### 11.1 Document-identity rendering policy (§13 item 3)

**Resolution:** **Permitted with per-pair marker.** The rendered source PDF is presented to the reviewer as-is, including any title page, author block, DOI, docket number, or other in-document identity metadata that a real user would see.

- **Reason.** Automated redaction of embedded document identity is fragile (title-page detection is not robust across PMC, DocLayNet, and Federal Register), destroys context on documents where the identity string is on every page (Federal Register docket numbers appear in the running head of every page), and does not match the deployment surface a real RAG user would encounter.
- **Enforcement.** The `analysis_record.json` for each pair records `document_identity_potentially_visible = true` for every pair (there is no attempt to redact and no way to reliably certify non-visibility). Downstream analysis can control for this if a specific study question requires it.
- **What this does NOT change.** Parser identity remains fully blinded via the `sha256(parser_id)[:16]` scheme. The study's primary blinding target — that the reviewer cannot tell which parser produced the markdown — is intact.

### 11.2 Reviewer independence enforcement (§13 item 2)

**Resolution:** **Attested with sampled spot-check.** Reviewer independence is self-attested at onboarding and re-attested per session; the labeling infrastructure additionally captures a light per-session forensic record and executes a stratified spot-check on ~10% of sessions.

- **Attestation.** At onboarding the reviewer signs a written attestation that they (a) have not seen AksharaMD source code beyond public README-level information, (b) will not discuss any pair with any other reviewer before submission, (c) will label from a single machine per session.
- **Forensic record per session.** `session_id`, salted-hashed IP address (not the raw IP), session start-time, session end-time, per-pair `time_on_pair_seconds`. IP hash is retained only to detect coincident sessions, never to identify individuals.
- **Spot-check.** A stratified 10% sample of sessions receives a post-session questionnaire probing (a) whether the reviewer noticed a repeated hash pattern that could correspond to a specific parser, (b) whether they know what AksharaMD is, (c) whether they discussed any pair with anyone. Any positive spot-check invalidates every label from that reviewer for the affected corpus and requires a full re-label by an independent reviewer.
- **Reason for not going stricter.** Full network-origin verification, hardware attestation, or supervised sessions are out of scope for a ~200-document pilot. Full trust-me is too weak. The attested-plus-spot-check middle path is auditable, cheap, and preserves the reviewer's confidentiality.

### 11.3 PMC JATS pane visibility (§13 item 6)

**Resolution:** **On-demand.** The JATS canonical body-text pane is available to the primary reviewer via a labeled toggle, and its use is recorded per pair.

- **Reason.** Always-shown biases the reviewer toward XML-derived judgments even when the reviewer's Q3 (downstream usability) judgment should reflect the PDF as the RAG-input surface. Adjudication-only denies the primary reviewer access to the anchor when they need it. On-demand preserves reviewer discretion.
- **Enforcement.** The reviewer artifact records `jats_pane_opened: bool` and `jats_pane_open_seconds` per pair. Sensitivity analysis over pairs where the JATS pane was and was not consulted is a study-observation surface, not a per-label decision surface.
- **Scope.** This resolution applies only to PMC-OA pairs. DocLayNet reviewer surface remains as described in `REVIEWER_CONTRACT_B1_V1.md` §10.2 (structural G1 overlay, no additional pane). Federal Register reviewer surface has no anchor pane (§10.3).

---

## 12. Explicit unresolved decisions (deferred to B1a-7c)

The following remain deliberately unresolved and are not decided by this contract:

1. **Reviewer-contract §13 item 1** — Whether the B1 pilot (distinct from the DEV training set and the held-out run) is labeled by project-affiliated reviewers, external reviewers, or both.
2. **Reviewer-contract §13 item 4** — Whether pairs where both reviewers abstain are excluded from the study population or replaced.
3. **Reviewer-contract §13 item 5** — Floor and ceiling for acceptable `time_on_pair_seconds`, to be set from DEV-set observed distributions in B1a-7c.
4. **Reviewer-contract §13 item 7** — Whether Federal Register pairs with no detector firings are reviewed as healthy-baseline calibration pairs or only detector-firing pairs are adjudicated.
5. **Parser wheel and model artifact SHAs.** The concrete SHA-256 for every pinned wheel and every model artifact in §3.2 and §4 is captured only at B1a-7c freeze time; this document commits to *recording* them, not to enumerating them.
6. **Study-controlled model cache directory location.** The literal filesystem path for `HF_HOME`, `TORCH_HOME`, and any parser-specific artifact cache is decided at B1a-7c so it can be locked alongside the frozen environment record.
7. **B1a-7b.2 smoke document selection.** Which one PMC-OA, one DocLayNet, and one Federal Register document from the V2 selected pool are used for the 12-run infrastructure smoke. This is deferred so the smoke selection can be justified per corpus (e.g., a well-textured PMC article, a table-rich DocLayNet page, a multi-column Federal Register rule) rather than picked arbitrarily.

---

## 13. Machine-readable configuration (informative)

The values in §3.2, §4, and §8 are collectively captured in a machine-readable configuration file, `benchmarks/eval_v1/config/parser_execution_contract_v1.json` (to be committed alongside this document in the same PR). That file is the harness's runtime source of truth; this document is the human-readable specification. If they disagree, this document wins and the config file must be brought into sync.

The config file's canonical-JSON SHA-256 is recorded per run in `execution_record.json :: parser_execution_contract_config_sha256`. Drift in that hash at execution time is a fail-closed condition — a fresh smoke must be re-run before any held-out label is emitted.

---

## 14. Change log

- **v1 (this document, B1a-7b.1).** Initial draft. Docs/config only. No parser executions, no infrastructure-smoke runs, no held-out study runs are authorized by this document. B1a-7b.2 (infrastructure smoke, 3 × 4 = 12 executions) is not authorized. B1a-7c (study freeze readiness) is not authorized. B1b, B1c are not authorized.
