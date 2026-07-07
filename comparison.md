# Choosing a Document-to-Markdown Tool

This guide compares three tools that convert documents to text or Markdown for downstream use:
[MarkItDown](https://github.com/microsoft/markitdown),
[Docling](https://github.com/DS4SD/docling), and
[AksharaMD](https://github.com/K2alyan/aksharaMD).

They have different design goals. Choosing the wrong one for your workflow is more likely to cost
you time than any technical limitation within a tool. Read the "When to choose" rows before
benchmarking.

---

## Quick reference

| | MarkItDown | Docling | AksharaMD |
|---|---|---|---|
| **Primary goal** | General-purpose document-to-Markdown conversion | Layout-aware document understanding | RAG ingestion preflight with quality gate |
| **Extraction quality signal** | None | None | AI Readiness Score 0–100, named warnings |
| **Layout fidelity** | Basic | High (tables, figures, reading order) | Semantic content; not a visual replica |
| **Format breadth** | Broad | PDF and Office formats | 40+ document categories, 118 extensions |
| **Base install speed** | Fast (no ML) | Slow (ML per page) | Fast (no ML in base install) |
| **Optional ML extras** | No | Yes (always on) | Yes (surgical: OCR, vision, math, audio) |
| **Output format** | Markdown | DoclingDocument (Markdown, JSON, HTML) | Markdown + schema-versioned JSON + chunks |
| **Ingestion gating** | No | No | Yes (`--min-readiness-score`) |
| **Visual inspection** | No | Docling Studio | No |
| **Fully local** | Yes | Yes | Yes |
| **Maintainer** | Microsoft | IBM / DS4SD | Independent |

---

## MarkItDown

MarkItDown converts a wide range of file types to Markdown with minimal configuration.
It is lightweight, dependency-light, and easy to integrate into scripts and automation.

**Strengths**
- Simple API and CLI — minimal setup
- Broad format support without heavy dependencies
- Well-maintained by Microsoft, with active community

**Limitations**
- No quality signal: there is no way to know whether the output is reliable before embedding it
- Output can be verbose — headers, footers, watermarks, and repeated boilerplate pass through unchanged
- No structured chunking metadata for RAG pipelines

**When to choose MarkItDown**
- You need a quick, dependency-light Markdown conversion for scripting or prototyping
- Extraction quality is not a concern, or you plan to quality-filter the output yourself
- You do not need per-document reliability metadata

---

## Docling

Docling is a document AI library built for high-fidelity understanding of complex documents.
It reconstructs layout, reading order, tables, and figures from PDFs and Office formats,
producing a rich `DoclingDocument` object that can be exported to multiple formats.
Docling Studio provides a visual workbench for inspecting and validating extractions.

**Strengths**
- Best-in-class layout-aware parsing for complex PDFs: tables, multi-column text, figures
- `DoclingDocument` format captures structure that flat text loses
- Docling Studio enables visual inspection and debugging of extraction results
- Active research and enterprise backing

**Limitations**
- ML runs on every page by default — throughput is significantly lower than lexical tools
- Primarily focused on PDFs and Office formats; not designed for audio, archives, or code
- No built-in ingestion quality gate: extraction quality must be evaluated externally

**When to choose Docling**
- You are parsing complex PDFs where layout, table structure, or reading order matters for correctness
- You need visual inspection of extraction results via Docling Studio
- Throughput is less important than structural fidelity
- You are building a document AI pipeline and need a rich intermediate representation

---

## AksharaMD

AksharaMD is designed for production RAG ingestion: it tells you whether a document is ready
to be embedded before it reaches your vector store. Every compilation returns an
[AI Readiness Score](readiness-score.md) (0–100), named warning codes, and schema-versioned
chunks — so you can gate ingestion, route documents for review, or alert on extraction problems
automatically.

**Strengths**
- Readiness Score and named warnings (`OCR_REQUIRED`, `LOW_TEXT_DENSITY`, `GLYPH_ARTIFACTS`, …)
  surface extraction problems before they reach your embedder
- `--min-readiness-score` gates ingestion at the CLI level — no custom filtering code required
- Schema-versioned JSON output with per-block confidence tags (`EXTRACTED`, `INFERRED`, `AMBIGUOUS`)
- Broad format support in the base install (zero ML); ML extras add capability surgically for
  scanned PDFs, math, and audio
- Fast bulk processing: base install runs at the same speed as MarkItDown on clean documents

**Limitations**
- Not a layout-reproduction tool: output prioritizes semantic content over visual fidelity
- No visual inspection workbench (Docling Studio fills this role if you need it)
- Scanned PDFs require the `[ocr]` or `[vision]` extra; base install flags them and scores them RISKY or POOR

**When to choose AksharaMD**
- You are building or operating a production RAG ingestion pipeline and need to know whether
  each document is trustworthy before it is embedded
- You want automatic ingestion gating based on extraction quality
- You are processing a mixed corpus of formats and want a single consistent quality signal
- Throughput matters: you need to process large document volumes without per-page ML overhead
  on clean documents

---

## Using tools together

These tools are not mutually exclusive. A practical pattern:

1. **AksharaMD** screens the full corpus — documents with a LOW score or `OCR_REQUIRED` warning
   are routed to a review queue or a heavier pipeline.
2. **Docling** handles the routed subset where layout fidelity matters and throughput is acceptable.
3. **MarkItDown** handles simple conversions in scripting contexts where quality signals are not needed.

---

## Benchmark note

The AksharaMD README reports token count and speed comparisons against MarkItDown and Docling.
Those numbers are specific to the benchmark corpus used (101 documents, 23 format types for the
MarkItDown comparison; 20 arXiv/technical-report PDFs for the Docling comparison). Results
depend on corpus composition. See the [benchmark methodology](benchmarks/LLM_QA_BENCHMARK.md)
for corpus details, reproducibility notes, and limitations before drawing general conclusions.
