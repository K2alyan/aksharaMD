"""Dataclass smoke tests for the OCR backend protocol types."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from aksharamd.plugins.ocr_backends._protocol import (
    BackendAvailability,
    BackendCapabilities,
    OcrBackend,
    OcrFailure,
    OcrPageRequest,
    OcrPageResult,
)


def test_backend_availability_equality_and_asdict():
    a = BackendAvailability(is_available=True)
    b = BackendAvailability(is_available=True, reason="")
    assert a == b
    assert asdict(a) == {
        "is_available": True,
        "reason": "",
        "hardware_compatible": True,
        "model_installed": True,
        "runnable_now": True,
        "details": None,
    }


def test_backend_availability_reason_on_unavailable():
    a = BackendAvailability(is_available=False, reason="torch missing")
    assert a.is_available is False
    assert a.reason == "torch missing"


def test_backend_capabilities_serializes():
    c = BackendCapabilities(
        supports_layout=True,
        supports_math=False,
        supports_tables=True,
        emits="markdown",
    )
    d = asdict(c)
    assert d["emits"] == "markdown"
    assert d["supports_layout"] is True
    assert d["supports_math"] is False
    assert d["supports_tables"] is True


def test_ocr_page_request_defaults():
    r = OcrPageRequest(pdf_path=Path("foo.pdf"), page_indices=[0, 2, 4])
    assert r.dpi == 300
    assert r.page_indices == [0, 2, 4]
    assert r.pdf_path == Path("foo.pdf")


def test_ocr_page_request_asdict():
    r = OcrPageRequest(pdf_path=Path("foo.pdf"), page_indices=[1], dpi=150)
    d = asdict(r)
    assert d["dpi"] == 150
    assert d["page_indices"] == [1]


def test_ocr_page_result_defaults_ok():
    res = OcrPageResult(page_index=3)
    assert res.is_ok is True
    assert res.blocks == []
    assert res.markdown == ""
    assert res.failure is None


def test_ocr_page_result_failure_carries_kind():
    fail = OcrFailure(kind="cuda_oom", message="ran out of vram")
    res = OcrPageResult(page_index=1, is_ok=False, failure=fail)
    assert res.is_ok is False
    assert res.failure is not None
    assert res.failure.kind == "cuda_oom"


def test_ocr_page_result_asdict_nested_failure():
    fail = OcrFailure(kind="timeout", message="30s")
    res = OcrPageResult(page_index=0, is_ok=False, failure=fail)
    d = asdict(res)
    assert d["failure"] == {"kind": "timeout", "message": "30s"}


def test_protocol_is_runtime_checkable():
    class _Fake:
        name = "fake"

        def capabilities(self):
            return BackendCapabilities(False, False, False, emits="blocks")

        def availability(self):
            return BackendAvailability(is_available=True)

        def process(self, request):
            return []

    assert isinstance(_Fake(), OcrBackend)
