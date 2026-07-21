"""Backwards-compat shim for the Unlimited-OCR worker.

The Unlimited-OCR runtime moved into the production package
``aksharamd.plugins.ocr_backends.unlimited_ocr`` in PR 93. This module
is retained ONLY so existing benchmark harnesses and tests that import
from ``benchmarks.pdf_benchmark_adapters.unlimited_ocr_worker``
continue to work without change.

New code should invoke
``python -m aksharamd.plugins.ocr_backends.unlimited_ocr.worker``
directly.

Implementation note: this shim replaces itself in ``sys.modules`` with
the real worker module — see ``unlimited_ocr_adapter.py`` for the
rationale.
"""
import sys

from aksharamd.plugins.ocr_backends.unlimited_ocr import worker as _impl

sys.modules[__name__] = _impl
