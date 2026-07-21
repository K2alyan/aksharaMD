"""OCR backend package.

PR 94a introduces the ``OcrBackend`` protocol and the backend
registry. Only the Tesseract wrapper is registered here; the
Unlimited-OCR backend lands in PR 94b, and CLI opt-in wiring lands
in PR 94c. See ``docs/adr/ocr_backend_execution_plan.md`` for the
full plan.

Importing this package must remain cheap: no torch, no pytesseract,
no PIL — heavy dependencies are pulled in lazily inside individual
backend methods.
"""
from __future__ import annotations

from pathlib import Path

from ._protocol import (
    BACKEND_AVAILABILITY_DETAIL_KEYS,
    BackendAvailability,
    BackendAvailabilityDetails,
    BackendCapabilities,
    OcrBackend,
    OcrFailure,
    OcrPageRequest,
    OcrPageResult,
)
from ._registry import available_backends, get_backend

_PKG_DIR = Path(__file__).resolve().parent

UNLIMITED_OCR_TRUSTED_MANIFEST_PATH = _PKG_DIR / "unlimited_ocr_trusted_manifest.json"
UNLIMITED_OCR_ACQUISITION_INVENTORY_PATH = _PKG_DIR / "unlimited_ocr_acquisition_inventory.json"

__all__ = [
    "BACKEND_AVAILABILITY_DETAIL_KEYS",
    "BackendAvailability",
    "BackendAvailabilityDetails",
    "BackendCapabilities",
    "OcrBackend",
    "OcrFailure",
    "OcrPageRequest",
    "OcrPageResult",
    "UNLIMITED_OCR_ACQUISITION_INVENTORY_PATH",
    "UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
    "available_backends",
    "get_backend",
]
