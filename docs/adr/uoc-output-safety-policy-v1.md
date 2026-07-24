# ADR: Output Safety Policy v1 for the Unlimited-OCR backend

Status: accepted
Date: 2026-07-24

## Context

The OCR Auto Policy v1 calibration harness (PR #101) surfaced a
reproducible failure mode of the Unlimited-OCR (VLM-backed) backend:
on image-only pages with little textual structure, the VLM produces
long repetitive n-gram sequences. The harness observed
`max_repeated_ngram_count` values of 159–4358 on the eight synthetic
image-only fixtures under UOC/Auto→UOC, versus a maximum of 3 on any
real-content ParseBench or GeoTopo document. The final Markdown from
these hallucination runs is unsuitable as ingestion input for
downstream LLM tasks, even when the compile pipeline reports it as
"successful" by every existing metric.

Auto Policy v1 chose which backend runs. It does NOT protect callers
from bad output the chosen backend produced. A separate policy is
needed for that.

## Decision

Introduce Output Safety Policy v1 as a runtime output-safety guard
implemented in `aksharamd/plugins/ocr_backends/output_safety.py` and
wired into the PDF parser dispatch (`_apply_alternate_ocr_backend` in
`aksharamd/plugins/parsers/pdf.py`) and the CLI compile command.

The policy inspects per-page UOC output via a sliding-window n-gram
detector and produces a `RepetitionSignal` verdict on each anchor
page. When any anchor page reports `detected=True`, the response
depends on how UOC was chosen.

## The split contract

**Explicit `--ocr-backend unlimited_ocr` → REJECT.**
When the user explicitly requested UOC, the dispatcher raises
`UocOutputRepetitionError` with a structured, bounded payload
(policy version, affected page indices, per-page repetition count and
ratio, bounded 100-char n-gram preview, and 64-hex sha256 fingerprint,
plus a remediation string). The CLI catches this and emits a concise
error (never a Python traceback) and exits non-zero. Tesseract is
never called. No manifest is produced.

Rationale: silently substituting a different backend when the user
explicitly asked for UOC would violate the user's backend choice.
The user asked a specific question about a specific backend; the
right answer is a specific failure, not a substituted success.

**`--ocr-backend auto` → DISCARD + Tesseract fallback.**
When Auto Policy v1 initially selected UOC and the UOC output tripped
Policy v1, the dispatcher discards the entire UOC document result,
re-runs all OCR-required pages via Tesseract, emits an
`AUTO_OCR_BACKEND_FALLBACK_REPETITION` warning exactly once, and
records structured audit fields in the manifest. Tesseract's output
becomes the final package. Fallback is document-level in v1 — no
per-page mixing of backends.

Rationale: Auto's contract is to produce safe output that lets the
downstream pipeline continue. Emitting garbage would defeat that
purpose. Falling back preserves progress; the loud fallback warning
keeps the substitution visible instead of silent.

## Fallback granularity — document-level, not per-page

v1 does not mix Tesseract and UOC pages within a single document. If
any anchor page trips the policy, the entire UOC result is discarded
and every OCR-required page is re-run through Tesseract. Rationale:
per-page mixing would require both backends to agree on block ordering
across boundary transitions, which the current dispatcher does not
support. Per-page granularity is a future extension, not a v1 goal.

## Thresholds are versioned code, not user configuration

Policy v1 uses three fixed thresholds baked into
`aksharamd/plugins/ocr_backends/output_safety.py`:

- `_MAX_REPEATED_NGRAM_COUNT = 50` — 16.7× the highest real-content
  observation from the calibration harness (3), and 3.2× smaller than
  the smallest hallucination signature observed (159). Deliberately
  conservative separation from the current observations, not a
  universal boundary.
- `_MIN_EVALUATED_CHARS = 200` — short outputs cannot establish a
  meaningful repetition ratio; the guard never rejects a legitimately
  tiny page.
- `_MIN_REPETITION_RATIO = 0.10` — the repeated phrase must dominate
  at least 10% of sliding windows. Below this ratio, a high count is
  a recurring heading or refrain in a large body, not garbage.

All three conditions must fire together — no single condition alone
triggers rejection. Any change to any threshold or eligibility gate
bumps `UOC_OUTPUT_SAFETY_POLICY_VERSION`.

Not exposing thresholds as CLI/config in v1 is deliberate. A public
configuration surface would freeze the current numbers before the
detector has been validated against a broader corpus. When the
detector accumulates enough real-world evidence to justify a
user-tunable knob, the corresponding config surface becomes safe to
ship — likely via an ADR bumping the policy version.

## No routing-threshold changes in this milestone

Auto Policy v1's routing thresholds (page floor of 3, OCR-required
fraction of 30%) are untouched. This ADR governs an
*output-safety* concern that runs after routing has already picked a
backend. The calibration harness's flagging thresholds are also
unchanged and continue to fire earlier than the runtime safety guard
by design — the harness is a review signal, the guard is a "definitely
garbage" bar.

## Manifest schema

Bumped `Manifest.schema_version` from `"1.4"` to `"1.5"`. Added seven
optional fields (all `None` when the fallback did not fire, so older
readers see identical shape on the common path):

- `ocr_output_safety_policy_version`
- `ocr_initially_selected_backend`
- `ocr_final_backend`
- `ocr_discarded_backend`
- `ocr_fallback_reason` (currently only `"uoc_output_repetition"`)
- `ocr_affected_page_count`
- `ocr_repetition_signals[]` with per-page: `page_index`,
  `max_repeated_ngram_count`, `repetition_ratio`,
  `evaluated_character_count`, `repeated_ngram_preview`,
  `repeated_ngram_sha256`.

`ocr_backend_selected` retains its historical meaning — the FINAL
effective backend that produced output. On fallback it becomes
`"tesseract"`, matching what legacy readers already expect it to
report.

Every field is bounded. No raw OCR markdown, no unbounded n-gram
excerpts, ever reach the manifest. Reviewers who need to identify
duplicates across pages or documents use the sha256 fingerprint; the
100-character preview is for eyeball inspection only.

## Consequences

- The `unlimited_ocr` backend can now fail in two new ways: explicit
  rejection with `UocOutputRepetitionError` (for `--ocr-backend
  unlimited_ocr`), or silent-to-caller / visible-to-auditor Tesseract
  substitution (for `--ocr-backend auto`).
- Auditors get a distinct field surface. `ocr_backend_selected` alone
  no longer tells the whole story on auto+fallback runs; readers who
  need the initial choice consult `ocr_initially_selected_backend`.
- Downstream tooling that reads `ocr_backend_selected` for
  reporting purposes continues to work unchanged — the field
  semantics are preserved.
- Threshold updates require an ADR + a `UOC_OUTPUT_SAFETY_POLICY_VERSION`
  bump, so the audit trail always identifies which policy generated a
  given fallback record.

## Related

- PR #100 — `--ocr-backend auto` with Auto Policy v1
- PR #101 — OCR Auto Policy v1 evaluation harness (surfaced the
  repetition signal)
- ADR: `docs/adr/ocr-auto-policy-v1.md` — Auto Policy v1 routing
  contract, unchanged by this ADR
