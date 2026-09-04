"""Mapping tests for ``PdfBlockTreeAdapter``.

These tests verify the one-to-one mapping in ``BLOCK_TREE_CONTRACT_DESIGN.md``
Section 5.2. They do NOT test scoring behavior — the adapter is behavior-
neutral by construction at this point (no consumer reads ``SourceProfile``
yet). They test that if a consumer WERE to read it, the values would be
correct.

The load-bearing assertion is that ``pages_containing_tables`` reads
``pdf_stats["table_pages"]`` directly, NOT ``len([b for b in blocks if
b.type == TABLE])``. The multi_table_page fixture in
``tests/fixtures/source_profile/`` produces `table_pages=1` and
`table_count=2`; the mapping test asserts the adapter emits `1`, which
would fail if anyone later "optimizes" the adapter to derive from blocks.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from aksharamd.models.block import Block
from aksharamd.models.document import Document
from aksharamd.models.stitching_profile import PageRowRange, StitchingProfile
from aksharamd.models.table import ExtractionMethod, TableCell, TableData
from aksharamd.scoring.pdf_block_tree_adapter import PdfBlockTreeAdapter
from aksharamd.scoring.source_profile import PageDim, SourceProfile
from aksharamd.scoring.table_expectation import RejectedTableCandidate
from aksharamd.scoring.table_quality import SigName, compute_table_quality
from tests._sourceprofile_fixtures import generate_fixtures


@pytest.fixture(scope="session")
def sourceprofile_fixture_paths(tmp_path_factory):
    """Regenerate the SourceProfile fixture PDFs once per test session in a
    tmp directory. PDFs are never checked into the repo (policy enforced at
    tests/test_parsebench_page_ground_truth.py); the generator produces
    byte-identical bytes across runs, so tmp regeneration is equivalent to
    a committed binary for the assertions here."""
    out = tmp_path_factory.mktemp("sp_fixtures")
    return generate_fixtures(out)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mk_doc(metadata: dict) -> Document:
    """Build a minimal Document with just the metadata needed for the adapter."""
    return Document(
        source="test",
        file_type="pdf",
        pages=int(metadata.get("pdf_stats", {}).get("page_count", 0) or 0),
        metadata=metadata,
    )


# ── Field list parity with design Section 2.1 ────────────────────────────────

def test_source_profile_field_list_matches_design():
    """SourceProfile has the 8 fields the design's Section 2.1 defines.

    Field-count regression guard — if anyone silently adds or removes a
    field, this test fails and forces a design-doc reconciliation.
    """
    expected = {
        "pages_with_text_layer",
        "pages_without_text_layer",
        "pages_total",
        "ocr_capability",
        "hallucinated_pages",
        "document_type_hint",
        "pages_containing_tables",
        "page_dimensions",
    }
    actual = set(SourceProfile.model_fields.keys())
    assert actual == expected, (
        f"SourceProfile field set drifted from design Section 2.1.\n"
        f"  missing from model: {expected - actual}\n"
        f"  extra in model:     {actual - expected}"
    )
    assert len(expected) == 8


# ── Case A: OCR / scanned PDF (matches structure.py:182-190 gate case) ───────

def test_adapter_maps_scanned_ocr_unavailable_document():
    """Scanned doc where OCR is unavailable. Every SourceProfile field
    should equal the corresponding pdf_* metadata value."""
    metadata = {
        "pdf_classification": "scanned",
        "pdf_ocr_available": False,
        "pdf_ocr_hallucination": False,
        "pdf_stats": {
            "page_count": 5,
            "text_pages": 0,
            "image_pages": 5,
            "table_pages": 0,
        },
        "pdf_column_info": {
            1: {"page_width": 612.0, "page_height": 792.0},
            2: {"page_width": 612.0, "page_height": 792.0},
        },
    }
    doc = _mk_doc(metadata)
    PdfBlockTreeAdapter().populate(doc)
    sp = doc.metadata["source_profile"]

    assert isinstance(sp, SourceProfile)
    assert sp.document_type_hint == "scanned"          # pdf_classification
    assert sp.ocr_capability == "unavailable"          # pdf_ocr_available False
    assert sp.hallucinated_pages == 0                  # pdf_ocr_hallucination False
    assert sp.pages_with_text_layer == 0               # pdf_stats.text_pages
    assert sp.pages_without_text_layer == 5            # pdf_stats.image_pages
    assert sp.pages_total == 5                         # pdf_stats.page_count
    assert sp.pages_containing_tables == 0             # pdf_stats.table_pages
    assert sp.page_dimensions == {
        1: PageDim(page_width=612.0, page_height=792.0),
        2: PageDim(page_width=612.0, page_height=792.0),
    }


# ── Case B: clean native-text PDF (OCR available, no image pages) ────────────

def test_adapter_maps_clean_native_text_document():
    """Native-text doc, OCR available but nothing image-only.
    Verify the "available" ocr_capability branch and the native_text
    document_type_hint round-trip."""
    metadata = {
        "pdf_classification": "native_text",
        "pdf_ocr_available": True,
        # pdf_ocr_hallucination absent (typical native-text case)
        "pdf_stats": {
            "page_count": 3,
            "text_pages": 3,
            "image_pages": 0,
            "table_pages": 0,
        },
        "pdf_column_info": {
            1: {"page_width": 595.0, "page_height": 842.0},   # A4
            2: {"page_width": 595.0, "page_height": 842.0},
            3: {"page_width": 595.0, "page_height": 842.0},
        },
    }
    doc = _mk_doc(metadata)
    PdfBlockTreeAdapter().populate(doc)
    sp = doc.metadata["source_profile"]

    assert sp.document_type_hint == "native_text"
    assert sp.ocr_capability == "available"
    assert sp.hallucinated_pages == 0
    assert sp.pages_with_text_layer == 3
    assert sp.pages_without_text_layer == 0
    assert sp.pages_total == 3
    assert sp.pages_containing_tables == 0
    assert len(sp.page_dimensions) == 3
    assert sp.page_dimensions[2].page_width == 595.0
    assert sp.page_dimensions[2].page_height == 842.0


# ── Case C: hallucinated OCR + unknown classifier value normalization ────────

def test_adapter_maps_hallucination_flag_and_normalizes_unknown_classifier():
    """pdf_ocr_hallucination True → hallucinated_pages 1. Also verify
    unknown pdf_classification is normalized to None so the Literal is
    honored — today's consumers already treat empty string as
    'no hint', so None is the neutral equivalent."""
    metadata = {
        "pdf_classification": "some_future_label",   # not in the Literal set
        "pdf_ocr_available": True,
        "pdf_ocr_hallucination": True,
        "pdf_stats": {
            "page_count": 2,
            "text_pages": 1,
            "image_pages": 1,
            "table_pages": 0,
        },
        # No pdf_column_info: adapter should produce an empty dict, not fail.
    }
    doc = _mk_doc(metadata)
    PdfBlockTreeAdapter().populate(doc)
    sp = doc.metadata["source_profile"]

    assert sp.document_type_hint is None
    assert sp.hallucinated_pages == 1
    assert sp.page_dimensions == {}


# ── Case D: idempotence guard ─────────────────────────────────────────────────

def test_adapter_is_idempotent():
    """Calling populate twice must produce the same SourceProfile."""
    metadata = {
        "pdf_classification": "native_text",
        "pdf_ocr_available": True,
        "pdf_stats": {"page_count": 1, "text_pages": 1, "image_pages": 0, "table_pages": 0},
    }
    doc = _mk_doc(metadata)
    adapter = PdfBlockTreeAdapter()
    adapter.populate(doc)
    first = doc.metadata["source_profile"]
    adapter.populate(doc)
    second = doc.metadata["source_profile"]
    assert first == second


# ── Case E: THE load-bearing test — multi_table_page fixture ─────────────────

def test_adapter_pages_containing_tables_is_not_block_derived(sourceprofile_fixture_paths):
    """Load-bearing test for design Section 2.3.

    The multi_table_page fixture has 1 page with 2 tables. If the
    adapter is ever "optimized" to derive pages_containing_tables from
    ``len([b for b in blocks if b.type == TABLE])``, the adapter would
    emit 2 (block count) instead of 1 (page count). The readiness notes
    string ``f"{tp} table page(s)"`` would then read "2 table page(s)"
    instead of "1 table page(s)" — breaking byte-identity on any doc
    with multi-table pages.

    This test compiles the fixture, feeds its metadata into the adapter,
    and asserts the mapping preserves the pages_containing_tables count
    (1), NOT the block count (2).
    """
    fixture_pdf = sourceprofile_fixture_paths.multi_table_page
    tmp = tempfile.mkdtemp(prefix="sp_adapter_test_")
    try:
        proc = subprocess.run(
            ["aksharamd", "compile", str(fixture_pdf),
             "-o", tmp, "--quiet"],
            capture_output=True, text=True,
        )
        docjs = list(Path(tmp).glob("*/document.json"))
        assert docjs, f"compile failed: stderr={proc.stderr[-500:]}"
        raw = json.load(open(docjs[0], encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Baseline preconditions: fixture actually exercises divergence.
    stats = raw["metadata"]["pdf_stats"]
    table_blocks = [b for b in raw["blocks"] if b["type"] == "table"]
    assert stats["table_pages"] == 1, (
        f"fixture regressed: expected table_pages=1, got {stats['table_pages']}"
    )
    assert len(table_blocks) == 2, (
        f"fixture regressed: expected 2 table blocks, got {len(table_blocks)}"
    )

    # Now the actual mapping assertion. Build a Document from the raw
    # metadata and run the adapter. pages_containing_tables MUST equal
    # 1 (from pdf_stats), NOT 2 (block count).
    doc = _mk_doc(raw["metadata"])
    PdfBlockTreeAdapter().populate(doc)
    sp = doc.metadata["source_profile"]

    assert sp.pages_containing_tables == 1, (
        "REGRESSION: adapter derived pages_containing_tables from blocks. "
        "Expected 1 (from pdf_stats.table_pages), got "
        f"{sp.pages_containing_tables} (probably len(blocks)). "
        "See BLOCK_TREE_CONTRACT_DESIGN.md Section 2.3."
    )


# ── Case F: absence of pdf_stats yields all-zero SourceProfile ────────────────

def test_adapter_handles_absent_pdf_stats_with_neutral_defaults():
    """Parser that doesn't populate pdf_stats at all → all-zero neutral
    SourceProfile, no crash. This is the "silently skip" behavior the
    design's Section 6.3 proof-of-neutrality relies on."""
    doc = _mk_doc({})   # no pdf_stats, no pdf_classification, no pdf_column_info
    PdfBlockTreeAdapter().populate(doc)
    sp = doc.metadata["source_profile"]
    assert sp.pages_with_text_layer == 0
    assert sp.pages_without_text_layer == 0
    assert sp.pages_total == 0
    assert sp.pages_containing_tables == 0
    assert sp.ocr_capability == "unavailable"   # absent → falsy → unavailable
    assert sp.hallucinated_pages == 0
    assert sp.document_type_hint is None
    assert sp.page_dimensions == {}


# ── Boundary 2: table-expectation rejected-candidate contract ────────────────
#
# These tests cover PdfBlockTreeAdapter._build_rejected_table_candidates —
# the second boundary of the parser-neutral refactor
# (BLOCK_TREE_CONTRACT_DESIGN.md §3.1). Consumer wiring is via option (b) —
# the neutral typed list is materialized back to dicts at the
# TableExpectationValidator boundary so compute_table_expectation's
# list[dict] signature stays unchanged (byte-identity discipline).


def test_adapter_rejected_candidates_opt_out_when_raw_key_absent():
    """Parser did not populate the raw accumulator → adapter attaches None.
    Design §3.1 opt-in semantics."""
    doc = _mk_doc({})
    PdfBlockTreeAdapter().populate(doc)
    assert doc.metadata["rejected_table_candidates_by_page"] is None


def test_adapter_rejected_candidates_empty_when_raw_key_empty_dict():
    """Parser considered the concept but produced no rejections → empty dict.
    Different from opt-out."""
    doc = _mk_doc({"table_rejected_candidates_by_page": {}})
    PdfBlockTreeAdapter().populate(doc)
    assert doc.metadata["rejected_table_candidates_by_page"] == {}


def test_adapter_maps_serff_like_substantial_candidate_field_by_field():
    """SERFF is the canonical PR #116 substantiality fixture: 48 rows x
    13 cols. This test asserts the adapter converts a SERFF-shaped raw
    dict into a RejectedTableCandidate with every field intact — including
    the row/col counts the substantiality guard reads and the
    quality_metrics nested-dict that _compute_leader_dot_signal reads for
    its evidence field.
    """
    raw = {
        "table_rejected_candidates_by_page": {
            1: [{
                "strategy": "pdfplumber",
                "page": 1,
                "bbox": [72.0, 100.0, 540.0, 700.0],
                "row_count": 48,
                "col_count": 13,
                "rejection_reasons": ["word_split", "too_few_cols"],
                "quality_metrics": {
                    "dot_leader_fraction": 0.0,
                    "empty_cell_fraction": 0.15,
                    "col_count": 13,
                },
            }],
        },
    }
    doc = _mk_doc(raw)
    PdfBlockTreeAdapter().populate(doc)

    neutral = doc.metadata["rejected_table_candidates_by_page"]
    assert isinstance(neutral, dict)
    assert list(neutral.keys()) == [1]
    lst = neutral[1]
    assert len(lst) == 1
    c = lst[0]
    assert isinstance(c, RejectedTableCandidate)

    # Field-by-field one-to-one mapping.
    assert c.strategy == "pdfplumber"
    assert c.page == 1
    assert c.bbox == [72.0, 100.0, 540.0, 700.0]
    assert c.row_count == 48
    assert c.col_count == 13
    assert c.rejection_reasons == ["word_split", "too_few_cols"]

    # quality_metrics: dict-of-scalars, must round-trip byte-identical
    # since _compute_leader_dot_signal reads
    # quality_metrics["dot_leader_fraction"] for the LEADER_DOT_ROWS
    # evidence dict.
    assert c.quality_metrics == {
        "dot_leader_fraction": 0.0,
        "empty_cell_fraction": 0.15,
        "col_count": 13,
    }

    # Substantiality-guard preview: with option (b) the guard still runs
    # on dicts materialized from the typed model, but confirm the typed
    # attribute values already satisfy the >= 10 rows AND >= 3 cols
    # threshold — the guard would fire on this candidate.
    assert c.row_count >= 10
    assert c.col_count >= 3

    # Round-trip via model_dump (what the shim does at the compute
    # boundary) preserves quality_metrics scalars exactly.
    round_tripped = c.model_dump()
    assert round_tripped["quality_metrics"] == {
        "dot_leader_fraction": 0.0,
        "empty_cell_fraction": 0.15,
        "col_count": 13,
    }
    assert round_tripped["row_count"] == 48
    assert round_tripped["col_count"] == 13


def test_adapter_handles_string_page_keys_in_raw_accumulator():
    """The raw accumulator may serialize page keys as strings (JSON
    round-trip). Adapter normalizes to int keys."""
    doc = _mk_doc({
        "table_rejected_candidates_by_page": {
            "3": [{"strategy": "hrule", "page": 3, "bbox": [0, 0, 100, 100],
                   "row_count": 5, "col_count": 2, "rejection_reasons": [], "quality_metrics": {}}],
        },
    })
    PdfBlockTreeAdapter().populate(doc)
    neutral = doc.metadata["rejected_table_candidates_by_page"]
    assert 3 in neutral      # int key present
    assert "3" not in neutral  # not the string version


# ── Boundary 3: table-level stitching-profile contract ───────────────────────
#
# These tests cover the third boundary of the parser-neutral refactor: the
# stitching-signal group in ``compute_table_quality`` reads a typed
# ``StitchingProfile`` field on ``TableData`` instead of the five pdf.py-
# specific ``td.metadata`` keys and the ``ExtractionMethod.PDF_STITCHED``
# gate. This is Shape 2 in the design menu — typed first-class field, not a
# metadata-dict shim.
#
# Boundary contract:
#   * Gate: ``td.stitching is not None`` (nothing else)
#   * Semantics: same signal values as pre-refactor
#   * Parser-neutral: any parser can populate the typed profile; the scorer
#     does not care whether the ``extraction_method`` is PDF_STITCHED or not
#   * Hard cut: the five old metadata keys and the ``PDF_STITCHED`` string
#     literal are gone from ``table_quality.py``


def _b3_make_stitched_td(
    rows: int = 4,
    cols: int = 2,
    *,
    stitching: StitchingProfile | None,
    extraction_method: ExtractionMethod | None = None,
) -> TableData:
    """Build a minimal 4-row / 2-col TableData with optional stitching profile
    and optional extraction_method.

    Both parameters are independent — this lets tests verify that the
    scorer's gate is purely ``td.stitching is not None`` and does NOT depend
    on ``extraction_method``.
    """
    cells = [
        TableCell(text=f"r{r}c{c}", row=r, column=c)
        for r in range(rows)
        for c in range(cols)
    ]
    return TableData(
        row_count=rows,
        column_count=cols,
        cells=cells,
        header_rows=[0],
        header_detection="assumed_first_row",
        span_detection="unsupported",
        extraction_method=extraction_method,
        stitching=stitching,
    )


def _b3_stitching_signal_names() -> set[str]:
    return {
        SigName.STITCHED_SOURCE_PAGE_COUNT,
        SigName.REPEATED_HEADER_REMOVED,
        SigName.STITCHING_CONFIDENCE,
        SigName.SOURCE_METHOD_CONSISTENCY,
        SigName.PAGE_ROW_RANGES_AVAILABLE,
        SigName.ROW_CONTINUITY_OK,
    }


def test_boundary3_pdf_stitched_signals_match_prerefactor_semantics():
    """A PDF-stitched table with a full StitchingProfile produces the same
    signal values the pre-refactor code produced for the same inputs.

    Byte-identical field-by-field: the profile is the only input the
    scorer consults; the values below are the exact values the pre-
    refactor ``_stitching_signals`` computed from the equivalent
    ``td.metadata`` dict.
    """
    sp = StitchingProfile(
        source_pages=[1, 2],
        source_table_methods=["pdf.ruled", "pdf.ruled"],
        page_row_ranges=[
            PageRowRange(page=1, row_start=0, row_end=1),
            PageRowRange(page=2, row_start=2, row_end=3),
        ],
        repeated_header_removed=False,
        stitching_confidence="inferred",
    )
    td = _b3_make_stitched_td(
        rows=4, cols=2, stitching=sp,
        extraction_method=ExtractionMethod.PDF_STITCHED,
    )
    block = Block.from_table(td, page=1, index=0)
    report = compute_table_quality(block)

    def _val(name: str):
        s = next((s for s in report.signals if s.name == name), None)
        return None if s is None else s.value

    def _stat(name: str):
        s = next((s for s in report.signals if s.name == name), None)
        return None if s is None else s.status

    assert _val(SigName.STITCHED_SOURCE_PAGE_COUNT) == 2
    assert _val(SigName.REPEATED_HEADER_REMOVED) is False
    assert _val(SigName.STITCHING_CONFIDENCE) == "inferred"
    assert _stat(SigName.STITCHING_CONFIDENCE) == "risk"
    assert _val(SigName.SOURCE_METHOD_CONSISTENCY) is True
    assert _val(SigName.PAGE_ROW_RANGES_AVAILABLE) is True
    assert _val(SigName.ROW_CONTINUITY_OK) is True


def test_boundary3_non_pdf_parser_can_receive_stitching_signals():
    """A parser with no relation to PDF (extraction_method=None or
    HTML_NATIVE) can construct a TableData with a StitchingProfile and
    still receive the full stitching signal set.

    This is the load-bearing parser-neutrality property: the scorer no
    longer requires ``ExtractionMethod.PDF_STITCHED`` to emit the
    stitching signal group.
    """
    sp = StitchingProfile(
        source_pages=[7, 8],
        source_table_methods=["html.native", "html.native"],
        page_row_ranges=[
            PageRowRange(page=7, row_start=0, row_end=1),
            PageRowRange(page=8, row_start=2, row_end=3),
        ],
        repeated_header_removed=True,
        stitching_confidence="inferred",
    )
    td = _b3_make_stitched_td(
        rows=4, cols=2, stitching=sp,
        extraction_method=ExtractionMethod.HTML_NATIVE,
    )
    block = Block.from_table(td, page=7, index=0)
    report = compute_table_quality(block)

    emitted = {s.name for s in report.signals}
    assert _b3_stitching_signal_names().issubset(emitted), (
        f"Non-PDF parser did not receive stitching signals; "
        f"missing: {_b3_stitching_signal_names() - emitted}"
    )


def test_boundary3_stitching_none_returns_neutral_no_signals():
    """A table with ``stitching=None`` (the default) receives ZERO
    stitching signals, regardless of ``extraction_method``.

    Same behavior as the pre-refactor code returned for tables where
    ``extraction_method != PDF_STITCHED``; the new gate is stricter (it
    ignores the extraction method entirely) but produces the same visible
    output for the common case.
    """
    td = _b3_make_stitched_td(
        rows=3, cols=2, stitching=None,
        extraction_method=ExtractionMethod.PDF_STITCHED,  # deliberately set
    )
    block = Block.from_table(td, page=1, index=0)
    report = compute_table_quality(block)

    emitted = {s.name for s in report.signals}
    assert not (emitted & _b3_stitching_signal_names()), (
        "stitching=None must emit ZERO stitching signals even if "
        f"extraction_method==PDF_STITCHED. Leaked: "
        f"{emitted & _b3_stitching_signal_names()}"
    )


def test_boundary3_pdf_stitched_extraction_method_is_not_required():
    """A table with a StitchingProfile but ``extraction_method=None``
    still receives the stitching signals.

    Confirms the new gate is purely ``td.stitching is not None``. Under
    the pre-refactor code, this table would have received no stitching
    signals because ``extraction_method != PDF_STITCHED``.
    """
    sp = StitchingProfile(
        source_pages=[1, 2],
        source_table_methods=[],
        page_row_ranges=[],
        repeated_header_removed=None,
        stitching_confidence="unknown",
    )
    td = _b3_make_stitched_td(
        rows=3, cols=2, stitching=sp,
        extraction_method=None,
    )
    block = Block.from_table(td, page=1, index=0)
    report = compute_table_quality(block)

    emitted = {s.name for s in report.signals}
    assert _b3_stitching_signal_names().issubset(emitted), (
        f"Missing stitching signals when extraction_method=None; "
        f"missing: {_b3_stitching_signal_names() - emitted}"
    )


def test_boundary3_static_assertion_no_pdf_stitched_literal_in_table_quality():
    """Static tripwire: the string ``PDF_STITCHED`` must not appear in
    ``table_quality.py`` — the scorer must not gate on this pdf.py-specific
    extraction method.
    """
    tq_path = Path(__file__).resolve().parents[1] / "aksharamd" / "scoring" / "table_quality.py"
    contents = tq_path.read_text(encoding="utf-8")
    assert "PDF_STITCHED" not in contents, (
        "PDF_STITCHED must not appear in table_quality.py — the scorer's "
        "stitching gate is purely td.stitching is not None (Boundary 3)."
    )


def test_boundary3_static_assertion_no_old_metadata_reads_in_table_quality():
    """Static tripwire: the scorer must not read the five old
    ``td.metadata`` keys used by the pre-refactor stitching code path.

    The check targets metadata-dict access patterns (``.metadata.get(``,
    ``.metadata[``, ``meta.get(``, ``meta[``) with each of the five old
    key names. The typed ``StitchingProfile`` is the only stitching input
    the scorer reads.

    The class attribute names ``REPEATED_HEADER_REMOVED``,
    ``STITCHING_CONFIDENCE``, and ``PAGE_ROW_RANGES_AVAILABLE`` on
    ``SigName`` continue to hold the string signal-name values (those
    are emitted signal names, not metadata-dict keys) — this test is
    scoped to metadata-dict access patterns and therefore does not
    conflict with those attributes.
    """
    tq_path = Path(__file__).resolve().parents[1] / "aksharamd" / "scoring" / "table_quality.py"
    contents = tq_path.read_text(encoding="utf-8")
    banned_keys = (
        "source_pages",
        "source_table_methods",
        "page_row_ranges",
        "repeated_header_removed",
        "stitching_confidence",
    )
    # Match .metadata.get("<key>"), .metadata["<key>"], meta.get("<key>"),
    # meta["<key>"] — any dict-access pattern with the banned key names.
    forbidden_patterns = []
    for key in banned_keys:
        for prefix in ('.metadata.get("', ".metadata.get('",
                       '.metadata["', ".metadata['",
                       'meta.get("', "meta.get('",
                       'meta["', "meta['"):
            forbidden_patterns.append(prefix + key)
    hits = [p for p in forbidden_patterns if p in contents]
    assert not hits, (
        "Scorer must not read the pre-refactor td.metadata stitching keys. "
        f"Found reads matching: {hits}"
    )

