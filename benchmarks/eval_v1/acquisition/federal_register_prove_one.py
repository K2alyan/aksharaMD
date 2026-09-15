"""Prove-one candidate selection for Federal Register (B1a-4).

Strategy:

1. Compute a **fixed** ``population_publication_date_lte`` = today − 2
   business days. Record it in provenance so reruns replay the same
   query, not a moving window.
2. Choose a pinned closed date range (default: 7 days ending on the
   safe cutoff) so the population is bounded.
3. Enumerate RULE documents in that range via FR API. This is metadata
   inspection ONLY — the ledger is not touched.
4. For each candidate in ``SHA-256(document_number)`` ascending order,
   fetch the per-doc JSON detail (also metadata-only, no contamination),
   apply the locked prove-one filters.
5. First eligible candidate wins. Only then fetch its PDF + XML (the
   contaminating step). Record every scanned + downloaded PMCID... err,
   document_number to the ledger.
6. Write per-doc manifest + selection/acceptance-ready cache under
   ``corpus/eval_v1/federal_register/<document_number>/``.

Split policy: N/A. Federal Register is a naturalistic corpus without
train/test splits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.acquisition.federal_register_api import (
    AcquisitionError,
    acquire_document,
    apply_prove_one_filters,
    compute_safe_cutoff,
    fetch_document_detail,
    govinfo_ids_from_pdf_url,
    list_rules_in_date_range,
)

DISCOVERY_METHOD = "fr-api-v1-rule-window-walk"
DISCOVERY_METHOD_VERSION = "1"


@dataclass
class CandidateOutcome:
    document_number: str
    rank_key: str
    stage: str  # "metadata_filter" | "selected"
    reason: str | None
    volume: int | None = None
    page_length: int | None = None
    pdf_sha256: str | None = None
    xml_sha256: str | None = None


@dataclass
class ProveOneRun:
    discovery_method: str = DISCOVERY_METHOD
    discovery_method_version: str = DISCOVERY_METHOD_VERSION
    discovery_started_utc: str = ""
    discovery_finished_utc: str = ""
    population_publication_date_gte: str = ""
    population_publication_date_lte: str = ""
    n_list_entries: int = 0
    n_details_fetched: int = 0
    n_rejected_pre_fetch: int = 0
    outcomes: list[CandidateOutcome] = field(default_factory=list)
    selected: CandidateOutcome | None = None


def _rank(document_number: str) -> str:
    return hashlib.sha256(document_number.encode("ascii")).hexdigest()


def _append_exclusion_ledger(
    ledger_path: Path,
    outcomes: list[CandidateOutcome],
    selected: CandidateOutcome | None,
    discovery_started_utc: str,
    population_date_lte: str,
) -> None:
    """Only documents whose PDF/XML content was fetched contaminate.

    API metadata scans (list + per-doc detail) do NOT contaminate. Only
    ``stage == "selected"`` fetched PDF/XML bytes and requires a ledger
    entry.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).isoformat(timespec="seconds")
    entries: list[dict[str, Any]] = []
    if selected is not None:
        entries.append(
            {
                "document_number": selected.document_number,
                "usage": "B1a-4 corpus-adapter prove-one (selected)",
                "evaluation_eligibility": "development_only",
                "held_out_v1_eligible": False,
                "b1_pilot_eligible": False,
                "reason": "inspected during evaluation-infrastructure development",
                "pdf_sha256": selected.pdf_sha256,
                "xml_sha256": selected.xml_sha256,
                "recorded_utc": ts,
                "discovery_started_utc": discovery_started_utc,
                "population_publication_date_lte": population_date_lte,
            }
        )
    if not entries:
        return
    with ledger_path.open("a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, sort_keys=True) + "\n")


def run(
    corpus_dir: Path,
    exclusion_ledger: Path,
    report_path: Path,
    *,
    date_lte: date | None = None,
    window_days: int = 7,
    max_details: int = 200,
) -> ProveOneRun:
    """Execute the prove-one pipeline. Returns the run record."""
    result = ProveOneRun(
        discovery_started_utc=datetime.now(tz=UTC).isoformat(timespec="seconds"),
    )

    # Step 1: fixed cutoff.
    cutoff = date_lte if date_lte is not None else compute_safe_cutoff()
    gte = cutoff - timedelta(days=window_days - 1)
    result.population_publication_date_gte = gte.isoformat()
    result.population_publication_date_lte = cutoff.isoformat()
    print(
        f"[prove-one] pinned publication_date range: "
        f"[{result.population_publication_date_gte}, "
        f"{result.population_publication_date_lte}]"
    )

    # Step 2: enumerate.
    entries = list_rules_in_date_range(date_gte=gte, date_lte=cutoff)
    result.n_list_entries = len(entries)
    print(f"[prove-one] FR API returned {len(entries)} RULE entries in window")
    if not entries:
        raise AcquisitionError(
            f"no RULE documents in publication_date window "
            f"[{gte}, {cutoff}]"
        )

    # Step 3: rank deterministically.
    entries_ranked = sorted(entries, key=lambda e: _rank(e.document_number))

    # Step 4: iterate + filter until a winner emerges.
    for entry in entries_ranked:
        if result.n_details_fetched >= max_details:
            print(
                f"[prove-one] max_details={max_details} reached without "
                "selection; stopping."
            )
            break
        rank_key = _rank(entry.document_number)
        try:
            detail, _api_raw, _api_sha = fetch_document_detail(entry.document_number)
        except Exception as e:  # noqa: BLE001
            result.outcomes.append(
                CandidateOutcome(
                    document_number=entry.document_number,
                    rank_key=rank_key,
                    stage="metadata_filter",
                    reason=f"detail_fetch_error:{type(e).__name__}",
                )
            )
            result.n_rejected_pre_fetch += 1
            continue
        result.n_details_fetched += 1
        decision = apply_prove_one_filters(detail)
        if not decision.ok:
            result.outcomes.append(
                CandidateOutcome(
                    document_number=entry.document_number,
                    rank_key=rank_key,
                    stage="metadata_filter",
                    reason=decision.reason,
                    volume=detail.volume,
                    page_length=detail.page_length,
                )
            )
            result.n_rejected_pre_fetch += 1
            continue

        # WINNER — this is the first candidate to fetch PDF/XML bytes.
        # Everything above this line is metadata-only; only from here
        # does the ledger get an entry.
        print(
            f"[prove-one] winner: document_number={entry.document_number} "
            f"(rank_key {rank_key[:12]}..., volume={detail.volume}, "
            f"page_length={detail.page_length})"
        )

        # Re-fetch detail to get raw bytes + sha256 for provenance.
        detail_r, api_raw, api_sha = fetch_document_detail(entry.document_number)
        if detail_r.document_number != detail.document_number:
            raise AcquisitionError(
                f"detail returned inconsistent document_number across two "
                f"fetches: {detail.document_number!r} vs "
                f"{detail_r.document_number!r}"
            )
        package_id, granule_id = govinfo_ids_from_pdf_url(detail_r.pdf_url)

        discovery = {
            "discovery_method": DISCOVERY_METHOD,
            "discovery_method_version": DISCOVERY_METHOD_VERSION,
            "discovery_started_utc": result.discovery_started_utc,
            "candidate_ordering_rule": "SHA-256(document_number) ascending",
            "candidate_rank_key": rank_key,
            "n_list_entries_in_window": len(entries),
            "n_details_fetched_before_selection": result.n_details_fetched,
            "population_publication_date_gte": result.population_publication_date_gte,
            "population_publication_date_lte": result.population_publication_date_lte,
            "govinfo_package_id": package_id,
            "govinfo_granule_id": granule_id,
        }
        acquired = acquire_document(
            detail_r,
            api_raw,
            api_sha,
            root=corpus_dir,
            population_publication_date_gte=gte,
            population_publication_date_lte=cutoff,
            discovery_provenance=discovery,
            selection_role="corpus-adapter prove-one",
        )
        pdf_sha = hashlib.sha256(acquired.pdf_path.read_bytes()).hexdigest()
        xml_sha = hashlib.sha256(acquired.xml_path.read_bytes()).hexdigest()
        result.selected = CandidateOutcome(
            document_number=entry.document_number,
            rank_key=rank_key,
            stage="selected",
            reason=None,
            volume=detail_r.volume,
            page_length=detail_r.page_length,
            pdf_sha256=pdf_sha,
            xml_sha256=xml_sha,
        )
        print(
            f"[prove-one] SELECTED {entry.document_number}: "
            f"pdf_sha256={pdf_sha[:12]}..., xml_sha256={xml_sha[:12]}..."
        )
        break

    result.discovery_finished_utc = datetime.now(tz=UTC).isoformat(timespec="seconds")
    _append_exclusion_ledger(
        exclusion_ledger,
        result.outcomes,
        result.selected,
        result.discovery_started_utc,
        result.population_publication_date_lte,
    )
    _write_report(report_path, result, corpus_dir)
    if result.selected is None:
        raise AcquisitionError(
            f"Federal Register prove-one selection failed: no candidate in "
            f"the pinned window [{gte}, {cutoff}] passed the locked filters "
            f"within max_details={max_details}. Do NOT broaden filters or "
            f"date window without human authorization."
        )
    return result


def _write_report(report_path: Path, result: ProveOneRun, corpus_dir: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "authorization": "B1a-4",
        "role": "corpus-adapter prove-one",
        "distribution": {
            "source": "federal_register_api_v1",
            "population_publication_date_gte": result.population_publication_date_gte,
            "population_publication_date_lte": result.population_publication_date_lte,
        },
        "discovery": {
            "method": result.discovery_method,
            "method_version": result.discovery_method_version,
            "started_utc": result.discovery_started_utc,
            "finished_utc": result.discovery_finished_utc,
            "ordering_rule": "SHA-256(document_number) ascending",
            "n_list_entries_in_window": result.n_list_entries,
            "n_details_fetched_before_selection": result.n_details_fetched,
        },
        "filters": {
            "structural": [
                "type == 'Rule'",
                "volume >= 60  (ground-truth-integrity: pre-1995 issues are digitized scans)",
                "pdf_url present and matches GovInfo canonical FR PDF pattern",
                "full_text_xml_url present (required for prove-one so G2 support path is exercised)",
                "2 <= page_length <= 50  (engineering bound, not a claimed population percentile)",
            ],
            "notes": (
                "PRESDOCU excluded by design. XML is fetched but treated as "
                "G2 adjudication support only, never as G1 oracle."
            ),
        },
        "version_policy": (
            "one document_number -> one document. Population is bounded by "
            "the pinned publication_date range recorded in distribution.*"
        ),
        "counts": {
            "n_rejected_pre_fetch": result.n_rejected_pre_fetch,
            "outcomes": len(result.outcomes),
            "selected": 1 if result.selected else 0,
        },
        "selected": None
        if not result.selected
        else {
            "document_number": result.selected.document_number,
            "rank_key": result.selected.rank_key,
            "volume": result.selected.volume,
            "page_length": result.selected.page_length,
            "pdf_sha256": result.selected.pdf_sha256,
            "xml_sha256": result.selected.xml_sha256,
            "cached_dir": str(corpus_dir / result.selected.document_number),
        },
        "candidate_trail_head": [
            {
                "document_number": o.document_number,
                "rank_key": o.rank_key,
                "stage": o.stage,
                "reason": o.reason,
                "volume": o.volume,
                "page_length": o.page_length,
            }
            for o in result.outcomes[:20]
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"[prove-one] report -> {report_path}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("corpus/eval_v1/federal_register"),
    )
    p.add_argument(
        "--exclusion-ledger",
        type=Path,
        default=Path("docs/evaluation/FEDERAL_REGISTER_EXCLUSION_LEDGER.jsonl"),
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/evaluation/FEDERAL_REGISTER_PROVE_ONE_SELECTION.json"),
    )
    p.add_argument(
        "--date-lte",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Pin the publication_date upper bound (default: today - 2 business days).",
    )
    p.add_argument("--window-days", type=int, default=7)
    p.add_argument("--max-details", type=int, default=200)
    args = p.parse_args(argv)
    run(
        corpus_dir=args.corpus_dir,
        exclusion_ledger=args.exclusion_ledger,
        report_path=args.report,
        date_lte=args.date_lte,
        window_days=args.window_days,
        max_details=args.max_details,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
