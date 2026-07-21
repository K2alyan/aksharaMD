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


def _fake_torch(
    cuda_available: bool = True,
    bf16_supported: bool = True,
    total_vram_mib: int = 12_288,  # 12 GiB, well above the 7000 floor
) -> types.ModuleType:
    torch = types.ModuleType("torch")
    cuda = types.ModuleType("torch.cuda")
    cuda.is_available = lambda: cuda_available  # type: ignore[attr-defined]
    cuda.is_bf16_supported = lambda: bf16_supported  # type: ignore[attr-defined]

    class _Props:
        total_memory = total_vram_mib * 1024 * 1024

    cuda.get_device_properties = lambda _idx: _Props()  # type: ignore[attr-defined]
    torch.cuda = cuda  # type: ignore[attr-defined]
    return torch


def _fake_hf_hub(cached: bool) -> types.ModuleType:
    """Stub huggingface_hub with ``try_to_load_from_cache`` returning
    a fake path when cached, None otherwise."""
    hf = types.ModuleType("huggingface_hub")
    hf.try_to_load_from_cache = (  # type: ignore[attr-defined]
        (lambda **kw: "/tmp/fake/config.json") if cached else (lambda **kw: None)
    )
    return hf


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


def test_availability_when_bf16_unsupported():
    """Turing / Volta / older cards report cuda available but no
    native bf16 — the model requires bf16 at the pinned revision."""
    backend = UnlimitedOcrBackend()
    with patch.dict(sys.modules, {"torch": _fake_torch(bf16_supported=False)}):
        avail = backend.availability()
    assert avail.is_available is False
    assert "bfloat16" in avail.reason.lower() or "bf16" in avail.reason.lower()


def test_availability_when_vram_below_floor():
    """A 6 GiB card cannot host the ~6.5 GiB model."""
    backend = UnlimitedOcrBackend()
    with patch.dict(sys.modules, {"torch": _fake_torch(total_vram_mib=6_144)}):
        avail = backend.availability()
    assert avail.is_available is False
    assert "vram" in avail.reason.lower() or "memory" in avail.reason.lower()


def test_availability_when_manifest_missing(tmp_path):
    backend = UnlimitedOcrBackend()
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=True),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        tmp_path / "does_not_exist.json",
    ):
        avail = backend.availability()
    assert avail.is_available is False
    assert "manifest" in avail.reason.lower()


def test_availability_when_model_snapshot_not_cached(tmp_path):
    """Fix 2: model weights absent → clear actionable reason so PR 94c
    can produce a helpful fatal error rather than a mysterious
    download during compile."""
    backend = UnlimitedOcrBackend()
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=False),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = backend.availability()
    assert avail.is_available is False
    assert "snapshot" in avail.reason.lower()
    assert "install" in avail.reason.lower()


def test_availability_when_hf_hub_missing(tmp_path):
    backend = UnlimitedOcrBackend()
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")

    # Bind the ORIGINAL __import__ before the patch so the stub's
    # fallback doesn't recurse into the patched wrapper.
    _orig_import = __import__

    def _stub_import(name, *args, **kwargs):
        if name == "huggingface_hub":
            raise ImportError("no huggingface_hub")
        return _orig_import(name, *args, **kwargs)

    with patch.dict(sys.modules, {"torch": _fake_torch()}), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ), patch("builtins.__import__", side_effect=_stub_import):
        avail = backend.availability()
    assert avail.is_available is False
    assert "huggingface_hub" in avail.reason.lower()


def test_availability_state_flags_torch_missing():
    """Torch absent → hardware_compatible=False, model_installed
    intentionally left True (unknown; not what failed here)."""
    backend = UnlimitedOcrBackend()
    _orig = __import__

    def _stub(name, *a, **kw):
        if name == "torch":
            raise ImportError("no torch")
        return _orig(name, *a, **kw)

    with patch("builtins.__import__", side_effect=_stub):
        avail = backend.availability()
    assert avail.is_available is False
    assert avail.hardware_compatible is False
    assert avail.runnable_now is False


def test_availability_state_flags_bf16_unsupported():
    backend = UnlimitedOcrBackend()
    with patch.dict(sys.modules, {"torch": _fake_torch(bf16_supported=False)}):
        avail = backend.availability()
    assert avail.is_available is False
    assert avail.hardware_compatible is False
    assert avail.model_installed is True  # never got that far, no reason to mark False
    assert avail.runnable_now is False


def test_availability_state_flags_model_missing(tmp_path):
    """Model snapshot absent → hardware_compatible stays True, only
    model_installed and runnable_now flip. PR 94c can produce the
    'install the model' message directly."""
    backend = UnlimitedOcrBackend()
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=False),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = backend.availability()
    assert avail.is_available is False
    assert avail.hardware_compatible is True
    assert avail.model_installed is False
    assert avail.runnable_now is False


def test_availability_state_flags_all_pass(tmp_path):
    backend = UnlimitedOcrBackend()
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=True),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = backend.availability()
    assert avail.is_available is True
    assert avail.hardware_compatible is True
    assert avail.model_installed is True
    assert avail.runnable_now is True


def test_availability_when_all_checks_pass(tmp_path):
    backend = UnlimitedOcrBackend()
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=True),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = backend.availability()
    assert avail.is_available is True
    assert avail.reason == ""


def test_availability_does_not_load_model(tmp_path):
    """The probe must not pull ``infer_pdf_portable``, the orchestrator,
    the worker, or the cache module — those all belong to ``process()``.
    ``adapter`` is imported for the pinned repo id / revision constants,
    which is intentional and does not itself load the model."""
    backend = UnlimitedOcrBackend()
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=True),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        before = set(sys.modules.keys())
        backend.availability()
        after = set(sys.modules.keys())
    added = after - before
    # The heavy runtime modules must NOT be touched during availability.
    forbidden = {
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable",
        "aksharamd.plugins.ocr_backends.unlimited_ocr.orchestrator",
        "aksharamd.plugins.ocr_backends.unlimited_ocr.worker",
        "aksharamd.plugins.ocr_backends.unlimited_ocr.cache",
    }
    assert not (added & forbidden), (
        f"availability() pulled runtime modules: {added & forbidden}"
    )


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


def test_process_success_records_subset_to_source_page_mapping(
    three_page_pdf: Path,
):
    """Reviewer's Fix 3: worker diagnostics refer to subset-local
    page indices (0..n-1). The backend must record the mapping to
    source page indices so PR 94c never mis-reports which page
    actually failed."""
    backend = UnlimitedOcrBackend()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.portable.infer_pdf_portable",
        return_value=("md", "", {}),
    ):
        # Deliberately reordered + non-contiguous to make the mapping
        # visibly non-identity.
        results = backend.process(
            OcrPageRequest(pdf_path=three_page_pdf, page_indices=[2, 0], dpi=200)
        )
    assert results[0].meta["subset_page_to_source_page"] == {0: 2, 1: 0}
    # Every result — first or not — carries the same translation table
    # so PR 94c does not have to look up the aggregation head.
    assert results[1].meta["subset_page_to_source_page"] == {0: 2, 1: 0}


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


def test_process_failure_single_page_oom_is_cuda_oom_but_not_retryable(
    three_page_pdf: Path,
):
    """Reviewer's Fix 1: single_page_oom IS a CUDA OOM (the child ran
    out of VRAM). It is not retryable (already at chunk size 1), but
    that distinction belongs to ``meta.retryable``, not to
    ``failure.kind``."""
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
    for r in results:
        assert r.failure is not None
        assert r.failure.kind == "cuda_oom"
        assert "single_page_oom" in r.failure.message
        assert r.meta.get("retryable") is False
        assert r.meta.get("minimum_chunk_reached") is True


def test_process_failure_cuda_unhealthy_is_cuda_oom_and_retryable(
    three_page_pdf: Path,
):
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
    assert results[0].meta.get("retryable") is True


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
