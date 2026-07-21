"""Backwards-compat shim for the Unlimited-OCR adapter.

The Unlimited-OCR runtime moved into the production package
``aksharamd.plugins.ocr_backends.unlimited_ocr`` in PR 93. This module
is retained ONLY so existing benchmark harnesses and tests that import
from ``benchmarks.pdf_benchmark_adapters.unlimited_ocr_adapter``
continue to work without change.

New code should import from
``aksharamd.plugins.ocr_backends.unlimited_ocr.adapter`` directly.

Implementation note
-------------------
This shim swaps its own entry in ``sys.modules`` for the real module
object. That means:

* Any attribute lookup (``mod.X``) reads the real module's attribute.
* Any ``monkeypatch.setattr(mod, ...)`` mutates the real module's
  namespace — which is what tests need for private helpers referenced
  inside class methods to be patchable.
* ``mod.__file__`` and ``mod.__spec__`` point at the production
  location, so any test that reads the source file grep-style sees
  the real code.
"""
import sys

from aksharamd.plugins.ocr_backends.unlimited_ocr import adapter as _impl

sys.modules[__name__] = _impl
