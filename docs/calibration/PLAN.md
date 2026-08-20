# USP Calibration — Plan

**Status:** ACTIVE (Phase 0 complete; Phase 1 in progress)
**Date:** 2026-08-20
**Owner:** k2alyan
**Related:**
- `docs/calibration/PHASE_0_INVENTORY.md` — verified state going in
- `docs/calibration/USP_CLAIM_V1.md` — Phase 1 output
- `C:\Users\kalya\parsebench\benchmarks\READINESS_CALIBRATION.md` — frozen contract (v1.0, 2026-07-13)
- `C:\Users\kalya\parsebench\benchmarks\RESCORE_REPORT_V1.md` — last measured state (2026-07-13)

---

## Goal

Convert the AksharaMD readiness score from an unbacked claim into an evidence-backed one, so that the USP ("we tell you how much to trust the transcription, and the number is right") is defensible.

Concretely: run the frozen calibration contract's locked-validation split and challenge split, produce a `WELL-CALIBRATED` verdict per contract §8, and publish the resulting numbers in the README as the operational USP claim.

---

## Anti-goals (what this plan is not)

- **Not a fresh corpus build.** The 75-doc calibration corpus is frozen and lives in the parsebench sibling repo. We do not construct new ground truth in this milestone.
- **Not a new scoring formula.** We adjust score policy (caps/penalties for existing warnings) and add signals for known silent failure classes. We do not redesign readiness.
- **Not v0.5.0 platform work.** Adaptive escalation, typed errors, plugin entry points — all deferred.
- **Not Auto Policy v2.** OCR-routing calibration is a separate downstream milestone. Reuses the corpus but is unblocked by this work, not part of it.

---

## Non-negotiables (from the earlier discussion)

1. Ground truth is done — the hard part; do not rebuild.
2. Never calibrate and validate on the same set. Dev is calibration; locked + challenge are validation. Contract §2.3 enforces this.
3. Version-lock before publishing. Bump `SCORING_POLICY_VERSION` and pin a receipt when the number goes public. Regression job re-runs on any future score change.
4. Ship the number the data supports, not the marketing number.
5. If Phase 3 signal work fails to move the false-safe rate under 10%, we narrow the claim rather than force-ship.

---

## Phases

Each phase has: goal, work, exit criteria, and what happens next.

### Phase 0 — Anchor (COMPLETE)
Goal: know the true starting point.
Work: inventory corpora, GT, harnesses, metrics, current score behavior, historical calibration results.
Output: `PHASE_0_INVENTORY.md`.
Headline finding: 53% raw false-safe rate as of 2026-07-13; two policy shapes for the fix; no fresh corpus needed.

### Phase 1 — Ratify the claim (IN PROGRESS)
Goal: lock exactly what claim we are going to defend and pick the score-policy shape.
Work:
- Adopt frozen contract's HIGH-band claim as-is, or write a scoped variant if metric-quality gaps force it (see contract §4.1 vs `RESCORE_REPORT_V1` observed metric ranges).
- Decide: score-cap-on-alerting-warning (Option A) vs redefine-false-safe-to-exclude-warned (Option B). Recommendation locked in inventory §9.1 = Option A.
- Decide: multimodal_usable dimension — does an image-only PDF with a valid `asset://` reference count as PASS?
- Decide: what to do about the four table-tier confirmed false-safes given the table parser is a separate roadmap.
Output: `USP_CLAIM_V1.md`.
Exit criteria: doc committed; four policy questions have documented answers; success bar for locked+challenge run is written down.
Estimated: 1–2 days.

### Phase 2 — Score-policy landing
Goal: attach score caps / penalties to the two alerting warnings so raw HIGH-band false-safe rate falls.
Work:
- In aksharaMD: modify `compute_readiness_score` to cap or penalize on `W_MULTICOLUMN_ORDER` and `W_HEADER_FOOTER_TABLE_GARBLED` per Phase 1 policy decision. Bump `SCORING_POLICY_VERSION`.
- Add regression tests locking in the new caps.
- Update `docs/readiness-score.md` scoring table.
- Run existing unit/integration tests. Confirm no PASS docs regress.
Exit criteria:
- All aksharaMD tests pass.
- Regression tests for the two warnings assert the new caps.
- `SCORING_POLICY_VERSION` bumped.
- PR merged to main.
Estimated: 3–5 days.

### Phase 3 — Silent-failure recall
Goal: close the remaining silent-failure gap identified by `RESCORE_REPORT_V1` (35% silent rate on 6 docs: ikea3, elpais, simple2, de, strikeUnderline, japanese-if-language-branch-added).
Work:
- Improve `W_MULTICOLUMN_ORDER` recall from 40% → target 80%+ without regressing 100% precision (calibrate against `battery` and `2colmercedes` PASS controls).
- Design and ship a signal for `text_dense__de` class (segmentation/omission).
- Design and ship a signal for `text_simple__strikeUnderline` class (TOC/heading-page-number omission).
- Add regression fixtures for all three.
Exit criteria:
- No silent HIGH-band failures across the dev split (or documented rationale for each remaining one).
- 100% precision preserved on the PASS controls.
- All new signals covered by unit tests.
- PR merged to main.
Estimated: 1–1.5 weeks.
**Risk gate:** if any of these fail to design in a week, timebox and fall back to narrower Phase 4 claim.

### Phase 4 — Run the validation splits
Goal: get a `WELL-CALIBRATED` verdict from the frozen contract.
Work:
- In parsebench: re-run dev split (25 docs) against the updated aksharaMD. Verify raw HIGH-band false-safe rate <10%.
- If dev passes review, open the locked-validation split (35 docs). Run.
- Open the challenge split (15 docs). Run.
- Produce `docs/calibration/READINESS_CALIBRATION_REPORT.md` per contract §8: corpus summary, per-band distributions, hypothesis-test results, false-safe/false-risky lists, verdict, recommended next actions.
Exit criteria:
- Locked and challenge runs complete.
- Verdict is WELL-CALIBRATED or NEEDS-THRESHOLD-ADJUSTMENT (not NEEDS-SCORE-REVISION).
- If NEEDS-THRESHOLD-ADJUSTMENT: adjust band thresholds only, re-run. If NEEDS-SCORE-REVISION: return to Phase 3.
Estimated: 3–5 days for a clean pass; +1 week if threshold adjustment; open-ended if NEEDS-SCORE-REVISION.

### Phase 5 — Publish
Goal: make the claim public and defended by CI.
Work:
- Update README with the exact `text_char_recall`/sentence-recall numbers from the locked+challenge runs.
- Add a "How we know" methodology section pointing to the report.
- Wire a CI regression job that re-runs the dev split on any `SCORING_POLICY_VERSION` change and fails on regression.
- Amend `MEMORY.md` project state.
- Optional: blog post using the report as the source-of-truth.
Exit criteria:
- README carries the calibrated number.
- CI job fails on synthetic regression (test with a deliberate score change).
- `SCORING_POLICY_VERSION` receipt pinned in `docs/calibration/`.
Estimated: 2 days.

---

## Timeline (optimistic to realistic)

| Phase | Optimistic | Realistic | Blocker risk |
|-------|-----------|-----------|--------------|
| 0 | done | done | — |
| 1 | 1 day | 2 days | policy decisions |
| 2 | 3 days | 5 days | test breakage on PASS-band docs |
| 3 | 1 week | 1.5 weeks | signal design difficulty |
| 4 | 3 days | 5 days | dev-split re-review, NEEDS-SCORE-REVISION |
| 5 | 2 days | 2 days | CI plumbing |
| **Total** | **~2.5 weeks** | **~3.5 weeks** | — |

Realistic path assumes one round of policy adjustment in Phase 4 and no `NEEDS-SCORE-REVISION` verdict.

---

## Decision gates

Explicit points where we pause before continuing.

| Gate | Between | Criterion | Fallback |
|------|---------|-----------|----------|
| G1 | Phase 1 → 2 | Claim spec and policy answer signed off | Rework spec |
| G2 | Phase 2 → 3 | All tests green, no PASS-band regression | Adjust caps |
| G3 | Phase 3 → 4 | Silent-failure count meets Phase 1 target on dev split | Timebox exhausted → narrow Phase 4 claim |
| G4 | Phase 4 (dev) → Phase 4 (locked) | Dev raw HIGH-band false-safe rate <10% | Return to Phase 3 |
| G5 | Phase 4 → 5 | Locked verdict = WELL-CALIBRATED or NEEDS-THRESHOLD-ADJUSTMENT | Rework signals or narrow claim |

---

## What comes after this plan (deferred, on purpose)

- **Auto Policy v2 calibration** — OCR routing thresholds. Reuses the corpus. Separate milestone.
- **UOC-benefit prediction** — for OCR-required pages, does layout complexity + coverage predict UOC value over Tesseract? Requires the "targeted OCR-required structural corpus" flagged in the layout-complexity evidence memory.
- **Table parser rewrites** — leader-dots, colspan/rowspan, numeric grids. Currently the four confirmed table-tier false-safes are accepted as parser gaps and handled at the score layer, not the extraction layer.
- **v0.5.0 stable platform** — typed errors, plugin entry points, process isolation. Blocked on this milestone because the platform should stabilize around a calibrated score.

---

## Open questions to close in Phase 1

1. Ratify contract §4.1 as USP claim, or narrow to sentence-recall + rule-order + duplication (the metrics with usable GT quality)?
2. Score-cap Option A (recommended) or false-safe redefinition Option B?
3. Multimodal-usable PASS state — accept or reject?
4. Table-tier docs — score at the readiness layer, or exclude from the false-safe criterion with documented rationale?
