# ParseBench Asset Provenance — 2026-07-18 (Issue #53, phase A)

**Commit under audit:** `2af22057d9e99fea0ff2dc262ce8cff41408ca54` (post-#58).

**Scope of this phase:** provenance, licence, availability, and rights
classification only. **No third-party PDF was downloaded, mirrored,
committed, or redistributed by this audit.** No fetch harness was built.
No production code was modified. This document is auditable evidence
that Phase B (mirror / fetch / replace / exclude) can be decided from.

Companion artefact: [`parsebench_assets.proposed.json`](parsebench_assets.proposed.json)

## Executive summary

- **Dataset provider identified:** LlamaIndex, ParseBench project. Code at
  https://github.com/run-llama/ParseBench; dataset at
  https://huggingface.co/datasets/llamaindex/ParseBench; paper
  arXiv:2604.08538.
- **Dataset licence:** Apache-2.0, applied by LlamaIndex to the *dataset
  compilation*. The dataset's own README declares: "All documents are
  sourced from public online channels. The dataset is released under the
  Apache 2.0 License. If there are any copyright concerns, please contact
  us via the GitHub repository."
- **Key caveat:** several of the underlying files are pages from
  identifiable third-party corporate or publisher material (IKEA
  marketing, El País newspaper, East Bay Times newspaper,
  Mercedes-Benz corporate). The dataset's downstream Apache-2.0
  declaration does not necessarily override the original owner's
  copyright. Because of that, this audit classifies **every asset as
  `reference-fetch-only`** — a defensible default that lets AksharaMD
  cite ParseBench and pull the file from HuggingFace at CI time without
  claiming direct redistribution rights we cannot independently verify.
- **All 11 named assets are identified locally on this workstation** at
  `C:/Users/kalya/parsebench/data/docs/text/`. The 12th (the "Japanese
  regression case") is **identity-unresolved** — the historical
  references do not specify which of six candidate PDFs is meant.
- **No new asset requires an immediate synthetic replacement** based on
  provenance alone. Synthetic replacement remains an option in Phase B
  for any asset that legal review classifies as restricted.

## Historical internal references

Every one of the 12 assets was already named somewhere in the repository
before this audit. Cross-references:

| Asset | Internal references |
|---|---|
| `3colpres` | `aksharamd/plugins/validators/multicolumn.py:133` (calibration threshold source), `:162` (maturity docstring); `benchmarks/ADVANCED_FIDELITY_2026-07-18.md:187` (fix rationale); `benchmarks/MULTICOLUMN_CANDIDATE_REPLAY_2026-07-18.md:28`; `benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.md`; `benchmarks/multicolumn_recalibration_labels.json` (unavailable list). |
| `ikea3` | `aksharamd/plugins/validators/multicolumn.py:163` (known FN class); `tests/test_plugins/test_warning_regression.py:22`; recalibration + replay reports. |
| `elpais` | Same as `ikea3` (known FN class); appears in `benchmarks/expectation_validation_run_v2.json:18091` and `_v3.json:18173` as `text/text_multicolumns__elpais`. |
| `simple2` | Same as `ikea3` (known FN class); appears in `benchmarks/ADVANCED_FIDELITY_2026-07-18.md:129` as a "named regression not reproduced". |
| `eastbaytimes` | `validators/multicolumn.py:133` (calibration hard-negative at TR≈0.25). |
| `battery` | `validators/multicolumn.py:162` (control that must stay silent). |
| `2colmercedes` | `validators/multicolumn.py:162`. |
| `text_dense__de` | `benchmarks/ADVANCED_FIDELITY_2026-07-18.md:12` (excluded ParseBench regression). |
| `letter3` | Same as `text_dense__de`. |
| `myctophidae` | Same as `text_dense__de`. |
| `strikeUnderline` | Same as `text_dense__de`. |
| Japanese case | `benchmarks/ADVANCED_FIDELITY_2026-07-18.md:12` says "a Japanese fixture" without a concrete filename. |

## Corpus discovery

The workstation running this audit has a local checkout of the
ParseBench repository at `C:/Users/kalya/parsebench`. That local checkout
resolves every historical name except the Japanese one:

| Historical name | Local ParseBench filename | Category |
|---|---|---|
| 3colpres | `text_multicolumns__3colpres.pdf` | multicolumns |
| ikea3 | `text_misc__ikea3.pdf` | misc |
| elpais | `text_multicolumns__elpais.pdf` | multicolumns |
| simple2 | `text_multicolumns__simple2.pdf` | multicolumns |
| eastbaytimes | `text_simple__eastbaytimes.pdf` | simple |
| battery | `text_multicolumns__battery.pdf` | multicolumns |
| 2colmercedes | `text_multicolumns__2colmercedes.pdf` | multicolumns |
| text_dense__de | `text_dense__de.pdf` | dense |
| letter3 | `text_simple__letter3.pdf` | simple |
| myctophidae | `text_simple__myctophidae.pdf` | simple |
| strikeUnderline | `text_simple__strikeUnderline.pdf` | simple |
| Japanese case | **candidates:** `text_dense__japanese.pdf`, `text_multilang__japanese.pdf`, `text_multilang__japanese2–5.pdf`, `text_multicolumns__japanesefrench.pdf` | multiple |

The local files were **not** copied into this repository. The audit
inspected the local install for filename existence and for the ParseBench
LICENSE / README bytes; nothing was staged or committed.

## Canonical source

For every asset the canonical source is:

- **Dataset home:** https://huggingface.co/datasets/llamaindex/ParseBench
- **Source page URL (text category):** https://huggingface.co/datasets/llamaindex/ParseBench/tree/main/docs/text
- **Per-file URL pattern:** `https://huggingface.co/datasets/llamaindex/ParseBench/resolve/main/docs/<category>/<basename>.pdf`
  - The category segment matches the local layout (e.g., `text` for
    every one of the 11 identified assets; a per-asset check may still
    be required in Phase B before wiring a fetch harness).
- **Paper for citation:** Zhang et al., 2026, "ParseBench: A Document
  Parsing Benchmark for AI Agents", arXiv:2604.08538.
- **BibTeX key:** `zhang2026parsebench`.

## Rights classification

Apache-2.0 governs the *dataset compilation*. The `Copyright Statement`
in ParseBench's own README explicitly notes that documents are "sourced
from public online channels" — i.e., LlamaIndex asserts they had licence
or fair-use grounds to include them, and offers a takedown pathway
("If there are any copyright concerns, please contact us via the GitHub
repository") rather than a per-file rights registry.

Consequence for AksharaMD:

- We can **cite the dataset and reference-fetch from HuggingFace** with
  attribution. This is `reference-fetch-only`.
- We can potentially **mirror the exact bytes with the ParseBench
  attribution + Apache-2.0 licence text** attached (i.e., a
  redistribution of Apache-2.0-declared content is Apache-2.0-permitted).
  BUT for any file whose underlying content is identifiably a
  third-party publisher / corporate document (IKEA, El País, East Bay
  Times, Mercedes-Benz), a takedown request could reach AksharaMD as
  the mirror operator regardless of ParseBench's downstream
  declaration.

**Phase A recommendation:** classify all 12 as
`reference-fetch-only`. If Phase B still wants a mirror, do that per-file
after independent legal review of the specific document.

### Per-asset classification

| Asset | Redistribution | Availability | Confidence | Underlying-rights concern |
|---|---|---|---|---|
| 3colpres | reference-fetch-only | available-stable | high | Underlying doc unidentified; assume standard ParseBench Apache-2.0 posture. |
| ikea3 | reference-fetch-only | available-stable | **medium** | IKEA branding — corporate copyright concern. |
| elpais | reference-fetch-only | available-stable | **medium** | El País newspaper — publisher copyright. |
| simple2 | reference-fetch-only | available-stable | medium | Underlying doc unidentified; two sibling filenames (`simple2col`, `simple2col2`) make disambiguation worth double-checking. |
| eastbaytimes | reference-fetch-only | available-stable | **medium** | East Bay Times newspaper — publisher copyright. |
| battery | reference-fetch-only | available-stable | medium | Filename suggests technical / product content. |
| 2colmercedes | reference-fetch-only | available-stable | **medium** | Mercedes-Benz corporate material — corporate copyright. |
| text_dense__de | reference-fetch-only | available-stable | medium | Underlying doc unidentified. |
| letter3 | reference-fetch-only | available-stable | medium | Sibling filenames (`text_ocr__letter*`) make disambiguation worth double-checking. |
| myctophidae | reference-fetch-only | available-stable | medium | Encyclopedic content — reuse posture likely permissive but not verified. |
| strikeUnderline | reference-fetch-only | available-stable | medium | Underlying doc unidentified. |
| Japanese case | reference-fetch-only | **identity-unresolved** | **low** | Cannot classify rights before picking a concrete filename. |

## Availability classification

- **11 of 12** map to concrete filenames in the local ParseBench install
  and to identifiable HuggingFace paths → `available-stable`.
- **1 of 12** (Japanese case) → `identity-unresolved`. Phase B must pick a
  concrete file before advancing.

No asset is currently classified as `missing`, `authentication-required`,
or `generated-asset`. The HuggingFace dataset is publicly accessible
without authentication for the file categories AksharaMD needs (docs/text).

## Synthetic-replacement analysis

Because every named asset resolves to an identifiable ParseBench file
that is available under a permissive-declared licence, **no asset in
this priority list currently requires a synthetic replacement based on
provenance alone.** Synthetic replacements would only be needed if:

- Phase B legal review reclassifies an asset from `reference-fetch-only`
  to `restricted` (e.g., a specific publisher's takedown request).
- Phase B decides the underlying-document copyright of the corporate /
  newspaper cluster (`ikea3`, `elpais`, `eastbaytimes`, `2colmercedes`)
  is too risky to depend on for CI.

For that eventuality, the properties each asset must reproduce
synthetically are:

| Asset | Property to reproduce |
|---|---|
| 3colpres | Three-column body with dense per-column text and section headings; block-level column-interleaving detectable by transition_rate ≈ 0.30. |
| ikea3 | Two- or three-column body where the column boundary sits between visually adjacent glyphs — span-level splice hidden inside blocks. |
| elpais | Newspaper-style two-column body with mid-word column joins; same span-level splice class. |
| simple2 | Two-column body where the block-level output is column-sorted but individual paragraphs cross the boundary. |
| eastbaytimes | Two-column body where extraction produces correctly ordered blocks (transition_rate ≈ 0.25) — hard negative just below threshold. |
| battery | Bimodal x-gap page whose block sequence is correctly column-first-sorted — should not fire. |
| 2colmercedes | Bimodal x-gap page whose block sequence is correctly column-first-sorted — should not fire. |
| text_dense__de | German dense-text page with tables and no column split. |
| letter3 | Single-column short letter body. |
| myctophidae | Single-column encyclopedic entry with a taxobox side element. |
| strikeUnderline | Single-column body with visible strikethrough + underline formatting — for formatting-preservation checks. |
| Japanese case | Non-latin script single- or multi-column body — first disambiguate which one. |

No synthetic asset is generated in this phase.

## Proposed lockfile schema

The proposed schema is in `benchmarks/parsebench_assets.proposed.json`.
Every asset entry carries: `id`, `aliases`, `tier`, `source_project`,
`source_page_url`, `binary_url`, `mirror_url`, `sha256`, `size_bytes`,
`license`, `license_url`, `copyright_owner`, `redistribution`,
`availability`, `attribution`, `expected_label`,
`page_level_ground_truth`, `defect_kind`, `ci_retrieval`, `checked_at`,
`notes`. Unresolved fields are explicit `null` values — nothing is
invented.

Once Phase B decides which files to mirror (if any), the lockfile can be
renamed to `benchmarks/parsebench_assets.lock.json`, populated with
`mirror_url` and `sha256` values captured under authorised download, and
frozen. That step is out of scope for Phase A.

## Phase B recommendations (per asset)

| Asset | Recommended next action | Rationale |
|---|---|---|
| 3colpres | **mirror exact bytes** (after standard Apache-2.0 attribution setup) OR **reference-fetch from canonical source** | Historical primary TP; needed on the calibration corpus. Underlying document unidentified — mirroring is defensible if attribution + licence bytes go with it. |
| ikea3 | **reference-fetch from canonical source** (do NOT mirror) | Corporate branding — decouple AksharaMD from potential publisher takedown. |
| elpais | **reference-fetch from canonical source** (do NOT mirror) | Newspaper publisher copyright. |
| simple2 | **mirror exact bytes** (after standard attribution) | Underlying unidentified; treat like `3colpres`. |
| eastbaytimes | **reference-fetch from canonical source** (do NOT mirror) | Newspaper publisher copyright. |
| battery | **mirror exact bytes** (after standard attribution) | Underlying unidentified; treat like `3colpres`. |
| 2colmercedes | **reference-fetch from canonical source** (do NOT mirror) | Mercedes-Benz corporate branding. |
| text_dense__de | **mirror exact bytes** (after standard attribution) | Underlying unidentified; treat like `3colpres`. |
| letter3 | **mirror exact bytes** (after standard attribution) + verify filename against sibling `text_ocr__letter*` set. | Same. |
| myctophidae | **mirror exact bytes** (after standard attribution) | Encyclopedic content. |
| strikeUnderline | **mirror exact bytes** (after standard attribution) | Underlying unidentified; formatting-only asset. |
| Japanese case | **continue identity research** — pick a concrete file from the six candidates before any mirror or fetch decision. | Cannot classify rights before naming a specific file. |

### Non-recommendations

- No asset was assigned `request permission` at this phase — that
  becomes relevant only if a specific publisher issues a takedown or
  refuses to be a `reference-fetch-only` upstream.
- No asset was assigned `replace synthetically` at this phase —
  synthetic replacements would erase the calibration provenance that
  the historical thresholds were tuned against; they are a fallback,
  not a first choice.
- No asset was assigned `exclude from automated evaluation` — every
  identified asset carries clear multicolumn / regression value.

## Unresolved questions for the reviewer

1. **Japanese fixture identity.** Six candidate PDFs on the local
   install match `/japanese/i`. The historical validation reports do not
   pin one. Reviewer to decide the canonical file (or split into
   multiple assets).
2. **Mirror vs. reference-fetch policy.** Recommendation table above
   proposes per-asset defaults. Reviewer to confirm the split.
3. **Legal review of the four corporate/newspaper items** (`ikea3`,
   `elpais`, `eastbaytimes`, `2colmercedes`). Recommendation is
   `reference-fetch-only`; reviewer may prefer stricter (`request
   permission` / `exclude`) or looser (`mirror` under blanket ParseBench
   Apache-2.0 assertion).
4. **CI retrieval tiering.** Issue #53 non-goals include "not gating PR
   merges" today. Recommendation is `nightly` for every asset. Reviewer
   to confirm.

## Do-not-yet-do list (locked)

- Do not download any of the 12 assets in this branch.
- Do not populate `sha256` or `size_bytes` values in the lockfile at
  Phase A — those require an authorised download.
- Do not add live `mirror_url` values.
- Do not modify `aksharamd/plugins/validators/multicolumn.py` or any
  scoring surface.
- Do not begin the fetch harness.
- Do not implement C3, C4, or C3+C4 in production logic.

## Reproducibility

The internal references were located with straightforward `grep`
searches against the repository at commit `2af2205`. The local
ParseBench install was inspected under
`C:/Users/kalya/parsebench` — the license (Apache-2.0) and README
(dataset provenance + citation) were read but no PDF was opened or
copied. No external network fetch of any asset was performed.
