"""PR 100 — Auto Policy v1 end-to-end dispatch tests.

These tests build small PDFs via the existing conftest fixtures
(``digital_only_pdf``, ``scanned_only_pdf``, ``mixed_pdf``) and drive
the compiler with ``--ocr-backend auto`` through :class:`CliRunner` or
directly. UOC availability is stubbed so the tests never load a real
model or perform network I/O.

Fixtures used:

* ``digital_only_pdf`` — 2 pages of digital text (0 OCR-required).
* ``scanned_only_pdf`` — 2 pages of pure image (2 OCR-required,
  below the 3-page floor).
* ``mixed_pdf`` — 3 pages: digital / scanned / digital
  (1 OCR-required).

For the 20-page mixed-with-9-scanned test case we build a fresh PDF
in-fixture because it doesn't map cleanly onto the shared conftest
inventory.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from aksharamd.compiler import Compiler
from aksharamd.plugins.ocr_backends._protocol import (
    BackendAvailability,
    BackendAvailabilityDetails,
    BackendCapabilities,
    OcrPageRequest,
    OcrPageResult,
)

# ── shared stubs ───────────────────────────────────────────────────────


class _StubUocBackend:
    """A stub UOC backend that records calls and returns aggregated MD."""

    name = "unlimited_ocr"

    def __init__(
        self,
        availability: BackendAvailability,
        markdown: str = "OCR line one\n\nOCR line two\n",
    ) -> None:
        self._availability = availability
        self._markdown = markdown
        self.process_calls: list[OcrPageRequest] = []

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_layout=True, supports_math=True,
            supports_tables=True, emits="markdown",
        )

    def availability(self) -> BackendAvailability:
        return self._availability

    def process(self, request: OcrPageRequest) -> list[OcrPageResult]:
        self.process_calls.append(request)
        results: list[OcrPageResult] = []
        for i, idx in enumerate(request.page_indices):
            if i == 0:
                results.append(OcrPageResult(
                    page_index=idx, markdown=self._markdown, is_ok=True,
                    meta={
                        "is_aggregated_batch": True,
                        "covers_page_indices": list(request.page_indices),
                    },
                ))
            else:
                results.append(OcrPageResult(
                    page_index=idx, markdown="", is_ok=True,
                    meta={"aggregated_at_page_index": request.page_indices[0]},
                ))
        return results


def _make_uoc_runnable() -> BackendAvailability:
    return BackendAvailability(
        is_available=True, reason="",
        hardware_compatible=True, model_installed=True, runnable_now=True,
        details=BackendAvailabilityDetails(
            model_snapshot_present=True, model_snapshot_verified=True,
        ),
        recommended_command=None,
    )


def _make_uoc_not_installed() -> BackendAvailability:
    return BackendAvailability(
        is_available=False, reason="Model snapshot is not installed.",
        hardware_compatible=True, model_installed=False, runnable_now=False,
        details=BackendAvailabilityDetails(
            model_snapshot_present=False, model_snapshot_verified=False,
        ),
        recommended_command="aksharamd models install unlimited_ocr",
    )


def _make_uoc_receipt_stale() -> BackendAvailability:
    return BackendAvailability(
        is_available=False, reason="Model snapshot is not verified.",
        hardware_compatible=True, model_installed=True, runnable_now=False,
        details=BackendAvailabilityDetails(
            model_snapshot_present=True, model_snapshot_verified=False,
        ),
        recommended_command="aksharamd models verify unlimited_ocr",
    )


def _make_uoc_hardware_incompatible() -> BackendAvailability:
    return BackendAvailability(
        is_available=False, reason="No CUDA-capable GPU detected.",
        hardware_compatible=False, model_installed=True, runnable_now=False,
        details=BackendAvailabilityDetails(
            model_snapshot_present=True, model_snapshot_verified=True,
        ),
        recommended_command=None,
    )


def _make_large_mixed_pdf(tmp_path: Path, *, total: int, scanned: int) -> Path:
    """Build a PDF with ``total`` pages, of which the FIRST ``scanned``
    pages are pure images (OCR-required) and the rest hold extractable
    text."""
    fitz = pytest.importorskip("fitz")
    from PIL import Image

    pdf = fitz.open()
    for i in range(total):
        page = pdf.new_page(width=595, height=842)
        if i < scanned:
            img = Image.new("RGB", (16, 16), color=(200, 200, 200))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            rect = fitz.Rect(50, 50, 550, 800)
            page.insert_image(rect, stream=buf.getvalue())
        else:
            page.insert_text(
                (72, 100),
                f"Digital page {i} carries plenty of extractable text — "
                "well above the OCR classifier threshold, so this page "
                "is not routed to the OCR backend.",
                fontsize=12,
            )
    p = tmp_path / f"mix_{total}_{scanned}.pdf"
    pdf.save(str(p))
    pdf.close()
    return p


def _compile_with_auto(tmp_path: Path, source: Path, stub: _StubUocBackend) -> Any:
    """Run the compiler with ``ocr_backend='auto'`` and the given UOC stub."""
    out = tmp_path / "out"
    compiler = Compiler(output_dir=str(out), ocr_backend="auto")
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        return compiler.compile(str(source))


# ── 1. Digital-only PDF, no OCR needed, no UOC installed ──────────────


def test_digital_only_auto_never_falls_back_or_warns(tmp_path, digital_only_pdf):
    """A fully digital PDF must succeed under ``auto`` even when UOC
    is completely broken: no OCR is needed, so the selector returns
    Tesseract with no fallback, and no fallback warning is emitted."""
    stub = _StubUocBackend(_make_uoc_hardware_incompatible())
    ctx = _compile_with_auto(tmp_path, digital_only_pdf, stub)

    # UOC never invoked (there was no OCR to do).
    assert stub.process_calls == []
    # Manifest reflects the auto request and Tesseract selection.
    m = ctx.manifest
    assert m is not None
    assert m.ocr_backend_requested == "auto"
    assert m.ocr_backend_selected == "tesseract"
    assert m.ocr_auto_policy_version == "1"
    assert m.ocr_auto_decision is not None
    d = m.ocr_auto_decision
    assert d["ocr_required_pages"] == 0
    assert d["fallback_occurred"] is False
    assert d["fallback_reason"] is None
    assert d["preferred_backend"] == "tesseract"
    # No fallback warning code appears.
    assert "AUTO_OCR_BACKEND_FALLBACK" not in m.warning_codes
    # SELECTED warning code IS emitted.
    assert "AUTO_OCR_BACKEND_SELECTED" in m.warning_codes


# ── 2. 2-page fully-scanned PDF (below page floor) ─────────────────────


def test_two_page_scanned_selects_tesseract_no_fallback(tmp_path, scanned_only_pdf):
    """Fully scanned but only 2 pages — below the 3-page floor. The
    policy picks Tesseract, and no fallback is recorded because
    Tesseract was the policy's own preference."""
    stub = _StubUocBackend(_make_uoc_runnable())
    ctx = _compile_with_auto(tmp_path, scanned_only_pdf, stub)

    # UOC was runnable but not preferred, so it was not consulted.
    assert stub.process_calls == []
    m = ctx.manifest
    assert m is not None
    assert m.ocr_backend_selected == "tesseract"
    assert m.ocr_auto_decision["fallback_occurred"] is False
    assert m.ocr_auto_decision["preferred_backend"] == "tesseract"
    assert "AUTO_OCR_BACKEND_FALLBACK" not in m.warning_codes


# ── 3. 20-page mixed PDF (9 scanned, 45%) → UOC selected ──────────────


def test_large_mixed_meets_thresholds_and_uoc_runnable_selects_uoc(tmp_path):
    src = _make_large_mixed_pdf(tmp_path, total=20, scanned=9)
    stub = _StubUocBackend(_make_uoc_runnable(),
                           markdown="UNIQUE_AUTO_UOC_MARKER text.\n")
    ctx = _compile_with_auto(tmp_path, src, stub)

    # UOC was consulted with exactly the 9 scanned page indices [0..8].
    assert len(stub.process_calls) == 1
    assert stub.process_calls[0].page_indices == list(range(9))
    m = ctx.manifest
    assert m is not None
    assert m.ocr_backend_selected == "unlimited_ocr"
    d = m.ocr_auto_decision
    assert d["preferred_backend"] == "unlimited_ocr"
    assert d["fallback_occurred"] is False
    assert d["ocr_required_pages"] == 9
    assert d["total_pages"] == 20
    assert d["fraction_threshold"] == pytest.approx(0.30)
    assert "AUTO_OCR_BACKEND_SELECTED" in m.warning_codes
    assert "AUTO_OCR_BACKEND_FALLBACK" not in m.warning_codes


# ── 4. Same but UOC not installed → fallback with recommended command ──


def test_large_mixed_uoc_not_installed_falls_back(tmp_path):
    src = _make_large_mixed_pdf(tmp_path, total=20, scanned=9)
    stub = _StubUocBackend(_make_uoc_not_installed())
    ctx = _compile_with_auto(tmp_path, src, stub)

    # UOC availability was probed but process() was never called because
    # the selector fell back to Tesseract.
    assert stub.process_calls == []
    m = ctx.manifest
    assert m is not None
    assert m.ocr_backend_selected == "tesseract"
    d = m.ocr_auto_decision
    assert d["preferred_backend"] == "unlimited_ocr"
    assert d["fallback_occurred"] is True
    assert d["fallback_reason"] == "model_not_installed"
    assert d["recommended_command"] == "aksharamd models install unlimited_ocr"
    # Both warnings appear.
    assert "AUTO_OCR_BACKEND_SELECTED" in m.warning_codes
    assert "AUTO_OCR_BACKEND_FALLBACK" in m.warning_codes


# ── 5. Receipt stale → falls back with the verify command ─────────────


def test_large_mixed_uoc_receipt_stale_falls_back(tmp_path):
    src = _make_large_mixed_pdf(tmp_path, total=20, scanned=9)
    stub = _StubUocBackend(_make_uoc_receipt_stale())
    ctx = _compile_with_auto(tmp_path, src, stub)

    m = ctx.manifest
    assert m is not None
    d = m.ocr_auto_decision
    assert d["fallback_occurred"] is True
    assert d["fallback_reason"] == "model_not_verified"
    assert d["recommended_command"] == "aksharamd models verify unlimited_ocr"


# ── 6. Hardware incompatible → fallback with no recommended command ───


def test_large_mixed_uoc_hardware_incompatible_falls_back(tmp_path):
    src = _make_large_mixed_pdf(tmp_path, total=20, scanned=9)
    stub = _StubUocBackend(_make_uoc_hardware_incompatible())
    ctx = _compile_with_auto(tmp_path, src, stub)

    m = ctx.manifest
    assert m is not None
    d = m.ocr_auto_decision
    assert d["fallback_occurred"] is True
    assert d["fallback_reason"] == "hardware_incompatible"
    assert d["recommended_command"] is None


# ── 7. Explicit unlimited_ocr with UOC unrunnable → hard-fail; no auto ──


def test_explicit_uoc_unavailable_hard_fails_via_cli(tmp_path, scanned_only_pdf):
    from click.testing import CliRunner

    from aksharamd.cli import main

    stub = _StubUocBackend(_make_uoc_not_installed())
    runner = CliRunner()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        result = runner.invoke(
            main,
            ["compile", str(scanned_only_pdf),
             "-o", str(tmp_path / "out"),
             "--ocr-backend", "unlimited_ocr"],
        )
    assert result.exit_code != 0
    # No manifest, no ocr_auto_decision — the CLI never entered compile.
    assert "aksharamd models install unlimited_ocr" in (result.output or "")
    # process() was never called.
    assert stub.process_calls == []


# ── 8. Explicit tesseract with UOC fully runnable → stays tesseract ────


def test_explicit_tesseract_when_uoc_runnable(tmp_path, digital_only_pdf):
    stub = _StubUocBackend(_make_uoc_runnable())
    out = tmp_path / "out"
    compiler = Compiler(output_dir=str(out), ocr_backend="tesseract")
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        ctx = compiler.compile(str(digital_only_pdf))
    # No auto behaviour whatsoever.
    assert stub.process_calls == []
    m = ctx.manifest
    assert m is not None
    assert m.ocr_backend_requested == "tesseract"
    assert m.ocr_backend_selected == "tesseract"
    assert m.ocr_auto_policy_version is None
    assert m.ocr_auto_decision is None
    assert "AUTO_OCR_BACKEND_SELECTED" not in m.warning_codes


# ── 9. Determinism — same inputs produce identical decision blobs ──────


def test_auto_decision_is_deterministic(tmp_path):
    src = _make_large_mixed_pdf(tmp_path, total=20, scanned=9)

    def _run(subdir: str) -> dict:
        stub = _StubUocBackend(_make_uoc_runnable())
        out = tmp_path / subdir
        compiler = Compiler(output_dir=str(out), ocr_backend="auto")
        with patch(
            "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
        ):
            ctx = compiler.compile(str(src))
        return dict(ctx.manifest.ocr_auto_decision)

    d1 = _run("run1")
    d2 = _run("run2")
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)


# ── 10. ocr_auto_policy_version == "1" for every auto run ──────────────


def test_auto_policy_version_populated_on_every_auto_run(tmp_path, digital_only_pdf):
    stub = _StubUocBackend(_make_uoc_runnable())
    ctx = _compile_with_auto(tmp_path, digital_only_pdf, stub)
    assert ctx.manifest.ocr_auto_policy_version == "1"


# ── 11. Fallback warnings do NOT reduce the readiness score ────────────


def test_fallback_warning_does_not_reduce_readiness(tmp_path):
    """Compile the same 20-page mixed PDF twice: once with UOC runnable
    (no fallback), once with UOC not installed (fallback fires). The
    readiness score must be identical because the two AUTO_* warnings
    are informational (max_penalty=0)."""
    src = _make_large_mixed_pdf(tmp_path, total=20, scanned=9)

    stub_ok = _StubUocBackend(_make_uoc_runnable(),
                              markdown="Same MD line for both runs.\n")
    stub_fb = _StubUocBackend(_make_uoc_not_installed())

    out1 = tmp_path / "no_fallback"
    compiler1 = Compiler(output_dir=str(out1), ocr_backend="auto")
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub_ok,
    ):
        ctx_ok = compiler1.compile(str(src))

    out2 = tmp_path / "fallback"
    compiler2 = Compiler(output_dir=str(out2), ocr_backend="auto")
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub_fb,
    ):
        ctx_fb = compiler2.compile(str(src))

    # The two compiles diverge on backend (UOC vs Tesseract), which
    # DOES affect content, so we can't compare readiness directly. But
    # we CAN assert that the AUTO_* codes themselves contribute zero
    # to deductions — i.e. they never appear in the deduction list.
    deduction_ids = {d["rule_id"] for d in ctx_fb.manifest.deductions}
    assert "AUTO_OCR_BACKEND_SELECTED" not in deduction_ids
    assert "AUTO_OCR_BACKEND_FALLBACK" not in deduction_ids
    # They appear as informational entries instead.
    info_ids = {d["rule_id"] for d in ctx_fb.manifest.informational}
    assert "AUTO_OCR_BACKEND_SELECTED" in info_ids
    assert "AUTO_OCR_BACKEND_FALLBACK" in info_ids
    # Zero penalty on both.
    for info in ctx_fb.manifest.informational:
        if info["rule_id"] in {"AUTO_OCR_BACKEND_SELECTED", "AUTO_OCR_BACKEND_FALLBACK"}:
            assert info["penalty"] == 0

    # The non-fallback run only shows SELECTED, still zero penalty.
    ok_info_ids = {d["rule_id"] for d in ctx_ok.manifest.informational}
    assert "AUTO_OCR_BACKEND_SELECTED" in ok_info_ids
    assert "AUTO_OCR_BACKEND_FALLBACK" not in ok_info_ids
