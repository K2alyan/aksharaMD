"""Unlimited-OCR OCR backend.

Adapter around the production portable entrypoint
``aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable``
which itself provides hardware-aware initial chunk sizing (PR 90),
subprocess isolation (PR 91), and the safe-size cache (PR 92).

This backend deliberately does NOT bypass the portable entrypoint. If
new functionality is needed here it must first be added to
``infer_pdf_portable`` so the isolation, sizing, and cache guarantees
continue to hold.

Deliberate contract note (markdown aggregation)
-----------------------------------------------
``infer_pdf_portable`` returns ONE merged Markdown string covering
every page it processed. There are no reliable per-page boundaries in
the model's output. Rather than fabricate per-page splits (fragile and
misleading), this backend uses an aggregated-batch convention:

* On success, the entire merged Markdown attaches to the OcrPageResult
  at ``page_indices[0]``. All other results carry empty ``markdown``
  but ``is_ok=True``. Their ``meta`` field records the aggregation.
* On failure, every requested page gets ``is_ok=False`` with the same
  ``OcrFailure``.

The pdf.py dispatch layer landing in PR 94c is aware of this convention
via ``BackendCapabilities.emits='markdown'`` and merges accordingly.

Heavy imports (``torch``, ``fitz``, ``PIL``) are lazy so
``import aksharamd`` remains cheap and the no-heavy-import invariant
enforced by ``tests/test_unlimited_ocr_no_heavy_import.py`` continues
to hold after this backend module lands.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
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


class UnlimitedOcrBackend(OcrBackend):
    """Production portable-entrypoint wrapper as an ``OcrBackend``.

    Availability is deliberately narrow: torch importable AND CUDA
    reachable AND the trust manifest present on disk. The full
    manifest verification (byte-level hashes) is deferred to model
    load inside ``infer_pdf_portable`` so ``availability()`` remains
    a lightweight probe suitable for CLI startup.
    """

    name: ClassVar[str] = "unlimited_ocr"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_layout=True,
            supports_math=True,
            supports_tables=True,
            emits="markdown",
        )

    def availability(self) -> BackendAvailability:
        # Torch importable?
        try:
            import torch  # type: ignore
        except ImportError as exc:
            return BackendAvailability(
                is_available=False,
                reason=f"torch not importable: {exc}",
            )
        # CUDA reachable? Probe without touching a device — the probe
        # itself must be side-effect-free.
        try:
            has_cuda = bool(torch.cuda.is_available())
        except Exception as exc:  # pragma: no cover - torch-specific error
            return BackendAvailability(
                is_available=False,
                reason=f"torch.cuda probe raised: {type(exc).__name__}: {exc}",
            )
        if not has_cuda:
            return BackendAvailability(
                is_available=False,
                reason="CUDA not available on this device",
            )
        # Trust manifest exists? The full byte-level verification runs
        # inside infer_pdf_portable at model load; availability() only
        # confirms the artefact is present so the CLI can bail early
        # if it's missing.
        from . import UNLIMITED_OCR_TRUSTED_MANIFEST_PATH
        if not UNLIMITED_OCR_TRUSTED_MANIFEST_PATH.exists():
            return BackendAvailability(
                is_available=False,
                reason=(
                    "Unlimited-OCR trusted manifest missing at "
                    f"{UNLIMITED_OCR_TRUSTED_MANIFEST_PATH}"
                ),
            )
        return BackendAvailability(is_available=True)

    def process(self, request: OcrPageRequest) -> list[OcrPageResult]:
        # Empty batch: one-in / one-out contract means an empty
        # request produces an empty result list.
        if not request.page_indices:
            return []

        # Extract the requested subset of pages to a temporary PDF so
        # infer_pdf_portable processes only those pages. We render the
        # temp PDF preserving the caller's ordering — a page repeated
        # in page_indices produces a repeated page in the temp PDF.
        try:
            import fitz  # type: ignore
        except ImportError as exc:
            failure = OcrFailure(
                kind="backend_unavailable",
                message=f"pymupdf (fitz) not importable: {exc}",
            )
            return [
                OcrPageResult(page_index=idx, is_ok=False, failure=failure)
                for idx in request.page_indices
            ]

        # Lazy import of the portable entrypoint keeps ``import
        # aksharamd`` free of torch even when this backend module is
        # imported.
        from .unlimited_ocr.portable import infer_pdf_portable

        with tempfile.TemporaryDirectory(prefix="uoc_") as workdir_str:
            workdir = Path(workdir_str)
            try:
                subset_pdf = self._extract_subset(
                    fitz, request.pdf_path, request.page_indices, workdir,
                )
            except Exception as exc:
                logger.debug(
                    "Failed to extract subset PDF for %s pages=%s",
                    request.pdf_path, request.page_indices, exc_info=True,
                )
                return self._all_fail(request.page_indices, OcrFailure(
                    kind="other",
                    message=f"subset extraction failed: {type(exc).__name__}: {exc}",
                ))

            try:
                text, exc_str, signals = infer_pdf_portable(subset_pdf, workdir)
            except Exception as exc:
                logger.debug(
                    "infer_pdf_portable raised for %s", request.pdf_path,
                    exc_info=True,
                )
                return self._all_fail(request.page_indices, OcrFailure(
                    kind="other",
                    message=f"infer_pdf_portable raised: {type(exc).__name__}: {exc}",
                ))

        if exc_str:
            return self._all_fail(
                request.page_indices,
                self._classify_infer_failure(exc_str),
            )

        return self._results_with_aggregated_markdown(
            request.page_indices, text or "", signals or {},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_subset(
        fitz_mod, source_pdf: Path, page_indices: list[int], workdir: Path,
    ) -> Path:
        """Return a new PDF path containing exactly the requested pages
        from ``source_pdf`` in the requested order, including any
        repeats."""
        subset = workdir / "subset.pdf"
        with fitz_mod.open(str(source_pdf)) as src:
            page_count = len(src)
            with fitz_mod.open() as dst:
                for idx in page_indices:
                    if idx < 0 or idx >= page_count:
                        raise IndexError(
                            f"page index {idx} out of range for "
                            f"{source_pdf.name} ({page_count} pages)"
                        )
                    dst.insert_pdf(src, from_page=idx, to_page=idx)
                dst.save(str(subset))
        return subset

    @staticmethod
    def _all_fail(
        page_indices: list[int], failure: OcrFailure,
    ) -> list[OcrPageResult]:
        return [
            OcrPageResult(page_index=idx, is_ok=False, failure=failure)
            for idx in page_indices
        ]

    @staticmethod
    def _results_with_aggregated_markdown(
        page_indices: list[int], markdown: str, signals: dict,
    ) -> list[OcrPageResult]:
        """Attach the full merged Markdown to the FIRST result and
        empty ``markdown`` to the rest, with ``meta`` documenting the
        aggregation on the first result so downstream dispatch can
        recognize the convention."""
        results: list[OcrPageResult] = []
        for i, idx in enumerate(page_indices):
            if i == 0:
                results.append(OcrPageResult(
                    page_index=idx,
                    markdown=markdown,
                    is_ok=True,
                    meta={
                        "is_aggregated_batch": True,
                        "covers_page_indices": list(page_indices),
                        "worker_signals": signals.get("worker_signals") or {},
                    },
                ))
            else:
                results.append(OcrPageResult(
                    page_index=idx,
                    markdown="",
                    is_ok=True,
                    meta={"aggregated_at_page_index": page_indices[0]},
                ))
        return results

    @staticmethod
    def _classify_infer_failure(exc_str: str) -> OcrFailure:
        """Map ``infer_pdf_portable``'s error string to a categorical
        ``OcrFailure``. The classifier mirrors the worker exit-code
        classifier so the dispatch layer can react consistently."""
        lower = (exc_str or "").lower()
        if "single_page_oom" in lower:
            # Terminal: even one-page-at-a-time did not fit. This is a
            # non-OOM outcome from the parent's perspective — no smaller
            # size to try.
            return OcrFailure(kind="other", message=exc_str)
        if "cuda_context_unhealthy" in lower or "cuda oom" in lower or "cuda out of memory" in lower:
            return OcrFailure(kind="cuda_oom", message=exc_str)
        if "outofmemoryerror" in lower or "acceleratorerror" in lower:
            return OcrFailure(kind="cuda_oom", message=exc_str)
        if "timeout" in lower:
            return OcrFailure(kind="timeout", message=exc_str)
        return OcrFailure(kind="other", message=exc_str)
