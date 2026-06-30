from __future__ import annotations

# Import the tool functions directly (they're plain Python callables)
from aksharamd.mcp_server import (
    compile_document,
    get_stats,
    get_supported_formats,
)


def test_compile_document_not_found():
    result = compile_document("/nonexistent/path/file.pdf")
    assert result.startswith("Error:")
    assert "not found" in result


def test_compile_document_directory(tmp_path):
    result = compile_document(str(tmp_path))
    assert result.startswith("Error:")
    assert "not a file" in result


def test_compile_document_empty_file(tmp_path):
    f = tmp_path / "empty.md"
    f.write_bytes(b"")
    result = compile_document(str(f))
    assert result.startswith("Error:")
    assert "empty" in result


def test_compile_document_markdown(tmp_path):
    f = tmp_path / "sample.md"
    f.write_text("# Hello\n\nThis is a test document.\n", encoding="utf-8")
    result = compile_document(str(f))
    assert isinstance(result, str)
    assert "Hello" in result
    assert "AksharaMD compilation summary" in result


def test_compile_document_html(tmp_path):
    f = tmp_path / "sample.html"
    f.write_text("<html><body><h1>Title</h1><p>Content.</p></body></html>", encoding="utf-8")
    result = compile_document(str(f))
    assert "Title" in result
    assert "Content" in result


def test_compile_document_txt(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("Short document.\n\nTwo paragraphs.", encoding="utf-8")
    result = compile_document(str(f))
    assert "Short document" in result


def test_compile_document_rst(tmp_path):
    f = tmp_path / "sample.rst"
    f.write_text("Title\n=====\n\nSome RST content.\n", encoding="utf-8")
    result = compile_document(str(f))
    assert "Title" in result or "RST" in result or "content" in result.lower()


def test_compile_document_summary_block(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# Doc\n\nParagraph.\n", encoding="utf-8")
    result = compile_document(str(f))
    assert "Tokens:" in result
    assert "Confidence:" in result
    assert "Time:" in result


def test_get_supported_formats():
    result = get_supported_formats()
    assert isinstance(result, str)
    assert "pdf" in result.lower()
    assert "docx" in result.lower()
    assert "html" in result.lower()
    assert "csv" in result.lower()
    assert "Total:" in result


def test_get_stats_returns_string():
    result = get_stats()
    assert isinstance(result, str)
    # Either "no compilations" (fresh install) or the stats table
    assert len(result) > 0


def test_compile_unknown_extension(tmp_path):
    f = tmp_path / "file.xyz123"
    f.write_text("content", encoding="utf-8")
    result = compile_document(str(f))
    # Should return an error or empty-content message, not crash
    assert isinstance(result, str)
