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
from pathlib import Path

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


def test_cache_destinations_are_outside_the_repo() -> None:
    _skip_if_missing(_CHECKSUMS)
    with _CHECKSUMS.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    repo = _REPO_ROOT.resolve()
    for entry in doc["captures"]:
        dest = Path(entry["cache_destination"]).resolve()
        try:
            dest.relative_to(repo)
            pytest.fail(
                f"asset {entry['asset_id']!r} cache_destination {dest} is INSIDE the "
                f"repo tree at {repo}. Corpus bytes must never be committed."
            )
        except ValueError:
            pass  # good — outside the repo


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
