"""Unlimited-OCR production runtime package.

Public API entrypoint for the Unlimited-OCR local vision-LLM OCR
backend. The heavy dependencies (``torch``, ``transformers``) are
NEVER imported at package load time — they are imported lazily inside
the functions that actually need them. Importing this package (or its
parent ``aksharamd``) must remain cheap so the CLI's cold-start cost
is not affected by an optional backend.

The public API is intentionally narrow:

* :func:`infer_pdf_portable` — the single end-to-end entrypoint that
  runs one document through the subprocess-isolated worker with
  hardware-aware initial sizing and safe-size caching.
* :func:`run_infer_pdf_isolated` — lower-level orchestrator that runs
  a document in a disposable worker, halving the chunk size and
  retrying on CUDA-OOM signals.
* :func:`default_cache_path`, :func:`is_cache_disabled`,
  :func:`clear_cache` — helpers for locating, disabling, and clearing
  the safe-size cache. See :mod:`._paths`.

Cache and cross-run persistence details are documented in
``docs/ocr/unlimited_ocr_cache.md``.

Nothing in this package wires into the AksharaMD compile flow at
present — that integration ships separately in a later PR.
"""
from __future__ import annotations

from ._paths import (
    clear_cache,
    default_cache_path,
    is_cache_disabled,
)

__all__ = [
    "clear_cache",
    "default_cache_path",
    "is_cache_disabled",
    "infer_pdf_portable",
    "run_infer_pdf_isolated",
]


def infer_pdf_portable(*args, **kwargs):
    """Lazy wrapper for :func:`.portable.infer_pdf_portable`.

    Deferring the submodule import keeps ``import aksharamd`` cheap:
    the portable module transitively references torch, and materialising
    it at package load would defeat the "no torch on import" guarantee.
    """
    from .portable import infer_pdf_portable as _impl

    return _impl(*args, **kwargs)


def run_infer_pdf_isolated(*args, **kwargs):
    """Lazy wrapper for :func:`.orchestrator.run_infer_pdf_isolated`."""
    from .orchestrator import run_infer_pdf_isolated as _impl

    return _impl(*args, **kwargs)
