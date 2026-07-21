"""Registry lookup and error surface tests."""
from __future__ import annotations

import pytest

from aksharamd.plugins.ocr_backends import available_backends, get_backend
from aksharamd.plugins.ocr_backends._protocol import OcrBackend


def test_available_backends_only_tesseract():
    # PR 94a ships the tesseract wrapper only; unlimited_ocr lands
    # in PR 94b. Any new entry here must arrive through a follow-up
    # PR that also registers the concrete implementation.
    assert available_backends() == ["tesseract"]


def test_get_backend_tesseract_returns_instance():
    backend = get_backend("tesseract")
    assert isinstance(backend, OcrBackend)
    assert backend.name == "tesseract"


def test_get_backend_unknown_raises_valueerror_listing_knowns():
    with pytest.raises(ValueError) as exc_info:
        get_backend("marker")
    msg = str(exc_info.value)
    assert "marker" in msg
    # Message must list at least the registered backend name so a CLI
    # error can surface it directly.
    assert "tesseract" in msg


def test_get_backend_unlimited_ocr_not_yet_registered():
    # Regression guard: 94b will register this. If it appears in
    # 94a's registry it means scope has widened — reviewer's rule.
    with pytest.raises(ValueError):
        get_backend("unlimited_ocr")


def test_get_backend_returns_fresh_instance():
    a = get_backend("tesseract")
    b = get_backend("tesseract")
    assert a is not b
