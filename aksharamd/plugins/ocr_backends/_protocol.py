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
from typing import Any, ClassVar, Protocol, runtime_checkable


@dataclass
class BackendAvailability:
    """Whether a backend is usable right now.

    ``reason`` is empty when ``is_available`` is True. When False,
    ``reason`` must be a short human-readable string suitable for a
    CLI error message (e.g. ``"torch not importable"``).
    """

    is_available: bool
    reason: str = ""


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
    emits: str  # "blocks" | "markdown"


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

    kind: str
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
    """

    page_index: int
    markdown: str = ""
    blocks: list[Any] = field(default_factory=list)
    is_ok: bool = True
    failure: OcrFailure | None = None


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
