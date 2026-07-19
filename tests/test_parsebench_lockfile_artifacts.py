"""Schema tests for the frozen ParseBench reference-fetch lockfile
(Issue #53 phase B1).

Locks in the invariants that make the lockfile a defensible policy
artefact:

- Every asset is `redistribution: reference-fetch-only`.
- Every asset's `mirror_url`, `sha256`, and `size_bytes` are exactly
  `null`. Populating any of these before an authorised fetch step is a
  policy violation.
- The Japanese fixture identity is resolved to `text_dense__japanese`
  from historical evidence, not from preference.
- The rights-review queue covers every asset.

These tests do NOT execute any download or fetch. They validate the
bytes of `parsebench_assets.lock.json`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARKS = _REPO_ROOT / "benchmarks"

_LOCKFILE = _BENCHMARKS / "parsebench_assets.lock.json"
_DESIGN_MD = _BENCHMARKS / "PARSEBENCH_REFERENCE_FETCH_DESIGN.md"

_REQUIRED_ASSET_FIELDS = {
    "id",
    "aliases",
    "tier",
    "source_project",
    "canonical_dataset_page",
    "hf_repo_path",
    "hf_repo_revision",
    "filename",
    "binary_url",
    "mirror_url",
    "sha256",
    "size_bytes",
    "license",
    "license_url",
    "license_caveat",
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


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"artefact not present: {path}")


def test_lockfile_shape() -> None:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc.get("schema_version") == "1.0"
    assert doc.get("issue") == 53
    assert doc.get("phase", "").startswith("B1"), "not marked as phase B1"
    assets = doc.get("assets") or []
    assert len(assets) == 12, f"expected 12 asset entries, got {len(assets)}"


def test_dataset_source_records_apache_license_and_paper() -> None:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    ds = doc.get("dataset_source") or {}
    assert ds.get("provider") == "LlamaIndex"
    assert ds.get("project") == "ParseBench"
    assert ds.get("dataset_license") == "Apache-2.0"
    assert ds.get("paper_arxiv_id") == "2604.08538"
    # Reproducibility policy: from Phase B2 the revision must be a
    # 40-character hexadecimal HuggingFace commit SHA (never a mutable
    # branch name). Phase B1 accepted null; from B2 onward the value is
    # locked to a specific SHA that reviewers can trace back to a HF
    # dataset commit.
    rev = ds.get("dataset_revision")
    assert rev is not None, "dataset_revision must be pinned from Phase B2 onwards"
    assert isinstance(rev, str) and len(rev) == 40 and all(c in "0123456789abcdef" for c in rev), (
        f"dataset_revision must be a 40-char hex SHA; got {rev!r}"
    )


def test_every_asset_carries_required_fields() -> None:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for entry in doc["assets"]:
        missing = _REQUIRED_ASSET_FIELDS - set(entry.keys())
        assert not missing, f"asset {entry.get('id')!r} missing fields: {missing}"


def test_every_asset_is_reference_fetch_only() -> None:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for entry in doc["assets"]:
        assert entry["redistribution"] == "reference-fetch-only", (
            f"asset {entry['id']!r} has redistribution={entry['redistribution']!r}; "
            f"Phase B1 forbids any other value. Reclassification to 'direct' requires "
            f"an explicit rights_review_queue entry with permission evidence."
        )


def test_no_mirror_or_checksum_populated_in_phase_b1() -> None:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    for entry in doc["assets"]:
        for field in ("mirror_url", "sha256", "size_bytes", "binary_url"):
            assert entry[field] is None, (
                f"asset {entry['id']!r} has non-null {field}; Phase B1 must NEVER invent "
                f"mirror URLs, checksums, or sizes. Authorised-fetch is a separate PR."
            )


def test_japanese_identity_resolved_to_text_dense_variant() -> None:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    (japanese,) = [e for e in doc["assets"] if e["id"] == "japanese_case"]
    assert japanese["resolved_identity"] == "text_dense__japanese", (
        "Japanese fixture must resolve to text_dense__japanese (per "
        "benchmarks/READINESS_CALIBRATION_DEV_REPORT.md lines 280/303/449/473)."
    )
    assert japanese["filename"] == "text_dense__japanese.pdf"
    assert japanese["hf_repo_path"] == "docs/text/text_dense__japanese.pdf"


def test_rights_review_queue_covers_every_asset() -> None:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        doc = json.load(f)
    asset_ids = {e["id"] for e in doc["assets"]}
    queue_ids = {row["asset_id"] for row in doc.get("rights_review_queue", [])}
    missing = asset_ids - queue_ids
    assert not missing, f"assets not in rights_review_queue: {missing}"


def test_design_document_locks_no_silent_skipping() -> None:
    """The reference-fetcher design must explicitly forbid silent skipping
    of available assets and must document the error-code taxonomy that
    surfaces every terminal state.
    """
    _skip_if_missing(_DESIGN_MD)
    body = _DESIGN_MD.read_text(encoding="utf-8")
    for phrase in [
        "No implementation",
        "No file was downloaded",
        "no silent skipping",
        "AKSHARAMD_PARSEBENCH_ALLOW_NETWORK",
        "reference-fetch-only",
        "prohibited",
        "does not upload",
    ]:
        assert phrase.lower() in body.lower(), (
            f"design doc missing required policy phrase: {phrase!r}"
        )
