"""Schema tests for the ParseBench provenance artefacts (Issue #53 phase A).

Validates the *shape* of `benchmarks/parsebench_assets.proposed.json`
and confirms that its provenance report references the expected
canonical source. These tests do NOT fetch, mirror, or validate any
third-party PDF.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARKS = _REPO_ROOT / "benchmarks"

_PROPOSED = _BENCHMARKS / "parsebench_assets.proposed.json"
_REPORT_MD = _BENCHMARKS / "PARSEBENCH_ASSET_PROVENANCE_2026-07-18.md"

_REQUIRED_ASSET_FIELDS = {
    "id",
    "aliases",
    "tier",
    "source_project",
    "source_page_url",
    "binary_url",
    "mirror_url",
    "sha256",
    "size_bytes",
    "license",
    "license_url",
    "copyright_owner",
    "redistribution",
    "availability",
    "attribution",
    "expected_label",
    "page_level_ground_truth",
    "defect_kind",
    "ci_retrieval",
    "checked_at",
    "notes",
}

_ALLOWED_REDISTRIBUTION = {"direct", "reference-fetch-only", "research-only", "restricted", "unknown"}
_ALLOWED_AVAILABILITY = {"available-stable", "available-unstable", "missing", "authentication-required", "generated-asset", "identity-unresolved"}


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"artefact not present: {path}")


def test_proposed_lockfile_shape() -> None:
    _skip_if_missing(_PROPOSED)
    with _PROPOSED.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc.get("issue") == 53
    assert doc.get("phase", "").startswith("A"), "not marked as phase A"
    assert doc.get("authored_on_commit") == "2af22057d9e99fea0ff2dc262ce8cff41408ca54"
    ds = doc.get("dataset_source") or {}
    assert ds.get("provider") == "LlamaIndex"
    assert ds.get("project") == "ParseBench"
    assert ds.get("dataset_license") == "Apache-2.0"
    assert ds.get("paper_arxiv_id"), "paper arXiv id missing"
    assets = doc.get("assets") or []
    assert len(assets) == 12, f"expected 12 asset entries, got {len(assets)}"


def test_every_asset_carries_required_fields() -> None:
    _skip_if_missing(_PROPOSED)
    with _PROPOSED.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for entry in doc["assets"]:
        missing = _REQUIRED_ASSET_FIELDS - set(entry.keys())
        assert not missing, f"asset {entry.get('id')!r} missing fields: {missing}"


def test_redistribution_and_availability_are_from_allowed_sets() -> None:
    _skip_if_missing(_PROPOSED)
    with _PROPOSED.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for entry in doc["assets"]:
        assert entry["redistribution"] in _ALLOWED_REDISTRIBUTION, (
            f"{entry['id']}: redistribution={entry['redistribution']!r} not in {_ALLOWED_REDISTRIBUTION}"
        )
        assert entry["availability"] in _ALLOWED_AVAILABILITY, (
            f"{entry['id']}: availability={entry['availability']!r} not in {_ALLOWED_AVAILABILITY}"
        )


def test_no_mirror_url_or_sha256_in_phase_a() -> None:
    """Phase A specifically forbids live mirror URLs and captured sha256
    values. Unknowns must be explicit `null` — never invented.
    """
    _skip_if_missing(_PROPOSED)
    with _PROPOSED.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for entry in doc["assets"]:
        assert entry["mirror_url"] is None, (
            f"{entry['id']}: mirror_url must remain null in phase A; got {entry['mirror_url']!r}"
        )
        assert entry["sha256"] is None, (
            f"{entry['id']}: sha256 must remain null in phase A; got {entry['sha256']!r}"
        )


def test_all_expected_asset_ids_present() -> None:
    _skip_if_missing(_PROPOSED)
    with _PROPOSED.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    ids = {entry["id"] for entry in doc["assets"]}
    expected = {
        "3colpres", "ikea3", "elpais", "simple2",
        "eastbaytimes", "battery", "2colmercedes",
        "text_dense__de", "letter3", "myctophidae",
        "strikeUnderline", "japanese_case",
    }
    assert ids == expected, (
        f"asset id set drift: extra={ids - expected}, missing={expected - ids}"
    )


def test_report_markdown_mentions_key_provenance_terms() -> None:
    _skip_if_missing(_REPORT_MD)
    body = _REPORT_MD.read_text(encoding="utf-8")
    for term in [
        "Apache-2.0",
        "LlamaIndex",
        "ParseBench",
        "2604.08538",
        "reference-fetch-only",
        "identity-unresolved",
        "No third-party PDF",
    ]:
        assert term in body, f"provenance report missing term: {term!r}"
