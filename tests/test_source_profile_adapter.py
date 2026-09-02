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
import sys
import tempfile
from pathlib import Path

import pytest

from aksharamd.models.document import Document
from aksharamd.scoring.pdf_block_tree_adapter import PdfBlockTreeAdapter
from aksharamd.scoring.source_profile import PageDim, SourceProfile
from aksharamd.scoring.table_expectation import RejectedTableCandidate

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

