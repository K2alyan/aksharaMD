# ADR: Local OCR backend strategy — Unlimited-OCR vs. Marker

**Status:** proposed (Phase 2 evidence-gathering PR opens; benchmark decision deferred to follow-up)

**Date:** 2026-07-20

**Related:** Issue #68 (AksharaMD PDF Benchmark v1). Follows the "measure first, integrate second" pattern established by PRs #64 → #69 → #70 → #71.

## Context

AksharaMD has three tiers of PDF extraction:

1. **Lightweight text-layer extraction** (PyMuPDF-driven) — the default for the ~74 % of the benchmark corpus with a usable text layer.
2. **Tesseract fallback** (`aksharamd[ocr]` extra) — invoked in-line when a page has fewer than 80 chars of extractable text and Tesseract is installed. See `aksharamd/plugins/parsers/pdf.py:1517`.
3. **Marker vision enhancement** (`aksharamd[vision]` extra) — Phase 5 post-text-extraction pass over image-only pages when `marker-pdf` is installed. See `aksharamd/plugins/parsers/pdf.py:1909` and the invocation site at line 2472.

The Phase 1 benchmark (PR #69, `main = 581069d`) established that **image-only PDFs are the largest quality gap**: AksharaMD with Marker recovers 4 / 13 image-only files (31 %) vs 1 / 13 for PyMuPDF4LLM and MarkItDown (no OCR). Marker is the single feature that drives AksharaMD's differentiation on this slice.

Baidu released **Unlimited-OCR** on 2026-06-22 as a competitor to DeepSeek-OCR — a document-parsing VLM under the Apache-2.0 license with weights on HuggingFace (`baidu/Unlimited-OCR`, arXiv:2606.23050). Marketed as "one-shot long-horizon parsing." If it materially outperforms Marker on image-only + tabular + multilingual PDFs, we should offer it as the preferred optional high-fidelity backend.

## Decision (proposed)

Adopt a **tiered local-only strategy** with two mutually-exclusive optional extras:

- **`aksharamd`** — base install. Lightweight text-layer path only. No heavy backend.
- **`aksharamd[unlimited-ocr]`** — candidate high-fidelity backend for users with an NVIDIA GPU with BF16 support. The exact minimum VRAM has not yet been measured; see § Risks. The 8 GB / 12 GB figures elsewhere in this document are aspirational until confirmed by real inference.
- **`aksharamd[vision]`** — the current Marker-backed high-fidelity extra. Remains the only installable heavy backend that has been exercised in production. (No `aksharamd[marker]` alias exists today; adding one is a possible future rename tracked as a follow-up, not part of this PR.)

**Users install ONE heavy backend, not both.** A developer-only extra `aksharamd[ocr-benchmark]` is shipped in this PR (pulls both `vision` and `unlimited-ocr`) strictly for internal comparison work; end-user documentation should not surface it. Whether to keep it as a public optional extra or move it to a `[dependency-groups]` entry is deferred to the follow-up benchmark PR, once we know if any non-developer needs it.

The `--ocr-backend {auto,marker,unlimited_ocr,none}` CLI flag (Phase 5 in the prompt) selects the backend explicitly; `auto` picks the installed optional backend and errors clearly when neither is present.

**This decision is contingent on the benchmark evidence.** The comparison run has not yet executed in this PR (see § Deferred work). The current PR ships the adapter, tests, and packaging so the benchmark can run; the actual selection (Unlimited-OCR preferred vs Marker preferred vs hardware-based split) is made in the follow-up ADR revision after inference completes.

## Product constraints (locked)

The final architecture MUST satisfy:

1. Documents never leave the user's computer.
2. No cloud OCR, hosted inference, telemetry, analytics upload, or external document API.
3. Runtime inference works offline after weights are installed.
4. Users do not need to install both backends.
5. Each heavy backend is an independent optional extra.
6. The high-fidelity backend runs only when: document is image-only, native extraction is near-empty, text density is too low, OCR is explicitly requested, or a specific backend is forced.
7. No scoring / warning-penalty changes in the same PR as backend integration.
8. No silent model downloads during document conversion.
9. No document text / images / filenames / metadata / hashes / diagnostics sent over the network.

## Backend protocol (design; implementation deferred to follow-up PR)

```python
# aksharamd/plugins/ocr_backends/base.py (proposed — not yet in this PR)

class OcrBackend(Protocol):
    name: str                          # "marker" | "unlimited_ocr"
    def is_available(self) -> bool: ...        # deps + local model both present
    def local_model_ready(self) -> bool: ...   # weights in local cache
    def capabilities(self) -> BackendCapabilities: ...
    def parse_page_image(self, png: bytes) -> PageResult: ...
    def parse_pdf_pages(self, pdf: Path, pages: list[int]) -> list[PageResult]: ...
    def cancel(self) -> None: ...
```

`PageResult` carries the extracted text or markdown, per-page runtime, per-page GPU memory, backend name, model revision, and hallucination flag. `BackendCapabilities` describes multi-page support, max pages, expected DPI, and hardware requirements.

The existing Marker code stays in `aksharamd/plugins/parsers/pdf.py` for backwards compatibility and is wrapped in a thin `MarkerBackend` implementing the protocol in a follow-up PR.

## Selection rule (`--ocr-backend auto`)

The `auto` selection rule is designed conservatively:

1. If `unlimited_ocr` is installed AND model cached AND GPU meets requirements → use it.
2. Else if `marker` is installed → use it.
3. Else emit an actionable diagnostic:

   ```
   No local high-fidelity OCR backend is installed. This PDF requires
   OCR (image-only page detected). Install one of:
     pip install "aksharamd[unlimited-ocr]"    # candidate on NVIDIA GPU (needs measurement)
     pip install "aksharamd[vision]"           # Marker-backed alternative (CPU-friendly)
   ```

Never automatically fall to the *other* heavy backend if it's not installed. Never automatically download a model during parsing.

## Unlimited-OCR loading contract (security + local-only)

The `trust_remote_code=True` requirement is a real risk surface. Every Unlimited-OCR load MUST:

- Pin an exact model revision (`revision=<40-char SHA>` on `AutoModel.from_pretrained` / `AutoTokenizer.from_pretrained`). A mutable branch reference is refused.
- Use `use_safetensors=True` — refuse pickle-based weights.
- Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` BEFORE `transformers` is imported. Every load call uses `local_files_only=True`.
- Call `verify_trusted_code_files()` **before** any `transformers` import completes. This function is fail-closed: it refuses to load if the revision is unset, if `_UNLIMITED_OCR_TRUSTED_CODE_FILES` is empty, if the snapshot directory for the pinned revision is not on disk, if any file listed in the trusted table is missing or has a different SHA-256, or if the snapshot contains any additional custom `.py` file the trusted table does not cover.
- Never send document text / images / filenames / metadata over the network. The adapter runs after offline enforcement is active; a network attempt inside inference is expected to fail loudly.

A **local-only verification test** (Phase 15 of the prompt) fails if inference attempts an outbound connection. Implementation: monkey-patch `urllib.request.urlopen` and `requests.request` during inference to raise; assert no exception is caught silently.

## Model install workflow

Because Unlimited-OCR weights are ~14 GB, package installation is decoupled from model acquisition:

1. `pip install "aksharamd[unlimited-ocr]"` — installs Python deps (~500 MB inc. torch).
2. `aksharamd models install unlimited-ocr --revision <SHA>` — designed but NOT implemented in this PR:
   - Shows expected source (`baidu/Unlimited-OCR`), pinned revision, expected disk usage.
   - Downloads via `huggingface_hub.snapshot_download` with pinned revision.
   - Verifies SHA-256 of custom Python files against the trusted table.
   - Stores to `~/.cache/huggingface/hub/` (or `HF_HOME`).
   - Fails clearly on incomplete downloads.
3. `aksharamd models status` — reports which OCR backends have model cached, disk usage, pinned revision, hash verification status.
4. `aksharamd models verify unlimited-ocr` — re-hashes custom code, checks revision matches pinned.
5. `aksharamd models remove unlimited-ocr` — removes weights from cache.

**Minimum viable in this PR:** the adapter itself will refuse to run real inference if the model is not cached under the pinned revision **and** its custom `.py` files do not match the trusted SHA-256 table. Each `infer_pdf` invocation renders its pages into a fresh `TemporaryDirectory` (auto-cleaned even on exception) so a deterministic recompile never reads output from a previous run. The user's manual download command is documented below. A follow-up PR wires the `aksharamd models install/status/verify/remove` subcommands into `aksharamd/cli.py`.

### Reproducible manual install (until CLI ships)

```bash
# Verify the revision SHA against
# https://huggingface.co/baidu/Unlimited-OCR/commits before running.
export UNLIMITED_OCR_REVISION=<40-char-SHA>

pip install "aksharamd[unlimited-ocr]"

python -c "
import os, huggingface_hub
huggingface_hub.snapshot_download(
    repo_id='baidu/Unlimited-OCR',
    revision=os.environ['UNLIMITED_OCR_REVISION'],
    allow_patterns=['*.safetensors', '*.py', '*.json', 'tokenizer*', 'preprocessor*'],
    local_files_only=False,
)
"

# Then edit benchmarks/pdf_benchmark_adapters/unlimited_ocr_adapter.py
# to set _UNLIMITED_OCR_MODEL_REVISION to the same SHA and populate
# _UNLIMITED_OCR_TRUSTED_CODE_FILES with the SHA-256 of each .py file
# in the snapshot. Then:

python -m benchmarks.pdf_benchmark_adapters.unlimited_ocr_adapter --real \
   --human-reviews benchmarks/pdf_benchmark_v1_unlimited_ocr_human_reviews.json
```

**No model download happens automatically during document conversion.** The install step is explicit; the parse step is a no-op if weights aren't present.

## Benchmark plan (deferred)

Once the model is installed, run the paired comparison:

- **Corpus:** same 45-asset frozen manifest (`benchmarks/pdf_benchmark_v1_manifest.json`).
- **Modes:** (a) `--only image-only` for the 13 image-only files where OCR quality is decisive, (b) full corpus for coverage.
- **Metrics:** identical to the other adapters (execution / package / meaningful_content / structurally_usable + repeat-content + image-placeholder + hidden-text-layer + peak GPU memory + runtime p50/p95).
- **Human review:** paired review on the same 28 asset ids reviewed for the other adapters. Add per-asset preference field `marker_better | unlimited_ocr_better | roughly_equal | both_unusable`. Every judgment includes concise evidence and an explicit hallucination check.
- **Selection gate:** Unlimited-OCR preferred iff:
  - Higher paired human-usable rate on OCR-relevant files.
  - Materially better recovery on Marker's known weak assets.
  - No unacceptable hallucination pattern (no ballooning outputs, no invented headings).
  - Acceptable page coverage.
  - Stable multilingual behaviour.
  - Manageable runtime (p50 under a documented threshold on RTX 3060 / 12 GB).
  - Manageable peak GPU memory (fits in 12 GB with the pinned batch strategy).
  - Deterministic or sufficiently stable output.
  - Fully local inference (verified by the network-block test).

If Unlimited-OCR's advantage is small, unreliable, or resource-intensive, **prefer Marker**. A hardware-based split ("Unlimited-OCR preferred for NVIDIA GPUs with BF16 and ≥ N GB VRAM measured in the benchmark run; Marker preferred for CPU or lower-resource systems") is acceptable and encouraged if the evidence supports it. The concrete VRAM threshold N is filled in only from measured data.

## What this PR includes

- `benchmarks/pdf_benchmark_adapters/unlimited_ocr_adapter.py` — the benchmark adapter with real-inference code and a dry-run mode. Runs against the same 45-asset manifest as the other adapters.
- `pyproject.toml` — adds `aksharamd[unlimited-ocr]` optional extra.
- `tests/test_pdf_benchmark_unlimited_ocr.py` — adapter shape tests, tool-neutrality invariant, mode-decision logic, mocked inference path.
- `docs/adr/ocr_backend_strategy.md` — this document.
- `benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.{md,json}` — dry-run artifact (no real inference).

## What this PR explicitly does NOT do

- Does not modify `aksharamd/` production parser / validator / scoring / warning-penalty / CLI code. `SCORING_POLICY_VERSION` remains `"1.0"`.
- Does not implement the shared `OcrBackend` protocol module (design only in this ADR).
- Does not add the `--ocr-backend` CLI flag.
- Does not add `aksharamd models install/status/verify/remove` CLI subcommands.
- Does not download the Unlimited-OCR weights.
- Does not run real inference on the benchmark corpus (dry-run only until the user or a follow-up PR triggers the download).
- Does not perform paired human review — that requires real inference output.
- Does not declare a winner between backends.

## Follow-up PRs (sequenced)

1. **`feat(ocr): download + verify Unlimited-OCR model`** — implements `aksharamd models install/status/verify/remove` under an explicit user command; populates the trusted-code hash table.
2. **`bench(ocr): paired Unlimited-OCR vs Marker`** — runs real inference on the 45-asset corpus; produces paired human review; updates this ADR with the empirical decision.
3. **`refactor(ocr): shared OcrBackend protocol`** — extracts the protocol from `pdf.py` and wraps the existing Marker code; adds `MarkerBackend` and `UnlimitedOcrBackend`.
4. **`feat(cli): --ocr-backend flag`** — adds explicit backend selection to the CLI and Python API.
5. **`docs(ocr): finalise ADR with empirical decision`** — updates this ADR from "proposed" to "accepted" with the concrete decision (Unlimited-OCR preferred, Marker preferred, or hardware-split).

Only after the follow-up sequence lands does the end-user installation model change; until then, `aksharamd[vision]` continues to point at Marker as it does today. There is no `aksharamd[marker]` alias; any rename from `vision` to `marker` is a separate follow-up decision, not implied by this ADR.

## Risks

- **Model revision drift.** If Baidu updates the `main` branch, users who copy old install commands could pull unpinned weights. Mitigated by requiring a pinned revision at load time.
- **`trust_remote_code` supply-chain.** Mitigated by pinning revision + SHA-256-hashing the custom code files.
- **VRAM requirement is unknown.** The RTX 3060 test machine has 12 GB. Whether Unlimited-OCR fits in 8 GB, needs the full 12 GB, or exceeds it has NOT been measured because real inference has not yet been executed. The benchmark PR will measure peak GPU memory and set the minimum-VRAM requirement based on empirical observation; until then no specific VRAM figure should be published as a hardware recommendation.
- **Package version mismatch.** The Unlimited-OCR README pins `torch==2.10.0`, `transformers==4.57.1`; the reference environment for the benchmark uses `torch==2.12.1+cu126`, `transformers==4.57.6`. Both are within compatible ranges for the `AutoModel` loading path, but if inference produces unexpected results the version diff is the first thing to investigate.
- **Pillow version.** Reference environment has Pillow 10.4.0; Unlimited-OCR pins 12.1.1. Not upgrading Pillow globally because it would break other packages in the environment. Flagged as a compatibility risk.

## Verification

The dry-run adapter output (`benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.json`) records the exact GPU capability report, the execution-mode decision, and the reason inference was skipped. A follow-up PR overwrites this artifact with the real-inference results.
