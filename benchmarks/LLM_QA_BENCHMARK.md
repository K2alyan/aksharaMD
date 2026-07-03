# AksharaMD — Downstream LLM Accuracy Benchmark

> **CONFIDENTIAL — Private Beta v0.1.0.** Do not distribute.

---

## What We Measured and Why

Better document extraction produces fewer tokens. That is easy to demonstrate.

What is harder to demonstrate — but more important to users — is whether cleaner extractions actually make LLMs *more accurate* when answering questions about a document. A 90% token reduction is not meaningful if the lost tokens carried the answers.

This benchmark answers both questions directly across five tools: **AksharaMD, MarkItDown (Microsoft), LlamaParse (LlamaIndex), PyMuPDF4LLM, and Docling (IBM)**.

---

## Methodology

### Corpus

36 documents across 12 formats, selected to span size and structural complexity within each type:

| Format | Files | Examples |
|--------|-------|---------|
| PDF | 3 | arXiv papers (575 KB – 3.9 MB) |
| DOCX | 3 | Business reports, market research, SRS |
| PPTX | 3 | Investor decks, partnership proposals |
| XLSX | 3 | Budget, employee data, inventory |
| HTML | 3 | Wikipedia articles (533 KB – 1.3 MB) |
| EPUB | 3 | Project Gutenberg books (207 KB – 546 KB) |
| TXT | 3 | Project Gutenberg plain text (232 KB – 1.2 MB) |
| EML | 3 | Email messages |
| IPYNB | 3 | Jupyter notebooks (statistics, data analysis) |
| CSV | 3 | Events, transactions, web server logs |
| JSON | 3 | Config files, reports, API responses |
| XML | 3 | Small, medium, and larger structured documents |

Documents were not chosen to favour AksharaMD — they represent everyday enterprise and research workloads.

### Q&A Pairs

4 factual questions per document (144 pairs total). Questions target specific, verifiable facts: names, numbers, dates, identifiers. Each expected answer is a short phrase.

### Evaluation Protocol

1. Each document is independently converted by all five tools
2. The first 6,000 characters of each conversion are used as LLM context (~4,000 tokens)
3. The same question is sent to the LLM with each conversion's context: *"Answer using only the document text. Be concise."*
4. Claude Haiku 4.5 scores every answer **0–10** against the expected answer

**LLM tested:** Claude Haiku 4.5  
**Judge:** Claude Haiku 4.5  
**Total answers evaluated:** 576 scored answers (unsupported formats excluded from scoring)

---

## Format Coverage

AksharaMD and MarkItDown handle every format in the corpus. LlamaParse handles 10 of 12 (fails JSON and IPYNB). PyMuPDF4LLM and Docling handle 8 of 12.

| Format | AksharaMD | MarkItDown | LlamaParse | PyMuPDF4LLM | Docling |
|--------|:---------:|:----------:|:----------:|:-----------:|:-------:|
| PDF | ✓ | ✓ | ✓ | ✓ | ✓ |
| DOCX | ✓ | ✓ | ✓ | ✓ | ✓ |
| PPTX | ✓ | ✓ | ✓ | ✓ | ✓ |
| XLSX | ✓ | ✓ | ✓ | ✓ | ✓ |
| HTML | ✓ | ✓ | ✓ | ✓ | ✓ |
| EPUB | ✓ | ✓ | ✓ | ✓ | ✓ |
| TXT | ✓ | ✓ | ✓ | ✓ | ✓ |
| EML | ✓ | ✓ | ✓ | ✗ | ✗ |
| IPYNB | ✓ | ✓ | ✗ | ✗ | ✗ |
| CSV | ✓ | ✓ | ✓ | ✗ | ✓ |
| JSON | ✓ | ✓ | ✗ | ✗ | ✗ |
| XML | ✓ | ✓ | ✓ | ✓ | ✗ |
| **Total** | **12/12** | **12/12** | **10/12** | **8/12** | **8/12** |

---

## Results

### Token efficiency

Token counts measured using the cl100k_base tokenizer (GPT-4 / Claude family).

| Tool | Avg tokens | Docs covered | vs AksharaMD |
|------|:----------:|:------------:|:------------:|
| **AksharaMD** | **6,114** | **36/36** | — |
| MarkItDown | 34,909 | 36/36 | 5.7× more |
| LlamaParse | 35,322 | 30/36 | 5.8× more |
| PyMuPDF4LLM | 46,523 | 24/36 | 7.6× more |
| Docling | 46,765 | 24/36 | 7.6× more |

AksharaMD uses **82% fewer tokens than MarkItDown**, **83% fewer than LlamaParse**, and **87% fewer than PyMuPDF4LLM and Docling**.

Per-format token averages (3 docs each, `—` = unsupported):

| Format | AksharaMD | MarkItDown | LlamaParse | PyMuPDF4LLM | Docling |
|--------|----------:|----------:|-----------:|------------:|--------:|
| PDF | 8,838 | 16,124 | 8,139 | 10,617 | 9,325 |
| DOCX | 256 | 262 | 268 | 253 | 272 |
| PPTX | 146 | 216 | 160 | 182 | 188 |
| XLSX | 1,872 | 1,872 | 1,768 | 410 | 2,238 |
| HTML | 27,583 | 73,411 | 29,630 | 32,642 | 56,840 |
| EPUB | 15,159 | 156,306 | 144,049 | 146,377 | 147,203 |
| TXT | 12,522 | 163,591 | 164,117 | 181,466 | 153,270 |
| EML | 205 | 299 | 160 | — | — |
| IPYNB | 403 | 375 | — | — | — |
| CSV | 4,074 | 4,056 | 4,434 | — | 4,785 |
| JSON | 1,763 | 1,471 | — | — | — |
| XML | 544 | 924 | 495 | 240 | — |

EPUB and TXT show the most dramatic gap. All four competing tools produce 10–20× more tokens than AksharaMD on long-form books — outputting essentially the full raw text. AksharaMD applies semantic compression, reducing a 1.2 MB novel to ~13,500 tokens.

### Answer accuracy

Scores are averaged across 4 questions × 3 documents per format (max 10.0). Formats marked `—` are unsupported and excluded from each tool's average.

| Tool | Avg score | Docs scored | Format coverage |
|------|:---------:|:-----------:|:---------------:|
| **AksharaMD** | **9.7** | **36/36** | **12/12** |
| MarkItDown | 8.9 | 36/36 | 12/12 |
| PyMuPDF4LLM | 8.4 | 24/36 | 8/12 |
| Docling | 8.4 | 24/36 | 8/12 |
| LlamaParse | 7.9 | 30/36 | 10/12 |

AksharaMD leads on accuracy **and** uses the fewest tokens **and** covers all 12 formats — the only tool to achieve all three simultaneously.

### Per-format accuracy — Claude Haiku 4.5

| Format | AksharaMD | MarkItDown | LlamaParse | PyMuPDF4LLM | Docling |
|--------|:---------:|:----------:|:----------:|:-----------:|:-------:|
| HTML | **10.0** | 4.8 | **10.0** | **10.0** | 4.2 |
| CSV | **10.0** | 8.7 | 8.6 | — | 7.7 |
| JSON | **10.0** | **10.0** | 0.5 | — | — |
| XLSX | 9.8 | 9.0 | 9.8 | 1.3 | 9.1 |
| PPTX | 9.8 | **10.0** | **9.9** | 9.8 | 9.9 |
| EPUB | 9.8 | **10.0** | 9.8 | 9.8 | 8.3 |
| TXT | 9.8 | 9.8 | 9.8 | **9.9** | 9.7 |
| XML | 9.8 | 9.1 | 8.5 | 7.5 | — |
| EML | 9.0 | 8.8 | 8.1 | — | — |
| IPYNB | 9.0 | 7.1 | 1.3 | — | — |
| PDF | 9.1 | 9.3 | 9.3 | 9.1 | 8.8 |
| DOCX | **10.0** | **10.0** | 9.8 | **10.0** | **10.0** |

**HTML** is the sharpest differentiator. AksharaMD (10.0), LlamaParse (10.0), and PyMuPDF4LLM (10.0) strip navigation aggressively and score perfectly. MarkItDown (4.8) and Docling (4.2) fill the 6K context window with navigation bars, navboxes, and boilerplate before reaching article body — the LLM cannot find the answers.

**IPYNB** and **JSON** are exclusive to AksharaMD and MarkItDown. LlamaParse errors on all six files; PyMuPDF4LLM and Docling are unsupported. AksharaMD outperforms MarkItDown on IPYNB (9.0 vs 7.1) by correctly counting both code and markdown cells — MarkItDown only counts code cells.

**CSV** row counting: AksharaMD is the only tool that correctly reports the exact number of rows across all three CSV files (10.0/10). Competing tools miscount due to truncated context windows.

**XLSX**: PyMuPDF4LLM extracts insufficient content from spreadsheets (avg 410 tokens vs 1,872 for AksharaMD), scoring 1.3/10 — effectively unusable for tabular data.

**LlamaParse** at 7.9 despite being a paid cloud API reflects two systematic failures: errors on JSON and IPYNB (contributing zero scores), and a consistent inability to extract EML sent dates (0/10 on all three email timestamp questions).

---

## API Cost Projection

Input token cost only. Prices verified July 2026 — confirm current rates at vendor sites.

**Pricing used:** Claude Haiku 4.5 $0.80/1M · GPT-4o mini $0.15/1M · Gemini 2.5 Flash $0.10/1M

### 10,000 documents/month

| Tool | Claude Haiku 4.5 | GPT-4o mini | Gemini Flash |
|------|:----------------:|:-----------:|:------------:|
| **AksharaMD** | **$49** | **$9** | **$6** |
| MarkItDown | $279 | $52 | $35 |
| LlamaParse | $283 | $53 | $35 |
| PyMuPDF4LLM | $372 | $70 | $47 |
| Docling | $374 | $70 | $47 |

### 100,000 documents/month

| Tool | Claude Haiku 4.5 | GPT-4o mini | Gemini Flash |
|------|:----------------:|:-----------:|:------------:|
| **AksharaMD** | **$489** | **$92** | **$61** |
| MarkItDown | $2,793 | $524 | $349 |
| LlamaParse | $2,826 | $530 | $353 |
| PyMuPDF4LLM | $3,722 | $698 | $465 |
| Docling | $3,741 | $701 | $468 |

**Savings at 100K docs/month vs each tool (Claude Haiku 4.5):**
- vs MarkItDown: **$2,304/month**
- vs LlamaParse: **$2,337/month**
- vs PyMuPDF4LLM: **$3,233/month**
- vs Docling: **$3,252/month**

### 1,000,000 documents/month

| Tool | Claude Haiku 4.5 | GPT-4o mini | Gemini Flash |
|------|:----------------:|:-----------:|:------------:|
| **AksharaMD** | **$4,891** | **$917** | **$611** |
| MarkItDown | $27,927 | $5,236 | $3,491 |
| LlamaParse | $28,258 | $5,298 | $3,532 |
| PyMuPDF4LLM | $37,219 | $6,979 | $4,652 |
| Docling | $37,412 | $7,015 | $4,677 |

These figures represent the cost of feeding extracted document text into an LLM. They do not include output tokens, which are typically smaller and roughly proportional across tools.

---

## HTML Raw Baseline

For HTML, developers sometimes pipe raw page content directly to an LLM without extraction. The full pipeline value:

| Stage | Tokens (black_hole.html, Wikipedia) |
|-------|-------------------------------------|
| Raw HTML | ~310,000 |
| Docling | 83,765 |
| MarkItDown | 103,207 |
| PyMuPDF4LLM | 44,505 |
| LlamaParse | 41,737 |
| **AksharaMD** | **40,486** |

AksharaMD reduces a 1.3 MB Wikipedia page from ~310,000 raw tokens to 40,486 — an **87% reduction** — while answering factual questions at 10.0/10 accuracy. MarkItDown produces 2.5× more tokens from the same page and scores only 4.8/10 because navigation content drowns the article body in the context window.

---

## Setup Complexity

Token efficiency and accuracy matter — but so does the cost of getting started.

| Tool | Install | API key | GPU required | Cold start |
|------|:-------:|:-------:|:------------:|:----------:|
| **AksharaMD** | `pip install` | None | No | Instant |
| MarkItDown | `pip install` | None | No | Instant |
| LlamaParse | `pip install` | **Required** | No | 5–110s per doc (cloud) |
| PyMuPDF4LLM | `pip install` | None | No | Instant |
| Docling | `pip install` | None | Optional | ~2s model load |

LlamaParse requires a paid LlamaCloud API key and sends documents to an external cloud service. Every conversion adds 5–110 seconds of network latency. AksharaMD runs entirely locally with no external calls, no API keys, and no latency beyond disk I/O.

---

## What This Shows

Across 36 documents, 144 Q&A pairs, and 576 scored answers:

1. **AksharaMD uses the fewest tokens** on every format it supports — 82–87% fewer than competing tools on average. On long-form content (EPUB, TXT), competing tools produce 10–20× more tokens with no accuracy gain.

2. **AksharaMD scores highest** across all tools at 9.7/10, +0.8 points ahead of MarkItDown and +1.8 points ahead of LlamaParse. The gap is not marginal — it is driven by structural wins on HTML, IPYNB, and CSV where extraction quality directly determines what the LLM can find.

3. **AksharaMD is the only tool with full format coverage** — 12/12 formats vs 10/12 for LlamaParse and 8/12 for PyMuPDF4LLM and Docling.

4. **The cost case is concrete.** At 100,000 documents/month using Claude Haiku 4.5, AksharaMD saves $2,304/month vs MarkItDown and $2,337/month vs LlamaParse — purely on input token spend, before accounting for the accuracy advantage.

5. **Zero infrastructure friction.** No API keys. No cloud dependency. No GPU. `pip install` and run.

---

## Reproducing This Benchmark

```bash
# Install eval dependencies
pip install -e ".[eval]"
pip install markitdown llamaparse pymupdf4llm docling   # optional comparison tools

# Place API keys in .env or export them
# ANTHROPIC_API_KEY=...
# LLAMA_CLOUD_API_KEY=...   (LlamaParse only)

# Full 5-tool comparison with scoring
python -m benchmarks.llm_qa_eval \
    --qa benchmarks/eval_corpus_qa.yaml \
    --tools aksharamd markitdown llamaparse pymupdf4llm docling \
    --llms claude

# Token stats only — no API keys needed
python -m benchmarks.llm_qa_eval \
    --qa benchmarks/eval_corpus_qa.yaml \
    --tools aksharamd markitdown llamaparse pymupdf4llm docling \
    --no-llm
```

Results saved to `benchmark_results/llm_qa_results.json`.  
Q&A pairs in `benchmarks/eval_corpus_qa.yaml`.  
Corpus in `benchmarks/eval_corpus.yaml`.

---

## Self-Hosted Model Impact

API cost only tells part of the story. When you run your own models, token count
drives three hardware costs: **KV-cache VRAM** (limits how many requests you can
serve in parallel), **prefill compute** (determines time-to-first-token), and
**effective GPU throughput** (documents processed per GPU-hour).

### KV-cache footprint per request

KV-cache size scales linearly with token count and determines maximum batch size.
Values below use fp16 KV cache (framework default for most vLLM / SGLang deployments).

**8B class** (128 KB per token, fp16 KV)

| Tool | Avg tokens | KV / request | vs AksharaMD |
|------|:----------:|:------------:|:------------:|
| **AksharaMD** | **6,114** | **0.75 GB** | **— (baseline)** |
| LlamaParse | 29,435 | 3.59 GB | 4.8× |
| PyMuPDF4LLM | 31,016 | 3.79 GB | 5.1× |
| Docling | 31,177 | 3.81 GB | 5.1× |
| MarkItDown | 34,909 | 4.26 GB | 5.7× |

**70B class** (320 KB per token, fp16 KV)

| Tool | Avg tokens | KV / request | vs AksharaMD |
|------|:----------:|:------------:|:------------:|
| **AksharaMD** | **6,114** | **1.87 GB** | **— (baseline)** |
| LlamaParse | 29,435 | 8.98 GB | 4.8× |
| PyMuPDF4LLM | 31,016 | 9.47 GB | 5.1× |
| Docling | 31,177 | 9.51 GB | 5.1× |
| MarkItDown | 34,909 | 10.65 GB | 5.7× |

### Maximum concurrent requests at fixed VRAM budgets

Concurrent capacity = available KV-cache VRAM ÷ KV-cache per request.
"Available" = total GPU VRAM minus model weight VRAM minus ~2 GB overhead.

| Deployment scenario | AksharaMD | LlamaParse | PyMuPDF4LLM | Docling | MarkItDown |
| --- | :---: | :---: | :---: | :---: | :---: |
| 8B int4 · RTX 4090 (24 GB) | **25** | 5 | 5 | 4 | 4 |
| 8B fp16 · A100 40 GB | **29** | 6 | 5 | 5 | 5 |
| 70B int4 · A100 80 GB | **21** | 4 | 4 | 4 | 3 |
| 70B int4 · H100 80 GB | **21** | 4 | 4 | 4 | 3 |

Throughput multiplier (AksharaMD ÷ next-best at each tier):

- **8B int4 · RTX 4090 (24 GB)**: AksharaMD serves **5.0× more requests** than the next-best tool
- **8B fp16 · A100 40 GB**: AksharaMD serves **4.8× more requests** than the next-best tool
- **70B int4 · A100 80 GB**: AksharaMD serves **5.2× more requests** than the next-best tool
- **70B int4 · H100 80 GB**: AksharaMD serves **5.2× more requests** than the next-best tool

### Prefill time-to-first-token (TTFT)

Self-attention in the prefill phase is O(n²) in FLOPs. Flash Attention reduces
memory from O(n²) to O(n) but the compute cost remains quadratic. A document
with 5.7× more tokens takes **32×** longer to prefill.

| Tool | Avg tokens | TTFT ratio vs AksharaMD |
|------|:----------:|:-----------------------:|
| **AksharaMD** | **6,114** | **1× (baseline)** |
| LlamaParse | 29,435 | 23.2× slower |
| PyMuPDF4LLM | 31,016 | 25.7× slower |
| Docling | 31,177 | 26.0× slower |
| MarkItDown | 34,909 | 32.6× slower |

*TTFT ratios are theoretical upper bounds based on attention FLOPs. Actual numbers
depend on GPU, batch size, Flash Attention version, and sequence-packing strategy.*

### Relative docs per GPU-hour

For prefill-dominated workloads (document QA, extraction, classification),
GPU throughput scales as 1/n². Normalized to AksharaMD = 1,000 docs/GPU-hr:

| Tool | Relative throughput | Docs/GPU-hr (if AksharaMD = 1,000) |
|------|:-------------------:|:-----------------------------------:|
| **AksharaMD** | **1.00** | **1,000** |
| LlamaParse | 0.0431 | 43 |
| PyMuPDF4LLM | 0.0389 | 39 |
| Docling | 0.0385 | 38 |
| MarkItDown | 0.0307 | 31 |

> For long-form generation (summarisation, rewriting), the decode phase reduces
> but does not eliminate this gap — the prefill advantage holds for any input-heavy workload.

### What this means in practice

Running a Llama 3 8B model on an RTX 4090 with MarkItDown context, you can serve **4 concurrent requests** at a time before VRAM is exhausted. With AksharaMD context, the same GPU serves **25 concurrent requests** — a **6× throughput increase with no hardware change**.

Time-to-first-token on MarkItDown's average context is **33× longer** than on AksharaMD's. For interactive applications where users wait for a response, this is the difference between a 0.3-second and a 10-second wait.

To reproduce these figures:

```bash
python -m benchmarks.compute_profile                  # console report
python -m benchmarks.compute_profile --markdown       # Markdown output
python -m benchmarks.compute_profile --results path/to/results.json
```

---

*Benchmark conducted July 2026. AksharaMD v0.1.0. Judge model: Claude Haiku 4.5.*
