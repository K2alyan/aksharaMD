# Benchmark Results

Full methodology and results: [`benchmarks/LLM_QA_BENCHMARK.md`](../LLM_QA_BENCHMARK.md)  
Corpus structure: [`benchmarks/corpus_manifest.json`](../corpus_manifest.json)  
Scoring prompts: [`benchmarks/scoring_prompt.md`](../scoring_prompt.md)  
Corpus Q&A file: [`benchmarks/eval_corpus_qa.yaml`](../eval_corpus_qa.yaml)

---

## Summary — AksharaMD v0.3.3 (July 2026)

**Corpus:** 996 documents × 12 formats × 4 Q&A pairs = 3,984 pairs, 19,920 graded answers  
**Primary judge:** Claude Haiku 4.5  
**Validation judges:** Gemini 2.5 Flash (144 docs), GPT-4o mini (100 docs)

### Answer accuracy — Claude Haiku 4.5 (primary, all 5 tools)

| Tool | Avg score (0–10) | Documents scored | Formats covered |
|------|:----------------:|:----------------:|:---------------:|
| **AksharaMD** | **9.5** | **996 / 996** | **12 / 12** |
| MarkItDown | 8.6 | 996 / 996 | 12 / 12 |
| Docling | 8.6 | 664 / 996 | 8 / 12 |
| PyMuPDF4LLM | 8.0 | 664 / 996 | 8 / 12 |
| LlamaParse | 7.8 | 996 / 996 | 12 / 12 |

### Answer accuracy — validation judges (AksharaMD vs MarkItDown)

| Judge | AksharaMD | MarkItDown | Docs |
|-------|:---------:|:----------:|:----:|
| Gemini 2.5 Flash | **9.3** | 8.7 | 144 |
| GPT-4o mini | **9.3** | 8.7 | 100 |

### Token efficiency — average tokens per document

| Tool | Avg tokens | vs AksharaMD |
|------|:----------:|:------------:|
| **AksharaMD** | **6,272** | — |
| LlamaParse | 26,274 | 4.2× more |
| MarkItDown | 27,449 | 4.4× more |
| PyMuPDF4LLM | 34,231 | 5.5× more |
| Docling | 35,461 | 5.7× more |

AksharaMD is the only tool to achieve the highest accuracy, lowest token count, and full format coverage simultaneously.

---

## Reproducing these results

```bash
# Install eval dependencies
pip install -e ".[eval]"

# Token counts only (no API keys needed)
python -m benchmarks.llm_qa_eval \
    --qa benchmarks/eval_corpus_qa.yaml \
    --tools aksharamd markitdown llamaparse pymupdf4llm docling \
    --no-llm

# Full accuracy benchmark (requires ANTHROPIC_API_KEY)
python -m benchmarks.llm_qa_eval \
    --qa benchmarks/eval_corpus_qa.yaml \
    --tools aksharamd markitdown llamaparse pymupdf4llm docling \
    --llms claude

# Validation run with Gemini (requires GEMINI_API_KEY)
python -m benchmarks.llm_qa_eval \
    --qa benchmarks/eval_corpus_qa.yaml \
    --tools aksharamd markitdown \
    --llms gemini

# Validation run with GPT-4o mini (requires OPENAI_API_KEY)
python -m benchmarks.llm_qa_eval \
    --qa benchmarks/eval_corpus_qa.yaml \
    --tools aksharamd markitdown \
    --llms openai
```

Results are saved to `benchmark_results/llm_qa_results.json`.

---

## Notes on benchmark design

- **Q&A pairs were generated from each document before any tool processed it.** The questions are document-specific and factual — they target names, numbers, dates, and identifiers that appear verbatim in the source.
- **The same 6,000-character context window is used for all tools.** This mirrors realistic RAG conditions where context is bounded.
- **The judge model (Claude Haiku 4.5) is separate from the answering model.** This avoids model-specific biases inflating scores for tools whose output happens to match a particular model's preferences.
- **LlamaParse JSON and IPYNB scores (0.9 and 1.1) reflect extraction failure**, not judge leniency — LlamaParse returned near-empty content for these formats.
- **The corpus was not curated to favour AksharaMD.** arXiv PDFs, Wikipedia HTML, and Project Gutenberg EPUB/TXT represent standard enterprise and research workloads.
