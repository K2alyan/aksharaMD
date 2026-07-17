"""PackageWriter — writes package artifacts to the output directory."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .models import (
    OmitReason,
    OmittedElement,
    PackageAssetReference,
    PackageFidelityReport,
    PackageSourceKind,
    RepresentationType,
    TableArtifact,
)

if TYPE_CHECKING:
    from ..models.document import Document
    from ..models.validation import ValidationReport
    from .models import DocumentPackagePlan

logger = logging.getLogger(__name__)


def _package_asset_id(*parts: str) -> str:
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _write_bytes_with_checksum(path: Path, data: bytes) -> str:
    """Write bytes to path and return SHA-256 checksum hex."""
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class PackageWriter:
    """Writes all package artifacts for a finalized DocumentPackagePlan."""

    def write(
        self,
        output_dir: str | Path,
        plan: "DocumentPackagePlan",
        document: "Document",
        validation_report: "ValidationReport | None" = None,
    ) -> tuple[list[PackageAssetReference], PackageFidelityReport]:
        """Write tables/, images/, regions/, package_plan.json.

        Returns (asset_references, fidelity_report).
        Images and regions are written only when bytes are available.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        document_id = plan.document_id

        # Build lookup maps from document
        block_by_id = {b.id: b for b in document.blocks}
        asset_by_id = {a.id: a for a in document.assets}

        asset_refs: list[PackageAssetReference] = []
        asset_failures = 0

        # ── Track which element IDs have a visual asset for fidelity ─────────
        elements_with_visual: set[str] = set()

        for elem in plan.elements:
            rep = elem.representation
            eid = elem.element_id

            # ── Structured tables ──────────────────────────────────────────────
            if rep == RepresentationType.STRUCTURED_TABLE and elem.block_id:
                block = block_by_id.get(elem.block_id)
                if block is not None and block.table_data is not None:
                    tables_dir = out / "tables"
                    tables_dir.mkdir(exist_ok=True)
                    artifact = TableArtifact(
                        document_id=document_id,
                        block_id=elem.block_id,
                        table=block.table_data,
                    )
                    table_path = tables_dir / f"{elem.block_id}.json"
                    try:
                        table_path.write_text(
                            artifact.model_dump_json(indent=2), encoding="utf-8"
                        )
                    except OSError as exc:
                        logger.debug("Failed to write table artifact %s: %s", table_path, exc)
                        asset_failures += 1

                    # Also create a PackageAssetReference for the table artifact
                    ref_id = _package_asset_id("table", document_id, elem.block_id)
                    ref = PackageAssetReference(
                        package_asset_id=ref_id,
                        source_asset_id=None,
                        role="table_artifact",
                        file_path=f"tables/{elem.block_id}.json",
                        include_by_default=elem.include_by_default,
                        related_element_ids=[eid],
                        extraction_method="native",
                        page=elem.page,
                    )
                    asset_refs.append(ref)

            # ── Key-value groups ───────────────────────────────────────────────
            elif rep == RepresentationType.KEY_VALUE_GROUP and elem.block_id:
                block = block_by_id.get(elem.block_id)
                if block is not None and block.key_value_group is not None:
                    kv_dir = out / "key_values"
                    kv_dir.mkdir(exist_ok=True)
                    artifact_data = {
                        "schema": "key_value_group_v1",
                        "document_id": document_id,
                        "block_id": elem.block_id,
                        "group_id": block.id,
                        "group": block.key_value_group.model_dump(),
                    }
                    artifact_bytes = json.dumps(artifact_data, ensure_ascii=False, indent=2).encode("utf-8")
                    artifact_path = kv_dir / f"{block.id}.json"
                    try:
                        artifact_path.write_bytes(artifact_bytes)
                    except OSError as exc:
                        logger.debug("Failed to write key_value artifact %s: %s", artifact_path, exc)
                        asset_failures += 1

            # ── Embedded images ────────────────────────────────────────────────
            elif rep in (RepresentationType.IMAGE, RepresentationType.IMAGE_AND_TEXT,
                         RepresentationType.REFERENCE_ONLY):
                if elem.block_id:
                    block = block_by_id.get(elem.block_id)
                    if block is not None:
                        # Try to find an asset with image bytes
                        src = block.metadata.get("src") or block.metadata.get("asset_id")
                        asset = asset_by_id.get(src) if src else None

                        # Also scan all assets for a page/block match
                        if asset is None and elem.page is not None:
                            for a in document.assets:
                                if a.page == elem.page and a.image_bytes:
                                    asset = a
                                    break

                        if asset is not None and asset.image_bytes:
                            images_dir = out / "images"
                            images_dir.mkdir(exist_ok=True)
                            ext = "png"
                            if asset.metadata.get("mime_type", "").endswith("jpeg"):
                                ext = "jpg"
                            img_filename = f"{asset.id}.{ext}"
                            img_path = images_dir / img_filename
                            try:
                                checksum = _write_bytes_with_checksum(img_path, asset.image_bytes)
                                ref_id = _package_asset_id("image", document_id, asset.id, "embedded_image")
                                ref = PackageAssetReference(
                                    package_asset_id=ref_id,
                                    source_asset_id=asset.id,
                                    role="embedded_image",
                                    file_path=f"images/{img_filename}",
                                    checksum=checksum,
                                    include_by_default=elem.include_by_default,
                                    related_element_ids=[eid],
                                    extraction_method="embedded",
                                    page=elem.page,
                                    metadata={
                                        "width": asset.width,
                                        "height": asset.height,
                                    },
                                )
                                asset_refs.append(ref)
                                elements_with_visual.add(eid)
                            except OSError as exc:
                                logger.debug("Failed to write image %s: %s", img_path, exc)
                                asset_failures += 1

            # ── Page-level fallbacks (region_crop / page_render) ──────────────
            if rep == RepresentationType.IMAGE and elem.source_kind in (
                PackageSourceKind.PAGE_REGION, PackageSourceKind.PAGE
            ):
                # Rendering requires PyMuPDF (fitz) — skip if not available or no source.
                # The plan element is recorded regardless; the region file may be absent.
                role = "region_crop" if elem.source_kind == PackageSourceKind.PAGE_REGION else "page_render"
                ref_id = _package_asset_id(
                    role, document_id, str(elem.page or 0),
                    ",".join(f"{v:.1f}" for v in (elem.bbox or []))
                )
                filename = f"{ref_id}.png"
                subdir = "regions"
                regions_dir = out / subdir
                rendered = _try_render_region(document, elem)
                if rendered is not None:
                    regions_dir.mkdir(exist_ok=True)
                    region_path = regions_dir / filename
                    try:
                        checksum = _write_bytes_with_checksum(region_path, rendered)
                        ref = PackageAssetReference(
                            package_asset_id=ref_id,
                            source_asset_id=None,
                            role=role,
                            file_path=f"{subdir}/{filename}",
                            checksum=checksum,
                            include_by_default=elem.include_by_default,
                            related_element_ids=[eid],
                            extraction_method="page_render" if role == "page_render" else "cropped",
                            page=elem.page,
                            bbox=elem.bbox,
                        )
                        asset_refs.append(ref)
                        elements_with_visual.add(eid)
                    except OSError as exc:
                        logger.debug("Failed to write region %s: %s", region_path, exc)
                        asset_failures += 1
                else:
                    # No render available — record a placeholder reference with empty checksum
                    ref = PackageAssetReference(
                        package_asset_id=ref_id,
                        source_asset_id=None,
                        role=role,
                        file_path=f"{subdir}/{filename}",
                        checksum="",
                        include_by_default=elem.include_by_default,
                        related_element_ids=[eid],
                        extraction_method="page_render" if role == "page_render" else "cropped",
                        page=elem.page,
                        bbox=elem.bbox,
                        metadata={"render_available": False},
                    )
                    asset_refs.append(ref)

        # ── Write package_plan.json ────────────────────────────────────────────
        (out / "package_plan.json").write_text(
            plan.model_dump_json(indent=2), encoding="utf-8"
        )

        # ── Build fidelity report ──────────────────────────────────────────────
        fidelity = _build_fidelity(
            document_id, plan, validation_report, elements_with_visual, asset_failures
        )

        return asset_refs, fidelity


def _try_render_region(document, elem) -> bytes | None:
    """Attempt to render a page region using PyMuPDF. Returns PNG bytes or None."""
    source = getattr(document, "source", None) or getattr(document, "_source_path", None)
    if not source:
        return None
    try:
        import fitz
    except ImportError:
        return None
    page_num = elem.page
    if page_num is None:
        return None
    try:
        doc = fitz.open(source)
    except Exception:
        return None
    try:
        page_idx = page_num - 1
        if page_idx < 0 or page_idx >= doc.page_count:
            return None
        page = doc[page_idx]
        if elem.bbox and len(elem.bbox) == 4:
            rect = fitz.Rect(*elem.bbox)
            mat = fitz.Matrix(2.0, 2.0)  # 2x scale for legibility
            pix = page.get_pixmap(clip=rect, matrix=mat, alpha=False)
        else:
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")
    except Exception as exc:
        logger.debug("Region render failed for page %s: %s", page_num, exc)
        return None
    finally:
        doc.close()


def _build_fidelity(
    document_id: str,
    plan: "DocumentPackagePlan",
    validation_report,
    elements_with_visual: set[str],
    asset_failures: int,
) -> PackageFidelityReport:
    preserved_reps = {
        RepresentationType.MARKDOWN,
        RepresentationType.STRUCTURED_TABLE,
        RepresentationType.IMAGE,
        RepresentationType.IMAGE_AND_TEXT,
        RepresentationType.REFERENCE_ONLY,
        RepresentationType.KEY_VALUE_GROUP,
    }

    meaningful = sum(
        1 for e in plan.elements
        if not (
            e.representation == RepresentationType.OMIT
            and e.omit_reason == OmitReason.STRUCTURAL_MARKER
        )
    )

    preserved = sum(1 for e in plan.elements if e.representation in preserved_reps)
    in_default = sum(
        1 for e in plan.elements
        if e.include_by_default
        and e.representation not in (RepresentationType.OMIT, RepresentationType.REFERENCE_ONLY)
    )
    reference_only = sum(
        1 for e in plan.elements if e.representation == RepresentationType.REFERENCE_ONLY
    )
    omitted = sum(1 for e in plan.elements if e.representation == RepresentationType.OMIT)

    omitted_elements = [
        OmittedElement(
            element_id=e.element_id,
            reason_code=e.omit_reason or OmitReason.UNSUPPORTED,
            block_id=e.block_id,
            page=e.page,
        )
        for e in plan.elements
        if e.representation == RepresentationType.OMIT and e.omit_reason is not None
    ]

    structured_tables = sum(
        1 for e in plan.elements
        if e.representation == RepresentationType.STRUCTURED_TABLE
    )
    tables_with_fallback = sum(
        1 for e in plan.elements
        if e.representation == RepresentationType.STRUCTURED_TABLE
        and e.element_id in elements_with_visual
    )
    images = sum(
        1 for e in plan.elements
        if e.representation in (RepresentationType.IMAGE, RepresentationType.IMAGE_AND_TEXT)
        and e.source_kind.value == "block"
    )
    regions = sum(
        1 for e in plan.elements
        if e.source_kind.value in ("page_region", "page")
    )

    # Warnings without visual fallback
    missed_table_elems = [
        e for e in plan.elements
        if e.source_kind.value in ("page_region", "page")
        and e.element_type == "table"
    ]
    warnings_no_fallback = sum(
        1 for e in missed_table_elems
        if e.element_id not in elements_with_visual
    )

    pages_unresolved: list[int] = []
    for e in missed_table_elems:
        if e.element_id not in elements_with_visual and e.page and e.page not in pages_unresolved:
            pages_unresolved.append(e.page)

    unresolved_te = 0
    ocr_blocks = sum(
        1 for e in plan.elements
        if e.confidence == "ambiguous"
    )

    if validation_report:
        unresolved_te = sum(
            1 for i in validation_report.issues
            if i.code == "W_TABLE_EXPECTED_NOT_EXTRACTED"
        )

    unresolved_ids = [
        e.element_id
        for e in missed_table_elems
        if e.element_id not in elements_with_visual
    ]

    return PackageFidelityReport(
        document_id=document_id,
        meaningful_elements_discovered=meaningful,
        elements_preserved_in_package=preserved,
        elements_included_in_default_payload=in_default,
        elements_reference_only=reference_only,
        elements_intentionally_omitted=omitted,
        omitted_elements=omitted_elements,
        preservation_failures=asset_failures,
        structured_tables=structured_tables,
        tables_with_visual_fallback=tables_with_fallback,
        images_preserved=images,
        visual_regions_preserved=regions,
        warnings_without_visual_fallback=warnings_no_fallback,
        pages_with_possible_unpreserved_content=sorted(pages_unresolved),
        unresolved_table_expectations=unresolved_te,
        ocr_uncertainty_blocks=ocr_blocks,
        asset_extraction_failures=asset_failures,
        unresolved_element_ids=unresolved_ids,
    )
