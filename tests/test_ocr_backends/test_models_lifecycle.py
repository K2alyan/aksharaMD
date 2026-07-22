"""Tests for the Unlimited-OCR lifecycle module (PR 98).

These tests exercise the lifecycle module directly (no CLI). They
stub the network / hardware / disk layers so no real download or
GPU probe happens.
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from aksharamd.plugins.ocr_backends.unlimited_ocr.models import (
    EXIT_DOWNLOAD_FAILURE,
    EXIT_HARDWARE_INCOMPATIBLE,
    EXIT_INSUFFICIENT_DISK,
    EXIT_OK,
    EXIT_OPERATION_FAILURE,
    EXIT_VERIFICATION_FAILURE,
    get_model_info,
    get_model_status,
    install_model,
    remove_model,
    verify_model,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


REPO = "baidu/Unlimited-OCR"
REVISION = "d549bb9d6a055dbe291408916d66acc2cd5920f6"
REPO_SLUG = REPO.replace("/", "--")


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch) -> Path:
    """Route HF cache to a per-test temp directory. Every lifecycle
    call reads HF_HOME through :func:`lifecycle._hf_cache_root`.
    """
    cache = tmp_path / "hf_home"
    cache.mkdir()
    monkeypatch.setenv("HF_HOME", str(cache))
    return cache


class _ManifestBundle:
    """Bundles the manifest path with its content dict so tests can
    read both without stuffing attrs on a PathLib object (which
    Windows Path rejects)."""

    def __init__(self, path: Path, files: dict, manifest: dict) -> None:
        self.path = path
        self.files = files
        self.manifest = manifest


@pytest.fixture
def manifest_fixture(tmp_path: Path, monkeypatch) -> _ManifestBundle:
    """Write a small manifest to disk and patch
    ``UNLIMITED_OCR_TRUSTED_MANIFEST_PATH`` + the adapter's
    ``load_trusted_manifest`` to consume it. The manifest matches
    the shape verification_receipt.py expects.
    """
    files = {
        "modeling.py": b"# fake code\n",
        "config.json": b'{"ok": true}\n',
        "weights.safetensors": b"\x00" * 8192,
    }
    import hashlib

    def _sha(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

    manifest = {
        "manifest_schema_version": 1,
        "manifest_id": "test-manifest-98-v1",
        "repo_id": REPO,
        "revision": REVISION,
        "generator": "test",
        "generator_version": "1.0",
        "files": {
            "modeling.py": {
                "sha256": _sha(files["modeling.py"]),
                "size_bytes": len(files["modeling.py"]),
                "class": "executable",
                "required_for_runtime": True,
                "verify_on_every_load": True,
            },
            "config.json": {
                "sha256": _sha(files["config.json"]),
                "size_bytes": len(files["config.json"]),
                "class": "config-sensitive",
                "required_for_runtime": True,
                "verify_on_every_load": True,
            },
            "weights.safetensors": {
                "sha256": _sha(files["weights.safetensors"]),
                "size_bytes": len(files["weights.safetensors"]),
                "class": "weights",
                "required_for_runtime": True,
                "verify_on_every_load": False,
            },
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Patch the module-level constant used by lifecycle + verification_receipt.
    monkeypatch.setattr(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.models."
        "UNLIMITED_OCR_TRUSTED_MANIFEST_PATH",
        manifest_path,
    )

    def _fake_load_trusted_manifest(path=None):
        return manifest

    monkeypatch.setattr(
        "aksharamd.plugins.ocr_backends.unlimited_ocr.adapter."
        "load_trusted_manifest",
        _fake_load_trusted_manifest,
    )
    return _ManifestBundle(manifest_path, files, manifest)


@pytest.fixture(autouse=True)
def hardware_ok(monkeypatch):
    """Force the hardware preflight to say OK unless a test overrides."""

    class _FakeBackend:
        def availability(self):
            from aksharamd.plugins.ocr_backends._protocol import (
                BackendAvailability,
                BackendAvailabilityDetails,
            )
            return BackendAvailability(
                is_available=False,  # not installed, but hardware ok
                reason="",
                hardware_compatible=True,
                model_installed=False,
                runnable_now=False,
                details=BackendAvailabilityDetails(
                    device_name="Fake GPU",
                    vram_mib_total=16384,
                    min_vram_mib=7000,
                    bf16_supported=True,
                ),
            )

    # Patch the import target inside lifecycle. The functions do a
    # lazy ``from ..unlimited_ocr_backend import UnlimitedOcrBackend``
    # so we patch the module attribute after import.
    import aksharamd.plugins.ocr_backends.unlimited_ocr_backend as ub
    monkeypatch.setattr(ub, "UnlimitedOcrBackend", lambda: _FakeBackend())
    yield


def _lay_out_snapshot(cache_root: Path, files: dict[str, bytes]) -> Path:
    snap = cache_root / f"models--{REPO_SLUG}" / "snapshots" / REVISION
    snap.mkdir(parents=True)
    for rel, content in files.items():
        p = snap / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return snap


# ── get_model_status ──────────────────────────────────────────────────────


def test_status_when_absent(isolated_cache, manifest_fixture):
    status = get_model_status()
    assert status.snapshot_present is False
    assert status.byte_verified is False
    assert status.manifest_present is True
    assert status.repo_id == REPO
    assert status.revision == REVISION
    # download_size_source reflects the manifest fixture.
    assert status.download_size_source == "manifest"
    assert status.download_size_bytes is not None and status.download_size_bytes > 0


def test_status_when_snapshot_present_but_not_verified(
    isolated_cache, manifest_fixture,
):
    files = manifest_fixture.files
    _lay_out_snapshot(isolated_cache, files)
    status = get_model_status()
    assert status.snapshot_present is True
    assert status.byte_verified is False


def test_status_when_verified(isolated_cache, manifest_fixture):
    files = manifest_fixture.files
    _lay_out_snapshot(isolated_cache, files)
    from aksharamd.plugins.ocr_backends.verification_receipt import (
        full_verify_and_write_receipt,
    )
    manifest = manifest_fixture.manifest
    out = full_verify_and_write_receipt(manifest, manifest_fixture.path, isolated_cache)
    assert out.ok, out.note
    status = get_model_status()
    assert status.snapshot_present is True
    assert status.byte_verified is True


def test_status_when_corrupt(isolated_cache, manifest_fixture):
    files = manifest_fixture.files
    snap = _lay_out_snapshot(isolated_cache, files)
    # Mutate one file so hashes won't match — verify then status.
    (snap / "config.json").write_bytes(b'{"tampered": true}')
    # No receipt written → byte_verified is False.
    status = get_model_status()
    assert status.snapshot_present is True
    assert status.byte_verified is False


# ── install_model ─────────────────────────────────────────────────────────


def _fake_snapshot_download_factory(files: dict[str, bytes], *, fail=False):
    """Return a callable that mimics huggingface_hub.snapshot_download.

    When ``fail`` is True the callable raises after writing partial
    contents into the local_dir — used to test cleanup on abort.
    """
    def _fake(**kwargs):
        rev = kwargs["revision"]
        assert rev == REVISION, f"revision must be pinned, got {rev!r}"
        assert kwargs["repo_id"] == REPO
        assert "trust_remote_code" not in kwargs
        local_dir = Path(kwargs["local_dir"])
        local_dir.mkdir(parents=True, exist_ok=True)
        # Write the first file always.
        for i, (rel, content) in enumerate(files.items()):
            (local_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (local_dir / rel).write_bytes(content)
            if fail and i == 0:
                raise RuntimeError(
                    "network interrupted mid-download at file "
                    f"{rel} (revision={rev} token=hf_ABCDEF1234)"
                )
        return str(local_dir)
    return _fake


def test_install_pinned_revision_passed_to_snapshot_download(
    isolated_cache, manifest_fixture,
):
    files = manifest_fixture.files
    fake = _fake_snapshot_download_factory(files)
    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(dict(kwargs))
        return fake(**kwargs)

    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = spy  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        outcome = install_model(assume_yes=True)

    assert outcome.status == "ok", outcome.note
    assert outcome.exit_code == EXIT_OK
    assert calls[0]["revision"] == REVISION
    assert calls[0]["repo_id"] == REPO


def test_install_promotes_atomically_from_staging(
    isolated_cache, manifest_fixture,
):
    files = manifest_fixture.files
    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = _fake_snapshot_download_factory(files)  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        outcome = install_model(assume_yes=True)
    assert outcome.status == "ok"
    final_snap = (
        isolated_cache / f"models--{REPO_SLUG}" / "snapshots" / REVISION
    )
    assert final_snap.is_dir()
    for rel in files:
        assert (final_snap / rel).exists()
    # Staging directory has been consumed.
    staging = (
        isolated_cache / f"models--{REPO_SLUG}" / "snapshots"
        / f"aksharamd_staging_{REVISION}"
    )
    assert not staging.exists()


def test_install_interrupted_download_leaves_no_partial_snapshot(
    isolated_cache, manifest_fixture,
):
    files = manifest_fixture.files
    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = _fake_snapshot_download_factory(files, fail=True)  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        outcome = install_model(assume_yes=True)
    assert outcome.status == "download_failure"
    assert outcome.exit_code == EXIT_DOWNLOAD_FAILURE
    # Sanitizer must have stripped the fake token from the error text.
    assert "hf_ABCDEF" not in outcome.note
    # No final snapshot, no leftover staging.
    final_snap = (
        isolated_cache / f"models--{REPO_SLUG}" / "snapshots" / REVISION
    )
    assert not final_snap.exists()
    staging = (
        isolated_cache / f"models--{REPO_SLUG}" / "snapshots"
        / f"aksharamd_staging_{REVISION}"
    )
    assert not staging.exists()


def test_install_hash_mismatch_reverts(isolated_cache, manifest_fixture):
    """Snapshot downloads bytes that do not match the manifest → install
    fails, snapshot at final destination is removed."""
    files = manifest_fixture.files
    tampered = dict(files)
    tampered["config.json"] = b'{"tampered": true}'  # different bytes → hash mismatch
    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = _fake_snapshot_download_factory(tampered)  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        outcome = install_model(assume_yes=True)
    assert outcome.status == "verification_failure"
    assert outcome.exit_code == EXIT_VERIFICATION_FAILURE
    final_snap = (
        isolated_cache / f"models--{REPO_SLUG}" / "snapshots" / REVISION
    )
    assert not final_snap.exists()


def test_install_hardware_incompatible(isolated_cache, manifest_fixture, monkeypatch):
    """Backend reports hw incompatible → install refuses to download."""

    class _BadHW:
        def availability(self):
            from aksharamd.plugins.ocr_backends._protocol import BackendAvailability
            return BackendAvailability(
                is_available=False,
                reason="no CUDA on this machine",
                hardware_compatible=False,
                model_installed=False,
                runnable_now=False,
            )
    import aksharamd.plugins.ocr_backends.unlimited_ocr_backend as ub
    monkeypatch.setattr(ub, "UnlimitedOcrBackend", lambda: _BadHW())

    fake_hf = types.ModuleType("huggingface_hub")

    def _forbid(**kwargs):
        raise AssertionError("snapshot_download must not be called")
    fake_hf.snapshot_download = _forbid  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        outcome = install_model(assume_yes=True)
    assert outcome.status == "hardware_incompatible"
    assert outcome.exit_code == EXIT_HARDWARE_INCOMPATIBLE


def test_install_insufficient_disk(isolated_cache, manifest_fixture, monkeypatch):
    """Disk usage says free < required → install refuses."""

    class _Usage:
        def __init__(self, free):
            self.free = free
            self.total = free * 2
            self.used = free

    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _p: _Usage(free=1024),
    )
    fake_hf = types.ModuleType("huggingface_hub")

    def _forbid(**kwargs):
        raise AssertionError("snapshot_download must not be called")
    fake_hf.snapshot_download = _forbid  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        outcome = install_model(assume_yes=True)
    assert outcome.status == "insufficient_disk"
    assert outcome.exit_code == EXIT_INSUFFICIENT_DISK


def test_install_already_installed_short_circuits(
    isolated_cache, manifest_fixture,
):
    """Existing verified snapshot: install returns already_installed
    without calling snapshot_download."""
    files = manifest_fixture.files
    _lay_out_snapshot(isolated_cache, files)
    fake_hf = types.ModuleType("huggingface_hub")

    def _forbid(**kwargs):
        raise AssertionError("snapshot_download must not be called")
    fake_hf.snapshot_download = _forbid  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        outcome = install_model(assume_yes=True)
    assert outcome.status == "already_installed"
    assert outcome.exit_code == EXIT_OK


# ── verify_model ──────────────────────────────────────────────────────────


def test_verify_does_not_call_snapshot_download(
    isolated_cache, manifest_fixture,
):
    files = manifest_fixture.files
    _lay_out_snapshot(isolated_cache, files)
    fake_hf = types.ModuleType("huggingface_hub")

    def _forbid(**_kwargs):
        raise AssertionError("verify must not call snapshot_download")
    fake_hf.snapshot_download = _forbid  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
        out = verify_model()
    assert out.ok, out.note
    assert out.exit_code == EXIT_OK


def test_verify_on_missing_snapshot_fails(
    isolated_cache, manifest_fixture,
):
    out = verify_model()
    assert not out.ok
    assert out.exit_code == EXIT_VERIFICATION_FAILURE


# ── remove_model ──────────────────────────────────────────────────────────


def test_remove_deletes_pinned_snapshot(isolated_cache, manifest_fixture):
    files = manifest_fixture.files
    snap = _lay_out_snapshot(isolated_cache, files)
    # Add an unrelated other-repo snapshot; must remain untouched.
    other = isolated_cache / "models--somebody--other" / "snapshots" / "abc"
    other.mkdir(parents=True)
    (other / "config.json").write_text('{"other": true}', encoding="utf-8")
    outcome = remove_model()
    assert outcome.status == "ok"
    assert outcome.exit_code == EXIT_OK
    assert not snap.exists()
    # Unrelated snapshot survives.
    assert (other / "config.json").exists()


def test_remove_on_absent_snapshot_returns_ok(
    isolated_cache, manifest_fixture,
):
    outcome = remove_model()
    assert outcome.status == "already_absent"
    assert outcome.exit_code == EXIT_OK


def test_remove_repeated_idempotent(isolated_cache, manifest_fixture):
    files = manifest_fixture.files
    _lay_out_snapshot(isolated_cache, files)
    a = remove_model()
    b = remove_model()
    assert a.exit_code == EXIT_OK
    assert b.exit_code == EXIT_OK
    assert b.status == "already_absent"


# ── concurrent lock behaviour ─────────────────────────────────────────────


def test_concurrent_install_lock_second_blocks(
    isolated_cache, manifest_fixture,
):
    """Two threads try to install simultaneously; the second sees the
    lock and returns an ``operation_failure`` (LockHeld path)."""
    files = manifest_fixture.files

    hold_event = threading.Event()
    release_event = threading.Event()

    def _blocking_download(**kwargs):
        # Simulate a long-running download: signal the outer test and
        # then wait until it releases us.
        hold_event.set()
        release_event.wait(timeout=5)
        # Perform a normal download after being released.
        return _fake_snapshot_download_factory(files)(**kwargs)

    fake_hf_slow = types.ModuleType("huggingface_hub")
    fake_hf_slow.snapshot_download = _blocking_download  # type: ignore[attr-defined]

    results: dict[str, object] = {}

    def _first():
        with patch.dict(sys.modules, {"huggingface_hub": fake_hf_slow}):
            results["first"] = install_model(assume_yes=True)

    t1 = threading.Thread(target=_first)
    t1.start()
    assert hold_event.wait(timeout=5), "first install never entered download"

    # Second attempt with a fake hf that would raise if reached.
    fake_hf_fast = types.ModuleType("huggingface_hub")

    def _forbid(**kwargs):
        raise AssertionError("second install must not download")
    fake_hf_fast.snapshot_download = _forbid  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"huggingface_hub": fake_hf_fast}):
        second = install_model(assume_yes=True)

    assert second.status == "operation_failure"
    assert second.exit_code == EXIT_OPERATION_FAILURE
    # Now release the first install and let it finish.
    release_event.set()
    t1.join(timeout=15)
    first = results["first"]
    assert first.exit_code == EXIT_OK  # type: ignore[union-attr]


# ── ModelInfo sanity ──────────────────────────────────────────────────────


def test_model_info_reports_manifest_source(isolated_cache, manifest_fixture):
    info = get_model_info()
    assert info.name == "unlimited_ocr"
    assert info.repo_id == REPO
    assert info.revision == REVISION
    assert info.download_size_source == "manifest"
    assert info.license_notice
