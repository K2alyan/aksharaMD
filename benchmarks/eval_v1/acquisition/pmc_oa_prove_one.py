"""Prove-one candidate selection for PMC-OA (B1a-2, AWS distribution).

Metadata-mirror walk (approved for prove-one only — B1a-5 uses the
frozen inventory anchor). For each candidate PMCID:

1. Enumerate ALL versions in the bucket.
2. Take only the LATEST version. If ineligible, EXCLUDE the PMCID
   entirely (no fallback to older versions).
3. Apply metadata filters (locked set in ``pmc_oa_aws``).
4. Post-fetch structural checks on JATS.
5. First candidate passing everything wins.

Every PMCID whose metadata/PDF/XML was fetched sufficiently to
determine eligibility becomes development-contaminated and is written
to the exclusion ledger — merely listing S3 keys does NOT contaminate.

Outputs
-------
- ``corpus/eval_v1/pmc_oa/<PMCID>.<v>/`` (selected article) with a v2
  ``manifest.json`` and side-by-side ``.json`` / ``.pdf`` / ``.xml``.
- ``docs/evaluation/PMC_OA_EXCLUSION_LEDGER.jsonl`` (append-only).
- ``docs/evaluation/PMC_OA_PROVE_ONE_SELECTION.json`` (compact
  checkpoint report per the human's spec).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    BUCKET_HTTPS,
    USER_AGENT,
    AcquisitionError,
    EligibilityDecision,
    acquire_article,
    apply_metadata_filters,
    fetch_metadata,
    jats_pmcid_matches,
    list_versions_for_pmcid,
)
from benchmarks.eval_v1.adapters.pmc_oa_v1 import (
    EXTRACTION_RULES_VERSION,
    _transform_jats,
)

MIN_BODY_TOKENS = 2_000  # engineering eligibility bound
MAX_BODY_TOKENS = 15_000
DISCOVERY_METHOD = "aws-metadata-mirror-walk"
DISCOVERY_METHOD_VERSION = "1"
_S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_METADATA_KEY_RE = re.compile(r"^metadata/(PMC\d+)\.(\d+)\.json$")


@dataclass
class CandidateOutcome:
    pmcid: str
    version: int | None
    metadata_key: str | None
    rank_key: str
    stage: str  # "metadata_filter" | "structural" | "selected"
    reason: str | None
    package_sha256_pdf: str | None = None
    package_sha256_xml: str | None = None
    body_tokens: int | None = None


@dataclass
class ProveOneRun:
    discovery_method: str = DISCOVERY_METHOD
    discovery_method_version: str = DISCOVERY_METHOD_VERSION
    discovery_started_utc: str = ""
    discovery_finished_utc: str = ""
    walk_prefix: str = "metadata/"
    max_metadata_keys_scanned: int = 0
    metadata_keys_scanned: int = 0
    pmcids_considered: int = 0
    pmcids_excluded_pre_fetch: int = 0
    pmcids_excluded_post_fetch: int = 0
    outcomes: list[CandidateOutcome] = field(default_factory=list)
    selected: CandidateOutcome | None = None


def _http_get(url: str, *, timeout: int = 60) -> bytes:
    req = urllib.request.Request(  # noqa: S310 (PMC bucket only)
        url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _list_metadata_page(start_after: str, max_keys: int) -> tuple[list[str], bool]:
    """Return (keys, is_truncated) for one page of ``metadata/`` listing."""
    q = f"?list-type=2&prefix=metadata/&max-keys={max_keys}"
    if start_after:
        q += f"&start-after={urllib.parse.quote(start_after, safe='')}"
    body = _http_get(BUCKET_HTTPS + q)
    root = ET.fromstring(body)
    keys = [
        c.find("s3:Key", _S3_NS).text or ""  # type: ignore[union-attr]
        for c in root.findall("s3:Contents", _S3_NS)
    ]
    truncated = (root.find("s3:IsTruncated", _S3_NS).text or "").lower() == "true"  # type: ignore[union-attr]
    return keys, truncated


def _rank(pmcid_version: str) -> str:
    return hashlib.sha256(pmcid_version.encode("ascii")).hexdigest()


def _paginate_metadata_keys(max_scan: int) -> list[str]:
    """Walk the ``metadata/`` mirror until ``max_scan`` keys collected."""
    keys: list[str] = []
    start_after = ""
    while len(keys) < max_scan:
        remaining = max_scan - len(keys)
        page, truncated = _list_metadata_page(start_after, min(1000, remaining))
        if not page:
            break
        keys.extend(page)
        if not truncated:
            break
        start_after = page[-1]
    return keys


def _post_fetch_check(xml_bytes: bytes, expected_pmcid: str) -> tuple[EligibilityDecision, int]:
    """JATS structural checks + canonical-body-tokens bound.

    Also asserts the JATS-internal PMCID matches ``expected_pmcid``.
    An unmatched or missing internal PMCID is a soft candidate
    rejection (identity mismatch is an eligibility failure, not a
    bytes-integrity failure).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        return EligibilityDecision(False, f"jats_parse_error:{e}"), 0
    matches, got = jats_pmcid_matches(xml_bytes, expected_pmcid)
    if not matches:
        return EligibilityDecision(
            False, f"jats_internal_pmcid_mismatch:got={got!r}:expected={expected_pmcid!r}",
        ), 0
    article_type = root.attrib.get("article-type", "").strip()
    if article_type != "research-article":
        return EligibilityDecision(False, f"article_type_not_research:{article_type!r}"), 0
    if root.find(".//article-meta/related-article[@related-article-type='retracted-article']") is not None:
        return EligibilityDecision(False, "retraction_related_article"), 0
    for subj in root.findall(".//article-meta/article-categories//subject"):
        text = (subj.text or "").strip().lower()
        if text in {"retraction", "correction", "erratum"}:
            return EligibilityDecision(False, f"subject_{text}"), 0
    if root.find(".//article-meta/abstract") is None:
        return EligibilityDecision(False, "no_abstract"), 0
    body = root.find(".//body")
    if body is None:
        return EligibilityDecision(False, "no_body"), 0
    sec_count = sum(1 for el in body.iter() if el.tag.split("}", 1)[-1] == "sec")
    if sec_count < 2:
        return EligibilityDecision(False, f"body_sections_lt_2:{sec_count}"), 0
    if not any(el.tag.split("}", 1)[-1] == "table-wrap" for el in body.iter()):
        return EligibilityDecision(False, "no_table_wrap"), 0
    if not any(el.tag.split("}", 1)[-1] == "fig" for el in body.iter()):
        return EligibilityDecision(False, "no_fig"), 0

    core, _stats, _tables, _captions = _transform_jats(xml_bytes)
    body_tokens = len(core["tokens"])
    if body_tokens < MIN_BODY_TOKENS:
        return EligibilityDecision(False, f"body_tokens_lt_{MIN_BODY_TOKENS}:{body_tokens}"), body_tokens
    if body_tokens > MAX_BODY_TOKENS:
        return EligibilityDecision(False, f"body_tokens_gt_{MAX_BODY_TOKENS}:{body_tokens}"), body_tokens
    return EligibilityDecision(True, None), body_tokens


def _append_exclusion_ledger(
    ledger_path: Path,
    outcomes: list[CandidateOutcome],
    selected: CandidateOutcome | None,
    discovery_started_utc: str,
) -> None:
    """Only PMCIDs whose XML/PDF was fetched become contaminated.

    Listing S3 keys does NOT contaminate. Fetching the tiny metadata
    JSON alone also does NOT contaminate — that's part of the discovery
    machinery and does not touch article content. Only outcomes at
    ``stage in {"structural", "selected"}`` fetched XML/PDF; those go
    into the ledger.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).isoformat(timespec="seconds")
    entries: list[dict[str, Any]] = []
    for o in outcomes:
        if o.stage != "structural":
            continue
        entries.append({
            "pmcid": o.pmcid,
            "version": o.version,
            "usage": "B1a-2 corpus-adapter prove-one candidate (rejected post-fetch)",
            "evaluation_eligibility": "development_only",
            "held_out_v1_eligible": False,
            "reason": (
                "XML/PDF fetched and inspected during PMC-OA adapter prove-one "
                f"validation; rejected: {o.reason}"
            ),
            "pdf_sha256": o.package_sha256_pdf,
            "xml_sha256": o.package_sha256_xml,
            "recorded_utc": ts,
            "discovery_started_utc": discovery_started_utc,
        })
    if selected is not None:
        entries.append({
            "pmcid": selected.pmcid,
            "version": selected.version,
            "usage": "B1a-2 corpus-adapter prove-one (selected)",
            "evaluation_eligibility": "development_only",
            "held_out_v1_eligible": False,
            "reason": "inspected during evaluation-infrastructure development",
            "pdf_sha256": selected.package_sha256_pdf,
            "xml_sha256": selected.package_sha256_xml,
            "recorded_utc": ts,
            "discovery_started_utc": discovery_started_utc,
        })
    if not entries:
        return
    with ledger_path.open("a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, sort_keys=True) + "\n")


def run(
    corpus_dir: Path,
    exclusion_ledger: Path,
    report_path: Path,
    max_metadata_keys: int,
    max_post_fetch_attempts: int,
) -> ProveOneRun:
    result = ProveOneRun(
        max_metadata_keys_scanned=max_metadata_keys,
        discovery_started_utc=datetime.now(tz=UTC).isoformat(timespec="seconds"),
    )

    print(f"[prove-one] walking metadata/ (up to {max_metadata_keys} keys)")
    keys = _paginate_metadata_keys(max_metadata_keys)
    result.metadata_keys_scanned = len(keys)
    print(f"[prove-one] scanned {len(keys)} metadata keys")

    # Reduce to one row per PMCID: latest observed version wins.
    latest_by_pmcid: dict[str, int] = {}
    for k in keys:
        m = _METADATA_KEY_RE.match(k)
        if not m:
            continue
        pmcid = m.group(1)
        v = int(m.group(2))
        cur = latest_by_pmcid.get(pmcid)
        if cur is None or v > cur:
            latest_by_pmcid[pmcid] = v
    result.pmcids_considered = len(latest_by_pmcid)
    print(f"[prove-one] {len(latest_by_pmcid)} distinct PMCIDs in scanned window")

    # Deterministic candidate order over PMCID.v.
    candidates = sorted(
        latest_by_pmcid.items(),
        key=lambda item: _rank(f"{item[0]}.{item[1]}"),
    )

    post_fetch_attempts = 0
    for pmcid, version_from_scan in candidates:
        rank_key = _rank(f"{pmcid}.{version_from_scan}")

        # Authoritative version list (the scan window might have missed a
        # newer version if it landed after our list-objects paging).
        versions = list_versions_for_pmcid(pmcid)
        if not versions:
            result.outcomes.append(CandidateOutcome(
                pmcid=pmcid, version=None,
                metadata_key=None, rank_key=rank_key,
                stage="metadata_filter", reason="no_versions_in_bucket",
            ))
            result.pmcids_excluded_pre_fetch += 1
            continue
        latest = versions[-1]

        try:
            md, md_raw, md_sha = fetch_metadata(f"{pmcid}.{latest}")
        except Exception as e:  # noqa: BLE001
            result.outcomes.append(CandidateOutcome(
                pmcid=pmcid, version=latest,
                metadata_key=f"metadata/{pmcid}.{latest}.json",
                rank_key=rank_key,
                stage="metadata_filter",
                reason=f"metadata_fetch_error:{type(e).__name__}",
            ))
            result.pmcids_excluded_pre_fetch += 1
            continue

        decision = apply_metadata_filters(md)
        if not decision.ok:
            # Version policy: if the LATEST version is ineligible, EXCLUDE
            # the PMCID entirely. No fallback to older versions.
            result.outcomes.append(CandidateOutcome(
                pmcid=pmcid, version=latest,
                metadata_key=md.metadata_key, rank_key=rank_key,
                stage="metadata_filter",
                reason=f"latest_version_ineligible:v{latest}:{decision.reason}",
            ))
            result.pmcids_excluded_pre_fetch += 1
            continue

        # About to fetch article content — this contaminates the PMCID.
        if post_fetch_attempts >= max_post_fetch_attempts:
            print(
                f"[prove-one] max_post_fetch_attempts={max_post_fetch_attempts} "
                "reached before selection; stopping."
            )
            break
        post_fetch_attempts += 1

        try:
            xml_bytes = _http_get(BUCKET_HTTPS + urllib.parse.quote(md.object_key("xml"), safe="/"))
        except Exception as e:  # noqa: BLE001
            result.outcomes.append(CandidateOutcome(
                pmcid=pmcid, version=latest,
                metadata_key=md.metadata_key, rank_key=rank_key,
                stage="structural",
                reason=f"xml_fetch_error:{type(e).__name__}",
            ))
            result.pmcids_excluded_post_fetch += 1
            continue

        struct, body_tokens = _post_fetch_check(xml_bytes, expected_pmcid=pmcid)
        xml_sha256 = hashlib.sha256(xml_bytes).hexdigest()
        if not struct.ok:
            result.outcomes.append(CandidateOutcome(
                pmcid=pmcid, version=latest,
                metadata_key=md.metadata_key, rank_key=rank_key,
                stage="structural", reason=struct.reason,
                package_sha256_xml=xml_sha256,
                body_tokens=body_tokens,
            ))
            result.pmcids_excluded_post_fetch += 1
            continue

        # PASS — acquire.
        discovery = {
            "discovery_method": DISCOVERY_METHOD,
            "discovery_method_version": DISCOVERY_METHOD_VERSION,
            "discovery_started_utc": result.discovery_started_utc,
            "walk_prefix": "metadata/",
            "max_metadata_keys_scanned": max_metadata_keys,
            "candidate_ordering_rule": "SHA-256(pmcid.version) ascending",
            "candidate_rank_key": rank_key,
            "candidate_metadata_key": md.metadata_key,
        }
        acquired = acquire_article(
            md, md_raw, md_sha,
            corpus_dir,
            discovery_provenance=discovery,
            selection_role="corpus-adapter prove-one",
        )
        pdf_sha256 = hashlib.sha256(acquired.pdf_path.read_bytes()).hexdigest()

        result.selected = CandidateOutcome(
            pmcid=pmcid, version=latest,
            metadata_key=md.metadata_key, rank_key=rank_key,
            stage="selected", reason=None,
            package_sha256_pdf=pdf_sha256,
            package_sha256_xml=xml_sha256,
            body_tokens=body_tokens,
        )
        print(
            f"[prove-one] SELECTED {pmcid}.{latest}: body_tokens={body_tokens}, "
            f"pdf_sha256={pdf_sha256[:12]}..., xml_sha256={xml_sha256[:12]}..."
        )
        break

    result.discovery_finished_utc = datetime.now(tz=UTC).isoformat(timespec="seconds")
    _append_exclusion_ledger(
        exclusion_ledger, result.outcomes, result.selected, result.discovery_started_utc
    )
    _write_report(report_path, result, corpus_dir)
    if result.selected is None:
        raise AcquisitionError(
            "PMC-OA prove-one selection failed: no candidate in the scanned "
            f"window (up to {max_metadata_keys} metadata keys, up to "
            f"{max_post_fetch_attempts} post-fetch attempts) passed the locked "
            "filters. Do NOT broaden the license or scan window without "
            "human authorization."
        )
    return result


def _write_report(report_path: Path, result: ProveOneRun, corpus_dir: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "authorization": "B1a-2",
        "role": "corpus-adapter prove-one",
        "discovery": {
            "method": result.discovery_method,
            "method_version": result.discovery_method_version,
            "started_utc": result.discovery_started_utc,
            "finished_utc": result.discovery_finished_utc,
            "walk_prefix": result.walk_prefix,
            "max_metadata_keys_scanned": result.max_metadata_keys_scanned,
            "metadata_keys_scanned": result.metadata_keys_scanned,
            "pmcids_considered": result.pmcids_considered,
            "ordering_rule": "SHA-256(pmcid.version) ascending",
        },
        "filters": {
            "metadata": [
                "license_code == 'CC BY' (exact)",
                "is_pmc_openaccess == True",
                "is_retracted == False",
                "is_manuscript == False",
                "is_historical_ocr == False  (ground-truth-integrity exclusion)",
                "pdf_url and xml_url both present",
            ],
            "structural": [
                "article-type == 'research-article'",
                "not retraction / correction / erratum",
                "<abstract> present",
                "<sec> count >= 2 in <body>",
                ">=1 <table-wrap>",
                ">=1 <fig>",
                f"body_tokens in [{MIN_BODY_TOKENS}, {MAX_BODY_TOKENS}]  (engineering bound, not a claimed population percentile)",
            ],
        },
        "version_policy": (
            "one PMCID -> latest eligible published version only; if the "
            "latest version is ineligible, the PMCID is excluded entirely "
            "(no fallback to older versions)."
        ),
        "extraction_rules_version": EXTRACTION_RULES_VERSION,
        "corpus_dir": str(corpus_dir),
        "counts": {
            "pmcids_excluded_pre_fetch": result.pmcids_excluded_pre_fetch,
            "pmcids_excluded_post_fetch": result.pmcids_excluded_post_fetch,
            "outcomes": len(result.outcomes),
            "selected": 1 if result.selected else 0,
        },
        "selected": None if not result.selected else {
            "pmcid": result.selected.pmcid,
            "version": result.selected.version,
            "metadata_key": result.selected.metadata_key,
            "rank_key": result.selected.rank_key,
            "body_tokens": result.selected.body_tokens,
            "pdf_sha256": result.selected.package_sha256_pdf,
            "xml_sha256": result.selected.package_sha256_xml,
        },
        "candidate_trail": [
            {
                "pmcid": o.pmcid,
                "version": o.version,
                "metadata_key": o.metadata_key,
                "rank_key": o.rank_key,
                "stage": o.stage,
                "reason": o.reason,
                "body_tokens": o.body_tokens,
            }
            for o in result.outcomes
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"[prove-one] report -> {report_path}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus-dir", type=Path, default=Path("corpus/eval_v1/pmc_oa"))
    p.add_argument(
        "--exclusion-ledger",
        type=Path,
        default=Path("docs/evaluation/PMC_OA_EXCLUSION_LEDGER.jsonl"),
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/evaluation/PMC_OA_PROVE_ONE_SELECTION.json"),
    )
    p.add_argument("--max-metadata-keys", type=int, default=5_000)
    p.add_argument("--max-post-fetch-attempts", type=int, default=25)
    args = p.parse_args(argv)
    run(
        corpus_dir=args.corpus_dir,
        exclusion_ledger=args.exclusion_ledger,
        report_path=args.report,
        max_metadata_keys=args.max_metadata_keys,
        max_post_fetch_attempts=args.max_post_fetch_attempts,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
