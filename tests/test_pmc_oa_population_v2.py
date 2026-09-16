"""Tests for the V2 PMC rank-walk (no network).

Fake metadata + JATS fetchers are injected so the walker can be
exercised offline. The fakes construct real
:class:`PmcVersionMetadata` and produce synthetic JATS byte strings
that flow through the same ``apply_text_oracle_filters`` predicate
merged in PR #186.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest

from benchmarks.eval_v1.acquisition.pmc_oa_aws import PmcVersionMetadata
from benchmarks.eval_v1.selection.pmc_oa_population_v2 import (
    V2InspectionRecord,
    rank_walk_v2_eligible_dev,
)


def _md(pmcid: str, version: int, **overrides: Any) -> PmcVersionMetadata:
    defaults: dict[str, Any] = dict(
        pmcid=pmcid, version=version,
        title="t", citation=None, doi=None, pmid=None,
        license_code="CC BY", is_pmc_openaccess=True, is_retracted=False,
        is_manuscript=False, is_historical_ocr=False, mid=None,
        pdf_url=(
            f"https://pmc-oa-opendata.s3.amazonaws.com/{pmcid}.{version}/"
            f"{pmcid}.{version}.pdf?md5=deadbeef"
        ),
        xml_url=(
            f"https://pmc-oa-opendata.s3.amazonaws.com/{pmcid}.{version}/"
            f"{pmcid}.{version}.xml?md5=deadbeef"
        ),
        text_url=None, media_urls=(),
        raw={"pmcid": pmcid, "version": version},
    )
    defaults.update(overrides)
    return PmcVersionMetadata(**defaults)


def _n_words(n: int, seed: str = "w") -> str:
    return " ".join(f"{seed}{i:04d}" for i in range(n))


def _jats_full_text_body(pmcid: str, n_body_tokens: int = 700) -> bytes:
    """Well-formed JATS with a body that satisfies the V2 predicate."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<article article-type="research-article">'
        "<front><article-meta>"
        f'<article-id pub-id-type="pmc">{pmcid}</article-id>'
        "<title-group><article-title>Title</article-title></title-group>"
        "<abstract><p>Abstract text.</p></abstract>"
        "</article-meta></front>"
        f"<body><sec><p>{_n_words(n_body_tokens)}</p></sec></body>"
        "</article>"
    ).encode()


def _jats_abstract_only(pmcid: str) -> bytes:
    """The exact failure class from B1a-6 V1 — no <body>."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<article article-type="abstract">'
        "<front><article-meta>"
        f'<article-id pub-id-type="pmc">{pmcid}</article-id>'
        "<title-group><article-title>Title</article-title></title-group>"
        "<abstract><p>Only abstract.</p></abstract>"
        "</article-meta></front></article>"
    ).encode()


def _jats_pmcid_wrong(pmcid_in_xml: str) -> bytes:
    """JATS whose internal PMCID contradicts the fetched key."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<article article-type="research-article">'
        "<front><article-meta>"
        f'<article-id pub-id-type="pmc">{pmcid_in_xml}</article-id>'
        "<title-group><article-title>Title</article-title></title-group>"
        "</article-meta></front>"
        f"<body><sec><p>{_n_words(600)}</p></sec></body>"
        "</article>"
    ).encode()


# ---------------------------------------------------------------------


def _make_fake_fetchers(
    metadata_by_id: dict[str, PmcVersionMetadata],
    jats_by_id: dict[str, bytes],
    metadata_calls: list[str] | None = None,
    jats_calls: list[str] | None = None,
    metadata_error: dict[str, Exception] | None = None,
    jats_error: dict[str, Exception] | None = None,
) -> tuple:
    metadata_calls = metadata_calls if metadata_calls is not None else []
    jats_calls = jats_calls if jats_calls is not None else []
    metadata_error = metadata_error or {}
    jats_error = jats_error or {}

    def fake_fetch_metadata(canonical: str):
        metadata_calls.append(canonical)
        if canonical in metadata_error:
            raise metadata_error[canonical]
        md = metadata_by_id[canonical]
        raw = b"{}"
        return md, raw, hashlib.sha256(raw).hexdigest()

    def fake_fetch_jats(md: PmcVersionMetadata):
        cid = f"{md.pmcid}.{md.version}"
        jats_calls.append(cid)
        if cid in jats_error:
            raise jats_error[cid]
        body = jats_by_id[cid]
        return body, hashlib.sha256(body).hexdigest()

    return fake_fetch_metadata, fake_fetch_jats, metadata_calls, jats_calls


# ---------------------------------------------------------------------
# Behaviors
# ---------------------------------------------------------------------


def _build_dev_population(n: int) -> dict[str, int]:
    """Build a PMCID->version map where every PMCID is DEV-assigned.

    DEV assignment uses SHA-256(canonical_id) mod 100 < 10. We iterate
    ``PMC{N}`` until we've collected ``n`` DEV canonical IDs.
    """
    from benchmarks.eval_v1.corpus_split import Partition, assign_partition
    pmcid_to_latest: dict[str, int] = {}
    i = 0
    while len(pmcid_to_latest) < n:
        pmcid = f"PMC{i:07d}"
        if assign_partition(f"{pmcid}.1") is Partition.DEV:
            pmcid_to_latest[pmcid] = 1
        i += 1
    return pmcid_to_latest


def test_metadata_ineligible_never_fetches_jats() -> None:
    """When V1 metadata says reject, the walker must not fetch JATS."""
    dev = _build_dev_population(3)
    canonicals = [f"{pmcid}.1" for pmcid in dev]
    # All three are retracted -> metadata fails.
    metadata = {
        c: _md(c.rsplit(".", 1)[0], 1, is_retracted=True) for c in canonicals
    }
    jats = {c: b"<article/>" for c in canonicals}
    fetch_md, fetch_jats, md_calls, jats_calls = _make_fake_fetchers(metadata, jats)
    eligible, result = rank_walk_v2_eligible_dev(
        dev, excluded_ids=set(), stop_after_eligible=12,
        fetch_metadata=fetch_md, fetch_jats=fetch_jats,
    )
    assert eligible == []
    assert result.metadata_records_probed == 3
    assert result.jats_candidates_fetched == 0
    assert jats_calls == []
    # Rejection reason recorded.
    assert result.rejection_counts_by_reason.get("is_retracted_true") == 3
    # Inspection ledger is empty because no JATS was fetched.
    assert result.inspection_ledger == []


def test_full_text_body_500plus_passes_and_records_ledger() -> None:
    dev = _build_dev_population(2)
    canonicals = [f"{pmcid}.1" for pmcid in dev]
    metadata = {c: _md(c.rsplit(".", 1)[0], 1) for c in canonicals}
    jats = {c: _jats_full_text_body(c.rsplit(".", 1)[0], 800) for c in canonicals}
    fetch_md, fetch_jats, _md_calls, _jats_calls = _make_fake_fetchers(metadata, jats)
    eligible, result = rank_walk_v2_eligible_dev(
        dev, excluded_ids=set(), stop_after_eligible=12,
        fetch_metadata=fetch_md, fetch_jats=fetch_jats,
    )
    assert len(eligible) == 2
    assert result.jats_candidates_fetched == 2
    assert result.eligible_records_found_before_stop == 2
    assert len(result.inspection_ledger) == 2
    for rec in result.inspection_ledger:
        assert isinstance(rec, V2InspectionRecord)
        assert rec.decision_ok is True
        assert rec.decision_reason is None
        assert len(rec.jats_sha256) == 64


def test_abstract_only_rejects_and_records_ledger_entry() -> None:
    dev = _build_dev_population(1)
    canonical = f"{next(iter(dev))}.1"
    metadata = {canonical: _md(canonical.rsplit(".", 1)[0], 1)}
    jats = {canonical: _jats_abstract_only(canonical.rsplit(".", 1)[0])}
    fetch_md, fetch_jats, _, _ = _make_fake_fetchers(metadata, jats)
    eligible, result = rank_walk_v2_eligible_dev(
        dev, excluded_ids=set(), stop_after_eligible=12,
        fetch_metadata=fetch_md, fetch_jats=fetch_jats,
    )
    assert eligible == []
    assert len(result.inspection_ledger) == 1
    rec = result.inspection_ledger[0]
    assert rec.decision_ok is False
    assert rec.decision_reason == "jats_body_absent"
    assert result.rejection_counts_by_reason.get("jats_body_absent") == 1


def test_jats_pmcid_mismatch_rejects_and_records_ledger() -> None:
    dev = _build_dev_population(1)
    pmcid = next(iter(dev))
    canonical = f"{pmcid}.1"
    metadata = {canonical: _md(pmcid, 1)}
    # JATS claims a different PMCID internally.
    jats = {canonical: _jats_pmcid_wrong("PMC9999999")}
    fetch_md, fetch_jats, _, _ = _make_fake_fetchers(metadata, jats)
    eligible, result = rank_walk_v2_eligible_dev(
        dev, excluded_ids=set(), stop_after_eligible=12,
        fetch_metadata=fetch_md, fetch_jats=fetch_jats,
    )
    assert eligible == []
    assert len(result.inspection_ledger) == 1
    rec = result.inspection_ledger[0]
    assert rec.decision_ok is False
    assert rec.decision_reason is not None
    assert rec.decision_reason.startswith("jats_pmcid_mismatch:")


def test_stops_after_stop_after_eligible_hits() -> None:
    """Enough candidates to reach 5 eligible; walker should stop at 5."""
    dev = _build_dev_population(20)
    canonicals = [f"{pmcid}.1" for pmcid in dev]
    metadata = {c: _md(c.rsplit(".", 1)[0], 1) for c in canonicals}
    jats = {c: _jats_full_text_body(c.rsplit(".", 1)[0], 800) for c in canonicals}
    fetch_md, fetch_jats, md_calls, jats_calls = _make_fake_fetchers(metadata, jats)
    eligible, result = rank_walk_v2_eligible_dev(
        dev, excluded_ids=set(), stop_after_eligible=5,
        fetch_metadata=fetch_md, fetch_jats=fetch_jats,
    )
    assert len(eligible) == 5
    # The walker must not have probed beyond what it needed.
    assert result.metadata_records_probed == 5
    assert result.jats_candidates_fetched == 5


def test_excluded_ids_bypass_the_walk_entirely() -> None:
    dev = _build_dev_population(3)
    canonicals = [f"{pmcid}.1" for pmcid in dev]
    # Exclude the FIRST canonical in SHA-rank order.
    from benchmarks.eval_v1.selection.pilot_selector import _rank
    canonicals_sorted = sorted(canonicals, key=_rank)
    excluded = {canonicals_sorted[0]}
    metadata = {c: _md(c.rsplit(".", 1)[0], 1) for c in canonicals}
    jats = {c: _jats_full_text_body(c.rsplit(".", 1)[0], 800) for c in canonicals}
    fetch_md, fetch_jats, md_calls, _ = _make_fake_fetchers(metadata, jats)
    eligible, result = rank_walk_v2_eligible_dev(
        dev, excluded_ids=excluded, stop_after_eligible=12,
        fetch_metadata=fetch_md, fetch_jats=fetch_jats,
    )
    assert result.excluded_before_rank_walk == 1
    # The excluded canonical must never be fetched.
    assert canonicals_sorted[0] not in md_calls


def test_network_exhaustion_on_metadata_propagates() -> None:
    dev = _build_dev_population(1)
    canonical = f"{next(iter(dev))}.1"
    class _Net(Exception): ...
    metadata = {canonical: _md(canonical.rsplit(".", 1)[0], 1)}
    fetch_md, fetch_jats, _, _ = _make_fake_fetchers(
        metadata, {}, metadata_error={canonical: _Net("dns fail")},
    )
    with pytest.raises(_Net):
        rank_walk_v2_eligible_dev(
            dev, excluded_ids=set(), stop_after_eligible=12,
            fetch_metadata=fetch_md, fetch_jats=fetch_jats,
        )


def test_network_exhaustion_on_jats_propagates() -> None:
    dev = _build_dev_population(1)
    canonical = f"{next(iter(dev))}.1"
    class _Net(Exception): ...
    metadata = {canonical: _md(canonical.rsplit(".", 1)[0], 1)}
    fetch_md, fetch_jats, _, _ = _make_fake_fetchers(
        metadata, {canonical: b"unused"}, jats_error={canonical: _Net("timeout")},
    )
    with pytest.raises(_Net):
        rank_walk_v2_eligible_dev(
            dev, excluded_ids=set(), stop_after_eligible=12,
            fetch_metadata=fetch_md, fetch_jats=fetch_jats,
        )


def test_mixed_reject_pass_flow() -> None:
    """One candidate ineligible at metadata, one at V2, one passes."""
    dev = _build_dev_population(3)
    canonicals = [f"{pmcid}.1" for pmcid in dev]
    from benchmarks.eval_v1.selection.pilot_selector import _rank
    canonicals_sorted = sorted(canonicals, key=_rank)
    c1, c2, c3 = canonicals_sorted
    metadata = {
        c1: _md(c1.rsplit(".", 1)[0], 1, is_retracted=True),  # metadata reject
        c2: _md(c2.rsplit(".", 1)[0], 1),                     # metadata ok
        c3: _md(c3.rsplit(".", 1)[0], 1),                     # metadata ok
    }
    jats = {
        c2: _jats_abstract_only(c2.rsplit(".", 1)[0]),        # V2 reject
        c3: _jats_full_text_body(c3.rsplit(".", 1)[0], 800),  # V2 pass
    }
    fetch_md, fetch_jats, _, _ = _make_fake_fetchers(metadata, jats)
    eligible, result = rank_walk_v2_eligible_dev(
        dev, excluded_ids=set(), stop_after_eligible=12,
        fetch_metadata=fetch_md, fetch_jats=fetch_jats,
    )
    assert [c.canonical_id for c in eligible] == [c3]
    assert result.metadata_records_probed == 3
    assert result.jats_candidates_fetched == 2
    assert result.eligible_records_found_before_stop == 1
    assert len(result.inspection_ledger) == 2
    assert result.rejection_counts_by_reason.get("is_retracted_true") == 1
    assert result.rejection_counts_by_reason.get("jats_body_absent") == 1
