# Scientific-figure-complexity calibration corpus

This directory ships the **frozen candidate list** of public scientific
PDFs used to calibrate the layout-complexity detector v1
(`feat/ocr-layout-complexity-v1`).

At this phase, **no bytes are stored in the repository**. The lockfile
at `science_corpus.lock.json` pins the exact arXiv IDs and PDF URLs;
the actual bytes will be fetched in Commit 3 into a per-user cache
root outside the repo tree, following the same reference-fetch
posture as `parsebench_assets.lock.json`.

## Why this corpus exists

The OCR Auto Policy v1 calibration corpus (PR #101) covers scanned
synthetics, single-page ParseBench excerpts, and one local mixed
sample. It does not include multi-column arxiv-style preprints with
dense figure and table structure. The layout-complexity detector
must be calibrated against exactly that kind of content — otherwise
we cannot distinguish "simple digital PDF" from "structurally
complex" with any confidence.

## The five entries

| id | title | expected class | why included |
|---|---|---|---|
| `attention_1706_03762_v7` | Attention Is All You Need | multicolumn_moderate_figures | control for "moderate figure density in multi-column" |
| `resnet_1512_03385_v1` | Deep Residual Learning for Image Recognition | multicolumn_figure_heavy | tests separation between moderate and heavy figure density |
| `bert_1810_04805_v2` | BERT | multicolumn_table_heavy | tests table-density signal on a doc where tables dominate |
| `ddpm_2006_11239_v2` | Denoising Diffusion Probabilistic Models | multicolumn_math_heavy | tests math_bbox signal on a real doc |
| `clip_2103_00020_v1` | Learning Transferable Visual Models From Natural Language Supervision | multicolumn_figure_and_table_dense | tests stability on a 48-page paper with appendices |

## What Commit 1 (this commit) does

- Pins the arxiv IDs, versions, and PDF URLs.
- Records the `expected_complexity_class` label so Commit 3's
  calibration report can score predicted-vs-labelled accuracy.
- Does NOT fetch anything, does NOT store any bytes.

## What Commit 3 (later) does

- Fetches each asset via `pdf_url` into a per-user cache root
  outside the repo tree.
- Populates `expected_sha256` and `expected_size_bytes` in the
  lockfile.
- Adds each entry as a `CorpusEntry` with `source="science"` in the
  calibration harness's corpus enumeration.
- Runs the layout-complexity detector on each fetched PDF and
  correlates its per-doc score with the `expected_complexity_class`
  label and with the Tesseract-vs-UOC quality delta.

## What we will NOT do

- Redistribute the fetched PDFs. Reference-fetch-only.
- Fetch anything at import time or in CI.
- Add more entries in Commit 1 or Commit 2 — additions require a
  separate lockfile update reviewed under the same rights posture.
