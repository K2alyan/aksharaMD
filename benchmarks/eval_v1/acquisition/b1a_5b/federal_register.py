"""Federal Register acquisition adapter for the B1a-5b.1 orchestrator.

Given one selected row from ``DEV_PILOT_MANIFEST_V1.json`` (a frozen
FR ``document_number``), acquire the FR-API detail JSON and the
authoritative GovInfo PDF for exactly that document. XML is an
optional G2-adjudication support asset and is reported separately —
its absence never fails acquisition.

Failure classifications:

- FR API returning a different ``document_number`` or a mismatched
  ``package_id`` / ``granule_id`` → IDENTITY_MISMATCH
- PDF SHA-256 doesn't match, or GovInfo URL pattern doesn't match →
  INTEGRITY_FAILURE
- FR API metadata no longer satisfies the selection contract
  (e.g. required stratum changed) → UPSTREAM_STATE_CHANGED
- retries exhausted → SELECTED_ACQUISITION_FAILURE
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.acquisition import federal_register_api
from benchmarks.eval_v1.acquisition.federal_register_api import (
    AcquiredDocument,
    AcquisitionError,
    FrDocumentDetail,
)

from .receipt import (
    ACQUISITION_SCHEMA_VERSION,
    AcquiredAsset,
    AcquisitionReceipt,
    AcquisitionStatus,
    ValidationCheck,
    sha256_file,
)
from .retry_policy import RetryExhaustedError, RetryPolicy, with_retry

FR_LOCKED_PUB_DATE_GTE = date(2025, 1, 1)
FR_LOCKED_PUB_DATE_LTE = date(2025, 12, 31)


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


@dataclass
class FederalRegisterAcquirer:
    """Per-document Federal Register acquisition."""

    corpus: str = "federal_register"
    fetch_detail_fn: Callable[..., tuple[FrDocumentDetail, bytes, str]] = (
        federal_register_api.fetch_document_detail
    )
    acquire_document_fn: Callable[..., AcquiredDocument] = (
        federal_register_api.acquire_document
    )
    govinfo_ids_fn: Callable[[str], tuple[str, str]] = (
        federal_register_api.govinfo_ids_from_pdf_url
    )
    locked_pub_date_gte: date = FR_LOCKED_PUB_DATE_GTE
    locked_pub_date_lte: date = FR_LOCKED_PUB_DATE_LTE
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep

    def acquire(
        self,
        selected: dict[str, Any],
        corpus_root: Path,
        selection_manifest_sha256: str,
    ) -> AcquisitionReceipt:
        canonical_id = selected["canonical_id"]  # document_number
        selected_metadata = selected.get("metadata") or {}
        selected_stratum = selected.get("stratum")
        selected_package_id = selected_metadata.get("package_id")
        selected_granule_id = selected_metadata.get("granule_id")
        selected_volume = selected_metadata.get("volume")
        selected_pub_date = selected_metadata.get("publication_date")
        checks: list[ValidationCheck] = []
        provenance: dict[str, Any] = {
            "selected": {
                "canonical_id": canonical_id,
                "stratum": selected_stratum,
                "metadata_at_selection": selected_metadata,
            },
        }

        # --- Step 1: fetch detail under retry
        try:
            detail, detail_raw, detail_sha = with_retry(
                lambda: self.fetch_detail_fn(canonical_id),
                policy=self.retry_policy, sleep=self.sleep,
            )
        except RetryExhaustedError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.SELECTED_ACQUISITION_FAILURE,
                f"detail_fetch_retries_exhausted: {e}",
                checks, provenance,
            )
        except AcquisitionError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"fr_detail_identity_error: {e}",
                checks, provenance,
            )

        # --- Step 2: identity checks vs selected
        doc_num_matches = detail.document_number == canonical_id
        checks.append(
            ValidationCheck("detail.document_number_matches_selected", doc_num_matches)
        )
        if not doc_num_matches:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"api document_number={detail.document_number!r} != selected={canonical_id!r}",
                checks, provenance,
            )
        stratum_matches = selected_stratum is None or detail.type == selected_stratum
        checks.append(
            ValidationCheck(
                "detail.type_matches_selected_stratum",
                stratum_matches,
                reason=None if stratum_matches else f"api_type={detail.type!r}",
            )
        )
        if not stratum_matches:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.UPSTREAM_STATE_CHANGED,
                f"stratum drift: api={detail.type!r} vs selected={selected_stratum!r}",
                checks, provenance,
            )
        if selected_volume is not None:
            vol_matches = detail.volume == selected_volume
            checks.append(
                ValidationCheck(
                    "detail.volume_matches_selected",
                    vol_matches,
                    reason=None if vol_matches else f"api_volume={detail.volume}",
                )
            )
            if not vol_matches:
                return self._fail(
                    canonical_id, selection_manifest_sha256,
                    AcquisitionStatus.UPSTREAM_STATE_CHANGED,
                    f"volume drift: api={detail.volume} vs selected={selected_volume}",
                    checks, provenance,
                )
        if selected_pub_date:
            pd_matches = detail.publication_date == selected_pub_date
            checks.append(
                ValidationCheck(
                    "detail.publication_date_matches_selected",
                    pd_matches,
                    reason=None if pd_matches else f"api_pub_date={detail.publication_date!r}",
                )
            )
            if not pd_matches:
                return self._fail(
                    canonical_id, selection_manifest_sha256,
                    AcquisitionStatus.UPSTREAM_STATE_CHANGED,
                    f"publication_date drift: api={detail.publication_date!r} vs "
                    f"selected={selected_pub_date!r}",
                    checks, provenance,
                )

        # --- Step 3: GovInfo identity (package_id + granule_id from PDF URL)
        if not detail.pdf_url:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.UPSTREAM_STATE_CHANGED,
                "api_response_has_no_pdf_url",
                checks, provenance,
            )
        try:
            package_id, granule_id = self.govinfo_ids_fn(detail.pdf_url)
        except AcquisitionError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.INTEGRITY_FAILURE,
                f"govinfo_url_pattern_mismatch: {e}",
                checks, provenance,
            )
        pkg_matches = selected_package_id is None or package_id == selected_package_id
        gran_matches = selected_granule_id is None or granule_id == selected_granule_id
        checks.append(
            ValidationCheck(
                "govinfo.package_id_matches_selected",
                pkg_matches,
                reason=None if pkg_matches else f"api_package_id={package_id!r}",
            )
        )
        checks.append(
            ValidationCheck(
                "govinfo.granule_id_matches_selected",
                gran_matches,
                reason=None if gran_matches else f"api_granule_id={granule_id!r}",
            )
        )
        if not (pkg_matches and gran_matches):
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"govinfo ids: package={package_id}/{selected_package_id} "
                f"granule={granule_id}/{selected_granule_id}",
                checks, provenance,
            )

        # --- Step 4: acquire under retry
        try:
            acquired: AcquiredDocument = with_retry(
                lambda: self.acquire_document_fn(
                    detail, detail_raw, detail_sha, corpus_root,
                    population_publication_date_gte=self.locked_pub_date_gte,
                    population_publication_date_lte=self.locked_pub_date_lte,
                    selection_role="b1a-5b.1 pilot acquisition",
                ),
                policy=self.retry_policy, sleep=self.sleep,
            )
        except RetryExhaustedError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.SELECTED_ACQUISITION_FAILURE,
                f"acquire_document_retries_exhausted: {e}",
                checks, provenance,
            )
        except AcquisitionError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.INTEGRITY_FAILURE,
                f"acquire_document_integrity_error: {e}",
                checks, provenance,
            )

        # --- Step 5: build ACQUIRED receipt
        assets: list[AcquiredAsset] = []
        assets.append(
            AcquiredAsset(
                role="api_json",
                source_url=(
                    f"{federal_register_api.FR_API_HOST}/api/v1/documents/"
                    f"{canonical_id}.json"
                ),
                source_key=None,
                source_revision=None,
                local_path=str(acquired.api_json_path),
                byte_size=acquired.api_json_path.stat().st_size,
                sha256=detail_sha,
                source_side_integrity={"api_response_sha256": detail_sha},
            )
        )
        assets.append(
            AcquiredAsset(
                role="pdf",
                source_url=detail.pdf_url,
                source_key=f"{package_id}/pdf/{granule_id}.pdf",
                source_revision=package_id,
                local_path=str(acquired.pdf_path),
                byte_size=acquired.pdf_path.stat().st_size,
                sha256=sha256_file(acquired.pdf_path),
                source_side_integrity={},
            )
        )
        xml_available = False
        if acquired.xml_path is not None and acquired.xml_path.exists():
            xml_available = True
            assets.append(
                AcquiredAsset(
                    role="xml",
                    source_url=detail.full_text_xml_url,
                    source_key=None,
                    source_revision=package_id,
                    local_path=str(acquired.xml_path),
                    byte_size=acquired.xml_path.stat().st_size,
                    sha256=sha256_file(acquired.xml_path),
                    source_side_integrity={},
                )
            )
        checks.append(
            ValidationCheck(
                "g2_adjudication_support",
                True,
                reason=("available" if xml_available else "unavailable"),
            )
        )
        provenance["upstream_at_acquisition"] = {
            "document_number": detail.document_number,
            "publication_date": detail.publication_date,
            "type": detail.type,
            "volume": detail.volume,
            "package_id": package_id,
            "granule_id": granule_id,
            "pdf_canonical_url": detail.pdf_url,
            "xml_canonical_url": detail.full_text_xml_url,
            "xml_available": xml_available,
        }
        return AcquisitionReceipt(
            canonical_id=canonical_id,
            corpus=self.corpus,
            selection_manifest_sha256=selection_manifest_sha256,
            acquisition_schema_version=ACQUISITION_SCHEMA_VERSION,
            status=AcquisitionStatus.ACQUIRED.value,
            reason=None,
            acquired_utc=_now_utc(),
            assets=assets,
            validation_checks=checks,
            provenance=provenance,
        )

    def _fail(
        self,
        canonical_id: str,
        selection_manifest_sha256: str,
        status: AcquisitionStatus,
        reason: str,
        checks: list[ValidationCheck],
        provenance: dict[str, Any],
    ) -> AcquisitionReceipt:
        return AcquisitionReceipt(
            canonical_id=canonical_id,
            corpus=self.corpus,
            selection_manifest_sha256=selection_manifest_sha256,
            acquisition_schema_version=ACQUISITION_SCHEMA_VERSION,
            status=status.value,
            reason=reason,
            acquired_utc=_now_utc(),
            assets=[],
            validation_checks=checks,
            provenance=provenance,
        )


__all__ = [
    "FR_LOCKED_PUB_DATE_GTE",
    "FR_LOCKED_PUB_DATE_LTE",
    "FederalRegisterAcquirer",
]
