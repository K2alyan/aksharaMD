"""Unlimited-OCR backend tests.

All tests operate against mocked ``infer_pdf_portable`` and mocked
``torch`` / ``torch.cuda``. None of them load the model, spawn a
subprocess, or touch a GPU.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from aksharamd.plugins.ocr_backends._protocol import OcrPageRequest
from aksharamd.plugins.ocr_backends.unlimited_ocr_backend import (
    UnlimitedOcrBackend,
)

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def three_page_pdf(tmp_path: Path) -> Path:
    """Synthetic three-page PDF used by ``process`` tests."""
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    for _ in range(3):
        pdf.new_page(width=100, height=100)
    p = tmp_path / "three.pdf"
    pdf.save(str(p))
    pdf.close()
    return p


def _fake_torch(cuda_available: bool) -> types.ModuleType:
    torch = types.ModuleType("torch")
    cuda = types.ModuleType("torch.cuda")
    cuda.is_available = lambda: cuda_available  # type: ignore[attr-defined]
    torch.cuda = cuda  # type: ignore[attr-defined]
    return torch


# ── Capabilities ────────────────────────────────────────────────────────


def test_capabilities():
    caps = UnlimitedOcrBackend().capabilities()
    assert caps.emits == "markdown"
    assert caps.supports_layout is True
    assert caps.supports_math is True
    assert caps.supports_tables is True


# ── Availability probe ─────────────────────────────────────────────────


def test_availability_when_torch_missing():
    backend = UnlimitedOcrBackend()
    fake_import_error = ImportError("No module named 'torch'")

    def _stub_import(name, *args, **kwargs):
        if name == "torch":
            raise fake_import_error
        return __import__(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_stub_import):
        avail = backend.availability()
    assert avail.is_available is False
    assert "torch" in avail.reason.lower()


def test_availability_when_cuda_absent():
    backend = UnlimitedOcrBackend()
    with patch.dict(sys.modules, {"torch": _fake_torch(cuda_available=False)}):
        avail = backend.availability()
    assert avail.is_available is False
    assert "cuda" in avail.reason.lower()


def test_availability_when_manifest_missing(tmp_path):
    backend = UnlimitedOcrBackend()
    with patch.dict(sys.modules, {"torch": _fake_torch(cuda_available=True)}), \
         patch(
             "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
             tmp_path / "does_not_exist.json",
         ):
        avail = backend.availability()
    assert avail.is_available is False
    assert "manifest" in avail.reason.lower()


def test_availability_when_all_checks_pass(tmp_path):
    backend = UnlimitedOcrBackend()
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    with patch.dict(sys.modules, {"torch": _fake_torch(cuda_available=True)}), \
         patch(
             "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
             fake_manifest,
         ):
        avail = backend.availability()
    assert avail.is_available is True
    assert avail.reason == ""


def test_availability_does_not_load_model():
    """The probe must not pull ``infer_pdf_portable`` or the runner."""
    backend = UnlimitedOcrBackend()
    with patch.dict(sys.modules, {"torch": _fake_torch(cuda_available=True)}):
        before = set(sys.modules.keys())
        backend.availability()
        after = set(sys.modules.keys())
    added = after - before
    # The runner + orchestrator + portable modules must NOT be touched.
    heavy = {
        m for m in added
        if m.startswith("aksharamd.plugins.ocr_backends.unlimited_ocr.")
    }
    # The manifest constant lives in the ``ocr_backends`` __init__ which
    # is already imported by earlier code paths — but no submodules
    # under ``unlimited_ocr.`` should be pulled.
    assert not heavy, f"availability() pulled runtime modules: {heavy}"


# ── process(): aggregated-markdown contract ────────────────────────────


def test_process_success_puts_markdown_on_first_result(three_page_pdf: Path):
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
        return_value=("hello world markdown", "", {"worker_signals": {"page_count": 2}}),
    ) as mock_infer:
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0, 1], dpi=200)
        )
    assert mock_infer.call_count == 1
    assert len(results) == 2
    # First result carries the full markdown.
    assert results[0].page_index == 0
    assert results[0].markdown == "hello world markdown"
    assert results[0].is_ok is True
    assert results[0].meta["is_aggregated_batch"] is True
    assert results[0].meta["covers_page_indices"] == [0, 1]
    # Subsequent result carries empty markdown but is ok.
    assert results[1].page_index == 1
    assert results[1].markdown == ""
    assert results[1].is_ok is True
    assert results[1].meta["aggregated_at_page_index"] == 0


def test_process_preserves_page_indices_order(three_page_pdf: Path):
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
        return_value=("md", "", {}),
    ):
        order = [2, 0, 1]
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=order, dpi=200)
        )
    assert [r.page_index for r in results] == order


def test_process_returns_one_result_per_requested_index_on_success(
    three_page_pdf: Path,
):
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
        return_value=("md", "", {}),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0, 1, 2], dpi=200)
        )
    assert len(results) == 3


def test_process_empty_page_indices():
    backend = UnlimitedOcrBackend()
    # infer_pdf_portable must never be called for an empty batch.
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
    ) as mock_infer:
        results = backend.process(
            OcrPageRequest(pdf_path=Path("/does/not/matter.pdf"),
                           page_indices=[], dpi=200)
        )
    assert results == []
    assert mock_infer.call_count == 0


# ── process(): failure paths ───────────────────────────────────────────


def test_process_failure_when_infer_returns_error_string(three_page_pdf: Path):
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
        return_value=("", "isolated_infer_failed: single_page_oom", {}),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0, 1], dpi=200)
        )
    assert len(results) == 2
    assert all(not r.is_ok for r in results)
    # single_page_oom is terminal and classified as 'other', not cuda_oom.
    for r in results:
        assert r.failure is not None
        assert r.failure.kind == "other"
        assert "single_page_oom" in r.failure.message


def test_process_failure_when_infer_returns_cuda_unhealthy(three_page_pdf: Path):
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
        return_value=("", "chunked_infer_failed: cuda_context_unhealthy_after_oom", {}),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0], dpi=200)
        )
    assert results[0].failure is not None
    assert results[0].failure.kind == "cuda_oom"


def test_process_failure_when_infer_raises(three_page_pdf: Path):
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
        side_effect=RuntimeError("something went sideways"),
    ):
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0, 1], dpi=200)
        )
    assert all(not r.is_ok for r in results)
    for r in results:
        assert r.failure is not None
        assert r.failure.kind == "other"


def test_process_failure_when_subset_extraction_hits_out_of_range(
    three_page_pdf: Path,
):
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
    ) as mock_infer:
        # 999 is out of range for the 3-page PDF; subset extraction must
        # fail cleanly BEFORE infer_pdf_portable is invoked.
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[0, 999], dpi=200)
        )
    assert mock_infer.call_count == 0
    assert len(results) == 2
    assert all(not r.is_ok for r in results)


# ── Import hygiene ────────────────────────────────────────────────────


def test_importing_backend_module_does_not_import_torch():
    """The whole point: importing the backend module (as the CLI
    registry does) must not bring torch into sys.modules."""
    import subprocess as sp
    code = (
        "import sys; "
        "import aksharamd.plugins.ocr_backends.unlimited_ocr_backend; "
        "assert 'torch' not in sys.modules; "
        "assert 'transformers' not in sys.modules; "
        "print('OK')"
    )
    result = sp.run(
        [sys.executable, "-c", code], capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert b"OK" in result.stdout
