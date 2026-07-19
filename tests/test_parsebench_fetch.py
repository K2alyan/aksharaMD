"""Unit tests for the revision-pinned ParseBench fetcher.

**No live network access.** Every test injects a mock HTTP fetcher or
avoids `fetch()` entirely. The tests verify every branch of the safety
contract:

- lockfile is loaded and its invariants are enforced;
- mutable / missing dataset revisions are refused;
- unresolved identities are reported without silently reducing the
  batch size;
- destinations inside the repo tree, escaping the cache root, or
  containing unsafe characters are refused;
- HTTP success / 404 / 401 / 5xx / empty-body / network-unreachable
  paths each return the documented error code;
- successful writes are atomic (a `.part` temp file never survives);
- outcome ordering is deterministic (matches lockfile order);
- fetched files are recorded as `identity-only`, `checksum_status =
  unavailable`, `approved_for_calibration = False`;
- the CLI refuses to run without `AKSHARAMD_PARSEBENCH_ALLOW_NETWORK=1`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.parsebench_fetch import (  # type: ignore
    FetchError,
    fetch,
)
from benchmarks.parsebench_fetch import main as fetcher_main  # type: ignore

_VALID_SHA = "abcdef0123456789abcdef0123456789abcdef01"


def _write_lockfile(tmp: Path, *, revision: str | None, assets: list[dict]) -> Path:
    doc = {
        "schema_version": "1.0",
        "issue": 53,
        "phase": "B2 (fetcher)",
        "dataset_source": {"dataset_revision": revision, "provider": "LlamaIndex", "project": "ParseBench"},
        "assets": assets,
    }
    p = tmp / "lock.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _valid_asset(**overrides) -> dict:
    e = {
        "id": "3colpres",
        "aliases": ["text_multicolumns__3colpres"],
        "filename": "text_multicolumns__3colpres.pdf",
        "hf_repo_path": "docs/text/text_multicolumns__3colpres.pdf",
        "redistribution": "reference-fetch-only",
        "availability": "available-stable",
        "mirror_url": None,
        "binary_url": None,
        "sha256": None,
        "size_bytes": None,
    }
    e.update(overrides)
    return e


class _FakeHttp:
    """Injectable HTTP fetcher stand-in used across the tests."""

    def __init__(self, responses: dict[str, tuple[int, bytes]]):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.calls.append(url)
        try:
            return self.responses[url]
        except KeyError:
            raise AssertionError(f"unexpected URL fetched: {url}")


# ── lockfile handling ────────────────────────────────────────────────────


def test_lockfile_missing_raises_code_11(tmp_path: Path) -> None:
    with pytest.raises(FetchError) as exc:
        fetch(tmp_path / "does-not-exist.json", tmp_path / "cache", http_fetch=_FakeHttp({}))
    assert exc.value.code == 11


def test_lockfile_invalid_json_raises_code_11(tmp_path: Path) -> None:
    p = tmp_path / "lock.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(FetchError) as exc:
        fetch(p, tmp_path / "cache", http_fetch=_FakeHttp({}))
    assert exc.value.code == 11


def test_populated_mirror_url_raises_code_12(tmp_path: Path) -> None:
    lock = _write_lockfile(
        tmp_path,
        revision=_VALID_SHA,
        assets=[_valid_asset(mirror_url="https://example.com/x.pdf")],
    )
    with pytest.raises(FetchError) as exc:
        fetch(lock, tmp_path / "cache", http_fetch=_FakeHttp({}))
    assert exc.value.code == 12


def test_direct_redistribution_raises_code_12(tmp_path: Path) -> None:
    lock = _write_lockfile(
        tmp_path,
        revision=_VALID_SHA,
        assets=[_valid_asset(redistribution="direct")],
    )
    with pytest.raises(FetchError) as exc:
        fetch(lock, tmp_path / "cache", http_fetch=_FakeHttp({}))
    assert exc.value.code == 12


def test_asset_selector_not_found_raises_code_13(tmp_path: Path) -> None:
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[_valid_asset()])
    with pytest.raises(FetchError) as exc:
        fetch(lock, tmp_path / "cache", asset_selector="nope", http_fetch=_FakeHttp({}))
    assert exc.value.code == 13


def test_unresolved_identity_reports_code_14_without_stopping(tmp_path: Path) -> None:
    body = b"%PDF-1.4 fake"
    url = f"https://huggingface.co/datasets/llamaindex/ParseBench/resolve/{_VALID_SHA}/docs/text/text_multicolumns__3colpres.pdf"
    fake = _FakeHttp({url: (200, body)})
    lock = _write_lockfile(
        tmp_path,
        revision=_VALID_SHA,
        assets=[
            _valid_asset(id="ghost", filename=None, hf_repo_path=None),
            _valid_asset(),  # 3colpres — should still succeed
        ],
    )
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert [o.status for o in report.outcomes] == ["skipped-unresolved", "fetched"]
    assert report.outcomes[0].error_code == 14
    assert report.exit_code == 14  # exit code is the max across the batch


def test_missing_revision_raises_code_15(tmp_path: Path) -> None:
    lock = _write_lockfile(tmp_path, revision=None, assets=[_valid_asset()])
    with pytest.raises(FetchError) as exc:
        fetch(lock, tmp_path / "cache", http_fetch=_FakeHttp({}))
    assert exc.value.code == 15


def test_mutable_revision_string_raises_code_15(tmp_path: Path) -> None:
    lock = _write_lockfile(tmp_path, revision="main", assets=[_valid_asset()])
    with pytest.raises(FetchError) as exc:
        fetch(lock, tmp_path / "cache", http_fetch=_FakeHttp({}))
    assert exc.value.code == 15


# ── HTTP behaviour ──────────────────────────────────────────────────────


def _happy_url(asset: dict, revision: str = _VALID_SHA) -> str:
    return f"https://huggingface.co/datasets/llamaindex/ParseBench/resolve/{revision}/{asset['hf_repo_path']}"


def test_happy_path_fetches_and_records_identity_only(tmp_path: Path) -> None:
    asset = _valid_asset()
    body = b"%PDF-1.4 fake"
    fake = _FakeHttp({_happy_url(asset): (200, body)})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert report.exit_code == 0
    (outcome,) = report.outcomes
    assert outcome.status == "fetched"
    assert outcome.bytes_downloaded == len(body)
    assert outcome.validation == "identity-only"
    assert outcome.checksum_status == "unavailable"
    assert outcome.approved_for_calibration is False
    assert Path(outcome.destination).exists()


def test_cached_second_run_does_not_refetch(tmp_path: Path) -> None:
    asset = _valid_asset()
    body = b"%PDF-1.4 fake"
    fake = _FakeHttp({_happy_url(asset): (200, body)})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report1 = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert report1.outcomes[0].status == "fetched"
    # Second run with an empty responses map — must be served from cache
    fake2 = _FakeHttp({})
    report2 = fetch(lock, tmp_path / "cache", http_fetch=fake2)
    assert report2.outcomes[0].status == "cached"


def test_404_maps_to_code_22(tmp_path: Path) -> None:
    asset = _valid_asset()
    fake = _FakeHttp({_happy_url(asset): (404, b"")})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert report.outcomes[0].error_code == 22
    assert report.exit_code == 22


def test_401_and_403_map_to_code_21(tmp_path: Path) -> None:
    for status in (401, 403):
        asset = _valid_asset()
        fake = _FakeHttp({_happy_url(asset): (status, b"")})
        lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
        report = fetch(lock, tmp_path / "cache", http_fetch=fake)
        assert report.outcomes[0].error_code == 21, status


def test_5xx_maps_to_code_24(tmp_path: Path) -> None:
    asset = _valid_asset()
    fake = _FakeHttp({_happy_url(asset): (503, b"")})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert report.outcomes[0].error_code == 24


def test_empty_body_maps_to_code_24(tmp_path: Path) -> None:
    asset = _valid_asset()
    fake = _FakeHttp({_happy_url(asset): (200, b"")})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert report.outcomes[0].error_code == 24


def test_network_unreachable_maps_to_code_20(tmp_path: Path) -> None:
    def _bad_http(url: str) -> tuple[int, bytes]:
        raise FetchError(20, "network unreachable: simulated DNS failure")

    asset = _valid_asset()
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    with pytest.raises(FetchError) as exc:
        fetch(lock, tmp_path / "cache", http_fetch=_bad_http)
    assert exc.value.code == 20


# ── destination safety ─────────────────────────────────────────────────


def test_destination_inside_repo_tree_raises_code_25(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # cache_root pointing INSIDE the repo — must be refused
    repo_root = Path(__file__).resolve().parents[1]
    bad_cache = repo_root / "benchmarks" / "_should_not_write"
    asset = _valid_asset()
    fake = _FakeHttp({_happy_url(asset): (200, b"%PDF-1.4 fake")})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    with pytest.raises(FetchError) as exc:
        fetch(lock, bad_cache, http_fetch=fake)
    assert exc.value.code == 25


def test_bad_asset_id_characters_raise_code_25(tmp_path: Path) -> None:
    asset = _valid_asset(id="../evil")
    fake = _FakeHttp({})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    with pytest.raises(FetchError) as exc:
        fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert exc.value.code == 25


# ── atomicity / determinism ────────────────────────────────────────────


def test_temp_part_files_do_not_remain_after_success(tmp_path: Path) -> None:
    asset = _valid_asset()
    fake = _FakeHttp({_happy_url(asset): (200, b"%PDF-1.4 fake")})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert report.exit_code == 0
    dest_parent = Path(report.outcomes[0].destination).parent
    leftover = [p for p in dest_parent.iterdir() if p.name.endswith(".part") or p.name.startswith(".")]
    assert leftover == []


def test_outcomes_are_in_lockfile_order(tmp_path: Path) -> None:
    a1 = _valid_asset(id="alpha", filename="alpha.pdf", hf_repo_path="docs/text/alpha.pdf")
    a2 = _valid_asset(id="beta", filename="beta.pdf", hf_repo_path="docs/text/beta.pdf")
    a3 = _valid_asset(id="gamma", filename="gamma.pdf", hf_repo_path="docs/text/gamma.pdf")
    fake = _FakeHttp({
        _happy_url(a1): (200, b"a"),
        _happy_url(a2): (200, b"bb"),
        _happy_url(a3): (200, b"ccc"),
    })
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[a1, a2, a3])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert [o.asset_id for o in report.outcomes] == ["alpha", "beta", "gamma"]


# ── CLI gate ────────────────────────────────────────────────────────────


def test_cli_refuses_without_network_opt_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AKSHARAMD_PARSEBENCH_ALLOW_NETWORK", raising=False)
    exit_code = fetcher_main([
        "--lockfile", str(tmp_path / "does-not-matter.json"),
        "--cache-root", str(tmp_path / "cache"),
    ])
    assert exit_code == 10


def test_cli_accepts_env_var_set_to_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AKSHARAMD_PARSEBENCH_ALLOW_NETWORK", "1")
    # Point at a missing lockfile so the CLI exits early with code 11
    exit_code = fetcher_main([
        "--lockfile", str(tmp_path / "missing.json"),
        "--cache-root", str(tmp_path / "cache"),
    ])
    assert exit_code == 11


# ── runtime checksum verification (Phase B4 promotion) ─────────────────


def _promoted_asset(body: bytes = b"%PDF-1.4 fake", **overrides) -> dict:
    """A lockfile entry with promoted sha256 and size_bytes."""
    import hashlib
    e = _valid_asset()
    e["sha256"] = hashlib.sha256(body).hexdigest()
    e["size_bytes"] = len(body)
    e.update(overrides)
    return e


def test_fetch_matches_promoted_checksum_records_verified(tmp_path: Path) -> None:
    body = b"%PDF-1.4 fake"
    asset = _promoted_asset(body=body)
    fake = _FakeHttp({_happy_url(asset): (200, body)})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    (outcome,) = report.outcomes
    assert outcome.status == "fetched"
    assert outcome.error_code == 0
    assert outcome.checksum_status == "verified"
    assert outcome.validation == "checksum-verified"
    # Even with checksum verified, ground truth is still null → not approved.
    assert outcome.approved_for_calibration is False


def test_fetch_checksum_mismatch_returns_code_23(tmp_path: Path) -> None:
    real_body = b"%PDF-1.4 real"
    wrong_body = b"%PDF-1.4 wrong"
    asset = _promoted_asset(body=real_body)
    # Fetcher receives the wrong bytes but the lockfile promises real_body's sha256
    fake = _FakeHttp({_happy_url(asset): (200, wrong_body)})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    (outcome,) = report.outcomes
    assert outcome.error_code == 23
    assert outcome.checksum_status == "mismatch"
    assert "sha256 mismatch" in outcome.error or "size mismatch" in outcome.error
    assert report.exit_code == 23


def test_fetch_size_mismatch_returns_code_23(tmp_path: Path) -> None:
    body = b"%PDF-1.4 fake"
    asset = _promoted_asset(body=body)
    asset["size_bytes"] = len(body) + 100  # deliberately wrong
    fake = _FakeHttp({_happy_url(asset): (200, body)})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    (outcome,) = report.outcomes
    assert outcome.error_code == 23
    assert "size mismatch" in outcome.error


def test_calibration_approval_requires_label_and_defect_kind(tmp_path: Path) -> None:
    """Byte identity alone is insufficient. `expected_label` + `defect_kind`
    must be present to flip `approved_for_calibration`.
    """
    body = b"%PDF-1.4 fake"
    asset = _promoted_asset(body=body, expected_label="true-positive", defect_kind="block-level")
    fake = _FakeHttp({_happy_url(asset): (200, body)})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report = fetch(lock, tmp_path / "cache", http_fetch=fake)
    (outcome,) = report.outcomes
    assert outcome.checksum_status == "verified"
    assert outcome.approved_for_calibration is True


def test_cached_second_run_still_verifies_checksum(tmp_path: Path) -> None:
    """Cache-hit path must re-verify — the file could have been tampered
    with between runs.
    """
    body = b"%PDF-1.4 fake"
    asset = _promoted_asset(body=body)
    fake = _FakeHttp({_happy_url(asset): (200, body)})
    lock = _write_lockfile(tmp_path, revision=_VALID_SHA, assets=[asset])
    report1 = fetch(lock, tmp_path / "cache", http_fetch=fake)
    assert report1.outcomes[0].status == "fetched"
    # Corrupt the cached file
    dest = Path(report1.outcomes[0].destination)
    dest.write_bytes(b"tampered content")
    # Second run must detect the mismatch and return 23
    report2 = fetch(lock, tmp_path / "cache", http_fetch=_FakeHttp({}))
    assert report2.outcomes[0].error_code == 23
    assert report2.outcomes[0].checksum_status == "mismatch"
