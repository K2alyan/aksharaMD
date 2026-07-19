# Sidebar vs. multicolumn geometry — 2026-07-19 (Issue #50 analysis-only phase)

**Commit under evaluation:** `0847038` (main, post-#65).

**Purpose.** Identify measurable geometric features that distinguish
the `strikeUnderline` sidebar false positive from the `3colpres`
genuine block-level multicolumn true positive, and evaluate whether
any candidate rule silences the FP without disturbing any confirmed
positive or introducing a new FP.

**No production code changes.** No parser, detector, scoring, warning-
penalty, or `SCORING_POLICY` modifications. `SCORING_POLICY_VERSION`
remains `"1.0"`. This PR is evidence only.

**Machine-readable output:** `benchmarks/SIDEBAR_MULTICOLUMN_SIGNAL_ANALYSIS_2026-07-19.json`.

## Corpus (frozen at `0847038`)

| Source | Ids | Count |
|---|---|---:|
| ParseBench block-level-observable positive | `3colpres` | 1 |
| ParseBench block-level-observable negatives (controls) | `2colmercedes`, `battery`, `eastbaytimes` | 3 |
| ParseBench block-level detector false positive | `strikeUnderline` | 1 |
| Public confirmed positive | `multicolumn.pdf` | 1 |
| Public attested negatives (excludes unattested / image-only / sparse-text; includes GeoTopo & GeoTopo-komprimiert as documented FPs) | 22 | 22 |
| **Total** | | **28 assets · 275 eligible pages** |

**Eligibility rule.** Every asset whose block-level detector can
observe the class — i.e., the `block-level-observable` subset of the
ParseBench reviewer-confirmed corpus (`page_calibration_summary`), plus
every attested (non-null `expected_positive`) public label. Span-only
cases (`elpais`, `simple2`) and non-multicolumn cases (`ikea3`,
`letter3`, `myctophidae`, `japanese_case`, `text_dense__de`, plus
ambiguous public labels) are **excluded from the primary metric** and
appear in the appendix only.

Every asset is compiled once via `aksharamd compile --json --quiet`.
No network fetch; ParseBench PDFs are served from the pre-verified
external cache at `%LOCALAPPDATA%\aksharamd\parsebench\<revision>\`
after sha256 + size re-verification. Public PDFs live at their
existing paths under `benchmarks/.public_corpus/pdf/`.

## Cluster reconstruction — the validator's view

The block-level multicolumn detector (`aksharamd/plugins/validators/multicolumn.py :: _analyse_page`) does NOT use `pdf_column_info` for its 2-cluster split. It uses **the largest gap in per-block `metadata.x0`** on each page and treats the midpoint of that gap as the cluster boundary. This analysis reconstructs the same split so every geometric feature is measured against what the detector actually sees.

Boundaries in `pdf_column_info` (which are populated by a separate parser-side column detector) are recorded for reference in `per_page.parser_boundaries` / `num_columns_parser`, but are **not** used in cluster assignment.

**Missing-data caveat.** Blocks carry `metadata.x0` / `metadata.y0` only (block start). They do NOT carry `x1` / `y1`. Vertical-coverage, per-cluster y-range, and block-height metrics are therefore computed from block-start positions and read as underestimates of true coverage. The magnitude of the underestimate is roughly one block-height per cluster. This is called out in every metric that depends on it, in code (`_estimates` field) and here.

## Per-page geometry (defined precisely; all in code)

For every eligible page, we compute:

- Page dimensions (`page_width`, `page_height` in PDF points).
- Parser column info (`num_columns_parser`, `parser_boundaries`) — reference only.
- Validator's 2-cluster split: `validator_boundary_x` (midpoint of largest x0-gap), `validator_gap_size`.
- Per block: cluster assignment (0 = left of validator boundary, 1 = right), `x0`, `y0`, block type, char count, word count.
- Per cluster: block count, chars sum, words sum, y-range (max_y0 − min_y0), y-coverage fraction, block-height proxy (median Δy0 between successive blocks in-cluster), disjoint-run count (splits when Δy0 > 2× block-height proxy), x0 variance, typewise counts.
- Cross-cluster: smaller-cluster identity (fewer chars), text share of smaller cluster, words share of smaller cluster, y0-overlap fraction, top-alignment delta `|min_y0(s) − min_y0(L)|`, bottom-alignment delta `|max_y0(s) − max_y0(L)|`, smaller-cluster disjoint-run count, smaller-cluster y-coverage, alternations across all blocks in reading order, alternations restricted to substantial text blocks (≥ 5 words, meaningful block types — no headings / images / page-breaks / captions / footnotes / metadata).

Baseline signals from `metadata.multicolumn_diagnostics.page_analyses` are echoed unchanged into `per_page.baseline` so no drift is possible between what this harness sees and what the shipped detector saw.

## Decisive comparison: `strikeUnderline` vs `3colpres`

Both are single-page assets. Baseline warns on both.

| Feature | `strikeUnderline` p1 (FP) | `3colpres` p1 (TP) | Discriminates? |
|---|---:|---:|:---:|
| `validator_gap_size` (pt) | 338.3 | 176.6 | weak (sidebar has a larger gap) |
| `smaller_text_share` | **0.003** | **0.010** | ← borderline |
| `smaller_y_coverage_frac` | **0.60** | **0.09** | **YES** — direction opposite of intuition |
| `smaller_disjoint_runs` | 1 | 1 | no |
| `y_overlap_frac` | 1.00 | 1.00 | no |
| `top_alignment_delta` (pt) | 0.0 | **655.0** | **YES** — 3colpres's small cluster starts near the bottom |
| `bottom_alignment_delta` (pt) | 11.5 | 0.0 | no |
| `alternations_all` | 8 | 4 | wrong-way (sidebar has more) |
| `alternations_substantial` | **0** | **1** | **YES** — sidebar has none |

**Reading of the geometry.** `strikeUnderline`'s smaller cluster is the right-margin revision-marker sidebar: an extremely thin (0.3% text share) and tall (60% of page height) column, top-aligned with the main body, and — critically — carrying **zero substantial-block interactions with the main body**. `3colpres`'s smaller cluster is the bottom-right headshot: also thin (1% share) but not tall (9% coverage), and its bottom-of-page location produces a substantial-block alternation with the main body (`alt_substantial = 1`).

A useful side finding: the `3colpres` reading-order damage that fires the block-level warning is not what the earlier per-asset review suggested. From the validator's clustering perspective the three magazine columns are collapsed into one large cluster; the second cluster is only the bottom-right headshot. The warning fires because the transitions between the merged main-body cluster and the isolated headshot cluster trigger the `transition_rate`/`gap_rel` signals. The visible span-level splices reported in PR #64 are a separate span-level issue that the block-level detector cannot observe.

## Candidate formulas evaluated

All eight candidates operate only on the page-level geometry above. None uses document identity. Every one is a Boolean predicate.

| Id | Rule (when to keep the warning) |
|---|---|
| `baseline` | as shipped |
| `H1_cov60` | smaller-cluster y-coverage ≥ 0.60 required |
| `H2_share15` | smaller-cluster text share ≥ 0.15 required |
| `H3_alt3` | ≥ 3 substantial alternations required |
| `H4_runs2` | smaller cluster's disjoint runs ≥ 2 required |
| `H5_align100` | top and bottom alignment deltas each ≤ 100 pt |
| `H1+H2` | both H1 and H2 must hold |
| `H1+H3` | both H1 and H3 must hold |
| **`H6_thin_tall_marker`** | **silence when smaller cluster is a thin (share ≤ 0.020) tall (cov ≥ 0.40) marker (alt_substantial ≤ 0)** |
| `H7_thin_marker_no_cov` | silence when share ≤ 0.010 AND alt_substantial ≤ 0 |
| `H8_top_aligned_sidebar` | silence when share ≤ 0.020 AND top_delta ≤ 50 pt AND cov ≥ 0.40 |

## Offline confusion matrices (28 documents, 275 pages)

Baseline: TP = 2 (`3colpres`, `multicolumn.pdf`), FP = 3 (`strikeUnderline`, `GeoTopo`, `GeoTopo-komprimiert`), TN = 23, FN = 0. Recall 1.000, FPR 0.115, F1 0.571.

| Candidate | TP | FP | TN | FN | R | FPR | F1 | Ships? |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| baseline | 2 | 3 | 23 | 0 | 1.000 | 0.115 | 0.571 | reference |
| H1_cov60 | 0 | 2 | 24 | 2 | 0.000 | 0.077 | 0.000 | no — silences `3colpres` |
| H2_share15 | 0 | 2 | 24 | 2 | 0.000 | 0.077 | 0.000 | no — silences `3colpres` |
| H3_alt3 | 0 | 2 | 24 | 2 | 0.000 | 0.077 | 0.000 | no — silences `3colpres` |
| H4_runs2 | 0 | 2 | 24 | 2 | 0.000 | 0.077 | 0.000 | no — silences `3colpres` |
| H5_align100 | 1 | 3 | 23 | 1 | 0.500 | 0.115 | 0.500 | no — silences `3colpres`, leaves `strikeUnderline` |
| H1+H2 | 0 | 0 | 26 | 2 | 0.000 | 0.000 | 0.000 | no — silences `3colpres` |
| H1+H3 | 0 | 2 | 24 | 2 | 0.000 | 0.077 | 0.000 | no — silences `3colpres` |
| **H6_thin_tall_marker** | **2** | **2** | **24** | **0** | **1.000** | **0.077** | **0.667** | **passes** |
| H7_thin_marker_no_cov | 2 | 2 | 24 | 0 | 1.000 | 0.077 | 0.667 | passes |
| H8_top_aligned_sidebar | 2 | 2 | 24 | 0 | 1.000 | 0.077 | 0.667 | passes |

## Changed decisions (per candidate)

For every candidate that passes the shipping gate, the ONLY page-level decision that flips is `strikeUnderline` p1: `True → False`. Baseline recall is preserved on `3colpres` and `multicolumn.pdf`. The 15+ GeoTopo pages that fire (in a document that stays FP) also see no page-level flips.

For the six candidates that fail (H1–H5, H1+H2, H1+H3, H5): `3colpres` p1 is incorrectly silenced. H5 additionally leaves `strikeUnderline` warning; the others silence `strikeUnderline` correctly but at the cost of the true positive.

Per-page decision detail across every baseline-warning page (the shipping-gate discriminator):

```
asset               page  gap  share    cov   top  alt_s   H6   H7   H8
strikeUnderline       1   338  0.003  0.60     0     0   SIL  SIL  SIL
3colpres              1   177  0.010  0.09   655     1   keep keep keep
multicolumn.pdf       3   106  0.085  0.00    13     0   keep keep keep
GeoTopo p9            9    96  0.075  0.00   185     1   keep keep keep
GeoTopo p23          23   184  0.218  0.53   148     3   keep keep keep
GeoTopo p29          29   225  0.100  0.79    79     3   keep keep keep
GeoTopo p40          40    77  0.387  0.51   157     3   keep keep keep
GeoTopo p101        101    66  0.389  0.54   135     7   keep keep keep
... (15 GeoTopo + 15 GeoTopo-komprimiert pages total; all keep on all three)
```

Every FP-candidate page in the GeoTopo family has either `text_share > 0.02` (fails H6/H8's share gate) or `alt_substantial >= 1` (fails H7's alternations gate). Their FP class is different from `strikeUnderline`'s and cannot be silenced by the sidebar rule.

## Which of H6 / H7 / H8 to prefer

All three pass the shipping gate. Recall = 1.000, FPR = 0.077, F1 = 0.667. **The corpus has only one known sidebar false positive (`strikeUnderline`), so the three-way tie is a small-sample artefact — we cannot distinguish them on evidence alone.**

Physical motivation, in order of restrictiveness (least → most):

- **H7** — `share <= 0.010 AND alt_substantial <= 0`. Simplest. Two conditions.
- **H6** — `share <= 0.020 AND cov >= 0.40 AND alt_substantial <= 0`. Adds the vertical-coverage gate that separates a sidebar (tall, thin) from a small isolated inset like the `3colpres` headshot (short, thin).
- **H8** — `share <= 0.020 AND top_delta <= 50 AND cov >= 0.40`. Requires top-alignment. A sidebar can be bottom-aligned; H8 will not silence such a case.

**Recommendation** — should the next phase proceed to implementation, prefer **H6**. Rationale:

- H6's smaller-cluster share threshold (0.020) gives ~6× headroom above `strikeUnderline`'s measured 0.003, so a slightly wider marker still triggers the silence.
- H6's coverage gate (`cov >= 0.40`) is what protects `3colpres` (measured 0.09) — this is the geometry we need explicit protection on.
- The `alt_substantial <= 0` clause is the strongest signal that the smaller cluster does not participate in the reading order.
- H6 does NOT depend on top-alignment (unlike H8), so a bottom-aligned sidebar would still be caught.
- H6 is more restrictive than H7 (which relies only on share and alternations) — worth the extra condition since we have one datapoint.

## Confidence and limitations

- **One known sidebar FP.** Every candidate's shipping-gate pass is driven by the same one page (`strikeUnderline` p1). Any of H6/H7/H8 could over-generalise on a wider corpus. Before shipping any of them, at least one additional sidebar FP needs to be located and annotated.
- **Missing block extents.** `x1` / `y1` are not stored per block. Vertical-coverage numbers are computed from `y0` extents only and underestimate true coverage by roughly one block-height. In the `3colpres` vs `strikeUnderline` comparison the direction of the difference is 5–7×, so this caveat does not affect the outcome — but at threshold `cov >= 0.40`, a page with true coverage 0.42 could measure 0.36 and slip through.
- **Cluster-boundary sensitivity.** The largest-gap cluster boundary is defined by a single x0 pair. On a page with two comparable large gaps, small perturbations of x0's can shift the boundary. `strikeUnderline` and `3colpres` both have a single dominant gap (338 pt and 177 pt respectively), so this is not a concern here — but a candidate must not assume it.
- **GeoTopo FP class is not addressed.** The two GeoTopo documents remain document-level false positives under every candidate. Their FP class is different (text-share above 0.05, substantial alternations ≥ 1) and needs its own analysis. It should be tracked separately in future Issue #50 phases.
- **Span-level false negatives are out of scope.** `elpais` and `simple2` are span-level cases and are not touched by the block-level detector or by any of the candidates evaluated here.

## Review addendum — verification of the four questions

Following the interim finding, four verification questions were raised. Each is answered from the raw data captured in the JSON.

### 1. Metric semantics — what does the minority cluster actually represent?

Direct dump of every block in `3colpres` p1 (13 blocks) with cluster assignment relative to the validator's boundary at `x = 321.6`:

| block id | type | x0 | y0 | chars | words | cluster |
|---|---|---:|---:|---:|---:|:---:|
| `b339160b…` | paragraph | 56.7 | 32.5 | 81 | 14 | 0 |
| `63f31da3…` | heading | 67.2 | 100.9 | 28 | 5 | 0 |
| `1f1b0043…` | paragraph | 145.3 | 135.5 | 134 | 23 | 0 |
| `4d1c0e7a…` | paragraph | 56.7 | 255.5 | 338 | 58 | 0 |
| `c486cd23…` | paragraph | 56.7 | 399.5 | 517 | 81 | 0 |
| `cd365dd2…` | paragraph | 56.7 | 603.5 | 268 | 44 | 0 |
| `48b2ca62…` | paragraph | 56.7 | 760.2 | 2411 | 402 | 0 |
| `4a65a8d1…` | paragraph | 233.3 | 651.5 | 154 | 24 | 0 |
| **`e8dd1ccc…`** | **heading** | **409.9** | **687.5** | **20** | **4** | **1** |
| `e1c70511…` | paragraph | 233.3 | 699.5 | 93 | 11 | 0 |
| **`29d7c0e5…`** | **paragraph** | **459.5** | **760.2** | **22** | **10** | **1** |
| `b149a5ae…` | image | — | — | 40 | 4 | n/a |
| `634ec856…` | image | — | — | 40 | 4 | n/a |

**Answer to the question:** `3colpres`'s minority cluster (cluster 1) is not one of the three magazine columns. It is **two small bottom-right blocks** — a 4-word heading at `(409.9, 687.5)` and a 10-word caption at `(459.5, 760.2)`. Total: 42 characters, ~14 words. The middle-column blocks at `x = 233.3` are lumped into cluster 0 with the leftmost blocks because they sit below the validator's biggest-gap boundary of `x = 321.6`. **The block-level detector cannot see the three-column body of `3colpres`.**

That has a consequential implication for the interpretation of the baseline warning on `3colpres`: it fires because `transition_rate = 0.300` — the two isolated bottom-right blocks create transitions in the y-sorted block sequence. It does **not** fire because the detector "sees" real multicolumn corruption. This mirrors, structurally, exactly what happens on `strikeUnderline`.

Same dump for `strikeUnderline` p1 (9 blocks) with validator boundary at `x = 235.4`:

| block id | type | x0 | y0 | chars | words | cluster |
|---|---|---:|---:|---:|---:|:---:|
| `1c05abad…` | paragraph | 58.3 | 144.9 | 127 | 6 | 0 |
| **`7486c1dd…`** | **heading** | **404.6** | **144.9** | **6** | **1** | **1** |
| `6cab1eb3…` | paragraph | 66.3 | 156.5 | 1230 | 39 | 0 |
| **`79628677…`** | **heading** | **404.6** | **223.0** | **6** | **1** | **1** |
| `e5b49515…` | paragraph | 66.3 | 234.6 | 3116 | 129 | 0 |
| **`1cd116f9…`** | **heading** | **404.6** | **420.4** | **6** | **1** | **1** |
| `cf890620…` | paragraph | 66.3 | 432.0 | 3013 | 154 | 0 |
| **`d8cf12b3…`** | **heading** | **404.6** | **617.8** | **6** | **1** | **1** |
| `347f3588…` | paragraph | 66.3 | 629.3 | 476 | 13 | 0 |

`strikeUnderline`'s minority cluster (cluster 1) is **four 1-word heading blocks running down the right margin** from `y = 145` to `y = 618`, uniform x-position `x = 404.6`, all extremely short (6 chars each — revision-marker tags). This IS the sidebar the visual review identified.

**Structural comparison:**

| dimension | `strikeUnderline` cluster 1 | `3colpres` cluster 1 |
|---|---|---|
| Blocks | 4 (all headings) | 2 (heading + paragraph) |
| Total chars | 24 | 42 |
| Total words | 4 | 14 |
| x-position | uniform at 404.6 | 409.9 and 459.5 (heterogeneous) |
| y-range (y0) | 145 – 618 (473 pt, ~60% of page) | 687 – 760 (73 pt, ~9% of page) |
| Vertical position | top-to-bottom | bottom-of-page |
| Interpretation | right-margin revision-marker sidebar | small bottom-right callout / caption region |

Neither minority cluster corresponds to a real reading column. Both are incidental objects that create transitions in the y-sorted block sequence. What separates them geometrically is **vertical placement + coverage**: the sidebar spans the page height and is top-aligned; the callout is compact and sits at the bottom.

### 2. Threshold sensitivity — is H6 stable or brittle?

A 40-cell grid was run over `share_max ∈ {0.010, 0.015, 0.020, 0.025, 0.030}`, `cov_min ∈ {0.30, 0.40, 0.50, 0.60}`, `alt_max ∈ {0, 1}`.

**Result: 30 / 40 cells pass the shipping gate.** The 10 failing cells are all at `cov_min = 0.60`, exactly where `strikeUnderline`'s measured 0.597 fails the sidebar test.

The H6 default `(share ≤ 0.020, cov ≥ 0.40, alt ≤ 0)` sits inside a stable operating region:

| neighbour | share_max | cov_min | alt_max | passes | flipped_ids |
|---|---:|---:|---:|:---:|---|
| −1 step in share | 0.015 | 0.40 | 0 | yes | `strikeUnderline` |
| −1 step in cov | 0.020 | 0.30 | 0 | yes | `strikeUnderline` |
| H6 default | 0.020 | 0.40 | 0 | yes | `strikeUnderline` |
| +1 step in alt | 0.020 | 0.40 | 1 | yes | `strikeUnderline` |
| +1 step in cov | 0.020 | 0.50 | 0 | yes | `strikeUnderline` |
| +1 step in share | 0.025 | 0.40 | 0 | yes | `strikeUnderline` |

Every direct neighbour passes with the same outcome (only `strikeUnderline` flips). No collateral damage in any direction. The brittle edge is at `cov ≥ 0.60`, which is not reachable from H6 by a single step.

**Verdict:** H6 lies in a stable region. However, the entire region owes its stability to a single data point — see Section 3 for why that is a serious constraint.

Raw grid in `benchmarks/SIDEBAR_MULTICOLUMN_THRESHOLD_GRID_2026-07-19.json`.

### 3. Full changed-decision audit

The exhaustive changed-decision audit for H6, H7, and H8 is in `benchmarks/SIDEBAR_MULTICOLUMN_CHANGED_DECISIONS_2026-07-19.json`. Summary:

| Rule | Total page flips | On corpus positives silenced | On corpus negatives raised | On unexpected assets | Desirable? |
|---|---:|---:|---:|---:|:---:|
| H6 | 1 | 0 | 0 | 0 | yes |
| H7 | 1 | 0 | 0 | 0 | yes |
| H8 | 1 | 0 | 0 | 0 | yes |

The **only page-level flip on this corpus is `strikeUnderline` p1**. Every other page — including all 15 baseline-warning GeoTopo pages, the `3colpres` p1 baseline warning, and the `multicolumn.pdf` p3 baseline warning — retains its baseline decision under all three candidates.

Confirmed:
- No candidate suppresses a confirmed observable true positive.
- No candidate creates a new false positive.
- No public-corpus control's decision changes unexpectedly.
- Every candidate operates only on page-level geometry — no document identity, no filename lookup.

### 4. Causal placement — where does the rule apply?

**H6 as currently defined is a page-level suppression rule.** Given a page whose baseline detector warns, H6 asks "does the smaller cluster look like a sidebar?", and if so, it silences the warning for the whole page.

**This is not the correct implementation model for a future detector change.** The correct model is:

1. Detect that a minority cluster satisfies the sidebar signature.
2. **Exclude that cluster's blocks** from the analysis.
3. Recompute `gap_size`, `gap_rel`, `transition_rate`, `large_y_drops`, `short_frac` on the remaining blocks.
4. Warn only if the recomputed geometry still supports the multicolumn hypothesis.

The two approaches — page-level suppression vs. cluster exclusion + recomputation — happen to produce identical confusion matrices on this corpus, because no page carries a real multicolumn body **and** a sidebar simultaneously. Verification: for `strikeUnderline`, excluding the 4 sidebar headings leaves 5 body paragraphs all at `x ≈ 66`; the recomputed `transition_rate` collapses to 0, no HTR signal fires, warning is silenced — same result. For `3colpres`, the minority cluster fails the sidebar test (`cov = 0.09 < 0.40`), so no exclusion happens; baseline verdict stands — same result.

But the two approaches diverge on the mixed case:

- A page with genuine multicolumn body **plus** a right-margin sidebar: page-level suppression would incorrectly silence the entire warning. Cluster exclusion + recomputation would remove only the sidebar cluster and reveal the real column geometry.

**No such mixed-case page exists in the current corpus.** So the two implementations are experimentally indistinguishable here, but they are architecturally different, and only the latter is safe to ship.

## Revised recommendation

Given the four verification results together, the recommendation from the interim reading needs to be tightened:

1. **Metric semantics** — the H6 signal is physically meaningful, but the interpretation is that "the minority cluster is a thin tall marker not participating in reading order," not "the page is not multicolumn." A page can be multicolumn AND have a sidebar.
2. **Threshold sensitivity** — H6 is in a stable region, but the whole region owes its stability to `strikeUnderline` alone. Widening the corpus is a prerequisite, not a nice-to-have.
3. **Changed-decision audit** — clean on this corpus.
4. **Causal placement** — H6 as a page-level suppression rule would be unsafe to ship for the mixed case. The correct implementation is cluster exclusion + baseline recomputation.

**Ship-none is the honest conclusion for this PR.** The rule that would ship in a follow-up detector-change PR is:

```
if minority_cluster satisfies (share <= 0.020 AND cov >= 0.40 AND alt_substantial == 0):
    excluded_blocks = blocks in minority cluster
    remaining_blocks = blocks NOT in minority cluster
    recompute baseline signals on remaining_blocks
    warn if the recomputed signals fire
else:
    warn as baseline
```

Not:

```
if minority_cluster satisfies sidebar signature:
    do not warn
else:
    warn as baseline
```

Before that implementation PR can be opened, two prerequisites must be met:

1. **At least one additional annotated sidebar FP** must be located, added to the frozen corpus, and confirmed to exhibit the same signature. `strikeUnderline` alone is not enough evidence.
2. **A test page that combines a real multicolumn body with a sidebar** must be found or constructed, so that the cluster-exclusion-and-recompute path can be validated. If none exists in observable corpus, a controlled synthetic page (analogous to the ones in `tests/test_plugins/test_multicolumn_validator.py`) is acceptable.

## Ranked recommendations (revised)

1. **Ship none in this PR.** Preserve the analysis, threshold grid, and changed-decision audit as evidence.
2. Next Issue #50 phase: find a second sidebar FP + build a mixed-case test page.
3. Then: implementer proposes a detector-change PR using **cluster-exclusion + recomputation**, not page-level suppression, gated at H6 default thresholds (or their post-widening replacement).
4. If a widened corpus does not surface a second sidebar FP within one work cycle, close the block-level sidebar workstream and move to the span-level detector track.

## Appendix — span-only cases (excluded from primary metric)

For record only:

- `elpais` — span-level positive; baseline correctly stays silent at block level (fires no `W_MULTICOLUMN_ORDER`). Not touched by any candidate.
- `simple2` — reviewer-ambiguous positive; excluded from confirmed corpus per phase B5.

Neither is used to judge a block-level candidate.

## Reproducibility

```
# 1. Verify the ParseBench cache is present + matches promoted checksums.
python -m benchmarks.sidebar_multicolumn_signal_analysis --output <path> --workdir <dir>

# 2. Every eligible PDF is recompiled from source; nothing is cached
#    between runs. Public corpus lives under benchmarks/.public_corpus/pdf/.

# Exit codes:
#   30 — ParseBench cache missing or checksum mismatch
#   31 — a public-corpus PDF is missing from disk
#   33 — a compile step failed
```

The harness is offline; no network fetch. Public corpus is a git-tracked
subtree at `0847038`. ParseBench cache is external and verified at
runtime by sha256.

## Constraints observed

- No detector / parser / scoring / warning-penalty / SCORING_POLICY code
  changed.
- `SCORING_POLICY_VERSION` remains `"1.0"`.
- Lockfile, promoted checksums / sizes / mirror_url / dataset_revision
  unchanged.
- No PDF bytes added to git.
- Span-only cases confined to the appendix.
- Every candidate operates only on measured page geometry — no
  document identity, no filename heuristics.
