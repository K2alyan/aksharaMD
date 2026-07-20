"""OCR backend package.

Currently holds only the Unlimited-OCR trust artifacts (runtime manifest,
acquisition inventory). The full ``OcrBackend`` protocol + backend
implementations land in Phase B of the OCR rollout — see
``docs/adr/ocr_backend_execution_plan.md``.
"""
from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

UNLIMITED_OCR_TRUSTED_MANIFEST_PATH = _PKG_DIR / "unlimited_ocr_trusted_manifest.json"
UNLIMITED_OCR_ACQUISITION_INVENTORY_PATH = _PKG_DIR / "unlimited_ocr_acquisition_inventory.json"
