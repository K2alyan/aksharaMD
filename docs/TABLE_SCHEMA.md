# Table Schema — Phase 4 Milestone 2

## What is `table_data`?

`block.table_data` is a `TableData` object that captures the full structured
representation of a table: row and column counts, per-cell text, spans,
header detection provenance, bounding boxes, and extraction metadata.

As of Milestone 2, `table_data` is populated by the following parsers:

| Parser    | Extraction method        |
| --------- | ------------------------ |
| CSV       | `csv.native`             |
| TSV       | `tsv.native`             |
| XLSX/XLSM | `xlsx.native`            |
| XLS       | `xls.native`             |
| DOCX      | `docx.native`            |
| HTML/HTM  | `html.native`            |

PDF structured tables are planned for Milestone 4.

## `block.content` remains available

`block.content` continues to hold a GFM pipe-table string and is always
derived deterministically from `table_data` by the `render_table_markdown`
renderer. Consumers that only read `content` continue to work without changes.

**Critical invariant:** `block.content` is ALWAYS derived from `table_data`.
It is never set independently when `table_data` is present. The `Block`
model validator enforces this on every construction.

## Schema version change: 1.1 → 1.2

All artifact schema versions (`Document`, `Chunk`, `Manifest`) have been
bumped from `"1.1"` to `"1.2"` to reflect the addition of the `table_data`
field on `Block`.

Old readers that do not know about `table_data` can safely ignore it and
continue using `block.content`.

## Block checksum

For structured table blocks (`table_data` is not None):

    checksum = SHA256(canonical_payload_json)[:16]

The canonical payload includes row/column counts, header_rows, and per-cell
text/span/formula information. It deliberately excludes provenance fields
(bbox, confidence, extraction_method, span_detection) so that the same
semantic table produces the same checksum regardless of how it was extracted.

For legacy table blocks (`table_data=None`) and all non-table blocks the
existing `SHA256(normalized_content)[:16]` formula is unchanged.

## ExtractionMethod namespacing

Extraction methods follow a `<format>.<variant>` convention. The format
prefix matches the parser's file type; the variant describes the extraction
strategy within that format:

    xlsx.native   — openpyxl cell API
    xls.native    — xlrd cell API
    csv.native    — Python csv module
    tsv.native    — Python csv module (tab delimiter)
    docx.native   — python-docx table/cell API
    html.native   — BeautifulSoup th/td traversal
    pdf.ruled     — (future) ruled-line detection
    pdf.booktabs  — (future) booktabs-style detection
    pdf.whitespace — (future) whitespace column splitting
    pdf.stitched  — (future) page-break table stitching

## Formula handling (XLSX only)

For XLSX files below the large-file threshold (10 MB), the parser performs
a dual load:

1. `data_only=True` — retrieves cached display values.
2. `data_only=False` — retrieves formula strings.

`TableCell.formula` is populated from the `data_only=False` load when the
cell's `data_type` is `'f'`. `TableCell.text` always holds the display value
(cached result if available, formula string as fallback).

For files above the threshold (`read_only=True` mode) and for XLS files,
formulas are not extracted and `span_detection` is `"unsupported"`.

## Known limitations

- **Merged cells in XLSX read-only mode**: when a file exceeds the large-file
  threshold, openpyxl is opened in `read_only=True` mode. Merged cell ranges
  are not available in this mode; spans are not detected.
- **PDF tables**: structured table extraction for PDF is not yet implemented.
  PDF table blocks continue to use `table_data=None` with content-based checksums.
- **PPTX, ODF**: these parsers have not yet been migrated to the structured model.
