from __future__ import annotations
import re
import unicodedata
from collections import Counter

from ..base import CleanerPlugin
from ..registry import register_plugin
from ...context import CompilationContext
from ...models.block import BlockType


_PAGE_NUMBER_RE = re.compile(r"^\d+$|^page\s+\d+(\s+of\s+\d+)?$", re.IGNORECASE)
_ZERO_WIDTH = re.compile(r"[​‌‍﻿­]")


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_page_number(text: str) -> bool:
    return bool(_PAGE_NUMBER_RE.match(text.strip()))


class DefaultCleaner(CleanerPlugin):
    name = "default_cleaner"
    priority = 10

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        blocks = ctx.document.blocks
        cleaned = []

        for block in blocks:
            # Drop lone page numbers
            if block.type == BlockType.PARAGRAPH and _is_page_number(block.content):
                continue
            # Normalize all text content
            block = block.model_copy(update={"content": _normalize_text(block.content)})
            if block.content:
                cleaned.append(block)

        ctx.document = ctx.document.model_copy(update={"blocks": cleaned})
        return ctx


register_plugin(DefaultCleaner)
