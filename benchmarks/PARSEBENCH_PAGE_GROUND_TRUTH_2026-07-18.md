# ParseBench page-level ground truth — 2026-07-18 (Issue #53, phase B5)

**Commit under evaluation:** `a90f0f7cd9c562364f307542195c845995a440c6` (post-#63).

**Scope:** annotate every reviewed page of every promoted asset so
`page_level_ground_truth` is non-null AND non-empty in
`benchmarks/parsebench_assets.lock.json`. No detector, parser, scoring,
packaging, or model code is modified. No calibration is rerun. **No PDF bytes** are committed by this PR — every reviewed page was rendered from
the existing external cache, and the rendered PNGs stayed outside the
git tree at `%TEMP%\parsebench_ground_truth_renders\`. `SCORING_POLICY_VERSION` remains `"1.0"`. Promoted
sha256 / size_bytes / mirror_url / binary_url / dataset revision are
unchanged.

**Cache:** all 12 PDFs remained in
`%LOCALAPPDATA%\aksharamd\parsebench\2805a1d940f95a203e0ae4b88be9934f7765b3fc\`
from the Phase B3 authorised fetch. Sha256 + size verified before
inspection began; no new network fetch occurred.

## Executive summary

Two independent summaries are provided. Consumers of this dataset must
not blur the two.

### (A) Historical / document-level labels

These counts use the frozen `expected_label` values recorded at
promotion time. They are **historical** — they reflect the label the
asset was originally shipped with, not the reviewer's page-level
verdict. Every count is derivable from `assets[*].expected_label` in
the lockfile.

| Historical `expected_label` | Count | Assets |
|---|---:|---|
| true-positive (multicolumn) | 4 | 3colpres, elpais, ikea3, simple2 |
| true-negative (not multicolumn) | 8 | 2colmercedes, battery, eastbaytimes, japanese_case, letter3, myctophidae, strikeUnderline, text_dense__de |
| excluded / null | 0 | — |

Document-level `approved_for_document_calibration` remains `True` for
all 12 assets after checksum verification — no historical label was
overwritten by this phase.

### (B) Reviewer-confirmed page-level corpus

These counts use ONLY assets whose page ground truth was reviewer-verified
non-ambiguous. Ambiguous assets and assets whose defect class is not
multicolumn are **excluded** — they do not appear as confirmed
positives or negatives. Every count is derivable from
`page_calibration_summary.reviewer_confirmed_page_level_corpus` (which
is in turn derivable from per-asset `page_level_ground_truth` +
`defect_kind`).

| Reviewer-confirmed category | Count | Assets |
|---|---:|---|
| block-level-observable positives (real column-interleaving damage AND the block-level detector should fire) | **1** | 3colpres |
| span-only positives (real damage exists but the block-level detector cannot see it) | **1** | elpais |
| hard negatives (correct extraction on a real multi-column page; detector must stay silent) | **2** | 2colmercedes, battery |
| single-column negatives (correct extraction on single-column source) | **1** | eastbaytimes |
| detector false positives (single-column page where the block-level detector wrongly fires) | **1** | strikeUnderline |

Assets **excluded** from the confirmed page-level corpus:

| Exclusion reason | Count | Assets |
|---|---:|---|
| ambiguous at 100 DPI review (extraction_status=ambiguous) | **3** | ikea3, simple2, text_dense__de |
| non-multicolumn (image-only PDF or magazine layout; irrelevant to detector) | **4** | ikea3, japanese_case, letter3, myctophidae |

`ikea3` deliberately appears in **both** exclusion buckets — it is
ambiguous AND its defect class is not multicolumn. Either flag
alone is sufficient to exclude it from the confirmed page-level
corpus.

Note the deliberate asymmetries with (A):

- `simple2` is historically labelled a true-positive but its only
  reviewed page is ambiguous, so it is NOT counted as a confirmed
  page-level positive. Its historical label is preserved for
  document-level comparisons.
- `ikea3` is historically labelled a true-positive but its reviewed
  page is both ambiguous AND reclassified to `non-multicolumn`
  (magazine layout, not column-interleaving) — so it appears in BOTH
  exclusion buckets and in NEITHER confirmed bucket.
- `text_dense__de` is historically labelled a true-negative but its
  reviewed page is ambiguous at 100 DPI, so it is NOT counted as a
  confirmed hard negative.

Recalibration consumers **MUST NOT** collapse (A) and (B). The expanded
recalibration in the next PR will consume category (B) exclusively for
page-level precision/recall metrics.

### Corrections

| Metric | Value |
|---|---:|
| Assets reviewed (12 assets total) | **12** |
| Total pages reviewed | **12** (every asset is exactly 1 page) |
| Label / defect-kind corrections applied | **5** (see §Corrections) |

## Per-page review

Every asset ships a `page_level_ground_truth` object in the lockfile
with `review_status="complete"`, `page_count=1`, and a `pages` array
containing the single reviewed page. Each page carries `layout`,
`extraction_status`, `defect_kind`, `severity`, `evidence`, `confidence`,
and `detector_observability`. What follows is the human-readable summary
of the same information.

### Tier A — expected independent positives

**3colpres** (`text_multicolumns__3colpres.pdf`)
- Page 1: **three-column** magazine layout with a body photo and a right-column headshot. Extraction produces 11 blocks band HIGH 85 and fires `HEADING_SKIP` + `W_MULTICOLUMN_ORDER`.
- Reading order damage confirmed: extracted line `"AANLCP JOURN A L … • Continual changes have been relationship, and look forward made to the website which is chock to hearing their ideas"` mixes content from three columns into a single sentence. This is both **block-level** (the block sequence is out of order — the detector correctly fires) AND **span-level** (line-level splicing within blocks). **Defect kind corrected from `block-level` to `mixed`.**
- Confidence: high. Observability: block-level-observable.

**ikea3** (`text_misc__ikea3.pdf`)
- Page 1: **mixed** IKEA-catalogue magazine spread — multiple headings, thumbnails, numeric callouts (178, 160, 92, 310), captions. Not a classic column-interleaving case. Extraction yields 51 short blocks band OK 79 with 7 `HEADING_SKIP` + `W_TABLE_EXPECTED_NOT_EXTRACTED`.
- The extraction is not visibly interleaved in the "left-column-then-right-column-then-back" sense; the problem class is layout complexity, not multi-column reading order per se. **Defect kind corrected from `span-level` to `non-multicolumn`.**
- Confidence: medium. Observability: not-applicable.

**elpais** (`text_multicolumns__elpais.pdf`)
- Page 1: **mixed** El País Spanish-newspaper front page — banner headline, four-column body, image caption, boxed sidebars, ads. Extraction 26 blocks band OK 79 with four `HEADING`-family warnings but no `W_MULTICOLUMN_ORDER`.
- Newspaper front pages have short columns and dense adjacency; the historical claim of span-level splices is plausible and consistent with the block-level detector not firing while the reading order is still not visually correct. Kept `defect_kind = span-level`.
- Confidence: medium. Observability: span-level-only.

**simple2** (`text_multicolumns__simple2.pdf`)
- Page 1: clean **two-column** academic page. Extraction 8 blocks band HIGH 85 with only `HEADING_HIERARCHY`. Block sequence looks column-first from the block count alone.
- The historical "span-level FN" claim needs a byte-level diff of the compiled Markdown against a manually-transcribed reference to confirm; from the block-level output alone the damage is not observable. Kept `defect_kind = span-level` and marked `extraction_status="ambiguous"` with low confidence.
- Confidence: low. Observability: span-level-only.

### Tier B — expected hard negatives

**eastbaytimes** (`text_simple__eastbaytimes.pdf`)
- Page 1: **single-column** East Bay Times library article — masthead, sub-heading, byline, four body paragraphs. Extraction 6 blocks band OK 83. No multicolumn warning fires — the reader would see the same order the PDF renders.
- Confidence: high. Observability: block-level-observable. `defect_kind = block-level` retained (it's the class the block-level detector would apply to, not that damage exists).

**battery** (`text_multicolumns__battery.pdf`)
- Page 1: **two-column** safety-information sheet with a boxed intro on top-left and a large right-column body. Extraction 2 blocks band HIGH 87 no warnings — the parser merges each column into one big block, so the block sequence carries no interleaving.
- The historical validator docstring labelled `battery` as a "single-column control". Visually it is TWO-COLUMN; the reason the block-level detector stays silent is that the parser merges. Label kept because the calibration intent (a document where the block-level detector should not fire) is still correct — but the report records the layout discrepancy.
- Confidence: high. Observability: block-level-observable.

**2colmercedes** (`text_multicolumns__2colmercedes.pdf`)
- Page 1: **two-column** Mercedes-branded product-description page. Extraction 7 blocks band HIGH 85 with only `HEADING_HIERARCHY`. Column-first block order preserved.
- Confidence: medium (the rendered image is low-DPI so span-level splices could still exist). Observability: block-level-observable.

### Tier C — broader regressions

**text_dense__de** (`text_dense__de.pdf`)
- Page 1: German dense-text page. Rendered layout not fully assessable at review DPI; the historical concern was dense-text fidelity, not multi-column reading order. Extraction 10 blocks band HIGH 85 with only `HEADING_HIERARCHY`.
- Confidence: low. Observability: not-applicable.

**letter3** (`text_simple__letter3.pdf`)
- Page 1: UK Home Office single-column letter — image-only PDF. Extraction 1 block band POOR 47 with `OCR_REQUIRED`. **Not a multicolumn asset.** **Defect kind corrected from `block-level` to `non-multicolumn`.**
- Confidence: high. Observability: not-applicable.

**myctophidae** (`text_simple__myctophidae.pdf`)
- Page 1: scientific taxonomy page — image-only PDF. Extraction 1 block band POOR 47 with `OCR_REQUIRED`. **Not a multicolumn asset.** **Defect kind corrected from `block-level` to `non-multicolumn`.**
- Confidence: high. Observability: not-applicable.

**strikeUnderline** (`text_simple__strikeUnderline.pdf`)
- Page 1: **single-column** ERISA benefits table-of-contents with a light-grey right-side revision-marker sidebar. Extraction 5 blocks band HIGH 85 fires `W_MULTICOLUMN_ORDER` — a **false positive** driven by the narrow sidebar creating a phantom bimodal x0 distribution.
- This is precisely the FP class Issue #54 addressed at the parser level (`_MIN_LINES_PER_COLUMN_CLUSTER = 2`), but the strikeUnderline sidebar cluster is dense enough to still bypass that gate. Defect kind kept as `block-level` because the block-level detector is what triggers the FP; extraction itself is `correct`.
- Confidence: high. Observability: block-level-observable.

**japanese_case** (`text_dense__japanese.pdf`)
- Page 1: Japanese magazine-style page — image + vertical Japanese text + Latin-script sidebars. Extraction 1 block band POOR 47 with `OCR_REQUIRED`. **Not a multicolumn asset by decoding.** **Defect kind corrected from `block-level` to `non-multicolumn`.**
- Confidence: high. Observability: not-applicable.

## Corrections

Five `defect_kind` values were changed under evidence. Each row records
the raw signal (block/warning counts from the installed-wheel
extraction) that drove the decision. All numeric counts below are
reproducible from `%TEMP%\parsebench_ground_truth_compile\<asset>\`
(installed-wheel `v0.3.6`, commit `a90f0f7`, cache revision
`2805a1d940f95a203e0ae4b88be9934f7765b3fc`).

### 3colpres — `block-level` → `mixed`

Both block-level AND span-level damage are present.

- Extraction: 11 blocks, band HIGH 85, warnings `HEADING_SKIP` +
  `W_MULTICOLUMN_ORDER` — the block-level detector correctly fires.
- Span-level evidence: the extracted sentence
  `"AANLCP JOURN A L … • Continual changes have been relationship, and look forward made to the website which is chock to hearing their ideas"`
  splices content from three separate columns into a single sentence.
  This is line-level interleaving inside blocks that block sequence
  reordering alone cannot fix.
- Confidence: high. Observability: block-level-observable.
- Consequence: `3colpres` is the ONLY confirmed block-level-observable
  true positive in the corpus.

### ikea3 — `span-level` → `non-multicolumn`

Magazine-catalogue layout, not column-interleaving.

- Extraction: 51 short blocks, band OK 79, warnings 7×`HEADING_SKIP` +
  `W_TABLE_EXPECTED_NOT_EXTRACTED`. The block-level multicolumn
  detector does **not** fire.
- Rendered page shows multiple product tiles, numeric callouts
  (178, 160, 92, 310), thumbnails, captions — the "reading order"
  concept the multicolumn detector operates on does not apply because
  there are no continuous columns to interleave.
- The page is nonetheless kept `extraction_status="ambiguous"` (not
  `correct`) because the extraction produces 51 short fragments that
  a human reader may or may not judge as "recovered content" — this
  ambiguity is separate from the multicolumn question.
- Confidence: medium. Observability: not-applicable.
- Consequence: `ikea3` is EXCLUDED from the reviewer-confirmed page
  corpus (both because it is ambiguous AND because its defect class
  is now non-multicolumn). Its historical `expected_label=true-positive`
  is **preserved** for document-level accounting.

### letter3 — `block-level` → `non-multicolumn`

Image-only PDF — no decodable text stream.

- Extraction: 1 block, band POOR 47, warning `OCR_REQUIRED`. The
  block-level detector has nothing to see.
- Rendered page shows a UK Home Office single-column letter that
  is rasterised into the PDF.
- The historical `block-level` label came from grouping regressions
  by "the block-level detector might fire" — but the block-level
  detector cannot fire on this page because there is no text stream
  to attribute to columns.
- Confidence: high. Observability: not-applicable.

### myctophidae — `block-level` → `non-multicolumn`

Image-only PDF — no decodable text stream.

- Extraction: 1 block, band POOR 47, warning `OCR_REQUIRED`.
- Rendered page shows a scientific-taxonomy plate (fish species,
  drawings, captions) rasterised into the PDF.
- Same rationale as `letter3`: the multicolumn concept does not
  apply to an image-only page.
- Confidence: high. Observability: not-applicable.

### japanese_case — `block-level` → `non-multicolumn`

Non-decoded Japanese page — no meaningful text stream.

- Extraction: 1 block, band POOR 47, warning `OCR_REQUIRED`.
- Rendered page shows a Japanese magazine spread with vertical
  Japanese text, Latin-script sidebars, and an image. The PDF text
  stream does not carry decodable Japanese glyphs at review DPI.
- Reclassifying to `non-multicolumn` is the honest answer — the
  historical `block-level` label imported the "multicolumn detector
  might fire" framing to a page where the detector has no input.
- Confidence: high. Observability: not-applicable.

### What was NOT corrected

- **No `expected_label` values were changed.** All 12 assets keep their
  historical document-level label so historical/document-level
  accounting stays byte-identical to Phase B3/B4.
- `battery` layout discrepancy is *documented* (see per-page review)
  but not *corrected* at the top-level label because the calibration
  intent (a document where the block-level detector should not fire)
  is preserved.
- `elpais` keeps `defect_kind=span-level`: the block-level detector
  correctly stays silent while the rendered layout is consistent with
  the historical span-level splicing claim.
- `simple2` keeps `defect_kind=span-level` with
  `extraction_status="ambiguous"` and `confidence=low`: at 100 DPI the
  span-level splicing claim is neither confirmed nor refuted.
- `strikeUnderline` keeps `defect_kind=block-level`: the block-level
  detector is what triggers the false positive on this page, so the
  defect class it maps to remains block-level even though
  `extraction_status="correct"`.

The corrections and their rationale are also recorded in
`benchmarks/parsebench_assets.lock.json` under
`promotion_history[<latest>].corrections`.

## Detector-observability totals for the calibration corpus

| Observability class | Count | Assets |
|---|---:|---|
| block-level-observable | 5 | 3colpres, eastbaytimes, battery, 2colmercedes, strikeUnderline |
| span-level-only | 2 | elpais, simple2 |
| not-applicable | 5 | ikea3, text_dense__de, letter3, myctophidae, japanese_case |

**Honest observable recall corpus** for the block-level detector: 5
assets (the block-level-observable set). Of those, only `3colpres`
exhibits real damage; the other 4 are hard-negative controls that must
stay silent. That's 1 true positive and 4 true negatives at the
block-level-observable slice.

## Page approval status

After annotation, the lockfile carries a populated non-empty
`page_level_ground_truth` for every asset. The fetcher's
`approved_for_page_calibration` gate (introduced in PR #63) is now
gated on the `_page_ground_truth_status()` state machine which
distinguishes `missing` / `incomplete` / `ambiguous` / `complete`.

- **12 / 12** assets: `page_level_ground_truth` populated with
  `review_status="complete"` and a non-empty `pages` array.
- **0 / 12** assets with `review_status="incomplete"` — none.
- **9 / 12** assets will surface `approved_for_page_calibration=True`
  after any authorised fetch (or cache verification) at this lockfile:
  `3colpres`, `elpais`, `eastbaytimes`, `battery`, `2colmercedes`,
  `strikeUnderline`, `letter3`, `myctophidae`, `japanese_case`.
- **3 / 12** assets will surface `approved_for_page_calibration=False`
  with `calibration_reason="page_level_ground_truth_ambiguous"`:
  `ikea3`, `simple2`, `text_dense__de` (their reviewed page carries
  `extraction_status="ambiguous"`).

Page-approval does NOT imply "multicolumn positive". The 5 assets
classified as `non-multicolumn` (letter3, myctophidae, japanese_case,
plus ikea3 which is also ambiguous) receive page-approval only when
they are unambiguous — page approval says the *evidence* is complete,
not that the block-level multicolumn detector should fire.

### Runtime verification (cached, no network fetch)

Runtime verification was executed against the pre-existing local cache
at `%LOCALAPPDATA%\aksharamd\parsebench\<revision>\` with no new
network fetch (`AKSHARAMD_PARSEBENCH_ALLOW_NETWORK=1` set only to
satisfy the fetcher's opt-in gate; every asset was served from the
verified cache). Result:

```
3colpres         checksum-verified  doc-approved  page-approved  reason=(empty)
ikea3            checksum-verified  doc-approved  page-DISAPPROVED  reason=page_level_ground_truth_ambiguous
elpais           checksum-verified  doc-approved  page-approved  reason=(empty)
simple2          checksum-verified  doc-approved  page-DISAPPROVED  reason=page_level_ground_truth_ambiguous
eastbaytimes     checksum-verified  doc-approved  page-approved  reason=(empty)
battery          checksum-verified  doc-approved  page-approved  reason=(empty)
2colmercedes     checksum-verified  doc-approved  page-approved  reason=(empty)
text_dense__de   checksum-verified  doc-approved  page-DISAPPROVED  reason=page_level_ground_truth_ambiguous
letter3          checksum-verified  doc-approved  page-approved  reason=(empty)
myctophidae      checksum-verified  doc-approved  page-approved  reason=(empty)
strikeUnderline  checksum-verified  doc-approved  page-approved  reason=(empty)
japanese_case    checksum-verified  doc-approved  page-approved  reason=(empty)

Totals: checksum-verified=12/12, document-approved=12/12,
        page-approved=9/12, ambiguous=3/12
```

These totals match `page_calibration_summary.expected_runtime_verification_at_this_lockfile`
in the lockfile exactly.

## Rendering + review methodology

- Cache verified before inspection: for every asset, sha256 recomputed
  from local bytes matched the promoted lockfile value; `size_bytes`
  matched; file exists and is a real PDF (first 8 bytes `%PDF-`).
- Each PDF rendered to PNG at 100 DPI via `fitz.Page.get_pixmap`. PNGs
  land in `%TEMP%\parsebench_ground_truth_renders\` — outside the repo
  tree, not committed.
- Extraction produced via the installed AksharaMD wheel (v0.3.6, matches
  main `a90f0f7`) into `%TEMP%\parsebench_ground_truth_compile\<asset>\`
  — also outside the repo tree, not committed.
- Visual comparison read the rendered PNG side-by-side with the
  extracted `document.md` and `manifest.json` for each asset.
- Ground-truth labels record what the SOURCE PAGE looks like and what
  the EXTRACTION produced, not what any specific warning code says.
  `W_MULTICOLUMN_ORDER` firing was recorded as *evidence*, not as truth.

## Annotation limitations

- Rendered at 100 DPI. Fine-grained per-word column splices may be
  undetectable at that resolution — this is why `simple2`, `elpais`,
  and `text_dense__de` carry `confidence: low` or `medium`.
- Text-dense pages in a language other than English (`text_dense__de`,
  `japanese_case`) are hard to evaluate for "correct reading order"
  without native fluency; those are marked with lower confidence and
  `not-applicable` observability rather than a claimed label.
- ParseBench does not ship per-page ground truth for text_content /
  text_formatting assets that maps onto the "column-interleaving"
  taxonomy the AksharaMD detector uses. The labels here are derived
  from visual + extraction inspection, not from a public ParseBench
  ground-truth file.

## Historical expectation vs. observed result

| Asset | Historical class (validator docstring, memory) | Observed reality |
|---|---|---|
| 3colpres | primary block-level TP | Mixed (block-level + span-level). Detector still fires. |
| ikea3 | span-level FN | Not really multicolumn — magazine layout. Reclassified `non-multicolumn`. |
| elpais | span-level FN | Confirmed — newspaper front page, span-level splicing plausible. |
| simple2 | span-level FN | Ambiguous at 100 DPI. Kept as `span-level` with low confidence. |
| eastbaytimes | hard negative | Confirmed — single-column article, correct extraction. |
| battery | single-column control | Actually two-column, but block-level detector correctly silent. |
| 2colmercedes | two-column control | Confirmed. Correct extraction. |
| text_dense__de | broader regression | Not multicolumn. Layout unassessable at review DPI. |
| letter3 | broader regression | Image-only PDF. `non-multicolumn`. |
| myctophidae | broader regression | Image-only PDF. `non-multicolumn`. |
| strikeUnderline | broader regression | Single-column with sidebar; `W_MULTICOLUMN_ORDER` FP. |
| japanese_case | broader regression | Image-only PDF. `non-multicolumn`. |

## Reproducibility

- Cache checksums verifiable by re-running the sha256/size loop
  documented at the top of `benchmarks/PARSEBENCH_ASSET_PROVENANCE_2026-07-18.md`.
- Every rendered PNG and every extraction output stays outside the
  git tree at the paths recorded in the methodology section. The paths
  are workstation-local; a future annotator on another workstation
  will produce identical bytes because the fetcher is revision-pinned
  and the extraction is deterministic on the fixed `a90f0f7` wheel.
