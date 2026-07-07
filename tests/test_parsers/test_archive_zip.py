from __future__ import annotations

import io
import zipfile
from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType


def _make_zip(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """Create a ZIP archive with the given {filename: content} dict."""
    out = tmp_path / "archive.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    out.write_bytes(buf.getvalue())
    return out


def _compile(path: Path, tmp_path: Path):
    return Compiler(output_dir=str(tmp_path / "out")).compile(str(path))


# ── Basic functionality ───────────────────────────────────────────────────────

def test_zip_produces_document(tmp_path):
    archive = _make_zip(tmp_path, {"readme.txt": b"Hello from zip."})
    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None


def test_zip_metadata_block_present(tmp_path):
    archive = _make_zip(tmp_path, {"readme.txt": b"Hello from zip."})
    ctx = _compile(archive, tmp_path)
    meta_blocks = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta_blocks) >= 1
    assert "archive.zip" in meta_blocks[0].content.lower()


def test_zip_table_of_contents(tmp_path):
    archive = _make_zip(tmp_path, {
        "file1.txt": b"Content one.",
        "file2.py": b"print('hello')",
    })
    ctx = _compile(archive, tmp_path)
    tables = [b for b in ctx.document.blocks if b.type == BlockType.TABLE]
    assert len(tables) >= 1
    assert "file1.txt" in tables[0].content


def test_zip_text_content_extracted(tmp_path):
    archive = _make_zip(tmp_path, {"script.py": b"def hello():\n    return 'world'\n"})
    ctx = _compile(archive, tmp_path)
    code_blocks = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code_blocks) >= 1
    assert "hello" in code_blocks[0].content


# ── Listing sanitization ──────────────────────────────────────────────────────

def test_zip_listing_sanitizes_dotdot_names(tmp_path):
    """Member names with ../ must appear sanitized in the listing table."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("../../evil.txt", "evil content")
        zf.writestr("normal.txt", "normal content")
    archive = tmp_path / "traversal.zip"
    archive.write_bytes(buf.getvalue())

    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None

    all_content = " ".join(b.content for b in ctx.document.blocks)
    assert "../../evil.txt" not in all_content
    assert "__/__/evil.txt" in all_content
    assert "normal.txt" in all_content


def test_zip_listing_sanitizes_absolute_paths(tmp_path):
    """/absolute/path entries must be stripped of leading slash in the listing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("/etc/shadow", "root:x:0:0")
        zf.writestr("safe.txt", "safe content")
    archive = tmp_path / "absolute.zip"
    archive.write_bytes(buf.getvalue())

    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None

    all_content = " ".join(b.content for b in ctx.document.blocks)
    assert "/etc/shadow" not in all_content
    assert "etc/shadow" in all_content


def test_zip_listing_sanitizes_windows_paths(tmp_path):
    """Windows drive-letter paths must be stripped of the drive prefix."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("C:\\Windows\\system32\\drivers\\etc\\hosts", "127.0.0.1 localhost")
    archive = tmp_path / "windows.zip"
    archive.write_bytes(buf.getvalue())

    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None

    all_content = " ".join(b.content for b in ctx.document.blocks)
    assert "C:\\" not in all_content
    assert "C:/" not in all_content
    assert "Windows" in all_content


def test_zip_listing_preserves_normal_nested_paths(tmp_path):
    """Normal nested paths must pass through unchanged."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("docs/readme.md", "# Readme\n\nHello world.")
    archive = tmp_path / "normal.zip"
    archive.write_bytes(buf.getvalue())

    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None

    all_content = " ".join(b.content for b in ctx.document.blocks)
    assert "docs/readme.md" in all_content


# ── File-heading sanitization ─────────────────────────────────────────────────

def test_zip_heading_sanitizes_dotdot_names(tmp_path):
    """Text-file headings must also use sanitized names."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("../../inject.py", "print('pwned')")
    archive = tmp_path / "heading_traversal.zip"
    archive.write_bytes(buf.getvalue())

    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None

    heading_contents = [b.content for b in ctx.document.blocks if b.type == BlockType.HEADING]
    all_headings = " ".join(heading_contents)
    assert "../../inject.py" not in all_headings
    assert "__/__/inject.py" in all_headings


def test_zip_heading_sanitizes_absolute_paths(tmp_path):
    """/absolute/path text-file headings must drop the leading slash."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("/opt/app/config.py", "SECRET = 'leaked'")
    archive = tmp_path / "heading_absolute.zip"
    archive.write_bytes(buf.getvalue())

    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None

    heading_contents = [b.content for b in ctx.document.blocks if b.type == BlockType.HEADING]
    all_headings = " ".join(heading_contents)
    assert "/opt/app/config.py" not in all_headings
    assert "opt/app/config.py" in all_headings
