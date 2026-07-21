"""Tesseract OCR backend.

Adapter around the existing per-page Tesseract path in
``aksharamd.plugins.parsers.image._try_ocr_structured``. This backend
does not change Tesseract behavior — it wraps it in the ``OcrBackend``
Protocol so ``pdf.py`` can dispatch to backends generically.

Heavy imports (``pytesseract``, ``PIL``, ``fitz``) are lazy so
``import aksharamd`` remains cheap.
"""
from __future__ import annotations

import logging
from typing import ClassVar

from ._protocol import (
    BackendAvailability,
    BackendCapabilities,
    OcrBackend,
    OcrFailure,
    OcrPageRequest,
    OcrPageResult,
)

logger = logging.getLogger(__name__)


class TesseractBackend(OcrBackend):
    """Existing Tesseract path exposed as an ``OcrBackend``."""

    name: ClassVar[str] = "tesseract"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_layout=False,
            supports_math=False,
            supports_tables=False,
            emits="blocks",
        )

    def availability(self) -> BackendAvailability:
        try:
            import pytesseract  # type: ignore
        except ImportError as exc:
            return BackendAvailability(
                is_available=False,
                reason=f"pytesseract not importable: {exc}",
            )
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:  # pragma: no cover - pytesseract-specific error
            return BackendAvailability(
                is_available=False,
                reason=f"tesseract binary not found: {exc}",
            )
        return BackendAvailability(is_available=True)

    def process(self, request: OcrPageRequest) -> list[OcrPageResult]:
        results: list[OcrPageResult] = []
        try:
            import io

            import fitz  # type: ignore
            from PIL import Image  # type: ignore

            from ..parsers.image import _try_ocr_structured
        except ImportError as exc:
            failure = OcrFailure(kind="backend_unavailable", message=str(exc))
            return [
                OcrPageResult(page_index=idx, is_ok=False, failure=failure)
                for idx in request.page_indices
            ]

        pdf = None
        try:
            pdf = fitz.open(str(request.pdf_path))
            for page_idx in request.page_indices:
                try:
                    page = pdf[page_idx]
                    pix = page.get_pixmap(dpi=request.dpi)
                    png_bytes = pix.tobytes("png")
                    pil_img = Image.open(io.BytesIO(png_bytes))
                    ocr_tuples = _try_ocr_structured(pil_img)
                except Exception as exc:
                    logger.debug(
                        "Tesseract OCR failed on page %d", page_idx, exc_info=True
                    )
                    results.append(
                        OcrPageResult(
                            page_index=page_idx,
                            is_ok=False,
                            failure=OcrFailure(kind="other", message=str(exc)),
                        )
                    )
                    continue
                # Return raw tuples from _try_ocr_structured; the pdf.py
                # merge step converts them into Blocks using the same
                # BlockType/level convention as the current inline path.
                results.append(
                    OcrPageResult(
                        page_index=page_idx,
                        blocks=list(ocr_tuples),
                        is_ok=True,
                    )
                )
        finally:
            if pdf is not None:
                pdf.close()

        return results
