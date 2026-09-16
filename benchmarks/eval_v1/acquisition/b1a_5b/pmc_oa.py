"""PMC-OA acquisition adapter for the B1a-5b.1 orchestrator.

Given one selected row from ``DEV_PILOT_MANIFEST_V1.json`` (a frozen
``PMCID.<version>`` canonical id), acquire the metadata JSON, JATS
XML, and PDF for exactly that identity from the pinned AWS Open Data
distribution. Every failure mode maps to one defined status; no
canonical id is ever swapped, no eligibility rule is relaxed.

The B1a-2 primitives in :mod:`benchmarks.eval_v1.acquisition.pmc_oa_aws`
do the real fetch + write + idempotent-verify work. This adapter's
job is to wire them into the acquisition-receipt contract:

- upstream metadata drift (retracted, license change, missing version)
  becomes ``UPSTREAM_STATE_CHANGED``,
- JATS-internal PMCID or metadata-reported version disagreeing with
  the selected id becomes ``IDENTITY_MISMATCH``,
- MD5 / SHA-256 mismatch becomes ``INTEGRITY_FAILURE``,
- exhausted network retries become ``SELECTED_ACQUISITION_FAILURE``.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.acquisition import pmc_oa_aws
from benchmarks.eval_v1.acquisition.pmc_oa_aws import (
    AcquiredArticle,
    AcquisitionError,
    EligibilityDecision,
    PmcVersionMetadata,
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


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _split_canonical(canonical_id: str) -> tuple[str, int]:
    """Split ``"PMC12345.6"`` into ``("PMC12345", 6)``."""
    if "." not in canonical_id:
        raise ValueError(f"malformed PMC canonical_id: {canonical_id!r}")
    pmcid, version = canonical_id.rsplit(".", 1)
    return pmcid, int(version)


@dataclass
class PmcOaAcquirer:
    """Per-document PMC-OA acquisition, driven by frozen selection metadata.

    The primitive callables are dependency-injected with the module
    defaults; tests can substitute fakes that raise controlled errors.
    """

    # Callable defaults are wrapped in ``default_factory`` so the
    # class body assigns a ``field()`` object (not a bare function)
    # to the attribute — otherwise CodeQL's static analysis interprets
    # ``self.X_fn(args)`` as an unbound-method call and mis-counts the
    # arity of the underlying primitive. Runtime behavior is unchanged.
    corpus: str = "pmc_oa"
    fetch_metadata_fn: Callable[..., tuple[PmcVersionMetadata, bytes, str]] = field(
        default_factory=lambda: pmc_oa_aws.fetch_metadata,
    )
    apply_filters_fn: Callable[[PmcVersionMetadata], EligibilityDecision] = field(
        default_factory=lambda: pmc_oa_aws.apply_metadata_filters,
    )
    acquire_article_fn: Callable[..., AcquiredArticle] = field(
        default_factory=lambda: pmc_oa_aws.acquire_article,
    )
    jats_pmcid_matches_fn: Callable[..., tuple[bool, str | None]] = field(
        default_factory=lambda: pmc_oa_aws.jats_pmcid_matches,
    )
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)

    def acquire(
        self,
        selected: dict[str, Any],
        corpus_root: Path,
        selection_manifest_sha256: str,
    ) -> AcquisitionReceipt:
        canonical_id = selected["canonical_id"]
        selected_pmcid, selected_version = _split_canonical(canonical_id)
        checks: list[ValidationCheck] = []
        provenance: dict[str, Any] = {
            "selected": {
                "canonical_id": canonical_id,
                "pmcid": selected_pmcid,
                "version": selected_version,
                "metadata_at_selection": selected.get("metadata") or {},
            },
        }

        # --- Step 1: fetch upstream metadata under retry
        try:
            md, md_raw, md_sha = with_retry(
                lambda: self.fetch_metadata_fn(canonical_id),
                policy=self.retry_policy, sleep=self.sleep,
            )
        except RetryExhaustedError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.SELECTED_ACQUISITION_FAILURE,
                f"metadata_fetch_retries_exhausted: {e}",
                checks, provenance,
            )
        except AcquisitionError as e:
            # e.g. metadata self-identifies as a different pmcid.version
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"metadata_identity_error: {e}",
                checks, provenance,
            )

        # --- Step 2: identity vs selected
        pmcid_matches = md.pmcid == selected_pmcid
        version_matches = md.version == selected_version
        checks.append(ValidationCheck("metadata.pmcid_matches_selected", pmcid_matches))
        checks.append(ValidationCheck("metadata.version_matches_selected", version_matches))
        if not (pmcid_matches and version_matches):
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"metadata pmcid.version={md.pmcid}.{md.version} disagrees "
                f"with selected {selected_pmcid}.{selected_version}",
                checks, provenance,
            )

        # --- Step 3: current eligibility (UPSTREAM_STATE_CHANGED if drifted)
        decision = self.apply_filters_fn(md)
        checks.append(
            ValidationCheck(
                "eligibility.current_upstream_matches_selection_contract",
                decision.ok,
                reason=None if decision.ok else decision.reason,
            )
        )
        if not decision.ok:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.UPSTREAM_STATE_CHANGED,
                f"upstream_no_longer_eligible: {decision.reason}",
                checks, provenance,
            )

        # --- Step 4: acquire the article under retry.
        #     pmc_oa_aws.acquire_article verifies MD5/ETag/SHA-256 and
        #     raises AcquisitionError on any integrity mismatch.
        try:
            acquired: AcquiredArticle = with_retry(
                lambda: self.acquire_article_fn(
                    md, md_raw, md_sha, corpus_root,
                    selection_role="b1a-5b.1 pilot acquisition",
                ),
                policy=self.retry_policy, sleep=self.sleep,
            )
        except RetryExhaustedError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.SELECTED_ACQUISITION_FAILURE,
                f"acquire_article_retries_exhausted: {e}",
                checks, provenance,
            )
        except AcquisitionError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.INTEGRITY_FAILURE,
                f"acquire_article_integrity_error: {e}",
                checks, provenance,
            )

        # --- Step 5: JATS-internal PMCID cross-check
        xml_bytes = acquired.xml_path.read_bytes()
        matches, got = self.jats_pmcid_matches_fn(xml_bytes, selected_pmcid)
        checks.append(
            ValidationCheck(
                "jats.internal_pmcid_matches_selected",
                matches,
                reason=None if matches else f"jats_internal_pmcid={got!r}",
            )
        )
        if not matches:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"jats_internal_pmcid={got!r} does not match {selected_pmcid}",
                checks, provenance,
            )

        # --- Step 6: build ACQUIRED receipt
        pdf_size = acquired.pdf_path.stat().st_size
        xml_size = acquired.xml_path.stat().st_size
        md_json_size = acquired.metadata_json_path.stat().st_size

        pdf_md5_meta = md.md5_from_url(md.pdf_url)
        xml_md5_meta = md.md5_from_url(md.xml_url)

        assets = [
            AcquiredAsset(
                role="metadata",
                source_url=pmc_oa_aws.BUCKET_HTTPS + md.metadata_key,
                source_key=md.metadata_key,
                source_revision=f"v{md.version}",
                local_path=str(acquired.metadata_json_path),
                byte_size=md_json_size,
                sha256=md_sha,
                source_side_integrity={"pmc_metadata_sha256": md_sha},
            ),
            AcquiredAsset(
                role="pdf",
                source_url=md.pdf_url,
                source_key=md.object_key("pdf"),
                source_revision=f"v{md.version}",
                local_path=str(acquired.pdf_path),
                byte_size=pdf_size,
                sha256=sha256_file(acquired.pdf_path),
                source_side_integrity={"md5_from_metadata": pdf_md5_meta},
            ),
            AcquiredAsset(
                role="xml",
                source_url=md.xml_url,
                source_key=md.object_key("xml"),
                source_revision=f"v{md.version}",
                local_path=str(acquired.xml_path),
                byte_size=xml_size,
                sha256=sha256_file(acquired.xml_path),
                source_side_integrity={"md5_from_metadata": xml_md5_meta},
            ),
        ]
        provenance["upstream_at_acquisition"] = {
            "pmcid": md.pmcid,
            "version": md.version,
            "license_code": md.license_code,
            "is_retracted": md.is_retracted,
            "is_manuscript": md.is_manuscript,
            "is_historical_ocr": md.is_historical_ocr,
            "metadata_key": md.metadata_key,
            "pdf_canonical_url": md.pdf_url,
            "xml_canonical_url": md.xml_url,
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


__all__ = ["PmcOaAcquirer"]
