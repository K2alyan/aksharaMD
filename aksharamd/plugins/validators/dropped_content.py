"""Dropped-content detector — W_DROPPED_CONTENT (Phase 3).

The **crown jewel** of the substance-detector portfolio. Compares the
PDF text layer against the parsed markdown output to detect silent
content loss — regions of the source PDF the parser failed to extract.

Positioning: no other OSS extraction-QA tool catches dropped content
by cross-referencing source against parsed output. Existing OSS tools
either (a) ship confidence signals locked to their own parser (Reducto,
pdfmux) or (b) score retrieval / generation but not the extraction
layer (RAGAS, TruLens, DeepEval). AksharaMD's advantage is that this
detector fires on ANY parser's output — the score travels with the
document across parser changes.

Uses ``ctx.raw_bytes()`` (P0.1 foundation) to access the raw PDF file
and PyMuPDF (existing runtime dep) for text-layer extraction.

**Framing as a LOWER BOUND** — per Rojas et al. arXiv 2605.07293,
pattern-based detection reports at-least counts, not ceiling counts.
This detector reports at-least-N missing words; the true loss may be
higher (e.g., words in the markdown but placed on the wrong page).

Signal — Word-count deficit:
    metric:     markdown_word_count / max(1, pdf_word_count)
    fires when: ratio < 0.5 (parser retained less than half the source text)
    scale guard: pdf_word_count must be >= 100 to avoid tiny-doc noise
                 (short PDFs have high signal-to-noise ratios that fool
                  the coarse ratio comparison)

Skip guards:
    * File type: pdf only (need PDF geometry access)
    * ``ctx.raw_bytes()`` must return the source bytes (URL/S3 sources
      that did not land in a local temp are skipped silently)
    * Adapter-provided documents (file_type=='md' by then) are already
      excluded by the pdf guard; the parser_provided_via_adapter flag
      is checked defensively for callers who rewrite file_type
    * PDF must have a text layer — scanned PDFs would score 0/0 and
      trip false positives (OCR failures surface via other detectors)
    * PDF word count must be >= 100 (scale guard)

Follow-up detectors on the same infrastructure:
    * W_PAGE_MISMATCH — per-page word-count anomaly rather than
      whole-doc ratio
    * W_TABLE_GEOMETRY_LOST — PyMuPDF find_tables() vs markdown
      table blocks
Both are separate PRs.
"""
from __future__ import annotations

import logging
import re

from ...context import CompilationContext
from ...models.block import BlockType
from ...scoring.detector_budget import DetectorBudget
from ..base import ValidatorPlugin
from ..registry import register_plugin

logger = logging.getLogger(__name__)

# Signal threshold: markdown / pdf word-count ratio at which the detector fires.
_DEFICIT_RATIO_THRESHOLD: float = 0.5

# Scale guard: PDF must have at least this many words before we score it.
_MIN_PDF_WORDS: int = 100

# Per-detector wall-clock budget. PyMuPDF text extraction on a 100-page PDF
# is typically < 1s; 3000ms is generous.
_BUDGET_MS: int = 3000

# Word tokenizer — whitespace-split.
_WORD_RE = re.compile(r"\S+")


def _extract_pdf_word_count(pdf_bytes: bytes) -> tuple[int, int, bool]:
    """Return (total_word_count, page_count, has_text_layer).

    Never raises — a malformed or zero-byte PDF yields (0, 0, False)
    so callers can safely skip.
    """
    if not pdf_bytes:
        return 0, 0, False
    try:
        import fitz  # PyMuPDF — existing runtime dep
    except ImportError:
        logger.debug("PyMuPDF (fitz) not importable; W_DROPPED_CONTENT skipping")
        return 0, 0, False
    total_words = 0
    page_count = 0
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            page_count = len(pdf)
            for page in pdf:
                text = page.get_text("text") or ""
                total_words += len(_WORD_RE.findall(text))
    except Exception as exc:  # noqa: BLE001 — any PyMuPDF failure = skip
        logger.debug("PyMuPDF failed to open PDF for W_DROPPED_CONTENT: %s", exc)
        return 0, 0, False
    has_text_layer = total_words > 0
    return total_words, page_count, has_text_layer


def _markdown_word_count(blocks: list) -> int:
    """Count whitespace-separated tokens in text-bearing blocks."""
    total = 0
    for b in blocks:
        if b.type in (BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST):
            total += len(_WORD_RE.findall(b.content or ""))
    return total


class DroppedContentValidator(ValidatorPlugin):
    """W_DROPPED_CONTENT — cross-reference detector for silent content loss."""

    name = "dropped_content_validator"
    # Runs after gibberish (40). Whole-document signal that reads the raw
    # source file — expensive relative to text-only detectors, so it runs
    # near the end of the validator chain.
    priority = 45

    warning_maturity = "experimental"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document

        # Guard 1: file type must be pdf.
        if doc.file_type != "pdf":
            return ctx

        # Guard 2: defensive — parser adapter would already have flipped
        # file_type to md, but explicit check for readers.
        if getattr(ctx, "parser_provided_via_adapter", False):
            return ctx

        with DetectorBudget(ctx, "W_DROPPED_CONTENT", budget_ms=_BUDGET_MS):
            pdf_bytes = ctx.raw_bytes()
            if pdf_bytes is None:
                doc.metadata["dropped_content_diagnostics"] = {
                    "warned": False,
                    "suppressed_reason": "ctx.raw_bytes() returned None",
                    "warning_maturity": self.warning_maturity,
                }
                return ctx

            pdf_word_count, pdf_page_count, has_text_layer = _extract_pdf_word_count(pdf_bytes)

            markdown_word_count = _markdown_word_count(doc.blocks)
            ratio = markdown_word_count / pdf_word_count if pdf_word_count > 0 else 1.0

            diagnostics: dict = {
                "pdf_word_count": pdf_word_count,
                "pdf_page_count": pdf_page_count,
                "markdown_word_count": markdown_word_count,
                "ratio": ratio,
                "threshold": _DEFICIT_RATIO_THRESHOLD,
                "has_text_layer": has_text_layer,
                "warned": False,
                "warning_maturity": self.warning_maturity,
            }

            # Guard 3: PDF must have a text layer.
            if not has_text_layer:
                diagnostics["suppressed_reason"] = (
                    "PDF has no text layer (scanned or malformed) — no comparison possible"
                )
                doc.metadata["dropped_content_diagnostics"] = diagnostics
                return ctx

            # Guard 4: scale guard on PDF word count.
            if pdf_word_count < _MIN_PDF_WORDS:
                diagnostics["suppressed_reason"] = (
                    f"pdf_word_count {pdf_word_count} < {_MIN_PDF_WORDS} scale guard"
                )
                doc.metadata["dropped_content_diagnostics"] = diagnostics
                return ctx

            if ratio >= _DEFICIT_RATIO_THRESHOLD:
                doc.metadata["dropped_content_diagnostics"] = diagnostics
                return ctx

            # Fire.
            missing_words = max(0, pdf_word_count - markdown_word_count)
            diagnostics["warned"] = True
            diagnostics["missing_words"] = missing_words
            doc.metadata["dropped_content_diagnostics"] = diagnostics

            ctx.warn(
                "W_DROPPED_CONTENT",
                (
                    f"Dropped content detected: parsed markdown retains "
                    f"{markdown_word_count}/{pdf_word_count} words from the source "
                    f"PDF ({ratio:.1%}), below the {_DEFICIT_RATIO_THRESHOLD:.0%} "
                    f"threshold. At least {missing_words} words appear to have "
                    f"been silently dropped by the parser (LOWER BOUND — the true "
                    f"loss may be higher if the markdown includes duplicated or "
                    f"synthesized text)."
                ),
            )

        return ctx


register_plugin(DroppedContentValidator)
