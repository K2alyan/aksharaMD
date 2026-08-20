# Scoring Policy — Phase 2 Landing

**Status:** LOCKED with `SCORING_POLICY_VERSION = "1.1"`
**Date:** 2026-08-20
**Related:** `USP_CLAIM_V1.md` §5.1 (Option A, ratified), `PLAN.md` Phase 2, `PHASE_0_INVENTORY.md` §9.1

This document records the score-policy decisions locked in the Phase 2 landing of the calibration cycle. It supplements the frozen calibration contract at `C:\Users\kalya\parsebench\benchmarks\READINESS_CALIBRATION.md`.

The score formula itself is not versioned by this document; the *policy* attached to specific warning codes is. Any change to a cap value or the set of warnings that cap must bump `SCORING_POLICY_VERSION` and re-run the calibration dev split.

---

## 1. Version bump

`aksharamd/scoring/models.py::SCORING_POLICY_VERSION`: `"1.0"` → `"1.1"`.

Threaded through `ReadinessResult.scoring_policy_version`, `Manifest.scoring_policy_version`, JSON output, and the CLI display in `cli.py`. Users see the version in the score-deductions panel.

## 2. Alerting warnings and their caps

An **alerting warning** is one that indicates a known extraction failure and score-caps the document out of the HIGH band. The USP claim in `USP_CLAIM_V1.md` §1 depends on this: "HIGH-band documents carry no unaddressed alerting warning."

| Warning | Cap | Band after cap | Maturity | Rationale |
|---------|-----|----------------|----------|-----------|
| `W_IMAGE_ONLY_NO_USABLE_FALLBACK` (internal id `IMAGE_PLACEHOLDER_NO_FALLBACK`) | 55 | RISKY (top) | stable | Existing since PR B1 — pre-Phase 2. Placeholder-only output with no image bytes is neither text-usable nor multimodal-usable. |
| `W_MULTICOLUMN_ORDER` | 69 | RISKY (top) | candidate | Reading-order failure = content is present but interleaved between columns. Downstream RAG on order-sensitive queries fails. "Extraction is partial or degraded" (contract §1 RISKY definition) matches. Precision 100% on 5-doc calibration set (`RESCORE_REPORT_V1.md` 2026-07-13). |
| `W_HEADER_FOOTER_TABLE_GARBLED` | 84 | OK (top) | experimental | Table near page furniture likely misrepresents structure. Precision 100% but only n=1 known-positive fixture, so the cap is softer while the evidence base grows. The USP claim ("out of HIGH") is preserved; the softer cap limits FP damage. |

### 2.1 Why maturity-aware caps

The ratified USP claim (`USP_CLAIM_V1.md` §5.1 Option A) says alerting warnings drop the document out of HIGH. It does not require all alerting warnings to cap at the same value.

Attaching a hard cap to an experimental signal creates asymmetric risk: a single false positive on a real HIGH document silently downgrades it. The `warning_maturity` field (`candidate`, `experimental`) is already threaded from validator diagnostics to `DeductionRecord.maturity`. Using it to gradate the cap keeps the USP claim honest while limiting damage from generalization gaps.

Graduation path: when `W_HEADER_FOOTER_TABLE_GARBLED`'s evidence base grows to ≥ 3 known positives with precision ≥ 90%, edit the validator's `warning_maturity` to `"candidate"` and drop the cap to 69. Bump `SCORING_POLICY_VERSION`.

### 2.2 Suppression when already below cap

When a document already scores at or below a warning's cap (e.g. because `OCR_REQUIRED` deducted 40 points), the DeductionRecord is emitted with `penalty=0`, `suppressed=True`, and `suppression_reason="score already <= <cap>"`. This preserves the audit trail: the warning fired, but the cap did not additionally reduce the score.

## 3. Non-alerting warnings

The following remain zero-penalty informational entries. They surface an observation to the user but do not affect the score.

| Warning | Reason |
|---------|--------|
| `W_PDF_ATTACHMENT_IGNORED` | Presence of embedded attachments is a documented product boundary, not an extraction failure. |
| `AUTO_OCR_BACKEND_SELECTED` | Auto Policy v1 selected a backend — informational routing signal. |
| `AUTO_OCR_BACKEND_FALLBACK` | Auto Policy v1 fell back — informational routing signal. |
| `W_PARSE_FALLBACK` | JSON/JSONL parse fell back to raw preservation — content not lost, just typed as text. Scoring effect deferred (issue `#41-B`). |
| `IMAGE_PLACEHOLDER_WITH_ASSETS` | Image-only page but bytes captured for multimodal use — the multimodal path is a valid consumption target. |

## 4. Regression tests

`tests/test_readiness_alerting_caps.py` locks:

- Each alerting warning caps at its documented value.
- Deductions carry the `maturity` value from the validator diagnostics.
- Suppression path fires (`penalty=0`, `suppressed=True`) when score is already below the cap.
- Non-alerting warnings do not cap.
- `SCORING_POLICY_VERSION == "1.1"` in the receipt.

The existing image-only regression suite (`tests/test_readiness_image_placeholder.py`) already locks the `W_IMAGE_ONLY_NO_USABLE_FALLBACK` cap at 55.

## 5. What Phase 2 deliberately does not change

- **No detector code changes.** Detection and scoring ship in separate PRs (project convention, memory `feedback_detection_vs_scoring_separation.md`). Detector-recall improvements for `W_MULTICOLUMN_ORDER` (currently 40%) are Phase 3.
- **No new detectors.** `W_TABLE_MISSING` was deferred to Phase 3 (Option iii in the pre-implementation decision) — the ratified plan groups new-detector work with the other silent-failure signals.
- **No parsebench-side changes.** The dev-split re-run against Phase 2 lives in Phase 4.
- **No README claim updates.** The USP claim goes public in Phase 5, after locked+challenge runs pass.

## 6. What Phase 3 will pick up

- Improve `W_MULTICOLUMN_ORDER` recall (currently 40% — misses `ikea3`, `elpais`, `simple2`).
- Design and ship `W_TABLE_MISSING` (leader-dot detection first per Trigger A from the pre-implementation decision) with the same cap treatment as `W_MULTICOLUMN_ORDER`.
- Design structural signals for `text_dense__de` and `text_simple__strikeUnderline`.

Each Phase 3 signal that ships must bump `SCORING_POLICY_VERSION` again if it adds a cap.
