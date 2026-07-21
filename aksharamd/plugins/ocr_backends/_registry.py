"""OCR backend registry.

Foundation-only registry (PR 94a). One entry: ``tesseract``. The
``unlimited_ocr`` entry lands in PR 94b; ``auto`` and any policy-
based selection is deliberately absent until doctor and rollout
work has landed.

Callers pass an explicit backend name (later wired to the CLI in
PR 94c). Unknown names raise ``ValueError`` with the known list.

The registry stores factory callables, not instances, so backend
construction can be delayed until the caller actually needs it. Each
factory returns a fresh instance; backends themselves may be lazy
about their heavy dependencies.
"""
from __future__ import annotations

from collections.abc import Callable

from ._protocol import OcrBackend


def _make_tesseract() -> OcrBackend:
    from .tesseract_backend import TesseractBackend
    return TesseractBackend()


_REGISTRY: dict[str, Callable[[], OcrBackend]] = {
    "tesseract": _make_tesseract,
}


def get_backend(name: str) -> OcrBackend:
    """Return a fresh backend instance for ``name``.

    Raises ``ValueError`` with a message listing the known backend
    names if ``name`` is not registered. The message is intended to
    be surfaced directly in a CLI error.
    """
    factory = _REGISTRY.get(name)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"Unknown OCR backend {name!r}. Known backends: {known}."
        )
    return factory()


def available_backends() -> list[str]:
    """Return the registered backend names, in registration order."""
    return list(_REGISTRY.keys())
