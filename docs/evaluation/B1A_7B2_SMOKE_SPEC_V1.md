# B1a-7b.2 Smoke Pre-Execution Specification, V1

Status: **DRAFT — B1a-7b.2 pre-execution spec, docs/config only. The 12 parser executions this document plans are NOT AUTHORIZED by this document. Authorization is a separate step after review of this spec.**

Scope: This document freezes five things before any smoke execution: (1) the exact three smoke documents drawn from `DEV_PILOT_MANIFEST_V2.json`, (2) the firewall-verification probe that underlies the `network_egress_blocked` execution-record field, (3) the smoke PASS/FAIL criteria, (4) the enumerated allowlist of `execution_record.json` fields that infrastructure reviewers may inspect and the corresponding prohibition list, (5) the DEFECT-handling rules that distinguish parser-level defects (continue) from harness-level defects (stop).

The smoke's purpose is to prove that the plumbing works — that a `(document, parser)` pair flows source → parser → raw output → normalization → analysis record → blinded reviewer artifact, and that the execution record carries what the parser-execution contract requires. Whether any particular parser produces good markdown is out of scope for the smoke.

If any part of this document conflicts with `PROTOCOL_V1.md`, `REVIEWER_CONTRACT_B1_V1.md`, or `PARSER_EXECUTION_CONTRACT_B1_V1.md`, those documents win and this one must be brought into sync.

---

## 1. Purpose and non-goals

**Purpose.**

- Fix the smoke's specimens, admission tests, acceptance criteria, review procedure, and failure semantics **before** any smoke output exists, so nothing about the smoke is designed after seeing its results.
- Make the network-isolation guarantee (`network_egress_blocked`) a real observation, not a declared intent — by specifying the probe design in full and including a positive control that proves the canary is reachable when unblocked.
- Fence the smoke so infrastructure review cannot turn into an accidental parser-quality evaluation. Reviewer eyes on smoke output must inspect field structure and hashes, not markdown content.

**Non-goals.**

- Not a run authorization. Executing this smoke is a separate step and remains **NOT AUTHORIZED** by this document.
- Not a study-freeze. Study freeze is B1a-7c. This spec's role is to make B1a-7b.2 executable *once* B1a-7c is ready to freeze the remaining decisions.
- Not a parser-quality evaluation. The 12 executions this smoke plans are not measured against ground truth, not compared across parsers, not scored by AksharaMD.
- Not a re-selection of the study population. The 20 V2-selected documents remain frozen; the smoke picks 3 for coverage of the harness surface.

---

## 2. Frozen references

| Artifact | Location | Identifier |
|---|---|---|
| Protocol | `docs/evaluation/PROTOCOL_V1.md` | authoritative |
| Reviewer contract | `docs/evaluation/REVIEWER_CONTRACT_B1_V1.md` | `reviewer_contract_version = v1` |
| Parser-execution contract | `docs/evaluation/PARSER_EXECUTION_CONTRACT_B1_V1.md` | `parser_execution_contract_version = v1` |
| Parser-execution config | `benchmarks/eval_v1/config/parser_execution_contract_v1.json` | canonical-JSON SHA recorded per run |
| Selection manifest | `docs/evaluation/DEV_PILOT_MANIFEST_V2.json` | `selection_manifest_version = 2`, `sha256 = 739fdbdb...` |
| Severity mapping | `benchmarks/eval_v1/mapping.v1.json` | `mapping_id = appendix_b_v1`, `version = v1` (not exercised by the smoke — labels are not emitted) |
| Stage-status vocabulary | `benchmarks/eval_v1/stages.py :: StageStatus` | `EXECUTED / NOT_APPLICABLE / DEFECT / REQUIRES_REVIEW / INFRASTRUCTURE_READY_NOT_EXECUTED` |
| Companion config | `benchmarks/eval_v1/config/smoke_spec_v1.json` | this spec's machine-readable form; SHA recorded per run |

---

## 3. The three smoke documents

Selection criterion is **infrastructure coverage**, not parser difficulty. No document was selected because any parser is known to do well or poorly on it. No AksharaMD score, warning, prior parser output, or benchmark result was consulted. The rationale below is grounded exclusively in the V2 manifest's structural metadata (corpus, category, region annotation counts, page counts, JATS predicate status).

The three selections are recorded here **before any smoke output exists**. If B1a-7b.2 review requires a different selection, this document is revised and re-reviewed; a new selection is not made after any smoke output has been observed.

### 3.1 PMC-OA — `PMC5773191.1` (rank 4 in V2 pool)

- **Purpose:** exercise the textual-G1 / JATS handoff surface — `apply_text_oracle_filters` predicate, canonical body text derivation, SHA anchoring across the V2 chain, JATS pane visibility (§11.3 of the parser-execution contract), reviewer-artifact preparation with a corpus-specific G1 anchor.
- **What the manifest says.** Prose-heavy social/health-services article on methadone-maintenance adherence. Body admitted by the V2 body-tokens predicate (`PMC_MIN_BODY_TOKENS = 500`). No abstract-only defect; no obvious special-media payload (no chemistry notation dominating the title, no Greek-letter or math-heavy prose).
- **Why this one from the 8 PMC docs.** Selected as the most structurally generic scientific-article JATS in the pool: full body, no case-report brevity, no highly-technical typography that would risk conflating a smoke observation with a corpus-quirk observation. This is the least distinctive PMC in the pool from a JATS-handoff plumbing perspective — which is exactly what we want to exercise.

### 3.2 DocLayNet — `3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77` (rank 0 in V2 pool)

- **Purpose:** exercise the structural-G1 surface — DocLayNet region-annotation ingest, bounding-box overlay handling, category-labeled region iteration, coordinate correctness across the parser adapter's understanding of page geometry.
- **What the manifest says.** `doc_category = financial_reports`, `n_annotations = 17`, `page_no = 26` from `AMEX_TTNP_2001.pdf`. Financial-report category exercises the region-type mix that matters for the structural-G1 anchor: table regions, page-header, page-footer, figure, list, and section-header. `n_annotations = 17` sits in the mid-to-upper range of the six DocLayNet selections (12 / 15 / 17 / 18 / 21 / 22), giving richer coordinate handling than the sparsest page without being an outlier.
- **Why this one from the 6 DocLayNet pages.** `financial_reports` is a "standard" DocLayNet category (well-annotated in the reference dataset), not an OCR-derived page (`AdobeOCR` variant excluded), not a category with peculiar layout (patents have specific claim-block conventions), and `rank = 0` — the deterministic-first selection within its stratum, which minimizes any appearance of hand-picking.

### 3.3 Federal Register — `2025-19924` (rank 0 in `Notice` stratum)

- **Purpose:** exercise the G2 / native-authored-PDF surface — no external oracle at label time, native PDF text layer (no OCR), multi-page PDF handoff. This is the corpus where the reviewer's Q1/Q2/Q3 answers *are* the criterion of record; the smoke does not exercise the label emission (§ 5.5), only the artifact-preparation path that will feed it.
- **What the manifest says.** `stratum = Notice`, `type = Notice`, `page_length = 3`, `has_xml = true`, `citation = 90 FR 51441`, `publication_date = 2025-11-17`, `document_number = 2025-19924`, `package_id = FR-2025-11-17`. Three pages exercises multi-page PDF handling without being an outlier (the 6 FR selections span 1, 1, 2, 2, 3, 13 pages). `Notice` is a stratum distinct from `Rule` and `Proposed Rule`, so this pick adds stratum diversity to the smoke.
- **Why this one from the 6 FR docs.** `Notice` is under-represented in most parser-benchmark corpora and picking the `rank = 0` document within its stratum gives deterministic-first coverage of it. Single-page documents are too small to properly exercise multi-page handoff; the 13-page proposed rule is a size outlier that would exercise timeout behavior more than baseline plumbing.

### 3.4 What was deliberately NOT considered

- The parser slate. No parser was consulted about any of the three documents.
- Any prior AksharaMD score, warning, or detector output on any of the three.
- The `parsed-vs-raw` benchmark's per-document results, if any exist for these documents.
- Any reviewer's prior labels or feedback on any of the three.

---

## 4. Firewall-verification probe design

`PARSER_EXECUTION_CONTRACT_B1_V1.md` §3.4 makes `network_egress_blocked` a required observable per invocation. This section specifies the probe that populates that field.

### 4.1 Threat model

The threat the probe defends against is **silent network dependency in a parser** — a parser that reaches out to a model server, telemetry endpoint, or license check even when `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and `DOCLING_ARTIFACTS_OFFLINE=1` are set. The probe must not be defeatable by a parser that opens a raw socket to a hard-coded IP.

Non-threats: adversarial exfiltration, timing side-channels, DNS-based covert channels. The smoke's purpose is not adversarial-hardening; it is to confirm the honest failure mode ("parser silently downloads a model at run time") does not occur.

### 4.2 Probe mechanism

Two probes, run **at parser-invocation entry** inside the parser-worker's network context, immediately before the parser call:

1. **TCP-connect probe.** `socket.create_connection(("1.1.1.1", 443), timeout=5.0)`. Expected: raises `ConnectionRefusedError`, `TimeoutError`, or `OSError` with `errno` in `{ECONNREFUSED, ETIMEDOUT, ENETUNREACH, EHOSTUNREACH, EACCES}`. Any successful socket connection → probe FAIL.
2. **HTTPS-GET probe.** `urllib.request.urlopen("https://1.1.1.1/", timeout=5.0)`. Expected: raises `URLError` / `TimeoutError` / a lower-level socket error consistent with egress block. Any HTTP response (any status code) → probe FAIL.

Both probes use the IP address `1.1.1.1` (Cloudflare public DNS resolver, reachable over TCP:443 by design) rather than a DNS name, so a DNS failure alone cannot masquerade as an egress block.

**Total probe wall-clock budget:** ≤ 15 seconds (2 × 5-second per-probe timeout + headroom).

### 4.3 What counts as `network_egress_blocked = true`

`network_egress_blocked = true` iff **both** of the following hold:

- TCP-connect probe raised an exception whose class or `errno` is on the expected-failure list in §4.2.
- HTTPS-GET probe raised an exception whose class or the lower-level `errno` is on the same list.

Any other outcome — either probe succeeds, or either probe fails with an exception outside the expected list — sets `network_egress_blocked = false`, and the invocation is `DEFECT` with `reason = "network_egress_not_blocked"`. This intentionally covers the DNS-failure case: a `socket.gaierror` alone is not on the expected list, and would not be admissible as proof of egress block. (In practice the probe uses a bare IP so `gaierror` should not occur, but if it does we prefer to fail closed.)

### 4.4 Positive control (before smoke)

Before the smoke begins, a **positive-control run** executes both probes from a **separate process** that is NOT under the firewall rule described in §4.5 — same host, same OS, same Python interpreter, but a different process token exempt from the outbound-block rule. Expected: both probes succeed (TCP connect returns, HTTPS returns any HTTP response).

If the positive control does not observe both probes succeeding, **the smoke does not proceed.** The canary is not reachable from this host at this moment (transient Cloudflare outage, upstream ISP issue, local network problem), and any interior `network_egress_blocked = true` observation during the smoke would be uninterpretable — indistinguishable from a real egress block.

The positive-control record is included in the smoke run's top-level log: `positive_control_started_at`, `positive_control_finished_at`, per-probe outcome, and a boolean `positive_control_pass`.

### 4.5 Windows firewall enforcement

The parser-worker process is subject to a Windows Defender Firewall outbound-block rule that matches the parser-worker's `Program` (executable path) or the smoke's per-invocation network profile. The rule is created in advance of the smoke via a PowerShell command of the shape:

```
New-NetFirewallRule -DisplayName "AksharaMD-Smoke-Egress-Block" `
    -Direction Outbound `
    -Program "<parser-worker-executable-path>" `
    -Action Block `
    -Profile Any `
    -Enabled True
```

The rule's existence and enabled state are verified before the smoke via `Get-NetFirewallRule -DisplayName "AksharaMD-Smoke-Egress-Block"`; that verification result is recorded in the smoke run log as `firewall_rule_verified_at`, `firewall_rule_enabled`.

**Cleanup.** The rule is removed at smoke end regardless of outcome. Cleanup failure is a harness-level defect (§7).

### 4.6 Positive control after smoke

After all invocations complete, the positive-control run is executed again. If it fails, that is recorded as `post_smoke_positive_control_pass = false` and the smoke run is marked **INCONCLUSIVE** — a network state change during the smoke could have caused interior observations to be uninterpretable. `INCONCLUSIVE` is not `PASS` and not `FAIL`; the smoke does not proceed to any downstream authorization.

---

## 5. Smoke PASS / FAIL criteria

The smoke answers exactly one question: **does the infrastructure function end-to-end for a `(document, parser)` pair?** All criteria below are infrastructure-level; none evaluates parser output quality.

### 5.1 Execution-count criterion

12 attempts (3 documents × 4 parsers) must each produce a valid `execution_record.json` on disk at the expected path. A missing record is a harness failure. An empty record is a harness failure. A record that does not parse as JSON is a harness failure.

### 5.2 Environment and contract hash criterion

For every execution record:

- `python_version` matches the pinned `3.12.2`.
- `platform_string` starts with the pinned Windows-11 prefix.
- `cuda_version`, `cuda_driver_version`, `cuda_device_name`: for `marker` and `docling`, non-null and reported. For `aksharamd-reference` and `markitdown`, null (per parser-execution contract).
- `parser_package_version` matches the pinned version for the corresponding `parser_id`.
- `parser_execution_contract_version = "v1"`.
- `parser_execution_contract_config_sha256` matches the canonical-JSON SHA of `benchmarks/eval_v1/config/parser_execution_contract_v1.json` as computed at smoke time. Drift is a harness failure.
- `smoke_spec_config_sha256` matches the canonical-JSON SHA of `benchmarks/eval_v1/config/smoke_spec_v1.json` as computed at smoke time.
- `normalization_version = "2"`.

### 5.3 Network-enforcement criterion

- `positive_control_pass = true` (before) and `post_smoke_positive_control_pass = true` (after).
- `firewall_rule_verified_at` populated, `firewall_rule_enabled = true` at smoke start.
- For every execution record, `network_egress_blocked = true` OR the record is `DEFECT` with `reason = "network_egress_not_blocked"`. Silent `false` without `DEFECT` is a harness failure.

### 5.4 Schema completeness criterion

For every execution record, every field enumerated in `parser_execution_contract_v1.json :: execution_record_schema.required_fields` is present and non-null unless nullability applies per `execution_record_schema.nullable_fields`. A missing required field or an unexpectedly-null field is a harness failure.

Timing fields (`wall_clock_seconds`, `cpu_seconds_user`, `cpu_seconds_system`, `peak_rss_bytes`) must be positive real numbers. `peak_vram_bytes` and `cuda_events` non-null for `marker` and `docling`, null for the CPU-only parsers. `output_bytes`, `output_sha256`, `stdout_bytes`, `stdout_sha256`, `stderr_bytes`, `stderr_sha256` present and internally consistent.

### 5.5 Downstream-flow criterion

For every `EXECUTED` invocation:

- `normalized_output.md` exists at the expected path.
- `analysis_record.json` exists and links back to the execution record by `pair_id`.
- The blinded reviewer artifact (`ReviewerArtifact` per `prepare_reviewer_artifact()` in `benchmarks/eval_v1/adjudication.py`) is constructed successfully. This is a structural check — the artifact's fields (`pair_id`, `blinded_parser_hash`, `source_pdf_path`, `extraction_markdown_path`, `normalized_markdown_path`, `questions`, `mapping_reference`) are all populated. **No labels are emitted.** Q1/Q2/Q3 remain unanswered on every smoke pair.
- `blinded_parser_hash` matches `sha256(parser_id)[:16]` per §6 of the reviewer contract; document identity is present per §11.1 of the parser-execution contract; the reviewer artifact has no parser-identity leakage in its rendered surface.

### 5.6 Parser-defect criterion

Parser-level `DEFECT` outcomes are permitted and are not smoke failures. See §7 for the specific rules that make this admissible. A `DEFECT` record must still satisfy §§5.1, 5.2, 5.3, and 5.4 — it just does not need to satisfy §5.5 (downstream flow only applies to `EXECUTED`).

### 5.7 PASS

The smoke **PASSES** iff §§5.1–5.5 are all satisfied. Parser `DEFECT` outcomes are compatible with PASS provided the defects are correctly classified per §7 and the harness records them cleanly.

### 5.8 FAIL

The smoke **FAILS** iff any of §§5.1–5.5 is violated, or if any harness-level defect from §7.2 occurred.

### 5.9 INCONCLUSIVE

The smoke is **INCONCLUSIVE** iff `positive_control_pass = false` (pre-smoke) or `post_smoke_positive_control_pass = false` (post-smoke). `INCONCLUSIVE` is not PASS and is not FAIL; downstream authorization does not proceed and the smoke must be re-run once the canary is reachable.

---

## 6. Review allowlist

Infrastructure reviewers may inspect only the fields enumerated in §6.1. Fields in §6.2 are prohibited on the smoke review surface. The distinction is enforced by presenting the smoke review output through a script that projects each execution record onto §6.1 and refuses to render §6.2.

### 6.1 Inspectable fields (allowlist)

From `execution_record.json`:

- Identifiers: `pair_id`, `canonical_id`, `corpus`, `parser_id`.
- Environment/version/hash fields: `python_version`, `platform_string`, `git_commit`, `cpu_physical_cores`, `cuda_version`, `cuda_driver_version`, `cuda_device_name`, `parser_package_version`, `parser_package_source_sha256`, `parser_model_version`, `parser_model_artifact_sha256`, `adapter_source_sha256`, `model_cache_path`, `normalization_version`, `parser_execution_contract_version`, `parser_execution_contract_config_sha256`, `smoke_spec_config_sha256`.
- Network enforcement: `network_egress_blocked`.
- Timing/resources: `pair_started_at`, `pair_finished_at`, `wall_clock_seconds`, `cpu_seconds_user`, `cpu_seconds_system`, `peak_rss_bytes`, `peak_vram_bytes`, `cuda_events`.
- Byte counts + hashes: `output_bytes`, `output_sha256`, `stdout_bytes`, `stdout_sha256`, `stderr_bytes`, `stderr_sha256`.
- Status: `exit_status`, `defect_reason`.

From the file system:

- File existence: `raw_output.md`, `raw_output.meta.json`, `normalized_output.md`, `stdout.txt`, `stderr.txt`, `execution_record.json`, `analysis_record.json`.
- Encoding readability: whether each file opens as valid UTF-8. Whether `raw_output.md` and `normalized_output.md` parse as CommonMark without error. **Not what they contain.**
- Byte-length and SHA-256 self-consistency: whether the recorded `output_sha256` matches the SHA of the on-disk `raw_output.md`. Whether `output_bytes` matches the file size.

From the analysis record and reviewer artifact:

- Schema validity: whether `analysis_record.json` conforms to its expected schema and links back to the execution record.
- Stage matrix: per-stage `StageStatus` (`EXECUTED / NOT_APPLICABLE / DEFECT / REQUIRES_REVIEW / INFRASTRUCTURE_READY_NOT_EXECUTED`) and coded `reason` strings.
- Reviewer-artifact construction: `blinded_parser_hash` present and 16-hex-char, `source_pdf_path` and extracted/normalized paths populated, `questions` populated with the frozen Q1/Q2/Q3, `mapping_reference` set to `appendix_b_v1 / v1`. **Q1/Q2/Q3 are not answered** on any smoke pair.

From the smoke run log:

- Positive-control results (pre and post), firewall rule verification, smoke start/end timestamps, per-invocation timing summary.

### 6.2 Prohibited fields (explicit blocklist)

Reviewers must **not** inspect:

- **Semantic content** of `raw_output.md`, `normalized_output.md`, or the reviewer artifact's rendered surface. Reading the markdown to form a quality judgment is prohibited even in "just a glance" mode.
- Any AksharaMD readiness score, severity band, or per-detector warning list that may have been produced during a downstream stage (the smoke does not emit these, and if any surface them incidentally, that surface is treated as blocklist too).
- Per-detector output: `W_DROPPED_CONTENT`, `W_GIBBERISH`, `W_PLACEHOLDER_STUB`, `W_ENCODING_ARTIFACTS`, `W_TABLE_MISSING`, `W_MULTICOLUMN_ORDER`, `W_HEADER_FOOTER_TABLE_GARBLED`.
- Ground-truth overlap metrics: PMC-OA word-set overlap against JATS canonical body, DocLayNet region-presence agreement, CUAD span presence.
- Q1 / Q2 / Q3 labels (there should be none in the smoke, but if any appear, that is a §7.2 harness defect and the labels must not be inspected before the defect is remediated).
- Cross-parser comparison of any of the above (e.g., "the reference parser produced longer markdown than marker for document X").

If a reviewer inadvertently sees any prohibited field, the smoke's downstream authorization is treated as compromised, and the review is discarded and re-conducted with a fresh reviewer.

### 6.3 Mechanical file-integrity verification

The user's nuance: someone must verify output files are genuinely produced rather than corrupt/unreadable, and this must be done mechanically, not by reading content. The following mechanical checks are part of the review allowlist:

- **File exists** at expected path.
- **Byte length** matches `output_bytes` in the execution record.
- **SHA-256** of file bytes matches `output_sha256` in the execution record.
- **UTF-8 decode** succeeds without error.
- **CommonMark parse** succeeds without error, for `.md` files. The parse's output structure (block count, heading count, paragraph count) may be inspected as a proxy for "the file has some markdown structure." **The parse's text content may NOT be inspected.**

Any check-list result is recorded in the smoke review log as a boolean per file per check.

---

## 7. DEFECT handling

### 7.1 Parser-level defects — continue

A parser-level `DEFECT` on any `(document, parser)` pair does NOT stop the smoke and does NOT trigger a retry or a document replacement. The defect is recorded as an observed infrastructure outcome and the remaining executions proceed.

Parser-level defect reasons (from `PARSER_EXECUTION_CONTRACT_B1_V1.md` §6):

- `<parser>_timeout_<n>s`
- `<parser>_exception:<Class>`
- `<parser>_cuda_oom`
- `<parser>_cuda_unavailable`
- `<parser>_native_crash`
- `network_egress_not_blocked` — this is on the boundary; it is treated as a parser-level defect for the affected `(document, parser)` invocation, not a harness-level defect, because the firewall itself is verified per §4 and its verification is a harness-level check separate from the per-invocation observation. The remaining executions continue.

Recording the defect requires the same schema completeness as an `EXECUTED` invocation (§5.4) — the smoke's PASS criterion depends on every attempt producing a valid execution record, regardless of parser outcome.

### 7.2 Harness-level defects — stop

A harness-level defect stops the smoke immediately, and the smoke is marked **FAIL**. Harness-level defects include but are not limited to:

- **Firewall verification broken.** `Get-NetFirewallRule` returns unexpected state, or the pre-smoke positive control finds probes succeeding *and* the interior parser-worker also finds them succeeding — the rule is not in force.
- **Schema writer broken.** Any execution record is missing a required field, is invalid JSON, or fails to write to disk.
- **Environment drift.** Any execution record's `python_version`, `platform_string`, `parser_package_version`, `parser_execution_contract_version`, `parser_execution_contract_config_sha256`, or `smoke_spec_config_sha256` differs from the pinned value at smoke start.
- **Provenance mismatch.** Any `analysis_record.json` fails to link to its `execution_record.json` by `pair_id`, or points to a non-existent execution record.
- **Blinding broken.** `blinded_parser_hash` in any reviewer artifact does not equal `sha256(parser_id)[:16]`, or the reviewer artifact's rendered surface contains a parser-identity string (`marker`, `docling`, `markitdown`, `aksharamd`, `pymupdf`, etc.).
- **Firewall rule cleanup broken.** At smoke end the outbound-block rule cannot be removed or is left in a partially-configured state.
- **Positive control drift.** `positive_control_pass = false` at smoke start (smoke does not proceed, per §4.4). `post_smoke_positive_control_pass = false` at smoke end (marks the smoke `INCONCLUSIVE` per §5.9).

Every harness-level defect requires a coded reason to be recorded in the smoke run log before the smoke exits. Silent stop is itself a harness defect.

### 7.3 What the smoke does NOT do about defects

- Does not retry parser invocations.
- Does not substitute a different document for a document whose parser DEFECT'd.
- Does not adjust parser configuration between the pre-execution spec and the executions.
- Does not modify the firewall rule during the smoke.

Any of the above would be a change to the instrument mid-experiment, which is precisely what B1a-7b.1 was designed to prevent.

---

## 8. Explicit unresolved decisions

Deferred to B1a-7c or later; not decided by this document:

1. **Whether smoke `INCONCLUSIVE` on post-smoke positive-control failure requires re-running or investigating first.** B1a-7b.2 marks it `INCONCLUSIVE`; the policy for what to do next belongs with the study-freeze authorization step.
2. **Whether the smoke's `execution_record.json` files are retained after B1a-7c freeze** or re-generated when the held-out study runs. This is a study-lifecycle decision.
3. **The exact allowlist projection script.** §6 fixes the field set; the script that enforces the allowlist on the review surface is a B1a-7b.2 harness detail that ships with the harness code, not with this spec.
4. **Cloud parsers, `mineru`, `unlimited_ocr`.** Excluded from V1 per PROTOCOL_V1 §7. Not smoked.

---

## 9. Machine-readable configuration (informative)

The concrete values in §§3–7 are captured in `benchmarks/eval_v1/config/smoke_spec_v1.json`, committed alongside this document in the same PR. Its canonical-JSON SHA is recorded per invocation in `execution_record.json :: smoke_spec_config_sha256`. Drift in that hash at execution time is a fail-closed condition.

---

## 10. Change log

- **v1 (this document, B1a-7b.2 pre-execution).** Initial draft. Docs/config only. The 12 parser executions this document plans are NOT AUTHORIZED. Downstream (B1a-7c, B1b, B1c) NOT AUTHORIZED.
