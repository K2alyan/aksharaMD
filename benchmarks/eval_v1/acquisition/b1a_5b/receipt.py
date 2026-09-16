"""Acquisition receipt schema for the frozen B1a-5a selected 20.

One immutable receipt file per acquired document lives at
``docs/evaluation/acquisition_v1/<corpus>/<canonical_id>.json``. That
per-document file is the primary durable record. The aggregate
``DEV_PILOT_ACQUISITION_V1.json`` is a derived index over those
receipts, not a source of truth.

The receipt is emitted with a canonical, self-verifying envelope:

.. code-block:: json

   {
     "fingerprint": {
       "canonical_id": "PMC3569185.1",
       "corpus": "pmc_oa",
       "selection_manifest_sha256": "...",
       "acquisition_schema_version": "1"
     },
     "payload": { ... AcquisitionReceipt body ... },
     "payload_sha256": "..."
   }

The fingerprint acts as an identity + provenance anchor. If a future
run wants to reuse a receipt, it MUST first check the fingerprint's
``selection_manifest_sha256`` against the current selection manifest
and then recompute ``payload_sha256`` before touching any local
payload. Fail-closed. See :mod:`.orchestrator` for the reuse path.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

ACQUISITION_SCHEMA_VERSION = "1"


class AcquisitionStatus(StrEnum):
    """Terminal statuses for a single-document acquisition attempt.

    ``ACQUIRED`` and ``REUSED_VERIFIED_RECEIPT`` are the only success
    states. Every other value is a defined failure mode that STOPS the
    run — see the B1a-5b contract locked with the human on 2026-09-15.
    """

    ACQUIRED = "acquired"
    """All required assets were fetched with intact identity, integrity, and eligibility."""

    TRANSIENT_RETRIEVAL_FAILURE = "transient_retrieval_failure"
    """A single fetch failed but subsequent retries would still be within the policy budget.

    This status is reserved for reporting inside per-attempt records
    only; it never appears as the final status of a run because the
    orchestrator retries and either advances to ``ACQUIRED`` or exits
    with ``SELECTED_ACQUISITION_FAILURE``.
    """

    SELECTED_ACQUISITION_FAILURE = "selected_acquisition_failure"
    """Retries exhausted. Do NOT substitute a cutoff neighbor or move to CAL."""

    IDENTITY_MISMATCH = "identity_mismatch"
    """Server returned a different canonical id (PMCID / page_hash / document_number)."""

    INTEGRITY_FAILURE = "integrity_failure"
    """Downloaded content's SHA-256 disagrees with source-side MD5/ETag/metadata."""

    UPSTREAM_STATE_CHANGED = "upstream_state_changed"
    """Selection was valid against its frozen snapshot; current upstream is no longer eligible.

    Examples: PMC now flags the article ``is_retracted``; DocLayNet
    can no longer serve the pinned HF revision; FR API no longer
    returns the selected document number.
    """

    REUSED_VERIFIED_RECEIPT = "reused_verified_receipt"
    """An already-durable receipt was rediscovered on resume and every check re-verified."""


@dataclass(frozen=True)
class ValidationCheck:
    """One boolean assertion the acquisition layer made against the acquired bytes."""

    name: str
    ok: bool
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "reason": self.reason}


@dataclass(frozen=True)
class AcquiredAsset:
    """One retrieved file, with source identity and integrity fingerprints.

    ``source_side_integrity`` carries the *authoritative* values as
    reported by the source (S3 ETag, ``?md5=`` on PMC URLs, HF commit
    SHA on the containing revision, FR-API JSON hash, …), separate
    from the locally computed ``sha256``. Both are recorded so a
    later reviewer can independently cross-verify.
    """

    role: str
    source_url: str | None
    source_key: str | None
    source_revision: str | None
    local_path: str
    byte_size: int
    sha256: str
    source_side_integrity: dict[str, str | None] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "source_url": self.source_url,
            "source_key": self.source_key,
            "source_revision": self.source_revision,
            "local_path": self.local_path,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "source_side_integrity": dict(self.source_side_integrity),
        }


@dataclass(frozen=True)
class AcquisitionReceipt:
    """Immutable per-document acquisition receipt.

    Emitted exactly once per canonical_id whenever the adapter reaches
    a terminal status (success or defined failure). On resume, the
    orchestrator re-verifies this receipt before treating it as
    ``REUSED_VERIFIED_RECEIPT``.
    """

    canonical_id: str
    corpus: str
    selection_manifest_sha256: str
    acquisition_schema_version: str
    status: str
    reason: str | None
    acquired_utc: str
    assets: list[AcquiredAsset]
    validation_checks: list[ValidationCheck]
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "corpus": self.corpus,
            "selection_manifest_sha256": self.selection_manifest_sha256,
            "acquisition_schema_version": self.acquisition_schema_version,
            "status": self.status,
            "reason": self.reason,
            "acquired_utc": self.acquired_utc,
            "assets": [a.as_dict() for a in self.assets],
            "validation_checks": [c.as_dict() for c in self.validation_checks],
            "provenance": dict(self.provenance),
        }


def sha256_json(obj: Any) -> str:
    """Canonical JSON SHA-256. Sort keys + compact separators."""
    b = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 of file bytes; uses streamed hashing so large PDFs don't spike RAM."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_receipt(path: Path, receipt: AcquisitionReceipt) -> None:
    """Atomically write a receipt with a self-verifying envelope.

    Layout:
      { "fingerprint": {...}, "payload": {...}, "payload_sha256": "..." }
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = receipt.as_dict()
    body = {
        "fingerprint": {
            "canonical_id": receipt.canonical_id,
            "corpus": receipt.corpus,
            "selection_manifest_sha256": receipt.selection_manifest_sha256,
            "acquisition_schema_version": receipt.acquisition_schema_version,
        },
        "payload": payload,
        "payload_sha256": sha256_json(payload),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True))
    tmp.replace(path)


def load_receipt_envelope(path: Path) -> dict[str, Any]:
    """Load an on-disk receipt envelope; caller decides whether to validate."""
    return json.loads(path.read_text(encoding="utf-8"))


def receipt_from_payload(payload: dict[str, Any]) -> AcquisitionReceipt:
    """Reconstruct the dataclass from a JSON payload dict."""
    return AcquisitionReceipt(
        canonical_id=payload["canonical_id"],
        corpus=payload["corpus"],
        selection_manifest_sha256=payload["selection_manifest_sha256"],
        acquisition_schema_version=payload["acquisition_schema_version"],
        status=payload["status"],
        reason=payload.get("reason"),
        acquired_utc=payload["acquired_utc"],
        assets=[
            AcquiredAsset(
                role=a["role"],
                source_url=a.get("source_url"),
                source_key=a.get("source_key"),
                source_revision=a.get("source_revision"),
                local_path=a["local_path"],
                byte_size=int(a["byte_size"]),
                sha256=a["sha256"],
                source_side_integrity=dict(a.get("source_side_integrity") or {}),
            )
            for a in payload.get("assets", [])
        ],
        validation_checks=[
            ValidationCheck(
                name=c["name"], ok=bool(c["ok"]), reason=c.get("reason"),
            )
            for c in payload.get("validation_checks", [])
        ],
        provenance=dict(payload.get("provenance") or {}),
    )


def _asdict_dataclass(x: Any) -> Any:
    """Dev helper used only in tests and error messages — never in the write path."""
    try:
        return asdict(x)
    except TypeError:
        return x


__all__ = [
    "ACQUISITION_SCHEMA_VERSION",
    "AcquisitionStatus",
    "AcquiredAsset",
    "AcquisitionReceipt",
    "ValidationCheck",
    "load_receipt_envelope",
    "receipt_from_payload",
    "sha256_file",
    "sha256_json",
    "write_receipt",
]
