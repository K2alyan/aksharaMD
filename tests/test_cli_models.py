"""Tests for the ``aksharamd models`` command group (PR 98).

Uses ``click.testing.CliRunner``. The lifecycle module is patched so
no real download / hash / GPU happens.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from unittest.mock import patch

from click.testing import CliRunner

from aksharamd.cli import main
from aksharamd.plugins.ocr_backends.unlimited_ocr.models import (
    EXIT_DOWNLOAD_FAILURE,
    EXIT_HARDWARE_INCOMPATIBLE,
    EXIT_OK,
    EXIT_VERIFICATION_FAILURE,
    InstallOutcome,
    ModelInfo,
    ModelStatus,
    RemoveOutcome,
    VerifyOutcome,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


REPO = "baidu/Unlimited-OCR"
REVISION = "d549bb9d6a055dbe291408916d66acc2cd5920f6"


def _mk_status(**overrides) -> ModelStatus:
    base = ModelStatus(
        name="unlimited_ocr",
        repo_id=REPO,
        revision=REVISION,
        download_size_bytes=6_700_000_000,
        download_size_source="manifest",
        snapshot_present=False,
        manifest_present=True,
        byte_verified=False,
        hardware_compatible=True,
        runnable_now=False,
        snapshot_path=None,
        receipt_path=None,
        reason="",
        availability_details={},
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _mk_info() -> ModelInfo:
    return ModelInfo(
        name="unlimited_ocr",
        repo_id=REPO,
        revision=REVISION,
        download_size_bytes=6_700_000_000,
        download_size_source="manifest",
        license_notice="Baidu Unlimited-OCR license",
        snapshot_path=None,
    )


# ── status --json ─────────────────────────────────────────────────────────


def test_status_json_schema_is_deterministic():
    fake_status = _mk_status()
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_status",
        return_value=fake_status,
    ):
        r = CliRunner().invoke(main, ["models", "status", "unlimited_ocr", "--json"])
    assert r.exit_code == 0, r.output
    # No ANSI markup in JSON output.
    assert "\x1b[" not in r.output
    payload = json.loads(r.output)
    # Stable top-level keys.
    expected_keys = {
        "availability_details",
        "byte_verified",
        "download_size_bytes",
        "download_size_source",
        "hardware_compatible",
        "manifest_present",
        "name",
        "reason",
        "receipt_path",
        "repo_id",
        "revision",
        "runnable_now",
        "snapshot_path",
        "snapshot_present",
    }
    assert set(payload.keys()) == expected_keys
    assert payload["name"] == "unlimited_ocr"
    assert payload["repo_id"] == REPO
    assert payload["revision"] == REVISION


def test_status_rich_output_no_ansi_needed_but_has_labels():
    fake_status = _mk_status(snapshot_present=True, byte_verified=True)
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_status",
        return_value=fake_status,
    ):
        r = CliRunner().invoke(main, ["models", "status", "unlimited_ocr"])
    assert r.exit_code == 0
    assert "Snapshot present" in r.output
    assert "Byte verified" in r.output


# ── invalid model name → exit 2 ───────────────────────────────────────────


def test_invalid_model_name_exit_2():
    r = CliRunner().invoke(main, ["models", "status", "made_up_model"])
    assert r.exit_code == 2
    assert "unknown model" in r.output.lower()


def test_invalid_model_name_on_install_exit_2():
    r = CliRunner().invoke(main, ["models", "install", "made_up_model", "--yes"])
    assert r.exit_code == 2


def test_invalid_model_name_on_remove_exit_2():
    r = CliRunner().invoke(main, ["models", "remove", "made_up_model", "--yes"])
    assert r.exit_code == 2


# ── install prompt / --yes ────────────────────────────────────────────────


def test_install_without_yes_prompts_and_aborts_on_no():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ):
        r = CliRunner().invoke(
            main, ["models", "install", "unlimited_ocr"], input="n\n",
        )
    # Should exit non-zero (user aborted).
    assert r.exit_code != 0


def test_install_with_yes_skips_prompt_and_reports_ok():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ), patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.install_model",
        return_value=InstallOutcome(
            status="ok",
            note="installed successfully",
            exit_code=EXIT_OK,
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "install", "unlimited_ocr", "--yes"],
        )
    assert r.exit_code == 0, r.output


# ── install failure paths surface correct exit codes ─────────────────────


def test_install_hardware_incompatible_exit_3():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ), patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.install_model",
        return_value=InstallOutcome(
            status="hardware_incompatible",
            note="no GPU",
            exit_code=EXIT_HARDWARE_INCOMPATIBLE,
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "install", "unlimited_ocr", "--yes"],
        )
    assert r.exit_code == EXIT_HARDWARE_INCOMPATIBLE


def test_install_download_failure_exit_5():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ), patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.install_model",
        return_value=InstallOutcome(
            status="download_failure",
            note="network borked",
            exit_code=EXIT_DOWNLOAD_FAILURE,
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "install", "unlimited_ocr", "--yes"],
        )
    assert r.exit_code == EXIT_DOWNLOAD_FAILURE


def test_install_verification_failure_exit_6():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ), patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.install_model",
        return_value=InstallOutcome(
            status="verification_failure",
            note="hash mismatch",
            exit_code=EXIT_VERIFICATION_FAILURE,
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "install", "unlimited_ocr", "--yes"],
        )
    assert r.exit_code == EXIT_VERIFICATION_FAILURE


# ── remove prompt / --yes ─────────────────────────────────────────────────


def test_remove_without_yes_prompts_and_aborts_on_no():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ):
        r = CliRunner().invoke(
            main, ["models", "remove", "unlimited_ocr"], input="n\n",
        )
    assert r.exit_code != 0


def test_remove_with_yes_proceeds():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ), patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.remove_model",
        return_value=RemoveOutcome(
            status="ok",
            note="removed",
            exit_code=EXIT_OK,
            bytes_recovered=6_700_000_000,
            snapshot_removed=True,
            blobs_removed=3,
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "remove", "unlimited_ocr", "--yes"],
        )
    assert r.exit_code == EXIT_OK, r.output


def test_remove_absent_returns_zero():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.get_model_info",
        return_value=_mk_info(),
    ), patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.remove_model",
        return_value=RemoveOutcome(
            status="already_absent",
            note="nothing to remove",
            exit_code=EXIT_OK,
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "remove", "unlimited_ocr", "--yes"],
        )
    assert r.exit_code == EXIT_OK


# ── verify --json ─────────────────────────────────────────────────────────


def test_verify_json_output():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.verify_model",
        return_value=VerifyOutcome(
            ok=True,
            note="ok",
            exit_code=EXIT_OK,
            files_hashed=["a.py", "b.json"],
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "verify", "unlimited_ocr", "--json"],
        )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["ok"] is True
    assert payload["exit_code"] == 0


def test_verify_failure_exit_6():
    with patch(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models.verify_model",
        return_value=VerifyOutcome(
            ok=False,
            note="hash mismatch on config.json",
            exit_code=EXIT_VERIFICATION_FAILURE,
        ),
    ):
        r = CliRunner().invoke(
            main, ["models", "verify", "unlimited_ocr"],
        )
    assert r.exit_code == EXIT_VERIFICATION_FAILURE


# ── --help stays cheap (no torch) ────────────────────────────────────────


def test_models_help_does_not_import_torch():
    code = textwrap.dedent("""
        import sys
        from click.testing import CliRunner
        from aksharamd.cli import main
        r = CliRunner().invoke(main, ["models", "--help"])
        assert r.exit_code == 0, r.output
        assert "torch" not in sys.modules, "torch was imported by models --help"
        r2 = CliRunner().invoke(main, ["models", "status", "--help"])
        assert r2.exit_code == 0, r2.output
        assert "torch" not in sys.modules, "torch was imported by models status --help"
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


# ── doctor's availability details reflect install/remove state ───────────


def test_doctor_reflects_install_state(monkeypatch):
    """After install returns success, doctor's ocr_backends section
    should report model_snapshot_verified=True for unlimited_ocr.

    The actual state transition is exercised by the lifecycle tests;
    here we just verify the doctor integration surface reads the same
    signal (via BackendAvailabilityDetails).
    """
    from aksharamd.plugins.ocr_backends._protocol import (
        BackendAvailability,
        BackendAvailabilityDetails,
    )

    class _Stub:
        def capabilities(self):
            from aksharamd.plugins.ocr_backends._protocol import BackendCapabilities
            return BackendCapabilities(
                supports_layout=True,
                supports_math=True,
                supports_tables=True,
                emits="markdown",
            )

        def availability(self):
            return BackendAvailability(
                is_available=True,
                reason="",
                hardware_compatible=True,
                model_installed=True,
                runnable_now=True,
                details=BackendAvailabilityDetails(
                    device_name="fake",
                    vram_mib_total=16000,
                    min_vram_mib=7000,
                    bf16_supported=True,
                    model_snapshot_present=True,
                    model_snapshot_verified=True,
                ),
            )

    def _get(name):
        return _Stub()

    with patch(
        "aksharamd.plugins.ocr_backends.get_backend",
        side_effect=_get,
    ):
        r = CliRunner().invoke(main, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    uoc = payload["ocr_backends"]["unlimited_ocr"]
    details = uoc["availability"]["details"]
    assert details["model_snapshot_verified"] is True
    assert details["model_snapshot_present"] is True


def test_doctor_reflects_remove_state(monkeypatch):
    """After remove, the same details should report False."""
    from aksharamd.plugins.ocr_backends._protocol import (
        BackendAvailability,
        BackendAvailabilityDetails,
    )

    class _Stub:
        def capabilities(self):
            from aksharamd.plugins.ocr_backends._protocol import BackendCapabilities
            return BackendCapabilities(
                supports_layout=True,
                supports_math=True,
                supports_tables=True,
                emits="markdown",
            )

        def availability(self):
            return BackendAvailability(
                is_available=False,
                reason="snapshot missing",
                hardware_compatible=True,
                model_installed=False,
                runnable_now=False,
                details=BackendAvailabilityDetails(
                    device_name="fake",
                    vram_mib_total=16000,
                    min_vram_mib=7000,
                    bf16_supported=True,
                    model_snapshot_present=False,
                    model_snapshot_verified=False,
                ),
            )

    def _get(name):
        return _Stub()

    with patch(
        "aksharamd.plugins.ocr_backends.get_backend",
        side_effect=_get,
    ):
        r = CliRunner().invoke(main, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    uoc = payload["ocr_backends"]["unlimited_ocr"]
    details = uoc["availability"]["details"]
    assert details["model_snapshot_verified"] is False
    assert details["model_snapshot_present"] is False
