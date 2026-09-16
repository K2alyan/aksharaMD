"""Provenance hash computation + placeholder-refusal tests.

Exercises the machinery that makes ``"0"*64`` and other placeholder
values unreachable in an execution record. No real installed
distribution is required beyond ``pytest`` itself (which we use as a
stable installed-distribution stand-in — every environment that runs
the test suite has ``pytest`` installed with a RECORD file).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.provenance import (
    ProvenanceHashError,
    compute_model_artifact_sha256,
    compute_package_source_sha256,
    is_placeholder_sha,
    validate_sha_format,
)

_HEX64 = "a" * 64  # single-char repetition — a placeholder by convention
_REAL = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


# ---------------------------------------------------------------------------
# is_placeholder_sha


def test_placeholder_all_zeros() -> None:
    assert is_placeholder_sha("0" * 64) is True


def test_placeholder_single_char_repetition() -> None:
    for c in "0123456789abcdef":
        assert is_placeholder_sha(c * 64) is True


def test_placeholder_empty_and_none() -> None:
    assert is_placeholder_sha(None) is True
    assert is_placeholder_sha("") is True


def test_placeholder_non_string_is_placeholder() -> None:
    assert is_placeholder_sha(12345) is True  # type: ignore[arg-type]


def test_placeholder_real_hash_is_not_placeholder() -> None:
    assert is_placeholder_sha(_REAL) is False


# ---------------------------------------------------------------------------
# validate_sha_format


def test_validate_accepts_real_hex_hash() -> None:
    validate_sha_format(_REAL, name="test")


def test_validate_refuses_placeholder() -> None:
    with pytest.raises(ProvenanceHashError, match="placeholder"):
        validate_sha_format("0" * 64, name="test")


def test_validate_refuses_none() -> None:
    with pytest.raises(ProvenanceHashError, match="missing"):
        validate_sha_format(None, name="test")


def test_validate_refuses_wrong_length() -> None:
    with pytest.raises(ProvenanceHashError, match="not a 64-hex"):
        validate_sha_format("abc123", name="test")


def test_validate_refuses_non_hex() -> None:
    # ``"gh" * 32`` is 64 chars, non-hex, but has more than one
    # distinct character so the placeholder check does not fire first;
    # the hex-shape check then rejects it.
    with pytest.raises(ProvenanceHashError, match="not a 64-hex"):
        validate_sha_format("gh" * 32, name="test")


def test_validate_refuses_uppercase_hex() -> None:
    """SHA-256 hex is lowercase by convention; enforce it so an
    accidental ``.upper()`` doesn't produce a "different" hash."""
    with pytest.raises(ProvenanceHashError, match="not a 64-hex"):
        validate_sha_format(_REAL.upper(), name="test")


# ---------------------------------------------------------------------------
# compute_package_source_sha256


def test_compute_package_source_sha_for_pytest_returns_real_hash() -> None:
    """``pytest`` is installed in every environment that runs this
    test suite. Its RECORD file is a well-formed PEP 376 CSV; hashing
    it produces a stable, non-placeholder SHA."""
    digest = compute_package_source_sha256("pytest")
    validate_sha_format(digest, name="pytest RECORD hash")
    # Second call is stable.
    assert compute_package_source_sha256("pytest") == digest


def test_compute_package_source_sha_missing_distribution_raises() -> None:
    with pytest.raises(ProvenanceHashError, match="not installed"):
        compute_package_source_sha256("this-distribution-does-not-exist-xyz")


# ---------------------------------------------------------------------------
# compute_model_artifact_sha256


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_model_artifact_hash_is_deterministic(tmp_path: Path) -> None:
    _write_file(tmp_path / "a.bin", b"hello")
    _write_file(tmp_path / "subdir" / "b.bin", b"world")
    h1 = compute_model_artifact_sha256(tmp_path)
    h2 = compute_model_artifact_sha256(tmp_path)
    assert h1 == h2
    validate_sha_format(h1, name="model_artifact_sha")


def test_model_artifact_hash_differs_on_content_change(tmp_path: Path) -> None:
    _write_file(tmp_path / "a.bin", b"hello")
    original = compute_model_artifact_sha256(tmp_path)
    _write_file(tmp_path / "a.bin", b"HELLO")
    changed = compute_model_artifact_sha256(tmp_path)
    assert original != changed


def test_model_artifact_hash_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceHashError, match="does not exist"):
        compute_model_artifact_sha256(tmp_path / "does-not-exist")


def test_model_artifact_hash_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceHashError, match="empty"):
        compute_model_artifact_sha256(tmp_path)


def test_model_artifact_hash_ignores_directories(tmp_path: Path) -> None:
    """Empty subdirectories with no files are ignored (not
    hashed); a non-empty directory of files is required."""
    (tmp_path / "empty_subdir").mkdir()
    (tmp_path / "another_empty").mkdir()
    with pytest.raises(ProvenanceHashError, match="empty"):
        compute_model_artifact_sha256(tmp_path)
