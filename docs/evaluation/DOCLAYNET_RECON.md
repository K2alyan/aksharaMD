# DocLayNet Reconnaissance (B1a-3)

**Status:** Read-only reconnaissance. No acquisition code has been written against these findings. No dataset payload downloaded. Awaiting human approval of the acquisition contract before B1a-3 resumes.

**Date:** 2026-09-14 (verified live where noted).

**Reason for reconnaissance:** DocLayNet is B1a's structural-G1 corpus. Its published distribution has migrated between the original 2022 IBM release and today; before B1a-3 spends any bandwidth on the multi-GB corpus, we want the smallest reproducible path from ONE source PDF page to its EXACT COCO annotation record.

**Role in PROTOCOL_V1** (§2.2, §2.3, §4.5, §6.1):
- Supplies **layout bboxes + block-type labels** per page, page-anchored.
- G1 oracle for structural detectors: `W_MULTICOLUMN_ORDER`, `W_TABLE_MISSING`, `W_HEADER_FOOTER_TABLE_GARBLED`.
- **NOT** an oracle for `W_DROPPED_CONTENT` (protocol §2.2 caveat 2: "layout truth ≠ text-content truth").
- Referenced for table geometry (TEDS) in §6.1 — **this reference needs to be tightened**; see §3 below.

---

## 1. Current canonical distribution channel

Three channels are live as of 2026-09-14:

**IBM Cloud Object Storage (original 2022 release, still live).** HEAD 2026-09-14 shows both zips returning HTTP 200 with `Last-Modified: 2022-08-02`:
- `DocLayNet_core.zip` — ~28 GB (PNG renders + split COCO JSONs)
- `DocLayNet_extra.zip` — ~7.5 GB (per-page single-page PDFs + per-page cell JSONs)
- Base URL: `https://codait-cos-dax.s3.us.cloud-object-storage.appdomain.cloud/dax-doclaynet/1.0.0/`
- Documented on `github.com/DS4SD/DocLayNet` README.

**Hugging Face — new canonical namespace `docling-project/*`.**
- The old `ds4sd/DocLayNet` / `ds4sd/DocLayNet-v1.1` IDs currently issue HTTP 307 redirects to `docling-project/DocLayNet` / `docling-project/DocLayNet-v1.1`. **Docs on `github.com/DS4SD/DocLayNet` have not been updated to reflect this move** — a first-time integrator relying only on the GitHub README could easily miss the migration.
- Available now: `docling-project/DocLayNet` (v1.0 recut), `docling-project/DocLayNet-v1.1`, `docling-project/DocLayNet-v1.2`, `docling-project/icdar2023-doclaynet`.

**Small community subsets for prove-one.**
- `miikatoi/DocLayNet-tiny` — 82 rows (70 train / 7 val / 5 test), Parquet. Ideal for adapter unit tests. **Does not include source PDF binaries.**
- Also present: `merve/doclaynet-small`, `pierreguillou/DocLayNet-small`, `imanmalik/doclaynet_sample`. None are DS4SD-official.

**Recommendation for prove-one:** stream one row from `docling-project/DocLayNet-v1.2` (Parquet, PDF binary column present). One HF `datasets` request in streaming mode; no 30 GB download. IBM COS is retained as a fallback but is a single point of failure.

## 2. Object hierarchy per document

Two structurally different layouts depending on channel — the adapter must know which.

**IBM COS zips (original 1.0):**
```
DocLayNet_core.zip
├── COCO/{train,val,test}.json   # one large COCO JSON per split
└── PNG/<page_hash>.png          # one PNG per rendered page (1025×1025)

DocLayNet_extra.zip
├── PDF/<page_hash>.pdf          # one SINGLE-PAGE PDF per record
└── JSON/<page_hash>.json        # per-page PDF text cells + coords
```

**Hugging Face Parquet (v1.1 / v1.2):** one Parquet row per page, columns include:
- `image` (PNG bytes)
- `bboxes`, `category_id`, `segmentation`, `pdf_cells`
- `metadata`: `page_hash`, `doc_category`, `original_filename`, `page_no`, `coco_width`, `coco_height`, `original_width`, `original_height`
- `pdf` (single-page PDF bytes) — **v1.2 only.** v1.1 dropped this column.

Unit of distribution in either channel is a **rendered single page**, not a multi-page document. The `<page_hash>` is the canonical join key across every asset.

## 3. COCO annotation schema — plus a required protocol tightening

Per-image record (from `docling-project/DocLayNet-v1.2` card):
```json
{
  "id": 1,
  "width": 1025, "height": 1025,
  "file_name": "<page_hash>.png",
  "doc_category": "financial_reports",
  "collection": "ann_reports_00_04_fancy",
  "doc_name": "NASDAQ_FFIN_2002.pdf",
  "page_no": 9,
  "precedence": 0
}
```

**Coordinate system:** bboxes are `[x, y, w, h]` in the 1025×1025 PNG pixel space. Rescaling to native PDF-point coordinates requires `original_width` / `original_height`. Getting this wrong silently tanks recall — a parser reporting PDF-point bboxes vs an oracle in PNG-pixel space will produce near-zero IoU.

**Block-type categories (11):** `Caption, Footnote, Formula, List-item, Page-footer, Page-header, Picture, Section-header, Table, Text, Title`. IDs 1–11.

**Page anchoring is native.** Every image record carries `doc_name` + `page_no`; annotations reference their `image_id` via `annotations[].image_id`.

**⚠️ Protocol tightening required for §6.1.**
> `PROTOCOL_V1.md` §6.1: *"TEDS for DocLayNet tables"*

DocLayNet only labels `Table` as a **region-level bbox**. It does **NOT** provide cell grids, row/column structure, or headers. TEDS (Tree-Edit-Distance-based Similarity) requires a full HTML/cell-graph oracle. **TEDS cannot be computed from DocLayNet alone.** The protocol's phrasing risks over-promising the DocLayNet oracle.

Two remediations:
- (Preferred) tighten §6.1 wording from "TEDS for DocLayNet tables" to "table-region localization for DocLayNet tables" (IoU-style geometric agreement, not TEDS). The stronger table-cell corpora (PubTables-1M, FinTabNet, PubTabNet) are out of V1 scope.
- (Alternative) add a table-cell corpus to V1 explicitly. Bigger scope change; recommend deferring to V2.

**This is a protocol-methodology finding, not an implementation choice.** Flagging it here for a human decision before B1a-3 goes further; the adapter code will match whichever decision is made.

## 4. PDF ↔ annotation identity

**Ground truth is anchored to the rendered 1025×1025 PNG, not to the source PDF's native coordinate space.** `original_width` / `original_height` support rescaling for parsers that report in PDF points, but the ground truth is defined on the raster.

**`<page_hash>` is the sole join key** across PNG ↔ single-page PDF ↔ per-page JSON ↔ COCO `image_id`. It is stable across releases.

**Original PDFs are distributed as single-page PDF extracts** (one file per annotated page). `doc_name` + `page_no` tell you what multi-page source the page came from, but reconstructing the original multi-page PDF requires an external fetch — not our concern for structural adjudication.

**Identity model for V1 adapter:**
```
DocLayNet page identity =
    (release_id, dataset_commit_sha, page_hash)
      ├── PNG bytes                  (image column in Parquet, or PNG/<hash>.png in zip)
      ├── PDF bytes (single-page)    (pdf column in v1.2, or PDF/<hash>.pdf in zip)
      ├── COCO image record          (metadata + bboxes columns, or COCO/*.json entry)
      └── PDF text cells JSON        (pdf_cells column, or JSON/<hash>.json in zip)
```

## 5. Small subset choices for prove-one

Ranked for our needs:

| Option | Rows | Has PDF? | Suitability |
|---|---:|---|---|
| `docling-project/DocLayNet-v1.2` in **streaming mode** | 80,863 | **Yes** | Best — official, current, gives us everything, no full download |
| `miikatoi/DocLayNet-tiny` | 82 | No | Unit tests only; not for prove-one (no PDF) |
| `docling-project/icdar2023-doclaynet` | subset | Partial | Ties us to competition splits |

**Recommended prove-one path:** stream one row from `docling-project/DocLayNet-v1.2`, extract PDF + PNG + bboxes + metadata, hash all three, run the structural-oracle acceptance test.

## 6. License

**CDLA-Permissive-1.0.** Confirmed on both HF v1.1 and v1.2 dataset cards. License text: `https://cdla.io/permissive-1-0/`.

Permits:
- Redistribution
- Derivative works
- Use in research pilots
- Recording per-page hashes + metadata + citing the paper — well within scope.

Attribution: Pfitzmann et al., *DocLayNet: A Large Human-Annotated Dataset for Document-Layout Segmentation*, KDD 2022, doi:10.1145/3534678.3539043.

## 7. Stable identifiers to record per selected page

Recommended manifest schema keys (all recorded):

- `dataset_id` — e.g. `docling-project/DocLayNet-v1.2`
- `dataset_commit_sha` — HF `revision=` value, pinning the exact snapshot
- `page_hash` — **primary key** for cross-file identity (PNG/PDF/JSON/COCO all key off this)
- `coco_image_id` — integer, split-scoped; needed for COCO annotation lookup
- `doc_name` + `page_no` — human-readable citation coordinates
- `precedence` (0/1/2) — flags whether this is the primary or a redundant annotation

**Primary key is `page_hash`, not `image_id`** — the latter collides across splits.

## 8. Version / update history

- **v1.0** (Aug 2022) — original IBM COS release; mirrored to HF as `ds4sd/DocLayNet` (Jan 2023).
- **v1.1** — HF-only reformat; adds `pdf_cells`, abandons COCO format for native Parquet, **drops raw PDF binary**. ~63.5k rows.
- **v1.2** — current, **80,863 rows** (69.4k train / 6.49k val / 5k test), Parquet, **re-adds `pdf` binary column**. Only official release with PNG + bboxes + segmentation + `pdf_cells` + PDF bytes together in one row.
- `docling-project/doclaynet-pt-enriched-formula` — Portuguese formula-enriched variant, not relevant to a general-purpose G1 oracle.

**Namespace note:** DS4SD (IBM Deep Search) rebranded the HF org to `docling-project` in 2024–2025 alongside Docling parser release. The `ds4sd/*` IDs redirect (HTTP 307) but `github.com/DS4SD/DocLayNet` docs have not been updated. First-time integrators may still write against dead old IDs; keeping a permanent note in acquisition provenance about which namespace was used protects reproducibility.

**Recommendation: pin V1 to `docling-project/DocLayNet-v1.2` at a specific commit SHA.**

## 9. Retraction / erratum record

None found in public web sources — no erratum section on GitHub README, HF cards, or in follow-on papers as of 2026-09-14.

The only quality feature the authors themselves flag is the **triple-annotation cohort**: some pages appear 2× or 3× in the dataset with different `image_id`s but the same `page_hash`, allowing inter-annotator-agreement analysis. `precedence` records which copy is primary (0). When adjudicating a parser against such a page, the adapter should either:
- take only `precedence=0` (default; matches paper's methodology), or
- explicitly fall back to a majority-vote over redundant copies (opt-in analysis).

Cannot fully rule out silent per-page quality regressions without inspection, but no systemic erratum exists.

## 10. Things that will trip a first-time adapter author

1. **PNG-space vs PDF-point coordinates.** GT is 1025×1025 raster space. Parsers reporting PDF-point bboxes must be rescaled via `original_width`/`original_height`. Silently wrong = near-zero IoU.
2. **No table-cell grid.** `Table` is a region-only label. **TEDS cannot be computed from DocLayNet alone.** (See §3 above — this needs a protocol tightening.)
3. **Reading-order is not directly labelled.** `W_MULTICOLUMN_ORDER` requires deriving expected reading order from bbox geometry; the DocLayNet paper itself notes this ambiguity. The adapter cannot ship reading order; the detector (or a downstream helper) must infer it.
4. **Single-page PDFs, not whole documents.** Every DocLayNet PDF file is one page. Multi-page parser behavior is not exercised by these blobs.
5. **Split leakage / held-out test set.** The 5k-page test split is what the DocLayNet paper and ICDAR 2023 competition are scored on. If V1 reports comparative numbers, use the same test split for comparability; keep DocLayNet train/val out of any calibration corpus.
6. **Redundant annotations & `precedence`.** Deduplicate on `page_hash` unless you explicitly want the redundancy.
7. **Namespace / migration risk.** `ds4sd/*` IDs redirect today, but the redirect is not a permanent contract. Pin the new `docling-project/*` ID + commit SHA in provenance.
8. **HF Datasets script deprecation.** `ds4sd/DocLayNet` card warns "Dataset scripts are no longer supported." Old `load_dataset("ds4sd/DocLayNet")` calls that relied on a loading script may error; use Parquet-native `docling-project/DocLayNet-v1.2` instead.
9. **CDLA-Permissive is NOT `CC BY`.** Metadata pre-filtering by "exact CC BY" (as used for PMC-OA) is not applicable — CDLA-Permissive is the dataset-native license, and the entire corpus is under one license, not a per-record variable.
10. **Structural oracle ≠ text oracle.** Protocol §2.2 caveat: adapter capability declaration must set `supports_layout_gt=True` and `supports_textual_gt=False`. Do NOT let this adapter be used to adjudicate `W_DROPPED_CONTENT`.

---

## Preliminary adapter shape (proposed, NOT implemented)

```python
@dataclass(frozen=True)
class DocLayNetAsset:
    page_hash: str            # primary key
    dataset_id: str           # "docling-project/DocLayNet-v1.2"
    dataset_commit_sha: str
    pdf_path: Path            # single-page PDF
    png_path: Path            # 1025x1025 rendered
    coco_json_path: Path      # per-page COCO annotations
    pdf_cells_json_path: Path # per-page PDF text cells + coords
    manifest_path: Path       # our local provenance receipt
```

Capabilities: `supports_layout_gt=True`, all others `False` with `not_applicable_reasons` citing §2.2.

Ground-truth `kind`: `"bbox_layout"` (matches the docstring example in `benchmarks/eval_v1/corpus_adapter.py:62`).

Ground-truth `data` payload (proposed):
```
{
  "page_hash": str,
  "doc_name": str,
  "page_no": int,
  "coco_width": int, "coco_height": int,
  "original_width": int, "original_height": int,
  "annotations": [
    {"category": "Text"|"Title"|..., "bbox": [x, y, w, h], "segmentation": ...},
    ...
  ],
  "pdf_cells": [ ... ]  # per-cell text + coords from pdf_cells_json
}
```

Coordinate-space rule: `annotations[].bbox` remains in 1025×1025 PNG space. Downstream comparison MUST rescale parser output before IoU.

## 11. What has NOT been touched

- No acquisition code written.
- No dataset payload downloaded.
- No changes to `PROTOCOL_V1.md` (see §3 for the requested tightening).
- No commits made.
- No changes to `benchmarks/eval_v1/adapters/__init__.py`.
- No memory updates.

**STOP for human review.**

## 12. Decisions requested before B1a-3 acquisition begins

1. **Protocol §6.1 wording — TEDS vs table-region localization.** DocLayNet cannot supply TEDS. Preferred fix: tighten §6.1 to say "table-region localization for DocLayNet tables" (IoU-style geometric agreement). Alternative: add a table-cell corpus to V1 (scope creep; recommend deferring to V2). Requires human call.

2. **Distribution pin.** Approve `docling-project/DocLayNet-v1.2` at a specific commit SHA (recorded in the acquisition manifest) as the canonical distribution for V1, with IBM COS as fallback? Or prefer IBM COS as canonical (older, more stable but multi-GB)?

3. **Prove-one path.** Approve streaming one row from `docling-project/DocLayNet-v1.2` via HF `datasets` (no full download)? Or require the IBM COS approach for the prove-one?

4. **Deterministic candidate ordering.** For B1a-3 prove-one, propose `SHA-256(page_hash)` ascending over the v1.2 test split (5k pages), same primitive used for PMC-OA. Confirm.

5. **`precedence` policy.** For B1a-3, default to `precedence == 0` (paper's primary-annotation semantics). Confirm.

6. **Structural eligibility filters.** For a structural-G1 oracle, propose requiring per prove-one candidate:
   - `precedence == 0`
   - annotations non-empty
   - at least one non-Text category (Title, Section-header, Table, Picture, etc.) so structural signal actually exists
   - engineering bound: 10 ≤ number of annotations ≤ 200 (avoid trivial and pathological pages)

   Confirm filter set (or refine).

7. **Adapter-side dependency.** Using HF's `datasets` library for streaming means adding `datasets` to the eval_v1 dependency surface if it isn't already there. Alternative: hit the HF Parquet HTTPS URLs directly (no dependency, more code). Which do you prefer?
