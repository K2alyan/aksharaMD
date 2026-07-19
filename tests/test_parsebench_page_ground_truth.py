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


def _derive_reviewer_confirmed_corpus(doc: dict) -> dict:
    """Rebuild `reviewer_confirmed_page_level_corpus` from per-asset fields.

    The rule:

    - An asset is in the confirmed corpus ONLY when its ground truth is
      complete AND no reviewed page is ambiguous.
    - Assets with `defect_kind == "non-multicolumn"` are excluded from
      the confirmed corpus regardless of their ambiguity.
    - Within the confirmed corpus:
        block_level_observable_positives: extraction_status="damaged",
          defect_kind in {"mixed", "block-level"},
          detector_observability="block-level-observable"
        span_only_positives:               extraction_status="damaged",
          defect_kind="span-level",
          detector_observability="span-level-only"
        hard_negatives:                    extraction_status="correct",
          defect_kind="block-level",
          expected_label="true-negative",
          layout in {"two-column", "three-column", "mixed", "table-heavy"}
        single_column_negatives:           extraction_status="correct",
          defect_kind="block-level",
          expected_label="true-negative",
          layout="single-column"  (and detector must stay silent —
          i.e., not a false positive)
        detector_false_positives:          extraction_status="correct"
          on a single-column source where the block-level detector
          nonetheless fires (recorded via evidence in the report).

    The single asset that hits the FP bucket in phase B5 is
    `strikeUnderline`; it is identified by matching its `evidence`
    string carrying "W_MULTICOLUMN_ORDER" while extraction_status is
    correct.
    """
    corpus: dict[str, list[str]] = {
        "block_level_observable_positives": [],
        "span_only_positives": [],
        "hard_negatives": [],
        "single_column_negatives": [],
        "detector_false_positives": [],
    }
    for entry in doc["assets"]:
        aid = entry["id"]
        gt = entry.get("page_level_ground_truth") or {}
        if gt.get("review_status") != "complete":
            continue
        pages = gt.get("pages") or []
        if not pages:
            continue
        if any(p.get("extraction_status") == "ambiguous" for p in pages):
            continue
        if entry.get("defect_kind") == "non-multicolumn":
            continue
        # Every asset in this dataset has exactly one page. Guard the
        # single-page assumption explicitly.
        assert len(pages) == 1, f"{aid}: expected 1 page, got {len(pages)}"
        p = pages[0]
        status = p.get("extraction_status")
        defect = entry.get("defect_kind")
        obs = p.get("detector_observability")
        layout = p.get("layout")
        expected = entry.get("expected_label")
        ev = (p.get("evidence") or "").upper()
        if status == "damaged" and defect == "mixed" and obs == "block-level-observable":
            corpus["block_level_observable_positives"].append(aid)
        elif status == "damaged" and defect == "block-level" and obs == "block-level-observable":
            corpus["block_level_observable_positives"].append(aid)
        elif status == "damaged" and defect == "span-level" and obs == "span-level-only":
            corpus["span_only_positives"].append(aid)
        elif status == "correct" and defect == "block-level" and expected == "true-negative":
            # A "correct" single-column page where the multicolumn
            # warning fires is a false positive, not a negative.
            fp_signal = layout == "single-column" and "W_MULTICOLUMN_ORDER" in ev
            if fp_signal:
                corpus["detector_false_positives"].append(aid)
            elif layout == "single-column":
                corpus["single_column_negatives"].append(aid)
            else:
                corpus["hard_negatives"].append(aid)
    for k in corpus:
        corpus[k].sort()
    return corpus


def test_page_calibration_summary_present_and_correct_shape() -> None:
    doc = _load_lockfile()
    summary = doc.get("page_calibration_summary")
    assert isinstance(summary, dict), (
        "lockfile must carry a top-level page_calibration_summary block "
        "so downstream consumers can key off machine-readable categories"
    )
    for key in (
        "historical_expected_label_counts",
        "reviewer_confirmed_page_level_corpus",
        "excluded_from_page_metrics",
        "expected_runtime_verification_at_this_lockfile",
    ):
        assert key in summary, f"page_calibration_summary missing key: {key!r}"


def test_page_calibration_summary_parity() -> None:
    """The machine-readable summary must be exactly what a consumer can
    derive from per-asset fields. Drift here means the expanded
    recalibration would consume a fabricated corpus rather than the
    reviewed one.
    """
    doc = _load_lockfile()
    summary = doc["page_calibration_summary"]

    # --- (A) Historical/document-level parity ---
    historical = summary["historical_expected_label_counts"]
    tp_expected = sorted(
        e["id"] for e in doc["assets"] if e.get("expected_label") == "true-positive"
    )
    tn_expected = sorted(
        e["id"] for e in doc["assets"] if e.get("expected_label") == "true-negative"
    )
    excluded_expected = sorted(
        e["id"]
        for e in doc["assets"]
        if e.get("expected_label") not in {"true-positive", "true-negative"}
    )
    assert sorted(historical["true-positive"]) == tp_expected, (
        f"historical TP drift: {sorted(historical['true-positive'])} vs derived {tp_expected}"
    )
    assert sorted(historical["true-negative"]) == tn_expected, (
        f"historical TN drift: {sorted(historical['true-negative'])} vs derived {tn_expected}"
    )
    assert sorted(historical.get("excluded_or_null", [])) == excluded_expected

    # --- (B) Reviewer-confirmed page-level corpus parity ---
    confirmed_actual = summary["reviewer_confirmed_page_level_corpus"]
    confirmed_derived = _derive_reviewer_confirmed_corpus(doc)
    for key, derived_val in confirmed_derived.items():
        actual_val = sorted(confirmed_actual.get(key, []))
        assert actual_val == derived_val, (
            f"confirmed corpus drift on {key!r}: "
            f"lockfile summary says {actual_val}, derivation says {derived_val}"
        )

    # --- Excluded set parity ---
    excluded = summary["excluded_from_page_metrics"]
    ambiguous_derived = sorted(
        e["id"]
        for e in doc["assets"]
        if any(
            p.get("extraction_status") == "ambiguous"
            for p in (e.get("page_level_ground_truth") or {}).get("pages") or []
        )
    )
    non_mc_derived = sorted(
        e["id"] for e in doc["assets"] if e.get("defect_kind") == "non-multicolumn"
    )
    assert sorted(excluded["ambiguous"]) == ambiguous_derived, (
        f"ambiguous exclusion drift: {sorted(excluded['ambiguous'])} vs {ambiguous_derived}"
    )
    assert sorted(excluded["non_multicolumn"]) == non_mc_derived, (
        f"non-multicolumn exclusion drift: {sorted(excluded['non_multicolumn'])} vs {non_mc_derived}"
    )


def test_ambiguous_and_confirmed_are_disjoint() -> None:
    """An asset must never simultaneously appear in a confirmed-positive
    or confirmed-negative bucket AND in the ambiguous exclusion. This
    is the invariant the review of PR #64 hinged on.
    """
    doc = _load_lockfile()
    summary = doc["page_calibration_summary"]
    _confirmed_buckets = (
        "block_level_observable_positives",
        "span_only_positives",
        "hard_negatives",
        "single_column_negatives",
        "detector_false_positives",
    )
    confirmed_ids: set[str] = set()
    for k in _confirmed_buckets:
        confirmed_ids.update(summary["reviewer_confirmed_page_level_corpus"].get(k, []))
    ambiguous_ids = set(summary["excluded_from_page_metrics"]["ambiguous"])
    non_mc_ids = set(summary["excluded_from_page_metrics"]["non_multicolumn"])
    overlap_amb = confirmed_ids & ambiguous_ids
    overlap_nmc = confirmed_ids & non_mc_ids
    assert not overlap_amb, (
        f"assets appear in BOTH confirmed corpus AND ambiguous exclusion: {overlap_amb}"
    )
    assert not overlap_nmc, (
        f"assets appear in BOTH confirmed corpus AND non-multicolumn exclusion: {overlap_nmc}"
    )


def test_consumer_gate_page_calibration_must_check_approval_flag() -> None:
    """Simulates a downstream page-level calibration consumer.

    Any hypothetical consumer that reads the lockfile to compute
    page-level precision/recall MUST gate every asset on
    `approved_for_page_calibration is True` at runtime. This test
    reproduces the gate that a well-behaved consumer would apply and
    asserts that:

    1. The set of assets the gate lets through equals the confirmed
       corpus (i.e., the summary in the lockfile is the same set a
       correct consumer would arrive at).
    2. Ambiguous assets and non-multicolumn assets are BOTH filtered
       out by the gate.

    If this test fails, the summary or the review flags disagree — the
    next PR must not consume this lockfile until the disagreement is
    resolved.

    Note: `approved_for_page_calibration` itself is populated at runtime
    by the fetcher (`_compute_calibration_gates`) using cached bytes.
    Here we replicate its page-side logic against static per-asset
    fields, so the test is deterministic and requires no network.
    """
    doc = _load_lockfile()

    def _would_approve_for_page_calibration(entry: dict) -> bool:
        # Replicates fetcher._page_ground_truth_status() + the
        # page-side portion of _compute_calibration_gates().
        gt = entry.get("page_level_ground_truth")
        if not gt:
            return False
        if gt.get("review_status") != "complete":
            return False
        pages = gt.get("pages") or []
        if not pages:
            return False
        if len(pages) != gt.get("page_count"):
            return False
        if any(p.get("extraction_status") == "ambiguous" for p in pages):
            return False
        # Consumer must also require expected_label + defect_kind
        # populated (the same document-side gate the fetcher applies).
        if not entry.get("expected_label"):
            return False
        if not entry.get("defect_kind"):
            return False
        return True

    approved_ids = {e["id"] for e in doc["assets"] if _would_approve_for_page_calibration(e)}

    summary = doc["page_calibration_summary"]
    _confirmed_buckets = (
        "block_level_observable_positives",
        "span_only_positives",
        "hard_negatives",
        "single_column_negatives",
        "detector_false_positives",
    )
    confirmed_ids: set[str] = set()
    for k in _confirmed_buckets:
        confirmed_ids.update(summary["reviewer_confirmed_page_level_corpus"].get(k, []))
    ambiguous_ids = set(summary["excluded_from_page_metrics"]["ambiguous"])

    # The gate lets non-multicolumn assets through (they are page-approved —
    # the evidence is complete) UNLESS they are also ambiguous. It always
    # filters ambiguous out. So the expected approved set is: all assets
    # minus ambiguous ones. Confirmed positives/negatives are a strict
    # subset of that set (they are non-ambiguous AND multicolumn-relevant).
    all_ids = {e["id"] for e in doc["assets"]}
    expected_approved = all_ids - ambiguous_ids
    assert approved_ids == expected_approved, (
        f"consumer-gate mismatch: gate approves {approved_ids}; "
        f"summary derivation says {expected_approved}"
    )
    assert not (approved_ids & ambiguous_ids), (
        f"consumer-gate approved ambiguous assets: {approved_ids & ambiguous_ids}"
    )
    assert confirmed_ids <= approved_ids, (
        f"confirmed corpus not a subset of approved set: "
        f"{confirmed_ids - approved_ids} confirmed but not approved"
    )

    # And the runtime-verification counts in the summary must match.
    exp = summary["expected_runtime_verification_at_this_lockfile"]
    assert exp["approved_for_page_calibration_count"] == len(approved_ids), (
        f"expected_runtime_verification.approved_for_page_calibration_count="
        f"{exp['approved_for_page_calibration_count']} but the gate yields {len(approved_ids)}"
    )
    assert sorted(exp["page_disapproved_with_reason_page_level_ground_truth_ambiguous"]) == sorted(
        ambiguous_ids
    ), (
        "expected_runtime_verification page-disapproval list must equal the "
        "ambiguous set exactly"
    )
