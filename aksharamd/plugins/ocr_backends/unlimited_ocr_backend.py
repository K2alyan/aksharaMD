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

    # Hardware compatibility floors. Bumped deliberately, not
    # negotiated: any change must land through a PR that also updates
    # the docs at ``docs/ocr/`` and re-runs the reviewer's acceptance.
    #
    # ``_MIN_VRAM_MIB``: floor for the total device memory, in MiB.
    # The Unlimited-OCR model at the pinned bf16 revision holds
    # ~6.5 GiB on device before any page is processed; a card that
    # cannot exceed ~7 GiB total has no headroom for even the smallest
    # chunk. 7000 MiB is generous enough to avoid false negatives on
    # driver-reported sizes just under 8 GiB while still rejecting
    # 6 GiB cards that cannot host the model at all.
    _MIN_VRAM_MIB = 7000

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
        # BF16 supported? The model runs in bfloat16 at the pinned
        # revision. Cards without native bf16 (Turing / Volta / older)
        # cannot execute it correctly. torch.cuda.is_bf16_supported
        # is a cheap capability probe — it does NOT run any kernel.
        try:
            bf16_ok = bool(torch.cuda.is_bf16_supported())
        except Exception as exc:  # pragma: no cover - defensive
            return BackendAvailability(
                is_available=False,
                reason=(
                    f"torch.cuda.is_bf16_supported probe raised: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
        if not bf16_ok:
            return BackendAvailability(
                is_available=False,
                reason=(
                    "GPU does not support bfloat16; Unlimited-OCR at the "
                    "pinned revision requires bf16. Turing / Volta / older "
                    "cards are not supported."
                ),
            )
        # Sufficient VRAM to host the model at all? Read the TOTAL
        # memory (not the free memory) so this is a hardware-capability
        # check, not a "is another app currently using the GPU"
        # check — the latter belongs to portable's per-run VRAM probe.
        try:
            total_bytes = int(torch.cuda.get_device_properties(0).total_memory)
        except Exception as exc:  # pragma: no cover - torch-specific error
            return BackendAvailability(
                is_available=False,
                reason=(
                    f"torch.cuda.get_device_properties(0) probe raised: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
        total_mib = total_bytes // (1024 * 1024)
        if total_mib < self._MIN_VRAM_MIB:
            return BackendAvailability(
                is_available=False,
                reason=(
                    f"GPU total VRAM {total_mib} MiB below the "
                    f"{self._MIN_VRAM_MIB} MiB minimum required to host "
                    "the Unlimited-OCR model at its pinned bf16 revision"
                ),
            )
        # Trust manifest present? The full byte-level verification runs
        # inside infer_pdf_portable at model load; availability() only
        # confirms the packaged artefact is present so the CLI can bail
        # early if an install has been corrupted.
        from . import UNLIMITED_OCR_TRUSTED_MANIFEST_PATH
        if not UNLIMITED_OCR_TRUSTED_MANIFEST_PATH.exists():
            return BackendAvailability(
                is_available=False,
                reason=(
                    "Unlimited-OCR trusted manifest missing at "
                    f"{UNLIMITED_OCR_TRUSTED_MANIFEST_PATH}"
                ),
            )
        # Model snapshot cached locally? An "available" backend means
        # `process()` should be able to run without downloading anything
        # at inference time. We probe by asking huggingface_hub whether
        # the pinned revision's config.json is already in the local
        # cache. This is a filesystem check + tiny hf_hub import — no
        # network call.
        try:
            from huggingface_hub import try_to_load_from_cache  # type: ignore
        except ImportError as exc:
            return BackendAvailability(
                is_available=False,
                reason=(
                    f"huggingface_hub not importable, cannot verify model "
                    f"snapshot presence: {exc}"
                ),
            )
        from .unlimited_ocr.adapter import (
            _UNLIMITED_OCR_MODEL_REPO,
            _UNLIMITED_OCR_MODEL_REVISION,
        )
        cached = try_to_load_from_cache(
            repo_id=_UNLIMITED_OCR_MODEL_REPO,
            filename="config.json",
            revision=_UNLIMITED_OCR_MODEL_REVISION,
        )
        if cached is None:
            return BackendAvailability(
                is_available=False,
                reason=(
                    "Unlimited-OCR model snapshot not cached locally at "
                    f"revision {_UNLIMITED_OCR_MODEL_REVISION}. "
                    "Install it before selecting this backend."
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
                return self._all_fail(
                    request.page_indices,
                    OcrFailure(
                        kind="other",
                        message=f"subset extraction failed: {type(exc).__name__}: {exc}",
                    ),
                    extra_meta={"retryable": False},
                )

            try:
                text, exc_str, signals = infer_pdf_portable(subset_pdf, workdir)
            except Exception as exc:
                logger.debug(
                    "infer_pdf_portable raised for %s", request.pdf_path,
                    exc_info=True,
                )
                return self._all_fail(
                    request.page_indices,
                    OcrFailure(
                        kind="other",
                        message=f"infer_pdf_portable raised: {type(exc).__name__}: {exc}",
                    ),
                    extra_meta={"retryable": False},
                )

        if exc_str:
            failure, extra_meta = self._classify_infer_failure(exc_str)
            return self._all_fail(request.page_indices, failure, extra_meta)

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
        page_indices: list[int],
        failure: OcrFailure,
        extra_meta: dict | None = None,
    ) -> list[OcrPageResult]:
        """Return one failed OcrPageResult per requested index.

        ``extra_meta`` (if provided) is copied onto each per-page
        result. Callers use this to attach classification-derived
        state like ``retryable`` or ``minimum_chunk_reached`` — the
        failure ``kind`` describes WHAT went wrong; ``meta``
        describes what the CALLER can DO about it.
        """
        return [
            OcrPageResult(
                page_index=idx,
                is_ok=False,
                failure=failure,
                meta=dict(extra_meta) if extra_meta else {},
            )
            for idx in page_indices
        ]

    @staticmethod
    def _results_with_aggregated_markdown(
        page_indices: list[int], markdown: str, signals: dict,
    ) -> list[OcrPageResult]:
        """Attach the full merged Markdown to the FIRST result and
        empty ``markdown`` to the rest, with ``meta`` documenting the
        aggregation on the first result so downstream dispatch can
        recognize the convention.

        Reviewer's Fix 3: the temporary subset PDF renumbers pages
        internally as 0..n-1. Worker diagnostics inside
        ``worker_signals`` refer to those SUBSET-LOCAL indices.
        ``subset_page_to_source_page`` is an explicit translation
        table so PR 94c never reports that source page 0 failed
        when the actual OCR request was for source page 37.
        """
        subset_map = {i: src for i, src in enumerate(page_indices)}
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
                        "subset_page_to_source_page": subset_map,
                        "worker_signals": signals.get("worker_signals") or {},
                    },
                ))
            else:
                results.append(OcrPageResult(
                    page_index=idx,
                    markdown="",
                    is_ok=True,
                    meta={
                        "aggregated_at_page_index": page_indices[0],
                        "subset_page_to_source_page": subset_map,
                    },
                ))
        return results

    @staticmethod
    def _classify_infer_failure(exc_str: str) -> tuple[OcrFailure, dict]:
        """Map ``infer_pdf_portable``'s error string to a categorical
        ``OcrFailure`` plus a retryability meta dict.

        Reviewer's Fix 1: ``kind`` describes WHAT happened; ``meta``
        describes what the caller can DO about it. In particular,
        ``single_page_oom`` is still fundamentally a CUDA OOM
        (the child ran out of VRAM), just not one a smaller subprocess
        chunk can survive — those two facts are captured as
        ``kind="cuda_oom"`` and ``meta={"retryable": False,
        "minimum_chunk_reached": True}``.

        Retryability policy:
          * cuda_oom above single-page             retryable=True
          * cuda_oom at single_page_oom            retryable=False,
                                                   minimum_chunk_reached=True
          * cuda_context_unhealthy_after_oom       retryable=True
          * timeout                                retryable=True
          * other                                  retryable=False
        """
        lower = (exc_str or "").lower()
        if "single_page_oom" in lower:
            # Terminal CUDA OOM — halving the chunk further makes no
            # sense (already at size 1) but the underlying cause IS
            # memory exhaustion. The failure kind reflects that; the
            # retryability meta tells the caller not to try again.
            return (
                OcrFailure(kind="cuda_oom", message=exc_str),
                {"retryable": False, "minimum_chunk_reached": True},
            )
        if "cuda_context_unhealthy" in lower:
            return (
                OcrFailure(kind="cuda_oom", message=exc_str),
                {"retryable": True, "reason": "cuda_context_unhealthy"},
            )
        if (
            "cuda oom" in lower
            or "cuda out of memory" in lower
            or "outofmemoryerror" in lower
            or "acceleratorerror" in lower
        ):
            return (
                OcrFailure(kind="cuda_oom", message=exc_str),
                {"retryable": True},
            )
        if "timeout" in lower:
            return (
                OcrFailure(kind="timeout", message=exc_str),
                {"retryable": True},
            )
        return (
            OcrFailure(kind="other", message=exc_str),
            {"retryable": False},
        )
