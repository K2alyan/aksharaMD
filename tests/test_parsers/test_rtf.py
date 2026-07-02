from __future__ import annotations

from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType

_MINIMAL_RTF = r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Times New Roman;}} \f0\fs24 Hello World}"

_RTF_WITH_PARAGRAPHS = (
    r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Arial;}}"
    r"\f0\fs24 First paragraph content.\par\par"
    r"Second paragraph content.\par\par"
    r"Third paragraph here.}"
)


def _compile_rtf(tmp_path: Path, content: str, filename: str = "doc.rtf"):
    f = tmp_path / filename
    f.write_text(content, encoding="utf-8")
    return Compiler(output_dir=str(tmp_path / "out")).compile(str(f))


def test_rtf_produces_document(tmp_path):
    ctx = _compile_rtf(tmp_path, _MINIMAL_RTF)
    assert ctx.document is not None
    assert ctx.document.file_type == "rtf"


def test_rtf_has_text_blocks(tmp_path):
    ctx = _compile_rtf(tmp_path, _MINIMAL_RTF)
    text_blocks = [b for b in ctx.document.blocks
                   if b.type in (BlockType.PARAGRAPH, BlockType.HEADING)]
    assert len(text_blocks) >= 1


def test_rtf_title_set(tmp_path):
    ctx = _compile_rtf(tmp_path, _MINIMAL_RTF)
    assert ctx.document.title is not None


def test_rtf_multiple_paragraphs(tmp_path):
    ctx = _compile_rtf(tmp_path, _RTF_WITH_PARAGRAPHS)
    assert ctx.document is not None
    # At least some text extracted
    all_content = " ".join(b.content for b in ctx.document.blocks)
    assert len(all_content) > 5


def test_rtf_corrupt_file_does_not_crash(tmp_path):
    f = tmp_path / "corrupt.rtf"
    f.write_bytes(b"\xff\xfe not rtf content at all")
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    assert ctx is not None


def test_rtf_empty_file(tmp_path):
    f = tmp_path / "empty.rtf"
    f.write_bytes(b"")
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    assert ctx is not None
