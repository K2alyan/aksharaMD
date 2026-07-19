"""Eligibility filters, confusion-matrix computation, and changed-decision
diffing for the multicolumn recalibration harness (Issue #50 follow-up).

Pure functions only. No detector, parser, or scoring code is imported.
No file I/O. Every helper consumes plain dicts / lists so the unit
tests can exercise the invariants without a compile step.

The four eligibility slices, all derived from per-asset records:

* ``doc_historical`` — assets that carry a frozen document-level label
  (``expected_positive`` is ``True`` or ``False``). Excludes only
  assets whose label author refused to attest a positive/negative
  verdict (``expected_positive is None``).
* ``doc_reviewer_confirmed`` — assets whose document-level label is
  attested AND whose reviewed page-level ground truth is NOT
  ambiguous AND whose defect class is not ``non-multicolumn``. This
  is the primary decision-making slice for the multicolumn detector.
* ``page`` — pages that are individually reviewer-approved:
  ``review_status == "complete"`` AND ``extraction_status !=
  "ambiguous"`` AND the asset's ``defect_kind != "non-multicolumn"``.
* ``observable`` — the subset of ``page`` where the reviewer recorded
  ``detector_observability == "block-level-observable"``. This is the
  honest recall corpus for the current block-level multicolumn
  detector.

The public corpus (frozen at commit ``a90f0f7``) has no page-level
review, so page-level and observable metrics are ParseBench-only. The
historical document view exists for both corpora. The reviewer-confirmed
document view exists only for ParseBench (the public labels do not carry
a ``defect_kind``; every non-null public label is treated as
multicolumn-relevant per the phase-1 protocol).
"""
from __future__ import annotations

from typing import Any

# ── Eligibility ──────────────────────────────────────────────────────────


def parsebench_doc_historical(lockfile: dict) -> dict[str, bool]:
    """Return {asset_id: expected_positive} where expected_positive is
    True/False. Assets with null ``expected_label`` are excluded.

    ``expected_label`` uses the string form ``"true-positive"`` /
    ``"true-negative"`` in the lockfile; convert to a boolean expected.
    """
    out: dict[str, bool] = {}
    for e in lockfile["assets"]:
        lab = e.get("expected_label")
        if lab == "true-positive":
            out[e["id"]] = True
        elif lab == "true-negative":
            out[e["id"]] = False
        # else: excluded from historical view
    return out


def parsebench_doc_reviewer_confirmed(lockfile: dict) -> dict[str, bool]:
    """Return {asset_id: expected_positive} for the reviewer-confirmed
    multicolumn corpus.

    Exclusion rules:

    - ``expected_label`` must be attested (``true-positive`` or
      ``true-negative``).
    - ``defect_kind`` must NOT be ``non-multicolumn``.
    - No reviewed page may carry ``extraction_status == "ambiguous"``.

    ``ikea3`` is excluded because it satisfies BOTH the ambiguous and
    the non-multicolumn exclusion. ``simple2`` and ``text_dense__de``
    are excluded because at least one reviewed page is ambiguous.
    ``letter3`` / ``myctophidae`` / ``japanese_case`` are excluded
    because their ``defect_kind`` is ``non-multicolumn``.
    """
    out: dict[str, bool] = {}
    for e in lockfile["assets"]:
        lab = e.get("expected_label")
        if lab not in ("true-positive", "true-negative"):
            continue
        if e.get("defect_kind") == "non-multicolumn":
            continue
        gt = e.get("page_level_ground_truth") or {}
        pages = gt.get("pages") or []
        if any((p.get("extraction_status") == "ambiguous") for p in pages):
            continue
        out[e["id"]] = (lab == "true-positive")
    return out


def parsebench_page_eligibility(lockfile: dict) -> list[dict[str, Any]]:
    """Return one row per reviewer-eligible page.

    Each row: ``{asset, page, expected_positive, observability}`` where
    ``expected_positive`` reflects the page's own ``defect_kind``-derived
    truth (``True`` if this page is a real multicolumn positive,
    ``False`` if a control/negative), and ``observability`` is the raw
    reviewer-recorded value so the observable slice can filter on it.

    Ambiguous pages and pages under ``defect_kind == "non-multicolumn"``
    are omitted entirely — they do not participate as negatives.
    """
    out: list[dict[str, Any]] = []
    for e in lockfile["assets"]:
        if e.get("defect_kind") == "non-multicolumn":
            continue
        gt = e.get("page_level_ground_truth") or {}
        if gt.get("review_status") != "complete":
            continue
        for p in gt.get("pages") or []:
            if p.get("extraction_status") == "ambiguous":
                continue
            out.append({
                "asset": e["id"],
                "page": p["page"],
                "expected_positive": (p.get("extraction_status") == "damaged"),
                "observability": p.get("detector_observability"),
                "defect_kind": e.get("defect_kind"),
            })
    return out


def parsebench_observable_eligibility(lockfile: dict) -> list[dict[str, Any]]:
    """Subset of ``parsebench_page_eligibility`` restricted to pages the
    block-level detector can actually see.
    """
    return [
        row for row in parsebench_page_eligibility(lockfile)
        if row["observability"] == "block-level-observable"
    ]


def public_doc_historical(labels_map: dict) -> dict[str, bool]:
    """Return {label_key: expected_positive} for public-corpus entries
    whose author attested a positive/negative verdict.

    ``labels_map`` is ``labels_doc["labels"]`` from
    ``multicolumn_recalibration_labels.json``. The key format matches
    the manifest's own keys (e.g., ``"001-trivial/minimal-document.pdf"``).
    """
    out: dict[str, bool] = {}
    for key, entry in labels_map.items():
        ep = entry.get("expected_positive")
        if ep is True:
            out[key] = True
        elif ep is False:
            out[key] = False
    return out


# ── Confusion matrix ─────────────────────────────────────────────────────


def confusion(
    per_row_verdicts: list[tuple[str, bool, bool]],
) -> dict[str, Any]:
    """Compute TP/FP/TN/FN + precision/recall/F1/FPR from a list of
    ``(id, expected_positive, predicted_positive)`` rows.

    Rows whose ``expected_positive`` is ``None`` must be filtered by
    the caller (eligibility) — this function does not silently drop
    them. If a caller passes a ``None``-expected row, we treat it as
    a programming error.
    """
    tp = fp = tn = fn = 0
    for _id, expected, predicted in per_row_verdicts:
        if expected is None:
            raise ValueError(f"confusion() received None expected for {_id!r}")
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif (not expected) and predicted:
            fp += 1
        elif (not expected) and not predicted:
            tn += 1
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return {
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "n": tp + fp + tn + fn,
        "precision": round(p, 4),
        "recall": round(r, 4),
        "false_positive_rate": round(fpr, 4),
        "f1": round(f1, 4),
    }


# ── Changed-decision log ─────────────────────────────────────────────────


def changed_decisions(
    baseline_verdicts: dict[str, bool],
    candidate_verdicts: dict[str, bool],
    *,
    baseline_signals: dict[str, dict[str, Any]] | None = None,
    candidate_signals: dict[str, dict[str, Any]] | None = None,
    eligibility_by_id: dict[str, str] | None = None,
    exclusion_reason_by_id: dict[str, str] | None = None,
    scope: str = "document",
) -> list[dict[str, Any]]:
    """Return one row per id where the candidate verdict differs from
    baseline. Each row records everything a reviewer needs to audit the
    flip:

    - ``id``, ``scope`` (``"document"`` or ``"page"``)
    - ``baseline`` and ``candidate`` verdicts
    - ``baseline_signals`` and ``candidate_signals`` if supplied
    - ``ground_truth_eligibility`` (which slice this id was scored in;
      empty string if scored in none)
    - ``exclusion_reason`` (why this id was excluded; only set when
      ``ground_truth_eligibility`` is empty)
    - ``candidate_reason`` — a stub for the caller to fill with a
      candidate-specific narrative. Left empty by this helper; the
      caller populates from its own rule catalogue.

    ``baseline_verdicts`` and ``candidate_verdicts`` must have the same
    key set. Any id present in one but not the other is a programming
    error and raises.
    """
    if set(baseline_verdicts) != set(candidate_verdicts):
        raise ValueError("baseline and candidate verdicts must share the same id set")
    baseline_signals = baseline_signals or {}
    candidate_signals = candidate_signals or {}
    eligibility_by_id = eligibility_by_id or {}
    exclusion_reason_by_id = exclusion_reason_by_id or {}

    out: list[dict[str, Any]] = []
    for _id in sorted(baseline_verdicts):
        b = baseline_verdicts[_id]
        c = candidate_verdicts[_id]
        if b == c:
            continue
        elig = eligibility_by_id.get(_id, "")
        row = {
            "id": _id,
            "scope": scope,
            "baseline": bool(b),
            "candidate": bool(c),
            "flip": "silenced" if b and not c else "raised",
            "baseline_signals": baseline_signals.get(_id, {}),
            "candidate_signals": candidate_signals.get(_id, {}),
            "ground_truth_eligibility": elig,
            "exclusion_reason": exclusion_reason_by_id.get(_id, "") if not elig else "",
            "candidate_reason": "",
            "affects_document_verdict": scope == "document",
            "page_noise_only": scope == "page",
        }
        out.append(row)
    return out


# ── Verdict aggregation ──────────────────────────────────────────────────


def document_verdict_from_pages(
    page_analyses: list[dict[str, Any]],
    rule_fn,
) -> bool:
    """Document-level verdict: warn if any eligible page warns. This
    matches the phase-2 harness's ``_document_warn`` semantics.
    """
    return any(rule_fn(a) for a in page_analyses)
