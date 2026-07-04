from __future__ import annotations

import base64

from ...models.asset import Asset
from ...models.block import Block, BlockType
from ...models.document import Document

_MAX_IMAGES = 20  # hard cap per document to prevent runaway token usage


def _media_type(data: bytes) -> str:
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"MM\x00*", b"II*\x00"):
        return "image/tiff"
    return "image/png"


def _block_to_md(block: Block) -> str:
    from .markdown import _block_to_md as _md
    return _md(block)


def build_multimodal_content(doc: Document) -> list[dict]:
    """
    Walk doc.blocks in order, building an Anthropic-compatible content array
    with text and image blocks interleaved at their document positions.

    Returns a list of dicts:
      {"type": "text",  "text": "..."}
      {"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}
    """
    asset_map: dict[str, Asset] = {a.id: a for a in doc.assets}
    content: list[dict] = []
    text_parts: list[str] = []
    images_included = 0

    def flush_text() -> None:
        joined = "\n\n".join(p for p in text_parts if p.strip())
        if joined.strip():
            content.append({"type": "text", "text": joined})
        text_parts.clear()

    for block in doc.blocks:
        if block.type == BlockType.IMAGE:
            asset_id = block.metadata.get("asset_id")
            if not asset_id or not isinstance(asset_id, str):
                label = block.content or block.metadata.get("src", "")
                if label:
                    text_parts.append(f"[Image: {label}]")
                continue
            asset = asset_map.get(asset_id)

            if asset and asset.image_bytes and images_included < _MAX_IMAGES:
                flush_text()
                mt = _media_type(asset.image_bytes)
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mt,
                        "data": base64.standard_b64encode(asset.image_bytes).decode(),
                    },
                })
                images_included += 1
                if block.content:
                    text_parts.append(f"*{block.content}*")
            else:
                label = block.content or block.metadata.get("src", "")
                if label:
                    text_parts.append(f"[Image: {label}]")
        else:
            md = _block_to_md(block)
            if md:
                text_parts.append(md)

    flush_text()
    return content
