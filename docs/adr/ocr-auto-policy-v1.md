# ADR: OCR Backend Auto Policy v1

## Status

Accepted, shipped in PR #100 (2026-07-22). Extends the OCR-backend
opt-in that shipped in PR 94c (`--ocr-backend`) and PR 99 (tightened
availability invariant + `recommended_command`).

## Context

AksharaMD has two working OCR backends:

* **Tesseract** — CPU-only, always installable, no model download.
  Best for a handful of scanned pages or a document that only needs a
  small OCR pass.
* **Unlimited-OCR (UOC)** — GPU-only, large model download, one-time
  verification receipt. Best when a document contains many scanned
  pages that would otherwise dominate compile time and hurt readiness.

Explicit `--ocr-backend tesseract` and `--ocr-backend unlimited_ocr`
shipped in PR 94c. Users who don't know which backend a given document
would benefit from need a bridge: a sensible automatic choice that
never silently underperforms and never silently fails.

## Decision

Introduce `--ocr-backend auto` as a third value. Auto Policy v1:

```
choose unlimited_ocr when ALL of:
    ocr_required_pages >= 3                            (minimum page floor)
    ocr_required_pages / total_pages >= 0.30           (30% fraction threshold)
    unlimited_ocr.runnable_now is True                 (probe result)

otherwise choose tesseract
```

Fallback semantics:

* When `unlimited_ocr` is preferred but not runnable, auto falls back
  to `tesseract` and emits `AUTO_OCR_BACKEND_FALLBACK` with the
  categorical reason (`hardware_incompatible`, `model_not_installed`,
  `model_not_verified`, or `not_runnable`) and the exact remediation
  command from `BackendAvailability.recommended_command` (when a
  single command applies).
* Every auto run also emits `AUTO_OCR_BACKEND_SELECTED` describing the
  choice and the classification counts that drove it.
* Both warnings are INFORMATIONAL (`max_penalty=0`); readiness is
  unaffected by the two new codes.

Explicit-mode invariants (unchanged from PRs 94c / 97 / 99):

* `--ocr-backend tesseract` always uses tesseract.
* `--ocr-backend unlimited_ocr` fails hard at the CLI probe if UOC
  isn't runnable — no silent fallback.
* Only `--ocr-backend auto` may fall back.

Digital-only docs (zero OCR-required pages): the selector returns
`selected_backend="tesseract"` with no fallback and no warning.
Compile succeeds even when both backends would fail because there is
no OCR to run.

## Rationale

* **Page count alone** would send a 200-page mostly-digital report
  with 5 scanned inserts through UOC, wasting the model-load overhead.
* **Percentage alone** would send a tiny 2-page scan through UOC
  startup for a job Tesseract handles quickly.
* **Pixel / glyph ratios** are harder to explain to a user and harder
  to calibrate against real workloads.
* **Reusing classification** — `pdf.py` already produces
  `ocr_required_pages` for every raw page during Phase 2. Auto Policy
  reads only that count plus the availability probe — no new probes,
  no new I/O.
* **Deterministic** — identical inputs (classification counts +
  availability snapshot) always produce the same `AutoOcrDecision`.
* **Cheap to revise** — thresholds are two constants at the top of
  `auto_selector.py`; changing them requires bumping
  `AUTO_POLICY_VERSION` and updating this ADR.

## Known limitation

The 3-page floor and 30% threshold are heuristic. They have NOT been
calibrated against a labeled benchmark. Future calibration should:

* Sample documents across the OCR-required spectrum (0% ... 100% of
  pages, and 1 ... N absolute pages).
* Measure both quality (readiness delta) and cost (wall time,
  model-load time).
* Fit thresholds to a stated objective (e.g. maximise
  quality-per-second at some cost floor).
* Consider whether thresholds should adapt per document class
  (native_text, scanned, hybrid, layout_heavy, table_heavy).

Until calibration exists, callers who need predictable behaviour
should prefer the explicit backends. Auto Policy v1 is documented as
heuristic in `--help`, in `AUTO_OCR_BACKEND_SELECTED`, and in this
ADR.

## Policy versioning

`AUTO_POLICY_VERSION = "1"` in
`aksharamd/plugins/ocr_backends/auto_selector.py`. Any semantic
change to the rule — thresholds, page floor, fallback semantics, or
warning content — requires bumping the version and updating this
ADR. The version appears in the compile manifest field
`ocr_auto_policy_version` (only when the requested backend was
`auto`) so downstream tooling can pin against a specific policy.

## Manifest schema

PR 100 bumps `schema_version` from `1.3` to `1.4`. The additive
fields are:

* `ocr_backend_requested` — one of `"tesseract"`, `"unlimited_ocr"`,
  `"auto"`. Always populated.
* `ocr_backend_selected` — one of `"tesseract"`, `"unlimited_ocr"`
  (never `"auto"`; that is resolved before writing). Always populated.
* `ocr_auto_policy_version` — populated ONLY when requested was
  `"auto"`. Mirrors `AUTO_POLICY_VERSION`.
* `ocr_auto_decision` — populated ONLY when requested was `"auto"`.
  Structured record with the classification counts, thresholds,
  preferred backend, runnability, fallback reason, and remediation
  command.

For a digital-only document processed under `--ocr-backend auto`,
`ocr_backend_selected="tesseract"` with `fallback_occurred=false` and
`ocr_required_pages=0`.

## Non-goals for v1

* No per-page backend switching within one document — one decision
  per document.
* No hallucination mitigation (that lives inside UOC itself).
* No readiness deductions from auto's two warning codes.
* No model download triggered by auto — the user still runs
  `aksharamd models install unlimited_ocr` explicitly.
* No new probes or classification passes — the selector consumes
  what pdf.py already produces.
* Benchmark calibration is a separate follow-up.

## Related documents

* `docs/adr/ocr_backend_execution_plan.md` — the multi-PR rollout
  plan (94a-94c, 97, 98, 99, 100).
* `docs/adr/ocr_backend_strategy.md` — earlier strategy notes on
  when each backend is preferred.
