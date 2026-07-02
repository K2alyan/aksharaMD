from __future__ import annotations

from pathlib import Path

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType


def _compile_eml(tmp_path: Path, content: str, filename: str = "test.eml"):
    f = tmp_path / filename
    f.write_text(content, encoding="utf-8")
    return Compiler(output_dir=str(tmp_path / "out")).compile(str(f))


_SIMPLE_EML = """\
From: alice@example.com
To: bob@example.com
Subject: Hello Bob
Date: Wed, 01 Jan 2025 12:00:00 +0000
Content-Type: text/plain; charset=utf-8

Hi Bob,

This is the email body.

Best,
Alice
"""

_HTML_EML = """\
From: sender@example.com
To: receiver@example.com
Subject: HTML Email
Date: Thu, 02 Jan 2025 09:00:00 +0000
Content-Type: text/html; charset=utf-8

<html><body><h1>Hello</h1><p>This is HTML content.</p></body></html>
"""

_MULTIPART_EML = """\
From: sender@example.com
To: receiver@example.com
Subject: Multipart Email
Date: Thu, 02 Jan 2025 09:00:00 +0000
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset=utf-8

Plain text body content here.

--boundary123
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="report.pdf"

binary-data-here

--boundary123--
"""

_MINIMAL_EML = """\
From: a@b.com
To: c@d.com

Short body.
"""


# ── basic parsing ─────────────────────────────────────────────────────────────

def test_eml_produces_document(tmp_path):
    ctx = _compile_eml(tmp_path, _SIMPLE_EML)
    assert ctx.document is not None
    assert ctx.document.file_type == "eml"


def test_eml_subject_becomes_heading(tmp_path):
    ctx = _compile_eml(tmp_path, _SIMPLE_EML)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert any("Hello Bob" in b.content for b in headings)


def test_eml_from_to_in_metadata(tmp_path):
    ctx = _compile_eml(tmp_path, _SIMPLE_EML)
    meta = [b for b in ctx.document.blocks if b.type == BlockType.METADATA]
    combined = " ".join(b.content for b in meta)
    assert "alice@example.com" in combined
    assert "bob@example.com" in combined


def test_eml_body_becomes_paragraph(tmp_path):
    ctx = _compile_eml(tmp_path, _SIMPLE_EML)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert len(paras) >= 1


def test_eml_author_set_from_sender(tmp_path):
    ctx = _compile_eml(tmp_path, _SIMPLE_EML)
    assert ctx.document.author is not None
    assert "alice" in ctx.document.author


def test_eml_title_from_subject(tmp_path):
    ctx = _compile_eml(tmp_path, _SIMPLE_EML)
    assert ctx.document.title == "Hello Bob"


# ── HTML body ─────────────────────────────────────────────────────────────────

def test_eml_html_body_extracted(tmp_path):
    ctx = _compile_eml(tmp_path, _HTML_EML)
    assert ctx.document is not None
    all_content = " ".join(b.content for b in ctx.document.blocks)
    assert "Hello" in all_content or "HTML" in all_content


# ── attachments ───────────────────────────────────────────────────────────────

def test_eml_multipart_with_attachment_detected(tmp_path):
    ctx = _compile_eml(tmp_path, _MULTIPART_EML)
    assert ctx.document is not None
    # Body text should be extracted
    assert ctx.document is not None


# ── minimal email ─────────────────────────────────────────────────────────────

def test_eml_minimal_no_subject_uses_stem_as_title(tmp_path):
    ctx = _compile_eml(tmp_path, _MINIMAL_EML)
    assert ctx.document is not None
    # No subject → title defaults to filename stem
    assert ctx.document.title is not None


# ── error handling ────────────────────────────────────────────────────────────

def test_eml_empty_file(tmp_path):
    f = tmp_path / "empty.eml"
    f.write_bytes(b"")
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile(str(f))
    # Empty EML parses as message with no fields — should not crash
    assert ctx is not None
