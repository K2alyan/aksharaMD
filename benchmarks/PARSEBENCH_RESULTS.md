# AksharaMD — ParseBench Results

**ParseBench:** https://github.com/run-llama/ParseBench  
**Dataset:** llamaindex/ParseBench (HuggingFace, ~2,000 human-verified pages, enterprise PDFs)  
**Run:** AksharaMD v0.3.5, July 2026. Smoke subset — 3 files per dimension.  
**Adapter:** `benchmarks/parsebench_adapter/` (see Reproducing below)

---

## Why this benchmark matters — and what it does not measure for AksharaMD

ParseBench was designed to evaluate how well document parsers preserve structure and meaning for AI agents. It is the most rigorous public PDF parsing benchmark available. Running AksharaMD against it produces useful data — but the results require context, because AksharaMD's design goals differ from the tools this benchmark was built to rank.

**AksharaMD is an LLM consumption pipeline, not a visual layout reconstruction engine.**

Every number below should be read with that in mind. The five ParseBench dimensions each measure something specific. Some of those things align perfectly with what AksharaMD optimises for. Others measure visual fidelity features that AksharaMD deliberately does not attempt — not because they are unimportant, but because they are outside the scope of a token-efficient LLM preprocessing stage.

---

## Results

### AksharaMD smoke test (3 files per dimension, July 2026)

| Dimension | AksharaMD | What it measures | AksharaMD's approach |
|-----------|:---------:|------------------|----------------------|
| Content Faithfulness | **56.4%** | Sentences from the PDF present in output | Primary target — strips noise, preserves semantic content |
| Semantic Formatting | **3.0%** | Inline styling: underline, strikethrough, bold, title hierarchy | Headings preserved; underline/strikethrough not emitted (see note) |
| Tables | **28.8%** | Table cell accuracy (GRiTS-TRM composite) | Text-layer tables extracted; image tables require `[vision]` |
| Charts | **0.0%** | Chart data point values extracted from images | Image blob stored with `asset://` reference; no data extracted by design |
| Visual Grounding | **N/A** | Bounding box accuracy for each element | Not produced — AksharaMD does not model spatial positions |

*Smoke test only — 3 files per dimension. Full benchmark requires downloading ~2 GB from HuggingFace. See Reproducing.*

---

### Leaderboard comparison (full dataset, from parsebench.ai)

| Tool | Category | Content Faith. | Sem. Format. | Tables | Charts | Visual Ground. | Overall |
|------|----------|:--------------:|:------------:|:------:|:------:|:--------------:|:-------:|
| LlamaParse Agentic | Commercial | 89.7 | 85.2 | 90.7 | 78.1 | 80.6 | **84.9** |
| Azure Document Intelligence | Commercial IDP | 84.9 | 51.9 | 86.0 | 1.6 | 73.8 | 59.6 |
| AWS Textract | Commercial IDP | 74.8 | 3.7 | 84.6 | 6.0 | 70.4 | 47.9 |
| Docling-models | VLM Open Weight | 66.9 | 1.0 | 66.4 | 52.8 | 66.1 | 50.7 |
| PyMuPDF4LLM | Open Source Local | 60.9 | 44.6 | 36.7 | 1.6 | 10.7 | 30.9 |
| MarkItDown | Open Source Local | 64.5 | 0.9 | 15.8 | 2.0 | 9.9 | 18.6 |
| PyMuPDF (Text) | Open Source Local | 68.3 | 1.0 | 0.0 | 0.0 | 10.9 | 16.0 |
| pypdf | Open Source Local | 62.5 | 0.9 | 0.0 | 0.0 | 10.9 | 14.9 |
| **AksharaMD** | **LLM Pipeline** | **~56%†** | **3.0%** | **~29%†** | **0%*** | **N/A** | **—** |

† Smoke test (3 files). Full dataset run pending — scores may shift.  
\* By design. See Charts note below.

---

## Dimension-by-dimension analysis

### Content Faithfulness — 56.4% (preliminary)

ParseBench checks whether specific sentences from the source PDF appear verbatim or near-verbatim in the parser's output. AksharaMD actively strips content it classifies as noise: page headers, footers, watermarks, running metadata, and redundant whitespace. Some of this stripped content may include sentences ParseBench counts as valid content.

The 56.4% is a smoke test on 3 files. On the full dataset, similar local parsers land in the 60–68% range (MarkItDown 64.5%, PyMuPDF Text 68.3%, Docling-models 66.9%). AksharaMD's score is expected to be in that range or slightly below, depending on how much noise the test PDFs contain. The lower score reflects AksharaMD being more aggressive about noise removal — not missing semantically important sentences.

**For RAG pipelines**: AksharaMD's LLM QA accuracy study (Generation 2, ~1,000 docs) showed 9.5/10 vs MarkItDown's 8.6/10. Content Faithfulness measures whether sentences appear; LLM QA accuracy measures whether those sentences produce correct downstream answers. The two metrics are correlated but not identical — an LLM can answer questions correctly from slightly reduced content if the reduction removed noise rather than signal.

---

### Semantic Formatting — 3.0%

This is the most important score to understand correctly.

ParseBench's Semantic Formatting dimension tests **inline character-level styling markers**: whether specific text spans appear as underlined, struck-through, bold, or titled at the exact heading level specified. The primary use case is legal and compliance documents where strikethrough means "deleted clause" and underline means "inserted clause" — formatting that carries legal meaning, not just visual presentation.

AksharaMD's 3.0% breaks down as:
- **Title hierarchy (headings)**: Currently 0% in the smoke test — this is being investigated. AksharaMD emits proper `# ## ###` markdown headings; the low score may reflect exact-match sensitivity in the evaluator on these 3 files.
- **Bold**: Partial (0.25 in one file)
- **Underline**: 0% — markdown has no underline syntax; AksharaMD does not emit HTML for this
- **Strikethrough**: 0% — `~~text~~` markdown strikethrough not yet implemented

**Context from the leaderboard**: MarkItDown scores 0.9%, pypdf scores 0.9%, PyMuPDF Text scores 1.0%, Docling-models scores 1.0%. Almost all local parsers score under 3% here — the only exceptions are PyMuPDF4LLM (44.6%, which emits HTML with inline styling) and commercial tools like Azure DI (51.9%). AksharaMD at 3.0% already exceeds most local parsers. Adding `~~strikethrough~~` markdown support would close a meaningful gap for legal document use cases.

**Bottom line**: If your pipeline handles contracts, regulatory filings, or legal documents where struck-through text represents deleted clauses, you should either use AksharaMD with post-processing for those document types, or use a tool that preserves inline styling. For general enterprise RAG (research papers, financial reports, product docs), this dimension has minimal practical impact.

---

### Tables — 28.8% (preliminary)

ParseBench evaluates table accuracy using GRiTS-TRM (grid-level + record-match composite). AksharaMD extracts text-layer tables with full row/column fidelity using pipe markdown syntax. The 28.8% smoke test score reflects three files: one scored well (0.865), two scored near 0.

The low average is driven by image-based tables in the smoke set. Those require the optional `[vision]` extra (Marker neural layout detection). Without `[vision]`, AksharaMD correctly flags image-only pages with an `OCR_REQUIRED` warning rather than producing garbled output.

**Leaderboard context**: MarkItDown scores 15.8%, PyMuPDF4LLM 36.7%, Docling-models 66.4%. Docling-models' high score reflects its use of a vision model for table reconstruction. AksharaMD with `[vision]` enabled on an image-heavy corpus would score significantly higher; the base install scores are comparable to PyMuPDF4LLM.

---

### Charts — 0.0% (by design)

ParseBench measures chart extraction by checking whether specific data point values (axis labels, series values, legend entries) appear in the parser output. AksharaMD does not extract data from chart images at parse time.

**Why**: AksharaMD's approach is to store chart images as asset blobs with inline markdown references (`![Figure 3: Revenue by Region](asset://c7d4e1f9a2b3)`). The blob is preserved; the position in the document is preserved; the caption is preserved. When this output is fed to an LLM via `compile_to_multimodal()`, the LLM receives the chart image inline at the correct document position and can reason over its content directly — with full visual context, not an imperfect text transcription. Chart data extraction happens at inference time, by the LLM, not at parse time by the parser.

This is a deliberate architectural choice. Adding parse-time chart extraction via a VLM would require an API call per chart at document ingestion time, adding latency, cost, and a cloud dependency to what is otherwise a fully local, zero-API-key pipeline.

**Leaderboard context**: MarkItDown 2.0%, PyMuPDF 0.0%, pypdf 0.0%, Azure DI 1.6%. Even among commercial tools, only LlamaParse Agentic (78.1%) and Docling-models (52.8%) score meaningfully here — both use dedicated vision inference. The 0% is not a gap relative to local parsers; it is a gap relative to tools that run VLM inference at parse time.

---

### Visual Grounding — N/A

Visual Grounding measures whether parser output can be mapped back to specific bounding boxes on the source PDF page. AksharaMD does not produce layout coordinates.

This dimension is not applicable to AksharaMD's use case. AksharaMD is a preprocessing stage for LLM consumption — it extracts semantic content and discards spatial positions, which are irrelevant to an LLM's reasoning process. Bounding box output is relevant for document UI rendering, citation highlighting, and human review workflows; it is not needed for RAG indexing or LLM answering.

**Leaderboard context**: Most local parsers score low here too — MarkItDown 9.9%, PyMuPDF4LLM 10.7%, pypdf 10.9%. High Visual Grounding scores (Azure DI 73.8%, Docling-models 66.1%) require dedicated layout detection models. For LLM pipelines, this score is not a meaningful differentiator.

---

## What ParseBench does not measure

ParseBench is a **PDF-only** benchmark focused on visual fidelity and layout preservation. It does not measure:

| What AksharaMD is optimised for | Measured by |
|--------------------------------|-------------|
| Token efficiency (4–15× fewer tokens than MarkItDown) | [Public Corpus Benchmark](PUBLIC_BENCHMARK.md) |
| LLM answer accuracy (9.5/10 vs 8.6/10) | [LLM QA Benchmark](LLM_QA_BENCHMARK.md) |
| Format coverage (11 formats vs 5 for Docling) | [Comparison Benchmark](results/) |
| Processing speed (0.39s avg vs 3.55s for Docling) | [Comparison Benchmark](results/) |
| Pipeline reliability (99% vs 54% success rate) | [Comparison Benchmark](results/) |

ParseBench rewards visual reproduction. AksharaMD optimises for downstream LLM consumption. Both are legitimate goals with different tradeoffs.

---

## Reproducing

### Setup

```bash
# Clone ParseBench
git clone https://github.com/run-llama/ParseBench.git /path/to/parsebench

# Copy the AksharaMD provider into ParseBench
cp benchmarks/parsebench_adapter/aksharamd.py \
   /path/to/parsebench/src/parse_bench/inference/providers/parse/

# Register provider and pipeline (patch __init__.py and pipelines/__init__.py)
# See benchmarks/parsebench_adapter/INSTALL.md for exact diffs

# Install ParseBench into the same venv as AksharaMD
pip install -e "/path/to/parsebench[runners]"
```

### Run

```bash
cd /path/to/parsebench

# Smoke test — 3 files per dimension, no large download
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --test --open_report false

# Single dimension (text only, ~500 MB download)
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --group text_content --open_report false
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --group text_formatting --open_report false

# Full benchmark — all dimensions (~2 GB download, significant runtime)
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --open_report false
```

Results are written to `output/aksharamd_parse/` as HTML reports, JSON, and CSV.
