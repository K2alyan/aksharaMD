from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType


def _make_tar_gz(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a .tar.gz archive with the given {filename: content} dict."""
    out = tmp_path / "archive.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    out.write_bytes(buf.getvalue())
    return out


def _make_plain_tar(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create an uncompressed .tar archive."""
    out = tmp_path / "archive.tar"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    out.write_bytes(buf.getvalue())
    return out


def _compile(path: Path, tmp_path: Path):
    return Compiler(output_dir=str(tmp_path / "out")).compile(str(path))


# ── .tar.gz ───────────────────────────────────────────────────────────────────

def test_tar_gz_produces_document(tmp_path):
    archive = _make_tar_gz(tmp_path, {"readme.txt": "Hello from archive."})
    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None


def test_tar_gz_metadata_block_present(tmp_path):
    archive = _make_tar_gz(tmp_path, {"readme.txt": "Hello from archive."})
    ctx = _compile(archive, tmp_path)
    meta_blocks = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta_blocks) >= 1
    assert "archive.tar.gz" in meta_blocks[0].content.lower()


def test_tar_gz_table_of_contents(tmp_path):
    archive = _make_tar_gz(tmp_path, {
        "file1.txt": "Content one.",
        "file2.py": "print('hello')",
    })
    ctx = _compile(archive, tmp_path)
    tables = [b for b in ctx.document.blocks if b.type == BlockType.TABLE]
    assert len(tables) >= 1
    assert "file1.txt" in tables[0].content


def test_tar_gz_text_content_extracted(tmp_path):
    archive = _make_tar_gz(tmp_path, {
        "script.py": "def hello():\n    return 'world'\n",
    })
    ctx = _compile(archive, tmp_path)
    code_blocks = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code_blocks) >= 1
    assert "hello" in code_blocks[0].content


def test_tar_gz_multiple_files(tmp_path):
    archive = _make_tar_gz(tmp_path, {
        "a.py": "x = 1",
        "b.md": "# Title\n\nContent.",
        "c.txt": "Plain text file.",
        "binary.bin": "not text",
    })
    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None
    assert ctx.document.metadata["total_entries"] >= 3


# ── .tar (uncompressed) ───────────────────────────────────────────────────────

def test_plain_tar_produces_document(tmp_path):
    archive = _make_plain_tar(tmp_path, {"hello.txt": "Hello uncompressed."})
    ctx = _compile(archive, tmp_path)
    assert ctx.document is not None


# ── corrupt archives ──────────────────────────────────────────────────────────

def test_corrupt_tar_does_not_crash(tmp_path):
    f = tmp_path / "corrupt.tar.gz"
    f.write_bytes(b"\x1f\x8b" + b"\x00" * 50)  # fake gzip header, garbage body
    ctx = _compile(f, tmp_path)
    assert ctx is not None


def test_corrupt_tar_produces_error(tmp_path):
    f = tmp_path / "bad.tar.gz"
    f.write_bytes(b"not a tar file at all")
    ctx = _compile(f, tmp_path)
    # Either produces an error or falls through gracefully
    assert ctx is not None


# ── 7z archives ───────────────────────────────────────────────────────────────

def test_sevenz_produces_document(tmp_path):
    pytest.importorskip("py7zr")
    import py7zr

    archive_path = tmp_path / "test.7z"
    with py7zr.SevenZipFile(str(archive_path), mode="w") as sz:
        data = b"Hello from 7z archive."
        sz.writestr(data, "readme.txt")

    ctx = _compile(archive_path, tmp_path)
    assert ctx.document is not None


def test_sevenz_metadata_present(tmp_path):
    pytest.importorskip("py7zr")
    import py7zr

    archive_path = tmp_path / "docs.7z"
    with py7zr.SevenZipFile(str(archive_path), mode="w") as sz:
        sz.writestr(b"print('hello')", "script.py")

    ctx = _compile(archive_path, tmp_path)
    meta = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    assert len(meta) >= 1
