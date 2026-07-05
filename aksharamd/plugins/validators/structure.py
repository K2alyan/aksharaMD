from __future__ import annotations

import re
from collections import Counter

from ...context import CompilationContext
from ...models.block import BlockType
from ..base import ValidatorPlugin
from ..registry import register_plugin

_CID_RE = re.compile(r"\(cid:\d+\)")

# Thresholds for new quality signals
_MIN_CHARS_PER_PAGE = 80          # avg chars/page below this → LOW_TEXT_DENSITY
_MAX_CID_RATIO = 0.02             # >2% of chars are CID glyphs → GLYPH_ARTIFACTS
_MIN_CID_COUNT = 10               # don't warn on a handful of isolated glyphs
_REPEATED_LINE_MIN_LEN = 20       # ignore short lines when counting repeats
_REPEATED_LINE_THRESHOLD = 5      # line appearing this many times → noise
_REPEATED_LINE_MIN_UNIQUE = 3     # at least this many distinct repeated lines → warn
_MAX_TOKENS_PER_PAGE = 2500       # PDF pages only; above this → TOKEN_BLOAT
_TOKEN_BLOAT_MIN_PAGES = 3        # don't fire on very short docs
_NEAR_EMPTY_CHARS_PER_PAGE = 80   # total output chars/page below this → NEAR_EMPTY_OUTPUT


class StructureValidator(ValidatorPlugin):
    name = "structure_validator"
    priority = 30

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            ctx.error("NO_DOCUMENT", "No document was produced by the parser")
            return ctx

        doc = ctx.document
        blocks = doc.blocks

        if not blocks:
            ctx.warn("EMPTY_DOCUMENT", "Document contains no blocks")
            return ctx

        # ── Heading hierarchy ──────────────────────────────────────────────────
        headings = [b for b in blocks if b.type == BlockType.HEADING]
        if headings:
            levels = [h.level for h in headings if h.level is not None]
            if levels and levels[0] > 2:
                ctx.warn("HEADING_HIERARCHY", f"Document starts at heading level {levels[0]}, expected 1 or 2")

            prev_level = 0
            for h in headings:
                if h.level and h.level > prev_level + 1 and prev_level > 0:
                    ctx.warn(
                        "HEADING_SKIP",
                        f"Heading level jumped from {prev_level} to {h.level}: '{h.content[:60]}'",
                        page=h.page,
                        block_id=h.id,
                    )
                if h.level:
                    prev_level = h.level

        # ── Very large blocks (likely a merge/parse failure) ───────────────────
        for block in blocks:
            if len(block.content) > 10_000:
                ctx.warn(
                    "LARGE_BLOCK",
                    f"Block {block.id} is unusually large ({len(block.content)} chars)",
                    page=block.page,
                    block_id=block.id,
                )

        # ── Empty content blocks ───────────────────────────────────────────────
        for block in blocks:
            if not block.content.strip():
                ctx.warn("EMPTY_BLOCK", f"Block {block.id} has empty content", block_id=block.id)

        # ── PDF: missing pages ─────────────────────────────────────────────────
        if doc.file_type == "pdf" and doc.pages > 0:
            pages_with_content = {b.page for b in blocks if b.page is not None}
            for p in range(1, doc.pages + 1):
                if p not in pages_with_content:
                    ctx.warn("MISSING_PAGE", f"Page {p} has no extracted content", page=p)

        # ══ New quality-signal checks ══════════════════════════════════════════

        total_content_chars = sum(len(b.content.strip()) for b in blocks)

        # ── Near-empty output despite having pages ─────────────────────────────
        if doc.pages > 0 and total_content_chars < doc.pages * _NEAR_EMPTY_CHARS_PER_PAGE:
            avg = total_content_chars // max(doc.pages, 1)
            ctx.warn(
                "NEAR_EMPTY_OUTPUT",
                f"Only {total_content_chars:,} chars extracted across {doc.pages} pages "
                f"({avg} avg chars/page) — document may be scanned, image-only, "
                "or have a font encoding problem.",
            )

        # ── Low text density (PDF-specific) ───────────────────────────────────
        if doc.file_type == "pdf" and doc.pages > 0:
            text_chars = sum(
                len(b.content.strip())
                for b in blocks
                if b.type in (BlockType.PARAGRAPH, BlockType.HEADING)
            )
            avg_text_per_page = text_chars / doc.pages
            if avg_text_per_page < _MIN_CHARS_PER_PAGE:
                ctx.warn(
                    "LOW_TEXT_DENSITY",
                    f"Average {avg_text_per_page:.0f} text chars/page — "
                    "PDF may be primarily image-based or have a font encoding problem. "
                    "Consider enabling OCR (pip install aksharamd[ocr]).",
                )

        # ── CID glyph artifacts ────────────────────────────────────────────────
        all_text = " ".join(b.content for b in blocks)
        cid_count = len(_CID_RE.findall(all_text))
        if cid_count >= _MIN_CID_COUNT:
            cid_ratio = cid_count / max(len(all_text), 1)
            if cid_ratio > _MAX_CID_RATIO:
                ctx.warn(
                    "GLYPH_ARTIFACTS",
                    f"{cid_count} CID glyph artifacts detected ({cid_ratio:.1%} of text) — "
                    "PDF uses a non-embedded or obfuscated font; extracted text is likely garbled. "
                    "OCR on a rasterized version may produce better results.",
                )

        # ── Repeated content (incomplete boilerplate removal) ─────────────────
        line_counts: Counter[str] = Counter()
        for b in blocks:
            if b.type in (BlockType.PARAGRAPH, BlockType.HEADING):
                for line in b.content.splitlines():
                    stripped = line.strip()
                    if len(stripped) >= _REPEATED_LINE_MIN_LEN:
                        line_counts[stripped] += 1

        repeated = {
            line: count
            for line, count in line_counts.items()
            if count >= _REPEATED_LINE_THRESHOLD
        }
        if len(repeated) >= _REPEATED_LINE_MIN_UNIQUE:
            ctx.warn(
                "REPEATED_CONTENT",
                f"{len(repeated)} content lines each appear ≥{_REPEATED_LINE_THRESHOLD}× — "
                "header/footer removal may be incomplete or the document has excessive boilerplate.",
            )

        # ── Token bloat (PDF-specific) ─────────────────────────────────────────
        if (
            doc.file_type == "pdf"
            and doc.pages >= _TOKEN_BLOAT_MIN_PAGES
            and ctx.original_tokens > 0
        ):
            tokens_per_page = ctx.original_tokens / doc.pages
            if tokens_per_page > _MAX_TOKENS_PER_PAGE:
                ctx.warn(
                    "TOKEN_BLOAT",
                    f"{tokens_per_page:,.0f} tokens/page (total {ctx.original_tokens:,}) — "
                    "extraction may have duplicated content or failed to remove boilerplate.",
                )

        # ── OCR required but unavailable ───────────────────────────────────────
        if doc.file_type == "pdf":
            classification = doc.metadata.get("pdf_classification", "")
            ocr_available = doc.metadata.get("pdf_ocr_available", True)
            image_pages = doc.metadata.get("pdf_stats", {}).get("image_pages", 0)

            if classification in ("scanned", "hybrid") and not ocr_available and image_pages > 0:
                ctx.warn(
                    "OCR_REQUIRED",
                    f"PDF classified as '{classification}': {image_pages} image-only page(s) "
                    "could not be extracted because OCR (pytesseract) is not installed. "
                    "Install it to recover this content: pip install aksharamd[ocr]",
                )

        return ctx


register_plugin(StructureValidator)
