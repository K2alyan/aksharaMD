# OCR backend rollout — execution plan

**Companion to:** `docs/adr/ocr_backend_strategy.md` (the decision record).
**Purpose:** phase-gated rollout of the dual-backend OCR story (Unlimited-OCR on capable NVIDIA GPUs, Marker everywhere else) with automatic selection + honest warning UX. This document is a **living checklist**: any session that resumes this work reads the *current status* section first, then works only inside the current phase.

**Read every session:**

1. The current-status table below (what phase we're in, what's blocked on what).
2. The current phase's *entry criteria* — do not proceed if any are missing.
3. The current phase's *exit criteria* — advance only when all are satisfied.
4. The *standing rules* section — these override any per-phase step.

Update the current-status table and the change log at the bottom of this file whenever a phase advances or a PR lands.

---

## Current status

| Phase | State | Owner | Notes |
|---|---|---|---|
| A — Evidence | in progress (A0 done, A1 split into A1a/A1b/A1c) | — | A0 complete (revision d549bb9d…, 14-file inventory, MIT license). A1.5 blocked until A1a + A1b + A1c all merged. |
| B — Backend abstraction | not started | — | Blocked on: Phase A exit criteria. |
| C — `aksharamd doctor` | not started | — | Blocked on: Phase B exit criteria. |
| D — `--ocr-backend` flag + warning UX | not started | — | Blocked on: Phase C exit criteria; selection matrix + warning taxonomy + readiness formula committed to this file. |
| E — `aksharamd models` subcommands | not started | — | Can run in parallel with D once B is in. |
| F — Docs + rollout | not started | — | Blocked on: D shipped. |

**Related merged PRs (context):**
`#69` Phase 1 baseline · `#70` PyMuPDF4LLM adapter · `#71` MarkItDown adapter · `#72` Unlimited-OCR scaffold + trusted-code verification (this branch → about to merge).

**Latest ADR revision required after each phase:** flip status field, append the phase's decision paragraph, bump the change log.

---

## Phase A — Evidence

**Goal.** Produce empirical evidence sufficient to make Phase D's auto-selection rule non-arbitrary. Until this is complete, any default-selection code is a guess.

**Entry criteria (A0 gate — must all hold before A0 begins).**
- User has explicitly authorized the ~14 GB `baidu/Unlimited-OCR` weights download.
- User has confirmed the disk-space budget below (§ "Disk footprint").
- User has confirmed the GPU-time budget: A1.5 smoke ≈ 5-10 min, A2 full run ≈ 60-120 min on RTX 3060 (foreground; not backgrounded because it needs to be watched for OOM).

**PRs (strictly sequential; each step is its own PR).**

- **A0 — download-only, no code execution.** Explicit user command downloads the pinned snapshot via `huggingface_hub.snapshot_download` with `allow_patterns` restricted first to safetensors + JSON + tokenizer files, then a second pass fetches `.py` files into a *quarantined* subdirectory. **Invariant:** no `transformers` import, no `AutoModel.from_pretrained`, no execution of any file under the snapshot. Produces a report listing every file downloaded, its size, and its SHA-256. Nothing is trusted yet.
- **A1 — split into three sequential PRs per review discipline (one thing per PR).**

  - **A1a — pinned revision + both manifests + file-set + symlink verification.** Ships:
    - `_UNLIMITED_OCR_MODEL_REVISION` (40-char SHA) constant in the adapter.
    - Committed **runtime manifest** `aksharamd/plugins/ocr_backends/unlimited_ocr_trusted_manifest.json` — **deterministic** (no timestamp, no host-specific fields), containing per-file `sha256`, `size_bytes`, `class`, `required_for_runtime`, `verify_on_every_load`. Uses `manifest_schema_version` (schema shape) and a separate `manifest_id` (stable content identifier, e.g., `"unlimited-ocr-d549bb9d-v1"`).
    - Committed **acquisition inventory** `aksharamd/plugins/ocr_backends/unlimited_ocr_acquisition_inventory.json` — informational, may carry a `generated_at` timestamp and the full 14-file download record including `LICENSE` and the quarantined wheel.
    - Removes the inline `_UNLIMITED_OCR_TRUSTED_CODE_FILES` dict; loader reads the JSON manifest.
    - Rewritten `verify_trusted_code_files()` performing: exact-set validation scoped to the snapshot directory (unknown executable or loader-referenced JSON → refuse; other files → warn or ignore), per-file SHA-256 match, and **canonical-containment symlink rules** (resolve strictly, target must be a regular file inside `<HF_HOME>/models--baidu--Unlimited-OCR/`, presence of `..` in raw link text alone is not a rejection cause).
    - Static-review artifact `docs/security/unlimited_ocr_static_review_d549bb9d.md` recording every finding at this revision (7 `eval()` sites, dormant `torch.load`, hard-coded `.cuda()`, `flash_attn` guard, absence of network calls / subprocess / credential access, safetensors-index consistency, wheel-not-referenced-by-config).
    - Tests: exact-set (missing / modified / partial / extra executable / extra loader-referenced JSON refused; harmless metadata ignored), canonical containment (target inside cache accepted, outside refused, broken refused, case-collision refused), safetensors-index integrity.

  - **A1b — verification receipt.** Ships:
    - `aksharamd/plugins/ocr_backends/verification_receipt.py` implementing the § "Verification receipt design" contract below (full mode + fast mode + invalidation rules).
    - Explicitly **decoupled from `SCORING_POLICY_VERSION`.** Uses its own `verification_implementation_version` constant plus `receipt_schema_version`.
    - Atomic receipt writes (`tempfile` → `os.replace`) with restrictive user-only permissions where the platform supports it.
    - Tests: full → receipt written; fast → 11 non-weight runtime files re-hashed on every load; every invalidation rule triggers a fresh full-verify requirement; receipt tampering caught (path-swap, size-preserving replace, mtime-collision) to the extent the OS provides stable file identity.
    - Documentation call-out: the receipt detects ordinary tampering / cache replacement / stale verification, but is **not tamper-proof against a local attacker able to modify both the cache and the receipt directory.** Fast mode is not equivalent to rehashing.

  - **A1c — runtime security mitigation for `trust_remote_code`.** Ships:
    - Module-local `eval` override in the adapter's `_UnlimitedOcrRunner.load()`:
      **Strict sequence: (1) `verify_trusted_code_files` returns True, (2) import the `modeling_unlimitedocr` module via `AutoModel` / `AutoTokenizer` loading, (3) immediately assign `remote_module.eval = ast.literal_eval` on the loaded module object, (4) assert the override is active AND that no new `eval`/`exec`/`compile`/`__import__` occurrence exists in the loaded module source compared with the A1a static-review baseline, (5) only then instantiate the model and permit inference.** Fail-closed on any assertion failure; never silently log-and-continue.
    - Forces `use_safetensors=True` on every load path (was already true; A1c re-asserts and locks with a test).
    - Malicious-output test suite: replays payloads such as `"__import__('os').system('...')"`, `"os.system('rm -rf /')"`, `"exec('print(1)')"`, `"()__class__.__subclasses__()"`, plus each of the 7 eval-site input shapes (coordinate tuples, dict-with-`line_type`, endpoint strings). Asserts (a) no execution side effect, (b) `ValueError`/`SyntaxError` (or the adapter's controlled wrapper) rather than crash, (c) the affected page receives a structured warning.
    - Tests proving all 7 known eval sites resolve to `ast.literal_eval` after the override.

  **Invariant across all three PRs.** `verify_trusted_code_files()` runs — and must return True — before any `transformers.AutoModel.from_pretrained` call in every code path (benchmark adapter, production port, `aksharamd models verify`). The A1c eval override runs — and its fail-closed assertions must pass — before any model instantiation or inference call in every code path.

- **A1.5 — feasibility smoke test.** Entry: A1a + A1b + A1c all merged. Runs Unlimited-OCR on exactly THREE predeclared assets:
  - one native-text PDF that also has embedded raster imagery,
  - one scanned / image-only PDF from the image-only class,
  - one dense-table or multicolumn layout.

  Concrete asset ids are pinned in this file at A1 kickoff (edit the change log). Records per-asset: model-load latency, first-token latency, peak allocated VRAM, peak reserved VRAM, RSS after run, output validity (non-empty, plausible character-set coverage, no ballooning above 3× expected chars), and whether the second and third assets ran without a Python restart between them; also records cold-cache vs warm-cache timings.

  **Stop conditions (hard, no retry).** Any of: OOM, trusted-code verification failure, unsupported-CUDA-op error, obvious hallucination (invented headings, output > 3× expected char count, fabricated table structure), or model-load taking > 10 min. If any stop condition fires, Phase A halts and Phase A4 records "Marker default; Unlimited-OCR opt-in only" without running A2.

  Publishes `benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_SMOKE_2026-07-20.{md,json}`. Full benchmark-artifact metadata block (§ below) required.

- **A2 — real inference on the 45-asset corpus.** ONLY after A1.5 passes. Two passes: first with `--real --no-deterministic-check`, second `--real` for the deterministic-check. Overwrites the dry-run artifacts at `benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.{md,json}`. No paired Marker rerun in this PR — Marker's Phase 1 numbers already exist. If environment drift forces a Marker rerun, that is a separate PR.

- **A3 — paired human review.** Per § "Paired-review methodology" below. Publishes `benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_HUMAN_REVIEWS.json` and a paired-review report.

- **A4 — decision rule.** Applies the § "Backend-selection decision thresholds" below against the A2 + A3 evidence. Flips `docs/adr/ocr_backend_strategy.md` from `Status: proposed` to `Status: accepted` with the concrete predicate (measured VRAM floor, decision outcome, threshold-by-threshold pass/fail). If any predicate fails, the decision is Marker default; the ADR records which predicate failed.

**Disk footprint (verify before A0).**
- Model shards: ~14 GB.
- HuggingFace download tempfiles + resume state: +50 % transient during download.
- Benchmark scratch (300-DPI PNGs, ~50 pages/asset × 45 assets × ~500 KB): ~1 GB per pass; auto-cleaned by `TemporaryDirectory`.
- Benchmark output MD + JSON: ~50 MB.
- **Preflight requirement.** ≥ 25 GB free on the drive holding `HF_HOME`; ≥ 5 GB free on the drive holding the repo. Both checked explicitly at A0.

**Backend-selection decision thresholds (predeclared before A2 — locked here, cannot be silently loosened).**

All six must hold for "Unlimited-OCR preferred on eligible hardware." Failure of any single predicate pushes the decision to "Marker default; Unlimited-OCR opt-in only." The ADR records the passing/failing result of each predicate individually.

1. **Execution success ≥ 95 % of eligible assets** (≥ 43 / 45): non-empty output, no fatal exception, no trusted-code verification failure.
2. **Severe hallucination count = 0.** Severe = invented headings not present in ground truth, output > 3× expected char count, fabricated table structure. Non-severe (occasional glyph misread, minor reordering) allowed but recorded per asset.
3. **Paired preference ≥ 60 % on OCR-relevant assets.** OCR-relevant = image-only + hybrid document classes (~15 assets in the 28-asset paired-review sample). "Preference" = `unlimited_ocr_better` in the paired review; `both_unusable` counts against Unlimited-OCR.
4. **Peak VRAM ≤ 11 GB** on any single asset. Leaves 1 GB safety margin on 12 GB consumer GPUs.
5. **Runtime**: median per-asset ≤ 60 s; p95 ≤ 300 s. Measured on the RTX 3060 test rig.
6. **No slice-level regression > 10 pp.** Per document-class slice, Unlimited-OCR's execution-success / meaningful-content / structurally-usable rates do not fall more than 10 percentage points below Marker's on the same slice.

Amendment to any threshold mid-Phase-A requires a change-log entry + explicit user sign-off in the conversation transcript (see standing rule 10 and open decision D-8).

**Paired-review methodology (locked before A3 — same discipline as A2 thresholds).**

- **Sample size and stratification.** 28 assets, same count as PyMuPDF4LLM (PR #70) and MarkItDown (PR #71) so cross-adapter reviews stay meaningfully paired. Target stratification:
  - 8 image-only
  - 6 hybrid (mixed image + text pages)
  - 4 dense-table
  - 4 multicolumn / complex layout
  - 3 handwriting or low-resolution
  - 3 native-text with figures

  If the actual eligible-manifest distribution cannot support these counts, the review script surfaces the shortfall and the reviewer explicitly signs off on the reduced sample before A3 begins.

- **Blinding.** Outputs presented as `Output A` and `Output B`. The `A → backend` / `B → backend` mapping is randomized per asset and written to a sealed file `benchmarks/pdf_benchmark_v1_unlimited_ocr_review_key.sealed.json` (not opened until scoring is complete). Left/right column ordering in the review UI also randomized per asset.

- **Rubric (1-5 per axis, per asset).** Completeness, hallucination absence, reading-order fidelity, table-structure fidelity, formatting fidelity. Plus a categorical preference: `A_better | B_better | roughly_equal | both_unusable`. **`roughly_equal` tolerance:** requires the two outputs to differ by ≤ 1 rubric point on every axis AND the reviewer's written evidence to explicitly state the difference is not material.

- **Mixed-outcome handling.** If different rubric axes push in opposing directions, the reviewer writes a one-paragraph justification for the categorical preference. The paragraph is included in the published JSON.

- **Reviewer count.** One primary reviewer suffices for the initial pass. Any asset receives a second-reviewer pass if either (a) the reviewer marks confidence "low," or (b) the total rubric-point delta between A and B is ≤ 2. The second reviewer sees the first reviewer's rubric but not the preference categorization. Disagreement on preference between the two reviewers → asset flagged in the published report and the paired-preference threshold (§ threshold 3) is computed both ways (with and without flagged assets).

**Platform scope (locked for the initial rollout).** The Phase A predicate is deliberately CUDA-only: `cuda_available AND bf16_supported AND vram_gib >= N`. This means every Apple Silicon Mac (M1/M2/M3/M4 of any tier) auto-selects Marker regardless of unified-memory headroom. Rationale:

- The adapter calls `.eval().cuda()` and reads memory via `torch.cuda`; MPS would need a device-selection rewrite.
- `trust_remote_code=True` runs Baidu's `modeling.py` unmodified; that code is CUDA-tested and may hard-code `.cuda()` or use CUDA-only fused kernels.
- Marker is CPU-friendly and behaves well on Apple Silicon, so "Mac = Marker" is a safe default, not a downgrade of the user's experience beyond what Marker delivers on any platform.

**MPS support is on the watch list, not scoped in this plan.** See open decision **D-6** below.

**Exit criteria (all must hold).**
- A0 disk + trust invariants satisfied.
- A1 trusted manifest committed; every fail-closed test passes.
- A1.5 smoke test passed with no stop condition fired.
- A2 real inference completed on every eligible asset (`per_asset[].execution_mode == "real_inference"` for all 45; per-asset failures documented in the report, not silently retried).
- A2 deterministic-check pass populated `per_asset[].deterministic` for every asset where inference succeeded.
- A3 paired review completed per methodology; sealed key opened and mapping recorded.
- Every predeclared threshold in § "Backend-selection decision thresholds" evaluated pass/fail; the ADR records each individually.
- ADR flipped to `accepted` with concrete predicate.

**Risks / open questions to record before starting.**
- If real inference hallucinates on any asset, the paired-review report must call it out — do not silently drop rows.
- If peak VRAM for Unlimited-OCR exceeds threshold 4 (11 GB), threshold 4 fails; a subsequent revisit of the threshold requires an amendment per standing rule 10.
- **Do not** run Phase A on the same commit as any AksharaMD production-code change. Benchmark evidence stands on a frozen production tree.

---

## Benchmark artifact metadata (standing schema, required on every benchmark JSON)

Every benchmark JSON published under this rollout carries this metadata block at the top level. Missing any required field fails schema validation and the artifact is not merged.

```json
{
  "aksharamd_commit_sha": "<40-char SHA of the frozen production tree>",
  "manifest_source": "<manifest filename>",
  "manifest_sha256": "<hex>",
  "model_repo": "baidu/Unlimited-OCR",
  "model_revision": "<40-char SHA>",
  "trusted_manifest_version": "<int>",
  "python_version": "<x.y.z>",
  "pytorch_version": "<x.y.z>",
  "cuda_version": "<x.y or null>",
  "nvidia_driver_version": "<int.int or null>",
  "os_and_arch": "<uname -a equivalent>",
  "gpu_model": "<...>",
  "gpu_vram_gib": <float>,
  "dependency_lockfile_sha256": "<hex of uv.lock>",
  "inference_precision": "bf16",
  "generation_parameters": {"max_length": ..., "no_repeat_ngram_size": ..., "ngram_window": ...},
  "random_seed": <int>,
  "per_asset": [
    {
      "asset_id": "...",
      "runtime_seconds": ...,
      "peak_vram_allocated_mib": ...,
      "peak_vram_reserved_mib": ...,
      "cache_state": "cold" | "warm",
      "process_restarted_before_asset": true | false,
      "failure_classification": "success" | "oom" | "timeout" | "hallucination" | "verify_failed" | "other:<reason>"
    }
  ]
}
```

Reruns that do not populate this block are not comparable to previous artifacts and must not be merged.

---

## Phase B — Shared backend abstraction (refactor only)

**Goal.** Extract an `OcrBackend` Protocol so Phase C's probe and Phase D's flag can talk to *both* backends through one interface. Zero user-visible behavior change.

**Entry criteria.** Phase A exit criteria satisfied and ADR flipped to accepted.

**Protocol shape (locked before B1).**

```python
class OcrBackend(Protocol):
    name: str
    def probe(self) -> BackendCapabilities: ...
        # Zero side effects; no model load; no torch import at call time
        # if the extras are missing. Returns hardware requirements + declared
        # runtime characteristics.

    def validate_installation(self) -> InstallStatus: ...
        # Runs dependency check, cache-presence check, and trusted-manifest
        # verification. Never loads the model. Returns a structured status
        # with a distinct field for each failure mode (deps missing, cache
        # missing, manifest missing, hash mismatch, unknown revision).

    def load(self) -> LoadResult: ...
        # Lazy; heavy. Idempotent — repeated calls return the cached loader.
        # Refuses if validate_installation() has not returned ok.

    def infer_pages(
        self,
        pages: list[PageInput],
        *,
        cancel: CancelToken,
        timeout_s: float | None,
    ) -> list[PageResult]: ...
        # Reuses the loaded model across pages within one call.
        # timeout_s is a per-page best-effort budget; cancel is cooperative
        # (see § Timeout and isolation).

    def release(self) -> None: ...
        # Frees device memory; the next load() re-initializes.

    def device_report(self) -> DeviceReport: ...
        # Reports the actual device + precision the loaded model is on,
        # not just what was requested.
```

`PageResult` carries a structured `failure_reason` enum (`ok | oom | timeout | verification_failed | degraded | unsupported_op | other`) rather than free-form text. `BackendCapabilities` distinguishes `expected_dpi`, `max_batch_pages`, `min_vram_gib`, and `supports_bf16_required`. No implementation-specific globals leak across instances — every test instantiates a fresh backend.

**PRs.**

- **B1 — `OcrBackend` protocol.** New module `aksharamd/plugins/ocr_backends/base.py`. Protocol + `BackendCapabilities` + `PageResult` + `InstallStatus` + `LoadResult` + `DeviceReport` + `CancelToken`. No wiring.
- **B2 — `MarkerBackend` wrapper.** New module `aksharamd/plugins/ocr_backends/marker.py`. Wraps `_get_marker_models` + `_apply_marker_to_image_pages` in `pdf.py`. Marker's per-document batching is preserved — the `infer_pages` implementation calls Marker in whatever grouping Marker prefers, not one page at a time.
- **B3 — `UnlimitedOcrBackend` production port.** New module `aksharamd/plugins/ocr_backends/unlimited_ocr.py`. Ports `_UnlimitedOcrRunner` + `verify_trusted_code_files` + offline enforcement from the benchmark adapter into production. The trusted manifest JSON also moves under `aksharamd/plugins/ocr_backends/`. The benchmark adapter subsequently imports from the production module.
- **B4 — backend registry.** `aksharamd/plugins/ocr_backends/__init__.py` provides `get_backend(name: str) -> OcrBackend | None`. No auto-selection.

**Exit criteria.**
- All existing PDF regression tests pass unchanged.
- `SCORING_POLICY_VERSION` unchanged.
- No new user-visible warnings.
- Bandit/mypy/ruff clean at project scope.
- No implementation-specific global state leaks between test cases (verified by an explicit test that instantiates two `UnlimitedOcrBackend` in the same process and asserts they share no mutable class-level state).

**Risks.**
- `pdf.py` is 2600+ lines with Marker code deeply intertwined. If the wrapper introduces any behavior change (call order, error handling, cache lifetime), that is a bug — surface it, do not paper over it. Extract in the smallest possible steps.

---

## Phase C — `aksharamd doctor`

**Goal.** Give users (and later, auto-selection) an inspectable, honest report of what backends are runnable on this machine — and separately, what this release's policy actually selects.

**Entry criteria.** Phase B exit criteria satisfied.

**PRs.**

- **C1 — probe module.** `aksharamd/system_probe.py`. Reports OS/arch, Python version, GPU vendor + model + VRAM + compute capability + BF16 support, per-backend install status, per-backend model-cache + verification status. The core selection function returns a structured report:
  ```
  {
    "hardware_eligible_backends": [...],
    "installed_backends": [...],
    "verified_backends": [...],
    "recommended_backend": "unlimited_ocr" | "marker" | null,
    "effective_auto_backend": "marker" | "unlimited_ocr" | null,
    "selection_reason_code": "<enum matching a § selection matrix row>",
    "release_policy": "opt_in" | "auto_prefer_unlimited"
  }
  ```
  Zero side effects; no network; no `torch` import at module scope (all deferred).

- **C2 — `aksharamd doctor` CLI.** Wires the probe into `aksharamd/cli.py`. Human-readable + `--json` output. `doctor` explicitly presents both `recommended_backend` and `effective_auto_backend`, and if they differ says why in one sentence (e.g., "Your hardware could run Unlimited-OCR, but this release's `auto` policy still selects Marker during the preview period; pass `--ocr-backend unlimited_ocr` to opt in.").

**Exit criteria.**
- `aksharamd doctor` runs cleanly on a base install (no `unlimited-ocr` extra, no `vision` extra) and reports both backends as `not installed` without crashing.
- `aksharamd doctor` runs cleanly with `[vision]` installed.
- `aksharamd doctor --json` schema locks both `recommended_backend` and `effective_auto_backend`; a test asserts the schema.
- No behavior change to `compile` / `watch`.

---

## Backend selection matrix (locked before D1)

Every row is a required test scenario in `tests/test_ocr_backend_selection.py`. Legend: `M` = Marker, `U` = Unlimited-OCR, `N` = no backend (compile continues without OCR); `fatal` = compile refuses to start with actionable error; `WARN` = per-document warning added; `INFO` = one informational message.

| # | State | `auto` (F3 opt-in) | `auto` (F4 default) | Explicit `marker` | Explicit `unlimited_ocr` | Explicit `none` |
|---|---|---|---|---|---|---|
| 1 | Neither installed | N + WARN `OCR_BACKEND_UNAVAILABLE` | N + WARN | fatal | fatal | N |
| 2 | Marker only, any hardware | M | M | M | fatal + install-hint | N |
| 3 | Unlimited-OCR only, eligible GPU, verified | M + INFO (`preview`) | U | fatal + install-hint | U | N |
| 4 | Unlimited-OCR only, ineligible hardware | N + WARN | N + WARN | fatal + install-hint | fatal + hardware-note | N |
| 5 | Both installed, eligible GPU, U verified | M + INFO (`preview`) | U | M | U | N |
| 6 | Both installed, ineligible GPU | M | M | M | fatal + hardware-note | N |
| 7 | U package installed, weights missing | M (if Marker present) else N + WARN | same | M | fatal + `aksharamd models install` hint | N |
| 8 | U weights present, verification never run | M + WARN `OCR_MODEL_UNVERIFIED` | same | M | fatal + `aksharamd models verify` hint | N |
| 9 | U weights present, verification fails | M + WARN `OCR_MODEL_UNVERIFIED` | same | M | fatal + specific refusal reason | N |
| 10 | Explicit `marker` (regardless of other state) | M (or fatal if not installed) | same | — | — | — |
| 11 | Explicit `unlimited_ocr` | see rows 3-9 for U-present states | same | — | — | — |
| 12 | Explicit `none` | N + INFO `OCR_DISABLED_BY_USER` | same | — | — | — |

Rules encoded in the matrix:

- **Explicit user selection never silently downgrades.** Rows 4/6/7/8/9 fatal on explicit `unlimited_ocr`.
- **`auto` never fatals.** If no backend can run, compile continues in the `none` behavior with a warning in the readiness report.
- **Row 8 ≠ row 9.** Never-verified is a different failure mode from verify-failed; the warning text differs.
- **The "preview" INFO on rows 3 and 5 in F3 disappears in F4** when `auto` is allowed to select U.

---

## OCR warning taxonomy (locked before D2)

| Code | Severity | Affects readiness? | Emitted when |
|---|---|---|---|
| `OCR_BACKEND_SELECTION_INFO` | info | no | `auto` picks Marker over Unlimited-OCR (hardware ineligible or opt-in release) |
| `OCR_BACKEND_UNAVAILABLE` | warn | no | Compile continued without any OCR backend and image-only pages exist |
| `OCR_MODEL_UNVERIFIED` | warn | no | Cache present but trusted-manifest verification never ran or failed; adapter refused to load |
| `OCR_PAGE_TIMEOUT` | warn | yes | A single page exceeded the per-page timeout budget |
| `OCR_PAGE_OOM` | warn | yes | Backend raised OOM on this page; page falls back to text-only extraction |
| `OCR_PAGE_UNRECOVERED` | warn | yes | Page is OCR-eligible and no backend produced usable text |
| `OCR_DEGRADED_BACKEND` | warn | yes (small) | Selected backend loaded but ran in a fallback mode (e.g., MPS silent CPU fallback, GPU-expected but on CPU) |
| `OCR_DISABLED_BY_USER` | info | no | User passed `--ocr-backend none`; OCR-eligible pages exist and were left unrecovered by user's choice |

Only the four `PAGE_*` codes and `OCR_DEGRADED_BACKEND` reduce readiness. Selection / availability / verification / user-opt-out codes surface to the user but do NOT lower the score — hardware and installation choices are not treated as document defects.

Every code carries the standing `detector` field per the auto-memory rule; values: `backend_selection | backend_availability | verification | page_ocr`.

---

## Readiness deduction formula (locked before D2 so detection emits all fields D3 needs)

Applied only for the `PAGE_*` codes and `OCR_DEGRADED_BACKEND`.

- **OCR-eligible page.** A per-page boolean produced by D2 detection. The plan does not fix the internal predicate here (that lives in D2 alongside existing per-page signals in `pdf.py`), only the *contract*: D2 emits `ocr_eligible: bool` on every warning object it produces so D3's aggregator can compute the ratio without recomputing per-page signals.
- **Denominator:** number of OCR-eligible pages in the document.
- **Numerator:** sum over eligible pages of per-page weight:
  - `OCR_PAGE_UNRECOVERED` = 1.0
  - `OCR_PAGE_OOM` = 1.0
  - `OCR_PAGE_TIMEOUT` = 1.0
  - `OCR_DEGRADED_BACKEND` on a page that still produced text = 0.25
- **Formula:** `deduction = min(0.30, 0.30 × numerator / denominator)`. Capped at 30 % of the readiness score. Minimum 0.
- **Duplicate suppression:** a page tagged with multiple `PAGE_*` codes contributes only its highest-weight code.
- **`OCR_REQUIRED` deprecation:** existing `OCR_REQUIRED` is superseded by `OCR_PAGE_UNRECOVERED`. In D3 both emit for one release cycle (backwards-compat). A subsequent PR removes `OCR_REQUIRED`.
- **User-selected `none`:** no `PAGE_*` codes fire. `OCR_DISABLED_BY_USER` fires once per document. Readiness reflects only what the user asked for.
- **Denominator = 0:** deduction = 0. Document has no OCR-eligible pages.

Formula lives in `aksharamd/scoring/models.py` beside `SCORING_POLICY_VERSION` and is snapshot-tested.

---

## Phase D — `--ocr-backend` flag + warning UX

**Goal.** Explicit backend selection + deterministic auto-selection per Phase A evidence + honest warnings when the selected backend runs with reduced capability. Never aborts a compile that would otherwise complete.

**Entry criteria.** Phase C exit criteria satisfied. Selection matrix + warning taxonomy + readiness formula committed in this file above.

**PRs.**

- **D1 — flag plumbing + selection matrix.** Adds `--ocr-backend {auto,marker,unlimited_ocr,none}` to `aksharamd/cli.py`. Implements every row of the § selection matrix. Distinguishes `recommended_backend` vs `effective_auto_backend` per Phase C. No readiness scoring change. Parameterized tests for all 12 matrix rows.
- **D2 — warning UX (detection only, no scoring change).** Emits every warning code from § warning taxonomy with the `ocr_eligible` field the D3 aggregator needs. `SCORING_POLICY_VERSION` unchanged.
- **D3 — scoring integration.** Wires the § readiness formula. Bumps `SCORING_POLICY_VERSION` from `"1.0"` to `"1.1"`. Updates `tests/test_table_quality.py` and `tests/test_table_findings.py` snapshots. Emits both `OCR_REQUIRED` (legacy) and `OCR_PAGE_UNRECOVERED` for one release.

**Exit criteria.**
- All rows of the selection matrix pass their parameterized tests.
- Every warning code emits with correct `detector` field and (where applicable) `ocr_eligible` boolean.
- Readiness formula matches the § spec exactly (snapshot-tested).
- **Manually attested positive-path NVIDIA tests** (mock tests cannot validate CUDA execution). Every case documented in `benchmarks/PDF_BENCHMARK_V1_NVIDIA_POSITIVE_ATTESTATION_2026-XX-XX.md` with exact GPU model + driver version + result:
  - Supported NVIDIA GPU, sufficient VRAM (RTX 3060 12 GB or better)
  - GPU with BF16 but below the § threshold-4 VRAM floor (simulated by capping visible memory via `CUDA_VISIBLE_DEVICES` + `PYTORCH_CUDA_ALLOC_CONF`)
  - GPU at the floor
  - Model installed and verified
  - Explicit `--ocr-backend unlimited_ocr` path
  - `auto` eligibility path (as it will behave post-F4)
  - OOM degradation path where safely reproducible (very-large PDF or intentionally over-large batch)
- Windows / macOS smoke tests pass (Marker default paths).
- Linux without NVIDIA sees Marker chosen and no crashes.

**Risks.**
- Warning UX must survive MCP-server invocations where the user is not watching stdout — all warnings land in the readiness report, not only stderr.
- Scoring bump is user-visible; CHANGELOG must explain the new deduction path so a score regression isn't misread as a bug.
- Mock tests alone cannot certify CUDA behavior; the NVIDIA attestation is the source of truth.

---

## Timeout and isolation (Phase D decision)

Python cannot reliably terminate arbitrary GPU work in-process. Decision for the initial rollout:

- **In-process, cooperative timeout.** Unlimited-OCR runs in the compile process. `CancelToken` and `timeout_s` are advisory; the backend checks them between pages, not mid-inference. A single-page inference that hangs will not be forcibly terminated — the user must interrupt the process.
- **Poisoned CUDA context after OOM.** After the first OOM in a process, `release()` is called and the backend re-loads on the next document. A per-process "consecutive OOM count ≥ 2" triggers a hard failure that recommends restarting the compile process rather than silently continuing with a suspect context.
- **Subprocess isolation is a follow-up, not in scope.** If field reports show hangs or poisoned-context propagation, a follow-up PR adds `--ocr-worker-subprocess` mode. Tracked as open decision D-7.

Documented so users understand "per-page timeout" is a best-effort budget, not a hard kill.

---

## Phase E — `aksharamd models install/status/verify/remove`

**Goal.** Turn the ADR's "reproducible manual install" section into real CLI commands with proper operational discipline.

**Entry criteria.** Phase B exit criteria satisfied. Can run in parallel with D once B is in.

**PRs.**

- **E1 — `models install`.** Operational contract:
  - **Disk preflight.** Refuses to start if free space on the `HF_HOME` drive is less than 1.5× the expected download size (accounting for HF cache overhead + resume state).
  - **License display.** Shows the model's license (MIT for `baidu/Unlimited-OCR`, per the `LICENSE` file in the pinned snapshot and the HuggingFace card metadata) and requires either an interactive `y` confirmation or `--i-accept-license <license-id>` in non-interactive mode. No download without acceptance.
  - **Atomic promote.** Downloads into `<HF_HOME>/.tmp_download.<uuid>/`, verifies trusted manifest against a *quarantined* copy of the `.py` files, then atomically renames into the final snapshot path. Any failure removes the tempdir.
  - **File lock.** OS-level lock on `<HF_HOME>/aksharamd-install.lock` so two concurrent installs cannot corrupt the cache.
  - **Interrupted downloads.** `snapshot_download` resume is used; the atomic-promote step is idempotent.
  - **AksharaMD-owned reference record.** Writes `<HF_HOME>/aksharamd_models.json` listing snapshots this tool installed, with `revision` + `trusted_manifest_version`. `remove` refuses to delete snapshots not in this record.
  - **Verification state.** Recorded per `(revision, trusted_manifest_version)` tuple. Bumping either invalidates prior verification and requires a fresh `models verify`.
  - **Non-interactive mode.** Every prompt has an equivalent flag (`--i-accept-license`, `--yes`, etc.). Detected via `sys.stdin.isatty()` or explicit `--non-interactive`.
- **E2 — `models status / verify / remove`.** Same operational discipline: `status` reports the reference record; `verify` re-runs against the current trusted manifest; `remove` respects ownership and refuses shared paths with an explanation.
- **E3 — plug into `doctor`.** `aksharamd doctor` output cross-references these commands ("run `aksharamd models install unlimited-ocr` to download the weights").

**Exit criteria.**
- Never downloads weights during `compile`.
- Never runs inference when verification has never succeeded for the current `(revision, trusted_manifest_version)` tuple.
- Every operational requirement above has a corresponding test.

---

## Phase F — Docs + gradual rollout

**Goal.** Ship the feature conservatively: opt-in first, `auto`-prefers-Unlimited-OCR only in a later release, and only after the rollback criteria remain unmet.

**Entry criteria.** Phase D shipped. Phase E strongly recommended.

**PRs.**

- **F1 — README + install docs.** Tiered install story: base / `[vision]` / `[unlimited-ocr]` / `[ocr-benchmark]` (dev only). Explicit "one heavy backend per user" guidance.
- **F2 — CHANGELOG.** Records the `SCORING_POLICY_VERSION` bump, the new `--ocr-backend` flag, the new `doctor` and `models` commands.
- **F3 — release with opt-in default.** Ships every mechanism (flag + doctor + models). `auto` resolves to Marker for the duration of the preview period **regardless of hardware**. Selecting Unlimited-OCR requires explicit `--ocr-backend unlimited_ocr` or `AKSHARAMD_OCR_BACKEND=unlimited_ocr`. `doctor` clearly reports `recommended_backend: unlimited_ocr` alongside `effective_auto_backend: marker` on eligible hardware and explains the reason. CHANGELOG explicitly notes preview status and lists rollback criteria.
- **F4 — release with `auto`-prefers-Unlimited-OCR (only after advance criteria met).**

  **Advance criteria (all must hold for ≥ 1 release cycle after F3):**
  - Zero severe-hallucination reports on the issue tracker attributable to Unlimited-OCR.
  - Crash rate on the Unlimited-OCR path < 1 % of documented invocations.
  - OOM rate on eligible hardware < 5 %.
  - Zero install-command failures traceable to `aksharamd models install`.
  - p50 runtime within +50 % of Phase A predeclared threshold 5.

  Flips `auto` to prefer Unlimited-OCR when hardware is eligible AND verification passed. Adds regression tests for every selection-matrix row that changes column. Updates ADR with the default change.

  **Rollback path.** If any advance criterion later regresses, a follow-up release reverts `auto` to Marker and records the reason in the ADR. Rollback is a full patch release, not a config toggle — users get predictable behavior per version.

---

## Orchestration model

The user is the human-in-loop; this document is the shared memory. Between sessions:

- **Orchestrator = the running Claude conversation.** No persistent daemon. Continuity comes from (1) this plan file, (2) `MEMORY.md` in the auto-memory directory, (3) the git history.
- **Session bootstrap:** read *this file first*, then the current phase's most recent commit, then the current PR's CI status.
- **Sub-agent usage:** launch `Explore` agents in parallel for independent research (e.g., "how does `pdf.py` invoke Marker today" alongside "how CLI flags are validated"). Do NOT delegate implementation of a phase step to a sub-agent — implementation stays in the orchestrator's conversation so context and gates are preserved.
- **Autonomous background work:** the only step that benefits is Phase A2 real inference (60-120 min). Runs foreground because it needs OOM watching, not backgrounded.
- **Predeclared-thresholds gate.** Once the Phase A thresholds and paired-review methodology are committed here, they cannot be silently loosened. Amendment requires an explicit change-log entry AND explicit user sign-off in the transcript. Post-hoc adjustment invalidates the decision (open decision D-8).
- **Human sign-off gates:**
  - Downloading the Unlimited-OCR weights (Phase A0 entry).
  - Any predeclared-threshold amendment.
  - Any `SCORING_POLICY_VERSION` bump (Phase D3).
  - Merging any PR into `main`.
  - Flipping `--ocr-backend auto` to prefer Unlimited-OCR (Phase F4).

---

## Standing rules (override per-phase details)

1. **One thing per PR.** Detection and scoring do not ship in the same PR. Refactor and behavior change do not ship in the same PR. Two heavy backends do not ship in the same PR.
2. **No cross-parser winner is declared until Phase 3 of the parent Issue #68 benchmark.** Phase A of *this* plan produces per-adapter evidence for a decision inside AksharaMD — it does not name a global winner across the four adapter comparisons.
3. **`SCORING_POLICY_VERSION` bump = advertised behavior change.** Bump exactly once per user-visible scoring change; document in CHANGELOG; update snapshot tests.
4. **Windows test discipline (per CLAUDE.md):** targeted tests first; full suite at most once; GitHub Actions is the source of truth.
5. **Fail-closed on any trust-remote-code path.** `verify_trusted_code_files` is the guard; never bypass it, never "temporarily" disable it during development.
6. **No auto-download of model weights during `compile`.** Ever. Users trigger downloads explicitly through `aksharamd models install`.
7. **Warnings are surfaced in the readiness report, not only on stderr.** MCP-server users must see the same diagnostics as CLI users.
8. **Every multi-stage warning carries a `detector` field** per the auto-memory rule on `warning_diagnostics_schema`.
9. **Every phase update touches this file and the change log below** before opening the PR.
10. **Predeclare, then measure.** Any decision criterion involving numerical thresholds (VRAM, success rate, preference percentage, latency, rollback trigger) is written into this file BEFORE the measurement that judges it. Amendment requires change-log entry + user sign-off.
11. **`recommended_backend` and `effective_auto_backend` are separate concepts.** Doctor, CLI docs, tests, and warning messages all use both terms explicitly. Do not collapse them.
12. **Every backend-related warning carries a code from the § warning taxonomy.** No ad-hoc warning strings.
13. **Every benchmark artifact carries the § benchmark artifact metadata block.** Reruns without it are not comparable and must not be merged.

---

## Open decisions (record as they resolve)

- **D-1 · Auto-default policy.** Ships in Phase F3 as opt-in and flips in F4 subject to advance criteria. Alternative: skip the two-release cadence and flip in F3 if Phase A evidence is overwhelming. User decides at Phase F kickoff.
- **D-2 · Where the trusted-code hash manifest lives after Phase B3.** Two candidates: (a) `aksharamd/plugins/ocr_backends/unlimited_ocr_trusted_manifest.json` alongside the backend module, (b) a shared `aksharamd/plugins/ocr_backends/trusted_manifests/` directory. Prefer (a) unless a second backend also needs remote-code verification.
- **D-3 · MCP-server presentation of degradation warnings.** Whether the warning stream is a separate MCP resource or embedded in the compile response payload. Defer to Phase D2.
- **D-4 · CPU-only fallback for Unlimited-OCR.** ADR currently forbids CPU inference. If a future release lifts this (int8 quantization, ONNX Runtime), it is a separate ADR revision, not covered here.
- **D-5 · Whether `[ocr-benchmark]` stays a public extra or moves to `[dependency-groups]`.** Deferred to Phase E kickoff.
- **D-6 · Apple Silicon (MPS) support for Unlimited-OCR.** *Status: watch.* Initial rollout is CUDA-only; every Mac defaults to Marker. Revisit when: (a) Baidu publishes an MPS-tested release, (b) a credible community report shows Unlimited-OCR running on M-series with acceptable fidelity, (c) we get an M-series Mac in the test rotation. **Trigger action:** open a follow-up ADR `ocr_backend_mps_support.md`, add an MPS branch to the Phase A predicate (`mps_available AND bf16_supported AND unified_memory_gib >= N`), rerun the 45-asset benchmark on an M-series Mac, and if it passes flip `auto` so capable Macs prefer Unlimited-OCR. Until then this stays open; check upstream at each phase kickoff and record any signal in the change log.
- **D-7 · Subprocess isolation for hard timeouts.** Deferred per § "Timeout and isolation." Revisit if field reports show hangs or poisoned-context propagation.
- **D-8 · Threshold-amendment protocol.** Any mid-Phase-A revision to a predeclared threshold requires explicit change-log entry + user sign-off. Amendments are public. Rejection: silently loosening a threshold after seeing results invalidates the decision.
- **D-9 · Second-reviewer selection for paired human review.** Whether the second reviewer for low-confidence assets is the same person on a different day, an LLM-assisted review, or explicitly the user. Defer to A3 kickoff.

---

## Change log

- **2026-07-20** — plan created. All phases in `not started` state.
- **2026-07-20** — Apple Silicon scope decision recorded (open decision D-6, CUDA-only initial rollout).
- **2026-07-20** — plan revised per reviewer feedback (15 items). Changes: Phase A entry split into A0 (download-only) → A1 (hash + commit) → A1.5 (feasibility smoke test) → A2 (full run); predeclared decision thresholds locked (6 predicates); paired-review methodology locked (stratification, blinding, rubric, mixed-outcome handling, reviewer count); `recommended_backend` vs `effective_auto_backend` separation introduced across Phase C / D / F; complete 12-row backend-selection matrix added; 8-code OCR warning taxonomy added; readiness deduction formula defined before D2; benchmark-artifact metadata schema mandated as standing requirement; trusted manifest changed from inline dict to committed generated JSON with symlink/traversal/case-collision/partial tests; Phase E expanded with atomicity + license acceptance + file lock + ownership + non-interactive requirements; Phase B protocol expanded with lifecycle methods (`probe / validate_installation / load / infer_pages / release / device_report`) and structured `failure_reason`; Phase D exit criteria gained NVIDIA positive-path attestation; timeout / isolation decision made in-process cooperative for v1 with subprocess-isolation as follow-up (D-7); Phase F3/F4 rewritten with two-concept language and explicit advance + rollback criteria; standing rules 10-13 added; open decisions D-7 / D-8 / D-9 opened.
- **2026-07-20** — model-license correction. Both the strategy ADR and this plan (E1 "License display") previously stated Apache-2.0 for `baidu/Unlimited-OCR`; the actual license is MIT (verified from the `LICENSE` file in the initial commit `d549bb9d6a055dbe291408916d66acc2cd5920f6` and from the HuggingFace card metadata `card_data.license: mit`). No functional or behavioral change; documentation only.
- **2026-07-20** — Phase A pinned revision **approved: `d549bb9d6a055dbe291408916d66acc2cd5920f6`** (Baidu's initial commit; every subsequent commit is README-only with byte-identical executable + configuration surface). A0 download proceeds restricted to this revision with the reviewer-specified allowlist (`.py`, `.json`, `tokenizer*`, `*.safetensors`, `*.safetensors.index.json`, `*.whl`, `LICENSE`, `LICENSE.txt`). Bundled `sglang` wheel is quarantined executable content — hashed and archive-metadata inspected but NOT installed unless the Transformers inference path is later shown to require it.
- **2026-07-20** — A0 complete. 14 files downloaded, 6.24 GB, zero unexpected files. LICENSE confirmed MIT. `config.json` does NOT reference the bundled wheel or `sglang`/`vllm`/`flash_attn`. Safetensors index consistent: 2,710 tensors, all point to the single approved shard.
- **2026-07-20** — Static security review of the 5 downloaded `.py` files (see `docs/security/unlimited_ocr_static_review_d549bb9d.md`). Clean on network / subprocess / eval-of-builtins / credential access / imports-of-wheel. Real findings: (a) **7 `eval()` calls on model-generated text** in `modeling_unlimitedocr.py:66, 1099, 1101, 1104, 1112, 1113, 1128` — code-injection surface via model output; must be mitigated before load; (b) `torch.load(checkpoint)` at `deepencoder.py:1049` dormant unless `checkpoint=` is passed (AutoModel path does not); (c) 16 hard-coded `.cuda()` calls confirm CUDA-only lock per D-6; (d) `import requests` at `modeling_unlimitedocr.py:6` present but never called (dead import).
- **2026-07-20** — A1 split into **A1a / A1b / A1c** per reviewer feedback (one-thing-per-PR rule). A1a = pinned revision + both manifests + file-set + symlink verification + static-review artifact. A1b = verification receipt (full + fast + invalidation), decoupled from `SCORING_POLICY_VERSION` via a dedicated `verification_implementation_version`. A1c = module-local `eval → ast.literal_eval` override with strict sequencing (verify → import → override → assert → instantiate), forced `use_safetensors=True`, and malicious-output regression tests. **Runtime manifest is deterministic** (no timestamps, no host-specific fields); uses `manifest_id` for stable content identity. **Symlink validation uses canonical containment** (resolved target must be a regular file inside `<HF_HOME>/models--baidu--Unlimited-OCR/`); raw `..` in link text is not a rejection cause. Receipt is documented as protecting against ordinary tampering / cache replacement / stale verification but NOT against a local attacker with write access to both the cache and receipt directory. A1.5 smoke test does not begin until all three merge.
