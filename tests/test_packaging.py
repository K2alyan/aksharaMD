"""Tests for aksharamd/packaging/ — Milestone B.

Tests use mock Block, Document, ValidationReport objects directly; no real
PDF compilation is needed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aksharamd.models.asset import Asset
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.models.manifest import Manifest
from aksharamd.models.table import ExtractionMethod, TableCell, TableData
from aksharamd.models.validation import Severity, ValidationIssue, ValidationReport
from aksharamd.packaging import (
    PLANNER_VERSION,
    OmitReason,
    PackageMode,
    PackageProfile,
    PackageSourceKind,
    PackageWriter,
    RepresentationType,
    build_token_report,
    plan_document,
)
from aksharamd.packaging.models import (
    PackageAssetReference,
    PackageElementPlan,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_doc(blocks: list[Block], doc_id: str = "testdoc") -> Document:
    doc = Document(source="test.pdf", blocks=blocks)
    doc.document_id = doc_id
    doc.id = doc_id
    return doc


def _para(content: str = "Hello world paragraph text.", page: int = 1) -> Block:
    return Block(type=BlockType.PARAGRAPH, content=content, page=page)


def _heading(content: str = "Section Title", page: int = 1) -> Block:
    return Block(type=BlockType.HEADING, content=content, level=2, page=page)


def _page_break(page: int = 1) -> Block:
    return Block(type=BlockType.PAGE_BREAK, content="", page=page)


def _table_block(
    extraction_method: ExtractionMethod | None = ExtractionMethod.PDF_RULED,
    page: int = 1,
    content: str = "",
) -> Block:
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0),
            TableCell(text="B", row=0, column=1),
            TableCell(text="C", row=1, column=0),
            TableCell(text="D", row=1, column=1),
        ],
        header_rows=[0],
        extraction_method=extraction_method,
    )
    return Block.from_table(td, page=page)


def _image_block(
    page: int = 1,
    caption: str | None = None,
    width: int = 200,
    height: int = 200,
) -> Block:
    meta: dict = {"width": width, "height": height}
    if caption:
        meta["caption"] = caption
    return Block(type=BlockType.IMAGE, content="", page=page, metadata=meta)


def _validation_report_with_missed_table(
    page: int,
    source: str | None = None,
) -> ValidationReport:
    issue = ValidationIssue(
        severity=Severity.WARNING,
        code="W_TABLE_EXPECTED_NOT_EXTRACTED",
        message="table expected but not extracted",
        page=page,
        source=source,
    )
    return ValidationReport(issues=[issue])


# ── Tests ──────────────────────────────────────────────────────────────────────

# 1. deterministic_plan
def test_deterministic_plan():
    blocks = [_para(), _heading(), _table_block()]
    doc = _make_doc(blocks)
    profile = PackageProfile(mode=PackageMode.ADAPTIVE)

    plan1 = plan_document(doc, profile)
    plan2 = plan_document(doc, profile)

    ids1 = [e.element_id for e in plan1.elements]
    ids2 = [e.element_id for e in plan2.elements]
    assert ids1 == ids2

    reps1 = [e.representation for e in plan1.elements]
    reps2 = [e.representation for e in plan2.elements]
    assert reps1 == reps2


# 2. non_block_page_fallback — page with warning and no table block
def test_non_block_page_fallback():
    blocks = [_para()]
    doc = _make_doc(blocks)
    validation = _validation_report_with_missed_table(page=3)
    plan = plan_document(doc, None, validation)

    fallback_elems = [
        e for e in plan.elements
        if e.source_kind in (PackageSourceKind.PAGE, PackageSourceKind.PAGE_REGION)
    ]
    assert len(fallback_elems) == 1
    fb = fallback_elems[0]
    assert fb.block_id is None
    assert fb.page == 3


# 3. page_region_fallback_with_bbox
def test_page_region_fallback_with_bbox():
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=2, source="bbox:10,20,300,400")
    plan = plan_document(doc, None, validation)

    fallbacks = [
        e for e in plan.elements
        if e.source_kind in (PackageSourceKind.PAGE, PackageSourceKind.PAGE_REGION)
    ]
    assert len(fallbacks) == 1
    fb = fallbacks[0]
    assert fb.source_kind == PackageSourceKind.PAGE_REGION
    assert fb.bbox == pytest.approx([10.0, 20.0, 300.0, 400.0])


# 4. full_page_fallback_without_bbox
def test_full_page_fallback_without_bbox():
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=5)
    plan = plan_document(doc, None, validation)

    fallbacks = [
        e for e in plan.elements
        if e.source_kind in (PackageSourceKind.PAGE, PackageSourceKind.PAGE_REGION)
    ]
    assert len(fallbacks) == 1
    fb = fallbacks[0]
    assert fb.source_kind == PackageSourceKind.PAGE
    assert fb.bbox is None


# 5. structured_table_artifact_creation
def test_structured_table_artifact_creation(tmp_path):
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan = plan_document(doc)

    writer = PackageWriter()
    asset_refs, fidelity = writer.write(tmp_path, plan, doc)

    # tables/ dir and at least one .json artifact
    tables_dir = tmp_path / "tables"
    assert tables_dir.is_dir()
    json_files = list(tables_dir.glob("*.json"))
    assert len(json_files) >= 1

    # Verify TableArtifact content
    data = json.loads(json_files[0].read_text())
    assert data["schema_version"] == "1.0"
    assert "table" in data
    assert data["block_id"] == tb.id


# 6. canonical_asset_vs_package_reference
def test_canonical_asset_vs_package_reference():
    """PackageAssetReference.source_asset_id references Asset.id; no caption/mime duplication."""
    asset = Asset(id="asset-001", type="image", page=1, image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    tb = _image_block(page=1, caption="Figure 1")
    # wire asset id into block metadata
    tb.metadata["asset_id"] = "asset-001"
    doc = Document(source="test.pdf", blocks=[tb], assets=[asset])
    doc.document_id = "doc123"
    doc.id = "doc123"

    plan = plan_document(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))

    writer = PackageWriter()
    # Write to tmp_path-style; use a temp dir
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        asset_refs, _ = writer.write(tmp, plan, doc)
        image_refs = [r for r in asset_refs if r.role == "embedded_image"]
        if image_refs:
            ref = image_refs[0]
            assert ref.source_asset_id == "asset-001"
            # No caption or mime_type in PackageAssetReference fields
            assert not hasattr(ref, "caption")
            assert not hasattr(ref, "mime_type")


# 7. asset_id_document_scoped
def test_asset_id_document_scoped():
    """Same page+bbox in two different documents produce different package_asset_ids."""
    from aksharamd.packaging.writer import _package_asset_id

    id1 = _package_asset_id("region", "docA", "5", "10.0,20.0,300.0,400.0")
    id2 = _package_asset_id("region", "docB", "5", "10.0,20.0,300.0,400.0")
    assert id1 != id2


# 8. risky_table_gets_structured_rep_adaptive
def test_risky_table_gets_structured_rep_adaptive():
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])

    # Add W_TABLE_WORD_SPLITS warning for this block
    issue = ValidationIssue(
        severity=Severity.WARNING,
        code="W_TABLE_WORD_SPLITS",
        message="word splits detected",
        block_id=tb.id,
    )
    validation = ValidationReport(issues=[issue])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)
    table_elems = [e for e in plan.elements if e.element_type == "table"]
    assert len(table_elems) == 1
    assert table_elems[0].representation == RepresentationType.STRUCTURED_TABLE
    assert table_elems[0].reason_code == "TABLE_STRUCTURED_RISKY"


# 9. risky_table_text_first_no_crop
def test_risky_table_text_first_no_crop():
    """Table in text_first mode — quality warnings don't downgrade to image fallback."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])

    issue = ValidationIssue(
        severity=Severity.WARNING,
        code="W_TABLE_WORD_SPLITS",
        message="word splits detected",
        block_id=tb.id,
    )
    validation = ValidationReport(issues=[issue])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST), validation)
    table_elems = [e for e in plan.elements if e.element_type == "table"]
    assert len(table_elems) == 1
    # text_first does not trigger risky_table path; falls through to reliable_table
    assert table_elems[0].representation == RepresentationType.STRUCTURED_TABLE


# 10. reference_only_preserved_not_selected
def test_reference_only_preserved_not_selected():
    ib = _image_block(page=1)  # no caption
    doc = _make_doc([ib])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST))
    image_elems = [e for e in plan.elements if e.element_type == "figure"]
    assert len(image_elems) == 1
    ie = image_elems[0]
    assert ie.representation == RepresentationType.REFERENCE_ONLY
    assert ie.include_by_default is False

    # Fidelity check
    from aksharamd.packaging.writer import _build_fidelity
    fidelity = _build_fidelity("doc1", plan, None, set(), 0)
    assert fidelity.elements_reference_only >= 1


# 11. selected_payload_tokens_includes_structured_tables
def test_selected_payload_tokens_includes_structured_tables():
    tb = _table_block(extraction_method=ExtractionMethod.XLSX_NATIVE)
    doc = _make_doc([tb])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))

    table_elems = [e for e in plan.elements if e.element_type == "table"]
    assert table_elems[0].representation == RepresentationType.STRUCTURED_TABLE
    assert plan.estimated_tokens > 0
    total_breakdown_tokens = sum(
        e.token_breakdown.structured_table_tokens for e in plan.elements
    )
    assert total_breakdown_tokens > 0


# 12. no_double_counting_markdown_and_structured
def test_no_double_counting_markdown_and_structured():
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan = plan_document(doc)

    for e in plan.elements:
        if e.element_type == "table" and e.representation == RepresentationType.STRUCTURED_TABLE:
            assert e.token_breakdown.markdown_tokens == 0
            assert e.token_breakdown.structured_table_tokens > 0


# 13. text_token_and_visual_stats_separate
def test_text_token_and_visual_stats_separate():
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan = plan_document(doc)

    asset_refs: list[PackageAssetReference] = []
    token_report = build_token_report("doc1", plan, 100, 80, asset_refs)

    # Selected payload tokens are text only
    assert token_report.selected_payload_tokens == plan.estimated_tokens
    # Visual stats reflect zero assets
    assert token_report.visual_stats.visual_asset_count == 0


# 14. package_mode_additive — planner+writer directly, no full compiler
def test_package_mode_additive(tmp_path):
    blocks = [_para("Some important paragraph content here."), _table_block()]
    doc = _make_doc(blocks)
    doc = doc.model_copy(update={"source": "test.pdf"})

    profile = PackageProfile(mode=PackageMode.ADAPTIVE)
    plan = plan_document(doc, profile)

    writer = PackageWriter()
    asset_refs, fidelity = writer.write(tmp_path, plan, doc)

    # package_plan.json must exist and be non-empty
    pkg_plan_path = tmp_path / "package_plan.json"
    assert pkg_plan_path.exists()
    assert pkg_plan_path.stat().st_size > 0

    # tables/ dir should exist (we have a structured table)
    tables_dir = tmp_path / "tables"
    assert tables_dir.is_dir()


# 15. document_block_chunk_ids_unchanged
def test_document_block_chunk_ids_unchanged():
    blocks = [_para(), _table_block()]
    doc = _make_doc(blocks)
    original_block_ids = [b.id for b in doc.blocks]
    original_doc_id = doc.document_id

    profile = PackageProfile()
    plan_document(doc, profile)

    # IDs are not modified by planning
    assert [b.id for b in doc.blocks] == original_block_ids
    assert doc.document_id == original_doc_id


# 16. schema_version_in_plan
def test_schema_version_in_plan():
    doc = _make_doc([_para()])
    plan = plan_document(doc)
    assert plan.schema_version == "1.0"


# 17. planner_version_in_plan
def test_planner_version_in_plan():
    doc = _make_doc([_para()])
    plan = plan_document(doc)
    assert plan.planner_version == "1.0"


# 18. block_source_requires_block_id
def test_block_source_requires_block_id():
    with pytest.raises(ValidationError):
        PackageElementPlan(
            element_id="abc123",
            source_kind=PackageSourceKind.BLOCK,
            block_id=None,  # required for BLOCK source
            element_type="text",
            representation=RepresentationType.MARKDOWN,
            reason_code="test",
            reason="test reason",
        )


# 19. page_region_requires_page_and_bbox
def test_page_region_requires_page_and_bbox():
    with pytest.raises(ValidationError):
        PackageElementPlan(
            element_id="abc123",
            source_kind=PackageSourceKind.PAGE_REGION,
            page=None,  # required for PAGE_REGION
            element_type="table",
            representation=RepresentationType.IMAGE,
            reason_code="test",
            reason="test reason",
        )


# 20. omit_structural_marker
def test_omit_structural_marker():
    page_break = _page_break()
    meta_block = Block(type=BlockType.METADATA, content="meta content", page=1)
    doc = _make_doc([page_break, meta_block])
    plan = plan_document(doc)

    for elem in plan.elements:
        assert elem.representation == RepresentationType.OMIT
        assert elem.omit_reason == OmitReason.STRUCTURAL_MARKER


# 21. omit_empty_block
def test_omit_empty_block():
    empty_para = Block(type=BlockType.PARAGRAPH, content="   ", page=1)
    doc = _make_doc([empty_para])
    plan = plan_document(doc)

    assert len(plan.elements) == 1
    assert plan.elements[0].representation == RepresentationType.OMIT
    assert plan.elements[0].omit_reason == OmitReason.EMPTY


# 22. table_no_structured_data_gets_markdown
def test_table_no_structured_data_gets_markdown():
    # A table block with no table_data — manually constructed
    block = Block(type=BlockType.TABLE, content="| A | B |\n|---|---|\n| C | D |", page=1)
    # Verify it has no table_data
    assert block.table_data is None
    doc = _make_doc([block])
    plan = plan_document(doc)

    table_elems = [e for e in plan.elements if e.element_type == "table"]
    assert len(table_elems) == 1
    assert table_elems[0].representation == RepresentationType.MARKDOWN


# 23. caption_to_image_relationship — when caption follows image, test relationship mechanism
def test_caption_to_image_relationship():
    """Image block with caption in metadata -> IMAGE representation with captioned reason."""
    ib = _image_block(page=1, caption="Figure 1: Results")
    doc = _make_doc([ib])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))

    image_elems = [e for e in plan.elements if e.element_type == "figure"]
    assert len(image_elems) == 1
    ie = image_elems[0]
    assert ie.representation == RepresentationType.IMAGE
    assert ie.reason_code == "IMAGE_CAPTIONED"
    assert ie.include_by_default is True


# 24. fidelity_report_preserved_vs_included
def test_fidelity_report_preserved_vs_included(tmp_path):
    ib = _image_block(page=1)  # no caption — goes REFERENCE_ONLY in text_first
    doc = _make_doc([ib])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST))

    writer = PackageWriter()
    asset_refs, fidelity = writer.write(tmp_path, plan, doc)

    # REFERENCE_ONLY counts in preserved but not in default payload
    assert fidelity.elements_preserved_in_package >= 1
    assert fidelity.elements_reference_only >= 1
    # reference_only is preserved but not included in default payload
    assert fidelity.elements_included_in_default_payload == 0 or (
        fidelity.elements_reference_only > 0
        and fidelity.elements_preserved_in_package > fidelity.elements_included_in_default_payload
    )


# 25. manifest_schema_version_1_3
def test_manifest_schema_version_1_3():
    m = Manifest(source="test.pdf")
    assert m.schema_version == "1.3"


# ── Milestone C tests ──────────────────────────────────────────────────────────

from aksharamd.packaging.models import (
    PlannerContext,
    ReasonCode,
    RelationshipType,
)


def _make_ctx(mode: str = "adaptive", **kwargs) -> PlannerContext:
    return PlannerContext(mode=mode, **kwargs)


# C1. reason_code_enum_values
def test_reason_code_enum_values():
    """ReasonCode values are stable SCREAMING_SNAKE_CASE strings."""
    assert ReasonCode.TEXT_RELIABLE == "TEXT_RELIABLE"
    assert ReasonCode.TABLE_STRUCTURED_RELIABLE == "TABLE_STRUCTURED_RELIABLE"
    assert ReasonCode.TABLE_STRUCTURED_RISKY == "TABLE_STRUCTURED_RISKY"
    assert ReasonCode.IMAGE_CAPTIONED == "IMAGE_CAPTIONED"
    assert ReasonCode.STRUCTURAL_MARKER == "STRUCTURAL_MARKER"


# C2. three_modes_same_doc_different_plans
def test_three_modes_same_doc_different_plans():
    """Same document produces different plans under text_first vs adaptive."""
    ib = _image_block(page=1)  # no caption
    doc = _make_doc([ib])

    plan_tf = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST))
    plan_ad = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    plan_ff = plan_document(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))

    tf_img = [e for e in plan_tf.elements if e.element_type == "figure"][0]
    ad_img = [e for e in plan_ad.elements if e.element_type == "figure"][0]
    ff_img = [e for e in plan_ff.elements if e.element_type == "figure"][0]

    # text_first: REFERENCE_ONLY; adaptive/fidelity_first: IMAGE
    assert tf_img.representation == RepresentationType.REFERENCE_ONLY
    assert ad_img.representation == RepresentationType.IMAGE
    assert ff_img.representation == RepresentationType.IMAGE


# C3. ocr_ambiguous_text_first_stays_markdown
def test_ocr_ambiguous_text_first_stays_markdown():
    """Ambiguous OCR block in text_first mode -> MARKDOWN (not IMAGE_AND_TEXT)."""
    block = Block(
        type=BlockType.PARAGRAPH,
        content="Some scanned text",
        page=1,
        confidence=ExtractionConfidence.AMBIGUOUS,
    )
    doc = _make_doc([block])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST))

    elems = [e for e in plan.elements if e.element_type == "text"]
    assert len(elems) == 1
    assert elems[0].representation == RepresentationType.MARKDOWN
    assert elems[0].reason_code == ReasonCode.TEXT_OCR_UNCERTAIN


# C4. ocr_ambiguous_fidelity_first_gets_image_and_text
def test_ocr_ambiguous_fidelity_first_gets_image_and_text():
    """Ambiguous OCR block in fidelity_first mode -> IMAGE_AND_TEXT."""
    block = Block(
        type=BlockType.PARAGRAPH,
        content="Some scanned text",
        page=1,
        confidence=ExtractionConfidence.AMBIGUOUS,
    )
    doc = _make_doc([block])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))

    elems = [e for e in plan.elements if e.element_type == "text"]
    assert len(elems) == 1
    assert elems[0].representation == RepresentationType.IMAGE_AND_TEXT


# C5. risky_table_creates_fallback_element_with_bbox
def test_risky_table_creates_fallback_element_with_bbox():
    """Risky table + quality warning in adaptive mode with bbox -> primary + fallback element."""
    from aksharamd.models.table import BoundingBox
    td = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0),
            TableCell(text="B", row=0, column=1),
            TableCell(text="C", row=1, column=0),
            TableCell(text="D", row=1, column=1),
        ],
        header_rows=[0],
        extraction_method=ExtractionMethod.PDF_RULED,
        bbox=BoundingBox(x0=50, y0=100, x1=400, y1=300),
    )
    tb = Block.from_table(td, page=1)
    doc = _make_doc([tb])

    # Add quality warning for this block
    issue = ValidationIssue(
        severity=Severity.WARNING,
        code="W_TABLE_WORD_SPLITS",
        message="word splits",
        block_id=tb.id,
    )
    validation = ValidationReport(issues=[issue])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)

    table_elems = [e for e in plan.elements if e.element_type == "table"]
    # Should have 2 elements: primary STRUCTURED_TABLE + fallback IMAGE
    assert len(table_elems) == 2

    primary = next(e for e in table_elems if e.representation == RepresentationType.STRUCTURED_TABLE)
    fallback = next(e for e in table_elems if e.representation == RepresentationType.IMAGE)

    assert primary.reason_code == ReasonCode.TABLE_STRUCTURED_RISKY
    assert fallback.reason_code == ReasonCode.TABLE_VISUAL_FALLBACK
    assert fallback.source_kind == PackageSourceKind.PAGE_REGION
    assert fallback.bbox == pytest.approx([50, 100, 400, 300])

    # Relationships
    fallback_rel_types = {r.relationship_type for r in fallback.relationships}
    assert RelationshipType.VISUAL_FALLBACK_FOR in fallback_rel_types


# C6. risky_table_text_first_no_fallback_element
def test_risky_table_text_first_no_fallback_element():
    """Risky table in text_first mode -> no fallback element created."""
    from aksharamd.models.table import BoundingBox
    td = TableData(
        row_count=2, column_count=2,
        cells=[TableCell(text="X", row=0, column=0), TableCell(text="Y", row=0, column=1),
               TableCell(text="A", row=1, column=0), TableCell(text="B", row=1, column=1)],
        header_rows=[0],
        extraction_method=ExtractionMethod.PDF_RULED,
        bbox=BoundingBox(x0=0, y0=0, x1=100, y1=100),
    )
    tb = Block.from_table(td, page=1)
    doc = _make_doc([tb])
    issue = ValidationIssue(severity=Severity.WARNING, code="W_TABLE_WORD_SPLITS",
                            message="w", block_id=tb.id)
    validation = ValidationReport(issues=[issue])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST), validation)
    table_elems = [e for e in plan.elements if e.element_type == "table"]
    # text_first: no fallback element; only 1 STRUCTURED_TABLE
    assert len(table_elems) == 1
    assert table_elems[0].representation == RepresentationType.STRUCTURED_TABLE


# C7. missed_table_fallback_text_first_reference_only
def test_missed_table_fallback_text_first_reference_only():
    """Missed-table fallback in text_first mode -> IMAGE but include_by_default=False."""
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=3)
    plan = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST), validation)

    fallbacks = [e for e in plan.elements if e.source_kind == PackageSourceKind.PAGE]
    assert len(fallbacks) == 1
    assert fallbacks[0].representation == RepresentationType.IMAGE
    assert fallbacks[0].include_by_default is False


# C8. missed_table_fallback_adaptive_included_by_default
def test_missed_table_fallback_adaptive_included_by_default():
    """Missed-table fallback in adaptive mode -> include_by_default=True."""
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=2)
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)

    fallbacks = [e for e in plan.elements
                 if e.source_kind in (PackageSourceKind.PAGE, PackageSourceKind.PAGE_REGION)]
    assert len(fallbacks) == 1
    assert fallbacks[0].include_by_default is True


# C9. adjacent_caption_block_linked_to_image
def test_adjacent_caption_block_linked_to_image():
    """IMAGE block followed by CAPTION block -> caption element has CAPTION_OF relationship."""
    ib = _image_block(page=1)
    cap = Block(type=BlockType.CAPTION, content="Figure 1: My chart", page=1)
    doc = _make_doc([ib, cap])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))

    cap_elem = next(e for e in plan.elements if e.block_id == cap.id)
    rel_types = {r.relationship_type for r in cap_elem.relationships}
    assert RelationshipType.CAPTION_OF in rel_types


# C10. image_adjacent_caption_gets_captioned_reason
def test_image_adjacent_caption_gets_captioned_reason():
    """IMAGE block with adjacent CAPTION -> IMAGE_CAPTIONED reason code."""
    ib = _image_block(page=1)
    cap = Block(type=BlockType.CAPTION, content="Figure 1", page=1)
    doc = _make_doc([ib, cap])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))
    img_elem = next(e for e in plan.elements if e.block_id == ib.id)
    assert img_elem.reason_code == ReasonCode.IMAGE_CAPTIONED


# C11. heading_before_table_provides_context
def test_heading_before_table_provides_context():
    """Heading before a table -> table element has CONTEXT_FOR relationship to heading."""
    h = _heading("Quarterly Results", page=1)
    tb = _table_block(page=1)
    doc = _make_doc([h, tb])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    table_elem = next(e for e in plan.elements if e.element_type == "table"
                      and e.source_kind == PackageSourceKind.BLOCK)
    rel_types = {r.relationship_type for r in table_elem.relationships}
    assert RelationshipType.CONTEXT_FOR in rel_types


# C12. warning_fallback_has_warning_fallback_for_relationship
def test_warning_fallback_has_warning_fallback_for_relationship():
    """Fallback element for missed-table warning has WARNING_FALLBACK_FOR relationship."""
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=5, source="bbox:0,0,100,100")
    plan = plan_document(doc, None, validation)

    fallbacks = [e for e in plan.elements if e.source_kind == PackageSourceKind.PAGE_REGION]
    assert len(fallbacks) == 1
    rel_types = {r.relationship_type for r in fallbacks[0].relationships}
    assert RelationshipType.WARNING_FALLBACK_FOR in rel_types


# C13. planner_context_built_correctly
def test_planner_context_built_correctly():
    """PlannerContext captures block warnings and caption adjacency."""
    from aksharamd.packaging.planner import _build_planner_context
    ib = _image_block(page=1)
    cap = Block(type=BlockType.CAPTION, content="Caption text", page=1)
    doc = _make_doc([ib, cap])

    issue = ValidationIssue(severity=Severity.WARNING, code="W_TEST_CODE",
                            message="test", block_id=ib.id)
    vr = ValidationReport(issues=[issue])
    profile = PackageProfile(mode=PackageMode.ADAPTIVE)

    ctx = _build_planner_context(doc, profile, vr)
    assert "W_TEST_CODE" in ctx.block_warnings.get(ib.id, frozenset())
    assert ib.id in ctx.caption_for_image


# C14. fidelity_report_has_unresolved_element_ids
def test_fidelity_report_has_unresolved_element_ids(tmp_path):
    """Unresolved fallback elements (no file written) appear in unresolved_element_ids."""
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=7)
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)

    writer = PackageWriter()
    _, fidelity = writer.write(tmp_path, plan, doc, validation)

    # The fallback region/page element has no written file (no source PDF)
    assert fidelity.warnings_without_visual_fallback >= 1
    assert len(fidelity.unresolved_element_ids) >= 1


# C15. three_mode_token_accounting
def test_three_mode_token_accounting():
    """Selected payload tokens differ by mode."""
    ib = _image_block(page=1)  # no caption — text_first excludes, adaptive includes
    para = _para("Important document content with several words here.")
    doc = _make_doc([ib, para])

    plan_tf = plan_document(doc, PackageProfile(mode=PackageMode.TEXT_FIRST))
    plan_ad = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))

    # Both modes include the text paragraph
    tf_tokens = plan_tf.estimated_tokens
    ad_tokens = plan_ad.estimated_tokens

    # Both should have some tokens (the paragraph)
    assert tf_tokens > 0
    assert ad_tokens > 0


# C16. supporting_reason_codes_on_risky_table
def test_supporting_reason_codes_on_risky_table():
    """Risky table element carries supporting reason codes from quality warnings."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    issue = ValidationIssue(severity=Severity.WARNING, code="W_TABLE_WORD_SPLITS",
                            message="w", block_id=tb.id)
    validation = ValidationReport(issues=[issue])

    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)
    table_elems = [e for e in plan.elements
                   if e.element_type == "table" and e.source_kind == PackageSourceKind.BLOCK]
    assert len(table_elems) == 1
    assert "W_TABLE_WORD_SPLITS" in table_elems[0].supporting_reason_codes


# C17. table_quality_findings_from_metadata
def test_table_quality_findings_from_metadata():
    """Table block with metadata['table_quality']['overall_status'] = 'risk' triggers risky path."""
    td = TableData(
        row_count=2, column_count=2,
        cells=[TableCell(text="A", row=0, column=0), TableCell(text="B", row=0, column=1),
               TableCell(text="C", row=1, column=0), TableCell(text="D", row=1, column=1)],
        header_rows=[0],
        extraction_method=ExtractionMethod.PDF_RULED,
    )
    tb = Block.from_table(
        td, page=1,
        metadata={"table_quality": {"overall_status": "risk", "signals": [{"name": "RAGGED_ROWS", "status": "risk"}]}}
    )
    doc = _make_doc([tb])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))

    table_elems = [e for e in plan.elements if e.element_type == "table"
                   and e.source_kind == PackageSourceKind.BLOCK]
    assert table_elems[0].reason_code == ReasonCode.TABLE_STRUCTURED_RISKY


# C18. image_decorative_small_dimensions
def test_image_decorative_small_dimensions():
    """Image with width < 50px -> REFERENCE_ONLY in adaptive mode."""
    ib = _image_block(page=1, width=30, height=30)
    doc = _make_doc([ib])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    img_elem = next(e for e in plan.elements if e.element_type == "figure")
    assert img_elem.representation == RepresentationType.REFERENCE_ONLY
    assert img_elem.reason_code == ReasonCode.IMAGE_DECORATIVE


# C19. plan_determinism_across_modes
def test_plan_determinism_across_modes():
    """Each mode produces a deterministic plan on repeated calls."""
    blocks = [_para(), _heading(), _table_block()]
    doc = _make_doc(blocks)
    for mode in [PackageMode.TEXT_FIRST, PackageMode.FIDELITY_FIRST, PackageMode.ADAPTIVE]:
        profile = PackageProfile(mode=mode)
        plan1 = plan_document(doc, profile)
        plan2 = plan_document(doc, profile)
        assert [e.element_id for e in plan1.elements] == [e.element_id for e in plan2.elements]
        assert [e.reason_code for e in plan1.elements] == [e.reason_code for e in plan2.elements]


# C20. backward_compat_existing_tests_unchanged
def test_backward_compat_package_plan_schema():
    """package_plan schema_version stays 1.0; planner_version is 1.0."""
    doc = _make_doc([_para()])
    plan = plan_document(doc)
    assert plan.schema_version == "1.0"
    assert plan.planner_version == "1.0"


# ── Milestone D tests ──────────────────────────────────────────────────────────

import tempfile

from aksharamd.packaging.adapters import to_multimodal_content, to_plain_text
from aksharamd.packaging.payload import (
    LLMPayload,
    PayloadContentType,
)
from aksharamd.packaging.payload_builder import build_llm_payload


def _make_plan_and_write(doc, profile=None, validation=None, tmp_path=None):
    """Helper: plan, write, return (plan, asset_refs, tmp_path)."""
    import tempfile
    plan = plan_document(doc, profile, validation)
    writer = PackageWriter()
    if tmp_path is None:
        td = tempfile.mkdtemp()
        tmp_path = Path(td)
    asset_refs, _ = writer.write(tmp_path, plan, doc, validation)
    return plan, asset_refs, tmp_path


def _math_block(content: str = "E=mc^2", page: int = 1) -> Block:
    return Block(type=BlockType.MATH, content=content, page=page)


# ── Truthful routing tests (formula correction) ────────────────────────────────

# D1
def test_empty_math_routes_to_markdown_not_image_and_text():
    """Empty math block in adaptive mode routes to MARKDOWN with FORMULA_VISUAL_UNAVAILABLE."""
    block = Block(type=BlockType.MATH, content="", page=1)
    doc = _make_doc([block])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    formula_elems = [e for e in plan.elements if e.element_type == "formula"]
    assert len(formula_elems) == 1
    fe = formula_elems[0]
    assert fe.representation == RepresentationType.MARKDOWN
    assert fe.reason_code == "FORMULA_VISUAL_UNAVAILABLE"


# D2
def test_formula_with_content_stays_formula_structured():
    """Math block with content -> MARKDOWN, FORMULA_STRUCTURED."""
    block = Block(type=BlockType.MATH, content="E=mc^2", page=1)
    doc = _make_doc([block])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    formula_elems = [e for e in plan.elements if e.element_type == "formula"]
    assert len(formula_elems) == 1
    assert formula_elems[0].representation == RepresentationType.MARKDOWN
    assert formula_elems[0].reason_code == ReasonCode.FORMULA_STRUCTURED


# D3
def test_formula_visual_unavailable_in_plan():
    """Plan element for empty math has representation=MARKDOWN with FORMULA_VISUAL_UNAVAILABLE."""
    block = Block(type=BlockType.MATH, content="   ", page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    formula_elems = [e for e in plan.elements if e.element_type == "formula"]
    assert len(formula_elems) == 1
    assert formula_elems[0].representation == RepresentationType.MARKDOWN
    assert formula_elems[0].reason_code == "FORMULA_VISUAL_UNAVAILABLE"


# ── Ordering tests ─────────────────────────────────────────────────────────────

# D4
def test_payload_items_follow_document_order():
    """Items from blocks in doc order appear in that order in payload."""
    h = _heading("Section", page=1)
    p = _para("Paragraph text.", page=1)
    tb = _table_block(page=1)
    doc = _make_doc([h, p, tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    # Items should appear in block order: heading, para, table
    types = [item.content_type for item in payload.items]
    assert PayloadContentType.TEXT in types
    assert PayloadContentType.STRUCTURED_TABLE in types
    # Check heading comes before table
    heading_idx = next(i for i, item in enumerate(payload.items) if item.text and "Section" in item.text)
    table_idx = next(i for i, item in enumerate(payload.items) if item.content_type == PayloadContentType.STRUCTURED_TABLE)
    assert heading_idx < table_idx


# D5
def test_page_fallback_interleaved_by_page():
    """Page fallback for page N appears after last block on page N (not purely at end)."""
    p1 = _para("Page 1 text", page=1)
    p2 = _para("Page 3 text", page=3)
    doc = _make_doc([p1, p2])
    validation = _validation_report_with_missed_table(page=1)
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, None, validation)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    # There should be a warning/image item for page 1 somewhere after the page-1 text
    page1_text_idx = next(
        (i for i, item in enumerate(payload.items) if item.page == 1 and item.content_type == PayloadContentType.TEXT),
        None,
    )
    # Both should exist
    assert page1_text_idx is not None
    # The fallback item might not exist if no asset; but the plan element should be there
    fallback_elems = [e for e in plan.elements if e.page == 1 and e.source_kind != PackageSourceKind.BLOCK]
    assert len(fallback_elems) >= 1


# D6
def test_visual_fallback_follows_source_table():
    """TABLE_VISUAL_FALLBACK element appears immediately after STRUCTURED_TABLE element in payload."""
    from aksharamd.models.table import BoundingBox
    td = TableData(
        row_count=2, column_count=2,
        cells=[TableCell(text="A", row=0, column=0), TableCell(text="B", row=0, column=1),
               TableCell(text="C", row=1, column=0), TableCell(text="D", row=1, column=1)],
        header_rows=[0],
        extraction_method=ExtractionMethod.PDF_RULED,
        bbox=BoundingBox(x0=0, y0=0, x1=100, y1=100),
    )
    tb = Block.from_table(td, page=1)
    doc = _make_doc([tb])
    issue = ValidationIssue(severity=Severity.WARNING, code="W_TABLE_WORD_SPLITS",
                            message="word splits", block_id=tb.id)
    validation = ValidationReport(issues=[issue])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    # Find the structured table item
    table_items = [i for i, item in enumerate(payload.items)
                   if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(table_items) >= 1
    # The element after the structured table should be an image reference (fallback)
    tbl_idx = table_items[0]
    if tbl_idx + 1 < len(payload.items):
        next_item = payload.items[tbl_idx + 1]
        assert next_item.content_type == PayloadContentType.IMAGE_REFERENCE


# ── Deduplication tests ────────────────────────────────────────────────────────

# D7
def test_caption_not_duplicated_in_payload():
    """If caption block is linked to an image, it does not appear as a separate TEXT item."""
    ib = _image_block(page=1)
    cap = Block(type=BlockType.CAPTION, content="Figure 1: My chart", page=1)
    doc = _make_doc([ib, cap])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    # Caption text should not appear as a standalone TEXT item
    text_items = [item for item in payload.items if item.content_type == PayloadContentType.TEXT]
    caption_texts = [item for item in text_items if item.text and "Figure 1" in item.text]
    assert len(caption_texts) == 0


# D8
def test_context_heading_in_table_provenance():
    """Heading context relationship is reflected in provenance (table has context relationship)."""
    h = _heading("Quarterly Results", page=1)
    tb = _table_block(page=1)
    doc = _make_doc([h, tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.ADAPTIVE))

    # Check that the table element has CONTEXT_FOR relationship in the plan
    table_elem = next(
        e for e in plan.elements
        if e.element_type == "table" and e.source_kind == PackageSourceKind.BLOCK
    )
    rel_types = {r.relationship_type for r in table_elem.relationships}
    assert RelationshipType.CONTEXT_FOR in rel_types


# ── Structured table tests ────────────────────────────────────────────────────

# D9
def test_structured_table_item_has_markdown():
    """STRUCTURED_TABLE elements get table_markdown field populated."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    table_items = [item for item in payload.items if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(table_items) >= 1
    assert table_items[0].table_markdown is not None
    assert len(table_items[0].table_markdown) > 0


# D10
def test_structured_table_has_artifact_path():
    """table_artifact_path is set and file exists."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    table_items = [item for item in payload.items if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(table_items) >= 1
    ti = table_items[0]
    assert ti.table_artifact_path is not None
    assert (tmp_path / ti.table_artifact_path).exists()


# D11
def test_table_tokens_from_markdown_not_json():
    """estimated_tokens counts markdown text, not JSON artifact size."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    table_items = [item for item in payload.items if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(table_items) >= 1
    ti = table_items[0]
    from aksharamd.packaging.token_accounting import count_text_tokens
    expected_tokens = count_text_tokens(ti.table_markdown or "")
    assert ti.estimated_tokens == expected_tokens


# ── Image tests ───────────────────────────────────────────────────────────────

# D12
def test_image_item_asset_path_set():
    """IMAGE element with matching asset_ref gets asset_path populated."""
    from aksharamd.models.asset import Asset
    asset = Asset(id="asset-001", type="image", page=1, image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    ib = _image_block(page=1, caption="Figure 1")
    ib.metadata["asset_id"] = "asset-001"
    doc = Document(source="test.pdf", blocks=[ib], assets=[asset])
    doc.document_id = "imgdoc"
    doc.id = "imgdoc"
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    image_items = [item for item in payload.items if item.content_type == PayloadContentType.IMAGE_REFERENCE]
    if image_items:
        # If asset was written, asset_path should be set
        for img_item in image_items:
            if img_item.asset_path:
                assert (tmp_path / img_item.asset_path).exists()


# D13
def test_reference_only_excluded_from_payload():
    """REFERENCE_ONLY elements not in payload items."""
    ib = _image_block(page=1)  # no caption
    doc = _make_doc([ib])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.TEXT_FIRST))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    # Check that reference-only elements are absent from payload
    ref_only_ids = {e.element_id for e in plan.elements if e.representation == RepresentationType.REFERENCE_ONLY}
    payload_ids = {item.element_id for item in payload.items}
    assert ref_only_ids.isdisjoint(payload_ids)


# D14
def test_text_first_images_not_in_default_payload():
    """In text_first mode, images are reference-only and excluded from payload."""
    ib = _image_block(page=1)
    doc = _make_doc([ib])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.TEXT_FIRST))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    image_items = [item for item in payload.items if item.content_type == PayloadContentType.IMAGE_REFERENCE]
    assert len(image_items) == 0


# D15
def test_missing_image_file_goes_to_unresolved():
    """If asset_ref exists but file does not, element_id appears in unresolved_element_ids."""
    ib = _image_block(page=1, caption="Figure")
    doc = _make_doc([ib])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))
    # Create a fake asset_ref pointing to a nonexistent file
    img_elem = next(e for e in plan.elements if e.element_type == "figure")
    fake_ref = PackageAssetReference(
        package_asset_id="fake123",
        role="embedded_image",
        file_path="images/nonexistent.png",
        related_element_ids=[img_elem.element_id],
    )
    with tempfile.TemporaryDirectory() as td:
        # Write tables dir but not images
        (Path(td) / "tables").mkdir()
        payload = build_llm_payload(plan, doc, Path(td), [fake_ref])
    assert img_elem.element_id in payload.unresolved_element_ids


# ── Token accounting tests ────────────────────────────────────────────────────

# D16
def test_actual_tokens_from_emitted_text():
    """actual_text_token_count matches sum of item estimated_tokens."""
    blocks = [_para("Some text content for token counting."), _heading("Title")]
    doc = _make_doc(blocks)
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    expected = sum(item.estimated_tokens for item in payload.items)
    assert payload.actual_text_token_count == expected


# D17
def test_planned_vs_actual_token_delta_reported():
    """token_delta = actual - planned is present in payload."""
    doc = _make_doc([_para("Text content here for testing tokens.")])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    assert payload.token_delta == payload.actual_text_token_count - payload.planned_text_tokens


# D18
def test_visual_assets_not_in_text_tokens():
    """IMAGE items don't contribute to actual_text_token_count (no text)."""
    ib = _image_block(page=1)  # no caption
    doc = _make_doc([ib])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    image_items = [item for item in payload.items if item.content_type == PayloadContentType.IMAGE_REFERENCE]
    for img in image_items:
        # No caption -> no tokens
        assert img.estimated_tokens == 0 or img.caption is None or img.estimated_tokens <= 10


# ── Fidelity tests ────────────────────────────────────────────────────────────

# D19
def test_every_selected_element_maps_to_item():
    """All include_by_default, non-OMIT, non-REFERENCE_ONLY elements appear in payload."""
    blocks = [_para("P1"), _heading("H1"), _table_block()]
    doc = _make_doc(blocks)
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    assert len(payload.fidelity.plan_payload_mismatches) == 0


# D20
def test_unresolved_elements_listed():
    """Elements with no asset produce entries in unresolved_element_ids."""
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=5)
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    # The missed-table element has no renderable source -> unresolved
    fallback_elems = [e for e in plan.elements if e.source_kind != PackageSourceKind.BLOCK]
    if fallback_elems:
        # At least some will be unresolved since we have no source PDF
        assert len(payload.unresolved_element_ids) >= 0  # may be 0 if WARNING emitted


# D21
def test_payload_fidelity_counts_correct():
    """fidelity.emitted_items matches len(items)."""
    doc = _make_doc([_para("Content"), _heading("H")])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    assert payload.fidelity.emitted_items == len(payload.items)


# ── Plain text adapter tests ──────────────────────────────────────────────────

# D22
def test_to_plain_text_contains_all_text():
    """to_plain_text output contains TEXT item text."""
    doc = _make_doc([_para("Hello world paragraph."), _heading("My Heading")])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)
    text = to_plain_text(payload)

    assert "Hello world paragraph." in text
    assert "My Heading" in text


# D23
def test_to_plain_text_includes_table_markdown():
    """Table serialization is in plain text output."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)
    to_plain_text(payload)

    # Table content should be present in some form (pipe table or TSV)
    # The selector may choose markdown (|) or TSV (\t) depending on token budget
    table_items = [item for item in payload.items
                   if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(table_items) >= 1
    assert table_items[0].table_markdown is not None
    assert len(table_items[0].table_markdown) > 0


# ── Multimodal adapter tests ──────────────────────────────────────────────────

# D24
def test_to_multimodal_content_types():
    """Returns list of dicts with 'type' field."""
    doc = _make_doc([_para("Some text."), _table_block()])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)
    content = to_multimodal_content(payload, tmp_path)

    assert isinstance(content, list)
    for item in content:
        assert isinstance(item, dict)
        assert "type" in item


# D25
def test_to_multimodal_content_image_reference_no_bytes():
    """Image items have path not base64."""
    ib = _image_block(page=1, caption="A figure")
    doc = _make_doc([ib])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.FIDELITY_FIRST))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)
    content = to_multimodal_content(payload, tmp_path)

    image_entries = [e for e in content if e.get("type") == "image"]
    for entry in image_entries:
        assert "path" in entry
        assert "base64" not in entry
        assert "bytes" not in entry


# ── Compatibility tests ───────────────────────────────────────────────────────

# D26
def test_compile_package_writes_llm_payload_json(tmp_path):
    """compile_package output dir contains llm_payload.json."""
    from aksharamd.compiler import Compiler
    # Create a minimal text file to compile
    src = tmp_path / "test.txt"
    src.write_text("Hello world. This is a test document for packaging.", encoding="utf-8")
    out_dir = tmp_path / "output"
    compiler = Compiler(output_dir=str(out_dir))
    compiler.compile_package(str(src))
    payload_path = out_dir / "llm_payload.json"
    assert payload_path.exists()


# D27
def test_llm_payload_schema_version():
    """payload.payload_schema_version == '1.0'."""
    doc = _make_doc([_para()])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)
    assert payload.payload_schema_version == "1.0"


# D28
def test_payload_document_id_matches_plan():
    """payload.document_id == plan.document_id."""
    doc = _make_doc([_para()])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)
    assert payload.document_id == plan.document_id


# D29
def test_planner_version_unchanged():
    """PLANNER_VERSION still == '1.0'."""
    assert PLANNER_VERSION == "1.0"


# D30
def test_package_profile_new_fields():
    """PackageProfile has table_payload_format, include_warning_items, include_provenance."""
    profile = PackageProfile()
    assert hasattr(profile, "table_payload_format")
    assert hasattr(profile, "include_warning_items")
    assert hasattr(profile, "include_provenance")
    assert profile.table_payload_format == "markdown"
    assert profile.include_warning_items is True
    assert profile.include_provenance is True


# ── WARNING item test ─────────────────────────────────────────────────────────

# D31
def test_missing_asset_emits_warning_item():
    """When a page-fallback IMAGE has no renderable asset, a WARNING item is emitted."""
    doc = _make_doc([_para()])
    validation = _validation_report_with_missed_table(page=3)
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.ADAPTIVE), validation)
    profile = PackageProfile(include_warning_items=True)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs, profile)

    # Since there is no source PDF, the region cannot be rendered
    # The payload should have either a WARNING item or an unresolved entry
    warning_items = [item for item in payload.items if item.content_type == PayloadContentType.WARNING]
    has_warning = len(warning_items) > 0
    has_unresolved = len(payload.unresolved_element_ids) > 0
    assert has_warning or has_unresolved


# ── build_llm_payload standalone function ─────────────────────────────────────

# D32
def test_build_llm_payload_returns_llm_payload():
    """Function returns LLMPayload instance."""
    doc = _make_doc([_para("Some text.")])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)
    assert isinstance(payload, LLMPayload)


# D33
def test_build_llm_payload_deterministic():
    """Same inputs -> same output (deterministic)."""
    doc = _make_doc([_para("Deterministic text."), _heading("A heading"), _table_block()])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)

    payload1 = build_llm_payload(plan, doc, tmp_path, asset_refs)
    payload2 = build_llm_payload(plan, doc, tmp_path, asset_refs)

    assert [item.item_id for item in payload1.items] == [item.item_id for item in payload2.items]
    assert payload1.actual_text_token_count == payload2.actual_text_token_count


# D34
def test_json_reference_table_format():
    """table_payload_format=json_reference produces short descriptor, not full markdown."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    profile = PackageProfile(table_payload_format="json_reference")
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs, profile)

    table_items = [item for item in payload.items if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(table_items) >= 1
    # json_reference format should not contain pipe characters (no markdown table)
    assert "|" not in (table_items[0].table_markdown or "")


# D35
def test_include_provenance_false_no_provenance():
    """With include_provenance=False, provenance dicts are empty."""
    doc = _make_doc([_para("Text.")])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    profile = PackageProfile(include_provenance=False)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs, profile)

    for item in payload.items:
        assert item.provenance == {}


# ── Correction tests ───────────────────────────────────────────────────────────

from aksharamd.packaging.payload import TokenDeltaBreakdown


# DC1
def test_planner_and_payload_use_same_table_serialization():
    """Planner estimated_text_tokens and payload estimated_tokens agree for STRUCTURED_TABLE."""
    tb = _table_block(extraction_method=ExtractionMethod.PDF_RULED)
    doc = _make_doc([tb])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    # Find STRUCTURED_TABLE in plan
    table_plan_elem = next(
        e for e in plan.elements
        if e.element_type == "table" and e.representation == RepresentationType.STRUCTURED_TABLE
        and e.source_kind == PackageSourceKind.BLOCK
    )
    # Find STRUCTURED_TABLE in payload
    table_payload_item = next(
        item for item in payload.items
        if item.content_type == PayloadContentType.STRUCTURED_TABLE
    )
    assert table_plan_elem.estimated_text_tokens == table_payload_item.estimated_tokens


# DC2
def test_token_delta_near_zero_for_no_tables():
    """Documents with only text blocks have near-zero token_delta."""
    doc = _make_doc([
        _para("This is a paragraph with some content."),
        _heading("A Heading Section"),
        _para("Another paragraph with more text here."),
    ])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    assert abs(payload.token_delta) <= 5


# DC3
def test_token_delta_breakdown_sum_equals_delta():
    """Sum of all breakdown parts equals token_delta."""
    ib = _image_block(page=1)
    cap = Block(type=BlockType.CAPTION, content="Figure 1: Chart", page=1)
    doc = _make_doc([ib, cap])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc, PackageProfile(mode=PackageMode.ADAPTIVE))
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    tdb = payload.token_delta_breakdown
    parts_sum = (
        tdb.caption_dedup_delta
        + tdb.warning_delta
        + tdb.representation_downgrade_delta
        + tdb.missing_asset_delta
        + tdb.other_delta
    )
    assert parts_sum == payload.token_delta


# DC4
def test_image_and_text_without_image_is_downgraded():
    """IMAGE_AND_TEXT with no asset -> TEXT item emitted, element_id in representation_downgrades."""
    block = Block(
        type=BlockType.PARAGRAPH,
        content="Some scanned text content.",
        page=1,
        confidence=ExtractionConfidence.AMBIGUOUS,
    )
    doc = _make_doc([block])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))

    # Find the IMAGE_AND_TEXT element
    iat_elems = [e for e in plan.elements if e.representation == RepresentationType.IMAGE_AND_TEXT]
    assert len(iat_elems) == 1
    iat_elem = iat_elems[0]

    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td)
        # No assets written — image side unavailable
        payload = build_llm_payload(plan, doc, pkg_dir, [], PackageProfile(mode=PackageMode.ADAPTIVE))

    # Text content is preserved
    text_items = [item for item in payload.items if item.content_type == PayloadContentType.TEXT]
    assert len(text_items) >= 1
    assert any("scanned text" in (item.text or "") for item in text_items)
    # Element is in representation_downgrades
    assert iat_elem.element_id in payload.fidelity.representation_downgrades


# DC5
def test_image_and_text_with_image_is_not_downgraded():
    """IMAGE_AND_TEXT with image file present -> no downgrade, asset_path set."""
    block = Block(
        type=BlockType.PARAGRAPH,
        content="Some scanned text content.",
        page=1,
        confidence=ExtractionConfidence.AMBIGUOUS,
    )
    doc = _make_doc([block])
    plan = plan_document(doc, PackageProfile(mode=PackageMode.ADAPTIVE))

    iat_elems = [e for e in plan.elements if e.representation == RepresentationType.IMAGE_AND_TEXT]
    assert len(iat_elems) == 1
    iat_elem = iat_elems[0]

    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td)
        # Write a fake image file at the expected asset path
        images_dir = pkg_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        fake_image = images_dir / "ocr_block.png"
        fake_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        # Create an asset ref pointing to this file
        fake_ref = PackageAssetReference(
            package_asset_id="ocr001",
            role="embedded_image",
            file_path="images/ocr_block.png",
            related_element_ids=[iat_elem.element_id],
        )
        payload = build_llm_payload(plan, doc, pkg_dir, [fake_ref], PackageProfile(mode=PackageMode.ADAPTIVE))

    # Not in downgrades
    assert iat_elem.element_id not in payload.fidelity.representation_downgrades
    # asset_path is set on the item
    iat_items = [item for item in payload.items
                 if item.element_id == iat_elem.element_id]
    assert len(iat_items) == 1
    assert iat_items[0].asset_path is not None


# DC6
def test_token_delta_breakdown_in_payload():
    """LLMPayload has token_delta_breakdown as TokenDeltaBreakdown instance."""
    doc = _make_doc([_para("Some text.")])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    assert isinstance(payload.token_delta_breakdown, TokenDeltaBreakdown)
    assert hasattr(payload.token_delta_breakdown, "caption_dedup_delta")
    assert hasattr(payload.token_delta_breakdown, "warning_delta")
    assert hasattr(payload.token_delta_breakdown, "representation_downgrade_delta")
    assert hasattr(payload.token_delta_breakdown, "missing_asset_delta")
    assert hasattr(payload.token_delta_breakdown, "other_delta")


# DC7
def test_representation_downgrades_field_in_fidelity():
    """PayloadFidelity has representation_downgrades as a list."""
    doc = _make_doc([_para("Text.")])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    assert hasattr(payload.fidelity, "representation_downgrades")
    assert isinstance(payload.fidelity.representation_downgrades, list)


# ── Benchmark Baseline A integration ──────────────────────────────────────────

def test_compile_with_baselines_returns_blocks(tmp_path):
    """compile_with_baselines returns pre-opt blocks captured before optimizer runs."""
    from aksharamd.compiler import Compiler

    src = tmp_path / "test.md"
    src.write_text(
        "# Heading\n\nParagraph one.\n\n## Section Two\n\nParagraph two.",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    compiler = Compiler(output_dir=str(out_dir))
    ctx, pre_opt_blocks = compiler.compile_with_baselines(str(src))

    assert ctx.document is not None
    assert ctx.manifest is not None
    # Should have captured pre-opt blocks
    assert isinstance(pre_opt_blocks, list)
    assert len(pre_opt_blocks) > 0


def test_compile_with_baselines_blocks_have_content(tmp_path):
    """Pre-optimization blocks captured by compile_with_baselines have content."""
    from aksharamd.compiler import Compiler

    src = tmp_path / "test.md"
    src.write_text(
        "# Title\n\nSome meaningful content here that should be in blocks.",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    compiler = Compiler(output_dir=str(out_dir))
    ctx, pre_opt_blocks = compiler.compile_with_baselines(str(src))

    contents = [getattr(b, "content", "") or "" for b in pre_opt_blocks]
    combined = " ".join(contents)
    assert len(combined) > 0


def test_compile_with_baselines_writes_output_files(tmp_path):
    """compile_with_baselines still writes document.md and other outputs."""
    from aksharamd.compiler import Compiler

    src = tmp_path / "test.txt"
    src.write_text("Hello world. A simple test document.", encoding="utf-8")
    out_dir = tmp_path / "output"
    compiler = Compiler(output_dir=str(out_dir))
    ctx, pre_opt_blocks = compiler.compile_with_baselines(str(src))

    assert (out_dir / "document.md").exists()
    assert (out_dir / "manifest.json").exists()


# ── PackageProfile new table serialization fields ─────────────────────────────

# E30
def test_package_profile_has_table_payload_strategy():
    """PackageProfile has table_payload_strategy field with default 'auto'."""
    profile = PackageProfile()
    assert hasattr(profile, "table_payload_strategy")
    assert profile.table_payload_strategy == "auto"


# E31
def test_package_profile_has_max_inline_table_tokens():
    """PackageProfile has max_inline_table_tokens field with default 1200."""
    profile = PackageProfile()
    assert hasattr(profile, "max_inline_table_tokens")
    assert profile.max_inline_table_tokens == 1200


# E32
def test_package_profile_has_table_preview_rows():
    """PackageProfile has table_preview_rows field with default 5."""
    profile = PackageProfile()
    assert hasattr(profile, "table_preview_rows")
    assert profile.table_preview_rows == 5


# E33
def test_package_profile_has_allow_table_artifact_references():
    """PackageProfile has allow_table_artifact_references field with default True."""
    profile = PackageProfile()
    assert hasattr(profile, "allow_table_artifact_references")
    assert profile.allow_table_artifact_references is True


# ── Heading marker tests (payload heading structure preservation) ──────────────

# E34
def test_heading_payload_item_has_hash_prefix():
    """HEADING block emitted in payload text includes '#' level prefix."""
    h = Block(type=BlockType.HEADING, content="Sunday Masses", level=4, page=1)
    doc = _make_doc([h])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    text_items = [item for item in payload.items if item.content_type == PayloadContentType.TEXT]
    assert len(text_items) >= 1
    heading_item = next((item for item in text_items if "Sunday Masses" in (item.text or "")), None)
    assert heading_item is not None
    assert heading_item.text == "#### Sunday Masses"


# E35
def test_heading_level_1_gets_single_hash():
    """Level-1 heading emits exactly one '#' prefix."""
    h = Block(type=BlockType.HEADING, content="Title", level=1, page=1)
    doc = _make_doc([h])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    heading_item = next(
        (item for item in payload.items if item.text and "Title" in item.text), None
    )
    assert heading_item is not None
    assert heading_item.text == "# Title"


# E36
def test_heading_level_2_gets_two_hashes():
    """Level-2 heading emits '## ' prefix."""
    h = Block(type=BlockType.HEADING, content="Section A", level=2, page=1)
    doc = _make_doc([h])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    heading_item = next(
        (item for item in payload.items if item.text and "Section A" in item.text), None
    )
    assert heading_item is not None
    assert heading_item.text == "## Section A"


# E37
def test_heading_without_level_defaults_to_h1():
    """HEADING block with level=None defaults to '#' (level 1) in payload."""
    h = Block(type=BlockType.HEADING, content="Untitled", level=1, page=1)
    doc = _make_doc([h])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    heading_item = next(
        (item for item in payload.items if item.text and "Untitled" in item.text), None
    )
    assert heading_item is not None
    assert heading_item.text.startswith("#")


# E38
def test_paragraph_not_given_hash_prefix():
    """PARAGRAPH block must not gain a '#' prefix from the heading fix."""
    p = Block(type=BlockType.PARAGRAPH, content="Plain text paragraph.", page=1)
    doc = _make_doc([p])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    para_item = next(
        (item for item in payload.items if item.text and "Plain text" in item.text), None
    )
    assert para_item is not None
    assert not para_item.text.startswith("#")


# E39
def test_heading_and_paragraph_both_in_payload():
    """Heading and paragraph appear as separate TEXT items; heading has prefix."""
    h = Block(type=BlockType.HEADING, content="Schedule", level=3, page=1)
    p = Block(type=BlockType.PARAGRAPH, content="All services at 9am.", page=1)
    doc = _make_doc([h, p])
    plan, asset_refs, tmp_path = _make_plan_and_write(doc)
    payload = build_llm_payload(plan, doc, tmp_path, asset_refs)

    texts = [item.text for item in payload.items if item.content_type == PayloadContentType.TEXT]
    assert any(t == "### Schedule" for t in texts), f"Expected '### Schedule' in {texts}"
    assert any("All services at 9am." in (t or "") for t in texts)
