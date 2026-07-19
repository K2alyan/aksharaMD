# Multicolumn sidebar corpus + mixed-case fixtures — 2026-07-19 (Issue #50)

**Commit under evaluation:** `71c4916` (main, post-#66).

**Purpose.** Deliver the two prerequisites identified by PR #66 — a
second annotated sidebar false positive and a mixed multicolumn-plus-sidebar
test fixture — and prototype the cluster-exclusion + baseline-recomputation
approach that a future detector implementation would use.

**No production code changes.** No parser, validator, scoring,
warning-penalty, or `SCORING_POLICY` modifications. `SCORING_POLICY_VERSION`
remains `"1.0"`. Everything ships under `benchmarks/` and `tests/`.

**Machine-readable output:** `benchmarks/SIDEBAR_FIXTURES_REPORT_2026-07-19.json`.

## Requirement 1 — search for a second sidebar false positive

**Result: no second real sidebar FP was found. `strikeUnderline` remains the sole datapoint.**

### Search protocol

Every asset in the frozen sidebar-analysis corpus (28 assets · 275 pages ·
merged at `d479b90`) was re-inspected. For each expected-negative asset,
every baseline-warning page was scored against a **wide** sidebar
signature (`share ≤ 0.05` AND `cov ≥ 0.30` AND `alt_substantial ≤ 1`),
which is deliberately more permissive than H6 (`share ≤ 0.020` AND
`cov ≥ 0.40` AND `alt_substantial ≤ 0`).

### Pages examined

| Asset | Warning pages | Any page satisfying WIDE sidebar signature? |
|---|---:|:---|
| `strikeUnderline` | 1 | **yes** (share=0.003, cov=0.60, alt_sub=0) — the known FP |
| `GeoTopo.pdf` | 15 | no — every warning page has either `share > 0.05` (typically 0.07–0.40) or `alt_substantial ≥ 1` |
| `GeoTopo-komprimiert.pdf` | 15 | no — identical geometry to GeoTopo.pdf, same failure to satisfy signature |
| every other attested negative | 0 | (baseline did not fire) |

### Why the GeoTopo family is not a second sidebar FP class

The GeoTopo documents fire the multicolumn detector because their
compile output leaves substantial text content in a right-side
region (a bibliography sidebar, figure captions, or theorem environments
that span multiple pages). These regions carry meaningful text — the
minority-cluster text share is 7% – 40%, not the sub-1% seen for
`strikeUnderline`'s marker sidebar. Substantial-block alternations also
run 1 – 7, incompatible with a marker sidebar's 0. Every GeoTopo
warning page falls outside the H6 signature.

Reclassifying any GeoTopo page as a sidebar-shaped FP would require
either widening the H6 signature (which would then also silence
genuine content) or relabelling — neither is acceptable under the
Phase-B5 protocol. Per the PR spec ("do not relabel a case merely
to create the desired corpus"), we leave the GeoTopo FPs classified
as they are: a **distinct FP class**, not addressable by sidebar rules.

### Conclusion on Requirement 1

**Prerequisite 1 is not satisfied by the currently reviewed corpus.**
The block-level sidebar workstream remains supported by a single real
false positive.

## Requirement 2 — synthetic mixed-case fixture

**Delivered as an in-memory block generator, no PDF bytes committed.**
The fixture is `mixed_multicolumn_and_sidebar_page()` in
`benchmarks/sidebar_multicolumn_fixtures.py`.

### Fixture design

- **Page:** US-letter, 612 × 792 pt.
- **Body:** two columns of 8 paragraphs each. Left column at `x = 60`,
  right column at `x = 180`. Right-column paragraphs sit 4 pt lower
  than their left-column peers so a stable y-sort interleaves them
  block-by-block.
- **Sidebar:** four short marker blocks at `x = 500`, distributed
  vertically at `y ∈ {120, 260, 420, 620}`.
- **Ordering:** blocks are y-sorted before being handed to the
  detector (mirrors how real compiled blocks arrive).

The largest `x0`-gap is between the right body column (`x = 180`) and
the sidebar (`x = 500`) — 320 pt. This forces the validator's
2-cluster split to isolate the sidebar in its own cluster:
`cluster 0 = 16 body blocks`, `cluster 1 = 4 sidebar blocks`.
Consequently the smaller cluster IS the sidebar.

### Measured cross-cluster geometry (from the prototype)

| Metric | Value |
|---|---:|
| `text_share_smaller` | 0.004 |
| `smaller_y_coverage_frac` | 0.63 |
| `alternations_substantial` | 0 |
| H6 match | **True** |

### Baseline verdict

| Signal | Value |
|---|---:|
| `gap_size` | 320.0 pt |
| `gap_rel` | 0.73 |
| `transition_rate` | 0.42 |
| `large_y_drops` | 0 |
| `short_frac` | 0.20 |
| `warn` | **True** |

The baseline correctly warns: `transition_rate 0.42 > 0.28` fires the
`high_transition_rate` signal, plus `y_monotonic_with_transitions`
confirms.

### Companion fixtures

Three companion pages exercise the neighbouring cases:

- `sidebar_only_page()` — mirrors `strikeUnderline`. Baseline warns,
  H6 matches, blanket suppression silences, cluster exclusion silences
  (via recomputed baseline).
- `true_three_column_page()` — mirrors `3colpres`. Baseline warns,
  H6 does NOT match (smaller cluster is compact — `cov ≈ 0.09`),
  cluster exclusion is not applied, verdict preserved.
- `single_column_control()` — clean page. Baseline stays silent, no
  action taken.

## Prototype — cluster exclusion + baseline recomputation

**Location:** `benchmarks/sidebar_cluster_exclusion_prototype.py`.

The prototype reimplements the shipped
`aksharamd/plugins/validators/multicolumn.py :: _analyse_page` in
benchmark code so signals can be recomputed offline after modifying
the block set. The reimplementation is validated against real
`strikeUnderline` p1 block positions (see
`test_reimplementation_matches_strikeunderline_reference`):

| Signal | Reimplementation on captured blocks | Shipped detector reference |
|---|---:|---:|
| `gap_size` | 338 pt | 338.3 pt |
| `gap_rel` | 0.98 | 0.98 |
| `transition_rate` | 1.00 | 1.00 |
| `large_y_drops` | 0 | 0 |
| `short_frac` | 0.56 | 0.56 |
| `warn` | True | True |

### The three modes

Given a page dict `{blocks, page_width, page_height}`, `evaluate_page(page)`
returns:

- `baseline` — signals produced by `_compute_baseline_signals(blocks)` on the
  unmodified block list. This is the shipped detector's verdict.
- `blanket_suppression_warn` — if the page's smaller cluster satisfies the
  H6 sidebar signature AND baseline warned, force `warn=False` for the
  whole page. This is the **unsafe** rule the PR #66 addendum rejected.
- `cluster_exclusion_warn` — if the H6 signature matches AND baseline warned,
  drop the smaller cluster's blocks from the analysis, recompute
  `_compute_baseline_signals(remaining_blocks)`, and use the recomputed
  verdict. This is the **safe** implementation model.

Modes only apply when the baseline warned AND the H6 signature matched.
Otherwise both modes fall through to the baseline verdict.

## Blanket-suppression vs. cluster-exclusion — the decisive contrast

| Fixture | Baseline warn | H6 matches | Blanket → warn | Cluster excl. → warn | Notes |
|---|:---:|:---:|:---:|:---:|---|
| `sidebar_only_page` | True | Yes | **False** | **False** | Both approaches silence the strikeUnderline surrogate |
| `true_three_column_page` | True | No | True | True | Both approaches preserve the 3colpres surrogate |
| **`mixed_multicolumn_and_sidebar_page`** | **True** | **Yes** | **False** ⚠ | **True** ✔ | **Discriminative: blanket incorrectly silences; cluster exclusion correctly preserves** |
| `single_column_control` | False | No | False | False | Both approaches leave the control silent |

### Recomputed baseline signals on the mixed case after sidebar exclusion

After removing the 4 sidebar blocks, the remaining 16 body blocks have:

| Signal | Value |
|---|---:|
| `gap_size` | 120.0 pt (between the two body columns) |
| `gap_rel` | 1.00 |
| `transition_rate` | 1.00 (body columns fully interleaved in y-sort) |
| `large_y_drops` | 0 |
| `short_frac` | 0.19 |
| `warn` | **True** — `high_transition_rate` + `y_monotonic_with_transitions` |

The two body columns' 60 → 180 gap becomes the new largest gap; the
recomputed baseline fires exactly like a clean 2-column page would.
**Cluster exclusion preserves the genuine warning.** Blanket suppression
under the same H6 match silences the whole page — the failure mode the
PR #66 addendum warned about.

## Acceptance-gate outcome

Per the phase spec, a future production implementation may proceed only
if cluster exclusion:

1. Silences `strikeUnderline` — **yes** (sidebar_only_page silences).
2. Silences a second independent sidebar FP — **N/A, prerequisite unmet**.
3. Preserves `3colpres` — **yes** (true_three_column_page preserves, via
   the cov gate rather than the exclusion path).
4. Preserves the mixed multicolumn-plus-sidebar fixture — **yes**
   (mixed_multicolumn_and_sidebar_page cluster-exclusion warn = True).
5. Preserves every other confirmed observable positive — **yes** (only
   one confirmed observable positive exists in-corpus: `3colpres`).
6. Creates no new false positives — **yes** (single_column_control
   stays silent; no other fixture changes verdict).
7. Has no document-specific exceptions — **yes** (the rule uses only
   measured page-level geometry).

**Prerequisite 1 (a second independent sidebar FP) is not satisfied,
so the acceptance gate is not fully met.**

## Recommendation

Per the phase spec:

> If a second sidebar FP cannot be found after a thorough corpus search,
> document the search, do not fabricate one, conclude that block-level
> sidebar correction remains under-supported, and recommend pausing this
> workstream and moving to span-level detection.

That is the honest conclusion here.

### Concrete recommendation

- **Do not open a detector-implementation PR for the sidebar signature
  based on the current evidence.** With a single real sidebar FP,
  H6's operating region cannot be validated on independent examples.
  A heuristic trained on one document is a heuristic overfitted to
  that document.
- **The prototype and fixtures are useful evidence** of what a future
  detector-implementation PR would look like, if a second real sidebar
  FP surfaces later. Preserve them as historical evidence.
- **Pause the block-level sidebar workstream.** The `strikeUnderline`
  FP remains unresolved by any production change, but the cost of
  shipping a fragile rule based on one datapoint is higher than the
  cost of leaving the FP in place.
- **Move Issue #50 focus to the span-level detector workstream.**
  `elpais` and `simple2` are span-only positives; they are undetectable
  by the current block-level detector but are the class of damage that
  span-level detection is designed to catch. The `strikeUnderline`
  FP can be revisited if a second annotated sidebar FP surfaces during
  future corpus expansion.

### If the recommendation is followed

The next Issue #50 phase should be a **span-level detector design and
evaluation** PR, opened against the same reviewed corpus:

1. Design a span-level signal (perhaps `docs/MULTICOLUMN_SPAN_DETECTION_DESIGN.md`,
   which was written earlier).
2. Evaluate on `elpais` and `simple2` (the two span-only positives).
3. Report on precision / recall against the same corpus with the
   block-level detector unchanged.

## Deliverables (this PR)

- `benchmarks/sidebar_multicolumn_fixtures.py` — deterministic fixture
  generator, no PDF bytes.
- `benchmarks/sidebar_cluster_exclusion_prototype.py` — offline
  `_analyse_page` reimplementation + three-mode evaluation harness.
- `benchmarks/SIDEBAR_FIXTURES_REPORT_2026-07-19.md` — this report.
- `benchmarks/SIDEBAR_FIXTURES_REPORT_2026-07-19.json` — machine output.
- `tests/test_sidebar_cluster_exclusion_prototype.py` — 22 invariants,
  including a reference-check that binds the reimplementation to the
  shipped detector on real `strikeUnderline` block positions.

## Constraints observed

- No parser / validator / scoring / warning-penalty / packaging / model
  code changed. `SCORING_POLICY_VERSION` remains `"1.0"`.
- No lockfile, checksum, page annotation, or dataset-revision mutation.
- No PDF bytes added to git.
- Prototype `_compute_baseline_signals` reimplements the shipped
  `_analyse_page`; a test binds it to the shipped detector's output on
  real block data at `71c4916`.
- No document-specific exceptions in any candidate rule.
- Span-only cases confined to the recommendation section — not used to
  judge any block-level candidate.
