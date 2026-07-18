# Output Schema

Every `aksharamd compile` run writes four files to the output directory:

```
output/<stem>/
├── document.md       # compiled Markdown (human- and LLM-readable)
├── document.json     # structured block model
├── manifest.json     # token counts, timings, readiness score
├── validation.json   # all validation issues and warning codes
└── chunks/
    └── <id>.json     # one file per semantic chunk
```

All JSON files include `schema_version: "1.0"` and are encoded in UTF-8.

---

## Schema Version

All four JSON output types carry a `schema_version` field:

```json
{ "schema_version": "1.0" }
```

**Compatibility guarantee for `1.0`:**
- Fields will not be removed or renamed without a schema version bump.
- New optional fields may be added at any time — parsers should tolerate unknown keys.
- Breaking changes (field removal, type change, semantic change) will increment the schema version.
- The `schema_version` field itself will not be removed.

If you build downstream tooling against AksharaMD JSON output, check `schema_version` on startup and raise an error if you encounter a version you have not tested against.

---

## `manifest.json`

High-level compilation metadata. Safe to load without the full document model.

```json
{
  "schema_version": "1.0",
  "source": "report.pdf",
  "file_type": "pdf",
  "pages": 18,
  "chunks": 12,
  "images": 3,
  "tables": 5,
  "original_tokens": 42300,
  "optimized_tokens": 8100,
  "token_reduction_percent": 80.8,
  "duplicate_blocks_removed": 14,
  "headers_removed": 36,
  "footers_removed": 36,
  "readiness_score": 83,
  "quality_band": "OK",
  "pdf_classification": "hybrid",
  "ocr_available": true,
  "image_pages": 4,
  "vision_available": false,
  "vision_pages": 0,
  "confidence_notes": [
    "Extracted: 42 paragraph(s), 8 heading(s) (H1, H2, H3), 3 table(s).",
    "PDF classified as: hybrid PDF (mixed text and image pages) (4 image-only page(s))."
  ],
  "elapsed_seconds": 1.84,
  "stage_timings": {
    "detect": 0.002,
    "parse": 1.201,
    "clean": 0.089,
    "optimize": 0.043,
    "validate": 0.021,
    "chunk": 0.118,
    "tokenize": 0.067,
    "score": 0.009,
    "export": 0.290
  },
  "ai_plugins_used": [],
  "warnings": ["4 image-only pages were not fully extracted — OCR applied"],
  "warning_codes": ["MISSING_PAGE"],
  "errors": [],
  "compiled_at": "2026-07-06T14:22:31.004Z",
  "blocks_extracted": 58,
  "blocks_inferred": 6,
  "blocks_ambiguous": 2,
  "aksharamd_version": "0.3.3"
}
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Schema version — currently `"1.0"` |
| `source` | string | Path or URI of the source document |
| `file_type` | string | Extension without leading dot (`"pdf"`, `"docx"`, etc.) |
| `pages` | int | Number of pages or logical sections |
| `chunks` | int | Number of semantic chunks produced |
| `images` | int | Number of image references in the document |
| `tables` | int | Number of extracted tables |
| `original_tokens` | int | Token count of raw extracted text before optimization |
| `optimized_tokens` | int | Token count after deduplication and cleanup |
| `token_reduction_percent` | float | `(original - optimized) / original × 100` |
| `duplicate_blocks_removed` | int | Blocks removed as exact or near-duplicates |
| `headers_removed` | int | Running header occurrences removed |
| `footers_removed` | int | Running footer occurrences removed |
| `readiness_score` | int | 0–100 AI Readiness Score |
| `quality_band` | string | `"HIGH"` / `"OK"` / `"RISKY"` / `"POOR"` |
| `pdf_classification` | string | PDF subtype: `"native_text"`, `"scanned"`, `"hybrid"`, `"table_heavy"`, `"layout_heavy"`, `"low_confidence"` (PDF only) |
| `ocr_available` | bool \| null | Whether Tesseract OCR was available during this compilation |
| `image_pages` | int | Number of image-only pages (PDF only) |
| `vision_available` | bool \| null | Whether the Marker vision model was available |
| `vision_pages` | int | Number of pages re-extracted via Marker |
| `confidence_notes` | string[] | Human-readable extraction quality notes |
| `elapsed_seconds` | float | Wall-clock time for the full compilation |
| `stage_timings` | object | Per-stage breakdown in seconds |
| `ai_plugins_used` | string[] | Names of ML plugins that ran (e.g. `"marker"`, `"whisper"`, `"pix2tex"`) |
| `warnings` | string[] | Human-readable warning messages |
| `warning_codes` | string[] | Machine-readable warning codes (see [readiness-score.md](readiness-score.md)) |
| `errors` | string[] | Fatal errors (non-empty means compilation was degraded or failed) |
| `compiled_at` | string | ISO 8601 UTC timestamp |
| `blocks_extracted` | int | Blocks tagged `EXTRACTED` (clean native-format parse) |
| `blocks_inferred` | int | Blocks tagged `INFERRED` (derived with some uncertainty) |
| `blocks_ambiguous` | int | Blocks tagged `AMBIGUOUS` (low-fidelity path: OCR, binary fallback) |
| `aksharamd_version` | string | Package version that produced this output |

---

## `document.json`

The full structured block model. Use this when you need per-block metadata, page numbers, or extraction confidence.

```json
{
  "schema_version": "1.0",
  "id": "a3f8c1e2d4b50617",
  "source": "report.pdf",
  "file_type": "pdf",
  "title": "Q3 Market Analysis",
  "author": "Acme Corp",
  "created": "2026-01-15T00:00:00Z",
  "pages": 18,
  "compiled_at": "2026-07-06T14:22:31.004Z",
  "metadata": {},
  "blocks": [
    {
      "id": "b1c2d3e4f5a60718",
      "type": "heading",
      "content": "Executive Summary",
      "level": 1,
      "language": null,
      "page": 1,
      "index": 0,
      "confidence": "extracted",
      "checksum": "8a3f2e1c9b4d7e60",
      "metadata": {}
    },
    {
      "id": "c2d3e4f5a6b70819",
      "type": "paragraph",
      "content": "Revenue grew 23% year-over-year ...",
      "level": null,
      "language": null,
      "page": 1,
      "index": 1,
      "confidence": "extracted",
      "checksum": "1b2c3d4e5f6a7b8c",
      "metadata": {}
    }
  ],
  "assets": []
}
```

### Block types

| `type` | Description |
|--------|-------------|
| `heading` | Section heading. `level` is 1–6 (H1–H6). |
| `paragraph` | Body text. |
| `table` | Markdown-formatted table. |
| `code_block` | Code or preformatted text. `language` is the identifier (e.g. `"python"`, `"sql"`). |
| `image` | Image reference. `content` is a description or alt text; actual bytes are in `assets`. |
| `list` | Bulleted or numbered list in Markdown format. |
| `blockquote` | Quoted text. |
| `math` | LaTeX math expression (requires `[math]` extra for PDF extraction). |
| `metadata` | Document-level metadata (title, author, dates). |
| `caption` | Figure or table caption. |
| `footnote` | Footnote or endnote. |
| `admonition` | Note, warning, or tip callout. |
| `page_break` | Explicit page boundary marker. |
| `unknown` | Block that could not be classified. |

### Extraction confidence

| `confidence` | Meaning |
|-------------|---------|
| `extracted` | Cleanly parsed from native structure (text layer, DOM, schema). |
| `inferred` | Derived with moderate uncertainty (whitespace-detected tables, font-size-inferred headings). |
| `ambiguous` | Low-fidelity path (OCR, olefile stream, binary fallback). Verify before relying on this content. |

---

## `validation.json`

All validation issues found during compilation, including both errors and warnings.

```json
{
  "schema_version": "1.0",
  "passed": true,
  "issues": [
    {
      "severity": "warning",
      "code": "MISSING_PAGE",
      "message": "Page 7 produced no extractable text — may be image-only",
      "page": 7,
      "block_id": null,
      "source": null
    }
  ]
}
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Schema version — currently `"1.0"` |
| `passed` | bool | `true` if no errors (warnings do not fail validation) |
| `issues` | array | All issues, ordered by severity then page |

### Issue fields

| Field | Type | Description |
|-------|------|-------------|
| `severity` | string | `"error"` / `"warning"` / `"info"` |
| `code` | string | Machine-readable code (e.g. `"OCR_REQUIRED"`, `"GLYPH_ARTIFACTS"`) |
| `message` | string | Human-readable description |
| `page` | int \| null | Source page number (1-indexed) if applicable |
| `block_id` | string \| null | Block ID if the issue is block-level |
| `source` | string \| null | Additional source context |
| `metadata` | object | Structured, code-specific data. Contents depend on the warning code. Never contains raw file contents or exception message strings — see `docs/readiness-score.md` for per-code metadata schemas. |

See [readiness-score.md](readiness-score.md) for a complete list of warning codes and their recommended actions.

**Metadata privacy invariant.** Warnings that describe extraction fallbacks or omissions (currently `W_PARSE_FALLBACK`, `W_PDF_ATTACHMENT_IGNORED`) carry structured metadata about *what* happened — parser name, source format, exception class, safe line/column location, or omission counts — and deliberately exclude the raw text of the malformed input, the failing snippet, the exception message string, and any attachment filenames or bytes, because those can carry source content that a caller may consider sensitive. Regression tests in `tests/test_parsers/test_parse_fallback.py` and `tests/test_plugins/test_pdf_attachment_warning.py` lock the invariant in.

### PDF-specific document metadata

`Document.metadata` may contain the following PDF-specific diagnostic fields:

| Key | Type | Description |
|-----|------|-------------|
| `pdf_classification` | string | See `manifest.pdf_classification`. |
| `pdf_stats` | object | Per-page counts (`image_pages`, `table_pages`, …). |
| `pdf_ocr_available` | bool | Whether an OCR backend was reachable during this run. |
| `pdf_vision_available` | bool | Whether the Marker vision extra was reachable. |
| `pdf_column_info` | object | Per-page column geometry (used by the multicolumn validator). |
| `pdf_multi_column_pages` | int[] | Pages the detector classified as multi-column. |
| `pdf_attachment_diagnostics` | object | `{attachment_count, backend, warning_maturity}` — recorded on every PDF parse so consumers can distinguish "no attachments" from "detector did not run". See `W_PDF_ATTACHMENT_IGNORED` in `readiness-score.md`. |

---

## `chunks/<id>.json`

One file per semantic chunk. Chunks are non-overlapping segments of the document, sized for embedding model context windows.

```json
{
  "schema_version": "1.0",
  "id": "d4e5f6a7b8c90a1b",
  "index": 0,
  "heading": "Executive Summary",
  "content": "# Executive Summary\n\nRevenue grew 23% year-over-year ...",
  "token_count": 412,
  "block_ids": ["b1c2d3e4f5a60718", "c2d3e4f5a6b70819"],
  "page_start": 1,
  "page_end": 2,
  "metadata": {}
}
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Schema version — currently `"1.0"` |
| `id` | string | Content-addressed ID (SHA-256 of index + content hash) |
| `index` | int | Zero-based position in the chunk sequence |
| `heading` | string \| null | Nearest heading above this chunk (useful as metadata in vector stores) |
| `content` | string | Markdown-formatted chunk text, ready to embed |
| `token_count` | int | tiktoken token count (cl100k_base) |
| `block_ids` | string[] | IDs of blocks from `document.json` that this chunk contains |
| `page_start` | int \| null | First source page covered by this chunk |
| `page_end` | int \| null | Last source page covered by this chunk |
| `metadata` | object | Reserved for future per-chunk metadata |

### Using chunks with a vector store

```python
import json
from pathlib import Path

chunks_dir = Path("output/report/chunks")
for chunk_file in chunks_dir.glob("*.json"):
    chunk = json.loads(chunk_file.read_text())
    # Recommended metadata to store alongside the embedding:
    metadata = {
        "source": "report.pdf",
        "chunk_index": chunk["index"],
        "heading": chunk["heading"],
        "page_start": chunk["page_start"],
        "page_end": chunk["page_end"],
        "token_count": chunk["token_count"],
    }
    embed(chunk["content"], metadata=metadata)
```

---

## Programmatic access

All models are importable for programmatic use:

```python
from aksharamd.models import Manifest, Document, Chunk, ValidationReport
import json

manifest = Manifest.model_validate_json(Path("output/report/manifest.json").read_text())
print(manifest.readiness_score)     # int
print(manifest.quality_band)        # "HIGH" | "OK" | "RISKY" | "POOR"
print(manifest.schema_version)      # "1.0"

document = Document.model_validate_json(Path("output/report/document.json").read_text())
for block in document.blocks:
    print(block.type, block.confidence, block.content[:80])
```
