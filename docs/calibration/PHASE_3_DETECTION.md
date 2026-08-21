# Phase 3 — Silent-Failure Detection PR

**Status:** DETECTION-ONLY. Score-cap attachment for `W_TABLE_MISSING` and
`W_ENCODING_ARTIFACTS` is a separate follow-up PR per the
`feedback_detection_vs_scoring_separation.md` project convention.

**Date:** 2026-08-20
**Related:**
- `docs/calibration/PLAN.md` — 5-phase parent plan (Phase 3 = silent-failure recall)
- `docs/calibration/USP_CLAIM_V1.md` — ratified claim spec
- `docs/calibration/PHASE_0_INVENTORY.md` — verified state going in
- `docs/calibration/SCORING_POLICY.md` — Phase 2 caps
- `C:\Users\kalya\parsebench\benchmarks\RESCORE_REPORT_V1.md` — 2026-07-13
  silent-failure baseline (35%, 6/17)
- `C:\Users\kalya\parsebench\benchmarks\MULTICOLUMN_OBSERVATION_REPORT_V2.md`
  — W_MULTICOLUMN_ORDER precision/recall (100% / 40%)
- `C:\Users\kalya\parsebench\benchmarks\TABLE_READINESS_DIAGNOSTIC_DESIGN.md`
  — Trigger A design source

---

## 1. What ships in this PR

### 1.1 Detector: `W_TABLE_MISSING` (Trigger A extended)

Implemented at `aksharamd/plugins/validators/table_missing.py` as
`TableMissingValidator`. Fires on EITHER of two OR-branches:

- **Per-line gate:** `leader_dot_lines >= 3` (initial detector shipped
  with the earlier commit on this branch).
- **Fallback gate:** `total_leader_dot_matches >= 5` (added in this
  revision per PHASE_3_DETECTION.md §3.1 Option A, user-confirmed).

Both sub-conditions use the same regex `r'(\.\s){4,}|\.{5,}'`. The
fallback gate catches the `text_simple__strikeUnderline` failure mode
where the parser emits the entire TOC on a single smushed line (54 total
matches, 1 line) so the per-line gate misses it.

**Skip guards** (per Phase 3 scope):
1. File-type eligibility: `pdf`, `docx`, `doc`, `html` only. Plain-text
   and markdown formats can carry legitimate leader-dot art.
2. `OCR_REQUIRED` suppression: if the structure validator already emitted
   `OCR_REQUIRED`, the leader-dot signal is skipped — image-only pages
   are a separate failure class already surfaced by the OCR pipeline.
3. Tiny-doc guard: less than 20 non-empty lines total → skip.

**Warning maturity:** `candidate`. The trigger has a well-defined positive
class (TOC-as-prose, table-as-prose) and 19 of 21 dev-split docs observed
zero total matches (the two exceptions are the positives ikea3 and
strikeUnderline). Threshold `>= 5` is locked with broad margin (all
controls at 0).

**Detection-only.** No readiness-score cap is attached in this PR. The
maturity is exposed via the `table_missing_diagnostics` metadata dict
(both `leader_dot_lines` and `total_leader_dot_matches` are always
recorded, along with `fired_triggers` when the warning fires) so the
eventual cap PR can respect the maturity-aware pattern already in use for
`W_MULTICOLUMN_ORDER` (cap 69) and `W_HEADER_FOOTER_TABLE_GARBLED`
(cap 84).

### 1.2 New detector: `W_ENCODING_ARTIFACTS`

Implemented at `aksharamd/plugins/validators/encoding_artifacts.py` as
`EncodingArtifactsValidator`. Fires on EITHER of two independent
triggers (both direct evidence of encoding/segmentation pipeline
failure, per PHASE_3_DETECTION.md §3.2 Candidates A + C jointly,
user-confirmed):

- **Trigger A — XML tag residue:** regex
  `r'</?(?:pt|font|span|div|tspan)\d+[^>]*>?'`, fires on
  `xml_fragment_count >= 3`.
- **Trigger C — Mojibake density:** fires on
  `count("�") / len(content) >= 0.005`.

**FP guard on Trigger A:** the `\d+` numeric-suffix requirement excludes
legitimate HTML content. A code block containing `</span>`, `<div>`, or
`</font>` does NOT match (no digit suffix), while PDF-to-XML residue like
`</pt192>`, `</font17>`, and `<tspan42>` does. Design in §3.2 originally
proposed `\d*[^a-zA-Z>]` — the revised form `\d+` is a stricter
implementation that achieves the same FP guard more decisively.

Same skip guards as `W_TABLE_MISSING` (file type, `OCR_REQUIRED`,
tiny-doc).

**Warning maturity:** `candidate`. Diagnostics dict
`encoding_artifacts_diagnostics` records `xml_fragment_count`,
`mojibake_count`, `mojibake_density`, `fired_triggers`, and
`warning_maturity` so the follow-up cap PR can attach a maturity-aware
cap.

### 1.3 Regression fixtures

- `tests/test_plugins/test_table_missing_validator.py` — extended unit
  tests covering the per-line gate, the fallback gate, borderline
  negatives on both gates, and all three guards on both gates.
- `tests/test_plugins/test_encoding_artifacts_validator.py` — full unit
  test suite: regex sanity (numbered fragments match, legit HTML does
  not), Trigger A positive/borderline/legit-HTML negatives, Trigger C
  positive/borderline/silent, combined trigger behaviour, and all three
  guards.
- `tests/test_plugins/test_warning_regression.py` extended with the
  `TestEncodingArtifactsWarning` class, additional
  `test_strike_underline_analogue_warns` case on
  `TestTableMissingWarning`, and updated `_ALERTING_CODES` to include
  `W_ENCODING_ARTIFACTS`.

---

## 2. What did NOT ship, and why

### 2.1 W_MULTICOLUMN_ORDER recall improvement — TIMEBOXED

Recall stays at 40% (2/5 on the calibration positives 3colpres and 4c).[^4c-regression]
The three missed positives (ikea3, elpais, simple2) all have block-level
signals below detector thresholds:

| doc | gap_rel | trans_rate | short_frac | reason missed |
|-----|---------|------------|------------|----------------|
| ikea3 | 0.13 | 0.0 | 0.0 | gap below 0.15 threshold |
| elpais | 0.14 | 0.0 | 0.0 | gap below 0.15 threshold |
| simple2 | 0.59 | 0.12 | 0.33 | has gap; trans + short_frac both under thresholds |

[^4c-regression]: As of 2026-08-20, `text_multicolumns__4c` no longer
    fires `W_MULTICOLUMN_ORDER` on live runs. Post-2026-07-13 the parser
    fix `c4dfe86` (`fix(parser): reject single-line clusters in
    column-boundary detection`) corrected block emission order for 4c
    from column-interleaved to column-first, collapsing the detector's
    `transition_rate` signal from 0.63 (RESCORE_REPORT_V1 era) to 0.02
    (fresh run). The block-level detector is therefore correct — 4c's
    residual FAIL is span-level (broken cross-column heading spans),
    matching the elpais / simple2 failure class. See
    `.claude/worktrees/agent-a28883f2/PHASE_4_4C_INVESTIGATION.md` for
    the full timeline and rescore_high_band_v1.jsonl evidence, and
    `benchmarks/CALIBRATION_DEV_RUN_PHASE4.md` §5.1 for the discrepancy
    that surfaced it. The 40% recall claim above reflects the corpus
    state at Phase 3 landing; the live recall on the block-level
    detector today is closer to 20% (3colpres only) with 4c moved into
    the span-level FN class.

**Attempts considered:**

- **Lower gap threshold from 0.15 to 0.12.** Would enable analysis of
  ikea3/elpais pages, but no data exists on what their true transition
  rate is at gap 0.12 without re-running parsebench (out of scope for
  this PR). Ships blind.
- **Add "wide-gap + moderate short_frac" signal.** Cannot distinguish
  simple2 (gap=0.59, short_frac=0.33) from PASS control eastbaytimes
  (gap=0.87, short_frac=0.33) at block-level aggregates. Would break
  the 100%-precision-on-controls invariant.
- **Trans-rate secondary path lowered.** Same problem — 2colmercedes
  (`trans=0.11, short_frac=0.30`) sits too close to simple2 (`trans=0.12,
  short_frac=0.33`) at block level.

Per the Phase 3 escape hatch and the
`feedback_calibration_reject_criteria.md` memory: reject calibration
candidates that regress observable recall. Recall gain that breaks a
PASS control is directionally worse than no change.

**Indirect improvement via W_TABLE_MISSING:** ikea3 has 21 leader-dot
lines in its output (a TOC), so it fires the per-line gate.
strikeUnderline (1 line, 54 matches) fires the fallback gate.
`W_MULTICOLUMN_ORDER` proper recall is unchanged at 40%; the aggregate
alerting rate on the 5 known multicolumn-target FAIL docs lifts from
2/5 (3colpres, 4c via W_MULTICOLUMN_ORDER) to 3/5 (adds ikea3 via
W_TABLE_MISSING). Span-level detection for elpais and simple2 remains
an open problem (see `docs/MULTICOLUMN_SPAN_DETECTION_DESIGN.md`).

### 2.2 Score caps for `W_TABLE_MISSING` and `W_ENCODING_ARTIFACTS`

Detection-only, per the `feedback_detection_vs_scoring_separation.md`
convention. Follow-up PR wires the caps and bumps
`SCORING_POLICY_VERSION`.

---

## 3. Shipped signal designs (user-confirmed)

### 3.1 `strikeUnderline` — TOC on a single smushed line

**Observation:** `text_simple__strikeUnderline` output is a single line
of 7066 chars containing 54 leader-dot matches. The original Trigger A
rule (`leader_dot_lines >= 3`) does NOT catch this because there is only
ONE line with matches. This is a real gap.

**Shipped shape (Option A — extend Trigger A):**
```python
# fire on EITHER
leader_dot_lines >= 3
# OR
total_leader_dot_matches >= 5
```
- strikeUnderline: 54 total matches → WARN
- ikea3: 21+ total matches → WARN (and per-line gate too)
- All 19 remaining controls: 0 total matches → silent

**Rationale:** The `W_TABLE_MISSING` design doc calls leader-dot presence
"a near-certain indicator that a table or TOC was rendered as prose";
the exact line-vs-match count is a serialization detail, not a
conceptual one. Match-count fallback is a natural extension that
catches the same root cause.

**Threshold:** `total_leader_dot_matches >= 5` — all 20 dev-split
controls observed 0 total matches, so 5 has broad margin. Locked at 5
per user confirmation.

### 3.2 `text_dense__de` — segmentation / omission

**Observation:** Extracted output is 3906 chars covering only ~25% of
GT sentences (dev report). Content shows visible XML tag fragments like
`</pt192><pt193` and mojibake characters (`�`) inside a German patent
document. The failure mode is "content silently dropped or fragmented
because font/glyph decoding partially succeeded."

**Shipped shape (Candidate A + Candidate C jointly):** the two triggers
OR together — either firing means the warning fires.

**Trigger A — XML tag residue:**
```python
XML_FRAGMENT_RE = re.compile(r'</?(?:pt|font|span|div|tspan)\d+[^>]*>?')
# Threshold: >= 3 matches across doc content
```
- Direct evidence of extraction pipeline failure.
- FP guard: the `\d+` numeric suffix requirement excludes legitimate
  HTML. A code block containing `</span>`, `<div>`, or `</font>` does
  NOT match (no digit suffix). PDF-to-XML residue like `</pt192>`,
  `</font17>`, and `<tspan42>` does match. Design docs originally
  proposed `\d*[^a-zA-Z>]` — the tighter `\d+` form achieves the same
  end goal (rule out real HTML) more decisively.

**Trigger C — Mojibake density:**
```python
mojibake_density = content.count("�") / max(1, total_chars)
# Threshold: >= 0.005 (0.5 percent)
```
- Simple, high-signal for encoding failures.
- Risk mitigated by density threshold: a single stray replacement char
  in a long doc has density well below 0.5 percent.

**Rationale:** Both triggers target the same underlying failure
(encoding/segmentation pipeline breakage), have direct textual evidence,
and the two signals together stay silent on all 20 dev-split control
docs.

**Thresholds:**
- XML-fragment regex → `>= 3` matches (de has multiple; controls have 0).
- Mojibake density → `>= 0.005` (de has visible replacement chars;
  controls have density near 0).

---

## 4. Definition of done for THIS PR

- [x] `W_TABLE_MISSING` implemented with unit tests for the per-line
      gate, the fallback gate, and all three trigger guards.
- [x] Warning maturity registered in the diagnostics dict.
- [x] `W_ENCODING_ARTIFACTS` implemented with unit tests for both
      triggers, the FP guard on Trigger A (legit HTML does not fire),
      and all three trigger guards.
- [x] Warning maturity registered in the encoding-artifacts diagnostics dict.
- [x] Existing regression fixture (`test_warning_regression.py`)
      extended with `TestEncodingArtifactsWarning` and updated
      `_ALERTING_CODES`.
- [x] `W_MULTICOLUMN_ORDER` recall — timeboxed at 40% direct + ikea3 via
      W_TABLE_MISSING per-line gate + strikeUnderline via
      W_TABLE_MISSING fallback gate + de via W_ENCODING_ARTIFACTS.
- [x] Targeted tests green locally.
- [x] No changes under `aksharamd/scoring/`.

---

## 5. Follow-up PRs

1. **Cap attachment for `W_TABLE_MISSING` and `W_ENCODING_ARTIFACTS`.**
   Once this PR merges, wire the caps (candidate maturity — same
   treatment as W_MULTICOLUMN_ORDER at 69) and bump
   `SCORING_POLICY_VERSION`. Extend `test_readiness_alerting_caps.py`.
2. **Phase 4 dev-split re-run** on updated aksharaMD (in parsebench)
   confirming ikea3, strikeUnderline, and de all move from silent-failure
   to alerted.
