# Parser routing — findings

Read-only investigation, main @ `c4eb565`. All citations to `aksharamd/plugins/parsers/pdf.py` and `aksharamd/plugins/ocr_backends/auto_selector.py`.

Preserve: `tools_scratch/step0d_engine_routing.py` (produces the per-doc engine attribution table below).

---

## 1. Routing logic

Three engines exist. Every document goes through the default engine. The other two are conditionally added.

### Engine A — Default text extraction (PyMuPDF + pdfplumber)

Runs **unconditionally per page** at Phase 1: `_extract_raw_page` (pdf.py:~L1000-1200, invoked from L2851 for small docs / L2837-2840 for large docs via `_extract_page_chunk`). No branching — every doc uses this.

### Engine B — Per-page Tesseract OCR (default OCR backend)

Runs in Phase 3 inside `_process_raw_page` when the alternate OCR backend is NOT selected. The per-page condition is (from earlier read of `_process_raw_page` around L2210):

```python
if _ocr_available():
    for img_bytes in raw.embedded_image_bytes:
        _apply_page_ocr(img_bytes, raw.page_num, blocks)
```

Gated per-doc by `use_alternate_ocr_backend` (pdf.py:2940 and L2951 — passed into `_process_raw_page`). When `--ocr-backend unlimited_ocr` is selected, per-page Tesseract is skipped and a single batched call is made after Phase 4 (pdf.py:2975-2988).

### Engine C — Marker vision reconstruction

**Per-doc gate** (pdf.py:3020):

```python
if _marker_available() and use_marker_phase:
```

- `_marker_available()` = marker-pdf installed (pdf.py:92-96).
- `use_marker_phase = not skip_marker_vision` (pdf.py:2929).
- `skip_marker_vision = use_alternate_ocr_backend` (pdf.py:2928).
- `use_alternate_ocr_backend = _ocr_backend_selected == "unlimited_ocr"` (pdf.py:2919).

**Per-page** decision on which pages Marker touches, from `_apply_marker_to_image_pages` (pdf.py:2396-2399):

```python
image_page_nums = [
    raw.page_num for raw in raw_pages
    if sum(len(s.get("text", "")) for s in raw.spans) < _OCR_TEXT_THRESHOLD
]
```

Marker only touches pages whose raw text-char count is below `_OCR_TEXT_THRESHOLD`. Text-native pages never see Marker.

### Summary

- **Per-page granularity:** which pages Marker actually processes (`_OCR_TEXT_THRESHOLD` check) and which pages need per-page OCR.
- **Per-doc granularity:** which OCR backend is selected (Tesseract vs unlimited_ocr) and whether the Marker phase runs at all.

---

## 2. What drives the routing

**Routing does NOT depend on `pdf_classification`.** Grepped every use of `pdf_classification` in `pdf.py`:

- **L2746:** `metadata={"pdf_classification": "low_confidence", ...}` — fallback path when parse errors on damaged/encrypted PDFs, not routing.
- **L2876:** `pdf_classification, pdf_stats = _classify_pdf(raw_pages)` — computation site.
- **L3059:** `pdf_metadata["pdf_classification"] = pdf_classification` — output writing.

Nothing else reads `pdf_classification`. The variable is computed, written to metadata for downstream scoring/reporting, and never fed back into engine selection. Confirmed by direct grep across `aksharamd/plugins/parsers/pdf.py`.

The signals routing actually keys on:

- **Marker page selection:** `raw.spans` text-char count vs `_OCR_TEXT_THRESHOLD` (per page, pdf.py:2396-2399 and also 3021 for the outer aggregate `image_page_count`).
- **Per-page OCR:** `raw.ocr_pixmap is not None` — which is set in Phase 1 by the same per-page char count.
- **OCR backend:** `_ocr_backend_selected` (pdf.py:2903, 2912) from either `ctx.ocr_backend` explicitly or Auto Policy v1.

**Auto Policy v1 does not take `pdf_classification` as input.** `select_ocr_backend(...)` signature (auto_selector.py:154-159):

```python
def select_ocr_backend(
    *,
    total_pages: int,
    ocr_required_pages: int,
    unlimited_ocr_availability: BackendAvailability,
) -> AutoOcrDecision:
```

The decision rule (auto_selector.py:223-245):

```python
meets_page_floor = ocr_required_pages >= _MIN_UOC_PAGES
meets_fraction = fraction >= _UOC_FRACTION_THRESHOLD
uoc_preferred = meets_page_floor and meets_fraction
```

`ocr_required_pages` is counted at the caller (pdf.py:2894-2896) as `sum(1 for raw in raw_pages if raw.ocr_pixmap is not None)` — the same per-page signal, not the classifier label.

### Answer to the specific question

**Does routing depend on `pdf_classification` (the classifier from #117 that mislabels 8/15 table-heavy docs)?**

**No.** #117's classifier bug affects the classification label consumed by downstream signals (e.g., `DOC_TABLE_HEAVY` in `table_expectation.py`, informational metadata surfaced in the manifest) but **does not send documents to the wrong parser**. The routing keys on per-page `ocr_pixmap` presence (a char-count check per page), not on the classification label.

---

## 3. Which engine parsed each residual

From `tools_scratch/step0d_engine_routing.py` output, live compile against parsebench fixtures on main @ `c4eb565`:

| Doc | Classification | image_pages | pdf_vision_pages | Engine attribution |
|---|---|---:|---:|---|
| `text_dense__de` | native_text | 0 | 0 | **Default only** (PyMuPDF + pdfplumber; no OCR, no Marker) |
| `text_multicolumns__4c` | layout_heavy | 0 | 0 | **Default only** |
| `text_multicolumns__simple2` | layout_heavy | 0 | 0 | **Default only** |

Interpretation: **`de`'s embedded image, `4c`'s merged spans, and `simple2`'s split word are all defects in the default extractor's block-formation stage** (Phase 3 `_process_raw_page` operating on `RawPage` structures from Phase 1). Not Marker, not OCR.

This tightens the fix-path recommendation from Task 3's verdict correction: the parser rules for #3 (`4c` span-clustering discipline) and #4 (`simple2` mid-word font-boundary handling) live in the PyMuPDF/pdfplumber-based default extraction path, not in the vision or OCR paths.

---

## 4. Is the dev split a blend

Per-engine breakdown for all 25 dev-split docs (env: marker-pdf installed, pytesseract installed, `--ocr-backend` default = tesseract):

| Engine attribution | Count | Docs |
|---|---:|---|
| Default only (no OCR, no Marker) | **22** | fqr, battery, webprint, refpage, docusigned, de, minutes2, FBLB, budget, simple2, elpais, 4c, edits, strikeUnderline, 2colmercedes, 3colpres, pwc, ikea3, SERFF_CA, gridofnumbers, VRSK, eastbaytimes |
| Default + Marker (image-only pages get vision reconstruction) | **3** | myctophidae, letter3, japanese |

**Yes, the dev split is a blend** — but a lopsided one. 22/25 docs go through the default extractor only. The 3 image-only docs additionally get Marker.

### Implication for the gate numbers

The current Phase 4 v2 gates (raw HIGH-band false-safe 25% = 3/12, silent-failure 25% = 3/12) are measured on the fresh-HIGH text-content docs. The 3 Marker-touched docs are all correctly capped out of HIGH by `W_IMAGE_ONLY_TEXT_BAR_FAIL` (Phase 3.5), so **none of them contribute to the current HIGH-band false-safe rate**. In practice, the current calibration gate is an all-default-engine measurement, not a mixed measurement.

The 3 Marker-parsed docs contribute to Section 4 ("what works") — the image-only routing that ships as `W_IMAGE_ONLY_TEXT_BAR_FAIL` is the mechanism that catches them.

**But** this only holds while the Marker path successfully caps its docs out of HIGH. If the sealed splits contain image-only or mixed docs where Marker's reconstruction is enough to keep them in HIGH but semantically wrong, that would introduce a genuine engine blend into the calibration numbers. Not a current concern; worth flagging for the sealed-splits run.

---

## 5. Consistency / determinism

Given the same input PDF and the same environment, routing is deterministic. Sources of non-reproducibility come from configuration and installed-package state:

| Axis | Deterministic within? | Non-determinism source |
|---|---|---|
| Same env, same doc, same CLI flags | Yes | — |
| Same doc across environments | **No** | `_marker_available()` and `_ocr_available()` gates change engine attribution. A machine without marker-pdf installed processes image-only pages differently (no vision reconstruction; the pages remain empty). |
| `--ocr-backend auto` | **Partially** | Auto Policy v1's `unlimited_ocr_availability.runnable_now` depends on whether UOC snapshot is cached AND matches the verification receipt (per PR #99 tightened invariant). Same doc on a fresh machine without UOC installed → falls back to Tesseract with a loud `AUTO_OCR_BACKEND_FALLBACK` warning. Documented and audited via `ocr_auto_decision` on the manifest, so it's not silent, but it IS environment-dependent. |
| Marker output | Deterministic per model version | Marker's model weights are pinned upstream but not by AksharaMD; a marker-pdf version bump could change Marker's block emission for the same page. |

**For the current calibration run:** parsebench env has both marker-pdf and pytesseract installed; `--ocr-backend` is the default (tesseract, not auto), so Auto Policy v1 is never invoked. Every dev-split doc has a single, deterministic engine attribution in that env.

**For anyone re-running the calibration in a different env:** the 3 image-only docs (`myctophidae`, `letter3`, `japanese`) would produce different blocks without marker-pdf installed. The 22 default-only docs would be identical. The gate math (25% / 25% on 12-doc HIGH denominator) would be the same because the 3 image-only docs are capped out of HIGH by `W_IMAGE_ONLY_TEXT_BAR_FAIL` regardless of whether Marker ran, but the manifest content on those 3 docs would differ.

---

## Bottom line

- Routing keys on **per-page text-char count** (via `raw.ocr_pixmap is not None`) and **user-selected OCR backend**. Never on `pdf_classification`.
- **Issue #117's classifier bug does not affect parser routing.** #117 remains a signal-consumption bug, not a routing bug. The re-scope concern the user raised is empirically not present in this codebase.
- **All three residuals (`de`, `4c`, `simple2`) were parsed by the default extractor** (PyMuPDF + pdfplumber), no Marker, no OCR. The Task 3 verdict correction naming them as parser block-formation bugs in `pdf.py` is exactly the right layer.
- **Dev split engine distribution: 22 default-only + 3 default+Marker.** Current gate numbers are effectively a single-engine measurement because all 3 Marker-touched docs are correctly capped out of HIGH.
- **Routing is deterministic within a fixed env** but environment-dependent (marker-pdf installed? pytesseract installed? UOC snapshot cached?). Not a reproducibility problem within the parsebench env; worth stating in the certified-parser direction.
