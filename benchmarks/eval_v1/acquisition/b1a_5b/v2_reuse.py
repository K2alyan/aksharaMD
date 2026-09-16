"""V2 acquisition receipt schema + verified-payload-reuse machinery.

For 18 of the 20 documents selected in ``DEV_PILOT_MANIFEST_V2.json``,
the payload bytes already exist on disk from the B1a-5b.2 (V1)
acquisition. B1a-5b-V2 does NOT redownload them — it verifies the
existing bytes and issues a NEW V2 receipt that binds those verified
bytes to the V2 manifest.

Distinction locked with the reviewer:

- **Physical payload reuse** — the acquired PDF/XML/PNG bytes on disk
  are the same objects the V1 acquisition wrote. They are re-hashed
  and cross-checked against the V1 receipt.
- **Provenance receipt reuse** — is NOT done. V1 receipts stay
  byte-identical and continue to bind to ``MANIFEST_V1``. The V2
  receipt is a NEW artifact that binds to ``MANIFEST_V2`` and
  carries a small provenance bridge back to the V1 receipt.

  So we do NOT pretend the V1 receipt belonged to V2. The V2 receipt
  says "the bytes recorded in this V1 receipt still hash the same
  values and I verified them in the context of MANIFEST_V2".

Fail-closed at every gate. No redownload of a retained asset merely
because its local copy failed verification — that would erase
evidence of the discrepancy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.acquisition.b1a_5b.receipt import (
    AcquiredAsset,
    AcquisitionStatus,
    ValidationCheck,
    load_receipt_envelope,
    receipt_from_payload,
    sha256_file,
    sha256_json,
)

# The V1 receipt schema version stays "1". V2 receipts advance the
# schema version to "2" so consumers can pattern-match on the
# fingerprint's ``acquisition_schema_version`` and know they're
# looking at a V2 delta receipt with the bridge fields.
V2_ACQUISITION_SCHEMA_VERSION = "2"


class ExecutionDisposition(StrEnum):
    """How this V2 receipt was produced."""

    VERIFIED_PAYLOAD_REUSE = "verified_payload_reuse"
    """The physical bytes on disk pre-existed from V1 acquisition and
    were verified in the context of the V2 manifest. No network
    fetch. A V1 receipt path + SHA + manifest SHA are recorded in
    the provenance bridge."""

    FRESH_ACQUISITION = "fresh_acquisition"
    """The bytes were fetched fresh under B1a-5b-V2 authorization.
    A V2 canonical id newly selected under Selection Algorithm V2."""


@dataclass(frozen=True)
class V2ProvenanceBridge:
    """Link back to the V1 receipt this V2 receipt verifies against.

    Populated only for ``verified_payload_reuse``. Its purpose is
    audit clarity: any reviewer inspecting the V2 receipt can walk
    back one step to the exact V1 receipt that first attested to
    these bytes, without ambiguity.
    """

    source_v1_receipt_path: str
    source_v1_receipt_sha256: str
    source_v1_manifest_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_v1_receipt_path": self.source_v1_receipt_path,
            "source_v1_receipt_sha256": self.source_v1_receipt_sha256,
            "source_v1_manifest_sha256": self.source_v1_manifest_sha256,
        }


@dataclass(frozen=True)
class V2AcquisitionReceipt:
    """Immutable per-document V2 acquisition receipt.

    Superset of the V1 receipt schema with an ``execution_disposition``
    label and an optional provenance bridge to the source V1 receipt.
    Emitted via the same self-verifying envelope shape
    (``{fingerprint, payload, payload_sha256}``) used by V1 receipts,
    so the on-disk validation contract is unchanged.
    """

    canonical_id: str
    corpus: str
    selection_manifest_version: str
    selection_manifest_sha256: str
    acquisition_schema_version: str
    execution_disposition: str
    status: str
    reason: str | None
    acquired_utc: str
    assets: list[AcquiredAsset]
    validation_checks: list[ValidationCheck]
    provenance: dict[str, Any]
    v2_provenance_bridge: V2ProvenanceBridge | None = None
    v2_inspection_jats_sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "canonical_id": self.canonical_id,
            "corpus": self.corpus,
            "selection_manifest_version": self.selection_manifest_version,
            "selection_manifest_sha256": self.selection_manifest_sha256,
            "acquisition_schema_version": self.acquisition_schema_version,
            "execution_disposition": self.execution_disposition,
            "status": self.status,
            "reason": self.reason,
            "acquired_utc": self.acquired_utc,
            "assets": [a.as_dict() for a in self.assets],
            "validation_checks": [c.as_dict() for c in self.validation_checks],
            "provenance": dict(self.provenance),
            "v2_inspection_jats_sha256": self.v2_inspection_jats_sha256,
        }
        d["v2_provenance_bridge"] = (
            self.v2_provenance_bridge.as_dict()
            if self.v2_provenance_bridge is not None else None
        )
        return d


def write_v2_receipt(path: Path, receipt: V2AcquisitionReceipt) -> None:
    """Atomically write a V2 receipt with a self-verifying envelope.

    Layout matches V1: ``{"fingerprint": {...}, "payload": {...},
    "payload_sha256": "..."}``. The fingerprint records V2 identity
    so a resume check would refuse a V2 receipt against a V1 manifest
    or vice versa.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = receipt.as_dict()
    body = {
        "fingerprint": {
            "canonical_id": receipt.canonical_id,
            "corpus": receipt.corpus,
            "selection_manifest_version": receipt.selection_manifest_version,
            "selection_manifest_sha256": receipt.selection_manifest_sha256,
            "acquisition_schema_version": receipt.acquisition_schema_version,
        },
        "payload": payload,
        "payload_sha256": sha256_json(payload),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True))
    tmp.replace(path)


class V2ReuseError(RuntimeError):
    """Any failure in verify-and-bind. The caller STOPS the run."""


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def verify_and_bind_from_v1(
    *,
    v1_receipt_path: Path,
    canonical_id: str,
    corpus: str,
    stratum: str | None,
    v1_manifest_sha256: str,
    v2_manifest_sha256: str,
    v2_selected_metadata: dict[str, Any] | None = None,
    v2_inspection_jats_sha256: str | None = None,
    pmc_v2_oracle_reverify: Any = None,
) -> V2AcquisitionReceipt:
    """Verify a V1 acquisition receipt + its local bytes and produce
    a V2 receipt bound to ``MANIFEST_V2.json`` at
    ``v2_manifest_sha256``.

    Fail-closed at every step; raises :class:`V2ReuseError` on any
    integrity, identity, or eligibility violation. The V1 receipt is
    NEVER modified.

    Corpus-specific:

    - PMC-OA: if ``v2_inspection_jats_sha256`` is provided (populated
      by the caller from the V2 inspection ledger for this canonical
      id), we require the local acquired JATS asset's SHA-256 to
      match. ``pmc_v2_oracle_reverify`` is an optional callable that
      takes ``(md_bytes, xml_bytes) -> TextOracleDecision`` and, when
      provided, is invoked as a final gate; a failing decision raises.

    - DocLayNet / Federal Register: identity checks come from the
      receipt's own validation_checks (recorded at V1 acquisition
      time) plus the local-asset SHA recomputation.
    """
    checks: list[ValidationCheck] = []

    # Step 1: load + envelope integrity.
    if not v1_receipt_path.exists():
        raise V2ReuseError(f"V1 receipt missing: {v1_receipt_path}")
    v1_receipt_bytes = v1_receipt_path.read_bytes()
    v1_receipt_sha = hashlib.sha256(v1_receipt_bytes).hexdigest()
    body = load_receipt_envelope(v1_receipt_path)
    if not isinstance(body, dict) or {"fingerprint", "payload", "payload_sha256"} - body.keys():
        raise V2ReuseError(f"{v1_receipt_path}: envelope shape invalid")
    if sha256_json(body["payload"]) != body["payload_sha256"]:
        raise V2ReuseError(f"{v1_receipt_path}: payload_sha256 does not recompute")
    checks.append(ValidationCheck("v1_receipt_envelope_valid", True))

    payload = body["payload"]
    v1_receipt = receipt_from_payload(payload)

    # Step 2: V1 receipt actually binds to MANIFEST_V1.
    if v1_receipt.selection_manifest_sha256 != v1_manifest_sha256:
        raise V2ReuseError(
            f"{v1_receipt_path}: V1 receipt selection_manifest_sha256="
            f"{v1_receipt.selection_manifest_sha256!r} does not match "
            f"expected V1 manifest SHA={v1_manifest_sha256!r}"
        )
    checks.append(ValidationCheck("v1_receipt_binds_to_v1_manifest", True))

    # Step 3: canonical id + corpus match.
    if v1_receipt.canonical_id != canonical_id:
        raise V2ReuseError(
            f"{v1_receipt_path}: canonical_id={v1_receipt.canonical_id!r} "
            f"expected {canonical_id!r}"
        )
    if v1_receipt.corpus != corpus:
        raise V2ReuseError(
            f"{v1_receipt_path}: corpus={v1_receipt.corpus!r} expected {corpus!r}"
        )
    if v1_receipt.status != AcquisitionStatus.ACQUIRED.value:
        raise V2ReuseError(
            f"{v1_receipt_path}: V1 receipt status={v1_receipt.status!r} not "
            f"{AcquisitionStatus.ACQUIRED.value!r}"
        )
    checks.append(ValidationCheck("v1_canonical_id_and_corpus_match", True))

    # Step 4: every recorded local asset exists + SHA-256 recomputes.
    for asset in v1_receipt.assets:
        p = Path(asset.local_path)
        if not p.exists():
            raise V2ReuseError(
                f"{v1_receipt_path}: recorded asset missing: {p}"
            )
        recomputed = sha256_file(p)
        if recomputed != asset.sha256:
            raise V2ReuseError(
                f"{v1_receipt_path}: asset {asset.role!r} sha256 drift — "
                f"stored {asset.sha256!r} recomputed {recomputed!r}"
            )
    checks.append(ValidationCheck("local_assets_rehash_matches_v1_receipt", True))

    # Step 5: corpus-specific gate — PMC JATS SHA cross-check + optional V2 oracle re-verify.
    if corpus == "pmc_oa":
        xml_asset = next((a for a in v1_receipt.assets if a.role == "xml"), None)
        if xml_asset is None:
            raise V2ReuseError(f"{v1_receipt_path}: no xml asset on PMC receipt")
        if v2_inspection_jats_sha256 is not None:
            if xml_asset.sha256 != v2_inspection_jats_sha256:
                raise V2ReuseError(
                    f"{v1_receipt_path}: acquired JATS sha256 "
                    f"{xml_asset.sha256!r} != V2 inspection JATS sha256 "
                    f"{v2_inspection_jats_sha256!r} — refuse to reuse; "
                    "upstream may have shifted between V2 selection "
                    "inspection and V1 acquisition"
                )
            checks.append(ValidationCheck(
                "pmc_acquired_jats_sha_matches_v2_inspection", True,
            ))
        if pmc_v2_oracle_reverify is not None:
            xml_bytes = Path(xml_asset.local_path).read_bytes()
            metadata_asset = next(
                (a for a in v1_receipt.assets if a.role == "metadata"), None,
            )
            if metadata_asset is None:
                raise V2ReuseError(f"{v1_receipt_path}: no metadata asset on PMC receipt")
            md_bytes = Path(metadata_asset.local_path).read_bytes()
            decision = pmc_v2_oracle_reverify(md_bytes, xml_bytes)
            if not decision.ok:
                raise V2ReuseError(
                    f"{v1_receipt_path}: V2 oracle re-verify failed: "
                    f"{decision.reason}"
                )
            checks.append(ValidationCheck(
                "pmc_v2_oracle_reverify_passed", True,
            ))

    provenance: dict[str, Any] = {
        "selected_v2": {
            "canonical_id": canonical_id,
            "corpus": corpus,
            "stratum": stratum,
            "metadata_at_selection": v2_selected_metadata or {},
        },
        "v1_receipt_provenance": dict(v1_receipt.provenance),
    }
    bridge = V2ProvenanceBridge(
        source_v1_receipt_path=v1_receipt_path.as_posix(),
        source_v1_receipt_sha256=v1_receipt_sha,
        source_v1_manifest_sha256=v1_manifest_sha256,
    )
    return V2AcquisitionReceipt(
        canonical_id=canonical_id,
        corpus=corpus,
        selection_manifest_version="2",
        selection_manifest_sha256=v2_manifest_sha256,
        acquisition_schema_version=V2_ACQUISITION_SCHEMA_VERSION,
        execution_disposition=ExecutionDisposition.VERIFIED_PAYLOAD_REUSE.value,
        status=AcquisitionStatus.ACQUIRED.value,
        reason=None,
        acquired_utc=_now_utc(),
        assets=list(v1_receipt.assets),
        validation_checks=checks,
        provenance=provenance,
        v2_provenance_bridge=bridge,
        v2_inspection_jats_sha256=v2_inspection_jats_sha256,
    )


def wrap_fresh_v1_receipt_as_v2(
    *,
    v1_receipt_bytes_on_disk_path: Path,
    v2_manifest_sha256: str,
    v2_selected_metadata: dict[str, Any] | None = None,
    v2_inspection_jats_sha256: str | None = None,
) -> V2AcquisitionReceipt:
    """After a fresh acquisition (via the merged B1a-5b.1 PmcOaAcquirer)
    has written a V1-schema receipt into the V2 acquisition namespace,
    re-emit it as a V2 receipt so the whole V2 chain has one consistent
    schema.

    ``v2_inspection_jats_sha256`` MUST equal the acquired JATS asset's
    SHA-256. If not, the caller has an upstream-state-changed
    condition and MUST stop before invoking this wrapper.
    """
    body = load_receipt_envelope(v1_receipt_bytes_on_disk_path)
    if sha256_json(body["payload"]) != body["payload_sha256"]:
        raise V2ReuseError(
            f"{v1_receipt_bytes_on_disk_path}: payload_sha256 does not recompute"
        )
    payload = body["payload"]
    inner = receipt_from_payload(payload)
    if inner.status != AcquisitionStatus.ACQUIRED.value:
        raise V2ReuseError(
            f"{v1_receipt_bytes_on_disk_path}: fresh V1 receipt status="
            f"{inner.status!r} not ACQUIRED"
        )
    if v2_inspection_jats_sha256 is not None and inner.corpus == "pmc_oa":
        xml_asset = next((a for a in inner.assets if a.role == "xml"), None)
        if xml_asset is None:
            raise V2ReuseError(
                f"{v1_receipt_bytes_on_disk_path}: fresh PMC receipt has no xml asset"
            )
        if xml_asset.sha256 != v2_inspection_jats_sha256:
            raise V2ReuseError(
                f"{v1_receipt_bytes_on_disk_path}: fresh acquired JATS "
                f"sha256={xml_asset.sha256!r} != V2 inspection sha256 "
                f"{v2_inspection_jats_sha256!r}. UPSTREAM_STATE_CHANGED."
            )

    provenance: dict[str, Any] = {
        "selected_v2": {
            "canonical_id": inner.canonical_id,
            "corpus": inner.corpus,
            "metadata_at_selection": v2_selected_metadata or {},
        },
        "fresh_acquisition_provenance": dict(inner.provenance),
    }
    checks = list(inner.validation_checks) + [
        ValidationCheck("fresh_acquisition_completed", True),
    ]
    if v2_inspection_jats_sha256 is not None:
        checks.append(ValidationCheck(
            "pmc_fresh_jats_sha_matches_v2_inspection", True,
        ))
    return V2AcquisitionReceipt(
        canonical_id=inner.canonical_id,
        corpus=inner.corpus,
        selection_manifest_version="2",
        selection_manifest_sha256=v2_manifest_sha256,
        acquisition_schema_version=V2_ACQUISITION_SCHEMA_VERSION,
        execution_disposition=ExecutionDisposition.FRESH_ACQUISITION.value,
        status=AcquisitionStatus.ACQUIRED.value,
        reason=None,
        acquired_utc=_now_utc(),
        assets=list(inner.assets),
        validation_checks=checks,
        provenance=provenance,
        v2_provenance_bridge=None,
        v2_inspection_jats_sha256=v2_inspection_jats_sha256,
    )


__all__ = [
    "V2_ACQUISITION_SCHEMA_VERSION",
    "ExecutionDisposition",
    "V2AcquisitionReceipt",
    "V2ProvenanceBridge",
    "V2ReuseError",
    "verify_and_bind_from_v1",
    "wrap_fresh_v1_receipt_as_v2",
    "write_v2_receipt",
]
