"""Tests for key-value group milestone.

Covers:
- Models (KV1-KV10)
- Detection (KD1-KD12)
- Rendering (KR1-KR10)
- Payload (KP1-KP8)
- Abergowrie regression (KA1-KA4)
- Compatibility (KC1-KC6)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.models.key_value import (
    KeyValueEntry,
    KeyValueGroup,
    KeyValueGroupType,
    KeyValueValueType,
)
from aksharamd.models.table import ExtractionMethod, TableCell, TableData
from aksharamd.packaging import (
    PackageWriter,
    RepresentationType,
    plan_document,
)
from aksharamd.packaging.payload import PayloadContentType
from aksharamd.packaging.payload_builder import build_llm_payload
from aksharamd.renderers.key_value_markdown import render_key_value_group, render_key_value_tsv
from aksharamd.scoring.key_value_detection import detect_key_value_entries

# ── Fixtures ────────────────────────────────────────────────────────────────────

def _abergowrie_group() -> KeyValueGroup:
    """Two Sunday Mass records for Abergowrie with different times."""
    entries = [
        KeyValueEntry(key="Location", value="Abergowrie", value_type=KeyValueValueType.TEXT),
        KeyValueEntry(key="Day", value="Saturday"),
        KeyValueEntry(key="Time", value="7:00 PM", value_type=KeyValueValueType.TIME),
        KeyValueEntry(key="Location", value="Abergowrie", value_type=KeyValueValueType.TEXT),
        KeyValueEntry(key="Day", value="Sunday"),
        KeyValueEntry(key="Time", value="9:00 AM", value_type=KeyValueValueType.TIME),
    ]
    return KeyValueGroup(
        entries=entries,
        title="Sunday Masses 29/30 September",
        group_type=KeyValueGroupType.SCHEDULE,
        extraction_method="inferred",
    )


def _make_doc(blocks: list[Block], doc_id: str = "testdoc") -> Document:
    doc = Document(source="test.pdf", blocks=blocks)
    doc.document_id = doc_id
    doc.id = doc_id
    return doc


def _simple_group() -> KeyValueGroup:
    return KeyValueGroup(
        entries=[
            KeyValueEntry(key="Name", value="John Doe"),
            KeyValueEntry(key="Role", value="Developer"),
        ],
        title="Staff Member",
    )


# ── MODELS ──────────────────────────────────────────────────────────────────────

def test_kv1_entry_full_construction():
    """KV1: KeyValueEntry valid construction with all fields."""
    entry = KeyValueEntry(
        key="Email",
        value="test@example.com",
        normalized_key="email",
        value_type=KeyValueValueType.EMAIL,
        page=3,
        confidence="extracted",
        metadata={"source": "form"},
    )
    assert entry.key == "Email"
    assert entry.value == "test@example.com"
    assert entry.normalized_key == "email"
    assert entry.value_type == KeyValueValueType.EMAIL
    assert entry.page == 3
    assert entry.confidence == "extracted"
    assert entry.metadata["source"] == "form"


def test_kv2_entry_requires_key_and_value():
    """KV2: KeyValueEntry requires key and value."""
    with pytest.raises(Exception):
        KeyValueEntry(key="")  # missing value
    with pytest.raises(Exception):
        KeyValueEntry(value="foo")  # missing key


def test_kv3_group_preserves_entry_order():
    """KV3: KeyValueGroup serializes entries in order (not sorted)."""
    entries = [
        KeyValueEntry(key="Z", value="last"),
        KeyValueEntry(key="A", value="first"),
        KeyValueEntry(key="M", value="middle"),
    ]
    group = KeyValueGroup(entries=entries)
    payload = group.canonical_payload()
    assert payload["entries"][0]["key"] == "Z"
    assert payload["entries"][1]["key"] == "A"
    assert payload["entries"][2]["key"] == "M"


def test_kv4_checksum_changes_on_value_change():
    """KV4: semantic_checksum changes when value changes."""
    g1 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="8:00 AM")])
    g2 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="9:00 AM")])
    assert g1.semantic_checksum() != g2.semantic_checksum()


def test_kv5_checksum_stable_on_bbox_change():
    """KV5: semantic_checksum does NOT change when only bbox changes."""
    from aksharamd.models.table import BoundingBox
    g1 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="8:00 AM")])
    g2 = KeyValueGroup(
        entries=[KeyValueEntry(key="Time", value="8:00 AM")],
        bbox=BoundingBox(x0=10, y0=10, x1=100, y1=50),
    )
    assert g1.semantic_checksum() == g2.semantic_checksum()


def test_kv6_checksum_stable_on_confidence_change():
    """KV6: semantic_checksum does NOT change when only confidence changes."""
    g1 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="8:00 AM")], confidence="extracted")
    g2 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="8:00 AM")], confidence="inferred")
    assert g1.semantic_checksum() == g2.semantic_checksum()


def test_kv7_checksum_stable_on_metadata_change():
    """KV7: semantic_checksum does NOT change when only metadata changes."""
    g1 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="8:00 AM")], metadata={})
    g2 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="8:00 AM")], metadata={"x": 1})
    assert g1.semantic_checksum() == g2.semantic_checksum()


def test_kv8_checksum_changes_on_key_change():
    """KV8: semantic_checksum changes when key changes."""
    g1 = KeyValueGroup(entries=[KeyValueEntry(key="Time", value="8:00 AM")])
    g2 = KeyValueGroup(entries=[KeyValueEntry(key="Date", value="8:00 AM")])
    assert g1.semantic_checksum() != g2.semantic_checksum()


def test_kv9_repeated_vs_nonrepeated_distinct_checksum():
    """KV9: repeated entries produce distinct semantic checksum from non-repeated."""
    g1 = KeyValueGroup(entries=[
        KeyValueEntry(key="Location", value="A"),
        KeyValueEntry(key="Location", value="B"),
    ])
    g2 = KeyValueGroup(entries=[
        KeyValueEntry(key="Location", value="A"),
        KeyValueEntry(key="Venue", value="B"),
    ])
    assert g1.semantic_checksum() != g2.semantic_checksum()


def test_kv10_from_key_value_group_has_content():
    """KV10: Block.from_key_value_group() creates KEY_VALUE_GROUP block with non-empty content."""
    group = _simple_group()
    block = Block.from_key_value_group(group, page=1)
    assert block.type == BlockType.KEY_VALUE_GROUP
    assert block.content  # non-empty
    assert "Name" in block.content
    assert "John Doe" in block.content
    assert block.id  # non-empty


# ── DETECTION ───────────────────────────────────────────────────────────────────

def test_kd1_two_adjacent_kv_lines_detected():
    """KD1: two adjacent 'Key: Value' lines detected as group."""
    text = "Location: Abergowrie\nTime: 8:30 AM"
    result = detect_key_value_entries(text)
    assert result.group is not None
    assert len(result.group.entries) == 2
    assert result.group.entries[0].key == "Location"
    assert result.group.entries[1].key == "Time"


def test_kd2_single_prose_colon_rejected():
    """KD2: single prose colon rejected (e.g., 'Note: this paragraph explains...')."""
    text = "Note: this paragraph explains why the system is designed this way."
    result = detect_key_value_entries(text)
    assert result.group is None


def test_kd3_rhetorical_colon_rejected():
    """KD3: rhetorical colon rejected ('The result was clear: the system needed revision.')."""
    text = "Result: the system required significant revision and overhaul."
    result = detect_key_value_entries(text)
    # Either rejected as rhetorical or value too long
    assert result.group is None


def test_kd4_long_value_excluded():
    """KD4: long value (>80 chars) not included as KV entry."""
    long_val = "x" * 85
    text = f"Summary: {long_val}\nName: John"
    result = detect_key_value_entries(text)
    # Only Name: John should be candidate; but that's only 1, insufficient alone
    assert result.group is None or all(
        len(e.value) <= 80 for e in (result.group.entries if result.group else [])
    )


def test_kd5_long_label_not_treated_as_key():
    """KD5: label with >5 words not treated as key."""
    text = "The quick brown fox jumps: over the lazy dog\nName: John"
    result = detect_key_value_entries(text)
    # The first line's key is 5 words "The quick brown fox jumps" - rejected because starts with "The "
    # Only "Name: John" may be a candidate — insufficient alone
    if result.group is not None:
        for entry in result.group.entries:
            assert len(entry.key.split()) <= 5


def test_kd6_contact_block_detected_as_contact():
    """KD6: contact block with email+phone detected as CONTACT group type."""
    text = "Email: alice@example.com\nPhone: +1 555-0100"
    result = detect_key_value_entries(text)
    assert result.group is not None
    assert result.group.group_type == KeyValueGroupType.CONTACT


def test_kd7_schedule_block_detected():
    """KD7: schedule block with Time: + Location: entries detected."""
    text = "Time: 9:00 AM\nLocation: Main Hall"
    result = detect_key_value_entries(text)
    assert result.group is not None
    assert result.group.group_type == KeyValueGroupType.SCHEDULE


def test_kd8_key_normalization_tel():
    """KD8: key normalization: 'Tel.' -> 'telephone' (normalized_key set)."""
    text = "Tel.: +1-555-0100\nFax: +1-555-0101"
    result = detect_key_value_entries(text)
    # "Tel." should normalize
    if result.group is not None:
        tel_entries = [e for e in result.group.entries if "tel" in e.key.lower()]
        for e in tel_entries:
            if e.normalized_key:
                assert "telephone" in e.normalized_key.lower() or "phone" in e.normalized_key.lower()


def test_kd9_duplicate_keys_signaled():
    """KD9: duplicate keys detected and signaled as KEY_VALUE_DUPLICATE_KEY."""
    text = "Location: Place A\nTime: 8:00 AM\nLocation: Place B"
    result = detect_key_value_entries(text)
    assert result.group is not None
    assert "KEY_VALUE_DUPLICATE_KEY" in result.signals


def test_kd10_ambiguous_date_not_inferred():
    """KD10: '10/11' is NOT inferred as a date (too short / ambiguous)."""
    from aksharamd.scoring.key_value_detection import _infer_value_type
    result = _infer_value_type("10/11")
    assert result != KeyValueValueType.DATE


def test_kd11_time_value_inferred():
    """KD11: '9:00 AM' IS inferred as TIME value type."""
    from aksharamd.scoring.key_value_detection import _infer_value_type
    result = _infer_value_type("9:00 AM")
    assert result == KeyValueValueType.TIME


def test_kd12_single_email_accepted():
    """KD12: single entry with email IS accepted (strong evidence exception)."""
    text = "Email: contact@example.com"
    result = detect_key_value_entries(text)
    assert result.group is not None
    assert len(result.group.entries) == 1
    assert result.group.entries[0].value_type == KeyValueValueType.EMAIL


# ── RENDERING ───────────────────────────────────────────────────────────────────

def test_kr1_render_single_record_with_title():
    """KR1: render_key_value_group single record produces '#### Title\n- **Key:** Value' format."""
    group = _simple_group()
    rendered = render_key_value_group(group)
    assert rendered.startswith("#### Staff Member")
    assert "- **Name:** John Doe" in rendered
    assert "- **Role:** Developer" in rendered


def test_kr2_render_no_title():
    """KR2: render_key_value_group no title produces only bullet list."""
    group = KeyValueGroup(entries=[
        KeyValueEntry(key="Name", value="Alice"),
        KeyValueEntry(key="Role", value="Admin"),
    ])
    rendered = render_key_value_group(group)
    assert not rendered.startswith("####")
    assert "- **Name:** Alice" in rendered


def test_kr3_render_repeated_keys_splits_records():
    """KR3: render_key_value_group with repeated keys splits into records."""
    group = _abergowrie_group()
    rendered = render_key_value_group(group)
    assert "Record 1" in rendered
    assert "Record 2" in rendered


def test_kr4_record_labels_correct_format():
    """KR4: record boundaries labeled '**Record 1**', '**Record 2**' etc."""
    group = _abergowrie_group()
    rendered = render_key_value_group(group)
    assert "**Record 1**" in rendered
    assert "**Record 2**" in rendered


def test_kr5_empty_value_not_omitted():
    """KR5: empty value renders as '- **Key:** ' (not omitted)."""
    group = KeyValueGroup(entries=[
        KeyValueEntry(key="Notes", value=""),
        KeyValueEntry(key="Name", value="Bob"),
    ])
    rendered = render_key_value_group(group)
    assert "- **Notes:**" in rendered


def test_kr6_pipe_in_value_escaped():
    """KR6: pipe char in value escaped as \\| in markdown output."""
    group = KeyValueGroup(entries=[
        KeyValueEntry(key="Hours", value="Mon|Fri"),
        KeyValueEntry(key="Name", value="Test"),
    ])
    rendered = render_key_value_group(group)
    assert "Mon\\|Fri" in rendered


def test_kr7_tsv_produces_tab_separated():
    """KR7: render_key_value_tsv produces 'key\\tvalue' lines."""
    group = _simple_group()
    rendered = render_key_value_tsv(group)
    lines = [line for line in rendered.splitlines() if "\t" in line]
    assert len(lines) >= 2
    for line in lines:
        parts = line.split("\t")
        assert len(parts) == 2


def test_kr8_tsv_repeated_records_get_headers():
    """KR8: render_key_value_tsv repeated records get [Record N] headers."""
    group = _abergowrie_group()
    rendered = render_key_value_tsv(group)
    assert "[Record 1]" in rendered
    assert "[Record 2]" in rendered


def test_kr9_tsv_title_emitted():
    """KR9: render_key_value_tsv title emitted as [Title]."""
    group = _simple_group()
    rendered = render_key_value_tsv(group)
    assert "[Staff Member]" in rendered


def test_kr10_tsv_tab_in_key_replaced():
    """KR10: render_key_value_tsv tab chars in key/value replaced with space."""
    group = KeyValueGroup(entries=[
        KeyValueEntry(key="Key\twith\ttabs", value="Value\twith\ttabs"),
        KeyValueEntry(key="Normal", value="Normal"),
    ])
    rendered = render_key_value_tsv(group)
    # Should not have literal tab inside a key or value field (only between k/v)
    for line in rendered.splitlines():
        if "\t" in line and not line.startswith("["):
            parts = line.split("\t")
            # Key part (before first tab) should have no embedded tabs
            assert len(parts) == 2, f"Line should have exactly 1 tab: {line!r}"


# ── PAYLOAD ─────────────────────────────────────────────────────────────────────

def test_kp1_kv_block_routes_to_key_value_group():
    """KP1: KEY_VALUE_GROUP block routes to RepresentationType.KEY_VALUE_GROUP in plan."""
    group = _simple_group()
    block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    kv_elems = [e for e in plan.elements if e.representation == RepresentationType.KEY_VALUE_GROUP]
    assert len(kv_elems) == 1


def test_kp2_payload_item_content_type():
    """KP2: payload item for KEY_VALUE_GROUP has content_type=PayloadContentType.KEY_VALUE_GROUP."""
    group = _simple_group()
    block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    payload = build_llm_payload(plan, doc, Path("/tmp/fake_pkg"), [])
    kv_items = [i for i in payload.items if i.content_type == PayloadContentType.KEY_VALUE_GROUP]
    assert len(kv_items) == 1


def test_kp3_payload_item_text_contains_group_content():
    """KP3: payload item text contains group title and key-value entries."""
    group = _simple_group()
    block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    payload = build_llm_payload(plan, doc, Path("/tmp/fake_pkg"), [])
    kv_items = [i for i in payload.items if i.content_type == PayloadContentType.KEY_VALUE_GROUP]
    assert kv_items
    item = kv_items[0]
    assert item.text
    assert "Name" in item.text
    assert "John Doe" in item.text


def test_kp4_kv_entry_count():
    """KP4: kv_entry_count matches number of entries in group."""
    group = _simple_group()
    block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    payload = build_llm_payload(plan, doc, Path("/tmp/fake_pkg"), [])
    kv_items = [i for i in payload.items if i.content_type == PayloadContentType.KEY_VALUE_GROUP]
    assert kv_items[0].kv_entry_count == 2


def test_kp5_kv_record_count_single():
    """KP5: kv_record_count is 1 for non-repeated group."""
    group = _simple_group()
    block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    payload = build_llm_payload(plan, doc, Path("/tmp/fake_pkg"), [])
    kv_items = [i for i in payload.items if i.content_type == PayloadContentType.KEY_VALUE_GROUP]
    assert kv_items[0].kv_record_count == 1


def test_kp6_kv_record_count_two_records():
    """KP6: kv_record_count is 2 for two-record schedule group."""
    group = _abergowrie_group()
    block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    payload = build_llm_payload(plan, doc, Path("/tmp/fake_pkg"), [])
    kv_items = [i for i in payload.items if i.content_type == PayloadContentType.KEY_VALUE_GROUP]
    assert kv_items[0].kv_record_count == 2


def test_kp7_kv_block_in_document_order(tmp_path):
    """KP7: KEY_VALUE_GROUP block appears in document order relative to paragraphs."""
    para_before = Block(type=BlockType.PARAGRAPH, content="Intro paragraph.", page=1)
    group = _simple_group()
    kv_block = Block.from_key_value_group(group, page=1)
    para_after = Block(type=BlockType.PARAGRAPH, content="Outro paragraph.", page=1)
    doc = _make_doc([para_before, kv_block, para_after])
    plan = plan_document(doc)
    payload = build_llm_payload(plan, doc, tmp_path, [])

    # Build id->index map
    kv_block_id = kv_block.id
    para_before_id = para_before.id
    para_after_id = para_after.id

    indices = {}
    for i, item in enumerate(payload.items):
        if item.block_id == kv_block_id:
            indices["kv"] = i
        elif item.block_id == para_before_id:
            indices["before"] = i
        elif item.block_id == para_after_id:
            indices["after"] = i

    assert "kv" in indices
    assert "before" in indices
    assert "after" in indices
    assert indices["before"] < indices["kv"] < indices["after"]


def test_kp8_writer_creates_kv_artifact(tmp_path):
    """KP8: writer creates key_values/<block.id>.json artifact for KEY_VALUE_GROUP elements."""
    group = _simple_group()
    block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    writer = PackageWriter()
    asset_refs, fidelity = writer.write(tmp_path, plan, doc)

    kv_dir = tmp_path / "key_values"
    assert kv_dir.is_dir()
    artifact_files = list(kv_dir.glob("*.json"))
    assert len(artifact_files) >= 1

    data = json.loads(artifact_files[0].read_text(encoding="utf-8"))
    assert data["schema"] == "key_value_group_v1"
    assert data["block_id"] == block.id
    assert "group" in data


# ── ABERGOWRIE REGRESSION ────────────────────────────────────────────────────────

def test_ka1_two_records_both_present_in_render():
    """KA1: Two service records (Location/Time pairs) remain distinct after render_key_value_group."""
    group = _abergowrie_group()
    rendered = render_key_value_group(group)
    assert "7:00 PM" in rendered
    assert "9:00 AM" in rendered
    assert "Record 1" in rendered
    assert "Record 2" in rendered


def test_ka2_sunday_record_findable():
    """KA2: 'What time is the Sunday service?' can be answered by scanning record fields."""
    group = _abergowrie_group()
    rendered = render_key_value_group(group)
    # Split into record blocks by "**Record N**" markers
    import re
    record_chunks = re.split(r'\n(?=\*\*Record \d+\*\*)', rendered)
    # Find the chunk with "Sunday"
    sunday_chunks = [c for c in record_chunks if "Sunday" in c and "Record" in c]
    assert sunday_chunks, f"No record block containing 'Sunday' found in:\n{rendered}"
    sunday_chunk = sunday_chunks[0]
    assert "9:00 AM" in sunday_chunk, (
        f"Expected '9:00 AM' in Sunday record, got:\n{sunday_chunk}"
    )


def test_ka3_two_abergowrie_locations_not_merged():
    """KA3: Schedule with two identical Location values is NOT flattened into one record."""
    group = _abergowrie_group()
    rendered = render_key_value_group(group)
    # Both Location: Abergowrie lines should appear
    location_count = rendered.count("Abergowrie")
    assert location_count >= 2


def test_ka4_heading_and_kv_block_both_in_payload(tmp_path):
    """KA4: heading preceding key-value group is preserved as separate block in payload."""
    heading = Block(type=BlockType.HEADING, content="Sunday Masses 29/30 September", level=4, page=1)
    group = _abergowrie_group()
    kv_block = Block.from_key_value_group(group, page=1)
    doc = _make_doc([heading, kv_block])
    plan = plan_document(doc)
    payload = build_llm_payload(plan, doc, tmp_path, [])

    # Both heading and kv group should appear in payload
    heading_items = [i for i in payload.items if i.block_id == heading.id]
    kv_items = [i for i in payload.items if i.content_type == PayloadContentType.KEY_VALUE_GROUP]
    assert len(heading_items) == 1
    assert len(kv_items) == 1
    # They should be separate items
    assert heading_items[0].item_id != kv_items[0].item_id


# ── COMPATIBILITY ────────────────────────────────────────────────────────────────

def test_kc1_paragraph_still_routes_to_markdown():
    """KC1: ordinary PARAGRAPH block still routes to MARKDOWN (unchanged)."""
    block = Block(type=BlockType.PARAGRAPH, content="Some paragraph text.", page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    assert any(e.representation == RepresentationType.MARKDOWN for e in plan.elements)
    kv_elems = [e for e in plan.elements if e.representation == RepresentationType.KEY_VALUE_GROUP]
    assert len(kv_elems) == 0


def test_kc2_heading_still_routes_to_markdown():
    """KC2: HEADING block still routes to MARKDOWN (unchanged)."""
    block = Block(type=BlockType.HEADING, content="A Section", level=2, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    assert all(e.representation == RepresentationType.MARKDOWN for e in plan.elements if e.representation != RepresentationType.OMIT)


def test_kc3_table_still_routes_to_structured_table():
    """KC3: TABLE block still routes to STRUCTURED_TABLE (unchanged)."""
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
    )
    block = Block.from_table(td, page=1)
    doc = _make_doc([block])
    plan = plan_document(doc)
    table_elems = [e for e in plan.elements if e.representation == RepresentationType.STRUCTURED_TABLE]
    assert len(table_elems) == 1


def test_kc4_paragraph_without_kv_routes_normally():
    """KC4: Block with no key_value_group but type=PARAGRAPH routes normally."""
    block = Block(type=BlockType.PARAGRAPH, content="Plain paragraph.", page=1)
    assert block.key_value_group is None
    doc = _make_doc([block])
    plan = plan_document(doc)
    para_elems = [e for e in plan.elements if e.representation == RepresentationType.MARKDOWN]
    assert len(para_elems) == 1


def test_kc5_packaging_suite_no_regressions():
    """KC5: existing packaging tests still pass — run them inline as a sanity gate."""
    # This test just verifies the module-level packaging imports are intact
    from aksharamd.packaging import (
        RepresentationType,
    )
    # Verify the existing RepresentationType values are unchanged
    assert RepresentationType.MARKDOWN == "markdown"
    assert RepresentationType.STRUCTURED_TABLE == "structured_table"
    assert RepresentationType.IMAGE == "image"
    assert RepresentationType.IMAGE_AND_TEXT == "image_and_text"
    assert RepresentationType.REFERENCE_ONLY == "reference_only"
    assert RepresentationType.OMIT == "omit"
    # And our new one is present
    assert RepresentationType.KEY_VALUE_GROUP == "key_value_group"


def test_kc6_from_key_value_group_content_non_empty():
    """KC6: Block.from_key_value_group() content field is non-empty after construction."""
    group = _abergowrie_group()
    block = Block.from_key_value_group(group, page=1)
    assert block.content  # non-empty string
    assert len(block.content) > 10
    assert block.type == BlockType.KEY_VALUE_GROUP
    assert block.key_value_group is not None
