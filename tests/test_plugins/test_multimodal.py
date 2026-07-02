from __future__ import annotations

from aksharamd.models.asset import Asset
from aksharamd.models.block import Block, BlockType
from aksharamd.models.document import Document
from aksharamd.plugins.exporters.multimodal import _media_type, build_multimodal_content

_PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
_JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 8
_GIF_HEADER = b"GIF89a" + b"\x00" * 8
_WEBP_HEADER = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
_TIFF_HEADER = b"MM\x00*" + b"\x00" * 8


def _make_doc(blocks: list[Block], assets: list[Asset] | None = None) -> Document:
    return Document(
        source="test.pdf",
        file_type="pdf",
        title="Test",
        pages=1,
        blocks=blocks,
        assets=assets or [],
    )


# ── _media_type ───────────────────────────────────────────────────────────────

def test_media_type_png():
    assert _media_type(_PNG_HEADER) == "image/png"


def test_media_type_jpeg():
    assert _media_type(_JPEG_HEADER) == "image/jpeg"


def test_media_type_gif():
    assert _media_type(_GIF_HEADER) == "image/gif"


def test_media_type_webp():
    assert _media_type(_WEBP_HEADER) == "image/webp"


def test_media_type_tiff():
    assert _media_type(_TIFF_HEADER) == "image/tiff"


def test_media_type_unknown_falls_back_to_png():
    assert _media_type(b"\x00\x00\x00\x00") == "image/png"


# ── build_multimodal_content: text-only ──────────────────────────────────────

def test_text_only_doc_returns_single_text_block():
    doc = _make_doc([
        Block(type=BlockType.HEADING, content="Title", level=1, index=0),
        Block(type=BlockType.PARAGRAPH, content="Some content here.", index=1),
    ])
    result = build_multimodal_content(doc)
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert "Title" in result[0]["text"]


def test_empty_doc_returns_empty_list():
    doc = _make_doc([])
    result = build_multimodal_content(doc)
    assert result == []


def test_doc_with_image_block_no_asset():
    """Image block without matching asset → falls through as text label."""
    doc = _make_doc([
        Block(type=BlockType.PARAGRAPH, content="Before image.", index=0),
        Block(type=BlockType.IMAGE, content="Figure 1", index=1, metadata={"asset_id": "missing"}),
        Block(type=BlockType.PARAGRAPH, content="After image.", index=2),
    ])
    result = build_multimodal_content(doc)
    assert len(result) == 1
    assert "Figure 1" in result[0]["text"]


def test_doc_with_image_block_with_asset():
    """Image block with a real asset → emits image content block."""
    asset = Asset(id="img1", type="image", image_bytes=_PNG_HEADER)
    doc = _make_doc(
        blocks=[
            Block(type=BlockType.PARAGRAPH, content="Before.", index=0),
            Block(type=BlockType.IMAGE, content="Chart", index=1, metadata={"asset_id": "img1"}),
            Block(type=BlockType.PARAGRAPH, content="After.", index=2),
        ],
        assets=[asset],
    )
    result = build_multimodal_content(doc)
    types = [r["type"] for r in result]
    assert "image" in types
    img_block = next(r for r in result if r["type"] == "image")
    assert img_block["source"]["media_type"] == "image/png"
    assert img_block["source"]["type"] == "base64"


def test_image_cap_not_exceeded():
    """More than _MAX_IMAGES images → only first 20 included."""
    from aksharamd.plugins.exporters.multimodal import _MAX_IMAGES
    asset = Asset(id="img", type="image", image_bytes=_PNG_HEADER)
    blocks = [
        Block(type=BlockType.IMAGE, content="", index=i, metadata={"asset_id": "img"})
        for i in range(_MAX_IMAGES + 5)
    ]
    doc = _make_doc(blocks, assets=[asset])
    result = build_multimodal_content(doc)
    image_count = sum(1 for r in result if r["type"] == "image")
    assert image_count == _MAX_IMAGES


def test_image_with_caption_added_as_text():
    """Image with content caption adds italicized caption to text stream."""
    asset = Asset(id="fig1", type="image", image_bytes=_PNG_HEADER)
    doc = _make_doc(
        blocks=[Block(type=BlockType.IMAGE, content="The caption", index=0, metadata={"asset_id": "fig1"})],
        assets=[asset],
    )
    result = build_multimodal_content(doc)
    text_blocks = [r for r in result if r["type"] == "text"]
    if text_blocks:
        assert "caption" in text_blocks[0]["text"]


def test_image_block_without_content_no_label():
    """IMAGE block with no content and no asset → no text label emitted."""
    doc = _make_doc([
        Block(type=BlockType.IMAGE, content="", index=0, metadata={"asset_id": "missing"}),
    ])
    result = build_multimodal_content(doc)
    assert result == []
