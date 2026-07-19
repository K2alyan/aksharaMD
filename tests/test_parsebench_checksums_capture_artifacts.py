"""Schema tests for the ParseBench checksum-review artefact (Issue #53, phase B3).

Locks in the invariants that make the captured checksum values a
defensible review surface:

- 12 captures, one per lockfile asset id.
- Every `sha256` is 64-char lowercase hex.
- Every `size_bytes` is a positive integer.
- `dataset_revision` matches the pinned SHA in the main lockfile.
- The artefact is explicitly NOT the main lockfile — `not_promoted`,
  `promotion_status`, and `lockfile_this_pairs_with` are recorded.
- Every recorded cache destination lives OUTSIDE the AksharaMD
  repository tree — no PDF bytes are inside git-tracked paths.
- The main lockfile's per-asset `sha256` / `size_bytes` values remain
  `null`. This artefact must not touch the main lockfile.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARKS = _REPO_ROOT / "benchmarks"
_LOCKFILE = _BENCHMARKS / "parsebench_assets.lock.json"
_CHECKSUMS = _BENCHMARKS / "parsebench_assets.lock.checksums.json"

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"artefact not present: {path}")


def test_checksums_file_shape() -> None:
    _skip_if_missing(_CHECKSUMS)
    with _CHECKSUMS.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc.get("schema_version") == "1.0"
    assert doc.get("issue") == 53
    assert doc.get("phase", "").startswith("B3")
    assert doc.get("not_promoted") is True, (
        "checksums file must carry an explicit not_promoted=True flag so a "
        "future PR that promotes these values into the main lockfile is "
        "traceable to a reviewer decision."
    )
    assert doc.get("promotion_status") == "awaiting-review"
    assert doc.get("lockfile_this_pairs_with") == "benchmarks/parsebench_assets.lock.json"
    assert doc.get("dataset_repo") == "llamaindex/ParseBench"


def test_dataset_revision_matches_main_lockfile() -> None:
    _skip_if_missing(_CHECKSUMS)
    _skip_if_missing(_LOCKFILE)
    with _CHECKSUMS.open("r", encoding="utf-8") as f:
        chk = json.load(f)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        lock = json.load(f)
    expected = (lock.get("dataset_source") or {}).get("dataset_revision")
    assert expected, "main lockfile has no pinned dataset_revision"
    assert chk.get("dataset_revision") == expected, (
        "captured checksums are for a DIFFERENT revision than the main lockfile "
        "pins; do not promote."
    )


def test_every_capture_has_valid_sha_and_size() -> None:
    _skip_if_missing(_CHECKSUMS)
    with _CHECKSUMS.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    captures = doc.get("captures") or []
    assert len(captures) == 12, f"expected 12 captures, got {len(captures)}"
    for entry in captures:
        aid = entry.get("asset_id")
        assert isinstance(aid, str) and aid, "asset_id must be non-empty string"
        sha = entry.get("sha256")
        assert isinstance(sha, str) and _SHA_RE.match(sha), (
            f"asset {aid!r} has malformed sha256: {sha!r}"
        )
        size = entry.get("size_bytes")
        assert isinstance(size, int) and size > 0, (
            f"asset {aid!r} has non-positive size_bytes: {size!r}"
        )
        dest = entry.get("cache_destination")
        assert isinstance(dest, str) and dest, (
            f"asset {aid!r} missing cache_destination"
        )


def test_captured_asset_ids_match_main_lockfile() -> None:
    _skip_if_missing(_CHECKSUMS)
    _skip_if_missing(_LOCKFILE)
    with _CHECKSUMS.open("r", encoding="utf-8") as f:
        chk = json.load(f)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        lock = json.load(f)
    capture_ids = {c["asset_id"] for c in chk["captures"]}
    lock_ids = {a["id"] for a in lock["assets"]}
    assert capture_ids == lock_ids, (
        f"captured id set drift: extra={capture_ids - lock_ids} "
        f"missing={lock_ids - capture_ids}"
    )


def _is_windows_style_absolute(s: str) -> bool:
    """A `C:\\...` / `D:/...` style path."""
    return len(s) >= 3 and s[0].isalpha() and s[1] == ":" and s[2] in ("\\", "/")


def _is_posix_style_absolute(s: str) -> bool:
    return s.startswith("/")


def _dest_is_outside_repo(cache_destination: str, repo_root: Path) -> tuple[bool, str]:
    """Return (is_outside, reason). Works across platforms.

    - A Windows absolute path (`C:\\...`) is trivially outside a POSIX repo
      root (`/home/runner/...`) because the two use different filesystem
      root conventions. Same in reverse.
    - When the path convention matches the current runtime, compare
      resolved paths.
    """
    is_win_abs = _is_windows_style_absolute(cache_destination)
    is_posix_abs = _is_posix_style_absolute(cache_destination)
    repo_str = str(repo_root)
    repo_is_win = _is_windows_style_absolute(repo_str)
    repo_is_posix = _is_posix_style_absolute(repo_str)

    if is_win_abs and not repo_is_win:
        return True, "windows absolute path on a non-windows repo root"
    if is_posix_abs and not repo_is_posix:
        return True, "posix absolute path on a non-posix repo root"
    if not (is_win_abs or is_posix_abs):
        # Relative or unrecognised path — reject: we require an absolute path
        return False, "cache_destination is not an absolute path we recognise"

    # Same convention as runtime — compare with PurePath semantics.
    # (Path.resolve() would be wrong here because it would follow symlinks
    # and may return a normalized form that misses genuine outside-repo
    # nesting. PurePath comparison of `parts` is enough for the invariant.)
    dest_parts: tuple[str, ...]
    repo_parts: tuple[str, ...]
    if is_win_abs:
        dest_parts = PureWindowsPath(cache_destination).parts
        repo_parts = PureWindowsPath(repo_str).parts
    else:
        dest_parts = PurePosixPath(cache_destination).parts
        repo_parts = PurePosixPath(repo_str).parts
    # If destination's leading segments equal the repo root parts, the dest is inside the repo.
    if dest_parts[: len(repo_parts)] == repo_parts:
        return False, f"destination sits under repo root parts {repo_parts}"
    return True, "outside repo root"


def test_cache_destinations_are_outside_the_repo() -> None:
    _skip_if_missing(_CHECKSUMS)
    with _CHECKSUMS.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    repo_root = _REPO_ROOT
    for entry in doc["captures"]:
        dest_str = entry["cache_destination"]
        ok, reason = _dest_is_outside_repo(dest_str, repo_root)
        assert ok, (
            f"asset {entry['asset_id']!r} cache_destination {dest_str!r} "
            f"is not confirmed outside the repo tree at {repo_root} — reason: {reason}"
        )


def test_main_lockfile_still_has_null_per_asset_checksums() -> None:
    """This artefact must not have leaked its values into the main lockfile."""
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        lock = json.load(f)
    for entry in lock["assets"]:
        aid = entry.get("id")
        for field in ("sha256", "size_bytes", "mirror_url", "binary_url"):
            assert entry.get(field) is None, (
                f"main lockfile asset {aid!r} has non-null {field}. "
                f"Promotion from the checksums artefact must happen via a "
                f"separate reviewed PR, not silently in the capture PR."
            )
