"""Provider-neutral adapters for LLMPayload."""
from __future__ import annotations

from pathlib import Path

from .payload import LLMPayload, PayloadContentType


def to_plain_text(payload: LLMPayload) -> str:
    """Concatenate all payload items into a single Markdown-like string."""
    parts: list[str] = []
    for item in payload.items:
        ct = item.content_type
        if ct == PayloadContentType.TEXT:
            if item.text:
                parts.append(item.text)
        elif ct == PayloadContentType.STRUCTURED_TABLE:
            if item.context_text:
                parts.append(item.context_text)
            if item.table_markdown:
                parts.append(item.table_markdown)
        elif ct == PayloadContentType.IMAGE_REFERENCE:
            cap = item.caption or ""
            if item.asset_path:
                parts.append(f"[Image at {item.asset_path}: {cap}]")
            else:
                parts.append(f"[Image: {cap or 'no caption'}]")
        elif ct == PayloadContentType.WARNING:
            if item.text:
                parts.append(item.text)
    return "\n\n".join(p for p in parts if p)


def to_multimodal_content(
    payload: LLMPayload,
    package_dir: Path | str,
) -> list[dict]:
    """Provider-neutral multimodal content array.

    Each item is a dict with a "type" key:
      {"type": "text", "text": "..."}
      {"type": "image", "path": "...", "mime_type": "...", "caption": "..."}
      {"type": "table", "markdown": "...", "artifact_path": "..."}
      {"type": "warning", "text": "...", "warning_codes": [...]}

    Does not load image bytes. Does not make network calls.

    `package_dir` is accepted for API symmetry with future asset-URL
    resolvers; the current implementation only reads from `payload`.
    """
    del package_dir  # accepted for API symmetry; unused in current impl
    result: list[dict] = []

    for item in payload.items:
        ct = item.content_type
        if ct == PayloadContentType.TEXT:
            if item.text:
                result.append({"type": "text", "text": item.text})
        elif ct == PayloadContentType.STRUCTURED_TABLE:
            entry: dict = {"type": "table", "markdown": item.table_markdown or ""}
            if item.table_artifact_path:
                entry["artifact_path"] = item.table_artifact_path
            result.append(entry)
        elif ct == PayloadContentType.IMAGE_REFERENCE:
            entry = {
                "type": "image",
                "path": item.asset_path or "",
                "mime_type": item.mime_type or "",
                "caption": item.caption or "",
            }
            result.append(entry)
        elif ct == PayloadContentType.WARNING:
            result.append({
                "type": "warning",
                "text": item.text or "",
                "warning_codes": list(item.warning_codes),
            })

    return result
