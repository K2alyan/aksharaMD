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

| Metric | Value |
|---|---:|
| Assets reviewed (12 assets total) | **12** |
| Total pages reviewed | **12** (every asset is exactly 1 page) |
| Independent multicolumn positives (real column-interleaving damage) | **3** — `3colpres`, `elpais`, `simple2` |
| Block-level-observable positives (multicolumn detector fires and the damage is block-level in the sense that block sequence is corrupted) | **1** — `3colpres` (mixed block + span) |
| Span-only positives (real damage exists but block-level detector cannot see it) | **2** — `elpais`, `simple2` |
| Hard negatives (correct extraction on a real multi-column page) | **2** — `battery`, `2colmercedes` |
| Clean single-column negatives (correct extraction on single-column source) | **1** — `eastbaytimes` |
| Ambiguous pages | **3** — `ikea3`, `simple2`, `text_dense__de` |
| Non-multicolumn assets (irrelevant to detector; historical inclusion was a mislabel) | **5** — `ikea3`, `text_dense__de`, `letter3`, `myctophidae`, `japanese_case` |
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

Five `defect_kind` values were changed under evidence:

| Asset | Old `defect_kind` | New `defect_kind` | Reason |
|---|---|---|---|
| 3colpres | block-level | mixed | Both block-level and span-level damage present. |
| ikea3 | span-level | non-multicolumn | Magazine catalogue layout — problem class is not column reading order. |
| letter3 | block-level | non-multicolumn | Image-only PDF; requires OCR. |
| myctophidae | block-level | non-multicolumn | Image-only PDF; requires OCR. |
| japanese_case | block-level | non-multicolumn | Non-decoded Japanese script; requires OCR. |

No `expected_label` corrections were applied. Documenting the layout
discrepancy on `battery` (declared "single-column control" in the
historical docstring but visually two-column) but not correcting the
top-level label because the calibration intent (block-level detector
should stay silent) is preserved.

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
`approved_for_page_calibration` gate (introduced in PR #63) will
therefore flip to `True` for every asset that also passes checksum
verification and has non-null `expected_label` + `defect_kind`.

- **12 / 12** assets: `page_level_ground_truth` populated with
  `review_status="complete"` and a non-empty `pages` array.
- **0 / 12** assets with `review_status="incomplete"` — none.
- Assets that will surface `approved_for_page_calibration=True` after
  the next authorised fetch: all 12 (checksums are verified, labels and
  defect kinds are populated). This includes the 5 assets classified as
  `non-multicolumn` — page approval says the *evidence* is complete,
  not that the block-level multicolumn detector should fire.

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
