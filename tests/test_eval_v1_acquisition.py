"""Synthetic/mocked tests for the B1a-5b.1 acquisition machinery.

No test touches a real network endpoint. Each per-corpus adapter is
wired with fake primitive callables that return real dataclass
instances (from the production primitive modules) built against
files under ``tmp_path``. That way schema drift in the primitives
still surfaces as test failures, but the entire suite is offline and
deterministic.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from benchmarks.eval_v1.acquisition.b1a_5b import doclaynet as dl_adapter_mod
from benchmarks.eval_v1.acquisition.b1a_5b import (
    federal_register as fr_adapter_mod,
)
from benchmarks.eval_v1.acquisition.b1a_5b import orchestrator as orch
from benchmarks.eval_v1.acquisition.b1a_5b import pmc_oa as pmc_adapter_mod
from benchmarks.eval_v1.acquisition.b1a_5b.receipt import (
    ACQUISITION_SCHEMA_VERSION,
    AcquiredAsset,
    AcquisitionReceipt,
    AcquisitionStatus,
    ValidationCheck,
    load_receipt_envelope,
    receipt_from_payload,
    sha256_file,
    sha256_json,
    write_receipt,
)
from benchmarks.eval_v1.acquisition.b1a_5b.retry_policy import (
    RetryExhaustedError,
    RetryPolicy,
    with_retry,
)
from benchmarks.eval_v1.acquisition.doclaynet_hf import (
    AcquiredPage,
    DocLayNetAnnotation,
    DocLayNetPage,
    HfDatasetRef,
)
from benchmarks.eval_v1.acquisition.doclaynet_hf import (
    EligibilityDecision as DlEligibilityDecision,
)
from benchmarks.eval_v1.acquisition.federal_register_api import (
    AcquiredDocument,
    FrDocumentDetail,
)
from benchmarks.eval_v1.acquisition.federal_register_api import (
    AcquisitionError as FrAcquisitionError,
)
from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    AcquiredArticle,
    PmcVersionMetadata,
)
from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    AcquisitionError as PmcAcquisitionError,
)
from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    EligibilityDecision as PmcEligibilityDecision,
)

NO_SLEEP = lambda _s: None  # noqa: E731 - test helper only
FAST_POLICY = RetryPolicy(
    max_attempts=3, initial_backoff_seconds=0.0, max_backoff_seconds=0.0,
    backoff_multiplier=1.0,
)


# ---------------------------------------------------------------------
# Receipt schema
# ---------------------------------------------------------------------


def _make_ok_receipt(canonical_id: str, corpus: str, manifest_sha: str) -> AcquisitionReceipt:
    return AcquisitionReceipt(
        canonical_id=canonical_id,
        corpus=corpus,
        selection_manifest_sha256=manifest_sha,
        acquisition_schema_version=ACQUISITION_SCHEMA_VERSION,
        status=AcquisitionStatus.ACQUIRED.value,
        reason=None,
        acquired_utc="2026-09-16T00:00:00+00:00",
        assets=[],
        validation_checks=[ValidationCheck("dummy", True)],
        provenance={"selected": {"canonical_id": canonical_id}},
    )


def test_receipt_write_load_roundtrip(tmp_path: Path) -> None:
    r = _make_ok_receipt("PMC3569185.1", "pmc_oa", "a" * 64)
    p = tmp_path / "receipt.json"
    write_receipt(p, r)
    body = load_receipt_envelope(p)
    assert set(body).issuperset({"fingerprint", "payload", "payload_sha256"})
    assert body["payload_sha256"] == sha256_json(body["payload"])
    r2 = receipt_from_payload(body["payload"])
    assert r2 == r


def test_receipt_sha256_json_is_canonical() -> None:
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert sha256_json(a) == sha256_json(b)


def test_acquisition_status_values() -> None:
    # Guardrail: the seven documented statuses must exist verbatim.
    expected = {
        "acquired",
        "transient_retrieval_failure",
        "selected_acquisition_failure",
        "identity_mismatch",
        "integrity_failure",
        "upstream_state_changed",
        "reused_verified_receipt",
    }
    assert {s.value for s in AcquisitionStatus} == expected


# ---------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------


def test_retry_happy_path_no_retries() -> None:
    calls: list[int] = []

    def fn() -> int:
        calls.append(1)
        return 42

    assert with_retry(fn, policy=FAST_POLICY, sleep=NO_SLEEP) == 42
    assert len(calls) == 1


def test_retry_exhausts_on_persistent_5xx() -> None:
    err = urllib.error.HTTPError(
        url="https://x/y", code=503, msg="Service Unavailable",
        hdrs=None, fp=None,
    )
    calls: list[int] = []

    def fn() -> None:
        calls.append(1)
        raise err

    with pytest.raises(RetryExhaustedError) as e:
        with_retry(fn, policy=FAST_POLICY, sleep=NO_SLEEP)
    assert len(calls) == FAST_POLICY.max_attempts
    assert e.value.last_error is err


def test_retry_immediate_on_permanent_4xx() -> None:
    err = urllib.error.HTTPError(
        url="https://x/y", code=404, msg="Not Found", hdrs=None, fp=None,
    )
    calls: list[int] = []

    def fn() -> None:
        calls.append(1)
        raise err

    with pytest.raises(urllib.error.HTTPError):
        with_retry(fn, policy=FAST_POLICY, sleep=NO_SLEEP)
    assert len(calls) == 1


def test_retry_408_and_429_are_retried() -> None:
    for code in (408, 429):
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise urllib.error.HTTPError(
                url="https://x/y", code=code, msg="", hdrs=None, fp=None,
            )
        with pytest.raises(RetryExhaustedError):
            with_retry(fn, policy=FAST_POLICY, sleep=NO_SLEEP)
        assert len(calls) == FAST_POLICY.max_attempts, f"code={code}"


def test_retry_after_header_honored() -> None:
    class _Hdrs:
        def get(self, key: str) -> str | None:
            return "7" if key == "Retry-After" else None

    err = urllib.error.HTTPError(
        url="https://x/y", code=429, msg="", hdrs=_Hdrs(), fp=None,  # type: ignore[arg-type]
    )
    slept: list[float] = []

    def fn() -> None:
        raise err

    with pytest.raises(RetryExhaustedError):
        with_retry(fn, policy=FAST_POLICY, sleep=slept.append)
    # 3 attempts → 2 sleeps, each honoring the header
    assert slept == [7.0, 7.0]


def test_retry_transient_then_success() -> None:
    seq = [
        urllib.error.HTTPError("u", 503, "", None, None),
        urllib.error.HTTPError("u", 502, "", None, None),
    ]

    def fn() -> str:
        if seq:
            raise seq.pop(0)
        return "ok"

    assert with_retry(fn, policy=FAST_POLICY, sleep=NO_SLEEP) == "ok"


# ---------------------------------------------------------------------
# PMC-OA adapter
# ---------------------------------------------------------------------


def _pmc_md(pmcid: str = "PMC3569185", version: int = 1, **overrides: Any) -> PmcVersionMetadata:
    defaults = dict(
        pmcid=pmcid, version=version, title="t", citation=None, doi=None, pmid=None,
        license_code="CC BY", is_pmc_openaccess=True, is_retracted=False,
        is_manuscript=False, is_historical_ocr=False, mid=None,
        pdf_url=f"https://pmc-oa-opendata.s3.amazonaws.com/{pmcid}.{version}/{pmcid}.{version}.pdf?md5=deadbeef",
        xml_url=f"https://pmc-oa-opendata.s3.amazonaws.com/{pmcid}.{version}/{pmcid}.{version}.xml?md5=deadbeef",
        text_url=None, media_urls=(), raw={"pmcid": pmcid, "version": version},
    )
    defaults.update(overrides)
    return PmcVersionMetadata(**defaults)


def _write_pmc_assets(root: Path, pmcid: str, version: int, xml_bytes: bytes = b"<article/>") -> AcquiredArticle:
    directory = root / f"{pmcid}.{version}"
    directory.mkdir(parents=True, exist_ok=True)
    pdf = directory / f"{pmcid}.{version}.pdf"
    xml = directory / f"{pmcid}.{version}.xml"
    md_json = directory / f"{pmcid}.{version}.json"
    manifest = directory / "manifest.json"
    pdf.write_bytes(b"%PDF-1.5\n%FAKE\n")
    xml.write_bytes(xml_bytes)
    md_json.write_bytes(b"{}")
    manifest.write_text("{}")
    return AcquiredArticle(
        pmcid=pmcid, version=version, directory=directory,
        pdf_path=pdf, xml_path=xml, metadata_json_path=md_json,
        manifest_path=manifest,
    )


def _pmc_adapter(
    tmp_path: Path,
    *,
    md: PmcVersionMetadata | None = None,
    metadata_error: Exception | None = None,
    filters_ok: bool = True,
    filters_reason: str | None = None,
    acquire_error: Exception | None = None,
    jats_ok: bool = True,
    jats_got: str | None = None,
    xml_bytes: bytes = b"<article/>",
) -> pmc_adapter_mod.PmcOaAcquirer:
    md = md if md is not None else _pmc_md()

    def fetch_metadata_fn(canonical_id: str) -> tuple[PmcVersionMetadata, bytes, str]:
        if metadata_error is not None:
            raise metadata_error
        return md, b"{}", "a" * 64

    def apply_filters_fn(_md: PmcVersionMetadata) -> PmcEligibilityDecision:
        return PmcEligibilityDecision(ok=filters_ok, reason=filters_reason)

    def acquire_article_fn(m: PmcVersionMetadata, *args: Any, **kwargs: Any) -> AcquiredArticle:
        if acquire_error is not None:
            raise acquire_error
        return _write_pmc_assets(tmp_path, m.pmcid, m.version, xml_bytes=xml_bytes)

    def jats_pmcid_matches_fn(_xml: bytes, _expected: str) -> tuple[bool, str | None]:
        return jats_ok, jats_got

    return pmc_adapter_mod.PmcOaAcquirer(
        fetch_metadata_fn=fetch_metadata_fn,
        apply_filters_fn=apply_filters_fn,
        acquire_article_fn=acquire_article_fn,
        jats_pmcid_matches_fn=jats_pmcid_matches_fn,
        retry_policy=FAST_POLICY,
        sleep=NO_SLEEP,
    )


def test_pmc_happy_path_yields_acquired(tmp_path: Path) -> None:
    adapter = _pmc_adapter(tmp_path)
    r = adapter.acquire(
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.ACQUIRED.value
    assert {a.role for a in r.assets} == {"metadata", "pdf", "xml"}
    assert r.provenance["upstream_at_acquisition"]["license_code"] == "CC BY"


def test_pmc_upstream_state_changed_when_retracted(tmp_path: Path) -> None:
    md = _pmc_md(is_retracted=True)
    adapter = _pmc_adapter(tmp_path, md=md, filters_ok=False, filters_reason="is_retracted_true")
    r = adapter.acquire(
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.UPSTREAM_STATE_CHANGED.value
    assert r.reason is not None and "is_retracted_true" in r.reason


def test_pmc_identity_mismatch_when_metadata_reports_different_version(tmp_path: Path) -> None:
    # Selected PMC3569185.1 but upstream now says version 2.
    md = _pmc_md(version=2)
    adapter = _pmc_adapter(tmp_path, md=md)
    r = adapter.acquire(
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.IDENTITY_MISMATCH.value


def test_pmc_integrity_failure_when_acquire_raises(tmp_path: Path) -> None:
    adapter = _pmc_adapter(
        tmp_path, acquire_error=PmcAcquisitionError("PDF MD5 mismatch"),
    )
    r = adapter.acquire(
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.INTEGRITY_FAILURE.value


def test_pmc_selected_acquisition_failure_when_retries_exhausted(tmp_path: Path) -> None:
    err = urllib.error.HTTPError("u", 503, "", None, None)
    adapter = _pmc_adapter(tmp_path, metadata_error=err)
    r = adapter.acquire(
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.SELECTED_ACQUISITION_FAILURE.value


def test_pmc_identity_mismatch_on_jats_pmcid(tmp_path: Path) -> None:
    adapter = _pmc_adapter(tmp_path, jats_ok=False, jats_got="PMC9999999")
    r = adapter.acquire(
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.IDENTITY_MISMATCH.value


# ---------------------------------------------------------------------
# DocLayNet adapter
# ---------------------------------------------------------------------


def _dl_page(page_hash: str, n_annots: int = 12) -> DocLayNetPage:
    return DocLayNetPage(
        page_hash=page_hash,
        image_id=1,
        original_filename="x.pdf",
        page_no=1,
        coco_width=1025,
        coco_height=1025,
        original_width=800.0,
        original_height=1000.0,
        doc_category="financial_reports",
        collection="c",
        num_pages=10,
        modalities=("text",),
        png_bytes=b"\x89PNG\r\n\x1a\n",
        pdf_bytes=b"%PDF-1.5\n",
        annotations=tuple(
            DocLayNetAnnotation(
                category_id=(1 if i > 0 else 9),
                category=("Text" if i > 0 else "Table"),
                bbox=(0.0, 0.0, 1.0, 1.0),
                area=1.0,
            )
            for i in range(n_annots)
        ),
    )


def _dl_write_assets(root: Path, page_hash: str, pdf: bool = True) -> AcquiredPage:
    directory = root / page_hash
    directory.mkdir(parents=True, exist_ok=True)
    png = directory / f"{page_hash}.png"
    pdf_path = directory / f"{page_hash}.pdf"
    ann = directory / f"{page_hash}.annotations.json"
    manifest = directory / "manifest.json"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    ann.write_text("[]")
    manifest.write_text("{}")
    if pdf:
        pdf_path.write_bytes(b"%PDF-1.5\n")
    # pdf_path is always a Path (AcquiredPage requires it); the file
    # may or may not exist depending on the pdf=True|False knob.
    return AcquiredPage(
        page_hash=page_hash, directory=directory,
        png_path=png, pdf_path=pdf_path,
        annotations_path=ann, manifest_path=manifest,
    )


def _dl_adapter(
    tmp_path: Path,
    *,
    revision: str = dl_adapter_mod.DOCLAYNET_LOCKED_REVISION,
    fetch_page_returns: DocLayNetPage | None = None,
    fetch_page_error: Exception | None = None,
    filters_ok: bool = True,
    filters_reason: str | None = None,
    acquire_error: Exception | None = None,
    pdf: bool = True,
) -> dl_adapter_mod.DoclaynetAcquirer:

    def resolve_dataset_ref_fn() -> HfDatasetRef:
        return HfDatasetRef(
            dataset_id="docling-project/DocLayNet-v1.2",
            sha=revision,
            last_modified="2026-01-01T00:00:00.000Z",
            api_url="https://huggingface.co/api/datasets/x",
        )

    def fetch_page_fn(page_hash: str, shard_key: str, _ref: HfDatasetRef) -> tuple[DocLayNetPage, str]:
        if fetch_page_error is not None:
            raise fetch_page_error
        page = fetch_page_returns if fetch_page_returns is not None else _dl_page(page_hash)
        return page, "s" * 64

    def apply_page_filters_fn(_row: dict[str, Any]) -> DlEligibilityDecision:
        return DlEligibilityDecision(ok=filters_ok, reason=filters_reason)

    def acquire_page_fn(page: DocLayNetPage, *args: Any, **kwargs: Any) -> AcquiredPage:
        if acquire_error is not None:
            raise acquire_error
        return _dl_write_assets(tmp_path, page.page_hash, pdf=pdf)

    return dl_adapter_mod.DoclaynetAcquirer(
        resolve_dataset_ref_fn=resolve_dataset_ref_fn,
        fetch_page_fn=fetch_page_fn,
        apply_page_filters_fn=apply_page_filters_fn,
        acquire_page_fn=acquire_page_fn,
        retry_policy=FAST_POLICY,
        sleep=NO_SLEEP,
    )


def test_doclaynet_happy_path(tmp_path: Path) -> None:
    ph = "a" * 64
    adapter = _dl_adapter(tmp_path)
    r = adapter.acquire(
        {"canonical_id": ph, "corpus": "doclaynet",
         "metadata": {"shard_key": "data/train-00000-of-00072.parquet"}},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.ACQUIRED.value
    assert {a.role for a in r.assets} == {"png", "pdf", "annotations"}


def test_doclaynet_upstream_revision_drift(tmp_path: Path) -> None:
    adapter = _dl_adapter(tmp_path, revision="deadbeef" + "0" * 32)
    r = adapter.acquire(
        {"canonical_id": "a" * 64, "corpus": "doclaynet",
         "metadata": {"shard_key": "data/train-00000-of-00072.parquet"}},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.UPSTREAM_STATE_CHANGED.value


def test_doclaynet_identity_mismatch_wrong_page_hash(tmp_path: Path) -> None:
    other = _dl_page("b" * 64)
    adapter = _dl_adapter(tmp_path, fetch_page_returns=other)
    r = adapter.acquire(
        {"canonical_id": "a" * 64, "corpus": "doclaynet",
         "metadata": {"shard_key": "data/train-00000-of-00072.parquet"}},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.IDENTITY_MISMATCH.value


def test_doclaynet_identity_mismatch_missing_shard(tmp_path: Path) -> None:
    adapter = _dl_adapter(tmp_path, fetch_page_error=KeyError("no such page"))
    r = adapter.acquire(
        {"canonical_id": "a" * 64, "corpus": "doclaynet",
         "metadata": {"shard_key": "data/train-00000-of-00072.parquet"}},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.IDENTITY_MISMATCH.value


def test_doclaynet_missing_shard_key_in_selection(tmp_path: Path) -> None:
    adapter = _dl_adapter(tmp_path)
    r = adapter.acquire(
        {"canonical_id": "a" * 64, "corpus": "doclaynet", "metadata": {}},
        tmp_path, "m" * 64,
    )
    assert r.status == AcquisitionStatus.IDENTITY_MISMATCH.value


# ---------------------------------------------------------------------
# Federal Register adapter
# ---------------------------------------------------------------------


def _fr_detail(
    doc_num: str = "2025-07879",
    stratum: str = "Rule",
    volume: int = 90,
    pub_date: str = "2025-05-06",
    pdf_url: str = "https://www.govinfo.gov/content/pkg/FR-2025-05-06/pdf/2025-07879.pdf",
    xml_url: str | None = "https://www.federalregister.gov/documents/full_text/xml/2025/05/06/2025-07879.xml",
) -> FrDocumentDetail:
    return FrDocumentDetail(
        document_number=doc_num,
        citation="90 FR 19125",
        publication_date=pub_date,
        volume=volume,
        type=stratum,
        title="Test",
        action=None,
        abstract=None,
        agencies=(),
        start_page=1,
        end_page=2,
        page_length=2,
        docket_ids=(),
        cfr_references=(),
        pdf_url=pdf_url,
        full_text_xml_url=xml_url,
        body_html_url=None,
        raw_text_url=None,
        mods_url=None,
        html_url="https://www.federalregister.gov/documents/2025/05/06/2025-07879/test",
        json_url=None,
        executive_order_number=None,
        presidential_document_number=None,
        raw={"document_number": doc_num},
    )


def _fr_write_assets(root: Path, doc_num: str, xml: bool = True) -> AcquiredDocument:
    directory = root / doc_num
    directory.mkdir(parents=True, exist_ok=True)
    pdf = directory / f"{doc_num}.pdf"
    api_json = directory / f"{doc_num}.api.json"
    manifest = directory / "manifest.json"
    xml_path = directory / f"{doc_num}.xml"
    pdf.write_bytes(b"%PDF-1.5\n")
    api_json.write_bytes(b"{}")
    manifest.write_text("{}")
    if xml:
        xml_path.write_bytes(b"<xml/>")
    # xml_path is always a Path (AcquiredDocument requires it); the
    # file may or may not exist depending on the xml=True|False knob.
    return AcquiredDocument(
        document_number=doc_num, directory=directory,
        pdf_path=pdf, xml_path=xml_path, api_json_path=api_json,
        manifest_path=manifest,
    )


def _fr_adapter(
    tmp_path: Path,
    *,
    detail: FrDocumentDetail | None = None,
    fetch_error: Exception | None = None,
    govinfo_ids: tuple[str, str] = ("FR-2025-05-06", "2025-07879"),
    govinfo_error: Exception | None = None,
    acquire_error: Exception | None = None,
    xml: bool = True,
) -> fr_adapter_mod.FederalRegisterAcquirer:
    detail = detail if detail is not None else _fr_detail()

    def fetch_detail_fn(canonical_id: str) -> tuple[FrDocumentDetail, bytes, str]:
        if fetch_error is not None:
            raise fetch_error
        return detail, b"{}", "d" * 64

    def govinfo_ids_fn(_url: str) -> tuple[str, str]:
        if govinfo_error is not None:
            raise govinfo_error
        return govinfo_ids

    def acquire_document_fn(d: FrDocumentDetail, *args: Any, **kwargs: Any) -> AcquiredDocument:
        if acquire_error is not None:
            raise acquire_error
        return _fr_write_assets(tmp_path, d.document_number, xml=xml)

    return fr_adapter_mod.FederalRegisterAcquirer(
        fetch_detail_fn=fetch_detail_fn,
        acquire_document_fn=acquire_document_fn,
        govinfo_ids_fn=govinfo_ids_fn,
        retry_policy=FAST_POLICY,
        sleep=NO_SLEEP,
    )


def _fr_selected() -> dict[str, Any]:
    return {
        "canonical_id": "2025-07879",
        "corpus": "federal_register",
        "stratum": "Rule",
        "metadata": {
            "document_number": "2025-07879",
            "publication_date": "2025-05-06",
            "type": "Rule",
            "volume": 90,
            "package_id": "FR-2025-05-06",
            "granule_id": "2025-07879",
            "has_xml": True,
        },
    }


def test_federal_register_happy_path_with_xml(tmp_path: Path) -> None:
    adapter = _fr_adapter(tmp_path)
    r = adapter.acquire(_fr_selected(), tmp_path, "m" * 64)
    assert r.status == AcquisitionStatus.ACQUIRED.value
    assert {a.role for a in r.assets} == {"api_json", "pdf", "xml"}
    g2 = next(c for c in r.validation_checks if c.name == "g2_adjudication_support")
    assert g2.reason == "available"


def test_federal_register_happy_path_without_xml(tmp_path: Path) -> None:
    adapter = _fr_adapter(tmp_path, xml=False)
    r = adapter.acquire(_fr_selected(), tmp_path, "m" * 64)
    assert r.status == AcquisitionStatus.ACQUIRED.value
    assert {a.role for a in r.assets} == {"api_json", "pdf"}
    g2 = next(c for c in r.validation_checks if c.name == "g2_adjudication_support")
    assert g2.reason == "unavailable"


def test_federal_register_identity_mismatch_wrong_doc_num(tmp_path: Path) -> None:
    detail = _fr_detail(doc_num="2025-99999")
    adapter = _fr_adapter(tmp_path, detail=detail)
    r = adapter.acquire(_fr_selected(), tmp_path, "m" * 64)
    assert r.status == AcquisitionStatus.IDENTITY_MISMATCH.value


def test_federal_register_upstream_state_changed_on_stratum_drift(tmp_path: Path) -> None:
    detail = _fr_detail(stratum="Notice")
    adapter = _fr_adapter(tmp_path, detail=detail)
    r = adapter.acquire(_fr_selected(), tmp_path, "m" * 64)
    assert r.status == AcquisitionStatus.UPSTREAM_STATE_CHANGED.value


def test_federal_register_integrity_failure_on_non_govinfo_url(tmp_path: Path) -> None:
    adapter = _fr_adapter(
        tmp_path, govinfo_error=FrAcquisitionError("bad url"),
    )
    r = adapter.acquire(_fr_selected(), tmp_path, "m" * 64)
    assert r.status == AcquisitionStatus.INTEGRITY_FAILURE.value


def test_federal_register_identity_mismatch_wrong_package_id(tmp_path: Path) -> None:
    adapter = _fr_adapter(tmp_path, govinfo_ids=("FR-2025-99-99", "2025-07879"))
    r = adapter.acquire(_fr_selected(), tmp_path, "m" * 64)
    assert r.status == AcquisitionStatus.IDENTITY_MISMATCH.value


# ---------------------------------------------------------------------
# Orchestrator + resume
# ---------------------------------------------------------------------


def _tiny_manifest(path: Path, selected: list[dict[str, Any]]) -> None:
    body = {
        "study": "B1",
        "phase": "B1a-5a-dry",
        "selection_algorithm_version": "1",
        "emitted_utc": "2026-09-16T00:00:00+00:00",
        "selected": selected,
        "exclusion_summary": {"union_ledger_path": "docs/evaluation/x.jsonl",
                              "union_ledger_sha256": "0" * 64},
        "cutoff_neighbors": {},
        "corpus_outcomes": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True))


class _StubAdapter:
    def __init__(self, corpus: str, receipt_factory) -> None:  # type: ignore[no-untyped-def]
        self.corpus = corpus
        self._factory = receipt_factory
        self.calls: list[dict[str, Any]] = []

    def acquire(
        self, selected: dict[str, Any], corpus_root: Path, manifest_sha: str,
    ) -> AcquisitionReceipt:
        self.calls.append(selected)
        return self._factory(selected, corpus_root, manifest_sha)


def _stub_receipt(status: AcquisitionStatus, tmp_path: Path):  # type: ignore[no-untyped-def]
    def factory(selected: dict[str, Any], corpus_root: Path, manifest_sha: str) -> AcquisitionReceipt:
        payload_dir = corpus_root / selected["canonical_id"]
        payload_dir.mkdir(parents=True, exist_ok=True)
        pdf = payload_dir / "asset.bin"
        pdf.write_bytes(b"hello")
        return AcquisitionReceipt(
            canonical_id=selected["canonical_id"],
            corpus=selected["corpus"],
            selection_manifest_sha256=manifest_sha,
            acquisition_schema_version=ACQUISITION_SCHEMA_VERSION,
            status=status.value,
            reason=None if status == AcquisitionStatus.ACQUIRED else "synthetic",
            acquired_utc="2026-09-16T00:00:00+00:00",
            assets=[
                AcquiredAsset(
                    role="pdf", source_url=None, source_key=None,
                    source_revision=None, local_path=str(pdf),
                    byte_size=pdf.stat().st_size,
                    sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                )
            ] if status == AcquisitionStatus.ACQUIRED else [],
            validation_checks=[],
            provenance={"corpus": selected["corpus"]},
        )
    return factory


def test_orchestrator_happy_path_writes_receipts_and_aggregate(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _tiny_manifest(manifest, [
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        {"canonical_id": "a" * 64, "corpus": "doclaynet",
         "metadata": {"shard_key": "s"}},
        {"canonical_id": "2025-07879", "corpus": "federal_register",
         "stratum": "Rule", "metadata": {}},
    ])
    receipts_root = tmp_path / "receipts"
    aggregate = tmp_path / "DEV_PILOT_ACQUISITION_V1.json"
    payloads = tmp_path / "corpus"
    adapters = {
        "pmc_oa": _StubAdapter("pmc_oa", _stub_receipt(AcquisitionStatus.ACQUIRED, tmp_path)),
        "doclaynet": _StubAdapter("doclaynet", _stub_receipt(AcquisitionStatus.ACQUIRED, tmp_path)),
        "federal_register": _StubAdapter("federal_register", _stub_receipt(AcquisitionStatus.ACQUIRED, tmp_path)),
    }
    result = orch.run(
        selection_manifest_path=manifest,
        receipts_root=receipts_root,
        aggregate_path=aggregate,
        payloads_root=payloads,
        adapters=adapters,
    )
    assert len(result.receipts) == 3
    assert all(r.status == AcquisitionStatus.ACQUIRED.value for r in result.receipts)
    # Every receipt file is written and self-verifying.
    for r in result.receipts:
        rp = receipts_root / r.corpus / f"{r.canonical_id}.json"
        assert rp.exists()
        body = load_receipt_envelope(rp)
        assert sha256_json(body["payload"]) == body["payload_sha256"]
    # Aggregate carries the manifest SHA and 3 records.
    agg = json.loads(aggregate.read_text())
    assert agg["selection_manifest_sha256"] == result.selection_manifest_sha256
    assert agg["counts"] == {"total": 3, "acquired": 3, "reused_verified_receipt": 0, "non_success": 0}


def test_orchestrator_stops_on_first_non_success(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _tiny_manifest(manifest, [
        {"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"},
        {"canonical_id": "PMC4160324.1", "corpus": "pmc_oa"},
    ])
    calls: list[str] = []

    def factory(status: AcquisitionStatus):  # type: ignore[no-untyped-def]
        def f(selected: dict[str, Any], corpus_root: Path, manifest_sha: str) -> AcquisitionReceipt:
            calls.append(selected["canonical_id"])
            return _stub_receipt(status, tmp_path)(selected, corpus_root, manifest_sha)
        return f

    class _Adapter:
        corpus = "pmc_oa"

        def acquire(self, selected: dict[str, Any], root: Path, sha: str) -> AcquisitionReceipt:
            # First call returns SELECTED_ACQUISITION_FAILURE; second must never run.
            if selected["canonical_id"] == "PMC3569185.1":
                return factory(AcquisitionStatus.SELECTED_ACQUISITION_FAILURE)(selected, root, sha)
            return factory(AcquisitionStatus.ACQUIRED)(selected, root, sha)

    with pytest.raises(orch.OrchestratorStop):
        orch.run(
            selection_manifest_path=manifest,
            receipts_root=tmp_path / "r",
            aggregate_path=tmp_path / "agg.json",
            payloads_root=tmp_path / "p",
            adapters={"pmc_oa": _Adapter()},
        )
    assert calls == ["PMC3569185.1"]


def test_orchestrator_resume_reuses_verified_receipt(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _tiny_manifest(manifest, [{"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"}])
    adapters = {
        "pmc_oa": _StubAdapter("pmc_oa", _stub_receipt(AcquisitionStatus.ACQUIRED, tmp_path)),
    }
    # First run: writes durable receipt.
    r1 = orch.run(
        selection_manifest_path=manifest,
        receipts_root=tmp_path / "receipts",
        aggregate_path=tmp_path / "agg.json",
        payloads_root=tmp_path / "corpus",
        adapters=adapters,
    )
    assert r1.receipts[0].status == AcquisitionStatus.ACQUIRED.value

    # Second run: adapter that would fail if called. Reuse must skip it.
    class _Explode:
        corpus = "pmc_oa"

        def acquire(self, *a: Any, **k: Any) -> AcquisitionReceipt:  # noqa: ARG002
            raise AssertionError("adapter.acquire must not be called on reuse")

    r2 = orch.run(
        selection_manifest_path=manifest,
        receipts_root=tmp_path / "receipts",
        aggregate_path=tmp_path / "agg.json",
        payloads_root=tmp_path / "corpus",
        adapters={"pmc_oa": _Explode()},
    )
    assert len(r2.receipts) == 1
    assert r2.receipts[0].status == AcquisitionStatus.REUSED_VERIFIED_RECEIPT.value


def _run_once_and_get_receipt_path(tmp_path: Path) -> tuple[Path, Path]:
    manifest = tmp_path / "manifest.json"
    _tiny_manifest(manifest, [{"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"}])
    orch.run(
        selection_manifest_path=manifest,
        receipts_root=tmp_path / "receipts",
        aggregate_path=tmp_path / "agg.json",
        payloads_root=tmp_path / "corpus",
        adapters={"pmc_oa": _StubAdapter(
            "pmc_oa", _stub_receipt(AcquisitionStatus.ACQUIRED, tmp_path)
        )},
    )
    return manifest, tmp_path / "receipts" / "pmc_oa" / "PMC3569185.1.json"


def test_orchestrator_resume_stops_when_manifest_sha_drifts(tmp_path: Path) -> None:
    manifest, _rp = _run_once_and_get_receipt_path(tmp_path)
    # Mutate the manifest (rewriting selected preserves canonical_id but
    # the whole-file SHA differs).
    body = json.loads(manifest.read_text())
    body["emitted_utc"] = "2099-01-01T00:00:00+00:00"
    manifest.write_text(json.dumps(body, indent=2, sort_keys=True))

    class _Explode:
        corpus = "pmc_oa"

        def acquire(self, *a: Any, **k: Any) -> AcquisitionReceipt:  # noqa: ARG002
            raise AssertionError("adapter.acquire must not be called")
    with pytest.raises(orch.OrchestratorStop) as e:
        orch.run(
            selection_manifest_path=manifest,
            receipts_root=tmp_path / "receipts",
            aggregate_path=tmp_path / "agg.json",
            payloads_root=tmp_path / "corpus",
            adapters={"pmc_oa": _Explode()},
        )
    assert "selection_manifest_sha256" in str(e.value)


def test_orchestrator_resume_stops_when_local_payload_missing(tmp_path: Path) -> None:
    manifest, rp = _run_once_and_get_receipt_path(tmp_path)
    # Delete the payload the receipt refers to.
    body = load_receipt_envelope(rp)
    payload_path = Path(body["payload"]["assets"][0]["local_path"])
    payload_path.unlink()

    class _Explode:
        corpus = "pmc_oa"

        def acquire(self, *a: Any, **k: Any) -> AcquisitionReceipt:  # noqa: ARG002
            raise AssertionError("adapter.acquire must not be called")
    with pytest.raises(orch.OrchestratorStop) as e:
        orch.run(
            selection_manifest_path=manifest,
            receipts_root=tmp_path / "receipts",
            aggregate_path=tmp_path / "agg.json",
            payloads_root=tmp_path / "corpus",
            adapters={"pmc_oa": _Explode()},
        )
    assert "asset missing" in str(e.value) or "missing on disk" in str(e.value)


def test_orchestrator_resume_stops_when_local_payload_sha_drifts(tmp_path: Path) -> None:
    manifest, rp = _run_once_and_get_receipt_path(tmp_path)
    body = load_receipt_envelope(rp)
    payload_path = Path(body["payload"]["assets"][0]["local_path"])
    payload_path.write_bytes(b"tampered")

    class _Explode:
        corpus = "pmc_oa"

        def acquire(self, *a: Any, **k: Any) -> AcquisitionReceipt:  # noqa: ARG002
            raise AssertionError("adapter.acquire must not be called")
    with pytest.raises(orch.OrchestratorStop) as e:
        orch.run(
            selection_manifest_path=manifest,
            receipts_root=tmp_path / "receipts",
            aggregate_path=tmp_path / "agg.json",
            payloads_root=tmp_path / "corpus",
            adapters={"pmc_oa": _Explode()},
        )
    assert "sha256 drift" in str(e.value)


def test_orchestrator_resume_stops_when_stored_status_is_not_acquired(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _tiny_manifest(manifest, [{"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"}])
    rp = tmp_path / "receipts" / "pmc_oa" / "PMC3569185.1.json"
    # Hand-write a receipt with UPSTREAM_STATE_CHANGED — reuse must not be granted.
    r = AcquisitionReceipt(
        canonical_id="PMC3569185.1", corpus="pmc_oa",
        selection_manifest_sha256=sha256_file(manifest),
        acquisition_schema_version=ACQUISITION_SCHEMA_VERSION,
        status=AcquisitionStatus.UPSTREAM_STATE_CHANGED.value,
        reason="synthetic",
        acquired_utc="2026-09-16T00:00:00+00:00",
        assets=[], validation_checks=[], provenance={},
    )
    write_receipt(rp, r)

    class _Explode:
        corpus = "pmc_oa"

        def acquire(self, *a: Any, **k: Any) -> AcquisitionReceipt:  # noqa: ARG002
            raise AssertionError("adapter.acquire must not be called")
    with pytest.raises(orch.OrchestratorStop) as e:
        orch.run(
            selection_manifest_path=manifest,
            receipts_root=tmp_path / "receipts",
            aggregate_path=tmp_path / "agg.json",
            payloads_root=tmp_path / "corpus",
            adapters={"pmc_oa": _Explode()},
        )
    assert "cannot reuse" in str(e.value)


def test_orchestrator_never_writes_to_selection_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _tiny_manifest(manifest, [{"canonical_id": "PMC3569185.1", "corpus": "pmc_oa"}])
    manifest_bytes_before = manifest.read_bytes()
    manifest_sha_before = sha256_file(manifest)
    orch.run(
        selection_manifest_path=manifest,
        receipts_root=tmp_path / "receipts",
        aggregate_path=tmp_path / "agg.json",
        payloads_root=tmp_path / "corpus",
        adapters={"pmc_oa": _StubAdapter(
            "pmc_oa", _stub_receipt(AcquisitionStatus.ACQUIRED, tmp_path)
        )},
    )
    assert manifest.read_bytes() == manifest_bytes_before
    assert sha256_file(manifest) == manifest_sha_before


# ---------------------------------------------------------------------
# Locked FR date range (regression guard)
# ---------------------------------------------------------------------


def test_federal_register_locked_pub_dates_match_selection() -> None:
    assert fr_adapter_mod.FR_LOCKED_PUB_DATE_GTE == date(2025, 1, 1)
    assert fr_adapter_mod.FR_LOCKED_PUB_DATE_LTE == date(2025, 12, 31)


def test_doclaynet_locked_revision_matches_selection() -> None:
    assert dl_adapter_mod.DOCLAYNET_LOCKED_DATASET_ID == "docling-project/DocLayNet-v1.2"
    assert dl_adapter_mod.DOCLAYNET_LOCKED_REVISION == (
        "0daf93102e2efce76c3e11a274a5e0d0969391d3"
    )
    assert dl_adapter_mod.DOCLAYNET_LOCKED_SPLIT == "train"
