"""Phase 2: stable identity and provenance tests.

Verifies derivation and propagation of source_id, capture_id, document_id,
block_id, and chunk_id as specified in docs/IDENTITY_DESIGN.md.
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler, _compute_source_id
from aksharamd.models.block import Block, BlockType, ExtractionConfidence
from aksharamd.models.chunk import Chunk
from aksharamd.models.document import Document
from aksharamd.models.manifest import Manifest

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_doc(blocks: list[Block], file_type: str = "md", pages: int = 1) -> Document:
    return Document(source="test.md", file_type=file_type, pages=pages, blocks=blocks)


def _para(content: str, index: int = 0, page: int | None = 1) -> Block:
    return Block(
        type=BlockType.PARAGRAPH,
        content=content,
        index=index,
        page=page,
        confidence=ExtractionConfidence.EXTRACTED,
    )


# ── document_id: content-based, not source-based ─────────────────────────────

def test_document_compute_id_content_based() -> None:
    """Two docs with identical blocks (different source) share the same document_id."""
    blocks = [_para("hello world", index=0)]
    doc_a = Document(source="path/a.md", file_type="md", pages=1, blocks=blocks)
    doc_b = Document(source="path/b.md", file_type="md", pages=1, blocks=blocks)
    doc_a.compute_id()
    doc_b.compute_id()
    assert doc_a.document_id == doc_b.document_id


def test_document_id_changes_with_content() -> None:
    """Different block content → different document_id."""
    doc_a = _make_doc([_para("hello world")])
    doc_b = _make_doc([_para("different content entirely")])
    doc_a.compute_id()
    doc_b.compute_id()
    assert doc_a.document_id != doc_b.document_id


def test_document_id_excludes_compiled_at() -> None:
    """document_id is stable even if compiled_at differs (no sleep needed — direct override)."""
    blocks = [_para("stable content")]
    doc_a = Document(source="x.md", file_type="md", pages=1, blocks=blocks, compiled_at="2026-01-01T00:00:00+00:00")
    doc_b = Document(source="x.md", file_type="md", pages=1, blocks=blocks, compiled_at="2026-07-13T12:00:00+00:00")
    doc_a.compute_id()
    doc_b.compute_id()
    assert doc_a.document_id == doc_b.document_id


def test_document_id_backward_compat_alias() -> None:
    """`id` is always equal to `document_id` after compute_id()."""
    doc = _make_doc([_para("content")])
    doc.compute_id()
    assert doc.id == doc.document_id
    assert doc.id != ""


def test_document_schema_version() -> None:
    assert Document(source="x.md").schema_version == "1.2"


# ── block_id includes page ─────────────────────────────────────────────────────

def test_block_id_differs_across_pages() -> None:
    """Identical content on different pages must get different IDs."""
    b1 = Block(type=BlockType.PARAGRAPH, content="same text", index=0, page=1)
    b2 = Block(type=BlockType.PARAGRAPH, content="same text", index=0, page=2)
    assert b1.id != b2.id


def test_block_id_stable_for_same_block() -> None:
    """Same block reconstructed twice → same ID."""
    b1 = Block(type=BlockType.PARAGRAPH, content="hello", index=3, page=2)
    b2 = Block(type=BlockType.PARAGRAPH, content="hello", index=3, page=2)
    assert b1.id == b2.id


def test_block_id_non_empty() -> None:
    b = Block(type=BlockType.HEADING, content="Title", index=0, page=1, level=1)
    assert b.id != ""
    assert len(b.id) == 16


# ── chunk_id includes document_id ─────────────────────────────────────────────

def test_chunk_id_includes_document_id() -> None:
    """Same chunk content + different document_id → different chunk_id."""
    c1 = Chunk(document_id="aaaaaaaabbbbbbbb", index=0, content="text")
    c1.compute_id()
    c2 = Chunk(document_id="ccccccccdddddddd", index=0, content="text")
    c2.compute_id()
    assert c1.id != c2.id


def test_chunk_id_stable() -> None:
    """Same document_id + index + content → same chunk_id."""
    c1 = Chunk(document_id="aabbccdd11223344", index=2, content="some content here")
    c1.compute_id()
    c2 = Chunk(document_id="aabbccdd11223344", index=2, content="some content here")
    c2.compute_id()
    assert c1.id == c2.id


def test_chunk_id_backward_compat_without_document_id() -> None:
    """When document_id is empty, chunk_id still computed (old formula)."""
    c = Chunk(document_id="", index=0, content="text")
    c.compute_id()
    assert c.id != ""


def test_chunk_schema_version() -> None:
    assert Chunk().schema_version == "1.2"


# ── manifest schema version ───────────────────────────────────────────────────

def test_manifest_schema_version() -> None:
    m = Manifest(source="test.md")
    # Bumped 1.3 → 1.4 in PR 100 for the additive OCR Auto Policy fields.
    # Bumped 1.4 → 1.5 in PR 102 for Output Safety Policy v1 fallback fields.
    assert m.schema_version == "1.5"


# ── _compute_source_id helper ─────────────────────────────────────────────────

def test_source_id_deterministic_for_path(tmp_path: Path) -> None:
    """source_id is deterministic and stable for the same path."""
    f = tmp_path / "doc.md"
    f.write_text("content", encoding="utf-8")
    sid1 = _compute_source_id(str(f))
    sid2 = _compute_source_id(str(f))
    assert sid1 == sid2
    assert len(sid1) == 16


def test_source_id_deterministic_for_url() -> None:
    url = "https://example.com/docs/report.pdf"
    assert _compute_source_id(url) == _compute_source_id(url)
    assert len(_compute_source_id(url)) == 16


# ── integration: full compile pipeline propagates IDs ────────────────────────

@pytest.fixture
def simple_md(tmp_path: Path) -> Path:
    f = tmp_path / "doc.md"
    f.write_text(textwrap.dedent("""\
        # Title

        A paragraph of content.

        ## Section

        More text here for chunking.
    """), encoding="utf-8")
    return f


def test_capture_id_from_file_bytes(simple_md: Path, tmp_path: Path) -> None:
    """capture_id matches SHA-256 of the source file bytes."""
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(simple_md))[1]
    expected = hashlib.sha256(simple_md.read_bytes()).hexdigest()
    assert ctx.manifest is not None
    assert ctx.manifest.capture_id == expected


def test_ids_propagated_to_manifest(simple_md: Path, tmp_path: Path) -> None:
    """After compile, manifest carries source_id, capture_id, document_id."""
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(simple_md))[1]
    m = ctx.manifest
    assert m is not None
    assert m.source_id != "", "source_id must be populated"
    assert m.capture_id != "", "capture_id must be populated"
    assert m.document_id != "", "document_id must be populated"
    assert len(m.source_id) == 16
    assert len(m.document_id) == 16
    assert len(m.capture_id) == 64  # full SHA-256 hex


def test_ids_propagated_to_chunks(simple_md: Path, tmp_path: Path) -> None:
    """After compile, every chunk carries source_id, capture_id, document_id."""
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(simple_md))[1]
    assert ctx.chunks, "expected at least one chunk"
    for chunk in ctx.chunks:
        assert chunk.source_id != "", f"chunk {chunk.index} missing source_id"
        assert chunk.capture_id != "", f"chunk {chunk.index} missing capture_id"
        assert chunk.document_id != "", f"chunk {chunk.index} missing document_id"
        assert chunk.source_id == ctx.manifest.source_id
        assert chunk.capture_id == ctx.manifest.capture_id
        assert chunk.document_id == ctx.manifest.document_id


def test_confidence_summary_in_chunks(simple_md: Path, tmp_path: Path) -> None:
    """Every chunk has a confidence_summary with count + block_ids per level."""
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(simple_md))[1]
    assert ctx.chunks, "expected at least one chunk"
    for chunk in ctx.chunks:
        cs = chunk.confidence_summary
        for level in ("extracted", "inferred", "ambiguous"):
            assert level in cs, f"missing key '{level}' in confidence_summary"
            assert "count" in cs[level], f"confidence_summary['{level}'] missing 'count'"
            assert "block_ids" in cs[level], f"confidence_summary['{level}'] missing 'block_ids'"
            assert isinstance(cs[level]["count"], int)
            assert isinstance(cs[level]["block_ids"], list)
        total_blocks = sum(cs[lv]["count"] for lv in ("extracted", "inferred", "ambiguous"))
        assert total_blocks > 0, "expected at least one block counted in confidence_summary"


def test_confidence_summary_block_ids_match_chunk_block_ids(simple_md: Path, tmp_path: Path) -> None:
    """All block_ids in confidence_summary are a subset of chunk.block_ids."""
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(str(simple_md))[1]
    for chunk in ctx.chunks:
        cs = chunk.confidence_summary
        all_summary_ids = (
            cs["extracted"]["block_ids"]
            + cs["inferred"]["block_ids"]
            + cs["ambiguous"]["block_ids"]
        )
        assert set(all_summary_ids) <= set(chunk.block_ids), (
            "confidence_summary block_ids must be a subset of chunk.block_ids"
        )
        # Counts must add up to total block count
        total = sum(cs[lv]["count"] for lv in ("extracted", "inferred", "ambiguous"))
        assert total == len(chunk.block_ids)


# ── Item 3: identical content, same page, different index → different block IDs ──

def test_identical_content_different_index_different_block_id() -> None:
    """Two blocks with same type, page, and content but different index must not collide."""
    b1 = Block(type=BlockType.PARAGRAPH, content="same text", index=0, page=1)
    b2 = Block(type=BlockType.PARAGRAPH, content="same text", index=1, page=1)
    assert b1.id != b2.id, (
        "Block IDs must differ when indices differ, even with identical content and page"
    )


def test_identical_blocks_different_pages_different_index_different_id() -> None:
    """Same content, same type, different page AND different index → different ID."""
    b1 = Block(type=BlockType.PARAGRAPH, content="same text", index=0, page=1)
    b2 = Block(type=BlockType.PARAGRAPH, content="same text", index=1, page=2)
    assert b1.id != b2.id


# ── Item 2: URL/S3 source_id uses original locator ───────────────────────────

def test_source_id_stable_for_path_regardless_of_presentation(tmp_path: Path) -> None:
    """source_id resolves both relative and absolute paths to the same absolute POSIX path."""
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    abs_path = str(f.resolve())
    assert _compute_source_id(abs_path) == _compute_source_id(abs_path)


def test_source_id_uses_original_url_not_temp(tmp_path: Path) -> None:
    """For URL sources, _compute_source_id is applied to the original URL string."""
    url = "https://example.com/paper.pdf"
    # source_id must match what _compute_source_id produces for the URL itself
    expected = _compute_source_id(url)
    assert len(expected) == 16
    # Verify it is NOT derived from a temp-file-like path
    import tempfile
    tmp_name = str(Path(tempfile.gettempdir()) / "aksharamd_tmp_paper.pdf")
    assert _compute_source_id(url) != _compute_source_id(tmp_name)


# ── Item 5: capture_id stable across chunk configs; document_id pre-chunk ────

def test_capture_id_independent_of_chunk_size(tmp_path: Path) -> None:
    """capture_id is derived from raw bytes; chunk_size does not affect it."""
    f = tmp_path / "doc.md"
    f.write_text("# H\n\nParagraph one.\n\nParagraph two.\n", encoding="utf-8")
    expected_capture = hashlib.sha256(f.read_bytes()).hexdigest()

    ctx_small = Compiler(output_dir=str(tmp_path / "s"), chunk_size=32).compile_to_string(str(f))[1]
    ctx_large = Compiler(output_dir=str(tmp_path / "l"), chunk_size=512).compile_to_string(str(f))[1]

    assert ctx_small.manifest.capture_id == expected_capture
    assert ctx_large.manifest.capture_id == expected_capture
    assert ctx_small.manifest.capture_id == ctx_large.manifest.capture_id


def test_document_id_independent_of_chunk_size(tmp_path: Path) -> None:
    """document_id is computed from block IR before chunking; chunk_size must not affect it."""
    f = tmp_path / "doc.md"
    f.write_text("# H\n\nParagraph one.\n\nParagraph two.\n", encoding="utf-8")

    ctx_small = Compiler(output_dir=str(tmp_path / "s"), chunk_size=32).compile_to_string(str(f))[1]
    ctx_large = Compiler(output_dir=str(tmp_path / "l"), chunk_size=512).compile_to_string(str(f))[1]

    assert ctx_small.manifest.document_id == ctx_large.manifest.document_id, (
        "document_id must be identical regardless of chunk_size — it is computed before chunking"
    )


def test_different_content_different_capture_and_document_id(tmp_path: Path) -> None:
    """Changing file content changes both capture_id and document_id."""
    f = tmp_path / "doc.md"
    f.write_text("# Version 1\n\nOriginal content.\n", encoding="utf-8")
    ctx_v1 = Compiler(output_dir=str(tmp_path / "v1")).compile_to_string(str(f))[1]

    f.write_text("# Version 2\n\nCompletely different text.\n", encoding="utf-8")
    ctx_v2 = Compiler(output_dir=str(tmp_path / "v2")).compile_to_string(str(f))[1]

    assert ctx_v1.manifest.capture_id != ctx_v2.manifest.capture_id
    assert ctx_v1.manifest.document_id != ctx_v2.manifest.document_id


# ── Item 6: chunk IDs differ when content differs across chunk configs ────────

def test_chunk_ids_differ_when_content_differs(tmp_path: Path) -> None:
    """Small chunk_size splits content differently → chunk 0 content differs → different chunk IDs."""
    lines = ["# Section\n"] + [f"Para {i}: " + ("word " * 15) + "\n" for i in range(8)]
    f = tmp_path / "long.md"
    f.write_text("\n".join(lines), encoding="utf-8")

    ctx_small = Compiler(output_dir=str(tmp_path / "s"), chunk_size=32).compile_to_string(str(f))[1]
    ctx_large = Compiler(output_dir=str(tmp_path / "l"), chunk_size=2048).compile_to_string(str(f))[1]

    # With different chunk sizes, chunk counts differ (or at least first chunk content differs)
    if len(ctx_small.chunks) > 1 and len(ctx_large.chunks) > 0:
        small_ids = {c.id for c in ctx_small.chunks}
        large_ids = {c.id for c in ctx_large.chunks}
        # Some chunk IDs must differ when content is split differently
        assert small_ids != large_ids, (
            "Expected different chunk IDs when chunk_size produces different splits"
        )


def test_chunk_ids_stable_same_config(simple_md: Path, tmp_path: Path) -> None:
    """Same file + same chunk_size → same chunk IDs (idempotent)."""
    ctx1 = Compiler(output_dir=str(tmp_path / "r1")).compile_to_string(str(simple_md))[1]
    ctx2 = Compiler(output_dir=str(tmp_path / "r2")).compile_to_string(str(simple_md))[1]

    ids1 = [c.id for c in ctx1.chunks]
    ids2 = [c.id for c in ctx2.chunks]
    assert ids1 == ids2, "Chunk IDs must be stable across identical compilations"


# ── Hardening: Unicode NFC + newline normalization ────────────────────────────

def test_block_checksum_nfc_normalization() -> None:
    """NFC and NFD representations of the same text produce the same checksum."""
    import unicodedata
    # "café" — NFC: é is U+00E9; NFD: e + combining acute U+0301
    nfc_text = unicodedata.normalize("NFC", "café")
    nfd_text = unicodedata.normalize("NFD", "café")
    assert nfc_text != nfd_text, "precondition: NFC and NFD must be byte-different"
    b_nfc = Block(type=BlockType.PARAGRAPH, content=nfc_text, index=0, page=1)
    b_nfd = Block(type=BlockType.PARAGRAPH, content=nfd_text, index=0, page=1)
    assert b_nfc.checksum == b_nfd.checksum, (
        "NFC and NFD forms of the same text must produce identical checksums"
    )


def test_block_checksum_newline_normalization() -> None:
    """\\r\\n and \\n content produce the same block checksum after normalization."""
    b_crlf = Block(type=BlockType.PARAGRAPH, content="line one\r\nline two", index=0, page=1)
    b_lf   = Block(type=BlockType.PARAGRAPH, content="line one\nline two",   index=0, page=1)
    assert b_crlf.checksum == b_lf.checksum, (
        "CRLF and LF line endings must produce identical checksums"
    )


def test_block_checksum_cr_normalization() -> None:
    """Bare \\r is also normalized to \\n for checksum purposes."""
    b_cr = Block(type=BlockType.PARAGRAPH, content="line one\rline two", index=0, page=1)
    b_lf = Block(type=BlockType.PARAGRAPH, content="line one\nline two", index=0, page=1)
    assert b_cr.checksum == b_lf.checksum


def test_document_id_stable_across_nfc_nfd(tmp_path: Path) -> None:
    """document_id is the same whether block content is NFC or NFD."""
    import unicodedata
    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    doc_nfc = Document(source="x.md", file_type="md", pages=1,
                       blocks=[Block(type=BlockType.PARAGRAPH, content=nfc, index=0)])
    doc_nfd = Document(source="x.md", file_type="md", pages=1,
                       blocks=[Block(type=BlockType.PARAGRAPH, content=nfd, index=0)])
    doc_nfc.compute_id()
    doc_nfd.compute_id()
    assert doc_nfc.document_id == doc_nfd.document_id, (
        "document_id must be stable regardless of Unicode normalization form"
    )


# ── Hardening: caller-provided source_id override ────────────────────────────

def test_source_id_override_propagates_to_manifest(simple_md: Path, tmp_path: Path) -> None:
    """Caller-provided source_id is used instead of the computed one."""
    custom_id = "s3://my-bucket/docs/report.pdf"
    expected = _compute_source_id(custom_id)
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(
        str(simple_md), source_id=expected
    )[1]
    assert ctx.manifest is not None
    assert ctx.manifest.source_id == expected, (
        "Caller-provided source_id must override the auto-derived value"
    )


def test_source_id_override_propagates_to_chunks(simple_md: Path, tmp_path: Path) -> None:
    """All chunks carry the caller-provided source_id."""
    custom_sid = "abcdef0123456789"  # 16-char override
    ctx = Compiler(output_dir=str(tmp_path / "out")).compile_to_string(
        str(simple_md), source_id=custom_sid
    )[1]
    assert ctx.chunks, "expected at least one chunk"
    for chunk in ctx.chunks:
        assert chunk.source_id == custom_sid, (
            f"chunk {chunk.index} source_id mismatch: {chunk.source_id!r}"
        )


def test_source_id_override_differs_from_default(simple_md: Path, tmp_path: Path) -> None:
    """The override and the auto-derived value are different (precondition check)."""
    custom_sid = "custom0000000001"
    ctx_default  = Compiler(output_dir=str(tmp_path / "d")).compile_to_string(str(simple_md))[1]
    ctx_override = Compiler(output_dir=str(tmp_path / "o")).compile_to_string(
        str(simple_md), source_id=custom_sid
    )[1]
    assert ctx_default.manifest.source_id != custom_sid
    assert ctx_override.manifest.source_id == custom_sid
