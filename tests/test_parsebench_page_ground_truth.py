"""Schema tests for the ParseBench page-level ground truth (Issue #53, phase B5).

Locks in the shape of `page_level_ground_truth` on every asset in
`benchmarks/parsebench_assets.lock.json` and the invariants that make
the annotation reviewer-verifiable:

- Every asset has a non-null, non-empty ground truth.
- `page_count` is a positive integer and matches the length of `pages`.
- Page numbers are `1..page_count` with no duplicates, no gaps, no
  extras.
- Every per-page field carries a value from the allowed enum.
- Damaged pages have non-empty evidence; ambiguous pages have an
  explanation; every page has a confidence value and a
  detector_observability value.
- The `promotion_history` records the ground-truth phase and any
  applied label / defect-kind corrections.
- The promoted sha256 / size_bytes / mirror_url / binary_url /
  dataset_revision remain unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCKFILE = _REPO_ROOT / "benchmarks" / "parsebench_assets.lock.json"
_CHECKSUMS = _REPO_ROOT / "benchmarks" / "parsebench_assets.lock.checksums.json"
_REPORT = _REPO_ROOT / "benchmarks" / "PARSEBENCH_PAGE_GROUND_TRUTH_2026-07-18.md"

_ALLOWED_LAYOUT = {
    "single-column", "two-column", "three-column", "mixed",
    "table-heavy", "image-heavy", "unknown",
}
_ALLOWED_EXTRACTION_STATUS = {"correct", "damaged", "ambiguous", "not-assessable"}
_ALLOWED_DEFECT_KIND_PAGE = {"none", "block-level", "span-level", "mixed", "non-multicolumn", "unknown"}
_ALLOWED_SEVERITY = {"none", "minor", "material", "severe", "unknown"}
_ALLOWED_CONFIDENCE = {"high", "medium", "low"}
_ALLOWED_OBSERVABILITY = {
    "block-level-observable", "span-level-only",
    "not-observable-by-current-detector", "not-applicable", "unknown",
}


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"artefact not present: {path}")


def _load_lockfile() -> dict:
    _skip_if_missing(_LOCKFILE)
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_every_asset_has_page_level_ground_truth() -> None:
    doc = _load_lockfile()
    for entry in doc["assets"]:
        aid = entry["id"]
        gt = entry.get("page_level_ground_truth")
        assert gt is not None, f"asset {aid!r} has null page_level_ground_truth"
        assert isinstance(gt, dict), f"asset {aid!r} page_level_ground_truth must be a dict"
        assert gt.get("review_status") == "complete", (
            f"asset {aid!r} review_status={gt.get('review_status')!r} is not 'complete'"
        )
        pages = gt.get("pages") or []
        assert isinstance(pages, list) and len(pages) > 0, (
            f"asset {aid!r} pages must be a non-empty list"
        )


def test_page_count_matches_pages_length() -> None:
    doc = _load_lockfile()
    for entry in doc["assets"]:
        aid = entry["id"]
        gt = entry["page_level_ground_truth"]
        page_count = gt.get("page_count")
        assert isinstance(page_count, int) and page_count > 0, (
            f"asset {aid!r} page_count must be a positive int, got {page_count!r}"
        )
        assert len(gt["pages"]) == page_count, (
            f"asset {aid!r}: page_count={page_count} but pages array has "
            f"{len(gt['pages'])} entries"
        )


def test_page_numbers_are_contiguous_and_unique() -> None:
    doc = _load_lockfile()
    for entry in doc["assets"]:
        aid = entry["id"]
        gt = entry["page_level_ground_truth"]
        page_nums = [p["page"] for p in gt["pages"]]
        expected = list(range(1, gt["page_count"] + 1))
        assert page_nums == expected, (
            f"asset {aid!r}: pages must be 1..{gt['page_count']} with no gaps "
            f"or duplicates; got {page_nums}"
        )


def test_every_page_has_valid_enum_values() -> None:
    doc = _load_lockfile()
    for entry in doc["assets"]:
        aid = entry["id"]
        for p in entry["page_level_ground_truth"]["pages"]:
            page_no = p["page"]
            assert p.get("layout") in _ALLOWED_LAYOUT, (
                f"{aid} p{page_no}: layout={p.get('layout')!r}"
            )
            assert p.get("extraction_status") in _ALLOWED_EXTRACTION_STATUS, (
                f"{aid} p{page_no}: extraction_status={p.get('extraction_status')!r}"
            )
            assert p.get("defect_kind") in _ALLOWED_DEFECT_KIND_PAGE, (
                f"{aid} p{page_no}: defect_kind={p.get('defect_kind')!r}"
            )
            assert p.get("severity") in _ALLOWED_SEVERITY, (
                f"{aid} p{page_no}: severity={p.get('severity')!r}"
            )
            assert p.get("confidence") in _ALLOWED_CONFIDENCE, (
                f"{aid} p{page_no}: confidence={p.get('confidence')!r}"
            )
            assert p.get("detector_observability") in _ALLOWED_OBSERVABILITY, (
                f"{aid} p{page_no}: detector_observability={p.get('detector_observability')!r}"
            )


def test_damaged_pages_have_non_empty_evidence() -> None:
    doc = _load_lockfile()
    for entry in doc["assets"]:
        aid = entry["id"]
        for p in entry["page_level_ground_truth"]["pages"]:
            if p["extraction_status"] == "damaged":
                ev = p.get("evidence") or ""
                assert ev.strip(), (
                    f"{aid} p{p['page']} status=damaged but evidence is empty"
                )
                # Damaged pages must NOT claim severity=none
                assert p["severity"] in {"minor", "material", "severe"}, (
                    f"{aid} p{p['page']} status=damaged but severity={p['severity']!r}"
                )


def test_ambiguous_pages_include_an_explanation() -> None:
    doc = _load_lockfile()
    for entry in doc["assets"]:
        aid = entry["id"]
        for p in entry["page_level_ground_truth"]["pages"]:
            if p["extraction_status"] == "ambiguous":
                ev = p.get("evidence") or ""
                assert ev.strip(), (
                    f"{aid} p{p['page']} status=ambiguous but evidence is empty"
                )


def test_every_page_has_confidence_and_observability() -> None:
    doc = _load_lockfile()
    for entry in doc["assets"]:
        for p in entry["page_level_ground_truth"]["pages"]:
            assert p.get("confidence"), f"{entry['id']} p{p['page']} missing confidence"
            assert p.get("detector_observability"), (
                f"{entry['id']} p{p['page']} missing detector_observability"
            )


def test_promotion_history_records_phase_b5() -> None:
    doc = _load_lockfile()
    hist = doc.get("promotion_history") or []
    b5_entries = [h for h in hist if str(h.get("phase", "")).startswith("B5")]
    assert b5_entries, "promotion_history missing B5 (page-level ground truth) entry"
    (b5,) = b5_entries
    assert b5.get("assets_annotated") == 12
    corrections = b5.get("corrections") or []
    # Each correction must state a reason
    for c in corrections:
        assert c.get("reason", "").strip(), (
            f"correction for {c.get('asset_id')!r} lacks a reason"
        )


def test_promoted_hashes_and_sizes_unchanged() -> None:
    """Phase B5 must not touch the promoted checksums or sizes."""
    _skip_if_missing(_CHECKSUMS)
    with _CHECKSUMS.open("r", encoding="utf-8") as f:
        chk = json.load(f)
    doc = _load_lockfile()
    cap_by_id = {c["asset_id"]: c for c in chk["captures"]}
    for entry in doc["assets"]:
        aid = entry["id"]
        cap = cap_by_id[aid]
        assert entry["sha256"] == cap["sha256"], (
            f"{aid}: promoted sha256 was mutated by the ground-truth PR"
        )
        assert entry["size_bytes"] == cap["size_bytes"], (
            f"{aid}: promoted size_bytes was mutated"
        )
        assert entry["mirror_url"] is None, f"{aid}: mirror_url must remain null"
        assert entry["binary_url"] is None, f"{aid}: binary_url must remain null"


def test_no_pdf_files_added_to_git() -> None:
    """This PR must not introduce any PDF into the repo tree."""
    for pdf in _REPO_ROOT.rglob("*.pdf"):
        rel = pdf.relative_to(_REPO_ROOT).as_posix()
        # The public corpus (benchmarks/.public_corpus/pdf/**) is
        # legitimately in the tree from earlier work. Nothing outside it.
        assert rel.startswith("benchmarks/.public_corpus/pdf/"), (
            f"unexpected PDF added to git: {rel}"
        )


def test_report_markdown_mentions_key_phrases() -> None:
    _skip_if_missing(_REPORT)
    body = _REPORT.read_text(encoding="utf-8")
    for phrase in [
        "12 assets",
        "reviewed",
        "page_level_ground_truth",
        "SCORING_POLICY_VERSION",
        "No PDF bytes",
    ]:
        assert phrase in body, f"page-GT report missing required phrase: {phrase!r}"


def test_dataset_revision_and_lockfile_still_pinned() -> None:
    doc = _load_lockfile()
    ds = doc.get("dataset_source") or {}
    rev = ds.get("dataset_revision")
    assert isinstance(rev, str) and len(rev) == 40, (
        f"dataset_revision must remain a 40-char SHA; got {rev!r}"
    )


def test_five_expected_corrections_recorded() -> None:
    """Phase B5 applied exactly five defect_kind corrections (documented
    in the report). If the count changes, the report body must be revised.
    """
    doc = _load_lockfile()
    hist = doc["promotion_history"]
    (b5,) = [h for h in hist if str(h.get("phase", "")).startswith("B5")]
    corrections = b5.get("corrections") or []
    assert len(corrections) == 5, (
        f"expected 5 corrections in phase B5; got {len(corrections)}"
    )
    corrected_ids = {c["asset_id"] for c in corrections}
    expected_ids = {"3colpres", "ikea3", "letter3", "myctophidae", "japanese_case"}
    assert corrected_ids == expected_ids, (
        f"correction asset-id set drift: {corrected_ids} vs expected {expected_ids}"
    )
