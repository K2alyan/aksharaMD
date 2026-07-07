<p align="center">
  <img src="assets/logo.png" alt="AksharaMD" width="120" />
</p>

<p align="center">
  <a href="https://github.com/K2alyan/aksharaMD/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/K2alyan/aksharaMD/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://pypi.org/project/aksharamd/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aksharamd.svg?cacheSeconds=300"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue.svg">
  <a href="LICENSE"><img alt="License: PolyForm NC" src="https://img.shields.io/badge/License-PolyForm%20NC-orange.svg"></a>
</p>

# AksharaMD

**An LLM document ingestion pipeline with a built-in quality gate.**

Every compilation returns a **0–100 AI Readiness Score** and per-block extraction confidence — so you know whether to trust the output before it reaches your vector store, not after your LLM gives a wrong answer.

AksharaMD takes any document — PDF, DOCX, XLSX, audio, image, archive, and more (40+ document categories, 118 registered extensions) — and produces structured, token-efficient Markdown designed to be fed directly to an LLM. The goal is not a visual replica of the source file. The goal is to give your LLM exactly what it needs to reason over the same content — at a fraction of the token cost — with a clear signal of how reliable that extraction actually is.

Runs entirely on-device. No cloud calls, no data leaving your machine, no API keys required.

---

## Why AksharaMD

### The problem no parser solves: you don't know if the output is trustworthy

Every parser returns text. None of them tell you whether that text is reliable enough to embed. A scanned PDF, a table-heavy report, or a document with garbled OCR can produce output that looks complete — until the LLM answers a question wrong. By then, bad data is already in your vector store.

AksharaMD produces a quality signal alongside the content:

- **AI Readiness Score 0–100** with quality bands — HIGH (≥85) / OK (≥70) / RISKY (≥50) / POOR (<50) — on every compilation
- **Per-block extraction confidence** — every block is tagged EXTRACTED, INFERRED, or AMBIGUOUS before it hits your embedder
- **Named warnings** — `OCR_REQUIRED`, `LOW_TEXT_DENSITY`, `GLYPH_ARTIFACTS`, `REPEATED_CONTENT`, and five more — tell you exactly what's wrong and how to fix it
- **Score drops automatically** when extraction is unreliable — no manual checking required

### One tool. Every format. No stitching.

Most teams assemble document pipelines from multiple tools: one for PDFs, another for scanned pages, another for spreadsheets, another for audio. Each has its own output format, its own failure modes, its own maintenance cost. When a new document type arrives, the pipeline breaks.

AksharaMD handles all of it — native PDFs, scanned PDFs, DOCX, XLSX, PPTX, HTML, EPUB, email, audio, archives, images, code, and more — with a single consistent output format and a single quality signal across 40+ document categories and 118 registered extensions. Install once, handle whatever your users throw at you.

### The token and speed problem

Every format wastes tokens differently: a PDF with headers, footers, and watermarks; a DOCX with revision history; an XLSX with thousands of empty cells. AksharaMD strips all of that before your LLM sees it.

- **15× fewer tokens than [MarkItDown](https://github.com/microsoft/markitdown)** on our internal benchmark corpus (101 documents, 23 format types) — see [benchmark methodology](benchmarks/LLM_QA_BENCHMARK.md) for corpus details and reproducibility limitations; results depend on corpus composition
- **98.5% less noise** on the same corpus — 3.7 avg noise lines vs 250.1 for MarkItDown
- **Same speed as MarkItDown** on the base install — 0.24s average across all formats, no ML overhead
- **27× faster than [Docling](https://github.com/DS4SD/docling)** on the PDF subset (20 arXiv/technical-report documents) — Docling averaged ~30s per PDF; AksharaMD averaged ~1s on this subset
- **Structured output** — real headings, tables, code blocks; not flat text
- **Fully local** — no cloud API, no document upload, no data retention concerns

### Speed is a choice, not a constraint

The base install (`pip install aksharamd`) has **zero ML dependencies** — it runs at MarkItDown speed and handles the majority of real-world documents. For harder document types, optional extras add ML capabilities surgically:

- **Scanned PDFs** without extras: the tool flags them with `OCR_REQUIRED` and a RISKY or POOR score — you know immediately, before bad data reaches your vector store.
- **Scanned PDFs** with `[ocr]` or `[vision]`: full text or layout-aware table extraction. The ML work runs only on image-only pages — your clean PDF pages are unaffected.
- **Math-heavy PDFs** with `[math]`: LaTeX equation extraction. Runs only on pages with undecodable font spans.
- **Audio files** with `[audio]`: Whisper transcription. No impact on non-audio documents.

The tradeoff is explicit and bounded: ML extras slow down only the document types that genuinely need ML. A pipeline processing 99% clean PDFs and 1% scanned forms still runs at base speed for 99% of its work.

---

## What AksharaMD does not guarantee

AksharaMD measures **extraction reliability** — how faithfully it converted a document into text. A high Readiness Score means the text was extracted cleanly. It does not mean your RAG pipeline will produce correct answers.

Specifically, AksharaMD makes no guarantee about:

- **Retrieval accuracy.** A clean extraction does not mean the right chunks will be retrieved for a given query. Retrieval quality depends on your chunking strategy, embedding model, and index configuration.
- **Final answer correctness.** Even perfectly extracted text can produce wrong LLM answers if the question requires reasoning the model cannot perform, or if the answer is not in the retrieved chunks.
- **Citation correctness.** AksharaMD does not generate citations. If your pipeline produces citations, their accuracy is a function of your retrieval and generation steps.
- **Optimal chunking for your embedding model.** The default semantic chunks are a reasonable starting point. Different embedding models have different context-window sensitivities. You should evaluate chunk size and overlap for your specific model and query distribution.
- **Embedding dilution.** A clean parse can still produce semantically broad chunks. A chapter that covers three unrelated topics will embed as a mixture — relevant to none of the three queries precisely. This is a retrieval problem, not an extraction problem.

**Run retrieval evals before production deployment.** The Readiness Score tells you whether the document was extracted reliably. It does not substitute for end-to-end RAG evaluation against your actual queries and expected answers.

AksharaMD is also not a pixel-perfect visual layout reproduction engine. The goal is to give your LLM the semantic content of a document at minimum token cost — not to reproduce how the document looks on screen.

---

## Quickstart

Requires **Python 3.11 or later**.

```bash
pip install aksharamd
```

AksharaMD uses subcommands. The pattern is always `aksharamd <command> <file>`. The primary command is `compile`:

```bash
aksharamd compile report.pdf     # convert a file to AI-optimized Markdown + JSON
aksharamd validate report.pdf    # check extraction quality without writing output
aksharamd formats                # list all supported file types
```

> **Note:** `aksharamd report.pdf` will not work — the subcommand (e.g. `compile`) is always required.

Output is written to `output/report/`:

```
output/report/
├── document.md       # compiled Markdown
├── document.json     # structured block model
├── manifest.json     # token counts, timings, readiness score
├── validation.json   # extraction issues
└── chunks/           # semantic chunks as JSON
```

**Scanned PDFs** (requires Tesseract 5+ installed at the system level — `pip install` alone is not enough):

```bash
pip install "aksharamd[ocr]"
# Install Tesseract 5+ separately: https://github.com/tesseract-ocr/tesseract
# Make sure the tesseract binary is on your PATH, then:
aksharamd compile scanned.pdf
```

**Image-based table reconstruction** (uses [Marker](https://github.com/VikParuchuri/marker) neural layout detection — requires PyTorch, downloads ~3 GB of models on first run):

```bash
pip install "aksharamd[vision]"
aksharamd compile scanned-with-tables.pdf
```

**Claude Desktop (MCP):**

```bash
aksharamd mcp-config --write
# Restart Claude Desktop — AksharaMD will appear in the tools panel
```

---

## Installation

### Base install

```bash
pip install aksharamd
```

The base install is intentionally lightweight. It handles the vast majority of documents out of the box — PDFs with a real text layer, Word, PowerPoint, Excel, HTML, Markdown, plain text, EPUB, RSS, email, archives, and more — across 40+ document categories covering 118 registered extensions, with no system binaries and no large model downloads.

### Optional extras

AksharaMD uses a modular extras system. Each extra unlocks a document type that the base install cannot handle — or handles with degraded quality. Install only what your use case requires, or install everything at once.

| Document type | Extra | What it unlocks | Speed impact | Added size |
|---|---|---|---|---|
| Scanned / image-only PDFs | `[ocr]` | Full text extraction via Tesseract | ~1–3s per image page | &lt;5 MB pip + [Tesseract binary](https://github.com/tesseract-ocr/tesseract) (~75 MB) |
| Scanned PDFs with image tables | `[vision]` | Layout-aware table reconstruction via [Marker](https://github.com/VikParuchuri/marker) | ~10–60s per image page (ML inference) | ~3 GB model weights (PyTorch, downloaded once) |
| Math equations and symbols | `[math]` | LaTeX equation extraction via [pix2tex](https://github.com/lukas-blecher/LaTeX-OCR) | ~2–10s on math-heavy pages (ML inference) | ~500 MB model weights (PyTorch, downloaded once) |
| Audio and video files | `[audio]` | Speech-to-text via [Whisper](https://github.com/openai/whisper) | Real-time to 2× real-time depending on model | 75 MB–1.5 GB (PyTorch + [ffmpeg](https://ffmpeg.org) on PATH) |
| S3 files (`s3://` URIs) | `[cloud]` | Direct S3 input, no manual download | No impact | ~20 MB |

**The speed impact only applies when the feature is actually used.** A pipeline processing mostly clean PDFs and Office files runs at base speed — ML inference only kicks in for the pages or files that require it. Documents with no image pages or math are completely unaffected by installing `[vision]` or `[math]`.

> **Note on PyTorch:** `[vision]`, `[math]`, and `[audio]` share a single PyTorch install (~2 GB). Installing more than one pays that cost once.

**Without the extras, you still get useful output.** Scanned pages emit an `OCR_REQUIRED` warning and a RISKY or POOR readiness score rather than silently producing garbage — you know immediately which documents need attention.

```bash
# Install a single extra
pip install "aksharamd[ocr]"

# Install multiple extras
pip install "aksharamd[ocr,cloud]"
```

### Install everything

If your documents are varied — contracts, research PDFs, scanned forms, spreadsheets, audio recordings — or you simply don't want to make decisions about extras upfront:

```bash
pip install "aksharamd[full]"
```

`[full]` is the single-command answer to "handle whatever arrives." It installs all extras and supports every format AksharaMD covers — native PDFs, scanned PDFs with tables, math equations, audio, cloud storage, and all Office and web formats. One install, one pipeline.

The tradeoff: PyTorch plus all model weights requires approximately 5–6 GB on first run. After that, the models are cached — subsequent runs on documents that need ML are fast. If install size matters, use individual extras instead.

### Install from source

```bash
git clone https://github.com/K2alyan/aksharaMD.git
cd aksharaMD
pip install -e .
```

### Optional system tools

These add support for niche formats and require no `pip install` — just the binary on your `PATH`:

| Format | Requirement |
|--------|-------------|
| Legacy Office (`.doc`, `.ppt`) | [LibreOffice](https://www.libreoffice.org) on PATH |
| AsciiDoc, Org-mode, Textile, MediaWiki, DocBook, man/roff, OPML | [Pandoc](https://pandoc.org/installing.html) on PATH |

---

## Known Limitations

The core pipeline is stable and production-tested across 118 file extensions, but please note the following before using in production workflows.

**OCR for scanned PDFs requires a system binary.**
`pip install "aksharamd[ocr]"` installs the Python wrapper (`pytesseract`) but not Tesseract itself. You must also install [Tesseract 5+](https://github.com/tesseract-ocr/tesseract) at the OS level and make sure the `tesseract` binary is on your `PATH`. Without it, scanned pages produce a RISKY or POOR score and an `OCR_REQUIRED` warning.

**Tesseract OCR extracts text from image pages but cannot reconstruct image-based tables.**
When a scanned PDF contains tables rendered as images (e.g. spreadsheet-style grids saved as PNG), Tesseract reads the cell text as a flat stream of paragraphs — column structure is lost. For layout-aware table reconstruction from image pages, install the optional Marker integration: `pip install "aksharamd[vision]"`. Marker uses neural layout detection to recover table structure. It requires PyTorch and downloads approximately 3 GB of model weights on first run. For offline / air-gapped use, pre-cache the models on a connected machine and copy them:
```bash
python -c "from marker.models import create_model_dict; create_model_dict()"
# Then copy ~/.cache/huggingface/hub/ to the target machine and set:
# export HF_HUB_OFFLINE=1
```

**Complex PPTX layouts are supported but experimental.**
Standard slide content, bullet points, and embedded tables extract reliably. Complex animations, heavily layered slide masters, and custom layout templates may produce incomplete output.

**Outlook `.msg` parsing is lower-confidence than EML/DOCX/PDF.**
The `.msg` format is a proprietary binary container. Body text and attachments extract correctly in most cases, but embedded calendar objects, rich-text encoding edge cases, and S/MIME-signed messages may not parse completely.

**Windows: prefer Windows Terminal or PowerShell 7.**
Legacy `cmd.exe` uses the cp1252 code page, which cannot render some Unicode characters used in the CLI output (e.g. box-drawing lines from Rich). Run AksharaMD in [Windows Terminal](https://aka.ms/terminal), VS Code's integrated terminal, or PowerShell 7. Setting `PYTHONUTF8=1` also resolves most encoding issues.

**`mcp-config --write` creates an automatic backup.**
Before overwriting your Claude Desktop config, AksharaMD saves a timestamped copy (e.g. `claude_desktop_config.1720123456.bak.json`) in the same directory. No data is lost, but you may want to clean these up manually after confirming the new config works.

---

## CLI Reference

### `compile`

Compile a document or URL into Markdown, JSON, and semantic chunks.

```bash
aksharamd compile <source> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-o`, `--output` | `output` | Output directory |
| `--timings` | — | Show per-stage timing breakdown |
| `--quiet` | — | Suppress all console output |
| `-v`, `--verbose` | — | Enable debug logging |
| `--min-readiness-score INTEGER` | — | Exit non-zero if readiness score is below this value. Output files are still written. Useful as a CI/CD ingestion gate. |
| `--json` | — | Print a single JSON object to stdout (suppresses Rich panels). Compatible with `--min-readiness-score`. |

**Examples:**

```bash
# Compile a local file
aksharamd compile report.pdf

# Specify output directory
aksharamd compile report.pdf -o compiled/

# Compile a URL
aksharamd compile https://arxiv.org/pdf/2301.00001

# Show timing breakdown
aksharamd compile report.pdf --timings

# Suppress output (for scripting)
aksharamd compile report.pdf --quiet

# CI/CD ingestion gate — fail the build if readiness score is below 70
aksharamd compile report.pdf --min-readiness-score 70

# Machine-readable JSON output (for scripting or CI)
aksharamd compile report.pdf --json

# JSON output with readiness gate
aksharamd compile report.pdf --json --min-readiness-score 70
```

**JSON output fields** (when `--json` is used):

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | `false` if validation errors or readiness threshold not met |
| `source` | string | Source file path or URL |
| `output_dir` | string | Directory where output files were written |
| `readiness_score` | int \| null | Readiness score 0–100 (null if compilation failed before scoring) |
| `quality_band` | string \| null | `HIGH` / `OK` / `RISKY` / `POOR` |
| `warning_codes` | list[string] | Named warning codes (e.g. `OCR_REQUIRED`) |
| `errors` | list[string] | Validation error messages |
| `chunks` | int \| null | Number of semantic chunks produced |
| `pages` | int \| null | Page or section count |
| `optimized_tokens` | int \| null | Tokens after pipeline optimisation |
| `elapsed_seconds` | float \| null | Wall-clock compilation time |

### `validate`

Validate extraction without writing output files.

```bash
aksharamd validate report.pdf
aksharamd validate https://example.com/doc.pdf
```

Exits `0` on success, `1` if validation errors are found.

### `benchmark`

Compile multiple files and compare them side by side.

```bash
aksharamd benchmark doc1.pdf doc2.docx doc3.html
```

### `stats`

Show cumulative token savings across all compilations.

```bash
aksharamd stats
aksharamd stats --reset    # clear the ledger
```

### `show-manifest`

Print the manifest from a previous compilation.

```bash
aksharamd show-manifest output/report/
```

### `corpus`

Compile every supported file under a directory into token-budget-bounded chunks, with automatic near-duplicate detection.

```bash
aksharamd corpus <source_dir> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-o`, `--output` | — | Write chunks to a JSON file |
| `--budget` | `60000` | Maximum tokens per chunk |
| `--dedup-threshold` | `0.5` | Jaccard similarity threshold for near-duplicate skipping |

```bash
aksharamd corpus ./documents/ --budget 8000 -o corpus.json
```

### `mcp-config`

Generate and apply the MCP server configuration for Claude Desktop.

```bash
aksharamd mcp-config           # print config to copy manually
aksharamd mcp-config --write   # write directly to Claude Desktop config
```

### `formats`

List all registered parsers and supported extensions.

```bash
aksharamd formats
```

---

## Python API

### Compile to string

Compile a document without writing any files to disk.

```python
from aksharamd.compiler import Compiler

compiler = Compiler(output_dir="output")
text, ctx = compiler.compile_to_string("report.pdf")

print(text)                                       # compiled Markdown
print(ctx.manifest.optimized_tokens)             # token count after optimisation
print(ctx.manifest.token_reduction_percent)      # % reduction vs raw
print(ctx.manifest.readiness_score)              # confidence 0–100
print(ctx.manifest.elapsed_seconds)              # wall-clock time
```

### Full compilation (writes to disk)

```python
ctx = compiler.compile("report.pdf")
# Output written to output/report/document.md, manifest.json, etc.
```

### Compile from URL

```python
text, ctx = compiler.compile_to_string("https://arxiv.org/pdf/2301.00001")
```

### Stream blocks incrementally

Process blocks as they are extracted and optimized, without waiting for the full document. Useful for feeding a RAG index, vector store, or any pipeline that can act on individual blocks.

```python
from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType

compiler = Compiler()

for block in compiler.stream("report.pdf"):
    if block.type == BlockType.TABLE:
        index_table(block.content)
    elif block.type == BlockType.PARAGRAPH:
        embed_and_store(block.content)
```

`stream()` runs detect → parse → clean → optimize and yields each `Block` in document order. Validate, chunk, manifest, and export stages are skipped — use `compile()` when you need those.

### Multimodal output (images inline)

Returns an Anthropic-compatible content array with text and base64 images interleaved at their document positions.

```python
content, ctx = compiler.compile_to_multimodal("report.pdf")

# Pass directly to the Anthropic API
response = anthropic_client.messages.create(
    model="claude-opus-4-7",
    max_tokens=4096,
    messages=[{"role": "user", "content": content + [{"type": "text", "text": "Summarise this."}]}],
)
```

### Compilation context

The `ctx` object returned by all compile methods exposes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `manifest.source` | `str` | Source file path or URL |
| `manifest.file_type` | `str` | Detected format |
| `manifest.pages` | `int` | Page or section count |
| `manifest.original_tokens` | `int` | Raw token estimate |
| `manifest.optimized_tokens` | `int` | Tokens after pipeline |
| `manifest.token_reduction_percent` | `float` | Reduction percentage |
| `manifest.readiness_score` | `int` | Extraction confidence 0–100 |
| `manifest.elapsed_seconds` | `float` | Wall-clock time |
| `manifest.tables` | `int` | Tables extracted |
| `manifest.chunks` | `int` | Semantic chunks produced |
| `validation.errors` | `list` | Extraction errors, if any |
| `validation.warnings` | `list` | Non-fatal issues |
| `document.blocks` | `list[Block]` | Structured block model |

---

## MCP Server

AksharaMD ships an [MCP](https://modelcontextprotocol.io) server that exposes the compilation pipeline as tools for any MCP-compatible host — Claude Desktop, Cursor, and others.

### Setup

Run this once after installation:

```bash
aksharamd mcp-config --write
```

This detects your Python environment, generates the correct configuration, and writes it directly into your Claude Desktop config file. Restart Claude Desktop — AksharaMD will appear in the tools panel.

To preview the config before writing:

```bash
aksharamd mcp-config
```

### Tools available in Claude

| Tool | Description |
|------|-------------|
| `compile_document` | Compile any file or URL into clean Markdown |
| `compile_document_multimodal` | Compile with charts and diagrams returned inline |
| `get_supported_formats` | List all supported formats and optional dependencies |
| `get_stats` | Lifetime token savings across all compilations |

### HTTP mode (server deployments)

For deployments where Claude connects over the network rather than launching a local process:

```bash
AKSHARAMD_MCP_API_KEY=your-secret-key \
AKSHARAMD_ALLOWED_ROOT=/path/to/allowed/documents \
aksharamd-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

`AKSHARAMD_ALLOWED_ROOT` restricts which directories the server can read from. `AKSHARAMD_MCP_API_KEY` requires clients to send an `X-API-Key` header with every request. Both are optional in local stdio mode but strongly recommended in HTTP mode.

---

## Ecosystem

AksharaMD operates as a **document ingestion layer** — it handles format conversion, noise removal, deduplication, and semantic chunking so that downstream tools receive clean, structured text. It integrates naturally with the following systems.

### LangChain

Replace LangChain's built-in document loaders (`PyPDFLoader`, `UnstructuredFileLoader`, and others) with AksharaMD's extraction pipeline. The output maps directly to `langchain_core.documents.Document`. Check the readiness score before embedding — skip or flag documents that score RISKY or POOR. For a complete loader implementation with readiness gating and per-chunk metadata, see [docs/rag-integration.md](docs/rag-integration.md).

```python
from aksharamd.compiler import Compiler
from langchain_core.documents import Document

compiler = Compiler()
text, ctx = compiler.compile_to_string("report.pdf")

# Skip unreliable extractions before they reach the vector store
if ctx.manifest.readiness_score < 50:
    print(f"POOR extraction ({ctx.manifest.readiness_score}/100) — skipping embedding")
    for w in ctx.validation.warnings:
        print(f"  [{w.code}] {w.message}")
else:
    doc = Document(
        page_content=text,
        metadata={
            "source": ctx.manifest.source,
            "file_type": ctx.manifest.file_type,
            "readiness_score": ctx.manifest.readiness_score,
            "quality_band": ctx.manifest.quality_band,       # HIGH / OK / RISKY / POOR
            "page_count": ctx.manifest.pages,
        },
    )
```

### LlamaIndex

Use AksharaMD as a document reader ahead of LlamaIndex's indexing and retrieval pipeline, replacing `SimpleDirectoryReader` for higher-fidelity extraction on complex formats. Store the readiness score as metadata so retrieval results can be filtered by extraction quality. For a complete `BaseReader` implementation, see [docs/rag-integration.md](docs/rag-integration.md).

```python
from aksharamd.compiler import Compiler
from llama_index.core import Document, VectorStoreIndex

compiler = Compiler()
text, ctx = compiler.compile_to_string("report.pdf")

index = VectorStoreIndex.from_documents([
    Document(
        text=text,
        metadata={
            "source": ctx.manifest.source,
            "readiness_score": ctx.manifest.readiness_score,
            "quality_band": ctx.manifest.quality_band,
        },
    ),
])
```

### Vector stores (ChromaDB, Pinecone, Weaviate, Qdrant)

`compile_corpus()` walks a directory, deduplicates near-identical documents via MinHash LSH, and returns token-budget-bounded chunks ready for embedding and upsert. The `token_budget` parameter should be set to match your embedding model's context window.

```python
from aksharamd.compiler import Compiler

compiler = Compiler()
chunks = compiler.compile_corpus("./documents", token_budget=8_000, dedup_threshold=0.8)

for chunk in chunks:
    texts  = [doc["text"]   for doc in chunk["documents"]]
    ids    = [doc["source"] for doc in chunk["documents"]]
    collection.add(documents=texts, ids=ids)
```

Each chunk carries a `confidence` breakdown (`extracted`, `inferred`, `ambiguous` block counts) that can be stored as metadata and used to filter retrieval results by extraction quality.

### Graphify

AksharaMD is designed to function as a preprocessing layer ahead of knowledge graph construction pipelines such as [Graphify](https://github.com/safishamsi/graphify). Graphify expects coherent text passages as input; AksharaMD handles the upstream problem of extracting that text from arbitrary document formats, including scanned PDFs, archives, and legacy Office files.

The two tools share the same MinHash signature family (Mersenne-prime universal hashing), so near-duplicate detection applied at the AksharaMD stage does not need to be repeated downstream.

```python
from aksharamd.compiler import Compiler

compiler = Compiler()
chunks = compiler.compile_corpus(
    "./documents",
    token_budget=60_000,  # size chunks to Graphify's preferred context window
    dedup_threshold=0.8,
)

for chunk in chunks:
    combined_text = "\n\n---\n\n".join(doc["text"] for doc in chunk["documents"])
    graph_builder.ingest(combined_text)
```

---

## Supported Formats

| Category | Extensions |
|----------|------------|
| Text and markup | `.md` `.txt` `.rst` `.tex` `.html` `.htm` |
| Documents | `.pdf` `.docx` `.pptx` `.xlsx` `.odt` `.ods` `.odp` `.epub` `.rtf` |
| Legacy Office | `.doc` `.ppt` `.xls` *(requires LibreOffice on PATH)* |
| Data | `.json` `.jsonl` `.csv` `.tsv` `.xml` `.yaml` `.toml` |
| Email | `.eml` `.msg` |
| Notebooks | `.ipynb` |
| Source code | `.py` `.js` `.ts` `.go` `.rs` `.java` `.c` `.cpp` `.sql` `.sh` and 30+ more |
| Images (OCR) | `.jpg` `.jpeg` `.png` `.tiff` `.bmp` `.webp` `.gif` *(requires Tesseract)* |
| Audio | `.mp3` `.wav` `.m4a` `.ogg` `.flac` *(requires Whisper + ffmpeg)* |
| Archives | `.zip` `.tar` `.tgz` `.gz` `.bz2` `.xz` `.7z` |
| Feeds | `.rss` `.atom` |

---

## Benchmarks

These are two independent studies. The first measures token efficiency and speed on a small internal corpus. The second measures whether token savings actually produce better LLM answers, using a larger independent corpus with an LLM judge.

---

### Study 1 — Token efficiency and speed

> Internal benchmark corpus, not fully reproducible from committed files. Corpus composition details are in `benchmarks/corpus_manifest.json`.

**Corpus:** 101 documents across 23 format types (internal production corpus, June 2026). The PDF sub-table uses a 20-document arXiv / technical-report subset where Docling was also evaluated.

#### PDF (20 documents — arXiv papers, technical reports)

| Metric | AksharaMD | MarkItDown | Docling |
|--------|-----------|------------|---------|
| Avg tokens | **12,608** | 24,506 | 15,049 |
| Quality score | **94.1** | 92.8 | 93.0 |
| Avg time | **1.09s** | 1.64s | 29.96s |

AksharaMD is **27× faster than Docling** on PDF with comparable quality and **49% fewer tokens than MarkItDown**.

#### All formats (101 documents, 23 types)

| Metric | AksharaMD | MarkItDown |
|--------|-----------|------------|
| Avg tokens | **21,199** | 331,171 |
| Avg noise lines | **3.7** | 250.1 |
| Avg time | 1.40s | 0.48s |
| Format types covered | **23** | 16 |

On this internal benchmark corpus, AksharaMD produces **15× fewer tokens** and **98.5% less noise**. MarkItDown is faster on simple formats; AksharaMD is slower due to deeper extraction (structure detection, deduplication, chunking). Results vary by corpus composition — text-heavy formats show larger gaps than structured spreadsheets.

#### Per-format quality scores

| Format | AksharaMD | MarkItDown |
|--------|-----------|------------|
| HTML | **98.2** | 93.4 |
| JSON | **98.8** | 43.5 |
| RSS / ATOM | **95.1** | 93.6 |
| CSV | **93.8** | 80.0 |
| XLSX | 80.0 | 80.0 |
| PPTX | 72.5 | 81.0 |

Formats with exclusive support (MarkItDown does not handle): `.zip`, `.tar`, `.7z`, `.jsonl`, `.xml`, `.rss`, `.atom`, `.eml`, `.rtf`, `.ipynb`, `.odt`, `.ods`, `.odp`, legacy Office via LibreOffice.

---

### Study 2 — Downstream LLM accuracy

> Benchmark run on AksharaMD v0.3.3. Current package is v0.3.4 (no parser changes affecting these results). See [benchmark docs](benchmarks/LLM_QA_BENCHMARK.md) for full methodology, reproducibility limitations, and what can be run from committed files.

**Corpus:** ~1,000 documents across 12 formats (83 per format) — a separate, independent dataset from Study 1.

Token efficiency is necessary but not sufficient — cleaner extraction only matters if it produces better LLM answers. We tested this with a stratified corpus spanning the full complexity range found in enterprise workloads. Documents were selected across three tiers — following the taxonomy used in document AI benchmarks such as [DocBank](https://github.com/doc-analysis/DocBank) and [PubLayNet](https://github.com/ibm-aur-nlp/PubLayNet):

| Tier | Description | Example formats |
|------|-------------|-----------------|
| **Simple** | Single-column prose, minimal formatting | Plain text, CSV, JSON, email |
| **Structured** | Multi-section with tables and embedded elements | DOCX, XLSX, PPTX, EPUB |
| **Complex** | Layout-intensive, mixed media | Multi-column academic PDFs, Jupyter notebooks, mixed-format archives |

Each document received 4 factual questions, independently answered by all 5 tools and scored 0–10 by Claude Haiku 4.5 as judge (19,920 graded answers total). No tool-specific prompt tuning was applied.

| Tool | Avg score | Avg tokens | Formats covered |
|------|:---------:|:----------:|:---------------:|
| **AksharaMD** | **9.5/10** | **6,272** | **12/12** |
| [MarkItDown](https://github.com/microsoft/markitdown) | 8.6/10 | 27,449 | 12/12 |
| [Docling](https://github.com/DS4SD/docling) | 8.6/10† | 35,461 | 8/12 |
| [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/) | 8.0/10† | 34,231 | 8/12 |
| [LlamaParse](https://github.com/run-llama/llama_parse) | 7.8/10 | 26,274 | 12/12 |

† Accuracy measured on supported formats only (EML, IPYNB, JSON, and XML are unsupported by Docling; EML, IPYNB, CSV, and JSON are unsupported by PyMuPDF4LLM).

On this benchmark corpus, AksharaMD uses **76–82% fewer tokens** than every competing tool while leading on accuracy — and is the only tool that handles all 12 format types. Results depend on corpus composition; see [benchmark docs](benchmarks/LLM_QA_BENCHMARK.md) for methodology, reproducibility limitations, and per-format breakdowns. At 100,000 documents/month, the token difference translates to **$1,600–$2,335 in saved API spend** (Claude Haiku 4.5 pricing, July 2026 — confirm current rates).

### Self-hosted model throughput

Token savings compound on self-hosted models. KV-cache VRAM is the binding constraint on concurrent request capacity, and prefill attention FLOPs are O(n²) in sequence length.

| Deployment scenario | AksharaMD | MarkItDown | Throughput gain |
|---------------------|:---------:|:----------:|:---------------:|
| 8B int4 · RTX 4090 (24 GB) | **25** concurrent | 5 concurrent | **5.0×** |
| 70B int4 · A100 80 GB | **20** concurrent | 4 concurrent | **5.0×** |

MarkItDown's average context takes **~19× longer to prefill** than AksharaMD's on the same GPU — the difference between a 0.3-second and a ~6-second time-to-first-token.

For the full methodology, per-format scores, cost tables, self-hosted throughput analysis, and reproduction instructions, see [`benchmarks/LLM_QA_BENCHMARK.md`](benchmarks/LLM_QA_BENCHMARK.md). Corpus structure is documented in [`benchmarks/corpus_manifest.json`](benchmarks/corpus_manifest.json); exact scoring prompts are in [`benchmarks/scoring_prompt.md`](benchmarks/scoring_prompt.md).

---

## Architecture

```
detect → parse → clean → optimise → validate → chunk → tokenise → manifest → score → export
```

Each stage receives and returns a `CompilationContext` object. Stages are independently pluggable.

```
aksharamd/
├── compiler.py          # Orchestrates the 10-stage pipeline
├── context.py           # CompilationContext — shared state across stages
├── cli.py               # Click-based CLI (compile, validate, benchmark, corpus, stats, mcp-config)
├── mcp_server.py        # FastMCP server (4 tools)
├── ledger.py            # Persistent savings ledger (~/.aksharamd/ledger.jsonl)
├── scoring/
│   └── readiness.py     # Extraction confidence scoring (0–100)
├── models/              # Pydantic v2 models (Manifest, Document, Block, Chunk, Asset)
└── plugins/
    ├── parsers/         # Format-specific extractors (40+ formats)
    ├── cleaners/        # Deduplication, noise removal, whitespace normalisation
    ├── optimizers/      # Token reduction passes
    ├── chunkers/        # Semantic chunking
    ├── exporters/       # Markdown and JSON serialisation
    └── validators/      # Schema and content validation
```

**Plugin registration** uses side-effect imports — parsers register themselves at module load time. Adding a new format requires only a class and a `register_parser("ext", MyParser)` call.

---

## Known Limitations

These are current boundaries of the system. They are not bugs.

**Scanned / image-heavy PDFs.** AksharaMD applies Tesseract OCR to extract text from image pages. Tesseract reads text as a flat stream — it cannot reconstruct table structure from image-based grids. For layout-aware table recovery, install the optional Marker integration (`pip install "aksharamd[vision]"`), which uses neural layout detection to rebuild table Markdown from scanned pages. Complex rotated text or very low-resolution scans may still produce lower-fidelity output; vision-LLM approaches ([olmOCR](https://github.com/allenai/olmocr), [Docling](https://github.com/DS4SD/docling) with VLM mode) are worth evaluating for corpus-level scanned document work.

**Legacy Office formats (`.doc`, `.ppt`).** Parsing requires LibreOffice on the system PATH for format conversion. If LibreOffice is absent, these files are rejected with a clear error. `.docx`, `.pptx`, and `.xlsx` have no such dependency.

**Audio transcription.** Quality depends on the Whisper model size (`base` by default). Set `AKSHARAMD_WHISPER_MODEL=large-v3` for higher accuracy at the cost of speed. Requires ffmpeg.

**Large files.** Files above 500 MB are rejected by default. Raise the limit with `AKSHARAMD_MAX_FILE_BYTES` if needed.

**No MCP streaming.** The CLI shows a live progress spinner and `Compiler.stream()` yields blocks incrementally for programmatic callers (RAG indexing, pipelines). The MCP `compile` tool still returns the full document atomically — SSE block streaming for MCP consumers is on the roadmap.

**No structured logging.** Log output is plain text. Per-request trace IDs, JSON-formatted logs, and Prometheus metrics (request count, latency histograms, token savings counters) are on the roadmap for the HTTP MCP server deployment path.

**Complex multi-row table headers.** Financial tables with merged cells or multi-row headers may produce column name artefacts (`Col1`, `Col2`). The table content is preserved; only the header row is affected.

---

## Documentation

| Document | Description |
|----------|-------------|
| [AI Readiness Score](docs/readiness-score.md) | Score bands, recommended ingestion policy, all warning codes, false positives |
| [Output Schema](docs/output-schema.md) | `manifest.json`, `document.json`, `validation.json`, `chunks/*.json` — schema 1.0, field reference, compatibility guarantee |
| [RAG Integration](docs/rag-integration.md) | Readiness-gated ingestion, per-block confidence filtering, LangChain and LlamaIndex loaders, corpus ingestion |
| [Benchmark Methodology](benchmarks/LLM_QA_BENCHMARK.md) | Full results: corpus, scoring prompts, per-format accuracy, token tables, cost projections, reproduction instructions |

---

## Contributing

Bug reports and pull requests are welcome. Please open an issue first to discuss significant changes.

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal and non-commercial use. For commercial licensing inquiries, please open an issue in this repository.
