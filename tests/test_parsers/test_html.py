from __future__ import annotations

from pathlib import Path

from aksharamd.context import CompilationContext
from aksharamd.models.block import BlockType
from aksharamd.plugins.parsers.html import HTMLParser


def _parse(html: str, tmp_path: Path) -> CompilationContext:
    p = tmp_path / "test.html"
    p.write_text(html, encoding="utf-8")
    ctx = CompilationContext(source=str(p), output_dir=str(tmp_path / "out"))
    return HTMLParser().execute(ctx)


def test_heading_levels(tmp_path):
    ctx = _parse("<html><body><h1>Title</h1><h2>Sub</h2><h3>Deep</h3></body></html>", tmp_path)
    headings = [b for b in ctx.document.blocks if b.type == BlockType.HEADING]
    assert headings[0].level == 1 and headings[0].content == "Title"
    assert headings[1].level == 2 and headings[1].content == "Sub"
    assert headings[2].level == 3 and headings[2].content == "Deep"


def test_paragraph(tmp_path):
    ctx = _parse("<html><body><p>Hello world.</p></body></html>", tmp_path)
    paras = [b for b in ctx.document.blocks if b.type == BlockType.PARAGRAPH]
    assert any("Hello world" in b.content for b in paras)


def test_flat_unordered_list(tmp_path):
    ctx = _parse("<html><body><ul><li>A</li><li>B</li><li>C</li></ul></body></html>", tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    assert "- A" in lists[0].content
    assert "- B" in lists[0].content


def test_nested_list(tmp_path):
    html = """<html><body>
    <ul>
      <li>Top</li>
      <li>Parent<ul><li>Child</li><li>Child2</li></ul></li>
    </ul></body></html>"""
    ctx = _parse(html, tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert len(lists) == 1
    content = lists[0].content
    assert "- Top" in content
    assert "  - Child" in content


def test_ordered_list(tmp_path):
    ctx = _parse("<html><body><ol><li>One</li><li>Two</li></ol></body></html>", tmp_path)
    lists = [b for b in ctx.document.blocks if b.type == BlockType.LIST]
    assert "1. One" in lists[0].content
    assert "2. Two" in lists[0].content


def test_table(tmp_path):
    html = """<html><body><table>
    <tr><th>Name</th><th>Age</th></tr>
    <tr><td>Alice</td><td>30</td></tr>
    </table></body></html>"""
    ctx = _parse(html, tmp_path)
    tables = [b for b in ctx.document.blocks if b.type == BlockType.TABLE]
    assert len(tables) == 1
    assert "Name" in tables[0].content and "Alice" in tables[0].content


def test_code_block(tmp_path):
    html = '<html><body><pre><code class="language-python">x = 1</code></pre></body></html>'
    ctx = _parse(html, tmp_path)
    code = [b for b in ctx.document.blocks if b.type == BlockType.CODE_BLOCK]
    assert len(code) == 1
    assert code[0].language == "python"
    assert "x = 1" in code[0].content


def test_blockquote(tmp_path):
    ctx = _parse("<html><body><blockquote>Quoted text here.</blockquote></body></html>", tmp_path)
    bqs = [b for b in ctx.document.blocks if b.type == BlockType.BLOCKQUOTE]
    assert len(bqs) == 1 and "Quoted text" in bqs[0].content


def test_nav_and_script_skipped(tmp_path):
    html = """<html><body>
    <nav>Skip me</nav>
    <script>alert('skip')</script>
    <p>Keep me</p>
    </body></html>"""
    ctx = _parse(html, tmp_path)
    all_text = " ".join(b.content for b in ctx.document.blocks)
    assert "Skip me" not in all_text
    assert "alert" not in all_text
    assert "Keep me" in all_text


def test_remote_image_not_fetched(tmp_path):
    html = '<html><body><img src="https://example.com/photo.png" alt="Photo"></body></html>'
    ctx = _parse(html, tmp_path)
    images = [b for b in ctx.document.blocks if b.type == BlockType.IMAGE]
    assert len(images) == 1
    # image_bytes should be None — no network fetch
    asset = ctx.document.assets[0]
    assert asset.image_bytes is None


def test_image_path_traversal_blocked(tmp_path):
    # Craft HTML that tries to read a file outside the document directory
    html = '<html><body><img src="../../../etc/passwd" alt="evil"></body></html>'
    ctx = _parse(html, tmp_path)
    images = [b for b in ctx.document.blocks if b.type == BlockType.IMAGE]
    assert len(images) == 1
    asset = ctx.document.assets[0]
    # Must NOT have loaded bytes from outside the safe root
    assert asset.image_bytes is None


def test_empty_body_produces_empty_document(tmp_path):
    ctx = _parse("<html><body></body></html>", tmp_path)
    assert ctx.document is not None
    assert len(ctx.document.blocks) == 0


def test_title_extracted(tmp_path):
    ctx = _parse("<html><head><title>My Doc</title></head><body><p>hi</p></body></html>", tmp_path)
    assert ctx.document.title == "My Doc"


# ── Admonition blocks ────────────────────────────────────────────────────────

def test_admonition_by_css_class(tmp_path):
    """A <blockquote class='note'> should produce an ADMONITION, not BLOCKQUOTE."""
    html = "<html><body><blockquote class='note'><p>This is a note.</p></blockquote></body></html>"
    ctx = _parse(html, tmp_path)
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(admonitions) == 1
    assert admonitions[0].metadata.get("admonition_type") == "note"
    assert "note" in admonitions[0].content.lower()


def test_admonition_warning_class(tmp_path):
    """A <blockquote class='warning'> should produce an ADMONITION with type warning."""
    html = "<html><body><blockquote class='warning'><p>Careful here.</p></blockquote></body></html>"
    ctx = _parse(html, tmp_path)
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(admonitions) == 1
    assert admonitions[0].metadata.get("admonition_type") == "warning"


def test_admonition_github_pattern_in_html(tmp_path):
    """A blockquote whose first paragraph starts with [!NOTE] should be an ADMONITION."""
    html = "<html><body><blockquote><p>[!NOTE] Something important.</p></blockquote></body></html>"
    ctx = _parse(html, tmp_path)
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(admonitions) == 1
    assert admonitions[0].metadata.get("admonition_type") == "note"


def test_plain_blockquote_html_unchanged(tmp_path):
    """A <blockquote> without any admonition markers stays as BLOCKQUOTE."""
    html = "<html><body><blockquote><p>Just a quote.</p></blockquote></body></html>"
    ctx = _parse(html, tmp_path)
    bqs = [b for b in ctx.document.blocks if b.type == BlockType.BLOCKQUOTE]
    admonitions = [b for b in ctx.document.blocks if b.type == BlockType.ADMONITION]
    assert len(bqs) == 1
    assert len(admonitions) == 0
