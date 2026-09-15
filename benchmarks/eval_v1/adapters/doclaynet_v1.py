"""V1 corpus adapter for DocLayNet (structural G1 for what it directly annotates).

Role in PROTOCOL_V1 (§2.2, §2.3, §2.4, §4.5): DocLayNet supplies
**layout bounding boxes + block-category labels per page**, in
1025×1025 PNG-pixel coordinate space. It is:

- Direct G1 for: table-region presence + geometry, header/footer-region
  presence + geometry, block-category assignment.
- NOT a direct G1 for reading order (see §2.4 caveat 4). Any use for
  ``W_MULTICOLUMN_ORDER``-style adjudication is a derived criterion on a
  geometric proxy — the adapter deliberately does NOT emit an
  ``expected_reading_order`` field so that downstream code cannot
  accidentally treat it as ground truth.
- NOT an oracle for TEDS (see §2.4 caveat 5). DocLayNet's ``Table``
  category is a region-only bbox — no cell grid. §6.1 uses table-region
  localization (IoU-style geometric agreement) instead of TEDS.
- NOT an oracle for W_DROPPED_CONTENT (layout truth ≠ text-content
  truth, §2.4 caveat 2).

Provenance model:

- Strictly offline. Consumes ``DocLayNetAsset`` records populated by
  ``benchmarks/eval_v1/acquisition/doclaynet_hf.py``. The adapter never
  opens a socket.
- The per-page ``manifest.json`` is the local provenance receipt (HF
  dataset id + commit SHA + shard key + our own SHA-256s). The per-page
  ``annotations.json`` is our locally-derived structural oracle
  (recorded once by the acquisition step; the adapter re-reads it).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.corpus_adapter import (
    CorpusCapabilities,
    GroundTruth,
    SourceIngestion,
    V1CorpusAdapter,
)

CORPUS_NAME = "doclaynet"
GT_KIND = "bbox_layout"
# Bumped when the oracle-derivation rules or the manifest schema they
# rely on change. Currently matches
# ``doclaynet_hf.MANIFEST_SCHEMA_VERSION``.
EXTRACTION_RULES_VERSION = "1"


@dataclass(frozen=True)
class DocLayNetAsset:
    """A single acquired DocLayNet page.

    Four paths of provenance, distinct roles:

    - ``manifest_path`` — our local provenance receipt (HF dataset id
      + commit SHA + shard key + per-file SHA-256s).
    - ``annotations_path`` — locally-derived structural oracle
      (categories + bboxes + coordinate-space header).
    - ``png_path`` — the rendered 1025×1025 page image.
    - ``pdf_path`` — the single-page PDF, if present in the
      distribution (v1.2 provides it; older reformats did not).
    """

    page_hash: str
    manifest_path: Path
    annotations_path: Path
    png_path: Path
    pdf_path: Path | None = None


def convert_bbox_to_pdf_points(
    bbox_png: tuple[float, float, float, float] | list[float],
    coco_width: int,
    coco_height: int,
    original_width: float,
    original_height: float,
) -> tuple[float, float, float, float]:
    """Convert a bbox from 1025×1025 PNG-pixel space to native PDF points.

    Linear map:
      x_pdf = x_png * original_width / coco_width
      w_pdf = w_png * original_width / coco_width
      y_pdf = y_png * original_height / coco_height
      h_pdf = h_png * original_height / coco_height

    Getting the coordinate space wrong is the DocLayNet oracle's
    highest-frequency silent failure mode. This helper is exposed
    (not private) so downstream code and tests can round-trip against
    the canonical conversion.
    """
    if coco_width == 0 or coco_height == 0:
        raise ValueError(
            "coco_width and coco_height must be non-zero for bbox conversion"
        )
    if len(bbox_png) != 4:
        raise ValueError(f"bbox must have 4 elements, got {len(bbox_png)}")
    sx = original_width / coco_width
    sy = original_height / coco_height
    x, y, w, h = bbox_png
    return (x * sx, y * sy, w * sx, h * sy)


class DocLayNetV1Adapter(V1CorpusAdapter):
    """DocLayNet adapter — G1 for direct region annotations only.

    Capability declaration:
      - supports_layout_gt=True
      - supports_textual_gt=False   (§2.4 caveat 2)
      - supports_clause_span_gt=False
      - supports_downstream_qa_gt=False
      - supports_clean_native_fpr=False

    The adapter exposes the primitive oracle only:
      { page_hash, bboxes+categories, page geometry }

    It deliberately does NOT emit ``expected_reading_order`` or any
    "table cell grid" field — those would be inferences not supplied
    by DocLayNet.
    """

    def __init__(self, assets: dict[str, DocLayNetAsset]) -> None:
        self._assets = dict(assets)

    # --- V1CorpusAdapter contract -----------------------------------

    def capabilities(self) -> CorpusCapabilities:
        na_layout_ok = "layout GT is the primary DocLayNet role"
        na_text = (
            "PROTOCOL_V1.md §2.4 caveat 2: layout truth is not text-content "
            "truth; DocLayNet cannot adjudicate W_DROPPED_CONTENT."
        )
        na_span = (
            "PROTOCOL_V1.md §2.2: DocLayNet does not supply clause spans."
        )
        na_qa = (
            "PROTOCOL_V1.md §2.2: DocLayNet does not supply QA pairs."
        )
        na_fpr = (
            "PROTOCOL_V1.md §2.2: DocLayNet is a structural oracle, not "
            "the FPR-baseline naturalistic corpus (that is Federal Register)."
        )
        # Note the layout row is intentionally not in not_applicable_reasons
        # because supports_layout_gt=True; the dict is only populated for
        # capabilities that are FALSE.
        _ = na_layout_ok  # keep for docstring readability
        return CorpusCapabilities(
            corpus_name=CORPUS_NAME,
            on_v1_manifest=True,
            supports_textual_gt=False,
            supports_layout_gt=True,
            supports_clause_span_gt=False,
            supports_downstream_qa_gt=False,
            supports_clean_native_fpr=False,
            not_applicable_reasons={
                "textual_gt": na_text,
                "clause_span_gt": na_span,
                "downstream_qa_gt": na_qa,
                "clean_native_fpr": na_fpr,
            },
        )

    def acquire(self, doc_id: str) -> Path:
        """Return the PDF path — that IS the parser input for B1a-3.

        The DocLayNet v1.2 Parquet distribution ships a single-page PDF
        per row. That PDF is the artifact each V1 parser must consume
        so the comparison is "same page, same source bytes." The PNG
        is the coordinate anchor for the structural oracle (see
        ``ingest_ground_truth``), NOT the parser input — never silently
        substitute it.

        Fails closed if the expected PDF is absent. B1a-3 will not
        silently fall back to PNG parsing.
        """
        asset = self._require_asset(doc_id)
        if asset.pdf_path is None or not asset.pdf_path.exists():
            raise FileNotFoundError(
                f"DocLayNet PDF missing for {doc_id}: "
                f"{asset.pdf_path}. B1a-3 pins to v1.2 which includes a "
                f"single-page PDF per row; refuse to substitute the PNG."
            )
        return asset.pdf_path

    def ingest_source(self, doc_id: str) -> SourceIngestion:
        """Ingest the single-page PDF as the parser source.

        The PNG hash + path are preserved in provenance as the oracle
        coordinate anchor — parsers do not consume them, but downstream
        adjudication needs them to map bbox_png back onto the raster.
        """
        asset = self._require_asset(doc_id)
        manifest = self._read_manifest(asset)

        pdf_meta = manifest.get("pdf") or {}
        if not pdf_meta.get("present_in_distribution") or not pdf_meta.get("sha256"):
            raise RuntimeError(
                f"{doc_id}: manifest reports no PDF present in "
                f"distribution. B1a-3 requires v1.2's single-page PDF as "
                f"the parser source; refuse to substitute the PNG."
            )
        if asset.pdf_path is None or not asset.pdf_path.exists():
            raise FileNotFoundError(
                f"DocLayNet PDF missing on disk for {doc_id}: "
                f"{asset.pdf_path}. Cache is inconsistent with manifest."
            )
        pdf_bytes = asset.pdf_path.read_bytes()
        actual_pdf_sha = _sha256(pdf_bytes)
        if actual_pdf_sha != pdf_meta["sha256"]:
            raise RuntimeError(
                f"{doc_id}: PDF on disk hashes {actual_pdf_sha} but "
                f"manifest records {pdf_meta['sha256']}; refuse to ingest "
                f"— the cache is inconsistent."
            )

        # Verify PNG integrity too — it's the oracle anchor. Do not
        # silently proceed if the PNG (recorded in provenance) has drifted.
        png_meta = manifest["png"]
        png_bytes = asset.png_path.read_bytes()
        actual_png_sha = _sha256(png_bytes)
        if actual_png_sha != png_meta["sha256"]:
            raise RuntimeError(
                f"{doc_id}: PNG on disk hashes {actual_png_sha} but "
                f"manifest records {png_meta['sha256']}; refuse to ingest "
                f"— the oracle coordinate anchor has drifted."
            )

        distribution = manifest.get("distribution", {})
        provenance = {
            "corpus": CORPUS_NAME,
            "source_kind": distribution.get("source", "doclaynet"),
            "source_role": "parser_input_single_page_pdf",
            "page_hash": asset.page_hash,
            "dataset_id": distribution.get("dataset_id"),
            "dataset_commit_sha": distribution.get("dataset_commit_sha"),
            "split": distribution.get("split"),
            "shard_key": distribution.get("shard_key"),
            "shard_sha256": distribution.get("shard_sha256"),
            "pdf_sha256": actual_pdf_sha,
            "pdf_path": str(asset.pdf_path),
            # The PNG is the raster the layout oracle is expressed in;
            # preserve its hash + path here so downstream adjudication
            # can rebuild the coordinate anchor without re-reading the
            # manifest.
            "oracle_coordinate_anchor": {
                "kind": "png_pixels",
                "png_sha256": actual_png_sha,
                "png_path": str(asset.png_path),
                "coco_width": manifest["coordinate_space"]["coco_width"],
                "coco_height": manifest["coordinate_space"]["coco_height"],
                "original_width": manifest["coordinate_space"]["original_width"],
                "original_height": manifest["coordinate_space"]["original_height"],
            },
            "manifest_path": str(asset.manifest_path),
            "annotations_path": str(asset.annotations_path),
            "acquired_utc": manifest["acquired_utc"],
        }
        return SourceIngestion(
            doc_id=doc_id,
            path=asset.pdf_path,
            sha256=actual_pdf_sha,
            size_bytes=pdf_meta["size_bytes"],
            media_type="application/pdf",
            provenance=provenance,
        )

    def ingest_ground_truth(self, doc_id: str) -> GroundTruth | None:
        asset = self._require_asset(doc_id)
        manifest = self._read_manifest(asset)
        ann_meta = manifest["annotations_file"]
        ann_bytes = asset.annotations_path.read_bytes()
        actual_sha = _sha256(ann_bytes)
        if actual_sha != ann_meta["sha256"]:
            raise RuntimeError(
                f"{doc_id}: annotations file hashes {actual_sha} but "
                f"manifest records {ann_meta['sha256']}; refuse to "
                f"ingest — the cache is inconsistent."
            )
        payload = json.loads(ann_bytes)
        coord = payload["coordinate_space"]

        return GroundTruth(
            doc_id=doc_id,
            kind=GT_KIND,
            data={
                "page_hash": asset.page_hash,
                "coordinate_space": {
                    "kind": coord["kind"],
                    "coco_width": coord["coco_width"],
                    "coco_height": coord["coco_height"],
                    "original_width": coord["original_width"],
                    "original_height": coord["original_height"],
                },
                "annotations": [
                    {
                        "category_id": a["category_id"],
                        "category": a["category"],
                        "bbox_png": tuple(a["bbox_png"]),
                        "area": a["area"],
                    }
                    for a in payload["annotations"]
                ],
                "categories": payload["coco_categories"],
                # NOTE: intentionally NO "expected_reading_order" field.
                # DocLayNet does not label reading order (§2.4 caveat 4).
                # NOTE: intentionally NO "table_cell_structure" field.
                # DocLayNet Table annotations are region-only (§2.4 caveat 5).
            },
            provenance={
                "corpus": CORPUS_NAME,
                "page_hash": asset.page_hash,
                "annotations_sha256": actual_sha,
                "extraction_rules_version": EXTRACTION_RULES_VERSION,
                "dataset_id": manifest["distribution"]["dataset_id"],
                "dataset_commit_sha": manifest["distribution"]["dataset_commit_sha"],
                "split": manifest["distribution"]["split"],
                "shard_key": manifest["distribution"]["shard_key"],
                "image_id": manifest["image_id"],
                "original_filename": manifest["original_filename"],
                "page_no": manifest["page_no"],
                "doc_category": manifest["doc_category"],
                "collection": manifest["collection"],
                # v1.0 field, absent in v1.2 upstream-deduplicated Parquet.
                # Recorded null so downstream readers see it was absent,
                # not silently defaulted.
                "precedence": manifest.get("precedence"),
                "annotations_summary": manifest.get("annotations_summary", {}),
            },
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "corpus": CORPUS_NAME,
            "extraction_rules_version": EXTRACTION_RULES_VERSION,
            "protocol_section": (
                "PROTOCOL_V1.md §2.2, §2.3, §2.4 caveats 2/4/5, §4.5, §6.1"
            ),
            "primitive_oracle": (
                "page_hash + bboxes + category labels + page geometry only. "
                "Does not emit reading order (§2.4 caveat 4) or table cell "
                "structure (§2.4 caveat 5)."
            ),
        }

    # --- helpers ----------------------------------------------------

    def _require_asset(self, doc_id: str) -> DocLayNetAsset:
        try:
            return self._assets[doc_id]
        except KeyError as e:
            raise KeyError(f"unknown DocLayNet doc_id: {doc_id}") from e

    def _read_manifest(self, asset: DocLayNetAsset) -> dict[str, Any]:
        return json.loads(asset.manifest_path.read_text())


def _sha256(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


__all__ = [
    "CORPUS_NAME",
    "EXTRACTION_RULES_VERSION",
    "GT_KIND",
    "DocLayNetAsset",
    "DocLayNetV1Adapter",
    "convert_bbox_to_pdf_points",
]
