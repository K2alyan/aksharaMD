"""No-accidental-real-execution boundary.

Verifies that:
- Importing the smoke package has no side effects (no parser call,
  no firewall rule, no network access).
- The CLI entry point refuses to run without the authorization
  sentinel.
- The conftest guards actually fire when a test tries to touch a
  real network / firewall / parser package / frozen smoke payload.
"""
from __future__ import annotations

import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest


def test_importing_smoke_package_has_no_side_effects() -> None:
    """The package's __init__ exports version constants only. This
    import should not (a) instantiate RealProbeBackend, (b) call any
    firewall backend, (c) open a socket, (d) call subprocess. If it
    did, the conftest guards would fire during collection."""
    import benchmarks.eval_v1.smoke_b1a_7b as pkg
    assert pkg.SMOKE_SPEC_VERSION == "v1"
    assert pkg.PARSER_EXECUTION_CONTRACT_VERSION == "v1"
    assert pkg.NORMALIZATION_VERSION == "2"


def test_entrypoint_refuses_without_authorization_token(monkeypatch) -> None:
    from benchmarks.eval_v1.smoke_b1a_7b.entrypoint import (
        AUTHORIZATION_ENV_VAR,
        UnauthorizedInvocationError,
        _check_authorization,
    )
    monkeypatch.delenv(AUTHORIZATION_ENV_VAR, raising=False)
    with pytest.raises(UnauthorizedInvocationError, match="AKSHARAMD_SMOKE_AUTHORIZED"):
        _check_authorization()


def test_entrypoint_refuses_wrong_token(monkeypatch) -> None:
    from benchmarks.eval_v1.smoke_b1a_7b.entrypoint import (
        AUTHORIZATION_ENV_VAR,
        UnauthorizedInvocationError,
        _check_authorization,
    )
    monkeypatch.setenv(AUTHORIZATION_ENV_VAR, "not-the-token")
    with pytest.raises(UnauthorizedInvocationError, match="separate authorization"):
        _check_authorization()


def test_guard_blocks_real_socket_connect() -> None:
    """socket.create_connection should be replaced by a raising stub."""
    from tests.smoke_b1a_7b.conftest import RealResourceAccessError
    with pytest.raises(RealResourceAccessError, match="socket.create_connection"):
        socket.create_connection(("1.1.1.1", 443), timeout=1.0)


def test_guard_blocks_real_urlopen() -> None:
    from tests.smoke_b1a_7b.conftest import RealResourceAccessError
    with pytest.raises(RealResourceAccessError, match="urllib.request.urlopen"):
        urllib.request.urlopen("https://1.1.1.1/", timeout=1.0)


def test_guard_blocks_real_firewall_subprocess() -> None:
    from tests.smoke_b1a_7b.conftest import RealResourceAccessError
    with pytest.raises(RealResourceAccessError, match="firewall"):
        subprocess.run(
            ["powershell", "-Command", "New-NetFirewallRule -DisplayName x"],
            capture_output=True,
        )


def test_guard_blocks_real_parser_import() -> None:
    from tests.smoke_b1a_7b.conftest import RealResourceAccessError
    with pytest.raises(RealResourceAccessError, match="real parser package"):
        __import__("marker")


def test_guard_blocks_frozen_smoke_payload_read(tmp_path: Path) -> None:
    from tests.smoke_b1a_7b.conftest import RealResourceAccessError
    # The guard fires on the path check before any real filesystem
    # access; the file itself does not need to exist.
    frozen_id = "PMC5773191.1"
    p = tmp_path / f"{frozen_id}.pdf"
    with pytest.raises(RealResourceAccessError, match="frozen smoke"):
        p.read_bytes()


def test_guard_blocks_frozen_smoke_payload_write(tmp_path: Path) -> None:
    """Writing to a frozen-payload path is also blocked. This proves
    the harness cannot even *stage* a real smoke payload accidentally."""
    from tests.smoke_b1a_7b.conftest import RealResourceAccessError
    for frozen_id in ("PMC5773191.1",
                      "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77",
                      "2025-19924"):
        p = tmp_path / f"{frozen_id}.pdf"
        with pytest.raises(RealResourceAccessError, match="frozen smoke"):
            p.write_bytes(b"synthetic")


def test_real_probe_backend_cannot_be_instantiated() -> None:
    from benchmarks.eval_v1.smoke_b1a_7b.firewall import RealProbeBackend
    from tests.smoke_b1a_7b.conftest import RealResourceAccessError
    with pytest.raises(RealResourceAccessError, match="RealProbeBackend"):
        RealProbeBackend()
