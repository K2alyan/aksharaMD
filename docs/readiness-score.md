# AI Readiness Score

Every AksharaMD compilation returns a **0–100 AI Readiness Score** alongside the extracted content. The score measures how reliably the source document's content was extracted — not how well-written the document is, and not a prediction of downstream LLM accuracy. A clean text file should score 90+. A partially-scanned PDF typically scores 50–65.

---

## Quality Bands

| Band | Score range | Meaning |
|------|-------------|---------|
| **HIGH** | ≥ 85 | Extraction is reliable. Content is structurally complete and token-efficient. |
| **OK** | 70–84 | Extraction is mostly complete. Minor issues present (e.g. some missing structure, low heading density). |
| **RISKY** | 50–69 | Extraction is partial or degraded. The document may be scanned, have encoding issues, or contain significant boilerplate. |
| **POOR** | < 50 | Extraction failed or produced unusable content. Do not ingest without manual review or remediation. |

---

## Recommended Ingestion Policy

These are the defaults we recommend. Adjust thresholds to match your application's tolerance for imperfect extractions.

| Band | Default action | Rationale |
|------|---------------|-----------|
| **HIGH** | Auto-ingest | Extraction is reliable; no review needed. |
| **OK** | Ingest and flag | Acceptable quality; log the document for periodic audit. |
| **RISKY** | Require review or rerun with extras | Extraction is degraded; embeddings may be unreliable. Rerun with `[ocr]`, `[vision]`, or `[math]` extras if applicable, or route to a human reviewer. |
| **POOR** | Block ingestion | Do not embed. Surface the document for manual inspection. |

Python example:

```python
from aksharamd.compiler import Compiler

compiler = Compiler(output_dir="output")
text, ctx = compiler.compile_to_string("report.pdf")

band = ctx.manifest.quality_band   # "HIGH" | "OK" | "RISKY" | "POOR"
score = ctx.manifest.readiness_score

if band == "POOR":
    raise ValueError(f"Ingestion blocked: score {score}/100 ({band})")
elif band == "RISKY":
    print(f"WARNING: score {score}/100 ({band}) — routing for review")
    # log to review queue rather than embedding
else:
    embed(text)   # HIGH or OK: proceed
```

---

## How the Score Is Computed

The score starts from a format-quality baseline, then adjustments are applied based on extraction signals.

### Format baselines

| Format | Baseline | Notes |
|--------|----------|-------|
| Markdown, plain text, source code | 93–95 | Lossless formats; near-perfect extraction expected |
| CSV, YAML, TOML | 90–95 | Structured data; all content is text |
| PDF (text layer), HTML | 87 | Good but subject to layout and encoding issues |
| XLSX | 85 | Spreadsheet; empty cells and merged ranges may be lost |
| XML, JSON | 82–88 | Depends on schema complexity |
| DOCX | 83 | Generally reliable; tracked-changes and some embedded objects not extracted |
| EPUB, PPTX | 78–80 | Layout recovery is partial for complex templates |
| Email (EML, MSG) | 78 | Body text reliable; calendar objects and S/MIME may not parse |
| RTF | 63 | Lossy conversion; images and complex tables not preserved |
| Legacy Office (DOC, PPT) | 62–65 | Requires LibreOffice; conversion quality varies |
| Archives (ZIP, TAR) | 65–68 | Text files extracted; binary files listed but not decoded |
| Images, audio | 68–75 | Requires OCR or Whisper extras for content extraction |

Formats not in the table default to 72.

### Score adjustments

Penalties are applied for extraction problems detected during parsing and validation:

| Signal | Penalty | Condition |
|--------|---------|-----------|
| Parse errors | up to −30 | Each error costs −12, capped at −30 |
| Missing pages | up to −38 | Each missing page costs −4; −8 additional if ≥50% of pages are image-only |
| `OCR_REQUIRED` | up to −40 | Scales with fraction of image-only pages; suppresses `NEAR_EMPTY_OUTPUT` and `LOW_TEXT_DENSITY` to avoid double-counting |
| `NEAR_EMPTY_OUTPUT` | −25 | Document produced near-zero content relative to page count |
| `LOW_TEXT_DENSITY` | −20 | Extracted text is sparse relative to page count |
| `GLYPH_ARTIFACTS` | −25 | CID font artifacts detected; extracted text is likely garbled |
| `REPEATED_CONTENT` | −8 | Headers, footers, or boilerplate not fully removed |
| `TOKEN_BLOAT` | −8 | Unusually high tokens per page; possible duplicate extraction |
| `LARGE_BLOCK` | up to −10 | Unusually large blocks suggest layout merge failure |
| No headings (multi-page) | −6 | Document structure may be flat or not preserved |
| Auto-generated table columns | up to −5 | Tables may be visual/scanned with no header row |

The final score is clamped to [0, 100].

---

## Warning Codes

Warning codes appear in `validation.json` and in the CLI output when the score drops below HIGH. Each code has a recommended action.

### `OCR_REQUIRED`

The document is a scanned PDF or image-based page, and OCR is not installed.

**Action:** Install the OCR extra and rerun.
```bash
pip install "aksharamd[ocr]"
# Also install Tesseract 5+ at the OS level: https://github.com/tesseract-ocr/tesseract
```
For image tables, use `[vision]` instead (Marker neural layout model).

### `NEAR_EMPTY_OUTPUT`

The compiled output is nearly empty relative to the document's page count.

**Action:** Check whether the document is encrypted, image-only, or in an unsupported encoding. If scanned, install `[ocr]`.

### `LOW_TEXT_DENSITY`

Extracted text is sparse (fewer characters per page than expected).

**Action:** Same as `NEAR_EMPTY_OUTPUT`. If OCR is installed but not helping, the scans may be low-resolution — try `AKSHARAMD_OCR_DPI=300`.

### `GLYPH_ARTIFACTS`

The PDF uses non-embedded fonts. Extracted text contains CID placeholder glyphs instead of readable characters.

**Action:** The source PDF is the problem. If you have access to the original, re-export from the source application with fonts embedded. If not, the `[vision]` extra (Marker) may extract readable text from the page images.

### `REPEATED_CONTENT`

Headers, footers, or running heads appear on multiple pages and were not fully deduplicated.

**Action:** Review the output for repetitive boilerplate. The token optimizer removes common repeating lines, but some patterns may not be caught. Manual post-processing may be needed for documents with aggressive headers.

### `TOKEN_BLOAT`

The document produces far more tokens per page than typical for its format.

**Action:** The source document may have unusual formatting, large embedded tables, or failed deduplication. Inspect `document.json` for unusually large blocks.

### `ENCRYPTED_PDF`

The PDF is password-protected and could not be decrypted.

**Action:** Decrypt the PDF before compilation. `aksharamd compile` cannot handle encrypted PDFs.

### `MISSING_PAGE`

One or more pages produced no extractable content.

**Action:** If the document is a hybrid PDF (some text pages, some scanned pages), install `[ocr]` or `[vision]` to handle the image pages. The warning message includes the count of affected pages.

### `W_PARSE_FALLBACK`

**Maturity:** candidate  |  **Current penalty:** 0 (informational; scoring effect deferred to a future release — see GitHub issue `#41-B`)

Emitted when a format-specific parser attempted a strict parse, failed, and the compiler preserved the input as raw text so the recoverable content isn't lost.

Currently fires for:

- `.json` — when `json.loads()` raises `JSONDecodeError` on the whole file. The document is emitted as a single fenced `json` code block containing the raw text.
- `.jsonl` / `.ndjson` — when **every** non-empty record fails strict parse. The document is emitted as one paragraph per non-empty line. Partial failures (some records parse, some do not) are covered by a future `W_PARSE_PARTIAL` signal and are intentionally NOT flagged here.

Metadata (attached to the `ValidationIssue`):

```json
{
  "parser":            "json_parser",       // or "jsonl_parser"
  "source_format":     "json",              // or "jsonl" / "ndjson"
  "exception_class":   "JSONDecodeError",
  "error_location":    "line 1 col 12",     // for JSON; "file line N" for JSONL
  "record_total":      3,                    // JSONL only
  "failed_record_count": 3,                  // JSONL only
  "warning_maturity":  "candidate"
}
```

The metadata deliberately excludes raw file contents, the failing snippet, and exception message strings. A regression test in `tests/test_parsers/test_parse_fallback.py` locks that in.

**Action:** If you rely on `--min-readiness-score` as an ingestion gate, treat `W_PARSE_FALLBACK` as an early signal that a document routed to a structured parser will only be indexed as raw text. In Phase 1 the readiness score is unchanged, so gates continue to behave as before; when the scoring effect ships in a future release the band for these documents will drop from HIGH to OK.

---

## Reading Quality Notes

The CLI displays quality notes in the "Extraction Quality" panel when the score is below HIGH. The Python API surfaces them via `ctx.manifest.confidence_notes`. Notes are plain English and actionable — they describe exactly what was detected and what to do.

```python
for note in ctx.manifest.confidence_notes:
    print(note)
# Example output:
# Extracted: 42 paragraph(s), 8 heading(s) (H1, H2, H3), 3 table(s).
# PDF classified as: hybrid PDF (mixed text and image pages) (4 image-only page(s)).
# 4 of 18 pages have no extractable text (22%) — OCR was applied where possible; verify output accuracy.
```

---

## False Positives and Known Limitations

- **Dense code documentation** (e.g. API reference PDFs with many pages of short function signatures) may score lower than the extraction quality warrants, because the low character-per-page ratio triggers `LOW_TEXT_DENSITY`.
- **Slide decks** with heavy use of graphics and minimal text will score in the RISKY range even when the text that exists is extracted correctly. The score reflects missing content, not extraction error.
- **Audio transcriptions** via Whisper start from a baseline of 72 and score based on token density. Whisper accuracy (typically 65–80% for clear speech) is not directly measured by the score.
- **RTF** always starts at 63 because the `striprtf` library is inherently lossy. Even a perfect RTF conversion will not exceed OK.
