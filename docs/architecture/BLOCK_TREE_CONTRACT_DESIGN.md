# Block-Tree Contract — Design

**Status:** design, not implementation. Zero code changes ship with this document.
**As of:** main @ `63c1e08` (post PRs #118, #119, #120 — bbox groundwork + verdict corrections + Section 8 renumbering).
**Depends on:** `docs/architecture/SCORER_COUPLING_INVENTORY.md` (particularly Section H — Verification pass).

## Purpose

Define the parser-neutral surface that a scoring-ready `Document` must expose so that `compute_confidence` (`aksharamd/scoring/readiness.py:64`) and every validator can run **byte-identically** against today's PDF-produced blocks *and* against a hypothetical non-PDF adapter. The contract closes the three coupled boundaries surfaced by the inventory's verification pass:

1. The **OCR / image-only cluster** (7 of 12 COUPLED signals, biggest by weight).
2. The **table-expectation boundary** (rejected-candidate accumulator + document type hint).
3. The **stitching boundary** (per-table extraction-method gate + stitching metadata).

The design is the specification of a refactor, not a rewrite: `SCORING_POLICY` (`aksharamd/scoring/models.py:88-225`) does not change, `compute_confidence`'s arithmetic does not change, the maturity-aware cap pattern does not change, and none of the 20 already-AGNOSTIC signals change. Only the reads at coupling boundaries move behind a named contract.

---

## 1. `BlockTreeContract` — the neutral scoring input

The contract is what a `Document` object must expose for a scoring run. It is expressed in terms of the existing neutral models (`aksharamd/models/block.py`, `aksharamd/models/document.py`, `aksharamd/models/table.py`) plus a small number of additions.

### 1.1 Fields already present on `Document` (unchanged, listed for completeness)

| Field | Source | Used by |
|---|---|---|
| `file_type: str` | Set by parser; already neutral | Signals #21 (`FORMAT_BASELINE`), #36 (`NO_TEXT_IN_IMAGE`), and all `_ELIGIBLE_FILE_TYPES` gates in validators |
| `pages: int` | Set by parser; already neutral | Signals #7, #8, #23, #27, #28, #34 (all page-count-driven ratios) |
| `blocks: list[Block]` | Set by parser; already neutral | Every content-inspection signal |
| `assets: list[Asset]` | Set by parser; already neutral | Signal #26 (`IMAGE_PLACEHOLDER_WITH_ASSETS`) presence check |
| `metadata: dict` | Set by parser; **partially coupled today** — this is the boundary the contract renames |

### 1.2 Fields already present on `Block` (unchanged)

| Field | Source | Used by |
|---|---|---|
| `type: BlockType` | Set by parser | Every type-gated signal (heading, table, image, paragraph counts and iteration) |
| `content: str` | Set by parser | Every content-inspection signal — table_missing, encoding_artifacts, repeated_content, glyph_artifacts, col_generic_tables, image_placeholder sentinel |
| `level: int \| None` | Set by parser (for HEADING) | Signals #3, #4 (`HEADING_HIERARCHY`, `HEADING_SKIP`) |
| `page: int \| None` | Set by parser | Signal #7 (`MISSING_PAGE`) via `{B.page for B in blocks}`; grouping in multicolumn/header_footer_table validators |
| `id: str` | Set by parser | Report attachment (extracted_table_block_ids in table_expectation) |
| `metadata: dict[str, Any]` | Set by parser; **partially coupled today** — see 1.3 for the new required keys |
| `data: TableData \| None` | Set by parser for TABLE blocks | Signal group A.7 (`compute_table_quality`) |

### 1.3 New required keys on `Block.metadata` (for coupled signals)

The contract adds **three** required per-block metadata keys with defined semantics. Two already exist under different names in pdf.py's output; the third landed with PR #118.

| Contract key | Populated today by | Type | Semantics | Consumed by |
|---|---|---|---|---|
| `bbox: [x0, y0, x1, y1] \| None` | PyMuPDF text extraction (as `x0`/`y0` scalars in existing metadata dict) for TEXT blocks; PR #118 for IMAGE blocks; `table_bbox` for TABLE blocks | List of 4 floats or None | Per-block bounding box in the source page's coordinate system. `None` means the parser cannot provide placement info for this block. | Multicolumn validator (uses `x0`/`y0` for cluster analysis); header_footer_table validator (uses full box for margin checks); future region-level vision-routing detector (uses IMAGE bbox) |
| `is_placeholder: bool` | Today: **not present** — grepped for via `"[Image not extracted"` string sentinel at readiness.py:191-194 | Bool | True when the block is only a placeholder for content the parser could not extract. Replaces the current sentinel-in-content grep. | Signal #26 (`IMAGE_PLACEHOLDER_NO_FALLBACK` cap) |
| `table_bbox: [x0, y0, x1, y1] \| None` | Existing per-TABLE-block metadata (populated by `pdf_tables/`) | Same as `bbox`, but table-specific | Rectangle of the table region on the page. Some validators want just the table's box independently of block-level bbox. | Header_footer_table validator (validated against page margins at header_footer_table.py:148) |

**Transition path for `bbox`:** the contract exposes `bbox` as a canonical list; the PDF adapter fills it from today's `x0`/`y0` scalars (text blocks), PR #118 tuple (image blocks), and `table_bbox` list (table blocks). No re-parsing needed.

**Transition path for `is_placeholder`:** the pdf.py IMAGE-block emitter that today writes `content=f"[Image not extracted...]"` also sets `metadata["is_placeholder"] = True`. Readiness.py L192-195 changes from sentinel grep to `b.metadata.get("is_placeholder")`. The sentinel string can remain for human-facing output; only the machine read moves off it.

### 1.4 New per-doc `SourceProfile` (replaces the `pdf_*` metadata cluster)

The `SourceProfile` is a typed sub-dict on `Document.metadata` under the neutral key `"source_profile"` (or, if preferred, a first-class attribute on `Document` in a follow-up model change). It carries every non-block-tree fact the scorer reads. See Section 2 for the field-by-field mapping from today's `pdf_*` reads.

### 1.5 New per-doc `TableExpectationInput` (replaces `table_rejected_candidates_by_page`)

The table expectation boundary already has a neutral shape defined by `RejectedTableCandidate` (`aksharamd/scoring/table_expectation.py:40`). The contract formalizes this: the parser optionally populates `Document.metadata["rejected_table_candidates_by_page"]` as `dict[int, list[RejectedTableCandidate]]`. If absent, the table_expectation validator uses only its caption-and-numeric-alignment signals; the rejected-candidate signal simply does not fire. See Section 3.

### 1.6 Existing `TableData` (unchanged for structural/fragmentation signals)

`TableData` (`aksharamd/models/table.py:58`) is already the right neutral shape for the AGNOSTIC and BORDERLINE table-quality signals: `row_count`, `column_count`, `cells: list[TableCell]`, `header_rows`, `header_detection`, `bbox`, `extraction_method`, `metadata`. No change needed except the stitching boundary (Section 4).

---

## 2. `SourceProfile` — replacement for the OCR/image-only cluster

This is the largest boundary — 7 of 12 COUPLED signals plus 1 BORDERLINE-adjacent read through this cluster. It is the boundary whose neutrality proves the pattern for the whole refactor. Getting this right is the load-bearing decision of the contract.

### 2.1 Proposed shape

```python
class SourceProfile(BaseModel):
    """Neutral per-document source characterization.

    Every field is defined in terms of what the SCORER needs to know about the
    source document, independent of how the parser produced the output.
    Any parser that can populate these fields participates in the coupled
    signals; parsers that cannot (e.g. plain-text input, no vision-required
    concept) leave them at their neutral defaults and the signals stay silent.
    """
    # Text-layer coverage
    pages_with_text_layer: int           # 0..pages; today = pdf_stats.text_pages
    pages_without_text_layer: int        # today = pdf_stats.image_pages
    pages_total: int                     # today = pdf_stats.page_count; must equal Document.pages
    # Vision / OCR capability and outcomes
    ocr_capability: Literal["available", "unavailable", "not_applicable"]
    # ^ today: pdf_ocr_available: bool. "not_applicable" is for parsers that
    #   never need OCR (e.g. Markdown) so the OCR_ATTEMPTED_SPARSE guard can
    #   distinguish "parser could OCR but produced nothing" from "parser
    #   doesn't do OCR at all".
    hallucinated_pages: int              # today = 1 if pdf_ocr_hallucination else 0
    # Document-type hint (for the coupled signals that read pdf_classification)
    document_type_hint: Literal[
        "native_text", "scanned", "hybrid", "table_heavy",
        "layout_heavy", "low_confidence"
    ] | None                             # today = pdf_classification
    # ^ Optional. If None, signals that read it fall back to the same neutral
    #   branch they take when today's pdf_classification is empty string.
    # Table page count (kept explicit because it is NOT block-derivable — see 2.3)
    pages_containing_tables: int         # today = pdf_stats.table_pages
    # ^ Count of PAGES containing at least one extracted table. Not the count
    #   of table blocks. Kept as an explicit field because the semantics
    #   diverge from any block-tree derivation on multi-table pages and on
    #   stitched cross-page tables (see 2.3).
    # Per-page dimensions (for validators that need page_width/page_height)
    page_dimensions: dict[int, PageDim]  # today = pdf_column_info[pg]
    # ^ PageDim = {page_width: float, page_height: float}. Missing pages
    #   → validator behaves as today when pdf_column_info entry absent.
```

### 2.2 One-to-one mapping (the load-bearing proof)

Every current `pdf_*` read documented in `SCORER_COUPLING_INVENTORY.md` Section H.2 maps to a `SourceProfile` field. Nothing is dropped. Nothing merged that shouldn't be.

| Signal | File:line | Current read | `SourceProfile` replacement | Behavior preserved? |
|---|---|---|---|---|
| #13 `OCR_REQUIRED` emit | structure.py:182-190 | `doc.metadata["pdf_classification"]` | `source_profile.document_type_hint` | Yes — gate is `hint in ("scanned", "hybrid")` |
| #13 `OCR_REQUIRED` emit | structure.py:184 | `doc.metadata["pdf_ocr_available"]` | `source_profile.ocr_capability == "available"` | Yes — gate is `not available` |
| #13 `OCR_REQUIRED` emit | structure.py:186 | `doc.metadata["pdf_stats"]["image_pages"]` | `source_profile.pages_without_text_layer` | Yes — gate is `> 0` |
| #14 `OCR_HALLUCINATION` emit | structure.py:200 | `doc.metadata["pdf_ocr_hallucination"]` | `source_profile.hallucinated_pages > 0` | Yes — truthy check |
| #15 `W_IMAGE_ONLY_TEXT_BAR_FAIL` emit | structure.py:235-238 | `pdf_classification == "scanned"` | `source_profile.document_type_hint == "scanned"` | Yes |
| #15 same | structure.py:237 | `pdf_stats["text_pages"] == 0` | `source_profile.pages_with_text_layer == 0` | Yes |
| #15 same | structure.py:238 | `pdf_stats["page_count"] >= 1` | `source_profile.pages_total >= 1` | Yes |
| #15 diagnostics write | structure.py:240-248 | Writes `classification, image_pages, text_pages, page_count, ocr_available` | Same fields sourced from `source_profile` | Yes — cap consumer at readiness.py:703-706 reads the diagnostics dict, whose shape does not change |
| #16 `W_MULTICOLUMN_ORDER` | multicolumn.py:175 + 188 | `doc.metadata["pdf_column_info"][pg]["page_width"]` | `source_profile.page_dimensions[pg].page_width` | Yes |
| #17 `W_HEADER_FOOTER_TABLE_GARBLED` | header_footer_table.py:140 + 153 | `doc.metadata["pdf_column_info"][pg]["page_height"]` | `source_profile.page_dimensions[pg].page_height` | Yes |
| #26 `IMAGE_PLACEHOLDER_NO_FALLBACK` | readiness.py:191-194 | `"[Image not extracted" in b.content` | `b.metadata.get("is_placeholder", False)` | Yes — semantics identical, sentinel string can still exist in `content` |
| #27 `OCR_REQUIRED` deduction | readiness.py:244 | `doc.metadata["pdf_classification"]` | `source_profile.document_type_hint` | Yes — used only in the notes string, no branch |
| #27 same | readiness.py:245 | `doc.metadata["pdf_stats"]["image_pages"]` | `source_profile.pages_without_text_layer` | Yes — used for ratio + notes |
| #28 `OCR_ATTEMPTED_SPARSE` | readiness.py:272 | `doc.metadata["pdf_ocr_available"]` | `source_profile.ocr_capability == "available"` | Yes |
| #28 same | readiness.py:275 | `doc.metadata["pdf_stats"]["image_pages"]` | `source_profile.pages_without_text_layer` | Yes |
| PDF classification note | readiness.py:454-475 | `pdf_classification, pdf_stats.image_pages, pdf_stats.table_pages` | `source_profile.document_type_hint` + `pages_without_text_layer` + `pages_containing_tables` | Yes — all three are one-to-one field renames (see 2.3 for why `pages_containing_tables` cannot be block-derived) |
| Cap consumers | readiness.py:512-753 | Read `<name>_diagnostics` dicts from `doc.metadata` | Unchanged — diagnostics dicts are written by validators that read `source_profile`; the shape they produce doesn't change | Yes |

**Field-count check.** Today's coupled reads touch: `pdf_classification` (5 sites), `pdf_ocr_available` (3 sites), `pdf_ocr_hallucination` (1 site), `pdf_stats.image_pages` (5 sites), `pdf_stats.text_pages` (1 site), `pdf_stats.page_count` (1 site), `pdf_stats.table_pages` (1 site, notes-only), `pdf_column_info[pg].page_width` (1 site), `pdf_column_info[pg].page_height` (1 site). Nine distinct keys total. `SourceProfile` maps them one-to-one to **seven scalar fields plus a per-page dimensions map**:
1. `document_type_hint` ← `pdf_classification`
2. `ocr_capability` ← `pdf_ocr_available`
3. `hallucinated_pages` ← `pdf_ocr_hallucination`
4. `pages_without_text_layer` ← `pdf_stats.image_pages`
5. `pages_with_text_layer` ← `pdf_stats.text_pages`
6. `pages_total` ← `pdf_stats.page_count`
7. `pages_containing_tables` ← `pdf_stats.table_pages`
8. `page_dimensions[pg]` ← `pdf_column_info[pg].page_width/page_height`

The mapping is fully one-to-one. Nothing is dropped or derived.

### 2.3 Why `pages_containing_tables` is not block-derivable

An earlier draft of this design proposed dropping `pages_containing_tables` and deriving it as `len([b for b in blocks if b.type == TABLE])` at consumer sites. Verification (see `SCORER_COUPLING_INVENTORY.md` Section H) showed this is **lossy** and would break byte-identity.

**Semantic divergence:** `pdf_stats["table_pages"]` is computed by `_classify_pdf` at `aksharamd/plugins/parsers/pdf.py:1223-1234` as:
```python
for raw in raw_pages:
    ...
    if raw.tables:
        table_pages += 1
```
It is a **count of pages that contain at least one table**. Multi-table pages increment once. A stitched cross-page table appears in `raw.tables` on each source page, so it increments `table_pages` once per source page it touches.

The block-tree derivation `len([b for b in blocks if b.type == TABLE])` counts **table blocks**, not pages:
- **Page with N tables:** original counts 1; block-count is N.
- **Stitched N-page table:** original counts N (once per source page); block-count is 1 (a stitched table is a single `Block` with a single `page` attribution).
- Even the tighter derivation `len({b.page for b in blocks if b.type == TABLE and b.page is not None})` fails on stitched tables (single `Block.page` → set size 1, not N).

**Byte-identity impact:** the only consumer of `pdf_stats["table_pages"]` is `readiness.py:468` — `tp = stats.get("table_pages", 0)` — used at `readiness.py:473` in the notes string `f"{tp} table page(s)"`. That string participates in `ReadinessResult.notes`, which participates in the manifest output. Any doc with multi-table pages or stitched tables would produce a different note under block-derivation. Byte-identity would fail.

**Contract stance:** `SourceProfile.pages_containing_tables` is an explicit field.
- **PDF adapter (`PdfBlockTreeAdapter`) reads it from today's `pdf_stats["table_pages"]`.** One-to-one, no computation.
- **Non-PDF adapters** that have a page-and-table concept but no equivalent counter MAY fall back to `len({b.page for b in blocks if b.type == TABLE and b.page is not None})`. This is a **fidelity fallback**, not the definition — the contract comment must say so, and any adapter using the fallback must accept that stitched tables (if that adapter's format can stitch) will undercount.

### 2.4 What this does NOT change

- Diagnostics dicts (`image_only_text_bar_diagnostics`, `multicolumn_diagnostics`, etc.) remain the interface between validators and readiness caps. Their contents move from `pdf_*`-sourced to `source_profile`-sourced, but their **shape** is preserved.
- The maturity-aware cap pattern (`warning_maturity` on each diagnostics dict) does not change.
- `SCORING_POLICY_VERSION` does not bump — the change is a refactor, not a policy change.

---

## 3. Table-expectation boundary

Signal #18 (`W_TABLE_EXPECTED_NOT_EXTRACTED`) is COUPLED for two reads:

1. `doc.metadata["table_rejected_candidates_by_page"]` — pdf.py's table-strategy accumulator.
2. `doc.metadata["pdf_classification"]` — passed as `doc_type` to `compute_table_expectation`.

The neutral shape for reads (1) already exists: `RejectedTableCandidate` at `aksharamd/scoring/table_expectation.py:40`. The contract formalizes the boundary:

### 3.1 Opt-in contract

```python
# On Document.metadata (or a first-class field in a later model change):
rejected_table_candidates_by_page: dict[int, list[RejectedTableCandidate]] | None
# None (or missing key) → parser did not provide the accumulator.
# Empty dict → parser considered the concept but produced no rejections.
# Populated → the substantial-rejected guard fires as today.
```

**Behavior when parser opts out (key missing or None):**
- `TableExpectationValidator` at table_expectation.py:33 reads `doc.metadata.get("rejected_table_candidates_by_page", {})` — same as today, empty dict = no rejected candidates observed.
- `compute_table_expectation` at scoring/table_expectation.py:259 receives `rejected_candidates=[]` for every page.
- The caption-regex and numeric-alignment signals (already agnostic per SCORER_COUPLING_INVENTORY.md #18) still evaluate on the block tree.
- The substantiality guard (from PR #116) fires only when the rejected-candidate list is non-empty. It never fires on an opt-out parser.
- Net effect: for opt-out parsers, the signal reduces to a caption+numeric-alignment detector, which is byte-identical to today's behavior on documents that happen to have empty `rejected_by_page`.

### 3.2 `doc_type` replacement

Read (2) — `doc_type=doc.metadata.get("pdf_classification")` at table_expectation.py:34 — is replaced by `source_profile.document_type_hint`. The consuming function `compute_table_expectation` at scoring/table_expectation.py:259 takes `doc_type: str | None`; the neutral hint values (`native_text`, `scanned`, `hybrid`, `table_heavy`, `layout_heavy`, `low_confidence`) are the same set of strings the parser produces today, so no change in `compute_table_expectation` is required.

---

## 4. Stitching boundary — opt-in signal group

The stitching group in `compute_table_quality` (`aksharamd/scoring/table_quality.py:426-486`) is gated on `td.extraction_method == ExtractionMethod.PDF_STITCHED` (L428). It reads five `td.metadata` keys populated by `aksharamd/plugins/parsers/pdf_tables/stitching.py`: `source_pages`, `source_table_methods`, `page_row_ranges`, `repeated_header_removed`, `stitching_confidence`.

### 4.1 Opt-in mechanism

The contract defines the stitching signal group as **opt-in**: it fires only when the parser sets `extraction_method` to a value in a `STITCHED_METHODS` set (initially `{ExtractionMethod.PDF_STITCHED}`; extensible to other parsers' stitchers) **and** populates the five metadata keys.

The current gate at table_quality.py:428 is already the correct opt-in check:
```python
if em is None or str(em) != str(ExtractionMethod.PDF_STITCHED):
    return []
```

**No design change needed for opt-out parsers**: they leave `extraction_method` unset (or set to a non-stitched method like `PDFPLUMBER` / `PYMUPDF`), and the stitching signals silently skip. Every AGNOSTIC and BORDERLINE table-quality signal continues to fire as today.

### 4.2 Extension for non-PDF parsers

If a future parser has its own stitching concept (e.g. cross-worksheet spreadsheet stitching for spreadsheet.py), the contract permits extension via:
1. Adding a new `ExtractionMethod` enum value (e.g. `SPREADSHEET_STITCHED`).
2. Extending the gate to `str(em) in {str(m) for m in STITCHED_METHODS}`.
3. Populating the same five `td.metadata` keys with the parser's own stitching state.

Non-PDF parsers that do not stitch simply do not opt in. No stitching signals fire; the AGNOSTIC/BORDERLINE table-quality signals still fire byte-identically.

---

## 5. Adapter interface

The `PdfBlockTreeAdapter` is the reference implementation that populates the contract from today's pdf.py output. Its byte-identical constraint is the correctness proof of the refactor.

### 5.1 Interface

```python
class BlockTreeAdapter(Protocol):
    """Populates the neutral scoring contract from a parser's raw output.

    Adapters are invoked once per document, after parsing and before scoring.
    They produce a Document + SourceProfile pair whose contract fields are
    ready for the scorer to consume without any parser-specific fallbacks.
    """
    def populate(self, doc: Document, raw_state: Any) -> None:
        """Mutates doc in place. Sets doc.metadata['source_profile'] to a
        SourceProfile instance (or attaches it as a first-class attribute in
        a follow-up model change). Ensures every block has bbox and
        is_placeholder in its metadata when the parser can provide them, or
        None/False otherwise.
        """
        ...
```

### 5.2 `PdfBlockTreeAdapter` — byte-identical constraint

For the PDF adapter, every field of the contract must be sourceable from today's pdf.py output such that:

- `compute_confidence(ctx).score` is identical to today's score, for every doc in the dev split (25 docs) and every doc in `parsebench/data/docs`.
- Every emitted warning code is identical, in the same order, with the same block_id attribution.
- Every diagnostics dict written by validators has the same key set and same values.

Concrete mapping the adapter must implement (post-parse mutation):

| Contract field | Adapter reads from today's pdf.py output |
|---|---|
| `source_profile.pages_with_text_layer` | `doc.metadata["pdf_stats"]["text_pages"]` |
| `source_profile.pages_without_text_layer` | `doc.metadata["pdf_stats"]["image_pages"]` |
| `source_profile.pages_total` | `doc.metadata["pdf_stats"]["page_count"]` |
| `source_profile.ocr_capability` | `"available"` if `doc.metadata["pdf_ocr_available"]` else `"unavailable"` |
| `source_profile.hallucinated_pages` | `1 if doc.metadata.get("pdf_ocr_hallucination") else 0` (or a real page count when pdf.py starts recording one) |
| `source_profile.document_type_hint` | `doc.metadata["pdf_classification"]` (string values match the Literal set) |
| `source_profile.pages_containing_tables` | `doc.metadata["pdf_stats"]["table_pages"]` — one-to-one (see 2.3 for why block-derivation is not equivalent) |
| `source_profile.page_dimensions[pg]` | `PageDim(page_width=doc.metadata["pdf_column_info"][pg]["page_width"], page_height=doc.metadata["pdf_column_info"][pg]["page_height"])` |
| `block.metadata["bbox"]` (TEXT blocks) | `[b.metadata["x0"], b.metadata["y0"], b.metadata.get("x1", b.metadata["x0"]), b.metadata.get("y1", b.metadata["y0"])]` |
| `block.metadata["bbox"]` (IMAGE blocks) | Already populated by PR #118 as `list(img_bbox)` |
| `block.metadata["bbox"]` (TABLE blocks) | Alias to existing `block.metadata["table_bbox"]` |
| `block.metadata["is_placeholder"]` | `True` if `block.type == PARAGRAPH and "[Image not extracted" in (block.content or "")` else `False` (temporary; long-term the emitter sets it directly) |
| `doc.metadata["rejected_table_candidates_by_page"]` | Rename from `doc.metadata["table_rejected_candidates_by_page"]` (a naming compat shim can keep the old key readable during transition) |

**Validation:** the adapter's correctness is verified by a dev-split re-run. Every readiness score, warning code, deduction record, informational record, quality band, and diagnostics dict must match the pre-adapter output byte-for-byte. This is a hard gate: any drift is a bug in the adapter, not a scoring change.

### 5.3 Backward-compat shim for one release

To keep the transition mechanical and low-risk, the scorer reads via the contract but falls back to the old `pdf_*` keys when `source_profile` is absent. This lets adopters land the contract adapter without a synchronized change to every downstream reader.

```python
# Pseudo-code inside validators / readiness.py
sp = doc.metadata.get("source_profile")
if sp is not None:
    text_pages = sp.pages_with_text_layer
    image_pages = sp.pages_without_text_layer
    ...
else:
    # legacy path — reads pdf_stats / pdf_classification as today
    text_pages = doc.metadata.get("pdf_stats", {}).get("text_pages", 0)
    image_pages = doc.metadata.get("pdf_stats", {}).get("image_pages", 0)
    ...
```

The shim is deleted once every parser adapter is in place.

---

## 6. Proof-of-neutrality plan (second adapter, hypothetical)

To prove the contract is genuinely parser-neutral, a second reference adapter is designed on paper (not built). Target: **Markdown** (`aksharamd/plugins/parsers/markdown.py` if it exists; otherwise a stubbed `MarkdownBlockTreeAdapter` alongside the PDF adapter).

### 6.1 Which SourceProfile fields Markdown can fill

| Field | Markdown adapter provides | Consequence |
|---|---|---|
| `pages_total` | 1 (Markdown has no page concept) | `NEAR_EMPTY_OUTPUT` / `LOW_TEXT_DENSITY` ratios still compute; MISSING_PAGE never fires |
| `pages_with_text_layer` | 1 | `W_IMAGE_ONLY_TEXT_BAR_FAIL` cannot fire (text_pages > 0) |
| `pages_without_text_layer` | 0 | `OCR_REQUIRED`, `OCR_ATTEMPTED_SPARSE` cannot fire |
| `ocr_capability` | `"not_applicable"` | The extra `not_applicable` enum value cleanly distinguishes "no OCR concept" from "OCR unavailable"; readiness deductions gate on `== "available"` today, so `not_applicable` behaves like `unavailable` for the fire/no-fire decision but is auditable in the receipts |
| `hallucinated_pages` | 0 | `OCR_HALLUCINATION` cannot fire |
| `document_type_hint` | `None` | `pdf_classification` note at readiness.py:454-475 silently skips (it already has an `if classification:` guard); no other signal branches on it |
| `page_dimensions` | `{}` (empty) | `W_MULTICOLUMN_ORDER` and `W_HEADER_FOOTER_TABLE_GARBLED` skip (they already skip when `page_width == 0.0` or `page_height == 0.0`, per multicolumn.py:189 and header_footer_table.py:154) |

### 6.2 Which Block.metadata keys Markdown can fill

| Key | Markdown | Consequence |
|---|---|---|
| `bbox` | `None` on every block | Multicolumn / header_footer_table validators skip pages with no page_dimensions AND blocks with no bbox; consistent with today's `page_width == 0.0 → skip` behavior |
| `is_placeholder` | `False` on every block | `IMAGE_PLACEHOLDER_NO_FALLBACK` never fires; matches today's behavior on Markdown (the sentinel string was never in Markdown content anyway) |
| `table_bbox` | `None` | Same as `bbox` |

### 6.3 Which signals fire on Markdown

**Fires (eligible and likely):** `HEADING_HIERARCHY` / `HEADING_SKIP` / `HEADING_ISSUES`, `LARGE_BLOCK`, `EMPTY_BLOCK`, `REPEATED_CONTENT`, `TOKEN_BLOAT`, `NEAR_EMPTY_OUTPUT`, `LOW_TEXT_DENSITY`, `W_TABLE_MISSING`, `W_ENCODING_ARTIFACTS`, `NO_HEADINGS_MULTIPAGE`, `COL_GENERIC_TABLES`, table-quality (structural completeness, cell fragmentation, header quality — all AGNOSTIC/BORDERLINE), `FORMAT_BASELINE`.

**Eligible but pattern-improbable:**

- `GLYPH_ARTIFACTS` — the signal is a content-grep for PyMuPDF's `(cid:N)` sentinel. It **remains eligible** on Markdown input; the contract does not gate it out. It just rarely matches because Markdown output does not typically contain that sentinel unless something upstream injected it. Distinguish this from "silently skips" — the code path runs, the regex evaluates, and it happens not to match.
- `W_TABLE_EXPECTED_NOT_EXTRACTED` (caption-and-numeric-alignment sub-signal) — this sub-signal reads only `B.content` (caption regex + numeric-alignment heuristic) and is not gated on parser opt-in. Fully eligible on Markdown; unlikely to fire because Markdown does not typically render caption-plus-numeric-column layouts that mimic tables.

**Silently skips (correct):**

- `MISSING_PAGE` — Markdown has no page concept and the validator is gated on `file_type == "pdf"`; concept doesn't apply.
- `OCR_REQUIRED` — `document_type_hint = None` fails the `hint in ("scanned", "hybrid")` gate.
- `OCR_HALLUCINATION` — `hallucinated_pages = 0` fails the truthy check.
- `OCR_ATTEMPTED_SPARSE` — `file_type != "pdf"` fails the file-type gate at `readiness.py:270`.
- `W_IMAGE_ONLY_TEXT_BAR_FAIL` — `document_type_hint == "scanned"` fails (None ≠ "scanned").
- `W_MULTICOLUMN_ORDER` / `W_HEADER_FOOTER_TABLE_GARBLED` — no `page_dimensions` provided; both validators already skip when `page_width == 0.0` / `page_height == 0.0` today.
- `IMAGE_PLACEHOLDER_NO_FALLBACK` — `is_placeholder = False` on every block; `placeholder_paragraphs = []`; gate fails.
- Stitching signal group — `extraction_method != PDF_STITCHED`; the gate at `table_quality.py:428` is already the opt-in mechanism.
- `W_TABLE_EXPECTED_NOT_EXTRACTED` (rejected-candidate sub-signal only) — parser opts out of `rejected_table_candidates_by_page`; the substantiality guard never fires. (The caption+numeric sub-signal above remains eligible.)
- `W_PDF_ATTACHMENT_IGNORED` — no PDF `/EmbeddedFiles` catalog concept.
- `AUTO_OCR_BACKEND_SELECTED` / `AUTO_OCR_BACKEND_FALLBACK` — no Auto Policy for non-PDF parsers.

**Effect:** every silently-skipped signal is one where the concept genuinely doesn't apply to Markdown; every eligible-but-improbable signal remains runnable and would fire if the content pattern happened to match. The contract makes the "why it doesn't fire" auditable (via the neutral inputs) rather than accidental (via missing `pdf_*` keys). This is the proof: the same scorer runs the same policy against both parsers; the skip decisions come from parser-provided inputs, not from parser-specific reads.

### 6.4 What this DOES NOT prove

- Does not prove the contract handles a THIRD parser paradigm (e.g. HTML with mixed inline images and tables). That's a deliberate scope limit — pick one hard case (PDF) plus one clean case (Markdown), and the pattern for a third is mechanical.
- Does not prove behavior on a parser that has bbox but no page_dimensions (e.g. some HTML-to-block pipelines). The correct behavior in that case is "multicolumn / header_footer_table skip"; the contract permits this cleanly (partial fills are fine).
- Does not prove the contract is future-proof for signals not yet designed. That's acceptable — the contract is designed to be extended.

---

## 7. One-adapter-first build recommendation

**Build the `SourceProfile` boundary first as the reference implementation of the pattern.**

Rationale:
- **Biggest coupling.** 7 of 12 COUPLED signals; closes ~35-40% of the coupled score movement on the dev split.
- **Hardest case.** Every field has to be sourced correctly for a byte-identical dev-split re-run — no room for silent divergence. Proving byte-identity here proves the pattern for the easier boundaries.
- **Shape already sketched.** Section 2.2 gives the complete one-to-one mapping. No further design work is needed to start implementing.
- **Isolates the reference-parser adapter concept early.** Once the PDF adapter is in place for `SourceProfile`, the same pattern applies mechanically to the table-expectation and stitching boundaries.
- **Small blast radius.** All changes are additive plus renames — no scoring math changes, no policy version change, no test rewrites (existing tests should pass unchanged because the PDF adapter reproduces today's output byte-for-byte).

**Explicitly NOT recommended as first:**

- Table-expectation boundary first: the opt-in mechanism is simpler than SourceProfile but not load-bearing enough to prove the pattern. Doing it first would leave the OCR cluster as a still-open question.
- Stitching boundary first: already opt-in in effect (gated on `extraction_method`); refactoring it changes nothing observable and doesn't test the contract.
- Full rewrite (all three boundaries at once): higher risk of a subtle divergence and no way to isolate which boundary caused it.

**Sequencing after SourceProfile lands (out of scope for this design doc):**

1. Table-expectation opt-in shape (`rejected_table_candidates_by_page` as neutral key, `document_type_hint` reads from `source_profile`).
2. Stitching opt-in formalization (no behavior change; just documents the pattern).
3. Delete the backward-compat shim (Section 5.3) once all adapters are in place.

---

## 8. What this document is not

- Not an implementation plan. Sizing estimate from SCORER_COUPLING_INVENTORY.md Section G remains 400-800 LOC net for the whole refactor; the SourceProfile boundary alone is likely 150-300 LOC.
- Not a specification change to `SCORING_POLICY` or `compute_confidence`. Neither changes.
- Not a schema for downstream consumers (packaging, chunking, rendering). Those read `Document.blocks` and `Document.metadata` today; whether they migrate to `source_profile` is a separate decision.
- Not committing this to a specific field-first vs. attribute-first choice on `Document`. The design uses `doc.metadata["source_profile"]` as the placeholder because it requires zero model changes; a follow-up can promote `SourceProfile` to a first-class `Document.source_profile: SourceProfile | None` attribute.

## 9. Approval-gate for implementation

Implementation of the `SourceProfile` boundary (per Section 7) requires:

1. This design doc reviewed and approved.
2. A named target: reproduce dev-split readiness scores byte-identically across the 25 docs, with the PDF adapter in place.
3. A named test artifact: `tests/test_scoring/test_source_profile_adapter_byte_identical.py` (or equivalent) that snapshots today's scores + warning codes + diagnostics dict shapes and asserts they are unchanged.

Nothing beyond this document ships without those three gates being cleared.
