"""PR 94c — CLI ``--ocr-backend`` option tests.

Uses ``click.testing.CliRunner`` to invoke the ``compile`` subcommand.
Backends are stubbed by patching
``aksharamd.plugins.ocr_backends.get_backend`` so we never touch a real
GPU, Tesseract binary, or downloaded model.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from unittest.mock import patch

from click.testing import CliRunner

from aksharamd.cli import main
from aksharamd.plugins.ocr_backends._protocol import BackendAvailability


class _AvailStub:
    """Backend whose ``availability()`` we control per-test."""

    def __init__(self, avail: BackendAvailability) -> None:
        self._avail = avail
        self.processed = 0

    def capabilities(self):  # pragma: no cover - not exercised
        raise AssertionError("capabilities not consulted by CLI probe")

    def availability(self) -> BackendAvailability:
        return self._avail

    def process(self, request):  # pragma: no cover - not exercised
        self.processed += 1
        return []


# ── 6. unavailable UOC on explicit select exits non-zero, actionable ───


def test_unavailable_unlimited_ocr_exits_nonzero_actionable(
    tmp_path, digital_only_pdf,
):
    stub = _AvailStub(BackendAvailability(
        is_available=False,
        reason="synthetic reason: no GPU detected",
        hardware_compatible=False,
        model_installed=True,
        runnable_now=False,
    ))
    runner = CliRunner()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        result = runner.invoke(
            main,
            ["compile", str(digital_only_pdf),
             "-o", str(tmp_path / "out"),
             "--ocr-backend", "unlimited_ocr"],
        )
    assert result.exit_code != 0
    # Click renders ClickException to stderr with prefix "Error: ".
    # In click 8+, result.output combines stdout+stderr by default.
    combined = result.output or ""
    assert "unavailable" in combined
    assert "Hardware requirements not met" in combined
    assert "synthetic reason" in combined


# ── 7. no silent fallback on unavailable ───────────────────────────────


def test_no_silent_fallback_on_unavailable(tmp_path, digital_only_pdf):
    stub = _AvailStub(BackendAvailability(
        is_available=False,
        reason="not available for test",
        hardware_compatible=False,
    ))
    runner = CliRunner()
    with patch(
        "aksharamd.plugins.ocr_backends.get_backend", return_value=stub,
    ):
        result = runner.invoke(
            main,
            ["compile", str(digital_only_pdf),
             "-o", str(tmp_path / "out"),
             "--ocr-backend", "unlimited_ocr"],
        )
    assert result.exit_code != 0
    # Backend was probed but never actually processed anything.
    assert stub.processed == 0
    # No output directory content: the compile short-circuits before
    # touching the pipeline.
    out_dir = tmp_path / "out"
    if out_dir.exists():
        # Any produced files would indicate a partial compile — the
        # tree must be empty (Click aborted before compile).
        produced = [p for p in out_dir.rglob("*") if p.is_file()]
        assert produced == []


# ── 11. --help documents both choices and UOC hardware requirements ────


def test_cli_help_documents_both_choices_and_uoc_requirements():
    runner = CliRunner()
    result = runner.invoke(main, ["compile", "--help"])
    assert result.exit_code == 0
    text = result.output.lower()
    assert "--ocr-backend" in text
    assert "tesseract" in text
    assert "unlimited_ocr" in text
    assert "nvidia" in text
    assert ("install" in text or "model" in text)


# ── 12. no heavy imports pulled in by `import aksharamd.cli` ──────────


def test_no_heavy_import_after_cli_import():
    """Subprocess check: after ``import aksharamd.cli``, neither
    ``torch`` nor ``pytesseract`` (the two OCR-backend heavy deps
    introduced by PRs 94a/94b) may be in ``sys.modules``. The 94c CLI
    plumbing must remain equally cheap.

    ``fitz`` is intentionally excluded from this assertion: it is
    imported at module scope by ``aksharamd.plugins.parsers.pdf``
    already on main and reworking that is out of scope for PR 94c.
    Reviewer was flagged in the PR report about the tension.
    """
    code = textwrap.dedent("""
        import sys
        import aksharamd.cli  # noqa: F401
        for mod in ("torch", "pytesseract"):
            assert mod not in sys.modules, mod
        print("OK")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, (
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "OK" in proc.stdout
