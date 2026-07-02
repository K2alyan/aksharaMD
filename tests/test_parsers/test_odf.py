from __future__ import annotations

import io
import zipfile
from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType

_CONTENT_XML_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<office:document-content
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
    xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
    office:version="1.2">
  <office:automatic-styles>
    <style:style style:name="Heading1" style:family="paragraph" style:parent-style-name="Heading_20_1"/>
    <style:style style:name="Body" style:family="paragraph"/>
  </office:automatic-styles>
  <office:body>
    <office:text>
      {body}
    </office:text>
  </office:body>
</office:document-content>"""

_META_XML_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<office:document-meta
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    office:version="1.2">
  <office:meta>
    <dc:title>{title}</dc:title>
    <dc:creator>{creator}</dc:creator>
  </office:meta>
</office:document-meta>"""

_MANIFEST_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
  <manifest:file-entry manifest:media-type="application/vnd.oasis.opendocument.text" manifest:full-path="/"/>
  <manifest:file-entry manifest:media-type="text/xml" manifest:full-path="content.xml"/>
  <manifest:file-entry manifest:media-type="text/xml" manifest:full-path="meta.xml"/>
</manifest:manifest>"""


def _make_odt(tmp_path: Path, body: str = "", title: str = "Test", creator: str = "Author", ext: str = "odt") -> Path:
    content = _CONTENT_XML_TEMPLATE.format(body=body)
    meta = _META_XML_TEMPLATE.format(title=title, creator=creator)

    path = tmp_path / f"test.{ext}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, "application/vnd.oasis.opendocument.text")
        zf.writestr("META-INF/manifest.xml", _MANIFEST_XML)
        zf.writestr("content.xml", content)
        zf.writestr("meta.xml", meta)
    path.write_bytes(buf.getvalue())
    return path


def _compile(path: Path, tmp_path: Path):
    return Compiler(output_dir=str(tmp_path / "out")).compile(str(path))


_H1_BODY = '<text:h text:style-name="Heading_20_1" text:outline-level="1">My Title</text:h>'
_PARA_BODY = '<text:p text:style-name="Text_20_Body">Hello world paragraph content.</text:p>'
_H1_PARA = _H1_BODY + _PARA_BODY


# ── basic parsing ─────────────────────────────────────────────────────────────

def test_odt_produces_document(tmp_path):
    path = _make_odt(tmp_path, body=_H1_PARA)
    ctx = _compile(path, tmp_path)
    assert ctx.document is not None
    assert ctx.document.file_type == "odt"


def test_odt_metadata_from_meta_xml(tmp_path):
    path = _make_odt(tmp_path, body=_PARA_BODY, title="My Book", creator="Jane Doe")
    ctx = _compile(path, tmp_path)
    assert ctx.document is not None
    assert ctx.document.title == "My Book"
    assert ctx.document.author == "Jane Doe"


def test_odt_heading_extracted(tmp_path):
    path = _make_odt(tmp_path, body=_H1_BODY + _PARA_BODY)
    ctx = _compile(path, tmp_path)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert len(headings) >= 1
    assert "My Title" in headings[0].content


def test_odt_paragraph_extracted(tmp_path):
    path = _make_odt(tmp_path, body=_PARA_BODY)
    ctx = _compile(path, tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) >= 1
    assert "Hello world" in paras[0].content


def test_odt_empty_body_does_not_crash(tmp_path):
    path = _make_odt(tmp_path, body="")
    ctx = _compile(path, tmp_path)
    # Empty body may produce no document (PARSE_FAILED) or an empty document — both OK
    assert ctx is not None


def test_odt_multiple_paragraphs(tmp_path):
    body = (
        '<text:p text:style-name="Body">First paragraph.</text:p>'
        '<text:p text:style-name="Body">Second paragraph.</text:p>'
        '<text:p text:style-name="Body">Third paragraph.</text:p>'
    )
    path = _make_odt(tmp_path, body=body)
    ctx = _compile(path, tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) >= 2


# ── error handling ────────────────────────────────────────────────────────────

def test_odt_corrupt_zip_does_not_crash(tmp_path):
    path = tmp_path / "corrupt.odt"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
    ctx = _compile(path, tmp_path)
    assert ctx is not None


def test_odt_empty_file_does_not_crash(tmp_path):
    path = tmp_path / "empty.odt"
    path.write_bytes(b"")
    ctx = _compile(path, tmp_path)
    assert ctx is not None


# ── ODF table ─────────────────────────────────────────────────────────────────

def test_odt_table_extracted(tmp_path):
    table_body = """
<table:table table:name="TestTable" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
  <table:table-row>
    <table:table-cell><text:p>Name</text:p></table:table-cell>
    <table:table-cell><text:p>Value</text:p></table:table-cell>
  </table:table-row>
  <table:table-row>
    <table:table-cell><text:p>Alice</text:p></table:table-cell>
    <table:table-cell><text:p>42</text:p></table:table-cell>
  </table:table-row>
</table:table>"""
    path = _make_odt(tmp_path, body=table_body)
    ctx = _compile(path, tmp_path)
    assert ctx.document is not None
    # Either table block or paragraph blocks should be present
    assert len(ctx.document.blocks) > 0
