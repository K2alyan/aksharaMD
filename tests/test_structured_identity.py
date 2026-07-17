"""Tests for structured table identity: checksums, block.id, document_id propagation."""
from __future__ import annotations

import json

import pytest

from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.document import Document
from aksharamd.models.table import ExtractionMethod, TableCell, TableData, BoundingBox


def _simple_table(
    texts: list[str] | None = None,
    header_rows: list[int] | None = None,
    **kwargs,
) -> TableData:
    texts = texts or ["A", "B", "C", "D"]
    return TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text=texts[0], row=0, column=0),
            TableCell(text=texts[1], row=0, column=1),
            TableCell(text=texts[2], row=1, column=0),
            TableCell(text=texts[3], row=1, column=1),
        ],
        header_rows=header_rows if header_rows is not None else [0],
        **kwargs,
    )


# ── same semantic table + different bbox -> same checksum ─────────────────────

def test_same_table_different_bbox_same_checksum():
    td1 = _simple_table()
    td2 = _simple_table(bbox=BoundingBox(x0=10, y0=20, x1=100, y1=200))
    assert td1.canonical_payload() == td2.canonical_payload()
    b1 = Block.from_table(td1, index=0)
    b2 = Block.from_table(td2, index=0)
    assert b1.checksum == b2.checksum


# ── same Markdown but different header_rows -> different checksum ─────────────

def test_different_header_rows_different_checksum():
    td_with_header = _simple_table(header_rows=[0])
    td_no_header = _simple_table(header_rows=[])
    b1 = Block.from_table(td_with_header, index=0)
    b2 = Block.from_table(td_no_header, index=0)
    # The Markdown text looks the same (separator always emitted)
    # but canonical_payload differs because header_rows differs
    assert b1.checksum != b2.checksum


# ── same Markdown but different row_span -> different checksum ────────────────

def test_different_row_span_different_checksum():
    td_no_span = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0),
            TableCell(text="B", row=0, column=1),
            TableCell(text="C", row=1, column=0),
            TableCell(text="D", row=1, column=1),
        ],
    )
    td_with_span = TableData(
        row_count=2,
        column_count=2,
        cells=[
            TableCell(text="A", row=0, column=0, row_span=2),
            TableCell(text="B", row=0, column=1),
            TableCell(text="D", row=1, column=1),
        ],
    )
    b1 = Block.from_table(td_no_span, index=0)
    b2 = Block.from_table(td_with_span, index=0)
    assert b1.checksum != b2.checksum


# ── cell text change -> block.checksum changes -> document_id changes ─────────

def test_cell_text_change_propagates_to_document_id():
    td1 = _simple_table(texts=["A", "B", "C", "D"])
    td2 = _simple_table(texts=["X", "B", "C", "D"])

    b1 = Block.from_table(td1, index=0)
    b2 = Block.from_table(td2, index=0)
    assert b1.checksum != b2.checksum

    doc1 = Document(source="x.csv", blocks=[b1]).compute_id()
    doc2 = Document(source="x.csv", blocks=[b2]).compute_id()
    assert doc1.document_id != doc2.document_id


# ── span_detection excluded from hash ────────────────────────────────────────

def test_span_detection_excluded_from_hash():
    td1 = _simple_table(span_detection="native")
    td2 = _simple_table(span_detection="unsupported")
    assert td1.canonical_payload() == td2.canonical_payload()
    b1 = Block.from_table(td1, index=0)
    b2 = Block.from_table(td2, index=0)
    assert b1.checksum == b2.checksum


# ── formula vs no formula with same display text -> different checksum ────────

def test_formula_vs_no_formula_different_checksum():
    td_with = TableData(
        row_count=1,
        column_count=1,
        cells=[TableCell(text="42", row=0, column=0, formula="=SUM(A1:A10)")],
    )
    td_without = TableData(
        row_count=1,
        column_count=1,
        cells=[TableCell(text="42", row=0, column=0)],
    )
    b1 = Block.from_table(td_with, index=0)
    b2 = Block.from_table(td_without, index=0)
    assert b1.checksum != b2.checksum


# ── deterministic canonical_payload JSON ─────────────────────────────────────

def test_canonical_payload_json_deterministic():
    td1 = _simple_table()
    td2 = _simple_table()
    j1 = json.dumps(td1.canonical_payload(), sort_keys=True, separators=(",", ":"))
    j2 = json.dumps(td2.canonical_payload(), sort_keys=True, separators=(",", ":"))
    assert j1 == j2


# ── legacy table block uses content-based checksum ───────────────────────────

def test_legacy_table_block_uses_content_checksum():
    import hashlib
    import unicodedata

    content = "| A | B |\n| --- | --- |\n| C | D |"
    b = Block(type=BlockType.TABLE, content=content, index=0)
    assert b.table_data is None

    def normalize(t: str) -> str:
        return unicodedata.normalize("NFC", t).replace("\r\n", "\n").replace("\r", "\n")

    expected = hashlib.sha256(normalize(content).encode()).hexdigest()[:16]
    assert b.checksum == expected


# ── Block.from_table derives content from table_data ─────────────────────────

def test_from_table_derives_content():
    td = _simple_table()
    b = Block.from_table(td, index=0)
    assert b.content != ""
    assert "| A | B |" in b.content
    assert "| --- | --- |" in b.content


def test_from_table_content_equals_render():
    from aksharamd.renderers.table_markdown import render_table_markdown
    td = _simple_table()
    b = Block.from_table(td, index=0)
    assert b.content == render_table_markdown(td)


# ── content="" passed to Block with table_data -> overwritten ─────────────────

def test_content_overwritten_by_compute_derived():
    td = _simple_table()
    b = Block(type=BlockType.TABLE, content="wrong content here", table_data=td, index=0)
    # content must be derived from table_data, not "wrong content here"
    assert b.content != "wrong content here"
    assert "| A |" in b.content


# ── table_id = block.id ───────────────────────────────────────────────────────

def test_compute_ids_uses_block_id():
    td = _simple_table()
    b = Block.from_table(td, index=0)
    b.table_data.compute_ids(b.id)
    assert b.table_data.id == b.id
