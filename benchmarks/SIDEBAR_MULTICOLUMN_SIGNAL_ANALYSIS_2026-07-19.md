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

## Ranked recommendations

1. **H6_thin_tall_marker** — passes the shipping gate; three physically-motivated conditions; most robust of the three passing candidates on prima facie grounds.
2. H7_thin_marker_no_cov — passes; simplest formulation; but no explicit protection against small isolated insets, only via `alt_substantial <= 0`.
3. H8_top_aligned_sidebar — passes; assumes sidebars are top-aligned. Would miss a bottom-aligned sidebar.
4. H1–H5 individually and the H1+H2 / H1+H3 combinations — reject; every one silences `3colpres` because the parser-detected column geometry there closely resembles a sidebar.

## Ship-or-stop decision

**A candidate that passes the shipping gate exists.** The next Issue #50 move is therefore option 1 in the phase spec: implement a detector-only sidebar correction based on H6 (or H7 / H8, at implementer's discretion) in a follow-up PR — **provided** the implementer secures at least one additional annotated sidebar FP to widen the shipping-gate corpus before merging.

If a widened corpus is not available and a single-datapoint pass is judged insufficient, option 2 stands: stop the block-level sidebar workstream, keep this analysis as evidence, and move to the separate span-level detector track.

**This PR ships neither. It is evidence only.**

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
