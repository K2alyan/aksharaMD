# AksharaMD

**AI Document Compiler** — convert any document into token-efficient, semantically complete Markdown optimised for language model consumption.

> **Status:** Private beta. This repository is proprietary and not open source.

---

## Overview

AksharaMD is a high-performance document processing pipeline that ingests 35+ file formats and produces clean, structured Markdown output. It is designed from the ground up for LLM input workflows, prioritising token efficiency and extraction fidelity over raw throughput.

The pipeline runs 10 stages per document:

```
detect → parse → clean → optimise → validate → chunk → tokenise → manifest → score → export
```

Each stage is independently configurable. The result is Markdown that is typically 20–80% smaller than the raw source while preserving all semantically meaningful content.

---

## Benchmarks

Evaluated on 347 documents across 18 formats against MarkItDown and Docling (June 2026).

### PDF — 20 documents (arXiv papers, technical reports)

| Metric          | AksharaMD | MarkItDown | Docling  |
|-----------------|-----------|------------|----------|
| Avg tokens      | 12,608    | 24,506     | 15,049   |
| Quality score   | **94.1**  | 92.8       | 93.0     |
| Avg time        | **1.09s** | 1.64s      | 29.96s   |

AksharaMD is **27× faster** than Docling on PDF with higher quality and 49% fewer tokens than MarkItDown.

### All formats — 347 documents across 18 types

| Metric               | AksharaMD | MarkItDown | Docling  |
|----------------------|-----------|------------|----------|
| Avg quality score    | **95.2**  | 84.1       | 93.7     |
| Avg time per file    | **0.14s** | 0.06s      | 1.86s    |
| Formats supported    | **17**    | 12         | 4        |

Selected per-format quality scores (0–100, higher is better):

| Format | AksharaMD | MarkItDown | Docling |
|--------|-----------|------------|---------|
| HTML   | **98.2**  | 93.4       | 95.2    |
| JSON   | **98.8**  | 43.5       | —       |
| ATOM   | **95.1**  | 93.6       | —       |
| CSV    | **93.8**  | 80.0       | —       |
| XLSX   | 80.0      | 80.0       | —       |
| PPTX   | 72.5      | 81.0       | 85.0    |

Exclusive format support (MarkItDown and Docling do not handle these):  
ZIP archives, JSONL, XML, RSS/ATOM feeds, EML email, RTF, legacy Office via LibreOffice.

---

## Supported Formats

| Category         | Extensions                                              |
|------------------|---------------------------------------------------------|
| Text / Markup    | `.md` `.txt` `.rst` `.tex` `.html` `.htm`              |
| Documents        | `.pdf` `.docx` `.pptx` `.xlsx` `.odt` `.ods` `.odp` `.epub` `.rtf` |
| Legacy Office    | `.doc` `.ppt` `.xls` (requires LibreOffice on PATH)    |
| Data             | `.json` `.jsonl` `.csv` `.tsv` `.xml` `.yaml` `.toml`  |
| Email            | `.eml` `.msg`                                           |
| Notebooks        | `.ipynb`                                                |
| Source code      | `.py` `.js` `.ts` `.go` `.rs` `.java` `.c` `.cpp` `.sql` `.sh` |
| Images (OCR)     | `.jpg` `.jpeg` `.png` `.tiff` `.bmp` `.webp` `.gif` (requires Tesseract) |
| Audio            | `.mp3` `.wav` `.m4a` `.ogg` `.flac` (requires Whisper + ffmpeg) |
| Archives         | `.zip` `.tar` `.tgz` `.gz` `.bz2` `.xz` `.7z`         |
| Feeds            | `.rss` `.atom`                                          |

---

## Installation

```bash
pip install -e .
```

For full format support, install optional dependencies:

```bash
pip install openai-whisper        # audio transcription (also needs ffmpeg on PATH)
pip install pytesseract           # image OCR (also needs Tesseract binary)
# LibreOffice on PATH             # .doc / .ppt legacy Office files
```

---

## CLI

```bash
# Compile a single file to stdout
aksharamd compile report.pdf

# Write to an output file
aksharamd compile report.pdf --output report.md

# Compile a directory recursively
aksharamd compile ./documents/ --output ./compiled/

# Show lifetime token savings
aksharamd stats

# List all supported formats
aksharamd formats
```

---

## Python API

```python
from aksharamd.compiler import Compiler

compiler = Compiler()
text, ctx = compiler.compile_to_string("report.pdf")

print(text)                              # compiled Markdown
print(ctx.manifest.optimized_tokens)    # token count after optimisation
print(ctx.manifest.token_reduction_percent)  # % reduction vs raw
print(ctx.manifest.readiness_score)     # extraction confidence 0–100
```

### Compilation context

The `ctx` object exposes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `manifest.source` | str | Source file path |
| `manifest.file_type` | str | Detected format |
| `manifest.pages` | int | Page / chunk count |
| `manifest.original_tokens` | int | Raw token estimate |
| `manifest.optimized_tokens` | int | Tokens after pipeline |
| `manifest.token_reduction_percent` | float | Reduction percentage |
| `manifest.readiness_score` | int | Confidence score 0–100 |
| `manifest.elapsed_seconds` | float | Wall-clock time |
| `validation.errors` | list | Any extraction errors |

---

## MCP Server

AksharaMD ships an [MCP](https://modelcontextprotocol.io) server that exposes the compilation pipeline as tools consumable by any MCP-compatible host (Claude Desktop, Cursor, etc.).

### Tools

| Tool | Description |
|------|-------------|
| `compile_document` | Compile any supported file into clean Markdown |
| `get_supported_formats` | List all supported formats and optional dependencies |
| `get_stats` | Lifetime token savings across all compilations |

### stdio (Claude Desktop / most hosts)

Add to your MCP config:

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

## Architecture

```
aksharamd/
├── compiler.py          # Orchestrates the 10-stage pipeline
├── context.py           # CompilationContext — shared state across stages
├── ledger.py            # Persistent savings ledger (~/.aksharamd/ledger.jsonl)
├── cli.py               # Click-based CLI
├── mcp_server.py        # FastMCP server (3 tools)
├── utils.py             # Token counting, pricing tables
├── models/              # Pydantic models (Manifest, Document, Block, Chunk)
├── plugins/
│   ├── parsers/         # Format-specific extractors (PDF, DOCX, HTML, …)
│   ├── cleaners/        # Deduplication, noise removal, whitespace normalisation
│   ├── optimisers/      # Token reduction passes
│   ├── chunkers/        # Semantic chunking strategies
│   ├── exporters/       # Markdown serialisation
│   └── validators/      # Schema and content validation
└── scoring/
    └── readiness.py     # Extraction confidence scoring (0–100)
```

---

## License

Copyright (c) 2026 Kalyan Kottapalli. All rights reserved.

This software is proprietary and confidential. See [LICENSE](LICENSE) for terms.
