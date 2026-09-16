"""Safety guards for the smoke_b1a_7b synthetic test suite.

The user's B1a-7b.2a authorization requires that:

- No test attempts real network access.
- No test creates a real Windows firewall rule.
- No test invokes a real parser (marker / docling / markitdown /
  aksharamd compilation) or downloads a real model.
- No test accesses one of the three frozen smoke payloads
  (PMC5773191.1, the DocLayNet page hash, or FR 2025-19924).

These are enforced session-wide in this conftest. A test that
violates any of them causes the whole session to fail loudly rather
than silently touching resources.
"""
from __future__ import annotations

import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b import firewall as _fw


class RealResourceAccessError(RuntimeError):
    """A test attempted to access a real OS/network/parser resource
    that is prohibited by the B1a-7b.2a synthetic-only test policy."""


# ---------------------------------------------------------------------------
# Frozen smoke document identifiers that must not be touched.

FROZEN_SMOKE_PAYLOAD_IDS = frozenset({
    "PMC5773191.1",
    "3a504c7cb73621234b114c2c9a8fccbac7a8db7733a9f6ec1259d34bedb16a77",
    "2025-19924",
})


# ---------------------------------------------------------------------------
# Session-scoped monkeypatches.


@pytest.fixture(autouse=True, scope="function")
def _hard_block_real_network(monkeypatch):
    """Every test runs with the real network APIs replaced by raising
    stubs. If a test uses the FakeProbeBackend correctly, these
    stubs are never reached; if a test accidentally instantiates
    RealProbeBackend or bypasses the injection, the stubs fire."""

    def _block_socket_connect(*args, **kwargs):
        raise RealResourceAccessError(
            "socket.create_connection called from a synthetic test "
            "(use FakeProbeBackend)"
        )

    def _block_urllib_open(*args, **kwargs):
        raise RealResourceAccessError(
            "urllib.request.urlopen called from a synthetic test "
            "(use FakeProbeBackend)"
        )

    monkeypatch.setattr(socket, "create_connection", _block_socket_connect)
    monkeypatch.setattr(urllib.request, "urlopen", _block_urllib_open)

    # Also block RealProbeBackend instantiation as a belt-and-suspenders
    # measure. Tests explicitly need FakeProbeBackend; the real backend
    # class exists in the module but must not be instantiated here.
    real_backend_class = _fw.RealProbeBackend

    def _blocked_init(self, *args, **kwargs):
        raise RealResourceAccessError(
            "RealProbeBackend must not be instantiated in synthetic tests"
        )

    monkeypatch.setattr(real_backend_class, "__init__", _blocked_init)


@pytest.fixture(autouse=True, scope="function")
def _hard_block_powershell_firewall_calls(monkeypatch):
    """Block subprocess.Popen / subprocess.run whose argv starts with a
    powershell / netsh / New-NetFirewallRule shape. Tests inject a
    FakeFirewallBackend; the real firewall backend (when B1a-7b.2b
    ships it) will be OS-touching and must not run here."""

    original_run = subprocess.run
    original_popen = subprocess.Popen
    original_check_output = subprocess.check_output

    _FIREWALL_MARKERS = (
        "powershell", "pwsh", "netsh",
        "new-netfirewallrule", "get-netfirewallrule", "remove-netfirewallrule",
    )

    def _looks_like_firewall_call(args) -> bool:
        if isinstance(args, (list, tuple)):
            joined = " ".join(str(a) for a in args).lower()
        else:
            joined = str(args).lower()
        return any(m in joined for m in _FIREWALL_MARKERS)

    def _guard(fn):
        def wrapper(*args, **kwargs):
            first_arg = args[0] if args else kwargs.get("args", "")
            if _looks_like_firewall_call(first_arg):
                raise RealResourceAccessError(
                    f"real firewall/powershell subprocess blocked in synthetic test: "
                    f"{first_arg!r}"
                )
            return fn(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(subprocess, "run", _guard(original_run))
    monkeypatch.setattr(subprocess, "Popen", _guard(original_popen))
    monkeypatch.setattr(subprocess, "check_output", _guard(original_check_output))


@pytest.fixture(autouse=True, scope="function")
def _hard_block_real_parser_imports(monkeypatch):
    """Refuse to let synthetic tests import the actual parser packages.
    If a test needs a parser, it must use fake_parsers.SyntheticAdapter.
    """

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__
    forbidden_top = {
        "marker",
        "marker_pdf",
        "docling",
        "markitdown",
    }
    # aksharamd is legitimately imported (adjudication lives there);
    # we do NOT forbid aksharamd itself. We only forbid the
    # aksharamd compiler entry point _compile_pdf_bytes being called
    # from synthetic tests — that's enforced at the SmokeRunner level
    # via the injected adapter, not the import level.

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        top = name.split(".", 1)[0]
        if top in forbidden_top:
            raise RealResourceAccessError(
                f"import of real parser package {name!r} is forbidden in "
                f"synthetic tests (use fake_parsers.SyntheticAdapter)"
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setitem(__builtins__ if isinstance(__builtins__, dict) else vars(__builtins__),
                        "__import__", _guarded_import)


@pytest.fixture(autouse=True, scope="function")
def _hard_block_frozen_smoke_payload_reads(monkeypatch, request):
    """Guard against tests reading one of the three frozen smoke
    payload files. The guard fires on Path.read_bytes / read_text /
    open when the path contains one of the frozen canonical IDs.
    """

    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    original_open = Path.open

    def _guarded_read_bytes(self):
        _check_path(self)
        return original_read_bytes(self)

    def _guarded_read_text(self, *args, **kwargs):
        _check_path(self)
        return original_read_text(self, *args, **kwargs)

    def _guarded_open(self, *args, **kwargs):
        _check_path(self)
        return original_open(self, *args, **kwargs)

    def _check_path(path):
        s = str(path)
        for fid in FROZEN_SMOKE_PAYLOAD_IDS:
            if fid in s:
                raise RealResourceAccessError(
                    f"synthetic test attempted to read a frozen smoke "
                    f"payload path: {path}"
                )

    monkeypatch.setattr(Path, "read_bytes", _guarded_read_bytes)
    monkeypatch.setattr(Path, "read_text", _guarded_read_text)
    monkeypatch.setattr(Path, "open", _guarded_open)


# ---------------------------------------------------------------------------
# Shared fixture: tmp run dir.


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "smoke_run"
    d.mkdir()
    return d
