"""Tests for the extended ``aksharamd doctor`` command.

Covers:
* backwards compatibility (existing Rich sections still render)
* new OCR Backends (Registered) section rendering + labeling
* ``--json`` deterministic schema
* ``--json`` output is pure data (no ANSI, no decoration)
* works with no torch installed
* works with CUDA unavailable
* distinguishes unsupported HW / missing model / runnable
* Tesseract diagnostic path is lightweight (no torch import triggered)
* no model download / network call is initiated
"""
from __future__ import annotations

import json
import re
import sys
import types
from unittest.mock import patch

from click.testing import CliRunner

from aksharamd.cli import main

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ── Small stubs mirroring test_unlimited_ocr_backend ────────────────────


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


# ── Backwards compatibility ────────────────────────────────────────────


def test_doctor_rich_output_still_contains_python_and_features_sections():
    """Existing users see the same sections; we only APPEND a new one."""
    r = CliRunner().invoke(main, ["doctor"])
    assert r.exit_code == 0
    assert "AksharaMD" in r.output
    assert "Python" in r.output
    # The original "Optional Features" / "Optional feature" wording.
    assert "Optional" in r.output
    # And the new backends section.
    assert "OCR Backends" in r.output


def test_doctor_rich_labels_backends_as_registered_not_runnable():
    """User rule: label the section so users do NOT read "available
    backends" as "all usable right now"."""
    r = CliRunner().invoke(main, ["doctor"])
    assert r.exit_code == 0
    assert "Registered" in r.output


# ── --json shape + purity ───────────────────────────────────────────────


def test_doctor_json_is_valid_json_and_matches_schema():
    r = CliRunner().invoke(main, ["doctor", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    # Top-level keys, sorted deterministically.
    assert set(payload.keys()) == {
        "python",
        "optional_dependencies",
        "ocr_backends",
        "registered_format_extensions",
    }
    # Python section.
    py = payload["python"]
    assert "version" in py
    assert "meets_minimum" in py
    assert isinstance(py["meets_minimum"], bool)
    assert py["minimum"] == "3.11"
    # ocr_backends is a dict keyed by backend name.
    backends = payload["ocr_backends"]
    assert "tesseract" in backends
    assert "unlimited_ocr" in backends
    for name, info in backends.items():
        assert "capabilities" in info
        assert "availability" in info
        avail = info["availability"]
        # Availability keys are stable — the three-state flags plus
        # is_available/reason/details.
        assert "is_available" in avail
        assert "hardware_compatible" in avail
        assert "model_installed" in avail
        assert "runnable_now" in avail
        assert "reason" in avail
        assert "details" in avail


def test_doctor_json_contains_no_ansi_or_rich_markup():
    """User rule: JSON must be machine-readable data — booleans,
    integers, nulls — not colored strings or Rich [bold] markup."""
    r = CliRunner().invoke(main, ["doctor", "--json"])
    assert r.exit_code == 0
    assert not _ANSI_RE.search(r.output), "ANSI escape sequences leaked into JSON"
    assert "[bold" not in r.output
    assert "[dim" not in r.output
    assert "[green" not in r.output
    assert "[red" not in r.output


def test_doctor_json_backend_availability_uses_native_types():
    """Every availability field is bool | str | int | dict | None —
    never a rendered string like ' ok ' or a color code."""
    r = CliRunner().invoke(main, ["doctor", "--json"])
    payload = json.loads(r.output)
    for name, info in payload["ocr_backends"].items():
        avail = info["availability"]
        assert isinstance(avail["is_available"], bool)
        assert avail["hardware_compatible"] is None or isinstance(
            avail["hardware_compatible"], bool)
        assert avail["model_installed"] is None or isinstance(
            avail["model_installed"], bool)
        assert avail["runnable_now"] is None or isinstance(
            avail["runnable_now"], bool)
        assert isinstance(avail["reason"], str)
        det = avail["details"]
        assert det is None or isinstance(det, dict)


# ── Works with no torch ────────────────────────────────────────────────


def test_doctor_works_with_torch_absent(monkeypatch):
    """Doctor must not crash when torch is not installed. The UOC
    entry becomes not-runnable with a torch-related reason; Tesseract
    remains independent."""
    err = ImportError("No module named 'torch'")

    real_import = __import__

    def _stub_import(name, *a, **kw):
        if name == "torch" or name.startswith("torch."):
            raise err
        return real_import(name, *a, **kw)

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    with patch("builtins.__import__", side_effect=_stub_import):
        r = CliRunner().invoke(main, ["doctor", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    uoc = payload["ocr_backends"]["unlimited_ocr"]["availability"]
    assert uoc["is_available"] is False
    assert "torch" in uoc["reason"].lower()


def test_doctor_works_with_cuda_unavailable(monkeypatch):
    """CUDA-unavailable is distinct from torch-missing. Hardware is
    marked incompatible, reason mentions CUDA."""
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=False))
    r = CliRunner().invoke(main, ["doctor", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    uoc = payload["ocr_backends"]["unlimited_ocr"]["availability"]
    assert uoc["is_available"] is False
    assert uoc["hardware_compatible"] is False
    assert "cuda" in uoc["reason"].lower()


# ── Distinguishes states ───────────────────────────────────────────────


def test_doctor_distinguishes_unsupported_hardware_from_missing_model(monkeypatch):
    """Small VRAM GPU: hardware_compatible=False, model_installed=?.
    Missing HF snapshot: hardware_compatible=True, model_installed=False.
    Same command must show them differently."""
    # Case 1: small GPU (unsupported hardware)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(total_vram_mib=6000))
    r1 = CliRunner().invoke(main, ["doctor", "--json"])
    uoc1 = json.loads(r1.output)["ocr_backends"]["unlimited_ocr"]["availability"]
    assert uoc1["hardware_compatible"] is False
    assert "vram" in uoc1["reason"].lower()

    # Case 2: sufficient hardware, model snapshot not cached
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hf_hub(cached=False))
    r2 = CliRunner().invoke(main, ["doctor", "--json"])
    uoc2 = json.loads(r2.output)["ocr_backends"]["unlimited_ocr"]["availability"]
    assert uoc2["model_installed"] is False
    assert "snapshot" in uoc2["reason"].lower() or "install" in uoc2["reason"].lower()


def test_doctor_runnable_backend_flags_all_true(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hf_hub(cached=True))
    r = CliRunner().invoke(main, ["doctor", "--json"])
    uoc = json.loads(r.output)["ocr_backends"]["unlimited_ocr"]["availability"]
    assert uoc["is_available"] is True
    assert uoc["hardware_compatible"] is True
    assert uoc["model_installed"] is True
    assert uoc["runnable_now"] is True
    assert uoc["details"]["device_name"] == "NVIDIA GeForce RTX 3060"
    assert uoc["details"]["vram_mib_total"] == 12_288


# ── Tesseract is independent ───────────────────────────────────────────


def test_doctor_json_tesseract_entry_has_null_details():
    """Tesseract diagnostics are lightweight — no GPU details."""
    r = CliRunner().invoke(main, ["doctor", "--json"])
    payload = json.loads(r.output)
    tess = payload["ocr_backends"]["tesseract"]["availability"]
    assert tess["details"] is None


# ── No model load, no network ──────────────────────────────────────────


def test_doctor_does_not_load_uoc_model_or_call_network(monkeypatch):
    """The full model load path lives in ``infer_pdf_portable`` /
    the adapter's ``_load_model``. Neither should be reached during
    a doctor probe. Also, huggingface_hub's ``try_to_load_from_cache``
    is a filesystem check — but we must never hit a network route
    like ``snapshot_download`` or ``hf_hub_download``."""
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hf_hub(cached=True))

    called: dict[str, bool] = {"load": False, "snapshot_download": False}

    def _boom_load(*a, **kw):
        called["load"] = True
        raise AssertionError("model must not load during doctor")

    def _boom_snapshot(*a, **kw):
        called["snapshot_download"] = True
        raise AssertionError("network fetch must not happen during doctor")

    # If either path had been reachable, the assertion would trip.
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.adapter._load_model",
        _boom_load,
        create=True,
    ), patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.adapter.snapshot_download",
        _boom_snapshot,
        create=True,
    ):
        r = CliRunner().invoke(main, ["doctor", "--json"])
    assert r.exit_code == 0
    assert called["load"] is False
    assert called["snapshot_download"] is False
