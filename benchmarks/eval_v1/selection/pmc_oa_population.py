"""PMC-OA population enumeration for B1a-5a (metadata only).

Order of operations (per the human's 2026-09-14 spec):

1. Fetch + verify the pinned inventory manifest ``2026-09-01T01-00Z``.
2. Walk shard CSVs to enumerate every ``metadata/PMC<n>.<v>.json`` key
   present in the frozen snapshot.
3. Reduce to ``{PMCID -> latest_version}``.
4. DEV assignment on ``"{PMCID}.{version}"``, BEFORE any metadata probe.
5. Hard exclusion removal from the DEV set (does NOT count as
   metadata ineligibility).
6. SHA-256 rank.
7. Lazy walk in rank order, fetch per-version metadata JSON, apply
   eligibility filters, stop after ``stop_after_eligible`` eligible
   candidates.

**Locked contamination rule (2026-09-15):** a network/retrieval
failure MUST NEVER be recorded as metadata ineligibility. It is a
selection-run stop condition (``NetworkExhaustionError``), not a
property of the article. Otherwise transient network conditions could
change which eight documents are selected.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    BUCKET_HTTPS,
    apply_metadata_filters,
)
from benchmarks.eval_v1.selection.http_policy import (
    NetworkExhaustionError,
    make_pmc_policy,
)
from benchmarks.eval_v1.selection.pilot_selector import Candidate, make_candidate

PMC_INVENTORY_SNAPSHOT_UTC = "2026-09-01T01-00Z"
PMC_INVENTORY_MANIFEST_KEY = (
    f"inventory-reports/pmc-oa-opendata/metadata/{PMC_INVENTORY_SNAPSHOT_UTC}/"
    "manifest.json"
)
PMC_ELIGIBILITY_RULE_VERSION = "1"
_METADATA_KEY_RE = re.compile(r"^metadata/(PMC\d+)\.(\d+)\.json$")
_ALLOWED_HOSTS = frozenset({"pmc-oa-opendata.s3.amazonaws.com"})

_policy = make_pmc_policy()


def _require_pmc(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in _ALLOWED_HOSTS:
        raise RuntimeError(f"refuse to fetch {url!r}: not on PMC allowlist")


def _http_get(url: str) -> bytes:
    _require_pmc(url)
    body, _headers = _policy.get(url)
    return body


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _md5(b: bytes) -> str:
    return hashlib.md5(b, usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class PmcInventorySnapshot:
    snapshot_utc: str
    manifest_url: str
    manifest_sha256: str
    manifest_bytes_len: int
    shards: list[dict[str, Any]]  # [{key, size, md5, sha256_computed}]


def fetch_and_verify_inventory() -> PmcInventorySnapshot:
    """Fetch the pinned inventory manifest, verify each shard's MD5,
    and record everything for the population snapshot."""
    manifest_url = BUCKET_HTTPS + urllib.parse.quote(PMC_INVENTORY_MANIFEST_KEY, safe="/")
    mf_bytes = _http_get(manifest_url)
    mf = json.loads(mf_bytes)
    if mf.get("sourceBucket") != "pmc-oa-opendata":
        raise RuntimeError(
            f"inventory manifest reports unexpected sourceBucket "
            f"{mf.get('sourceBucket')!r}"
        )
    shards: list[dict[str, Any]] = []
    for entry in mf["files"]:
        shard_url = BUCKET_HTTPS + urllib.parse.quote(entry["key"], safe="/")
        shard_bytes = _http_get(shard_url)
        actual_md5 = _md5(shard_bytes)
        if actual_md5 != entry["MD5checksum"]:
            raise RuntimeError(
                f"shard {entry['key']} MD5 mismatch: "
                f"computed {actual_md5} vs manifest {entry['MD5checksum']}"
            )
        shards.append(
            {
                "key": entry["key"],
                "size": entry["size"],
                "md5_from_manifest": entry["MD5checksum"],
                "md5_computed": actual_md5,
                "sha256_computed": _sha256(shard_bytes),
                "bytes_len": len(shard_bytes),
            }
        )
    return PmcInventorySnapshot(
        snapshot_utc=PMC_INVENTORY_SNAPSHOT_UTC,
        manifest_url=manifest_url,
        manifest_sha256=_sha256(mf_bytes),
        manifest_bytes_len=len(mf_bytes),
        shards=shards,
    )


def enumerate_metadata_keys(snapshot: PmcInventorySnapshot) -> list[str]:
    """Return every ``metadata/PMC<n>.<v>.json`` key in the frozen snapshot."""
    keys: list[str] = []
    for shard in snapshot.shards:
        shard_url = BUCKET_HTTPS + urllib.parse.quote(shard["key"], safe="/")
        gz_bytes = _http_get(shard_url)
        with gzip.GzipFile(fileobj=io.BytesIO(gz_bytes)) as gz:
            text = gz.read().decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if len(row) < 2:
                continue
            key = row[1]
            if _METADATA_KEY_RE.match(key):
                keys.append(key)
    return keys


def latest_version_per_pmcid(metadata_keys: Iterable[str]) -> dict[str, int]:
    latest: dict[str, int] = {}
    for key in metadata_keys:
        m = _METADATA_KEY_RE.match(key)
        if not m:
            continue
        pmcid = m.group(1)
        version = int(m.group(2))
        cur = latest.get(pmcid)
        if cur is None or version > cur:
            latest[pmcid] = version
    return latest


def _fetch_metadata_with_policy(canonical: str) -> tuple[Any, bytes, str]:
    """Fetch per-version metadata JSON under the policy.

    NetworkExhaustionError propagates unchanged; callers MUST NOT
    catch it and continue to the next candidate.
    """
    url = BUCKET_HTTPS + urllib.parse.quote(
        f"metadata/{canonical}.json", safe="/"
    )
    body = _http_get(url)
    from benchmarks.eval_v1.acquisition.pmc_oa_aws import _parse_metadata

    parsed = _parse_metadata(body)
    expected = f"{parsed.pmcid}.{parsed.version}"
    if expected != canonical:
        raise RuntimeError(
            f"PMC metadata self-identifies as {expected} but was "
            f"fetched as {canonical}"
        )
    return parsed, body, _sha256(body)


@dataclass
class PmcRankWalkResult:
    """Outcome of the lazy deterministic rank-walk over DEV PMCIDs.

    NOTE: does NOT report ``n_eligible_dev`` — full eligibility was
    not materialized. Selection is defined as "first-K eligible under
    deterministic ordering," which is provably equivalent to "K
    lowest-ranked eligible" when eligibility is independent of rank.
    """

    dev_assignment_count_before_metadata_eligibility: int
    excluded_before_rank_walk: int
    metadata_records_probed: int
    eligible_records_found_before_stop: int
    stopping_rule: str
    ineligible_head: list[dict[str, Any]]


def rank_walk_eligible_dev(
    pmcid_to_latest: dict[str, int],
    excluded_ids: set[str],
    *,
    stop_after_eligible: int = 12,
    on_progress=None,
) -> tuple[list[Candidate], PmcRankWalkResult]:
    """Lazy deterministic rank-walk over DEV PMCIDs.

    Order:
    1. Latest-version resolution (caller-provided ``pmcid_to_latest``).
    2. DEV assignment on ``"{PMCID}.{version}"``.
    3. Exclusion removal (never a metadata request).
    4. SHA-256 rank ascending.
    5. Walk, fetch metadata under the policy, apply eligibility.
    6. Stop after ``stop_after_eligible`` eligible.

    Locked rule: a NetworkExhaustionError STOPS the run. It never
    becomes a metadata ineligibility.
    """
    from benchmarks.eval_v1.corpus_split import Partition, assign_partition
    from benchmarks.eval_v1.selection.pilot_selector import _rank

    dev_ids_pre_exclusion: list[str] = []
    for pmcid, version in pmcid_to_latest.items():
        canonical = f"{pmcid}.{version}"
        if assign_partition(canonical) is Partition.DEV:
            dev_ids_pre_exclusion.append(canonical)
    dev_assignment_count = len(dev_ids_pre_exclusion)
    excluded = [cid for cid in dev_ids_pre_exclusion if cid in excluded_ids]
    remaining = [cid for cid in dev_ids_pre_exclusion if cid not in excluded_ids]
    remaining.sort(key=_rank)

    eligible: list[Candidate] = []
    ineligible: list[dict[str, Any]] = []
    probed = 0
    for canonical in remaining:
        if len(eligible) >= stop_after_eligible:
            break
        probed += 1
        if on_progress and probed % 20 == 0:
            on_progress(probed, len(eligible))
        # NetworkExhaustionError propagates; only apply_metadata_filters
        # rejections are recorded as ineligibility.
        md, _raw, _sha = _fetch_metadata_with_policy(canonical)
        decision = apply_metadata_filters(md)
        if not decision.ok:
            if len(ineligible) < 200:
                ineligible.append(
                    {"canonical_id": canonical, "reason": decision.reason}
                )
            continue
        pmcid = canonical.rsplit(".", 1)[0]
        version = int(canonical.rsplit(".", 1)[1])
        eligible.append(
            make_candidate(
                canonical_id=canonical,
                metadata={
                    "pmcid": pmcid,
                    "version": version,
                    "license_code": md.license_code,
                    "title": md.title[:120] if md.title else None,
                    "pmid": md.pmid,
                    "citation": md.citation,
                },
                corpus="pmc_oa",
            )
        )
    result = PmcRankWalkResult(
        dev_assignment_count_before_metadata_eligibility=dev_assignment_count,
        excluded_before_rank_walk=len(excluded),
        metadata_records_probed=probed,
        eligible_records_found_before_stop=len(eligible),
        stopping_rule=f"stop after {stop_after_eligible} eligible DEV candidates",
        ineligible_head=ineligible,
    )
    return eligible, result


__all__ = [
    "PMC_ELIGIBILITY_RULE_VERSION",
    "PMC_INVENTORY_MANIFEST_KEY",
    "PMC_INVENTORY_SNAPSHOT_UTC",
    "NetworkExhaustionError",
    "PmcInventorySnapshot",
    "PmcRankWalkResult",
    "enumerate_metadata_keys",
    "fetch_and_verify_inventory",
    "latest_version_per_pmcid",
    "rank_walk_eligible_dev",
]
