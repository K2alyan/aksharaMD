from __future__ import annotations

import json

from aksharamd.plugins.parsers.data import _summarise_json

# ── _summarise_json ─────────────────────────────────────────────────────────

def test_summarise_flat_dict():
    lines = _summarise_json({"name": "Alice", "age": 30})
    text = "\n".join(lines)
    assert "name" in text
    assert "Alice" in text
    assert "age" in text


def test_summarise_nested_dict():
    obj = {"outer": {"inner": "value"}}
    lines = _summarise_json(obj)
    text = "\n".join(lines)
    assert "outer" in text
    assert "dict" in text


def test_summarise_list():
    lines = _summarise_json([1, 2, 3])
    text = "\n".join(lines)
    assert "3 items" in text


def test_summarise_list_with_dicts():
    obj = [{"x": 1}, {"x": 2}, {"x": 3}, {"x": 4}]
    lines = _summarise_json(obj)
    text = "\n".join(lines)
    assert "4 items" in text
    assert "1 more" in text


def test_summarise_max_depth():
    deep = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
    lines = _summarise_json(deep, max_depth=2)
    assert "..." in "\n".join(lines)


def test_summarise_scalar():
    lines = _summarise_json("hello")
    assert "hello" in "\n".join(lines)


def test_summarise_long_value_truncated():
    obj = {"key": "x" * 200}
    lines = _summarise_json(obj)
    # value should be capped at 120 chars
    assert len("\n".join(lines)) < 300


# ── JsonParser ──────────────────────────────────────────────────────────────

def test_json_parser_simple(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import JsonParser

    data = {"project": "aksharamd", "version": "0.1.0", "active": True}
    f = tmp_path / "sample.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = JsonParser().execute(ctx)

    assert ctx.document is not None
    assert ctx.document.file_type == "json"
    types = {b.type for b in ctx.document.blocks}
    assert BlockType.METADATA in types
    assert BlockType.PARAGRAPH in types


def test_json_parser_small_file_includes_raw(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import JsonParser

    data = [1, 2, 3]
    f = tmp_path / "tiny.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = JsonParser().execute(ctx)

    code_blocks = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert code_blocks
    assert "1" in code_blocks[0].content


def test_json_parser_invalid_json_falls_back(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import JsonParser

    f = tmp_path / "bad.json"
    f.write_text("{not valid json", encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = JsonParser().execute(ctx)

    # Should still produce a document with a code block fallback
    assert ctx.document is not None
    code_blocks = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert code_blocks


# ── JsonlParser ─────────────────────────────────────────────────────────────

def test_jsonl_parser_structured(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.data import JsonlParser

    records = [{"id": i, "val": f"item_{i}"} for i in range(5)]
    f = tmp_path / "records.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = JsonlParser().execute(ctx)

    assert ctx.document is not None
    meta_block = ctx.document.blocks[0]
    assert "Records: 5" in meta_block.content


def test_jsonl_parser_schema_shown(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import JsonlParser

    records = [{"name": "a", "score": 1}, {"name": "b", "score": 2}]
    f = tmp_path / "schema.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = JsonlParser().execute(ctx)

    paragraphs = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert any("name" in b.content for b in paragraphs)


def test_jsonl_parser_plain_text_lines(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import JsonlParser

    f = tmp_path / "plain.jsonl"
    f.write_text("hello world\nfoo bar\nbaz qux\n", encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = JsonlParser().execute(ctx)

    paragraphs = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    contents = " ".join(b.content for b in paragraphs)
    assert "hello world" in contents


def test_jsonl_parser_empty_lines_skipped(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.data import JsonlParser

    f = tmp_path / "sparse.jsonl"
    f.write_text('\n{"a": 1}\n\n{"b": 2}\n\n', encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = JsonlParser().execute(ctx)

    assert ctx.document.metadata["records"] == 2


# ── XmlParser ───────────────────────────────────────────────────────────────

def test_xml_parser_simple(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import XmlParser

    # Root with >5 children forces recursion; <title> child is then detected as a heading
    items = "\n".join(f"  <p>Paragraph number {i} with enough words to count.</p>" for i in range(6))
    xml = f"<?xml version='1.0'?>\n<root>\n  <title>Test Document</title>\n{items}\n</root>"
    f = tmp_path / "doc.xml"
    f.write_text(xml, encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = XmlParser().execute(ctx)

    assert ctx.document is not None
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert any("Test Document" in b.content for b in headings)


def test_xml_parser_title_used_as_document_title(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.data import XmlParser

    # >5 children so root recurses; <title> is detected and sets document title
    items = "\n".join(f"  <p>Word-rich paragraph {i} has some text in it.</p>" for i in range(6))
    xml = f"<root>\n  <title>My Title</title>\n{items}\n</root>"
    f = tmp_path / "titled.xml"
    f.write_text(xml, encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = XmlParser().execute(ctx)

    assert ctx.document.title == "My Title"


def test_xml_parser_invalid_falls_back_with_error(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.data import XmlParser

    f = tmp_path / "broken.xml"
    f.write_text("<root><unclosed>", encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = XmlParser().execute(ctx)

    assert ctx.document is None
    assert not ctx.validation.passed


def test_xml_parser_sparse_extracts_leaf_text(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import XmlParser

    # Single element with short text — now extracted as "item: hi" paragraph
    xml = "<root><item>hi</item></root>"
    f = tmp_path / "sparse.xml"
    f.write_text(xml, encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = XmlParser().execute(ctx)

    para_blocks = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert any("item" in b.content and "hi" in b.content for b in para_blocks)


def test_xml_parser_many_elements(tmp_path):
    from aksharamd.context import CompilationContext
    from aksharamd.models.block import BlockType
    from aksharamd.plugins.parsers.data import XmlParser

    items = "\n".join(f"<article><p>Article number {i} has meaningful text content here.</p></article>" for i in range(20))
    xml = f"<feed>{items}</feed>"
    f = tmp_path / "feed.xml"
    f.write_text(xml, encoding="utf-8")

    ctx = CompilationContext(source=str(f), output_dir=str(tmp_path / "out"))
    ctx = XmlParser().execute(ctx)

    paragraphs = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paragraphs) >= 5
