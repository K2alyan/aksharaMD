# AksharaMD

> **CONFIDENTIAL — Private Beta v0.1.0**
> This document and the software it describes are proprietary and confidential.
> Do not distribute, copy, or share outside your organisation without written permission from Kalyan Kottapalli.

---

**Compile any document into token-efficient, structured Markdown for LLM workflows.**

AksharaMD is a high-performance document processing pipeline. It ingests 40+ file formats and produces clean, semantically complete Markdown — preserving headings, tables, code blocks, and metadata while eliminating boilerplate. No AI models required. All processing runs locally and deterministically.

---

## Why AksharaMD

- **15× fewer tokens than MarkItDown** on equivalent documents — measured across 23 format types
- **98.5% less noise** — 3.7 avg noise lines vs 250.1 for MarkItDown
- **27× faster than Docling** on PDF with higher extraction quality
- **Structured output** — emits real headings, tables, code blocks; MarkItDown produces flat text
- **AI Readiness Score** — every compilation returns a 0–100 confidence score
- **No ML dependencies** — fast, memory-efficient, and fully reproducible

---

## Quickstart

```bash
pip install -e .

aksharamd compile report.pdf
```

Output is written to `output/report/`:

```
output/report/
├── document.md       # compiled Markdown
├── document.json     # structured block model
├── manifest.json     # token counts, timings, readiness score
├── validation.json   # extraction issues
└── chunks/           # semantic chunks as JSON
```

---

## Installation

```bash
# Base installation
pip install -e .

# With image OCR (requires Tesseract binary — see below)
pip install -e ".[ocr]"

# With audio transcription (requires ffmpeg on PATH)
pip install -e ".[audio]"

# Everything
pip install -e ".[full]"
```

**Optional system dependencies:**

| Feature | Requirement |
|---------|-------------|
| Image OCR | [Tesseract 5+](https://github.com/tesseract-ocr/tesseract) binary on PATH, then `pip install pytesseract` |
| Audio transcription | [ffmpeg](https://ffmpeg.org) on PATH, then `pip install openai-whisper` |
| Legacy Office (`.doc`, `.ppt`) | [LibreOffice](https://www.libreoffice.org) on PATH |

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
```

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

### Tools

| Tool | Description |
|------|-------------|
| `compile_document` | Compile a file path or URL into clean Markdown |
| `compile_document_multimodal` | Compile with images returned inline |
| `get_supported_formats` | List all supported formats and their requirements |
| `get_stats` | Lifetime token savings across all compilations |

### stdio (Claude Desktop, most hosts)

```json
{
  "mcpServers": {
    "aksharamd": {
      "command": "python",
      "args": ["-m", "aksharamd.mcp_server"],
      "cwd": "/path/to/aksharamd"
    }
  }
}
```

### Streamable HTTP

```bash
python -m aksharamd.mcp_server --transport streamable-http --host 0.0.0.0 --port 8000
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

Evaluated against MarkItDown (Microsoft) and Docling (IBM) on an internal corpus of production documents — June 2026.

### PDF (20 documents — arXiv papers, technical reports)

| Metric | AksharaMD | MarkItDown | Docling |
|--------|-----------|------------|---------|
| Avg tokens | **12,608** | 24,506 | 15,049 |
| Quality score | **94.1** | 92.8 | 93.0 |
| Avg time | **1.09s** | 1.64s | 29.96s |

AksharaMD is **27× faster than Docling** on PDF with comparable quality and **49% fewer tokens than MarkItDown**.

### All formats (23 types covered)

| Metric | AksharaMD | MarkItDown |
|--------|-----------|------------|
| Avg tokens | **21,199** | 331,171 |
| Avg noise lines | **3.7** | 250.1 |
| Avg time | 1.40s | 0.48s |
| Format types covered | **23** | 16 |

AksharaMD produces **15× fewer tokens** and **98.5% less noise** across the full corpus. MarkItDown is faster on simple formats; AksharaMD is slower due to deeper extraction (structure detection, deduplication, chunking).

### Per-format quality scores

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

## Architecture

```
detect → parse → clean → optimise → validate → chunk → tokenise → manifest → score → export
```

Each stage receives and returns a `CompilationContext` object. Stages are independently pluggable.

```
aksharamd/
├── compiler.py          # Orchestrates the 10-stage pipeline
├── context.py           # CompilationContext — shared state across stages
├── cli.py               # Click-based CLI (compile, validate, benchmark, stats)
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

**Scanned / image-heavy PDFs.** AksharaMD applies Tesseract OCR to image pages, but complex multi-column layouts, rotated text, or low-resolution scans will produce lower-fidelity output than vision-LLM approaches (olmOCR, Docling with VLM mode). If your corpus is primarily scanned documents, evaluate carefully.

**Legacy Office formats (`.doc`, `.ppt`).** Parsing requires LibreOffice on the system PATH for format conversion. If LibreOffice is absent, these files are rejected with a clear error. `.docx`, `.pptx`, and `.xlsx` have no such dependency.

**Audio transcription.** Quality depends on the Whisper model size (`base` by default). Set `AKSHARAMD_WHISPER_MODEL=large-v3` for higher accuracy at the cost of speed. Requires ffmpeg.

**Large files.** Files above 500 MB are rejected by default. Raise the limit with `AKSHARAMD_MAX_FILE_BYTES` if needed.

**No incremental / streaming output.** The pipeline processes documents atomically. Very large PDFs (500+ pages) are processed in parallel pages but the final output is not streamed.

**Complex multi-row table headers.** Financial tables with merged cells or multi-row headers may produce column name artefacts (`Col1`, `Col2`). The table content is preserved; only the header row is affected.

---

## Partner Support

This repository is in private beta. Authorised partners should direct all questions, issues, and feedback to:

**Kalyan Kottapalli** — [kalyan.kottapalli@poppy.com](mailto:kalyan.kottapalli@poppy.com)

Please do not open public issues, create forks, or share access with parties outside your organisation.

---

## License

Copyright © 2026 Kalyan Kottapalli. All rights reserved.

This software is proprietary and confidential. Redistribution, modification, or use beyond the scope of the partner evaluation agreement is prohibited without prior written permission.
