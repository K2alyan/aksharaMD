"""B1a-5b.1 orchestrator + resume validation + aggregate index.

Reads ``DEV_PILOT_MANIFEST_V1.json`` (byte-stable, produced by B1a-5a
finalization), records its SHA-256, and iterates over the 20 selected
canonical IDs. For each, dispatches to the corpus-appropriate adapter,
which returns an :class:`AcquisitionReceipt`. A per-document receipt
is written to disk *immediately* on completion so a crash at document
17 leaves receipts 1..16 durable and independently verifiable.

Resume validation is fail-closed: before treating an on-disk receipt
as re-usable, the orchestrator verifies

- envelope shape and fingerprint keys,
- fingerprint's ``selection_manifest_sha256`` equals the current
  selection manifest's SHA,
- fingerprint's ``canonical_id`` and ``corpus`` match the selected row,
- fingerprint's ``acquisition_schema_version`` equals the current version,
- recomputed ``payload_sha256`` matches the stored value,
- payload's ``status == ACQUIRED``,
- every declared asset's ``local_path`` still exists and its SHA-256
  recomputes to the recorded value.

Only then does the run continue at ``REUSED_VERIFIED_RECEIPT``. Any
mismatch is a hard stop; the orchestrator refuses to silently
redownload over questionable evidence.

Aggregate index: ``docs/evaluation/DEV_PILOT_ACQUISITION_V1.json``.
Every entry inside is a compact projection of one on-disk receipt.
Regenerating the aggregate is a pure function over the receipts
directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .doclaynet import DoclaynetAcquirer
from .federal_register import FederalRegisterAcquirer
from .pmc_oa import PmcOaAcquirer
from .receipt import (
    ACQUISITION_SCHEMA_VERSION,
    AcquisitionReceipt,
    AcquisitionStatus,
    load_receipt_envelope,
    receipt_from_payload,
    sha256_file,
    sha256_json,
    write_receipt,
)

DEFAULT_SELECTION_MANIFEST = Path("docs/evaluation/DEV_PILOT_MANIFEST_V1.json")
DEFAULT_RECEIPTS_ROOT = Path("docs/evaluation/acquisition_v1")
DEFAULT_AGGREGATE_PATH = Path("docs/evaluation/DEV_PILOT_ACQUISITION_V1.json")
DEFAULT_PAYLOADS_ROOT = Path("corpus/eval_v1")


class AcquisitionAdapter(Protocol):
    """Structural protocol for a corpus-specific acquisition adapter.

    Concrete implementers set ``corpus`` to one of ``"pmc_oa"``,
    ``"doclaynet"``, or ``"federal_register"``. The default value on
    the Protocol itself is unused — it exists only so CodeQL does not
    treat the bare annotation as a statement-without-effect.
    """

    corpus: str = ""

    def acquire(
        self,
        selected: dict[str, Any],
        corpus_root: Path,
        selection_manifest_sha256: str,
    ) -> AcquisitionReceipt: ...


class OrchestratorStop(RuntimeError):
    """Raised on any terminal failure — the run STOPS rather than substitute."""


@dataclass
class OrchestratorResult:
    """Outcome of a complete B1a-5b.1 run against the frozen 20."""

    selection_manifest_sha256: str
    receipts: list[AcquisitionReceipt]
    aggregate_path: Path


def _receipt_path(receipts_root: Path, corpus: str, canonical_id: str) -> Path:
    # canonical IDs are hex page hashes, PMC IDs, or FR document numbers;
    # none contain path separators, but sanitize defensively.
    safe = canonical_id.replace("/", "_").replace("\\", "_")
    return receipts_root / corpus / f"{safe}.json"


def _default_adapters(payloads_root: Path) -> dict[str, AcquisitionAdapter]:
    """Wire the three production adapters with default primitives.

    ``payloads_root`` is the directory each corpus's assets land under
    (``<payloads_root>/<corpus>/<canonical_id>/``). The adapter itself
    is corpus-agnostic on this point — it only sees ``corpus_root``.
    """
    return {
        "pmc_oa": PmcOaAcquirer(),
        "doclaynet": DoclaynetAcquirer(),
        "federal_register": FederalRegisterAcquirer(),
    }


def try_reuse(
    receipt_path: Path,
    selected_row: dict[str, Any],
    selection_manifest_sha256: str,
) -> AcquisitionReceipt:
    """Fail-closed verification of an existing receipt on resume.

    Returns a receipt with status ``REUSED_VERIFIED_RECEIPT`` when every
    check passes. On any failure raises :class:`OrchestratorStop`.
    """
    canonical_id = selected_row["canonical_id"]
    corpus = selected_row["corpus"]
    if not receipt_path.exists():
        raise OrchestratorStop(f"receipt missing on resume: {receipt_path}")

    body = load_receipt_envelope(receipt_path)
    if not isinstance(body, dict) or {"fingerprint", "payload", "payload_sha256"} - body.keys():
        raise OrchestratorStop(f"{receipt_path}: envelope shape invalid")

    fp = body["fingerprint"]
    for key, expected in (
        ("canonical_id", canonical_id),
        ("corpus", corpus),
        ("selection_manifest_sha256", selection_manifest_sha256),
        ("acquisition_schema_version", ACQUISITION_SCHEMA_VERSION),
    ):
        got = fp.get(key)
        if got != expected:
            raise OrchestratorStop(
                f"{receipt_path}: fingerprint {key}={got!r} expected {expected!r}"
            )

    if sha256_json(body["payload"]) != body.get("payload_sha256"):
        raise OrchestratorStop(f"{receipt_path}: payload_sha256 does not recompute")

    payload = body["payload"]
    if payload.get("status") != AcquisitionStatus.ACQUIRED.value:
        raise OrchestratorStop(
            f"{receipt_path}: cannot reuse — recorded status is "
            f"{payload.get('status')!r}, not {AcquisitionStatus.ACQUIRED.value!r}"
        )

    for asset in payload.get("assets") or []:
        local_path = Path(asset["local_path"])
        if not local_path.exists():
            raise OrchestratorStop(
                f"{receipt_path}: recorded asset missing on disk: {local_path}"
            )
        recomputed = sha256_file(local_path)
        if recomputed != asset["sha256"]:
            raise OrchestratorStop(
                f"{receipt_path}: asset {asset.get('role')!r} sha256 drift — "
                f"stored {asset['sha256']!r} recomputed {recomputed!r}"
            )

    receipt = receipt_from_payload(payload)
    # Re-emit with REUSED_VERIFIED_RECEIPT so downstream sees the
    # reuse status while retaining the original acquired_utc etc.
    return AcquisitionReceipt(
        canonical_id=receipt.canonical_id,
        corpus=receipt.corpus,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
        acquisition_schema_version=receipt.acquisition_schema_version,
        status=AcquisitionStatus.REUSED_VERIFIED_RECEIPT.value,
        reason=None,
        acquired_utc=receipt.acquired_utc,
        assets=receipt.assets,
        validation_checks=receipt.validation_checks,
        provenance=receipt.provenance,
    )


def run(
    *,
    selection_manifest_path: Path = DEFAULT_SELECTION_MANIFEST,
    receipts_root: Path = DEFAULT_RECEIPTS_ROOT,
    aggregate_path: Path = DEFAULT_AGGREGATE_PATH,
    payloads_root: Path = DEFAULT_PAYLOADS_ROOT,
    adapters: dict[str, AcquisitionAdapter] | None = None,
    stop_on_failure: bool = True,
) -> OrchestratorResult:
    """Drive B1a-5b.1 acquisition over the frozen 20.

    ``stop_on_failure=True`` is the locked production behavior: any
    terminal status other than ``ACQUIRED`` / ``REUSED_VERIFIED_RECEIPT``
    raises :class:`OrchestratorStop`. Tests can flip it to ``False`` to
    exercise multi-document failure aggregation without abort.
    """
    manifest = json.loads(selection_manifest_path.read_text(encoding="utf-8"))
    manifest_sha = sha256_file(selection_manifest_path)
    adapters = adapters or _default_adapters(payloads_root)

    receipts: list[AcquisitionReceipt] = []
    for row in manifest["selected"]:
        corpus = row["corpus"]
        adapter = adapters.get(corpus)
        if adapter is None:
            raise OrchestratorStop(f"no adapter registered for corpus={corpus!r}")

        rec_path = _receipt_path(receipts_root, corpus, row["canonical_id"])

        if rec_path.exists():
            reused = try_reuse(rec_path, row, manifest_sha)
            # Do NOT overwrite the durable ACQUIRED receipt on disk with a
            # REUSED_* record. The reuse-state receipt is a run-time value.
            receipts.append(reused)
            continue

        corpus_root = payloads_root / corpus
        receipt = adapter.acquire(row, corpus_root, manifest_sha)
        write_receipt(rec_path, receipt)
        receipts.append(receipt)
        if receipt.status != AcquisitionStatus.ACQUIRED.value:
            if stop_on_failure:
                raise OrchestratorStop(
                    f"acquisition stopped at {row['canonical_id']} "
                    f"({corpus}): status={receipt.status} reason={receipt.reason}"
                )

    write_aggregate(
        aggregate_path,
        selection_manifest_sha256=manifest_sha,
        receipts=receipts,
    )
    return OrchestratorResult(
        selection_manifest_sha256=manifest_sha,
        receipts=receipts,
        aggregate_path=aggregate_path,
    )


def write_aggregate(
    aggregate_path: Path,
    *,
    selection_manifest_sha256: str,
    receipts: list[AcquisitionReceipt],
) -> None:
    """Emit the derived aggregate index over per-document receipts."""
    aggregate_path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "selection_manifest_sha256": selection_manifest_sha256,
        "acquisition_schema_version": ACQUISITION_SCHEMA_VERSION,
        "created_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "records": [
            {
                "canonical_id": r.canonical_id,
                "corpus": r.corpus,
                "status": r.status,
                "reason": r.reason,
                "acquired_utc": r.acquired_utc,
                "assets": [
                    {
                        "role": a.role,
                        "local_path": a.local_path,
                        "byte_size": a.byte_size,
                        "sha256": a.sha256,
                    }
                    for a in r.assets
                ],
                "validation_checks_summary": {
                    "n_total": len(r.validation_checks),
                    "n_ok": sum(1 for c in r.validation_checks if c.ok),
                },
            }
            for r in receipts
        ],
        "counts": {
            "total": len(receipts),
            "acquired": sum(
                1 for r in receipts if r.status == AcquisitionStatus.ACQUIRED.value
            ),
            "reused_verified_receipt": sum(
                1 for r in receipts
                if r.status == AcquisitionStatus.REUSED_VERIFIED_RECEIPT.value
            ),
            "non_success": sum(
                1 for r in receipts
                if r.status not in (
                    AcquisitionStatus.ACQUIRED.value,
                    AcquisitionStatus.REUSED_VERIFIED_RECEIPT.value,
                )
            ),
        },
    }
    aggregate_path.write_text(json.dumps(body, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--selection-manifest", type=Path, default=DEFAULT_SELECTION_MANIFEST)
    p.add_argument("--receipts-root", type=Path, default=DEFAULT_RECEIPTS_ROOT)
    p.add_argument("--aggregate-path", type=Path, default=DEFAULT_AGGREGATE_PATH)
    p.add_argument("--payloads-root", type=Path, default=DEFAULT_PAYLOADS_ROOT)
    args = p.parse_args(argv)
    try:
        result = run(
            selection_manifest_path=args.selection_manifest,
            receipts_root=args.receipts_root,
            aggregate_path=args.aggregate_path,
            payloads_root=args.payloads_root,
        )
    except OrchestratorStop as e:
        print(f"ACQUISITION STOPPED: {e}", file=sys.stderr)
        return 2
    print(f"selection_manifest_sha256 = {result.selection_manifest_sha256}")
    print(f"aggregate_path            = {result.aggregate_path}")
    print(f"receipts                  = {len(result.receipts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "AcquisitionAdapter",
    "OrchestratorResult",
    "OrchestratorStop",
    "run",
    "try_reuse",
    "write_aggregate",
]
