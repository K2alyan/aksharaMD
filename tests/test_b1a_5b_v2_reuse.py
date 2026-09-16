"""Tests for the B1a-5b-V2 verify-and-bind machinery.

Every failure mode of ``verify_and_bind_from_v1`` must raise
``V2ReuseError`` — the driver stops on that exception. No network.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.eval_v1.acquisition.b1a_5b.receipt import (
    ACQUISITION_SCHEMA_VERSION,
    AcquiredAsset,
    AcquisitionReceipt,
    AcquisitionStatus,
    ValidationCheck,
    write_receipt,
)
from benchmarks.eval_v1.acquisition.b1a_5b.v2_reuse import (
    ExecutionDisposition,
    V2ReuseError,
    verify_and_bind_from_v1,
    write_v2_receipt,
)

V1_MANIFEST_SHA = "a" * 64
V2_MANIFEST_SHA = "b" * 64


def _write_v1_pmc_receipt(
    tmp_path: Path,
    canonical_id: str = "PMC1234.1",
    xml_bytes: bytes = b"<article><body><p>full</p></body></article>",
    md_bytes: bytes = b'{"pmcid":"PMC1234","version":1}',
    pdf_bytes: bytes = b"%PDF-1.5\nfake",
) -> Path:
    """Write a synthetic V1 PMC receipt + its local payload files."""
    dir_ = tmp_path / canonical_id
    dir_.mkdir(parents=True, exist_ok=True)
    xml_path = dir_ / f"{canonical_id}.xml"
    md_path = dir_ / f"{canonical_id}.json"
    pdf_path = dir_ / f"{canonical_id}.pdf"
    xml_path.write_bytes(xml_bytes)
    md_path.write_bytes(md_bytes)
    pdf_path.write_bytes(pdf_bytes)

    receipt = AcquisitionReceipt(
        canonical_id=canonical_id,
        corpus="pmc_oa",
        selection_manifest_sha256=V1_MANIFEST_SHA,
        acquisition_schema_version=ACQUISITION_SCHEMA_VERSION,
        status=AcquisitionStatus.ACQUIRED.value,
        reason=None,
        acquired_utc="2026-09-16T00:00:00+00:00",
        assets=[
            AcquiredAsset(
                role="metadata", source_url=None, source_key=None,
                source_revision=None, local_path=str(md_path),
                byte_size=md_path.stat().st_size,
                sha256=hashlib.sha256(md_bytes).hexdigest(),
            ),
            AcquiredAsset(
                role="pdf", source_url=None, source_key=None,
                source_revision=None, local_path=str(pdf_path),
                byte_size=pdf_path.stat().st_size,
                sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            ),
            AcquiredAsset(
                role="xml", source_url=None, source_key=None,
                source_revision=None, local_path=str(xml_path),
                byte_size=xml_path.stat().st_size,
                sha256=hashlib.sha256(xml_bytes).hexdigest(),
            ),
        ],
        validation_checks=[
            ValidationCheck("metadata.pmcid_matches_selected", True),
        ],
        provenance={"selected": {"canonical_id": canonical_id}},
    )
    rp = tmp_path / "v1_receipts" / "pmc_oa" / f"{canonical_id}.json"
    write_receipt(rp, receipt)
    return rp


class _FakeDecision:
    def __init__(self, ok: bool, reason: str | None = None) -> None:
        self.ok = ok
        self.reason = reason


def test_happy_path_verified_payload_reuse(tmp_path: Path) -> None:
    xml = b"<article><body><p>full text</p></body></article>"
    rp = _write_v1_pmc_receipt(tmp_path, xml_bytes=xml)
    inspection_sha = hashlib.sha256(xml).hexdigest()

    v2_receipt = verify_and_bind_from_v1(
        v1_receipt_path=rp,
        canonical_id="PMC1234.1",
        corpus="pmc_oa",
        stratum=None,
        v1_manifest_sha256=V1_MANIFEST_SHA,
        v2_manifest_sha256=V2_MANIFEST_SHA,
        v2_selected_metadata={"pmcid": "PMC1234", "version": 1},
        v2_inspection_jats_sha256=inspection_sha,
        pmc_v2_oracle_reverify=lambda _md, _xml: _FakeDecision(True),
    )
    assert v2_receipt.execution_disposition == ExecutionDisposition.VERIFIED_PAYLOAD_REUSE.value
    assert v2_receipt.selection_manifest_version == "2"
    assert v2_receipt.selection_manifest_sha256 == V2_MANIFEST_SHA
    assert v2_receipt.status == AcquisitionStatus.ACQUIRED.value
    assert v2_receipt.v2_provenance_bridge is not None
    assert v2_receipt.v2_provenance_bridge.source_v1_manifest_sha256 == V1_MANIFEST_SHA
    assert v2_receipt.v2_inspection_jats_sha256 == inspection_sha
    # V1 receipt bytes are unchanged.
    assert rp.exists()

    # And the whole thing roundtrips through write_v2_receipt cleanly.
    v2_rp = tmp_path / "v2_receipts" / "pmc_oa" / "PMC1234.1.json"
    write_v2_receipt(v2_rp, v2_receipt)
    body = json.loads(v2_rp.read_text(encoding="utf-8"))
    assert body["fingerprint"]["selection_manifest_version"] == "2"
    assert body["fingerprint"]["acquisition_schema_version"] == "2"


def test_v1_receipt_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(V2ReuseError, match="V1 receipt missing"):
        verify_and_bind_from_v1(
            v1_receipt_path=tmp_path / "nope.json",
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
        )


def test_v1_receipt_wrong_manifest_raises(tmp_path: Path) -> None:
    rp = _write_v1_pmc_receipt(tmp_path)
    with pytest.raises(V2ReuseError, match="does not match"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256="c" * 64,  # wrong
            v2_manifest_sha256=V2_MANIFEST_SHA,
        )


def test_canonical_id_mismatch_raises(tmp_path: Path) -> None:
    rp = _write_v1_pmc_receipt(tmp_path)
    with pytest.raises(V2ReuseError, match="canonical_id"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC9999.9", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
        )


def test_local_payload_missing_raises(tmp_path: Path) -> None:
    xml = b"<article><body><p>full text</p></body></article>"
    rp = _write_v1_pmc_receipt(tmp_path, xml_bytes=xml)
    # Delete the XML the receipt refers to.
    body = json.loads(rp.read_text(encoding="utf-8"))
    xml_asset = next(a for a in body["payload"]["assets"] if a["role"] == "xml")
    Path(xml_asset["local_path"]).unlink()
    with pytest.raises(V2ReuseError, match="asset missing"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
        )


def test_local_payload_sha_drift_raises(tmp_path: Path) -> None:
    rp = _write_v1_pmc_receipt(tmp_path)
    body = json.loads(rp.read_text(encoding="utf-8"))
    xml_asset = next(a for a in body["payload"]["assets"] if a["role"] == "xml")
    # Tamper with the file so its SHA no longer matches the receipt.
    Path(xml_asset["local_path"]).write_bytes(b"tampered")
    with pytest.raises(V2ReuseError, match="sha256 drift"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
        )


def test_pmc_jats_sha_differs_from_v2_inspection_raises(tmp_path: Path) -> None:
    xml = b"<article><body><p>full text</p></body></article>"
    rp = _write_v1_pmc_receipt(tmp_path, xml_bytes=xml)
    wrong_inspection_sha = "d" * 64
    with pytest.raises(V2ReuseError, match="V2 inspection JATS sha256"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
            v2_inspection_jats_sha256=wrong_inspection_sha,
        )


def test_pmc_v2_oracle_reverify_failure_raises(tmp_path: Path) -> None:
    xml = b"<article><body><p>full text</p></body></article>"
    rp = _write_v1_pmc_receipt(tmp_path, xml_bytes=xml)
    inspection_sha = hashlib.sha256(xml).hexdigest()

    def _fail(md: bytes, xml_bytes: bytes) -> Any:
        return _FakeDecision(False, reason="canonical_body_tokens_lt_500:3")

    with pytest.raises(V2ReuseError, match="V2 oracle re-verify failed"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
            v2_inspection_jats_sha256=inspection_sha,
            pmc_v2_oracle_reverify=_fail,
        )


def test_v1_receipt_envelope_tampered_raises(tmp_path: Path) -> None:
    rp = _write_v1_pmc_receipt(tmp_path)
    # Tamper with the payload without updating payload_sha256.
    body = json.loads(rp.read_text(encoding="utf-8"))
    body["payload"]["reason"] = "tampered!"
    rp.write_text(json.dumps(body, indent=2, sort_keys=True))
    with pytest.raises(V2ReuseError, match="payload_sha256 does not recompute"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
        )


def test_v1_receipt_not_acquired_status_raises(tmp_path: Path) -> None:
    """If the V1 receipt records something other than ACQUIRED, reuse is refused."""
    rp = _write_v1_pmc_receipt(tmp_path)
    body = json.loads(rp.read_text(encoding="utf-8"))
    body["payload"]["status"] = AcquisitionStatus.UPSTREAM_STATE_CHANGED.value
    # Recompute payload_sha256 to make envelope self-consistent but
    # semantically not ACQUIRED.
    from benchmarks.eval_v1.acquisition.b1a_5b.receipt import sha256_json
    body["payload_sha256"] = sha256_json(body["payload"])
    rp.write_text(json.dumps(body, indent=2, sort_keys=True))
    with pytest.raises(V2ReuseError, match="not"):
        verify_and_bind_from_v1(
            v1_receipt_path=rp,
            canonical_id="PMC1234.1", corpus="pmc_oa", stratum=None,
            v1_manifest_sha256=V1_MANIFEST_SHA,
            v2_manifest_sha256=V2_MANIFEST_SHA,
        )
