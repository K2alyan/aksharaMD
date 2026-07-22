"""Tests for the typed ``BackendAvailabilityDetails`` dataclass and
its progressive population inside ``UnlimitedOcrBackend.availability``.

The details schema is a stable output contract for ``aksharamd doctor
--json``. These tests lock the field set so silent additions are caught
in review.
"""
from __future__ import annotations

import dataclasses
import sys
import types
from unittest.mock import patch

from aksharamd.plugins.ocr_backends import (
    BACKEND_AVAILABILITY_DETAIL_KEYS,
    BackendAvailability,
    BackendAvailabilityDetails,
)
from aksharamd.plugins.ocr_backends.tesseract_backend import TesseractBackend
from aksharamd.plugins.ocr_backends.unlimited_ocr_backend import (
    UnlimitedOcrBackend,
)

# ── Schema locks ────────────────────────────────────────────────────────


def test_details_field_set_matches_module_constant():
    """Adding or removing a details field without updating the
    exported constant is a silent breaking change to the doctor
    --json contract. This test forces the two to stay in sync."""
    fields = {f.name for f in dataclasses.fields(BackendAvailabilityDetails)}
    assert fields == BACKEND_AVAILABILITY_DETAIL_KEYS


def test_details_defaults_are_all_none():
    """Every field defaults to None so a backend records only signals
    it actually observed. A default of False would falsely claim a
    negative observation."""
    d = BackendAvailabilityDetails()
    for name in BACKEND_AVAILABILITY_DETAIL_KEYS:
        assert getattr(d, name) is None, f"{name} should default to None"


def test_availability_details_field_defaults_to_none():
    """BackendAvailability.details is optional; the default is None
    so backends without probe state (Tesseract) don't need to build
    an empty details object."""
    a = BackendAvailability(is_available=True)
    assert a.details is None


# ── Tesseract keeps details=None ────────────────────────────────────────


def test_tesseract_availability_has_no_details():
    """Tesseract has no GPU / model-snapshot probe state, so its
    details should stay None. This distinguishes it from UOC in the
    doctor --json output."""
    fake_pt = types.ModuleType("pytesseract")
    fake_pt.get_tesseract_version = lambda: "5.0.0"
    with patch.dict(sys.modules, {"pytesseract": fake_pt}):
        avail = TesseractBackend().availability()
    assert avail.is_available is True
    assert avail.details is None


def test_tesseract_availability_details_none_on_missing_binary():
    """Even when Tesseract fails to find the binary, we do not
    fabricate details — the backend has no GPU/model concepts."""
    backend = TesseractBackend()
    err = ImportError("No module named 'pytesseract'")

    def _stub_import(name, *a, **kw):
        if name == "pytesseract":
            raise err
        return __import__(name, *a, **kw)

    with patch("builtins.__import__", side_effect=_stub_import):
        avail = backend.availability()
    assert avail.is_available is False
    assert avail.details is None


# ── UOC populates details progressively ─────────────────────────────────


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


def _patch_receipt_valid(monkeypatch, valid: bool = True) -> None:
    """Patch ``check_verification_receipt`` at its import site inside
    ``unlimited_ocr_backend`` so the tightened availability invariant
    (snapshot cached AND receipt matches) can be exercised without a
    real HF cache or receipt directory.

    Also patches ``load_trusted_manifest`` at the same site to return
    a minimal, valid-looking manifest dict — the receipt probe only
    consults keys we control via the fake, and never reads bytes.
    """
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

    reason = "" if valid else "receipt missing"

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


def test_uoc_details_min_vram_recorded_even_when_torch_missing():
    """Even the earliest failure path (torch absent) still carries
    the backend's static minimum-VRAM floor, so ``doctor`` can show
    "requires >= 7000 MiB" without probing hardware."""
    backend = UnlimitedOcrBackend()
    err = ImportError("No module named 'torch'")

    def _stub_import(name, *a, **kw):
        if name == "torch":
            raise err
        return __import__(name, *a, **kw)

    with patch("builtins.__import__", side_effect=_stub_import):
        avail = backend.availability()
    assert avail.is_available is False
    assert avail.details is not None
    assert avail.details.min_vram_mib == UnlimitedOcrBackend._MIN_VRAM_MIB
    # Nothing else was observed yet.
    assert avail.details.bf16_supported is None
    assert avail.details.device_name is None
    assert avail.details.vram_mib_total is None
    assert avail.details.model_snapshot_present is None
    assert avail.details.model_snapshot_verified is None


def test_uoc_details_bf16_recorded_when_bf16_false():
    """When BF16 fails, we still record what we observed (bf16=False)
    so users see WHY not, not just that the backend is unavailable."""
    with patch.dict(sys.modules, {"torch": _fake_torch(bf16_supported=False)}):
        avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is False
    assert avail.details is not None
    assert avail.details.bf16_supported is False
    # VRAM never got probed because we failed at BF16 first.
    assert avail.details.vram_mib_total is None


def test_uoc_details_device_and_vram_recorded_when_vram_too_small():
    """VRAM-too-small path still populates device_name + vram_mib_total
    so a doctor report can say "3050 with 6144 MiB" not just "insufficient VRAM"."""
    with patch.dict(sys.modules, {"torch": _fake_torch(
        total_vram_mib=6000, device_name="NVIDIA GeForce RTX 3050",
    )}):
        avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is False
    assert avail.details is not None
    assert avail.details.bf16_supported is True
    assert avail.details.device_name == "NVIDIA GeForce RTX 3050"
    assert avail.details.vram_mib_total == 6000
    # Model probes never ran.
    assert avail.details.model_snapshot_present is None
    assert avail.details.model_snapshot_verified is None


def test_uoc_details_full_when_runnable(monkeypatch):
    """The happy path — every probe passes AND the local verification
    receipt matches — populates every non-null detail field. Post-PR-99
    the runnable predicate requires a valid receipt as well as a
    cached snapshot."""
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hf_hub(cached=True))
    _patch_receipt_valid(monkeypatch, valid=True)
    avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is True
    assert avail.details is not None
    assert avail.details.min_vram_mib == UnlimitedOcrBackend._MIN_VRAM_MIB
    assert avail.details.bf16_supported is True
    assert avail.details.vram_mib_total == 12_288
    assert avail.details.device_name == "NVIDIA GeForce RTX 3060"
    assert avail.details.model_snapshot_present is True
    assert avail.details.model_snapshot_verified is True


def test_uoc_details_snapshot_missing_recorded_as_false(monkeypatch):
    """When the trusted manifest is present but the HF snapshot is not
    cached, we record ``model_snapshot_present=False`` AND
    ``model_snapshot_verified=False`` — there is nothing to verify.
    The doctor surface can then say "install the model" vs
    "reinstall aksharamd"."""
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hf_hub(cached=False))
    avail = UnlimitedOcrBackend().availability()
    assert avail.is_available is False
    assert avail.details is not None
    assert avail.details.model_snapshot_present is False
    assert avail.details.model_snapshot_verified is False
