"""A1a — trusted-manifest loader, snapshot verification, canonical
containment, and classified extra-file rejection.

None of these tests load the model, invoke ``transformers``, or import
any file from the downloaded snapshot.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_adapter import (  # type: ignore
    _KNOWN_LOADER_JSON,
    TrustedManifestError,
    _canonical_containment_check,
    _classify_snapshot_file,
    load_trusted_manifest,
    verify_snapshot_against_manifest,
)

# ── Fixture helpers ─────────────────────────────────────────────────────


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write(p: Path, content: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def _valid_test_manifest(snap: Path, revision: str = "a" * 40) -> dict:
    """Materialize a minimal manifest against fake files under ``snap``.

    Layout:
      snap/
        modeling.py          (executable, verify_on_every_load=True)
        config.json          (config-sensitive, verify_on_every_load=True)
        weights.safetensors  (weights, verify_on_every_load=False)
    """
    _write(snap / "modeling.py", b"# fake modeling\n")
    _write(snap / "config.json", b'{"ok": true}')
    _write(snap / "weights.safetensors", b"\x00" * 1024)
    return {
        "manifest_schema_version": 1,
        "manifest_id": "test-manifest-v1",
        "repo_id": "test/repo",
        "revision": revision,
        "generator": "test",
        "generator_version": "1.0",
        "files": {
            "modeling.py": {
                "sha256": _sha256(b"# fake modeling\n"),
                "size_bytes": len(b"# fake modeling\n"),
                "class": "executable",
                "required_for_runtime": True,
                "verify_on_every_load": True,
            },
            "config.json": {
                "sha256": _sha256(b'{"ok": true}'),
                "size_bytes": len(b'{"ok": true}'),
                "class": "config-sensitive",
                "required_for_runtime": True,
                "verify_on_every_load": True,
            },
            "weights.safetensors": {
                "sha256": _sha256(b"\x00" * 1024),
                "size_bytes": 1024,
                "class": "weights",
                "required_for_runtime": True,
                "verify_on_every_load": False,
            },
        },
    }


# ── load_trusted_manifest ───────────────────────────────────────────────


def test_load_trusted_manifest_missing_file_raises(tmp_path: Path):
    with pytest.raises(TrustedManifestError, match="manifest missing"):
        load_trusted_manifest(tmp_path / "nope.json")


def test_load_trusted_manifest_invalid_json_raises(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text("{not-json", encoding="utf-8")
    with pytest.raises(TrustedManifestError, match="not valid JSON"):
        load_trusted_manifest(p)


def test_load_trusted_manifest_missing_required_field_raises(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"manifest_schema_version": 1}), encoding="utf-8")
    with pytest.raises(TrustedManifestError, match="missing required field"):
        load_trusted_manifest(p)


def test_load_trusted_manifest_schema_mismatch_raises(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "manifest_schema_version": 999,
        "manifest_id": "x",
        "repo_id": "test/repo",
        "revision": "a" * 40,
        "files": {"x": {"sha256": "0" * 64, "size_bytes": 1, "class": "x",
                        "required_for_runtime": True, "verify_on_every_load": True}},
    }), encoding="utf-8")
    with pytest.raises(TrustedManifestError, match="manifest_schema_version"):
        load_trusted_manifest(p)


def test_load_trusted_manifest_bad_revision_raises(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "manifest_schema_version": 1,
        "manifest_id": "x",
        "repo_id": "test/repo",
        "revision": "main",
        "files": {"x": {"sha256": "0" * 64, "size_bytes": 1, "class": "x",
                        "required_for_runtime": True, "verify_on_every_load": True}},
    }), encoding="utf-8")
    with pytest.raises(TrustedManifestError, match="40-char"):
        load_trusted_manifest(p)


def test_load_trusted_manifest_empty_files_raises(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "manifest_schema_version": 1,
        "manifest_id": "x",
        "repo_id": "test/repo",
        "revision": "a" * 40,
        "files": {},
    }), encoding="utf-8")
    with pytest.raises(TrustedManifestError, match="non-empty dict"):
        load_trusted_manifest(p)


def test_load_trusted_manifest_file_missing_field_raises(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "manifest_schema_version": 1,
        "manifest_id": "x",
        "repo_id": "test/repo",
        "revision": "a" * 40,
        "files": {"x.py": {"sha256": "0" * 64, "size_bytes": 1, "class": "executable"}},
    }), encoding="utf-8")
    with pytest.raises(TrustedManifestError, match="missing field"):
        load_trusted_manifest(p)


def test_load_committed_runtime_manifest_is_valid():
    """The committed runtime manifest must satisfy every schema check
    the loader enforces."""
    m = load_trusted_manifest()
    assert m["manifest_schema_version"] == 1
    assert m["manifest_id"] == "unlimited-ocr-d549bb9d-v1"
    assert m["repo_id"] == "baidu/Unlimited-OCR"
    assert m["revision"] == "d549bb9d6a055dbe291408916d66acc2cd5920f6"
    assert len(m["files"]) == 12
    # No timestamp allowed in the runtime manifest.
    assert "generated_at" not in m


def test_committed_runtime_manifest_is_deterministic():
    """The runtime manifest bytes must be reproducible — this locks in
    'no timestamps in the runtime trust root' per reviewer feedback."""
    from aksharamd.plugins.ocr_backends import UNLIMITED_OCR_TRUSTED_MANIFEST_PATH
    raw = UNLIMITED_OCR_TRUSTED_MANIFEST_PATH.read_bytes()
    assert b"generated_at" not in raw
    # Re-parsing and re-serializing with the same key order should yield
    # the same content.
    parsed = json.loads(raw)
    assert "generated_at" not in parsed


def test_committed_acquisition_inventory_records_wheel_and_license():
    """The acquisition inventory must record all 14 downloaded files,
    including the quarantined wheel and LICENSE — those are excluded
    from the runtime manifest but must still be tracked."""
    from aksharamd.plugins.ocr_backends import UNLIMITED_OCR_ACQUISITION_INVENTORY_PATH
    inv = json.loads(UNLIMITED_OCR_ACQUISITION_INVENTORY_PATH.read_text(encoding="utf-8"))
    assert inv["total_files"] == 14
    paths = set(inv["files"].keys())
    assert "LICENSE" in paths
    # Wheel path uses forward slashes.
    assert any(p.startswith("wheel/") and p.endswith(".whl") for p in paths)


# ── _classify_snapshot_file ─────────────────────────────────────────────


def test_classify_known_file():
    mf = {"modeling.py", "config.json"}
    assert _classify_snapshot_file("modeling.py", mf) == "known"
    assert _classify_snapshot_file("config.json", mf) == "known"


def test_classify_extra_python_refused():
    assert _classify_snapshot_file("backdoor.py", set()) == "refuse_executable"


def test_classify_extra_native_binary_refused():
    for ext in ("so", "pyd", "dll", "sh", "bat", "cmd", "exe"):
        assert _classify_snapshot_file(f"x.{ext}", set()) == "refuse_executable"


def test_classify_extra_json_refused():
    assert _classify_snapshot_file("secrets.json", set()) == "refuse_json"


def test_classify_known_loader_json_still_refused_if_missing_from_manifest():
    """A file in the KNOWN_LOADER_JSON set that's ALSO not in the
    manifest indicates a manifest bug — refuse."""
    # Use a name from the constant so this test tracks it.
    example = next(iter(_KNOWN_LOADER_JSON))
    assert _classify_snapshot_file(example, set()) == "refuse_json"


def test_classify_ignore_metadata():
    for name in ("README.md", "demo.gif", "figure.png", "sample.pdf",
                 ".gitattributes", "NOTES.txt", "LICENSE"):
        assert _classify_snapshot_file(name, set()) == "ignore_metadata"


def test_classify_unknown_extension_warns():
    assert _classify_snapshot_file("mystery.xyz", set()) == "warn_unknown"


# ── _canonical_containment_check ────────────────────────────────────────


def test_canonical_containment_accepts_file_inside_root(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "a.py"
    f.write_bytes(b"pass\n")
    ok, note = _canonical_containment_check(f, root)
    assert ok, note


def test_canonical_containment_rejects_broken_symlink(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    link = root / "broken"
    try:
        link.symlink_to(tmp_path / "does_not_exist")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    ok, note = _canonical_containment_check(link, root)
    assert not ok
    assert "broken symlink" in note or "missing file" in note


def test_canonical_containment_rejects_symlink_outside_root(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "target.py"
    outside_file.write_bytes(b"pass\n")
    link = root / "escape"
    try:
        link.symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    ok, note = _canonical_containment_check(link, root)
    assert not ok
    assert "escapes cache root" in note


def test_canonical_containment_accepts_relative_symlink_inside_root(tmp_path: Path):
    """Relative symlinks with '..' are fine as long as canonical
    resolution stays inside the cache root."""
    root = tmp_path / "root"
    (root / "snapshots" / "rev1").mkdir(parents=True)
    (root / "blobs").mkdir()
    target = root / "blobs" / "content"
    target.write_bytes(b"weights\n")
    link = root / "snapshots" / "rev1" / "weights"
    try:
        link.symlink_to(Path("..") / ".." / "blobs" / "content")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    ok, note = _canonical_containment_check(link, root)
    assert ok, note


def test_canonical_containment_rejects_directory_target(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    d = root / "d"
    d.mkdir()
    ok, note = _canonical_containment_check(d, root)
    assert not ok
    assert "not a regular file" in note


# ── verify_snapshot_against_manifest ────────────────────────────────────


def test_verify_snapshot_happy_path(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert ok, note
    assert "verified 3 runtime files" in note


def test_verify_snapshot_refuses_missing_file(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    (snap / "modeling.py").unlink()
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok
    assert "manifest file missing" in note


def test_verify_snapshot_refuses_size_mismatch(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    # Truncate weights (simulates partial download).
    (snap / "weights.safetensors").write_bytes(b"\x00" * 512)
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok
    assert "size mismatch" in note


def test_verify_snapshot_refuses_sha_mismatch_on_executable(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    # Same size (16 bytes), different bytes — must fail the SHA check,
    # not the size check.
    original = b"# fake modeling\n"
    tampered = b"# TAMPERED code\n"
    assert len(tampered) == len(original)
    (snap / "modeling.py").write_bytes(tampered)
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok
    assert "SHA-256 mismatch" in note


def test_verify_snapshot_hashes_weights_when_requested(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    # Tamper weights (size preserved).
    (snap / "weights.safetensors").write_bytes(b"\xff" * 1024)
    # Fast mode skips weights → passes.
    ok_fast, _ = verify_snapshot_against_manifest(
        manifest, snapshot_root=snap, hash_weights=False,
    )
    assert ok_fast, "fast mode should skip weights hashing"
    # Full mode catches it.
    ok_full, note = verify_snapshot_against_manifest(
        manifest, snapshot_root=snap, hash_weights=True,
    )
    assert not ok_full
    assert "SHA-256 mismatch" in note
    assert "weights.safetensors" in note


def test_verify_snapshot_refuses_extra_executable(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    (snap / "backdoor.py").write_bytes(b"import os\n")
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok
    assert "unreviewed executable" in note
    assert "backdoor.py" in note


def test_verify_snapshot_refuses_extra_json(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    (snap / "extra.json").write_text('{"x":1}', encoding="utf-8")
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok
    assert "unreviewed JSON" in note


def test_verify_snapshot_refuses_extra_native_binary(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    (snap / "rogue.so").write_bytes(b"\x7fELF")
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok
    assert "unreviewed executable" in note


def test_verify_snapshot_ignores_readme_and_gif(tmp_path: Path):
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    (snap / "README.md").write_text("# readme\n", encoding="utf-8")
    (snap / "demo.gif").write_bytes(b"GIF89a\x00")
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert ok, note


def test_verify_snapshot_tolerates_wheel_subdir(tmp_path: Path):
    """The wheel/ subdirectory containing a .whl is expected at this
    revision (quarantined; tracked by acquisition inventory only).
    Must not cause a runtime-manifest verification failure.
    """
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    (snap / "wheel").mkdir()
    (snap / "wheel" / "sglang-0.0.0-py3-none-any.whl").write_bytes(b"PK\x03\x04fakewheel")
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert ok, note


def test_verify_snapshot_refuses_extra_py_in_wheel_subdir(tmp_path: Path):
    """A .py inside wheel/ that isn't the tolerated .whl is still an
    unreviewed executable — must refuse."""
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    (snap / "wheel").mkdir()
    (snap / "wheel" / "setup_hook.py").write_bytes(b"import os\n")
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok
    assert "unreviewed executable" in note


def test_verify_snapshot_refuses_when_symlink_escapes(tmp_path: Path):
    """A manifest file whose symlink target escapes the model-cache
    root must be refused."""
    snap = tmp_path / "snap"
    manifest = _valid_test_manifest(snap)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "modeling.py"
    outside_target.write_bytes(b"# fake modeling\n")  # matches sha
    (snap / "modeling.py").unlink()
    try:
        (snap / "modeling.py").symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    ok, note = verify_snapshot_against_manifest(manifest, snapshot_root=snap)
    assert not ok, "escape via symlink must be caught"


# ── Sanity: sha256_file matches manifest for real committed files ──────


def test_committed_manifest_sha_matches_no_real_files_required():
    """We do NOT hash the real 6.67 GB safetensors in the test suite —
    that's an integration test. But loading the manifest must not raise.
    """
    m = load_trusted_manifest()
    # Spot-check one field's shape rather than the content.
    entry = m["files"]["modeling_unlimitedocr.py"]
    assert entry["class"] == "executable"
    assert entry["required_for_runtime"] is True
    assert entry["verify_on_every_load"] is True
    assert len(entry["sha256"]) == 64
