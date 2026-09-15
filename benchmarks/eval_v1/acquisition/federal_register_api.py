"""Federal Register acquisition against the FR API v1 + GovInfo cross-check.

The offline adapter
(``benchmarks/eval_v1/adapters/federal_register_v1.py``) never opens a
socket. This module is the operator-side script that populates
``corpus/eval_v1/federal_register/<document_number>/`` and writes the
per-document ``manifest.json`` provenance receipt.

Identity contract (locked, B1a-4)
---------------------------------

**A Federal Register document identity is:**

  (document_number, publication_date, package_id, granule_id, pdf_sha256)

- ``document_number`` is the OFR-assigned FR Doc Number (e.g.,
  ``2024-31234``). Not monotonic — do not parse for ordering.
- ``package_id`` = GovInfo issue package (``FR-YYYY-MM-DD``) parsed
  from ``pdf_url``.
- ``granule_id`` = ``document_number`` for post-2010 docs; older docs
  use ``E9-...`` style IDs.
- Both FR-API and GovInfo identifiers are recorded so provenance can
  be cross-verified against either source of truth.

Corpus role — LOCKED for B1a-4
------------------------------

- **G2 FPR-baseline only.** Federal Register is the naturalistic
  no-failure-expected corpus. XML/HTML/plaintext are G2 adjudication
  support only — never G1 text oracle. The adapter enforces this by
  refusing to emit an ``oracle_text`` field.
- The parser source is the **PDF** (GPO's composed authoritative
  record). ``PDF and Text versions of Federal Register content on GPO
  Access and FDsys have legal status as parts of the official online
  format`` — FR-XML User Guide.

Locked pre-filters (§9 of the recon report)
-------------------------------------------

- ``type == "Rule"`` for prove-one (mapped from API request
  ``conditions[type][]=RULE``). PRESDOCU explicitly excluded.
- ``volume >= 60`` (1995 onward). Earlier issues are digitized scans;
  including them would confound the FPR baseline.
- Both ``pdf_url`` and ``full_text_xml_url`` non-null (XML is
  adjudication support only, but its presence is required for prove-one
  so the full G2 reviewer-support path is exercised).
- Engineering bound: ``2 <= page_length <= 50``. Not a claimed
  population percentile — just avoids trivial and mega-rules for the
  prove-one.

Publication-lag safety
----------------------

For prove-one, the operational safety rule is "T - 2 business days at
query time." The literal cutoff date is recorded in the manifest as
``population_publication_date_lte`` so reruns replay the same query
against the same fixed date — not a moving window.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

FR_API_HOST = "https://www.federalregister.gov"
FR_LIST_ENDPOINT = f"{FR_API_HOST}/api/v1/documents.json"
FR_DOC_ENDPOINT = f"{FR_API_HOST}/api/v1/documents"
GOVINFO_HOST = "https://www.govinfo.gov"
USER_AGENT = "aksharamd-eval-v1/1.0 (+contact: ksrkklabs@gmail.com)"
MANIFEST_SCHEMA_VERSION = "1"

_ALLOWED_HOSTS = frozenset({"www.federalregister.gov", "www.govinfo.gov"})
_ALLOWED_SCHEMES = frozenset({"https"})

# Extracts (package_id, granule_id) from
# https://www.govinfo.gov/content/pkg/FR-YYYY-MM-DD/pdf/<document_number>.pdf
_PDF_URL_RE = re.compile(
    r"^https://www\.govinfo\.gov/content/pkg/(?P<package_id>FR-\d{4}-\d{2}-\d{2})/pdf/(?P<granule_id>[^/]+)\.pdf$"
)


class AcquisitionError(RuntimeError):
    """Identity / protocol violation during Federal Register acquisition."""


# --- HTTP -----------------------------------------------------------


def _require_fr_url(url: str) -> None:
    """Fail closed on any URL outside the FR-API + GovInfo allowlist."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise AcquisitionError(
            f"refuse to fetch {url!r}: scheme {parsed.scheme!r} not in "
            f"allowlist {sorted(_ALLOWED_SCHEMES)}"
        )
    if parsed.netloc not in _ALLOWED_HOSTS:
        raise AcquisitionError(
            f"refuse to fetch {url!r}: host {parsed.netloc!r} not in "
            f"allowlist {sorted(_ALLOWED_HOSTS)}"
        )


def http_get_fr(url: str, *, timeout: int = 60) -> tuple[bytes, dict[str, str]]:
    _require_fr_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # nosec B310: URL scheme + host validated by _require_fr_url above.
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.read(), headers


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --- Publication-lag safety ----------------------------------------


def compute_safe_cutoff(today: date | None = None, business_days: int = 2) -> date:
    """Return the latest publication_date that has had time for XML + GovInfo
    packaging to settle (roughly ``today - business_days`` weekdays).

    This is the OPERATIONAL rule; the actual date returned should be
    frozen into the manifest so reruns replay a fixed cutoff, not a
    moving window (see ``population_publication_date_lte``).
    """
    d = today or datetime.now(tz=UTC).date()
    stepped = 0
    while stepped < business_days:
        d = d - timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri (public holidays are ignored — safety only)
            stepped += 1
    return d


# --- API models -----------------------------------------------------


@dataclass(frozen=True)
class FrListEntry:
    """One row of the FR API ``documents.json`` list response.

    List responses omit many fields (volume, page_length,
    full_text_xml_url, etc.). Those require fetching the per-doc
    detail via :func:`fetch_document_detail`.
    """

    document_number: str
    publication_date: str
    type: str  # "Rule" | "Proposed Rule" | "Notice" | "Presidential Document"
    title: str
    pdf_url: str
    html_url: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class FrDocumentDetail:
    """Per-document detail from ``/api/v1/documents/{document_number}.json``."""

    document_number: str
    citation: str | None
    publication_date: str
    volume: int | None
    type: str
    title: str
    action: str | None
    abstract: str | None
    agencies: tuple[dict[str, Any], ...]
    start_page: int | None
    end_page: int | None
    page_length: int | None
    docket_ids: tuple[str, ...]
    cfr_references: tuple[dict[str, Any], ...]
    pdf_url: str
    full_text_xml_url: str | None
    body_html_url: str | None
    raw_text_url: str | None
    mods_url: str | None
    html_url: str
    json_url: str | None
    executive_order_number: int | None
    presidential_document_number: str | None
    raw: dict[str, Any]


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_list_entry(row: dict[str, Any]) -> FrListEntry:
    return FrListEntry(
        document_number=row["document_number"],
        publication_date=row["publication_date"],
        type=row.get("type", ""),
        title=row.get("title", ""),
        pdf_url=row.get("pdf_url", ""),
        html_url=row.get("html_url", ""),
        raw=row,
    )


def _parse_detail(j: dict[str, Any]) -> FrDocumentDetail:
    return FrDocumentDetail(
        document_number=j["document_number"],
        citation=j.get("citation"),
        publication_date=j["publication_date"],
        volume=_to_int_or_none(j.get("volume")),
        type=j.get("type", ""),
        title=j.get("title", ""),
        action=j.get("action"),
        abstract=j.get("abstract"),
        agencies=tuple(j.get("agencies") or ()),
        start_page=_to_int_or_none(j.get("start_page")),
        end_page=_to_int_or_none(j.get("end_page")),
        page_length=_to_int_or_none(j.get("page_length")),
        docket_ids=tuple(j.get("docket_ids") or ()),
        cfr_references=tuple(j.get("cfr_references") or ()),
        pdf_url=j.get("pdf_url", ""),
        full_text_xml_url=j.get("full_text_xml_url"),
        body_html_url=j.get("body_html_url"),
        raw_text_url=j.get("raw_text_url"),
        mods_url=j.get("mods_url"),
        html_url=j.get("html_url", ""),
        json_url=j.get("json_url"),
        executive_order_number=_to_int_or_none(j.get("executive_order_number")),
        presidential_document_number=j.get("presidential_document_number"),
        raw=j,
    )


def list_rules_in_date_range(
    *,
    date_gte: date,
    date_lte: date,
    per_page: int = 100,
) -> list[FrListEntry]:
    """Enumerate RULE documents in a pinned date range.

    Metadata inspection (this call + fetch_document_detail) does NOT
    contaminate — the enumeration surface never touches PDF/XML
    document bytes.
    """
    q = urllib.parse.urlencode(
        {
            "conditions[publication_date][gte]": date_gte.isoformat(),
            "conditions[publication_date][lte]": date_lte.isoformat(),
            "conditions[type][]": "RULE",
            "per_page": str(per_page),
        }
    )
    all_entries: list[FrListEntry] = []
    url = f"{FR_LIST_ENDPOINT}?{q}"
    while url:
        body, _headers = http_get_fr(url)
        data = json.loads(body)
        for row in data.get("results") or []:
            all_entries.append(_parse_list_entry(row))
        url = data.get("next_page_url")
    return all_entries


def fetch_document_detail(document_number: str) -> tuple[FrDocumentDetail, bytes, str]:
    """Fetch per-document JSON. Returns (parsed, raw_bytes, sha256).

    Metadata inspection only — does not contaminate. The raw_bytes /
    sha256 are recorded on the manifest so downstream code can verify
    the exact API snapshot consumed.
    """
    url = f"{FR_DOC_ENDPOINT}/{urllib.parse.quote(document_number, safe='')}.json"
    body, _headers = http_get_fr(url)
    j = json.loads(body)
    return _parse_detail(j), body, _sha256(body)


# --- GovInfo cross-check --------------------------------------------


def govinfo_ids_from_pdf_url(pdf_url: str) -> tuple[str, str]:
    """Parse (package_id, granule_id) from a GovInfo FR PDF URL.

    Raises AcquisitionError on any URL not matching the canonical
    GovInfo FR pattern so we never silently mislabel provenance.
    """
    m = _PDF_URL_RE.match(pdf_url)
    if not m:
        raise AcquisitionError(
            f"pdf_url {pdf_url!r} does not match canonical GovInfo FR "
            f"PDF pattern; refuse to infer (package_id, granule_id)."
        )
    return m.group("package_id"), m.group("granule_id")


# --- Eligibility ----------------------------------------------------


@dataclass(frozen=True)
class EligibilityDecision:
    ok: bool
    reason: str | None


MIN_PAGES = 2
MAX_PAGES = 50


def apply_prove_one_filters(detail: FrDocumentDetail) -> EligibilityDecision:
    """Locked B1a-4 prove-one filter set.

    Metadata-only filters — no PDF/XML fetch. Adjust
    ``locked filter set (§9 recon)`` docstring above if changing.
    """
    if detail.type != "Rule":
        return EligibilityDecision(False, f"type_not_Rule:{detail.type!r}")
    if detail.volume is None:
        return EligibilityDecision(False, "volume_missing")
    if detail.volume < 60:
        return EligibilityDecision(
            False,
            f"volume_lt_60:{detail.volume}  (pre-1995 issues are digitized scans; "
            f"would confound FPR baseline — recon §10)",
        )
    if not detail.pdf_url:
        return EligibilityDecision(False, "no_pdf_url")
    if not detail.full_text_xml_url:
        return EligibilityDecision(
            False,
            "no_full_text_xml_url  (required for prove-one so the G2 "
            "reviewer-support path is exercised end-to-end)",
        )
    pl = detail.page_length
    if pl is None:
        return EligibilityDecision(False, "page_length_missing")
    if pl < MIN_PAGES:
        return EligibilityDecision(
            False, f"page_length_lt_{MIN_PAGES}:{pl}  (engineering bound)"
        )
    if pl > MAX_PAGES:
        return EligibilityDecision(
            False, f"page_length_gt_{MAX_PAGES}:{pl}  (engineering bound)"
        )
    # Sanity: the pdf_url must be a canonical GovInfo FR PDF URL so we
    # can extract package_id + granule_id for cross-check.
    try:
        govinfo_ids_from_pdf_url(detail.pdf_url)
    except AcquisitionError as e:
        return EligibilityDecision(False, f"pdf_url_not_govinfo:{e}")
    return EligibilityDecision(True, None)


# --- Acquisition ---------------------------------------------------


@dataclass(frozen=True)
class AcquiredDocument:
    document_number: str
    directory: Path
    pdf_path: Path
    xml_path: Path
    api_json_path: Path
    manifest_path: Path


def acquire_document(
    detail: FrDocumentDetail,
    api_json_raw: bytes,
    api_json_sha256: str,
    root: Path,
    *,
    population_publication_date_gte: date,
    population_publication_date_lte: date,
    discovery_provenance: dict[str, Any] | None = None,
    selection_role: str = "corpus-adapter prove-one",
) -> AcquiredDocument:
    """Download PDF + XML for ``detail`` and write a v1 manifest.

    Idempotent: existing manifest triggers hash verification instead of
    redownload. Hash mismatch raises; never silently reuses.
    """
    package_id, granule_id = govinfo_ids_from_pdf_url(detail.pdf_url)
    target = root / detail.document_number
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"

    if manifest_path.exists():
        return _verify_cached(detail, target, manifest_path)

    # Fetch PDF (parser source, authoritative record).
    pdf_bytes, _pdf_headers = http_get_fr(detail.pdf_url, timeout=120)
    pdf_sha256 = _sha256(pdf_bytes)

    # Fetch XML (G2-adjudication support only; MUST be present per prove-one filter).
    if not detail.full_text_xml_url:
        raise AcquisitionError(
            f"{detail.document_number}: filter should have rejected — "
            f"full_text_xml_url missing"
        )
    xml_bytes, _xml_headers = http_get_fr(detail.full_text_xml_url, timeout=120)
    xml_sha256 = _sha256(xml_bytes)

    # Persist artifacts.
    api_json_path = target / f"{detail.document_number}.api.json"
    pdf_path = target / f"{detail.document_number}.pdf"
    xml_path = target / f"{detail.document_number}.xml"
    api_json_path.write_bytes(api_json_raw)
    pdf_path.write_bytes(pdf_bytes)
    xml_path.write_bytes(xml_bytes)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus": "federal_register",
        "document_number": detail.document_number,
        "acquired_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "distribution": {
            "source": "federal_register_api_v1",
            "fr_api_host": FR_API_HOST,
            "govinfo_host": GOVINFO_HOST,
            "population_publication_date_gte": population_publication_date_gte.isoformat(),
            "population_publication_date_lte": population_publication_date_lte.isoformat(),
        },
        # FR API identity
        "citation": detail.citation,
        "publication_date": detail.publication_date,
        "volume": detail.volume,
        "type": detail.type,
        "title": detail.title,
        "action": detail.action,
        "abstract": detail.abstract,
        "agencies": list(detail.agencies),
        "start_page": detail.start_page,
        "end_page": detail.end_page,
        "page_length": detail.page_length,
        "docket_ids": list(detail.docket_ids),
        "cfr_references": list(detail.cfr_references),
        # GovInfo cross-check identity
        "govinfo": {
            "package_id": package_id,
            "granule_id": granule_id,
            "detail_url": f"{GOVINFO_HOST}/app/details/{package_id}/{granule_id}",
        },
        # Provenance of the API JSON itself
        "api_json": {
            "path": api_json_path.name,
            "sha256": api_json_sha256,
            "size_bytes": len(api_json_raw),
            "url": f"{FR_DOC_ENDPOINT}/{detail.document_number}.json",
        },
        # Parser source
        "pdf": {
            "path": pdf_path.name,
            "canonical_url": detail.pdf_url,
            "sha256": pdf_sha256,
            "size_bytes": len(pdf_bytes),
            "role": "parser_input_authoritative_record",
        },
        # G2-adjudication support only — NOT a G1 oracle
        "xml": {
            "path": xml_path.name,
            "canonical_url": detail.full_text_xml_url,
            "sha256": xml_sha256,
            "size_bytes": len(xml_bytes),
            "role": "g2_adjudication_support_only",
            "note": (
                "Per GPO FR-XML User Guide: derived from SGML source; "
                "documented-lossy on tables; NOT the legally-authoritative "
                "record. See PROTOCOL_V1.md §2.4 caveat 3."
            ),
        },
        "selection": {
            "authorization": "B1a-4",
            "role": selection_role,
            "discovery": discovery_provenance or {},
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return AcquiredDocument(
        document_number=detail.document_number,
        directory=target,
        pdf_path=pdf_path,
        xml_path=xml_path,
        api_json_path=api_json_path,
        manifest_path=manifest_path,
    )


def _verify_cached(
    detail: FrDocumentDetail, target: Path, manifest_path: Path
) -> AcquiredDocument:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise AcquisitionError(
            f"{manifest_path}: unsupported schema "
            f"{manifest.get('schema_version')!r}"
        )
    pdf_path = target / manifest["pdf"]["path"]
    xml_path = target / manifest["xml"]["path"]
    api_json_path = target / manifest["api_json"]["path"]
    for label, path, expected in (
        ("pdf", pdf_path, manifest["pdf"]["sha256"]),
        ("xml", xml_path, manifest["xml"]["sha256"]),
        ("api_json", api_json_path, manifest["api_json"]["sha256"]),
    ):
        actual = _sha256(path.read_bytes())
        if actual != expected:
            raise AcquisitionError(
                f"{detail.document_number}: cached {label} sha256 "
                f"{actual} != manifest {expected}; refuse to reuse."
            )
    return AcquiredDocument(
        document_number=detail.document_number,
        directory=target,
        pdf_path=pdf_path,
        xml_path=xml_path,
        api_json_path=api_json_path,
        manifest_path=manifest_path,
    )


__all__ = [
    "FR_API_HOST",
    "FR_DOC_ENDPOINT",
    "FR_LIST_ENDPOINT",
    "GOVINFO_HOST",
    "MAX_PAGES",
    "MIN_PAGES",
    "MANIFEST_SCHEMA_VERSION",
    "AcquiredDocument",
    "AcquisitionError",
    "EligibilityDecision",
    "FrDocumentDetail",
    "FrListEntry",
    "acquire_document",
    "apply_prove_one_filters",
    "compute_safe_cutoff",
    "fetch_document_detail",
    "govinfo_ids_from_pdf_url",
    "http_get_fr",
    "list_rules_in_date_range",
]
