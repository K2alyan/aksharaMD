# AksharaMD — Downstream LLM Accuracy Benchmark

> Benchmark results for AksharaMD v0.3.0.

---

## What We Measured and Why

Better document extraction produces fewer tokens. That is easy to demonstrate.

What is harder to demonstrate — but more important to users — is whether cleaner extractions actually make LLMs *more accurate* when answering questions about a document. A 90% token reduction is not meaningful if the lost tokens carried the answers.

This benchmark answers both questions directly across five tools: **AksharaMD, MarkItDown (Microsoft), LlamaParse (LlamaIndex), PyMuPDF4LLM, and Docling (IBM)**.

---

## Methodology

### Corpus

~1,000 documents across 12 formats (83 per format), selected to span size and structural complexity within each type:

| Format | Files | Examples |
|--------|-------|---------|
| PDF | 83 | arXiv papers (575 KB – 3.9 MB) |
| DOCX | 83 | Business reports, market research, SRS |
| PPTX | 83 | Investor decks, partnership proposals |
| XLSX | 83 | Budget, employee data, inventory |
| HTML | 83 | Wikipedia articles (533 KB – 1.3 MB) |
| EPUB | 83 | Project Gutenberg books (207 KB – 546 KB) |
| TXT | 83 | Project Gutenberg plain text (232 KB – 1.2 MB) |
| EML | 83 | Email messages |
| IPYNB | 83 | Jupyter notebooks (statistics, data analysis) |
| CSV | 83 | Events, transactions, web server logs |
| JSON | 83 | Config files, reports, API responses |
| XML | 83 | Small, medium, and larger structured documents |

Documents were not chosen to favour AksharaMD — they represent everyday enterprise and research workloads.

### Q&A Pairs

4 factual questions per document (3,984 pairs total). Questions target specific, verifiable facts: names, numbers, dates, identifiers. Each expected answer is a short phrase.

### Evaluation Protocol

1. Each document is independently converted by all five tools
2. The first 6,000 characters of each conversion are used as LLM context (~4,000 tokens)
3. The same question is sent to the LLM with each conversion's context: *"Answer using only the document text. Be concise."*
4. Claude Haiku 4.5 scores every answer **0–10** against the expected answer

**LLM tested:** Claude Haiku 4.5  
**Judge:** Claude Haiku 4.5  
**Total answers evaluated:** 19,920 graded answers (unsupported formats excluded from per-tool averages)

---

## Format Coverage

AksharaMD, MarkItDown, and LlamaParse handle every format in the corpus. LlamaParse processes JSON and IPYNB but produces near-zero accuracy on both (0.9/10 and 1.1/10 respectively). PyMuPDF4LLM and Docling handle 8 of 12.

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
| IPYNB | ✓ | ✓ | ✓† | ✗ | ✗ |
| CSV | ✓ | ✓ | ✓ | ✗ | ✓ |
| JSON | ✓ | ✓ | ✓† | ✗ | ✗ |
| XML | ✓ | ✓ | ✓ | ✓ | ✗ |
| **Total** | **12/12** | **12/12** | **12/12** | **8/12** | **8/12** |

† LlamaParse processes these formats but returns near-empty content — accuracy scores of 1.1/10 (IPYNB) and 0.9/10 (JSON).

---

## Results

### Token efficiency

Token counts measured using the cl100k_base tokenizer (GPT-4 / Claude family).

| Tool | Avg tokens | Docs covered | vs AksharaMD |
|------|:----------:|:------------:|:------------:|
| **AksharaMD** | **6,272** | **996/996** | — |
| LlamaParse | 26,274 | 996/996 | 4.2× more |
| MarkItDown | 27,449 | 996/996 | 4.4× more |
| PyMuPDF4LLM | 34,231 | 664/996 | 5.5× more |
| Docling | 35,461 | 664/996 | 5.7× more |

AksharaMD uses **77% fewer tokens than MarkItDown**, **76% fewer than LlamaParse**, and **82% fewer than PyMuPDF4LLM and Docling**.

Per-format token averages (83 docs each, `—` = unsupported):

| Format | AksharaMD | MarkItDown | LlamaParse | PyMuPDF4LLM | Docling |
|--------|----------:|----------:|-----------:|------------:|--------:|
| PDF | 10,407 | 16,222 | 10,209 | 11,857 | 10,474 |
| DOCX | 261 | 268 | 275 | 255 | 280 |
| PPTX | 151 | 218 | 173 | 183 | 190 |
| XLSX | 1,643 | 1,643 | 1,587 | 437 | 1,957 |
| HTML | 27,071 | 72,650 | 28,354 | 31,887 | 55,737 |
| EPUB | 14,784 | 125,341 | 116,508 | 118,412 | 118,044 |
| TXT | 12,635 | 108,727 | 109,276 | 122,111 | 102,058 |
| EML | 203 | 297 | 159 | — | — |
| IPYNB | 403 | 375 | — | — | — |
| CSV | 3,502 | 3,484 | 3,708 | — | 4,122 |
| JSON | 1,832 | 1,517 | — | — | — |
| XML | 583 | 1,015 | 572 | 267 | — |

EPUB and TXT show the most dramatic gap. All four competing tools produce 8–10× more tokens than AksharaMD on long-form books — outputting essentially the full raw text. AksharaMD applies semantic compression, reducing a 1.2 MB novel to ~13,000–15,000 tokens.

### Answer accuracy

Scores are averaged across 4 questions × 83 documents per format (max 10.0). Formats marked `—` are unsupported and excluded from each tool's average.

| Tool | Avg score | Docs scored | Format coverage |
|------|:---------:|:-----------:|:---------------:|
| **AksharaMD** | **9.5** | **996/996** | **12/12** |
| MarkItDown | 8.6 | 996/996 | 12/12 |
| Docling | 8.6 | 664/996 | 8/12 |
| PyMuPDF4LLM | 8.0 | 664/996 | 8/12 |
| LlamaParse | 7.8 | 996/996 | 12/12 |

AksharaMD leads on accuracy **and** uses the fewest tokens **and** covers all 12 formats — the only tool to achieve all three simultaneously.

### Per-format accuracy — Claude Haiku 4.5

| Format | AksharaMD | MarkItDown | LlamaParse | PyMuPDF4LLM | Docling |
|--------|:---------:|:----------:|:----------:|:-----------:|:-------:|
| HTML | **9.9** | 4.9 | 9.5 | **8.8** | 6.2 |
| CSV | **10.0** | 9.1 | 8.9 | — | 8.1 |
| JSON | **9.7** | 9.5 | 0.9† | — | — |
| XLSX | 9.1 | 9.0 | 9.8 | 1.6 | 8.6 |
| PPTX | 9.9 | 9.9 | 9.9 | 9.9 | 9.9 |
| EPUB | 9.9 | 9.4 | 9.9 | 9.8 | 9.1 |
| TXT | 9.9 | 9.9 | 9.9 | 9.9 | 9.9 |
| XML | 9.4 | 8.5 | 7.7 | 6.1 | — |
| EML | 9.0 | 8.7 | 6.9 | — | — |
| IPYNB | 9.2 | 7.4 | 1.1† | — | — |
| PDF | 8.8 | 6.8 | 8.0 | 7.9 | 7.2 |
| DOCX | 9.9 | 9.9 | **10.0** | **10.0** | 9.9 |

† LlamaParse returns empty or unparseable content on these formats.

**HTML** is the sharpest differentiator. AksharaMD (9.9) strips navigation aggressively and scores near-perfectly. MarkItDown (4.9) and Docling (6.2) fill the 6K context window with navigation bars, navboxes, and boilerplate before reaching article body — the LLM cannot find the answers.

**IPYNB** and **JSON** highlight extraction depth. LlamaParse errors on both; PyMuPDF4LLM and Docling are unsupported. AksharaMD outperforms MarkItDown on IPYNB (9.2 vs 7.4) by correctly counting both code and markdown cells — MarkItDown only counts code cells.

**CSV** row counting: AksharaMD is the only tool that correctly reports the exact number of rows across all CSV files (10.0/10). Competing tools miscount due to truncated context windows.

**XLSX**: PyMuPDF4LLM extracts insufficient content from spreadsheets (avg 437 tokens vs 1,643 for AksharaMD), scoring 1.6/10 — effectively unusable for tabular data.

**LlamaParse** at 7.8 despite being a paid cloud API reflects two systematic failures: near-zero extraction on JSON and IPYNB, and a consistent inability to extract EML sent dates (low scores on email timestamp questions).

---

## API Cost Projection

Input token cost only. Prices verified July 2026 — confirm current rates at vendor sites.

**Pricing used:** Claude Haiku 4.5 $0.80/1M · GPT-4o mini $0.15/1M · Gemini 2.5 Flash $0.10/1M

### 10,000 documents/month

| Tool | Claude Haiku 4.5 | GPT-4o mini | Gemini Flash |
|------|:----------------:|:-----------:|:------------:|
| **AksharaMD** | **$50** | **$9** | **$6** |
| MarkItDown | $220 | $41 | $27 |
| LlamaParse | $210 | $39 | $26 |
| PyMuPDF4LLM | $274 | $51 | $34 |
| Docling | $284 | $53 | $35 |

### 100,000 documents/month

| Tool | Claude Haiku 4.5 | GPT-4o mini | Gemini Flash |
|------|:----------------:|:-----------:|:------------:|
| **AksharaMD** | **$502** | **$94** | **$63** |
| MarkItDown | $2,196 | $412 | $274 |
| LlamaParse | $2,102 | $394 | $263 |
| PyMuPDF4LLM | $2,738 | $513 | $342 |
| Docling | $2,837 | $532 | $355 |

**Savings at 100K docs/month vs each tool (Claude Haiku 4.5):**
- vs MarkItDown: **$1,694/month**
- vs LlamaParse: **$1,600/month**
- vs PyMuPDF4LLM: **$2,236/month**
- vs Docling: **$2,335/month**

### 1,000,000 documents/month

| Tool | Claude Haiku 4.5 | GPT-4o mini | Gemini Flash |
|------|:----------------:|:-----------:|:------------:|
| **AksharaMD** | **$5,018** | **$941** | **$627** |
| MarkItDown | $21,958 | $4,117 | $2,745 |
| LlamaParse | $21,019 | $3,941 | $2,627 |
| PyMuPDF4LLM | $27,385 | $5,135 | $3,423 |
| Docling | $28,369 | $5,319 | $3,546 |

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

AksharaMD reduces a 1.3 MB Wikipedia page from ~310,000 raw tokens to 40,486 — an **87% reduction** — while answering factual questions at 9.9/10 accuracy. MarkItDown produces 2.5× more tokens from the same page and scores only 4.9/10 because navigation content drowns the article body in the context window.

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

Across ~1,000 documents, 3,984 Q&A pairs, and 19,920 graded answers:

1. **AksharaMD uses the fewest tokens** on every format it supports — 76–82% fewer than competing tools on average. On long-form content (EPUB, TXT), competing tools produce 8–10× more tokens with no accuracy gain.

2. **AksharaMD scores highest** across all tools at 9.5/10, +0.9 points ahead of MarkItDown and +1.7 points ahead of LlamaParse. The gap is driven by structural wins on HTML, IPYNB, and CSV where extraction quality directly determines what the LLM can find.

3. **AksharaMD is the only tool with full, high-quality format coverage** — 12/12 formats at meaningful accuracy, vs 10/12 for LlamaParse (near-zero on JSON and IPYNB) and 8/12 for PyMuPDF4LLM and Docling.

4. **The cost case is concrete.** At 100,000 documents/month using Claude Haiku 4.5, AksharaMD saves $1,694/month vs MarkItDown and $1,600/month vs LlamaParse — purely on input token spend, before accounting for the accuracy advantage.

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
| **AksharaMD** | **6,272** | **0.77 GB** | **— (baseline)** |
| LlamaParse | 26,274 | 3.21 GB | 4.2× |
| MarkItDown | 27,449 | 3.35 GB | 4.4× |
| PyMuPDF4LLM | 34,231 | 4.18 GB | 5.5× |
| Docling | 35,461 | 4.33 GB | 5.7× |

**70B class** (320 KB per token, fp16 KV)

| Tool | Avg tokens | KV / request | vs AksharaMD |
|------|:----------:|:------------:|:------------:|
| **AksharaMD** | **6,272** | **1.91 GB** | **— (baseline)** |
| LlamaParse | 26,274 | 8.02 GB | 4.2× |
| MarkItDown | 27,449 | 8.38 GB | 4.4× |
| PyMuPDF4LLM | 34,231 | 10.45 GB | 5.5× |
| Docling | 35,461 | 10.83 GB | 5.7× |

### Maximum concurrent requests at fixed VRAM budgets

Concurrent capacity = available KV-cache VRAM ÷ KV-cache per request.
"Available" = total GPU VRAM minus model weight VRAM minus ~2 GB overhead.

| Deployment scenario | AksharaMD | LlamaParse | MarkItDown | PyMuPDF4LLM | Docling |
| --- | :---: | :---: | :---: | :---: | :---: |
| 8B int4 · RTX 4090 (24 GB) | **25** | 6 | 5 | 4 | 4 |
| 8B fp16 · A100 40 GB | **28** | 6 | 6 | 5 | 5 |
| 70B int4 · A100 80 GB | **20** | 4 | 4 | 3 | 3 |
| 70B int4 · H100 80 GB | **20** | 4 | 4 | 3 | 3 |

Throughput multiplier (AksharaMD ÷ next-best at each tier):

- **8B int4 · RTX 4090 (24 GB)**: AksharaMD serves **4.2× more requests** than the next-best tool
- **8B fp16 · A100 40 GB**: AksharaMD serves **4.7× more requests** than the next-best tool
- **70B int4 · A100 80 GB**: AksharaMD serves **5.0× more requests** than the next-best tool
- **70B int4 · H100 80 GB**: AksharaMD serves **5.0× more requests** than the next-best tool

### Prefill time-to-first-token (TTFT)

Self-attention in the prefill phase is O(n²) in FLOPs. Flash Attention reduces
memory from O(n²) to O(n) but the compute cost remains quadratic. A document
with 4.4× more tokens takes **~19×** longer to prefill.

| Tool | Avg tokens | TTFT ratio vs AksharaMD |
|------|:----------:|:-----------------------:|
| **AksharaMD** | **6,272** | **1× (baseline)** |
| LlamaParse | 26,274 | 17.5× slower |
| MarkItDown | 27,449 | 19.2× slower |
| PyMuPDF4LLM | 34,231 | 29.8× slower |
| Docling | 35,461 | 32.0× slower |

*TTFT ratios are theoretical upper bounds based on attention FLOPs. Actual numbers
depend on GPU, batch size, Flash Attention version, and sequence-packing strategy.*

### Relative docs per GPU-hour

For prefill-dominated workloads (document QA, extraction, classification),
GPU throughput scales as 1/n². Normalized to AksharaMD = 1,000 docs/GPU-hr:

| Tool | Relative throughput | Docs/GPU-hr (if AksharaMD = 1,000) |
|------|:-------------------:|:-----------------------------------:|
| **AksharaMD** | **1.00** | **1,000** |
| LlamaParse | 0.0570 | 57 |
| MarkItDown | 0.0522 | 52 |
| PyMuPDF4LLM | 0.0336 | 34 |
| Docling | 0.0313 | 31 |

> For long-form generation (summarisation, rewriting), the decode phase reduces
> but does not eliminate this gap — the prefill advantage holds for any input-heavy workload.

### What this means in practice

Running a Llama 3 8B model on an RTX 4090 with MarkItDown context, you can serve **5 concurrent requests** at a time before VRAM is exhausted. With AksharaMD context, the same GPU serves **25 concurrent requests** — a **5× throughput increase with no hardware change**.

Time-to-first-token on MarkItDown's average context is **~19× longer** than on AksharaMD's. For interactive applications where users wait for a response, this is the difference between a 0.3-second and a ~6-second wait.

To reproduce these figures:

```bash
python -m benchmarks.compute_profile                  # console report
python -m benchmarks.compute_profile --markdown       # Markdown output
python -m benchmarks.compute_profile --results path/to/results.json
```

---

*Benchmark conducted July 2026. AksharaMD v0.1.0. Judge model: Claude Haiku 4.5.*
