"""PR 99 — locked exact ``recommended_command`` strings per state.

These tests cover each state that ``UnlimitedOcrBackend.availability``
can end in and pin the exact CLI command surfaced to the user (or
``None`` when the remediation is not a single command). They also
lock the state → ``reason`` string for the two lifecycle-related
outcomes so future doctor rendering can rely on the pair being stable.

None of these tests touch a real GPU, HF cache, or receipt directory —
every dependency is stubbed at its import site.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

from aksharamd.plugins.ocr_backends.tesseract_backend import TesseractBackend
from aksharamd.plugins.ocr_backends.unlimited_ocr_backend import (
    UnlimitedOcrBackend,
)

# ── Fakes ──────────────────────────────────────────────────────────────


def _fake_torch(
    cuda_available: bool = True,
    bf16_supported: bool = True,
    total_vram_mib: int = 12_288,
    device_name: str | None = "NVIDIA GeForce RTX 3060",
) -> types.ModuleType:
    torch = types.ModuleType("torch")
    cuda = types.ModuleType("torch.cuda")
    cuda.is_available = lambda: cuda_available  # type: ignore[attr-defined]
    cuda.is_bf16_supported = lambda: bf16_supported  # type: ignore[attr-defined]

    class _Props:
        total_memory = total_vram_mib * 1024 * 1024
        name = device_name

    cuda.get_device_properties = lambda _idx: _Props()  # type: ignore[attr-defined]
    torch.cuda = cuda  # type: ignore[attr-defined]
    return torch


def _fake_hf_hub(cached: bool) -> types.ModuleType:
    hf = types.ModuleType("huggingface_hub")
    hf.try_to_load_from_cache = (  # type: ignore[attr-defined]
        (lambda **kw: "/tmp/fake/config.json") if cached else (lambda **kw: None)
    )
    return hf


def _patch_receipt(monkeypatch, valid: bool, reason: str = "receipt missing") -> None:
    """Stub the receipt probe + manifest loader at their import sites
    inside ``unlimited_ocr_backend``."""
    def _fake_load_trusted_manifest(path=None):  # noqa: ARG001
        return {
            "manifest_schema_version": 1,
            "manifest_id": "fake",
            "repo_id": "fake/repo",
            "revision": "0" * 40,
            "files": {"config.json": {
                "sha256": "0" * 64,
                "size_bytes": 1,
                "class": "config",
                "required_for_runtime": True,
                "verify_on_every_load": False,
            }},
        }

    def _fake_check(manifest, manifest_path, cache_root=None):  # noqa: ARG001
        return (valid, "" if valid else reason)

    monkeypatch.setattr(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.adapter.load_trusted_manifest",
        _fake_load_trusted_manifest,
    )
    monkeypatch.setattr(
        "aksharamd.plugins.ocr_backends.verification_receipt.check_verification_receipt",
        _fake_check,
    )


# ── Snapshot absent → install command ──────────────────────────────────


def test_snapshot_absent_recommends_install(tmp_path, monkeypatch):
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=False),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = UnlimitedOcrBackend().availability()
    assert avail.recommended_command == "aksharamd models install unlimited_ocr"
    assert avail.reason == "Model not installed."
    assert avail.details is not None
    assert avail.details.model_snapshot_present is False
    assert avail.details.model_snapshot_verified is False


# ── Snapshot present but receipt missing/stale → verify command ────────


def test_snapshot_present_receipt_missing_recommends_verify(tmp_path, monkeypatch):
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    _patch_receipt(monkeypatch, valid=False, reason="receipt missing")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=True),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = UnlimitedOcrBackend().availability()
    assert avail.recommended_command == "aksharamd models verify unlimited_ocr"
    assert avail.reason == "Model snapshot is not verified."
    assert avail.details is not None
    assert avail.details.model_snapshot_present is True
    assert avail.details.model_snapshot_verified is False


def test_snapshot_present_receipt_stale_recommends_verify(tmp_path, monkeypatch):
    """A receipt that exists but records a different manifest_id (e.g.
    the user upgraded aksharamd, changing the pinned revision) is
    treated identically to a missing receipt: doctor points the user
    at ``aksharamd models verify unlimited_ocr``."""
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    _patch_receipt(monkeypatch, valid=False, reason="receipt invalid: manifest_id changed")
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=True),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = UnlimitedOcrBackend().availability()
    assert avail.recommended_command == "aksharamd models verify unlimited_ocr"
    assert avail.reason == "Model snapshot is not verified."


# ── Hardware / dependency states leave recommended_command=None ────────


def test_hardware_incompatible_no_recommendation():
    # 6 GiB card cannot host the model — hardware, not a model command.
    with patch.dict(sys.modules, {
        "torch": _fake_torch(total_vram_mib=6_000),
    }):
        avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is False
    assert avail.recommended_command is None


def test_bf16_missing_no_recommendation():
    with patch.dict(sys.modules, {
        "torch": _fake_torch(bf16_supported=False),
    }):
        avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is False
    assert avail.recommended_command is None


def test_cuda_absent_no_recommendation():
    with patch.dict(sys.modules, {
        "torch": _fake_torch(cuda_available=False),
    }):
        avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is False
    assert avail.recommended_command is None


def test_torch_missing_no_recommendation():
    _orig_import = __import__

    def _stub(name, *a, **kw):
        if name == "torch":
            raise ImportError("no torch")
        return _orig_import(name, *a, **kw)

    with patch("builtins.__import__", side_effect=_stub):
        avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is False
    assert avail.recommended_command is None


# ── Happy path leaves recommended_command=None + reason="" ────────────


def test_runnable_no_recommendation(tmp_path, monkeypatch):
    fake_manifest = tmp_path / "trusted.json"
    fake_manifest.write_text("{}", encoding="utf-8")
    _patch_receipt(monkeypatch, valid=True)
    with patch.dict(sys.modules, {
        "torch": _fake_torch(),
        "huggingface_hub": _fake_hf_hub(cached=True),
    }), patch(
        "aksharamd.plugins.ocr_backends.UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        fake_manifest,
    ):
        avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is True
    assert avail.recommended_command is None
    assert avail.reason == ""


# ── Tesseract never recommends a MODEL command ────────────────────────


def test_tesseract_never_recommends_a_model_command():
    """Tesseract's remediation is an OS-package install, not a
    ``aksharamd models`` command. Its ``recommended_command`` must
    stay None regardless of whether it is currently runnable."""
    avail = TesseractBackend().availability()
    assert avail.recommended_command is None
