"""Registry lookup and error surface tests."""
from __future__ import annotations

import pytest

from aksharamd.plugins.ocr_backends import available_backends, get_backend
from aksharamd.plugins.ocr_backends._protocol import OcrBackend


def test_available_backends_exact_two():
    # PR 94a shipped tesseract; PR 94b adds unlimited_ocr. Any further
    # addition (e.g. an ``auto`` selector) must land through a
    # deliberate PR that also updates every consumer switch.
    assert available_backends() == ["tesseract", "unlimited_ocr"]


def test_get_backend_tesseract_returns_instance():
    backend = get_backend("tesseract")
    assert isinstance(backend, OcrBackend)
    assert backend.name == "tesseract"


def test_get_backend_unlimited_ocr_returns_instance():
    backend = get_backend("unlimited_ocr")
    assert isinstance(backend, OcrBackend)
    assert backend.name == "unlimited_ocr"


def test_get_backend_unknown_raises_valueerror_listing_knowns():
    with pytest.raises(ValueError) as exc_info:
        get_backend("marker")
    msg = str(exc_info.value)
    assert "marker" in msg
    # Message must list every registered backend name so a CLI error
    # can surface it directly.
    assert "tesseract" in msg
    assert "unlimited_ocr" in msg


def test_get_backend_returns_fresh_instance():
    a = get_backend("tesseract")
    b = get_backend("tesseract")
    assert a is not b
