"""PMC Open Access acquisition against the AWS Open Data Cloud Service.

Replaces the legacy ``oa_file_list.csv`` + ``oa.fcgi`` pipeline that
NCBI removed during the week of 2026-08-24. Distribution is now the
``pmc-oa-opendata`` S3 bucket (anonymous HTTPS; no boto3 dependency).

Identity contract (locked, B1a-2)
---------------------------------

**A PMC-OA source / ground-truth pair is identified by the same PMCID
and PMC article version, with PDF and JATS XML referenced by the
canonical metadata record for that version.**

- The article-version prefix ``<PMCID>.<v>/`` in ``pmc-oa-opendata``
  IS the version boundary. Every ``<PMCID>.<v>.*`` object under it is
  part of the same version by construction.
- The per-version metadata JSON is the authoritative source of truth
  for pdf/xml object identity, per-file MD5, license, and flags.
- Our local ``manifest.json`` is a provenance receipt, NOT a surrogate
  source of truth. The adapter reads the PMC metadata JSON directly
  when it needs canonical fields.

Integrity hashing
-----------------

- SHA-256 is our integrity identifier (recorded per file after
  download).
- MD5 (the S3 ETag for single-part objects; also embedded in the PMC
  metadata JSON as ``?md5=...`` on each URL) is an INDEPENDENT source-
  side integrity check; we cross-verify but never treat MD5 as a
  substitute for SHA-256.

Version policy
--------------

- One PMCID contributes at most one document to any evaluation
  population.
- The chosen version is the LATEST eligible published version present
  in the population source (metadata-mirror walk for prove-one; frozen
  inventory snapshot for B1a-5).
- If the latest version is ineligible (retracted, historical OCR,
  manuscript, missing PDF/XML, non-CC-BY, ...), the PMCID is EXCLUDED
  entirely. No fallback to an older eligible version. Selecting a
  superseded representation after learning something undesirable about
  the current one would be scientifically indefensible.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

BUCKET = "pmc-oa-opendata"
BUCKET_HTTPS = f"https://{BUCKET}.s3.amazonaws.com/"
USER_AGENT = "aksharamd-eval-v1/1.0 (+contact: ksrkklabs@gmail.com)"
MANIFEST_SCHEMA_VERSION = "2"

_S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_PREFIX_RE = re.compile(r"^(PMC\d+)\.(\d+)/$")
_METADATA_KEY_RE = re.compile(r"^metadata/(PMC\d+)\.(\d+)\.json$")


class AcquisitionError(RuntimeError):
    """Raised when an AWS acquisition invariant is violated.

    Distinct from ValueError so callers can distinguish integrity /
    protocol failures from argument mistakes.
    """


# --- low-level HTTP / S3 ------------------------------------------


def _http_get(url: str, *, timeout: int = 60) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(  # noqa: S310 (allowlist: PMC S3 bucket)
        url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.read(), headers


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()  # noqa: S324 (integrity cross-check only)


def _strip_etag(raw: str | None) -> str | None:
    if raw is None:
        return None
    return raw.strip().strip('"').lower()


def _s3_object_url(key: str) -> str:
    return BUCKET_HTTPS + urllib.parse.quote(key, safe="/")


def _s3_list(prefix: str, delimiter: str, start_after: str, max_keys: int) -> bytes:
    q = f"?list-type=2&max-keys={max_keys}"
    if prefix:
        q += f"&prefix={urllib.parse.quote(prefix, safe='')}"
    if delimiter:
        q += f"&delimiter={urllib.parse.quote(delimiter, safe='')}"
    if start_after:
        q += f"&start-after={urllib.parse.quote(start_after, safe='')}"
    body, _headers = _http_get(BUCKET_HTTPS + q)
    return body


# --- metadata JSON model ------------------------------------------


@dataclass(frozen=True)
class PmcVersionMetadata:
    """Parsed per-version metadata JSON — authoritative for identity + flags."""

    pmcid: str
    version: int
    title: str
    citation: str | None
    doi: str | None
    pmid: int | None
    license_code: str | None
    is_pmc_openaccess: bool
    is_retracted: bool
    is_manuscript: bool
    is_historical_ocr: bool
    mid: str | None
    pdf_url: str
    xml_url: str
    text_url: str | None
    media_urls: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def prefix(self) -> str:
        return f"{self.pmcid}.{self.version}/"

    @property
    def metadata_key(self) -> str:
        return f"metadata/{self.pmcid}.{self.version}.json"

    def object_key(self, kind: str) -> str:
        """Return the in-directory canonical key for pdf/xml/text."""
        return f"{self.pmcid}.{self.version}/{self.pmcid}.{self.version}.{kind}"

    def md5_from_url(self, url: str) -> str | None:
        # PMC embeds ``?md5=<hex>`` on every asset URL.
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        md5 = qs.get("md5", [None])[0]
        return md5.lower() if md5 else None


def _parse_metadata(raw_bytes: bytes) -> PmcVersionMetadata:
    j = json.loads(raw_bytes)
    return PmcVersionMetadata(
        pmcid=j["pmcid"],
        version=int(j["version"]),
        title=j.get("title", ""),
        citation=j.get("citation"),
        doi=j.get("doi"),
        pmid=j.get("pmid"),
        license_code=j.get("license_code"),
        is_pmc_openaccess=bool(j.get("is_pmc_openaccess", False)),
        is_retracted=bool(j.get("is_retracted", False)),
        is_manuscript=bool(j.get("is_manuscript", False)),
        is_historical_ocr=bool(j.get("is_historical_ocr", False)),
        mid=j.get("mid"),
        pdf_url=j.get("pdf_url", ""),
        xml_url=j.get("xml_url", ""),
        text_url=j.get("text_url"),
        media_urls=tuple(j.get("media_urls", []) or ()),
        raw=j,
    )


def fetch_metadata(pmcid_version: str) -> tuple[PmcVersionMetadata, bytes, str]:
    """Fetch and parse ``metadata/<PMCID>.<v>.json``.

    Returns ``(parsed, raw_bytes, sha256)`` where ``sha256`` is our own
    SHA-256 of the raw metadata JSON bytes.
    """
    key = f"metadata/{pmcid_version}.json"
    raw, _headers = _http_get(_s3_object_url(key))
    parsed = _parse_metadata(raw)
    expected_prefix = f"{parsed.pmcid}.{parsed.version}"
    if expected_prefix != pmcid_version:
        raise AcquisitionError(
            f"metadata self-identifies as {expected_prefix} but was fetched "
            f"as {pmcid_version}; refuse to trust."
        )
    return parsed, raw, _sha256(raw)


# --- eligibility --------------------------------------------------


@dataclass(frozen=True)
class EligibilityDecision:
    ok: bool
    reason: str | None


def apply_metadata_filters(md: PmcVersionMetadata) -> EligibilityDecision:
    """Metadata-only pre-filters. No XML/PDF fetch required.

    Locked filter set (2026-09-14):
    - ``license_code == "CC BY"`` (exact)
    - ``is_pmc_openaccess`` (redundant with license but explicit)
    - ``is_retracted == False``
    - ``is_manuscript == False``
    - ``is_historical_ocr == False`` (ground-truth-integrity exclusion:
      historical OCR articles derive JATS from OCR of scans, not
      native structured markup)
    - both ``pdf_url`` and ``xml_url`` must be present
    """
    if md.license_code != "CC BY":
        return EligibilityDecision(False, f"license_not_CC_BY:{md.license_code or 'null'}")
    if not md.is_pmc_openaccess:
        return EligibilityDecision(False, "is_pmc_openaccess_false")
    if md.is_retracted:
        return EligibilityDecision(False, "is_retracted_true")
    if md.is_manuscript:
        return EligibilityDecision(False, "is_manuscript_true")
    if md.is_historical_ocr:
        return EligibilityDecision(False, "is_historical_ocr_true")
    if not md.pdf_url:
        return EligibilityDecision(False, "no_pdf_url")
    if not md.xml_url:
        return EligibilityDecision(False, "no_xml_url")
    return EligibilityDecision(True, None)


# --- latest-eligible-version resolution ---------------------------


def list_versions_for_pmcid(pmcid: str) -> list[int]:
    """List all versions present in the bucket for a given PMCID.

    Returns a sorted list of integers (ascending). Uses the flat
    ``metadata/<PMCID>.<v>.json`` mirror for reliability — the
    top-level ``<PMCID>.<v>/`` prefixes have the same shape but
    listing metadata objects is one deterministic API call.
    """
    body = _s3_list(prefix=f"metadata/{pmcid}.", delimiter="", start_after="", max_keys=100)
    root = ET.fromstring(body)
    versions: set[int] = set()
    for c in root.findall("s3:Contents", _S3_NS):
        key = c.find("s3:Key", _S3_NS).text or ""  # type: ignore[union-attr]
        m = _METADATA_KEY_RE.match(key)
        if m and m.group(1) == pmcid:
            versions.add(int(m.group(2)))
    return sorted(versions)


def resolve_latest_eligible(pmcid: str) -> tuple[PmcVersionMetadata, bytes, str] | tuple[None, None, str]:
    """Return ``(metadata, raw_bytes, sha256)`` for the LATEST eligible
    version of ``pmcid``, or ``(None, None, reason)`` if the latest
    version is not eligible.

    Version policy is strict: if the latest version fails eligibility,
    the PMCID is excluded. No fallback to older versions.
    """
    versions = list_versions_for_pmcid(pmcid)
    if not versions:
        return None, None, "no_versions_in_bucket"
    latest = versions[-1]
    md, raw, sha = fetch_metadata(f"{pmcid}.{latest}")
    decision = apply_metadata_filters(md)
    if not decision.ok:
        return None, None, f"latest_version_ineligible:v{latest}:{decision.reason}"
    return md, raw, sha


# --- inventory snapshot -------------------------------------------


@dataclass(frozen=True)
class InventorySnapshotRef:
    snapshot_utc: str  # e.g. "2026-09-14T01-00Z"
    manifest_key: str
    manifest_sha256: str
    manifest_bytes: bytes


def latest_inventory_manifest() -> InventorySnapshotRef:
    """Fetch the most recent dated inventory manifest.json.

    The Hive-partitioned pseudo-folder ``hive/`` is intentionally
    skipped; only ``YYYY-MM-DDTHH-MMZ/`` dated snapshots are candidates.
    """
    body = _s3_list(
        prefix="inventory-reports/pmc-oa-opendata/metadata/",
        delimiter="/",
        start_after="",
        max_keys=1000,
    )
    root = ET.fromstring(body)
    dated: list[str] = []
    for p in root.findall("s3:CommonPrefixes/s3:Prefix", _S3_NS):
        text = p.text or ""
        if re.match(r".*\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z/$", text):
            dated.append(text)
    if not dated:
        raise AcquisitionError("no dated inventory snapshots found")
    latest = sorted(dated)[-1]
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z)/", latest)
    if not m:
        raise AcquisitionError(f"cannot parse snapshot date from {latest}")
    key = latest + "manifest.json"
    raw, _ = _http_get(_s3_object_url(key))
    return InventorySnapshotRef(
        snapshot_utc=m.group(1),
        manifest_key=key,
        manifest_sha256=_sha256(raw),
        manifest_bytes=raw,
    )


# --- one-article acquisition -------------------------------------


@dataclass(frozen=True)
class AcquiredArticle:
    pmcid: str
    version: int
    directory: Path
    pdf_path: Path
    xml_path: Path
    metadata_json_path: Path
    manifest_path: Path


def acquire_article(
    md: PmcVersionMetadata,
    md_raw_bytes: bytes,
    md_sha256: str,
    root: Path,
    *,
    inventory_snapshot_utc: str | None = None,
    inventory_manifest_sha256: str | None = None,
    discovery_provenance: dict[str, Any] | None = None,
    selection_role: str = "corpus-adapter prove-one",
) -> AcquiredArticle:
    """Download PDF + XML for ``md`` and write a v2 manifest.

    Idempotent: if a valid ``manifest.json`` already exists for this
    PMCID.version, verify cached hashes and return without redownloading.
    A hash mismatch never silently reuses — it raises so the operator
    removes the corrupted cache explicitly.
    """
    target = root / f"{md.pmcid}.{md.version}"
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"

    if manifest_path.exists():
        return _verify_cached(md, target, manifest_path)

    # Write the canonical PMC metadata JSON alongside our artifacts.
    metadata_json_path = target / f"{md.pmcid}.{md.version}.json"
    metadata_json_path.write_bytes(md_raw_bytes)

    # Fetch XML.
    xml_key = md.object_key("xml")
    xml_bytes, xml_headers = _http_get(_s3_object_url(xml_key))
    xml_sha256 = _sha256(xml_bytes)
    xml_md5 = _md5(xml_bytes)
    xml_etag = _strip_etag(xml_headers.get("etag"))
    xml_md5_meta = md.md5_from_url(md.xml_url)
    if xml_md5_meta and xml_md5_meta != xml_md5:
        raise AcquisitionError(
            f"{md.pmcid}.{md.version}: XML MD5 mismatch — computed {xml_md5} "
            f"vs metadata_url {xml_md5_meta}"
        )
    if xml_etag and xml_etag != xml_md5:
        raise AcquisitionError(
            f"{md.pmcid}.{md.version}: XML ETag {xml_etag} disagrees with "
            f"computed MD5 {xml_md5}"
        )

    # Fetch PDF.
    pdf_key = md.object_key("pdf")
    pdf_bytes, pdf_headers = _http_get(_s3_object_url(pdf_key), timeout=120)
    pdf_sha256 = _sha256(pdf_bytes)
    pdf_md5 = _md5(pdf_bytes)
    pdf_etag = _strip_etag(pdf_headers.get("etag"))
    pdf_md5_meta = md.md5_from_url(md.pdf_url)
    if pdf_md5_meta and pdf_md5_meta != pdf_md5:
        raise AcquisitionError(
            f"{md.pmcid}.{md.version}: PDF MD5 mismatch — computed {pdf_md5} "
            f"vs metadata_url {pdf_md5_meta}"
        )
    if pdf_etag and pdf_etag != pdf_md5:
        raise AcquisitionError(
            f"{md.pmcid}.{md.version}: PDF ETag {pdf_etag} disagrees with "
            f"computed MD5 {pdf_md5}"
        )

    xml_path = target / f"{md.pmcid}.{md.version}.xml"
    pdf_path = target / f"{md.pmcid}.{md.version}.pdf"
    xml_path.write_bytes(xml_bytes)
    pdf_path.write_bytes(pdf_bytes)

    # JATS-internal PMCID is recorded for provenance. The cross-check
    # against the metadata PMCID is an eligibility assertion, not a
    # bytes-integrity check; callers should assert it (via
    # jats_pmcid_matches()) as part of their post-fetch phase.
    xml_pmcid = _extract_pmcid_from_xml(xml_bytes)

    license_info = _extract_license_from_xml(xml_bytes)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus": "pmc_oa",
        "pmcid": md.pmcid,
        "version": md.version,
        "article_key_prefix": md.prefix,
        "acquired_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "distribution": {
            "source": "aws_open_data_pmc_oa",
            "bucket": BUCKET,
            "https_base_url": BUCKET_HTTPS,
            "inventory_snapshot_utc": inventory_snapshot_utc,
            "inventory_manifest_sha256": inventory_manifest_sha256,
        },
        "metadata_object": {
            "key": md.metadata_key,
            "size_bytes": len(md_raw_bytes),
            "sha256": md_sha256,
        },
        "pdf": {
            "key": pdf_key,
            "canonical_url": md.pdf_url,
            "size_bytes": len(pdf_bytes),
            "sha256": pdf_sha256,
            "md5": pdf_md5,
            "etag": pdf_etag,
            "md5_from_metadata": pdf_md5_meta,
        },
        "xml": {
            "key": xml_key,
            "canonical_url": md.xml_url,
            "size_bytes": len(xml_bytes),
            "sha256": xml_sha256,
            "md5": xml_md5,
            "etag": xml_etag,
            "md5_from_metadata": xml_md5_meta,
            "pmcid_in_jats": xml_pmcid,
        },
        "license": {
            "license_code_from_metadata": md.license_code,
            "license_type_from_xml": license_info["license_type"],
            "license_text_from_xml": license_info["license_text"],
        },
        "flags_from_metadata": {
            "is_pmc_openaccess": md.is_pmc_openaccess,
            "is_retracted": md.is_retracted,
            "is_manuscript": md.is_manuscript,
            "is_historical_ocr": md.is_historical_ocr,
        },
        "citation": md.citation,
        "doi": md.doi,
        "pmid": md.pmid,
        "selection": {
            "authorization": "B1a-2",
            "role": selection_role,
            "discovery": discovery_provenance or {},
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return AcquiredArticle(
        pmcid=md.pmcid,
        version=md.version,
        directory=target,
        pdf_path=pdf_path,
        xml_path=xml_path,
        metadata_json_path=metadata_json_path,
        manifest_path=manifest_path,
    )


def _verify_cached(
    md: PmcVersionMetadata,
    target: Path,
    manifest_path: Path,
) -> AcquiredArticle:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise AcquisitionError(
            f"{manifest_path}: unsupported schema {manifest.get('schema_version')!r}"
        )
    pdf_path = target / Path(manifest["pdf"]["key"]).name
    xml_path = target / Path(manifest["xml"]["key"]).name
    metadata_json_path = target / Path(manifest["metadata_object"]["key"]).name
    for label, path, expected in (
        ("pdf", pdf_path, manifest["pdf"]["sha256"]),
        ("xml", xml_path, manifest["xml"]["sha256"]),
        ("metadata", metadata_json_path, manifest["metadata_object"]["sha256"]),
    ):
        actual = _sha256(path.read_bytes())
        if actual != expected:
            raise AcquisitionError(
                f"{md.pmcid}.{md.version}: cached {label} sha256 {actual} != "
                f"manifest {expected}; refuse to silently reuse — remove "
                f"{target} and re-acquire."
            )
    return AcquiredArticle(
        pmcid=md.pmcid,
        version=md.version,
        directory=target,
        pdf_path=pdf_path,
        xml_path=xml_path,
        metadata_json_path=metadata_json_path,
        manifest_path=manifest_path,
    )


# --- JATS helpers (kept small — full transformation lives in the adapter)


def jats_pmcid_matches(xml_bytes: bytes, expected_pmcid: str) -> tuple[bool, str | None]:
    """Cross-check: does JATS-internal PMCID equal ``expected_pmcid``?

    Returns ``(True, extracted)`` on match. On mismatch or absence,
    returns ``(False, extracted_or_none)`` so the caller can record
    what it found. This is an eligibility signal, not a bytes-integrity
    check — callers should treat a mismatch as a candidate rejection,
    not a hard abort.
    """
    got = _extract_pmcid_from_xml(xml_bytes)
    if got is None:
        return False, None
    return got.upper() == expected_pmcid.upper(), got


def _extract_pmcid_from_xml(xml_bytes: bytes) -> str | None:
    """Extract the PMCID from JATS ``<article-meta>/<article-id>``.

    Accepts current NLM JATS ``pub-id-type`` values (``"pmcid"`` on
    articles published after the 2020s tag-set refresh) as well as the
    older ``"pmc"`` and ``"pmcaid"`` variants. Normalizes the returned
    value to ``PMC<digits>``.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    priority = ("pmcid", "pmc", "pmcaid")
    found: dict[str, str] = {}
    for aid in root.findall(".//article-meta/article-id"):
        t = aid.attrib.get("pub-id-type") or ""
        if t in priority and t not in found:
            text = (aid.text or "").strip()
            if text:
                found[t] = text
    for key in priority:
        if key in found:
            v = found[key]
            return v if v.startswith("PMC") else f"PMC{v}"
    return None


def _extract_license_from_xml(xml_bytes: bytes) -> dict[str, str | None]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {"license_type": None, "license_text": None}
    lic = root.find(".//article-meta/permissions/license")
    if lic is None:
        return {"license_type": None, "license_text": None}
    text = " ".join(lic.itertext()).strip()
    return {
        "license_type": lic.attrib.get("license-type"),
        "license_text": text or None,
    }


__all__ = [
    "BUCKET",
    "BUCKET_HTTPS",
    "MANIFEST_SCHEMA_VERSION",
    "AcquiredArticle",
    "AcquisitionError",
    "EligibilityDecision",
    "InventorySnapshotRef",
    "PmcVersionMetadata",
    "acquire_article",
    "apply_metadata_filters",
    "fetch_metadata",
    "jats_pmcid_matches",
    "latest_inventory_manifest",
    "list_versions_for_pmcid",
    "resolve_latest_eligible",
]
