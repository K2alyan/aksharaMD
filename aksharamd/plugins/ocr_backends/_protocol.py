"""OCR backend protocol and shared data types.

Defines the small, JSON-friendly value objects and the ``OcrBackend``
Protocol that concrete OCR backends implement. The protocol is
intentionally minimal:

* ``availability()`` returns a synchronous check that never loads models
  or performs long-running work — safe to call from CLI startup.
* ``capabilities()`` reports statically-known capability flags.
* ``process()`` receives an ``OcrPageRequest`` and returns one
  ``OcrPageResult`` per requested page.

Importing this module is deliberately cheap: it must not import torch,
pytesseract, transformers, or any other heavy dependency. That keeps
``import aksharamd`` fast and lets the CLI import the registry to
resolve a backend name before deciding whether to load anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Closed enums as Literal aliases. Using ``Literal`` catches misspellings at
# type-check time (e.g. a future backend claiming ``emits="markdowns"`` or
# reporting ``kind="oom"``) that a dispatch layer would otherwise silently
# not recognise. Extending either set is a breaking change and must be a
# deliberate PR that also updates every consumer switch.
# ---------------------------------------------------------------------------

OcrEmission = Literal["blocks", "markdown"]

OcrFailureKind = Literal[
    "backend_unavailable",
    "cuda_oom",
    "timeout",
    "other",
]


@dataclass
class BackendAvailability:
    """Whether a backend is usable right now.

    Three orthogonal predicates describe the backend's state; the
    ``is_available`` boolean is their conjunction and remains the
    primary field callers should branch on.

    * ``hardware_compatible`` — the physical device meets this
      backend's minimum requirements (CUDA + bf16 + VRAM floor for
      GPU backends; always True for CPU-only backends like Tesseract).
    * ``model_installed`` — the artefacts the backend needs to run
      (model weights, tesseract binary, pinned trust manifest) are
      present locally. A False here typically means the user needs
      to install something rather than replace hardware.
    * ``runnable_now`` — nothing transient is blocking. Reserved for
      future use (e.g. "another OCR run is holding the GPU"); for
      now, mirrors ``is_available``.

    Callers that want to render distinct CLI messages ("install the
    model" vs "unsupported GPU") inspect the three flags directly.
    ``reason`` records the FIRST failing predicate's actionable text
    and remains empty when ``is_available`` is True.
    """

    is_available: bool
    reason: str = ""
    hardware_compatible: bool = True
    model_installed: bool = True
    runnable_now: bool = True


@dataclass
class BackendCapabilities:
    """Static capability flags for a backend.

    ``emits`` is one of ``"blocks"`` (the backend returns a list of
    already-structured ``Block`` objects via ``OcrPageResult.blocks``)
    or ``"markdown"`` (the backend returns a markdown string via
    ``OcrPageResult.markdown``). Callers dispatch on this to merge
    results into the document.
    """

    supports_layout: bool
    supports_math: bool
    supports_tables: bool
    emits: OcrEmission


@dataclass
class OcrPageRequest:
    """One OCR request covering a set of pages from a single PDF.

    ``page_indices`` are 0-based indices into the source PDF and must
    only contain pages that the caller has already classified as
    needing OCR. The backend is not required to re-classify them.
    """

    pdf_path: Path
    page_indices: list[int]
    dpi: int = 300


@dataclass
class OcrFailure:
    """Categorical failure for a single page's OCR result.

    ``kind`` is one of ``"backend_unavailable"``, ``"cuda_oom"``,
    ``"timeout"``, ``"other"``. Callers use ``kind`` to decide whether
    to surface a specific validator warning; ``message`` is the
    human-readable detail.
    """

    kind: OcrFailureKind
    message: str = ""


@dataclass
class OcrPageResult:
    """Result of OCR for a single page.

    Exactly one of ``blocks`` or ``markdown`` will be populated in
    practice, keyed by the backend's ``capabilities().emits`` value.
    Both remain on the dataclass so callers can dispatch on ``emits``
    without inspecting the shape.

    ``is_ok=False`` means the page failed and ``failure`` describes why.
    Callers must not silently drop such pages — the compile flow is
    expected to surface a per-page warning.

    ``meta`` is a free-form dict of backend-specific per-page metadata.
    Backends that batch output (e.g. UnlimitedOcrBackend's aggregated-
    markdown convention) use it to describe the aggregation to a
    dispatch layer without changing the primary shape.
    """

    page_index: int
    markdown: str = ""
    blocks: list[Any] = field(default_factory=list)
    is_ok: bool = True
    failure: OcrFailure | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OcrBackend(Protocol):
    """A single OCR backend implementation.

    Concrete backends live in sibling modules
    (``tesseract_backend.py``, ``unlimited_ocr_backend.py``) and are
    registered in :mod:`._registry`.
    """

    name: ClassVar[str]

    def capabilities(self) -> BackendCapabilities: ...

    def availability(self) -> BackendAvailability: ...

    def process(self, request: OcrPageRequest) -> list[OcrPageResult]: ...
