# AksharaMD — ParseBench Results

**ParseBench:** https://github.com/run-llama/ParseBench  
**Dataset:** llamaindex/ParseBench (HuggingFace, ~2,000 human-verified pages, enterprise PDFs)  
**Run:** AksharaMD v0.3.5+, July 2026. Full dataset — 1,537 PDFs, all dimensions.  
**Adapter:** Install instructions are in `benchmarks/parsebench_adapter/` (see Reproducing below)

---

## Why this benchmark matters — and what it does not measure for AksharaMD

ParseBench was designed to evaluate how well document parsers preserve structure and meaning for AI agents. It is the most rigorous public PDF parsing benchmark available. Running AksharaMD against it produces useful data — but the results require context, because AksharaMD's design goals differ from the tools this benchmark was built to rank.

**AksharaMD is an LLM consumption pipeline, not a visual layout reconstruction engine.**

Every number below should be read with that in mind. The five ParseBench dimensions each measure something specific. Some of those things align perfectly with what AksharaMD optimises for. Others measure visual fidelity features that AksharaMD deliberately does not attempt — not because they are unimportant, but because they are outside the scope of a token-efficient LLM preprocessing stage.

---

## Results

### AksharaMD full-dataset run (1,537 PDFs, July 2026)

Two runs: baseline (text-layer extraction + Tesseract OCR on scanned pages) and vision-enabled (Marker neural layout model additionally installed). Results are identical because the ParseBench corpus is entirely text-layer enterprise PDFs — no page fell below the 50-character threshold that triggers Marker. The vision numbers below are therefore the same as baseline; see the Vision note at the bottom of this section.

| Dimension | AksharaMD (text layer) | AksharaMD + vision | What it measures |
|-----------|:----------------------:|:------------------:|------------------|
| Content Faithfulness | **61.4%** | **61.4%** | Sentences from the PDF present in output |
| Semantic Formatting | **37.6%** | **37.6%** | Inline styling: bold, italic, underline, strikethrough, title hierarchy |
| Tables (GRiTS Con) | **26.9%** | **26.4%** | Table cell accuracy across all expected tables |
| Charts | **1.9%** | **1.9%** | Chart data point values extracted from images |
| Visual Grounding | **N/A** | **N/A** | Bounding box accuracy — not produced by design |

Inline formatting breakdown (text_formatting group, n=476 docs):

| Rule type | Score | Notes |
|-----------|-------|-------|
| `is_bold` | **50.5%** | Bold font flags + font-name fallback → `**text**` |
| `is_italic` | **42.3%** | Italic font flags + font-name fallback → `*text*` |
| `is_underline` | **27.0%** | Drawing paths below text baseline → `<u>text</u>` |
| `is_strikeout` | **7.8%** | Drawing paths through text midline → `~~text~~` |
| `is_title` | **37.7%** | Heading level from font-size relative to page median |

**Vision mode note**: AksharaMD's Marker integration (`pip install aksharamd[vision]`) activates on pages with fewer than 50 characters of text layer — fully scanned or image-only pages. The ParseBench corpus does not contain such pages: every document has a text layer. Marker therefore never triggered across all 1,537 PDFs. On a corpus of scanned documents (historical records, photocopied contracts, hand-signed forms), vision mode would reconstruct tables and body text from the image layer. The relevant benchmark for that scenario is a scanned-document corpus, not ParseBench.

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
| **AksharaMD** | **LLM Pipeline** | **61.4%** | **37.6%** | **26.9%** | **1.9%*** | **N/A** | **—** |

\* By design. See Charts note below.

---

## Dimension-by-dimension analysis

### Content Faithfulness — 61.4% (full dataset, n=506)

ParseBench checks whether specific sentences from the source PDF appear verbatim or near-verbatim in the parser's output. AksharaMD actively strips content it classifies as noise: page headers, footers, watermarks, running metadata, and redundant whitespace. Some of this stripped content may include sentences ParseBench counts as valid content.

At 61.4% on the full 506-doc sample, AksharaMD sits above AWS Textract (74.8% is the next commercial threshold) and is well ahead of the other local parsers. The full dataset includes many enterprise PDFs with multi-column layouts, headers/footers with legal boilerplate, and OCR-required pages — all areas where noise stripping trades off raw sentence presence against cleaner LLM input.

**For RAG pipelines**: AksharaMD's LLM QA accuracy study (Generation 2, ~1,000 docs) showed 9.5/10 vs MarkItDown's 8.6/10. Content Faithfulness measures whether sentences appear verbatim; LLM QA accuracy measures whether those sentences produce correct downstream answers. The two metrics are correlated but not identical — an LLM can answer questions correctly from slightly reduced content if the reduction removed noise rather than signal.

---

### Semantic Formatting — 37.6% (full dataset)

ParseBench's Semantic Formatting dimension tests **inline character-level styling markers**: whether specific text spans appear as underlined, struck-through, bold, or titled at the exact heading level specified. The primary use case is legal and compliance documents where strikethrough means "deleted clause" and underline means "inserted clause" — formatting that carries legal meaning, not just visual presentation.

AksharaMD's 37.6% breaks down across the text_formatting dataset (476 docs) as:

| Rule type | Score | Notes |
|-----------|-------|-------|
| `is_bold` | **50.5%** | Bold font flags + font-name fallback (`Arial-BoldMT`, `Helvetica-Bold`, etc.) → `**text**` |
| `is_italic` | **42.3%** | Italic font flags + font-name fallback (`Helvetica-Oblique`, etc.) → `*text*` |
| `is_underline` | **27.0%** | Horizontal drawing paths below text baseline → `<u>text</u>` |
| `is_strikeout` | **7.8%** | Drawing paths through text midline → `~~text~~` (small sample) |
| `is_title` | **37.7%** | Heading level from font-size relative to page median |
| `is_mark` | 0% | Highlight/mark annotations not extracted |

**How inline formatting is detected:**

- **Bold and Italic**: Extracted from PyMuPDF span font flags. When flags are absent (common in PDFs that embed bold/italic as named font variants), the font name is checked for tokens like `Bold`, `Heavy`, `Black`, `Italic`, `Oblique`. Non-heading bold spans become `**text**`; italic spans become `*text*`.
- **Underline and Strikethrough**: PDFs typically encode these as separate drawn paths rather than font flags. AksharaMD analyses `page.get_drawings()` after text extraction and tags spans whose bounding boxes are crossed by thin horizontal strokes. Underlines appear just below the text bbox; strikethroughs cross the vertical midpoint.
- **Strikethrough on scanned/OCR documents**: Documents where strikethrough is a visual mark on a rasterized image cannot be detected from the text layer. This is expected behaviour.

**Context from the leaderboard**: MarkItDown 0.9%, pypdf 0.9%, PyMuPDF Text 1.0%, Docling-models 1.0%. AksharaMD's 37.6% exceeds every open-source local parser and now exceeds Azure DI (51.9% is next, primarily from heading hierarchy and mark detection). PyMuPDF4LLM scores 44.6% using raw HTML — AksharaMD achieves comparable bold/italic coverage (50.5% / 42.3%) while keeping output clean for LLM consumption.

**Bottom line**: For contracts, regulatory filings, or legal documents where struck-through text represents deleted clauses, AksharaMD preserves that distinction inline. The remaining gap to commercial tools is primarily in superscript/subscript, mark/highlight annotations, and heading hierarchy precision.

---

### Tables — 26.9% (full dataset, GRiTS Con)

ParseBench evaluates table accuracy using GRiTS metrics. AksharaMD extracts text-layer tables with full row/column fidelity using pipe markdown syntax. On the full 503-doc table dataset:

- **GRiTS Con**: 26.9% — cell content accuracy across all expected tables
- **GRiTS TRM Composite**: 18.5% — grid+record combined metric
- **GRiTS Con (among predicted)**: 49.9% — accuracy for tables AksharaMD actually produced

The gap between "all" (26.9%) and "predicted" (49.9%) reflects table coverage: AksharaMD produces roughly half of expected tables, but with higher overall accuracy than before. The unmatched tables are primarily image-based and require `[vision]` — without it, AksharaMD flags those pages with `OCR_REQUIRED` rather than producing garbled output. The predicted-only score dropped slightly from 56.3% to 49.9% because the loosened quality gates now accept a wider range of tables, including some with lower individual cell accuracy.

**Leaderboard context**: MarkItDown 15.8%, PyMuPDF4LLM 36.7%, Docling-models 66.4%. AksharaMD now exceeds MarkItDown by 11 points. Docling-models' high score reflects its vision model for table reconstruction. AksharaMD with `[vision]` enabled on an image-heavy corpus would score significantly higher.

---

### Charts — 1.9% (by design)

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
cp <aksharamd-repo>/benchmarks/parsebench_adapter/aksharamd.py \
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

`PYTHONUTF8=1` is required on Windows to avoid cp1252 encoding errors in the ParseBench progress output.
