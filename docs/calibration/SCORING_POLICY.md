# Scoring Policy — Phase 2 + Phase 3 Landing

**Status:** LOCKED with `SCORING_POLICY_VERSION = "1.2"`
**Date:** 2026-08-20 (Phase 2 landed as `0700479`; Phase 3 detection landed as `cac462d`; Phase 3 caps land in this PR)
**Related:** `USP_CLAIM_V1.md` §5.1 (Option A, ratified), `PLAN.md`, `PHASE_0_INVENTORY.md` §9.1, `PHASE_3_DETECTION.md`

This document records the score-policy decisions locked in Phase 2 and Phase 3 of the calibration cycle. It supplements the frozen calibration contract at `C:\Users\kalya\parsebench\benchmarks\READINESS_CALIBRATION.md`.

The score formula itself is not versioned by this document; the *policy* attached to specific warning codes is. Any change to a cap value or the set of warnings that cap must bump `SCORING_POLICY_VERSION` and re-run the calibration dev split.

---

## 1. Version bumps

`aksharamd/scoring/models.py::SCORING_POLICY_VERSION`:

| Version | Landing | Change |
|---------|---------|--------|
| `1.0` → `1.1` | Phase 2 (`0700479`) | Attach caps to `W_MULTICOLUMN_ORDER` and `W_HEADER_FOOTER_TABLE_GARBLED` |
| `1.1` → `1.2` | Phase 3 caps (this PR) | Attach caps to `W_TABLE_MISSING` and `W_ENCODING_ARTIFACTS` |

The version is threaded through `ReadinessResult.scoring_policy_version`, `Manifest.scoring_policy_version`, JSON output, and the CLI display in `cli.py`. Users see the version in the score-deductions panel.

## 2. Alerting warnings and their caps

An **alerting warning** is one that indicates a known extraction failure and score-caps the document out of the HIGH band. The USP claim in `USP_CLAIM_V1.md` §1 depends on this: "HIGH-band documents carry no unaddressed alerting warning."

| Warning | Cap | Band after cap | Maturity | Landing | Rationale |
|---------|-----|----------------|----------|---------|-----------|
| `W_IMAGE_ONLY_NO_USABLE_FALLBACK` (internal id `IMAGE_PLACEHOLDER_NO_FALLBACK`) | 55 | RISKY | stable | pre-Phase 2 | Placeholder-only output with no image bytes is neither text-usable nor multimodal-usable. |
| `W_MULTICOLUMN_ORDER` | 69 | RISKY (top) | candidate | Phase 2 | Reading-order failure — content interleaved between columns. Downstream RAG on order-sensitive queries fails. Precision 100% on 5-doc calibration set. |
| `W_HEADER_FOOTER_TABLE_GARBLED` | 84 | OK (top) | experimental | Phase 2 | Table near page furniture likely misrepresents structure. Precision 100% on n=1 known-positive; softer cap while evidence base grows. |
| `W_TABLE_MISSING` | 69 | RISKY (top) | candidate | Phase 3 | Leader-dot density indicates a TOC/table was flattened to prose. Fires on either `leader_dot_lines >= 3` or `total_leader_dot_matches >= 5` (fallback for smushed-line TOCs). Precision 100% on 20 dev-split controls (all observed 0 matches). |
| `W_ENCODING_ARTIFACTS` | 69 | RISKY (top) | candidate | Phase 3 | XML tag residue (`</pt192>`-style, `\d+` suffix required) at `>= 3` matches OR mojibake density (`�` chars) `>= 0.005`. Direct evidence of encoding/segmentation pipeline failure. |

### 2.1 Why maturity-aware caps

The ratified USP claim (`USP_CLAIM_V1.md` §5.1 Option A) says alerting warnings drop the document out of HIGH. It does not require all alerting warnings to cap at the same value.

Attaching a hard cap to an experimental signal creates asymmetric risk: a single false positive on a real HIGH document silently downgrades it. The `warning_maturity` field (`candidate`, `experimental`) is threaded from validator diagnostics to `DeductionRecord.maturity`. Using it to gradate the cap keeps the USP claim honest while limiting damage from generalization gaps.

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

- Each alerting warning caps at its documented value (Phase 2: 69/84, Phase 3: 69/69, pre-existing: 55).
- Deductions carry the `maturity` value from the validator diagnostics.
- Evidence dict records both counts (`leader_dot_lines`+`total_leader_dot_matches` for `W_TABLE_MISSING`; `xml_fragment_count`+`mojibake_density` for `W_ENCODING_ARTIFACTS`).
- Suppression path fires (`penalty=0`, `suppressed=True`) when score is already below the cap.
- Non-alerting warnings do not cap.
- `SCORING_POLICY_VERSION == "1.2"` in the receipt.

The existing image-only regression suite (`tests/test_readiness_image_placeholder.py`) already locks the `W_IMAGE_ONLY_NO_USABLE_FALLBACK` cap at 55.

## 5. What Phase 3 caps deliberately do not change

- **No detector code changes.** Detection and scoring ship in separate PRs (project convention, memory `feedback_detection_vs_scoring_separation.md`). Detector recall for `W_MULTICOLUMN_ORDER` is still timeboxed at 40% direct recall — see `PHASE_3_DETECTION.md` §2.1.
- **No parsebench-side changes.** The dev-split re-run against Phase 3 lives in Phase 4.
- **No README claim updates.** The USP claim goes public in Phase 5, after locked+challenge runs pass.

## 6. What's still open

- **Phase 4 dev-split re-run** on updated aksharaMD (parsebench sibling repo). First measurement of whether Phase 2 + Phase 3 signals actually got the HIGH-band false-safe rate under 10%.
- **Phase 5 publication.** Once locked+challenge runs pass, publish the calibrated USP claim.
- **Span-level multicolumn detection.** `text_multicolumns__elpais` and `text_simple__simple2` cannot be caught by block-level heuristics without breaking the 100%-precision-on-controls invariant. Requires a separate design track (design doc placeholder: `docs/MULTICOLUMN_SPAN_DETECTION_DESIGN.md`).
