"""Backwards-compat shim for the Unlimited-OCR safe-size cache.

The Unlimited-OCR runtime moved into the production package
``aksharamd.plugins.ocr_backends.unlimited_ocr`` in PR 93. This module
is retained ONLY so existing benchmark harnesses and tests that import
from ``benchmarks.pdf_benchmark_adapters.unlimited_ocr_safe_size_cache``
continue to work without change.

New code should import from
``aksharamd.plugins.ocr_backends.unlimited_ocr.cache`` directly.

Implementation note: this shim replaces itself in ``sys.modules`` with
the real cache module — see ``unlimited_ocr_adapter.py`` for the
rationale.
"""
import sys

from aksharamd.plugins.ocr_backends.unlimited_ocr import cache as _impl

sys.modules[__name__] = _impl
