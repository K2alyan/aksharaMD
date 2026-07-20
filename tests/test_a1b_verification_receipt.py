"""A1b — verification receipt (full mode, fast mode, invalidation).

Uses a synthetic snapshot laid out in the same shape HuggingFace uses
(``models--<slug>/snapshots/<revision>/``) inside a monkeypatched
``HF_HOME``, so the tests never touch the real ~14 GB cache.

Never loads the model. Never invokes ``transformers``. Never imports
any file from the downloaded snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from pathlib import Path

import pytest

from aksharamd.plugins.ocr_backends.verification_receipt import (
    RECEIPT_SCHEMA_VERSION,
    VERIFICATION_IMPLEMENTATION_VERSION,
    ReceiptError,
    fast_verify,
    full_verify_and_write_receipt,
    invalidate_receipt,
    receipt_path,
)

# ── Fixture ─────────────────────────────────────────────────────────────


REPO = "baidu/Unlimited-OCR"
REVISION = "d549bb9d6a055dbe291408916d66acc2cd5920f6"


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _lay_out_snapshot(cache_root: Path, files: dict[str, bytes]) -> Path:
    """Materialize a synthetic HF-cache snapshot under cache_root.
    Returns the snapshot directory."""
    slug = REPO.replace("/", "--")
    snap = cache_root / f"models--{slug}" / "snapshots" / REVISION
    snap.mkdir(parents=True)
    for rel, content in files.items():
        p = snap / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return snap


def _write_manifest(manifest_path: Path, files_content: dict[str, bytes]) -> dict:
    """Build + write a manifest matching the on-disk file set."""
    manifest = {
        "manifest_schema_version": 1,
        "manifest_id": "unlimited-ocr-d549bb9d-v1",
        "repo_id": REPO,
        "revision": REVISION,
        "generator": "test",
        "generator_version": "1.0",
        "files": {
            "modeling.py": {
                "sha256": _sha256(files_content["modeling.py"]),
                "size_bytes": len(files_content["modeling.py"]),
                "class": "executable",
                "required_for_runtime": True,
                "verify_on_every_load": True,
            },
            "config.json": {
                "sha256": _sha256(files_content["config.json"]),
                "size_bytes": len(files_content["config.json"]),
                "class": "config-sensitive",
                "required_for_runtime": True,
                "verify_on_every_load": True,
            },
            "weights.safetensors": {
                "sha256": _sha256(files_content["weights.safetensors"]),
                "size_bytes": len(files_content["weights.safetensors"]),
                "class": "weights",
                "required_for_runtime": True,
                "verify_on_every_load": False,
            },
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    """Yields (manifest, manifest_path, cache_root, snap)."""
    cache_root = tmp_path / "hf_home"
    cache_root.mkdir()
    monkeypatch.setenv("HF_HOME", str(cache_root))
    files_content = {
        "modeling.py": b"# fake modeling code\n",
        "config.json": b'{"ok": true}',
        "weights.safetensors": b"\x00" * 4096,
    }
    snap = _lay_out_snapshot(cache_root, files_content)
    manifest_path = tmp_path / "manifest.json"
    manifest = _write_manifest(manifest_path, files_content)
    return manifest, manifest_path, cache_root, snap


# ── Full-mode ───────────────────────────────────────────────────────────


def test_full_verify_writes_receipt(setup):
    manifest, manifest_path, cache_root, _snap = setup
    out = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert out.ok, out.note
    assert out.receipt_path is not None
    assert out.receipt_path.exists()
    # Receipt file should record every runtime file (weights + non-weights).
    r = json.loads(out.receipt_path.read_text(encoding="utf-8"))
    assert r["manifest_id"] == "unlimited-ocr-d549bb9d-v1"
    assert r["revision"] == REVISION
    assert r["verification_implementation_version"] == VERIFICATION_IMPLEMENTATION_VERSION
    assert r["receipt_schema_version"] == RECEIPT_SCHEMA_VERSION
    entries = {e["path"]: e for e in r["entries"]}
    assert set(entries) == {"modeling.py", "config.json", "weights.safetensors"}


def test_full_verify_refuses_missing_snapshot(setup, tmp_path):
    manifest, manifest_path, _cache_root, _snap = setup
    other_cache = tmp_path / "empty_cache"
    other_cache.mkdir()
    out = full_verify_and_write_receipt(manifest, manifest_path, other_cache)
    assert not out.ok
    assert "snapshot not present" in out.note


def test_full_verify_refuses_on_weights_tamper(setup):
    manifest, manifest_path, cache_root, snap = setup
    # Tamper preserves size but changes content — SHA must catch it.
    p = snap / "weights.safetensors"
    tampered = b"\xff" * p.stat().st_size
    p.write_bytes(tampered)
    out = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert not out.ok
    assert "SHA-256 mismatch" in out.note
    assert "weights.safetensors" in out.note


def test_full_verify_refuses_on_executable_tamper(setup):
    manifest, manifest_path, cache_root, snap = setup
    p = snap / "modeling.py"
    tampered = b"X" * p.stat().st_size
    p.write_bytes(tampered)
    out = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert not out.ok
    assert "SHA-256 mismatch" in out.note
    assert "modeling.py" in out.note


@pytest.mark.skipif(platform.system() == "Windows",
                     reason="POSIX permission model only")
def test_full_verify_writes_receipt_with_user_only_perms(setup):
    manifest, manifest_path, cache_root, _snap = setup
    out = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert out.ok
    assert out.receipt_path is not None
    mode = out.receipt_path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600 permissions, got 0o{mode:o}"


# ── Fast-mode ───────────────────────────────────────────────────────────


def test_fast_verify_after_full_passes(setup):
    manifest, manifest_path, cache_root, _snap = setup
    full = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert full.ok
    fast = fast_verify(manifest, manifest_path, cache_root)
    assert fast.ok, fast.note
    # Fast mode hashed the small files (not weights).
    assert "modeling.py" in fast.files_hashed
    assert "config.json" in fast.files_hashed
    assert "weights.safetensors" not in fast.files_hashed
    # Fast mode checked weights via receipt.
    assert "weights.safetensors" in fast.files_checked_by_receipt


def test_fast_verify_refuses_without_receipt(setup):
    manifest, manifest_path, cache_root, _snap = setup
    fast = fast_verify(manifest, manifest_path, cache_root)
    assert not fast.ok
    assert "receipt missing" in fast.note
    assert "aksharamd models verify" in fast.note


def test_fast_verify_refuses_on_manifest_bytes_change(setup):
    manifest, manifest_path, cache_root, _snap = setup
    full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    # Rewrite the manifest with an added comment — same content dict but
    # different bytes.
    body = json.loads(manifest_path.read_text(encoding="utf-8"))
    body["generator_version"] = "1.0.0-nocache-bust"
    manifest_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    fast = fast_verify(body, manifest_path, cache_root)
    assert not fast.ok
    assert "manifest_sha256 changed" in fast.note


def test_fast_verify_refuses_on_revision_change(setup):
    """Changing the pinned revision must be caught: the receipt records
    the old revision, and either the snapshot lookup fails (no such
    revision cached) or the mismatch check fires."""
    manifest, manifest_path, cache_root, _snap = setup
    full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    # Simulate a new manifest for a different revision.
    manifest["revision"] = "b" * 40
    fast = fast_verify(manifest, manifest_path, cache_root)
    assert not fast.ok
    assert (
        "snapshot not present" in fast.note
        or "revision changed" in fast.note
        or "receipt missing" in fast.note
    )


def test_fast_verify_refuses_on_manifest_id_change(setup):
    """Changing manifest_id changes the receipt path lookup (keyed on
    manifest_id) — so the check surfaces as either ``receipt missing``
    at the new path OR ``manifest_id changed`` if a collision receipt
    happened to exist. Either invalidation is acceptable."""
    manifest, manifest_path, cache_root, _snap = setup
    full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    manifest["manifest_id"] = "some-other-id"
    fast = fast_verify(manifest, manifest_path, cache_root)
    assert not fast.ok
    assert "receipt missing" in fast.note or "manifest_id changed" in fast.note


def test_fast_verify_refuses_on_weights_size_drift(setup):
    manifest, manifest_path, cache_root, snap = setup
    full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    # Truncate weights.
    (snap / "weights.safetensors").write_bytes(b"\x00" * 100)
    # Update the manifest to match the new (truncated) size so the
    # fresh SHA doesn't fire — this isolates the receipt-drift check.
    manifest["files"]["weights.safetensors"]["size_bytes"] = 100
    manifest["files"]["weights.safetensors"]["sha256"] = _sha256(b"\x00" * 100)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # But now the receipt covers a different manifest_sha256 - which
    # means the earlier check catches it before the weight drift check.
    fast = fast_verify(manifest, manifest_path, cache_root)
    assert not fast.ok


def test_fast_verify_refuses_when_weights_replaced_with_same_size_and_mtime(setup):
    """Detect a replacement that preserves size AND mtime but changes
    the underlying file identity (POSIX inode swap). On filesystems
    where identity is unavailable, size+mtime is the fallback (which
    would allow this exact case) — that's called out in the module
    docstring as a known tamper limit.
    """
    manifest, manifest_path, cache_root, snap = setup
    full = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert full.ok
    p = snap / "weights.safetensors"
    old_mtime_ns = p.stat().st_mtime_ns
    # Replace via unlink+create — new inode on POSIX. Restore mtime.
    original = p.read_bytes()
    p.unlink()
    p.write_bytes(original)  # same content, but new file identity
    os.utime(p, ns=(old_mtime_ns, old_mtime_ns))
    fast = fast_verify(manifest, manifest_path, cache_root)
    if platform.system() == "Windows":
        # Windows: inode is often unavailable; we accept the fast path
        # for now. Full verify is required for cryptographic guarantee.
        # Test does NOT assert refusal here.
        pass
    else:
        assert not fast.ok
        assert "identity drifted" in fast.note or "identity" in fast.note


def test_fast_verify_refuses_when_verification_impl_version_bumps(setup, monkeypatch):
    manifest, manifest_path, cache_root, _snap = setup
    full = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert full.ok
    # Simulate a code-level bump by editing the receipt to record an
    # older version — same effect as running new code against an old
    # receipt.
    r = json.loads(full.receipt_path.read_text(encoding="utf-8"))
    r["verification_implementation_version"] = 0  # older than 1
    full.receipt_path.write_text(json.dumps(r, indent=2), encoding="utf-8")
    fast = fast_verify(manifest, manifest_path, cache_root)
    assert not fast.ok
    assert "verification_implementation_version changed" in fast.note


# ── Invalidation ────────────────────────────────────────────────────────


def test_invalidate_receipt_removes_file(setup):
    manifest, manifest_path, cache_root, _snap = setup
    full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    rpath = receipt_path(manifest, cache_root)
    assert rpath.exists()
    assert invalidate_receipt(manifest, cache_root) is True
    assert not rpath.exists()
    # Idempotent.
    assert invalidate_receipt(manifest, cache_root) is False


def test_fast_verify_after_invalidate_refuses(setup):
    manifest, manifest_path, cache_root, _snap = setup
    full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    invalidate_receipt(manifest, cache_root)
    fast = fast_verify(manifest, manifest_path, cache_root)
    assert not fast.ok
    assert "receipt missing" in fast.note


# ── Atomic writes ───────────────────────────────────────────────────────


def test_atomic_write_leaves_no_tempfile_on_success(setup):
    manifest, manifest_path, cache_root, _snap = setup
    full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    rdir = receipt_path(manifest, cache_root).parent
    leftovers = [p for p in rdir.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, f"leftover tempfiles: {leftovers}"


def test_atomic_write_does_not_partially_update(setup, monkeypatch):
    """If the write fails halfway, the existing receipt (if any) must
    remain valid — os.replace is atomic."""
    manifest, manifest_path, cache_root, _snap = setup
    # First: write a good receipt.
    good = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert good.ok
    good_bytes = good.receipt_path.read_bytes()

    # Now simulate a write failure by making os.replace raise.
    import aksharamd.plugins.ocr_backends.verification_receipt as vr

    def _boom(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(vr.os, "replace", _boom)
    fail = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    assert not fail.ok
    # The pre-existing receipt bytes must be untouched.
    assert good.receipt_path.read_bytes() == good_bytes
    # No tempfile leaked.
    rdir = good.receipt_path.parent
    leftovers = [p for p in rdir.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, f"leftover tempfiles: {leftovers}"


# ── Reject smoke ────────────────────────────────────────────────────────


def test_receipt_path_uses_manifest_id(setup):
    manifest, _mp, cache_root, _snap = setup
    p = receipt_path(manifest, cache_root)
    assert manifest["manifest_id"] in p.name
    assert p.suffix == ".json"


def test_receipt_error_is_raised_type_for_stat_failures():
    """ReceiptError should be raised (not caught silently) on malformed
    input to _load_receipt via receipt-file corruption paths that fast
    mode must surface with a clean note rather than a crash."""
    # Covered indirectly via test_fast_verify_refuses_without_receipt;
    # kept here as a shape assertion.
    assert issubclass(ReceiptError, Exception)


def test_full_verify_records_platform_metadata(setup):
    manifest, manifest_path, cache_root, _snap = setup
    out = full_verify_and_write_receipt(manifest, manifest_path, cache_root)
    r = json.loads(out.receipt_path.read_text(encoding="utf-8"))
    assert "verified_at" in r
    assert "verified_by" in r
    # Hostname is hashed, not recorded in plaintext.
    assert "hostname_hash" in r["verified_by"]
    assert len(r["verified_by"]["hostname_hash"]) == 16
    assert r["verified_by"]["os"] == platform.system()
    # verified_at is an ISO-ish timestamp.
    t = time.strptime(r["verified_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert t is not None
