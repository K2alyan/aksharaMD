"""Federal Register population enumeration for B1a-5a (2025 calendar year).

Locked anchors:
- publication_date interval: [2025-01-01, 2025-12-31] (completed calendar year)
- types: RULE, PRORULE, NOTICE (each with a 2-doc quota)
- PRESDOCU excluded by design
- volume >= 60 (born-digital era)
- pdf_url present + canonical GovInfo pattern
- NO prove-one 2-50 page bound (adapter-validation-only)
- NO XML-availability requirement (XML is optional G2 support)

All FR API requests go through :mod:`http_policy` so pacing (>= 750 ms
between requests) and bounded 429 backoff are enforced automatically.
Detail requests are as paced as list requests.
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import date
from typing import Any

from benchmarks.eval_v1.acquisition.federal_register_api import (
    FR_LIST_ENDPOINT,
    FrDocumentDetail,
    _parse_detail,
    govinfo_ids_from_pdf_url,
)
from benchmarks.eval_v1.selection.http_policy import make_fr_policy
from benchmarks.eval_v1.selection.pilot_selector import Candidate, make_candidate

POPULATION_PUBLICATION_DATE_GTE = date(2025, 1, 1)
POPULATION_PUBLICATION_DATE_LTE = date(2025, 12, 31)
FR_ELIGIBILITY_RULE_VERSION = "1"

_TYPE_TO_API_ENUM = {
    "Rule": "RULE",
    "Proposed Rule": "PRORULE",
    "Notice": "NOTICE",
}

_ALLOWED_HOSTS = frozenset({"www.federalregister.gov", "www.govinfo.gov"})
_policy = make_fr_policy()


def _require_fr(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in _ALLOWED_HOSTS:
        raise RuntimeError(f"refuse to fetch {url!r}: not on FR/GovInfo allowlist")


def _http_get(url: str) -> bytes:
    _require_fr(url)
    body, _headers = _policy.get(url)
    return body


@dataclass(frozen=True)
class FrPopulationSnapshot:
    publication_date_gte: str
    publication_date_lte: str
    per_type_counts: dict[str, int]


def list_type_in_date_range(
    api_type_enum: str,
    date_gte: date,
    date_lte: date,
    per_page: int = 1000,
) -> list[dict[str, Any]]:
    q = urllib.parse.urlencode(
        {
            "conditions[publication_date][gte]": date_gte.isoformat(),
            "conditions[publication_date][lte]": date_lte.isoformat(),
            "conditions[type][]": api_type_enum,
            "per_page": str(per_page),
        }
    )
    url = f"{FR_LIST_ENDPOINT}?{q}"
    all_rows: list[dict[str, Any]] = []
    while url:
        body = _http_get(url)
        data = json.loads(body)
        for row in data.get("results") or []:
            all_rows.append(row)
        url = data.get("next_page_url")
    return all_rows


def _fetch_detail(document_number: str) -> tuple[FrDocumentDetail, bytes, str]:
    import hashlib

    url = (
        "https://www.federalregister.gov/api/v1/documents/"
        + urllib.parse.quote(document_number, safe="")
        + ".json"
    )
    body = _http_get(url)
    j = json.loads(body)
    return _parse_detail(j), body, hashlib.sha256(body).hexdigest()


def enumerate_stratum_candidates(
    display_type: str,
    api_enum: str,
    excluded_ids: set[str],
    *,
    on_progress=None,
) -> tuple[list[Candidate], int, list[dict[str, Any]]]:
    """Enumerate one stratum. Returns (candidates, n_list_rows, ineligible_head).

    Metadata inspection only. Network failures propagate — never
    silently reweight the stratum by counting a network error as
    ineligibility.
    """
    from benchmarks.eval_v1.corpus_split import Partition, assign_partition

    if on_progress:
        on_progress(display_type, "listing")
    rows = list_type_in_date_range(
        api_enum,
        POPULATION_PUBLICATION_DATE_GTE,
        POPULATION_PUBLICATION_DATE_LTE,
    )
    if on_progress:
        on_progress(display_type, f"listed {len(rows)} rows")

    # DEV pre-filter before per-doc detail requests (partition is a
    # pure function of canonical_id).
    dev_rows = [
        r for r in rows if assign_partition(r["document_number"]) is Partition.DEV
    ]
    dev_rows.sort(key=lambda r: r["document_number"])
    if on_progress:
        on_progress(display_type, f"DEV pre-filter: {len(dev_rows)} rows")

    eligible: list[Candidate] = []
    ineligible: list[dict[str, Any]] = []
    for i, r in enumerate(dev_rows):
        if on_progress and i % 50 == 0:
            on_progress(display_type, f"detail {i}/{len(dev_rows)} eligible={len(eligible)}")
        document_number = r["document_number"]
        if document_number in excluded_ids:
            # Hard exclusion — never counted as metadata ineligibility.
            continue
        # NetworkExhaustionError propagates; do NOT convert to ineligibility.
        detail, _raw, _sha = _fetch_detail(document_number)
        reason = _apply_b1_pilot_filters(detail)
        if reason:
            if len(ineligible) < 200:
                ineligible.append(
                    {
                        "canonical_id": document_number,
                        "stratum": display_type,
                        "reason": reason,
                    }
                )
            continue
        package_id, granule_id = govinfo_ids_from_pdf_url(detail.pdf_url)
        eligible.append(
            make_candidate(
                canonical_id=document_number,
                metadata={
                    "document_number": document_number,
                    "publication_date": detail.publication_date,
                    "type": detail.type,
                    "volume": detail.volume,
                    "citation": detail.citation,
                    "page_length": detail.page_length,
                    "package_id": package_id,
                    "granule_id": granule_id,
                    "has_xml": bool(detail.full_text_xml_url),
                },
                corpus="federal_register",
            )
        )
    return eligible, len(rows), ineligible


def enumerate_eligible_by_stratum(
    excluded_ids: set[str] | None = None,
    on_progress=None,
) -> tuple[dict[str, list[Candidate]], FrPopulationSnapshot, list[dict[str, Any]]]:
    """Return (candidates_by_stratum, snapshot, ineligible_head).

    Hard exclusions are applied per-stratum; they do NOT count as
    metadata ineligibility.
    """
    excluded_ids = excluded_ids or set()
    per_stratum: dict[str, list[Candidate]] = {}
    per_type_counts: dict[str, int] = {}
    all_ineligible: list[dict[str, Any]] = []
    for display_type, api_enum in _TYPE_TO_API_ENUM.items():
        eligible, n_rows, ineligible = enumerate_stratum_candidates(
            display_type, api_enum, excluded_ids, on_progress=on_progress
        )
        per_stratum[display_type] = eligible
        per_type_counts[display_type] = n_rows
        all_ineligible.extend(ineligible)

    snapshot = FrPopulationSnapshot(
        publication_date_gte=POPULATION_PUBLICATION_DATE_GTE.isoformat(),
        publication_date_lte=POPULATION_PUBLICATION_DATE_LTE.isoformat(),
        per_type_counts=per_type_counts,
    )
    return per_stratum, snapshot, all_ineligible


def _apply_b1_pilot_filters(detail: FrDocumentDetail) -> str | None:
    if detail.type not in _TYPE_TO_API_ENUM:
        return f"type_not_in_strata:{detail.type!r}"
    if detail.volume is None:
        return "volume_missing"
    if detail.volume < 60:
        return f"volume_lt_60:{detail.volume}"
    if not detail.pdf_url:
        return "no_pdf_url"
    try:
        govinfo_ids_from_pdf_url(detail.pdf_url)
    except Exception as e:  # noqa: BLE001
        return f"pdf_url_not_govinfo:{type(e).__name__}"
    return None


__all__ = [
    "FR_ELIGIBILITY_RULE_VERSION",
    "POPULATION_PUBLICATION_DATE_GTE",
    "POPULATION_PUBLICATION_DATE_LTE",
    "FrPopulationSnapshot",
    "enumerate_eligible_by_stratum",
    "enumerate_stratum_candidates",
    "list_type_in_date_range",
]
