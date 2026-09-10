"""Regression coverage for header context and unsupported merged-cell serialization."""
from __future__ import annotations

import json

import pytest

from aksharamd.models.block import Block
from aksharamd.models.document import Document
from aksharamd.models.table import ExtractionMethod, TableCell, TableData
from aksharamd.packaging import PackageProfile, PackageWriter, build_llm_payload, plan_document
from aksharamd.packaging.models import TablePayloadFormat
from aksharamd.packaging.payload import PayloadContentType
from aksharamd.packaging.payload_builder import build_table_candidates, render_table_for_payload
from aksharamd.renderers.table_markdown import render_table_row_records

UNITS = "amounts in thousands of United States dollars"


def _context_table(kind):
    if kind == "multiheader":
        cells = [
            TableCell(row=0, column=0, text=UNITS),
            TableCell(row=0, column=1, text="Reported amounts"),
        ]
        header_rows = [0, 1]
        offset = 1
    elif kind == "merged_header":
        cells = [TableCell(row=0, column=0, text=UNITS, column_span=2)]
        header_rows = [0, 1]
        offset = 1
    else:
        cells = []
        header_rows = [0]
        offset = 0
    cells += [
        TableCell(row=offset, column=0, text="Revenue"),
        TableCell(row=offset, column=1, text="Expense"),
        TableCell(row=offset + 1, column=0, text="100", row_span=2 if kind == "merged_body" else 1),
        TableCell(row=offset + 1, column=1, text="80"),
    ]
    if kind == "merged_body":
        cells.append(TableCell(row=2, column=1, text="90"))
    return TableData(
        row_count=3 if kind == "merged_body" else offset + 2,
        column_count=2, cells=cells, header_rows=header_rows,
        extraction_method=ExtractionMethod.XLSX_NATIVE,
    )


@pytest.mark.parametrize("kind", ["multiheader", "merged_header", "merged_body"])
@pytest.mark.parametrize("strategy", ["auto", "full_inline", "preview_reference"])
def test_complex_table_final_payload_keeps_context_without_claiming_preservation(tmp_path, kind, strategy):
    table = _context_table(kind)
    profile = PackageProfile(table_payload_strategy=strategy)
    candidates = build_table_candidates(table, "t1", "tables/t1.json", 0, profile)
    assert render_table_row_records(table) == ""
    assert TablePayloadFormat.ROW_RECORDS not in {candidate.format for candidate in candidates}
    assert all(not candidate.preserves_structure_inline for candidate in candidates)

    text, selected = render_table_for_payload(table, profile)
    assert selected.format != TablePayloadFormat.ROW_RECORDS
    assert not selected.preserves_structure_inline
    for value in ("Revenue", "Expense", "100", "80"):
        assert value in text
    if kind != "merged_body":
        assert UNITS in text

    block = Block.from_table(table, page=1)
    doc = Document(source="report.xlsx", blocks=[block])
    doc.document_id = doc.id = "test-doc"
    plan = plan_document(doc, profile)
    assets, _ = PackageWriter().write(tmp_path, plan, doc, None)
    payload = build_llm_payload(plan, doc, tmp_path, assets, profile)
    items = [item for item in payload.items if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(items) == 1
    item = items[0]
    assert item.table_payload_format != "row_records"
    assert not item.inline_complete
    assert item.table_rows_omitted == 0
    assert item.table_rows_inline == item.table_rows_total
    assert item.table_markdown is not None
    for value in ("Revenue", "Expense", "100", "80"):
        assert value in item.table_markdown
    if kind != "merged_body":
        assert UNITS in item.table_markdown
    # Exact associations remain available in the structured artifact.
    assert item.full_table_artifact_path
    artifact = json.loads((tmp_path / item.full_table_artifact_path).read_text())["table"]
    assert artifact["header_rows"] == table.header_rows
    assert [(c["text"], c["row"], c["column"], c["row_span"], c["column_span"])
            for c in artifact["cells"]] == [
                (c.text, c.row, c.column, c.row_span, c.column_span) for c in table.cells
            ]


def test_simple_table_retains_row_records_and_preservation_claim():
    table = _context_table("simple")
    assert render_table_row_records(table) == "Revenue=100; Expense=80"
    profile = PackageProfile(table_payload_strategy="full_inline")
    candidates = build_table_candidates(table, "t1", None, 0, profile)
    records = next(c for c in candidates if c.format == TablePayloadFormat.ROW_RECORDS)
    assert records.preserves_all_rows_inline
    assert records.preserves_structure_inline
    text, candidate = render_table_for_payload(table, profile)
    assert candidate.preserves_all_rows_inline and candidate.preserves_structure_inline
    assert "100" in text and "80" in text


def test_nonleading_header_is_not_flattened_to_row_records():
    table = TableData(row_count=2, column_count=1, header_rows=[1], cells=[
        TableCell(row=0, column=0, text="100"),
        TableCell(row=1, column=0, text="Revenue"),
    ])
    assert render_table_row_records(table) == ""


def test_simple_table_final_payload_remains_complete(tmp_path):
    block = Block.from_table(_context_table("simple"), page=1)
    doc = Document(source="report.xlsx", blocks=[block])
    doc.document_id = doc.id = "simple-doc"
    profile = PackageProfile()
    plan = plan_document(doc, profile)
    assets, _ = PackageWriter().write(tmp_path, plan, doc, None)
    payload = build_llm_payload(plan, doc, tmp_path, assets, profile)
    items = [item for item in payload.items if item.content_type == PayloadContentType.STRUCTURED_TABLE]
    assert len(items) == 1
    assert items[0].inline_complete
    assert items[0].table_rows_inline == items[0].table_rows_total == 1
    assert items[0].table_rows_omitted == 0
    assert items[0].table_markdown == "Revenue\tExpense\n100\t80"


@pytest.mark.parametrize("headers", [["A", "A", "A_1"], ["", "B", "C"], ["A", " A ", "B"]])
def test_ambiguous_record_headers_keep_grid_column_associations(tmp_path, headers):
    rows = [headers, ["first", "second", "third"]]
    table = TableData(row_count=2, column_count=3, header_rows=[0],
                      extraction_method=ExtractionMethod.XLSX_NATIVE,
                      cells=[TableCell(row=r, column=c, text=text)
                             for r, row in enumerate(rows) for c, text in enumerate(row)])
    assert render_table_row_records(table) == ""
    profile = PackageProfile(table_payload_strategy="full_inline")
    text, candidate = render_table_for_payload(table, profile)
    assert candidate.format != TablePayloadFormat.ROW_RECORDS
    assert text.splitlines()[1] == "first\tsecond\tthird"
    block = Block.from_table(table, page=1)
    doc = Document(source="ambiguous.xlsx", blocks=[block])
    doc.document_id = doc.id = "ambiguous-doc"
    plan = plan_document(doc, profile)
    payload = build_llm_payload(plan, doc, tmp_path, [], profile)
    item = next(i for i in payload.items if i.content_type == PayloadContentType.STRUCTURED_TABLE)
    assert item.table_payload_format == "tsv"
    assert item.table_markdown == "\n".join("\t".join(row) for row in rows)


@pytest.mark.parametrize("strategy", ["auto", "full_inline", "preview_reference", "reference_only"])
@pytest.mark.parametrize("artifact_state", ["missing", "directory", "present"])
def test_reference_requires_real_artifact_or_keeps_all_rows(tmp_path, strategy, artifact_state):
    table = TableData(row_count=12, column_count=1, header_rows=[0, 1],
                      extraction_method=ExtractionMethod.XLSX_NATIVE,
                      cells=[TableCell(row=r, column=0, text=text) for r, text in enumerate(
                          [UNITS, "Revenue"] + [f"amount {r} details " * 20 for r in range(10)]
                      )])
    profile = PackageProfile(table_payload_strategy=strategy, max_inline_table_tokens=20)
    # A direct rendering call has no artifact promise and must keep the entire tail.
    text, candidate = render_table_for_payload(table, profile)
    assert candidate.preserves_all_rows_inline
    assert candidate.omitted_row_count == 0
    assert "amount 9 details" in text
    assert candidate.format not in {TablePayloadFormat.PREVIEW_REFERENCE, TablePayloadFormat.JSON_REFERENCE}

    block = Block.from_table(table, page=1)
    doc = Document(source="long.xlsx", blocks=[block])
    doc.document_id = doc.id = "long-doc"
    plan = plan_document(doc, profile)
    assets = []
    if artifact_state == "present":
        assets, _ = PackageWriter().write(tmp_path, plan, doc, None)
    elif artifact_state == "directory":
        (tmp_path / "tables" / f"{block.id}.json").mkdir(parents=True)
    payload = build_llm_payload(plan, doc, tmp_path, assets, profile)
    item = next(i for i in payload.items if i.content_type == PayloadContentType.STRUCTURED_TABLE)
    assert not item.inline_complete  # hierarchy is not represented by grid formats
    if artifact_state == "present":
        assert item.full_table_artifact_path
        assert (tmp_path / item.full_table_artifact_path).is_file()
        if strategy != "full_inline":
            assert item.table_rows_omitted > 0
    else:
        assert item.full_table_artifact_path is None
        assert item.table_rows_omitted == 0
        assert item.table_rows_inline == item.table_rows_total == 10
        assert "amount 9 details" in item.table_markdown


def test_legacy_json_reference_without_artifact_keeps_data():
    text, candidate = render_table_for_payload(
        _context_table("simple"), PackageProfile(table_payload_format="json_reference")
    )
    assert candidate.format != TablePayloadFormat.JSON_REFERENCE
    assert "100" in text and "80" in text


def test_selector_rejects_unavailable_reference_candidates():
    from aksharamd.packaging.models import TableSerializationCandidate
    from aksharamd.packaging.payload_builder import select_table_serialization

    inline = TableSerializationCandidate(format=TablePayloadFormat.TSV, text="A\nlast row",
                                         token_count=50, preserves_all_rows_inline=True,
                                         preserves_structure_inline=True)
    unavailable = TableSerializationCandidate(format=TablePayloadFormat.PREVIEW_REFERENCE,
                                              text="preview", token_count=1,
                                              preserves_all_rows_inline=False,
                                              preserves_structure_inline=True, artifact_path=None)
    selected = select_table_serialization(
        [inline, unavailable], "adaptive", PackageProfile(max_inline_table_tokens=1), 1
    )
    assert selected == inline
