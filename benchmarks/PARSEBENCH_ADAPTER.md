# ParseBench Adapter Design Note

> **Status: Design note only — not yet implemented.**
>
> This document describes how AksharaMD could be wired into
> [run-llama/ParseBench][parsebench] for a fair third-party comparison.
> No ParseBench results are claimed in this PR.

## Two Different Benchmarks

This repository includes two benchmark dimensions that serve different purposes:

| Benchmark | Corpus | What It Measures |
| --- | --- | --- |
| **Public corpus** (`PUBLIC_BENCHMARK.md`) | 34 py-pdf/sample-files PDFs + 100 synthetic | **PDF robustness** — does the parser succeed, how many blocks, how fast |
| **ParseBench** (`PARSEBENCH_ADAPTER.md`) | 1,211 HuggingFace documents | **Semantic parsing quality** — tables, charts, content faithfulness, formatting, visual grounding |

These are complementary. The public corpus tells you whether AksharaMD **can parse** a
document. ParseBench tells you whether the extracted content is **semantically accurate**.
Neither alone is sufficient for a complete parser evaluation.

### Why py-pdf/sample-files ≠ ParseBench

`py-pdf/sample-files` is a **PDF robustness corpus**: 34 edge-case PDFs chosen to stress-test
different generators, encodings, and structural features (RTL text, encrypted PDFs,
multi-column layouts, 117-page academic documents). It is ideal for detecting parser
regressions and format support gaps.

`ParseBench` is a **semantic quality corpus**: 1,211 documents (almost all PDFs) annotated
with 169,011 ground-truth rules. It measures whether extracted content preserves tables
accurately, whether charts are reproduced, whether reading order is correct, and whether
extracted elements can be traced back to their source location (visual grounding).

Do not conflate the two. A 100% parser success rate on py-pdf/sample-files says nothing
about ParseBench content faithfulness scores, and vice versa.

## What ParseBench Is

ParseBench is an open evaluation framework (Apache 2.0) from LlamaIndex that
benchmarks document parsers across five dimensions using 1,211 documents and
169,011 rules:

| Dimension | What It Tests |
| --- | --- |
| Tables | GriTS + TableRecordMatch — merged cells, hierarchical headers |
| Charts | Exact data-point extraction with series / axis labels |
| Content faithfulness | Omissions, hallucinations, reading-order violations |
| Semantic formatting | Strikethrough, superscript, bold, title hierarchy |
| Visual grounding | Element traceability to source page location |

The benchmark corpus is hosted on HuggingFace (`llamaindex/ParseBench`) and is
not bundled with the repository. It focuses almost entirely on PDFs.

## How Parsers Are Plugged In

ParseBench uses the concept of **pipelines**: named, configured parser/extractor
combinations. As of mid-2025:

- 90+ preconfigured pipelines exist (LlamaParse, OpenAI, Anthropic, Google, AWS, Azure, …)
- Custom pipelines are registered via `/integrate-pipeline <name> <API_docs>` — this
  generates a runner script in `pipelines/<name>/run.py`
- Every pipeline is driven by: `uv run parse-bench run <pipeline_name>`
- Results land in `output/<pipeline_name>/<dimension>/*.result.json`

**TL;DR:** Adding AksharaMD requires writing one `pipelines/aksharamd/run.py` that
reads input PDFs, runs the AksharaMD compiler, and writes output in the format
ParseBench expects.

## What Output ParseBench Expects

Each pipeline's runner must produce a `result.json` for every test document.
The exact schema is dimension-dependent, but for content/table dimensions the
common shape is:

```json
{
  "file_id": "doc-0042",
  "extracted_text": "...",
  "tables": [
    {
      "rows": [["Header A", "Header B"], ["val1", "val2"]],
      "bbox": [x0, y0, x1, y1]
    }
  ],
  "page_count": 4
}
```

For visual grounding, bounding boxes per extracted element are required. AksharaMD
does not currently produce bounding boxes, so it cannot compete on this dimension.

## Which AksharaMD Output to Use

| ParseBench Dimension | AksharaMD Output to Use | Feasibility |
| --- | --- | --- |
| Content faithfulness | `\n\n`.join of all `PARAGRAPH` and `HEADING` block contents | Feasible |
| Tables | `TABLE` blocks — content is Markdown pipe tables | Feasible (needs Markdown → row parser) |
| Charts | Not applicable — AksharaMD doesn't extract chart data | Not feasible |
| Semantic formatting | `HEADING` levels + inline formatting from blocks | Partial |
| Visual grounding | Bounding boxes required — not produced | Not feasible |

The most defensible adapter would compete on **content faithfulness** and
**tables** only, and explicitly opt out of charts and visual grounding.

## What Would Count as a Fair Comparison

1. **Same document set** — use ParseBench's standard HuggingFace corpus, not a
   self-selected subset.
2. **No cherry-picking dimensions** — report all dimensions AksharaMD enters, and
   clearly note which it opts out of and why.
3. **Same pre-processing** — if other tools receive the raw PDF bytes, AksharaMD
   should too; no pre-conversion to text allowed.
4. **Published runner code** — the `pipelines/aksharamd/run.py` script must be
   committed and reproducible.
5. **Result files committed** — commit the `output/aksharamd/` directory so
   anyone can verify the scores.

## What Not To Claim Before Running the Official Evaluator

- Do not quote ParseBench scores from this repository until
  `uv run parse-bench run aksharamd` has been executed against the official corpus
  and the result files are committed.
- Do not compare AksharaMD scores against other pipelines' scores obtained under
  different corpus versions; ParseBench corpus updates can shift baselines significantly.
- "Content faithfulness" in ParseBench is not the same as "RAG answer accuracy";
  do not conflate the two in any public-facing claim.
- The public corpus (py-pdf/sample-files) success rate is a parser robustness metric,
  not a ParseBench score. These numbers are not comparable.

## Implementation Checklist (Phase 2)

- [ ] Install ParseBench locally: `git clone https://github.com/run-llama/ParseBench && cd ParseBench && uv sync`
- [ ] Download corpus: HuggingFace dataset `llamaindex/ParseBench`
- [ ] Write `pipelines/aksharamd/run.py`:
  - reads `input_path` from CLI arg
  - calls `Compiler().compile(input_path)`
  - extracts text from `PARAGRAPH` + `HEADING` blocks
  - extracts tables from `TABLE` blocks (Markdown → list-of-rows)
  - writes `result.json` in the required schema
- [ ] Start with a subset of 100–200 documents before committing to the full 1,211
- [ ] Run: `uv run parse-bench run aksharamd`
- [ ] Commit `output/aksharamd/` and open a PR with the full results table
- [ ] Add a note in `PUBLIC_BENCHMARK.md` pointing to the ParseBench results

[parsebench]: https://github.com/run-llama/ParseBench
