"""DocLayNet acquisition adapter for the B1a-5b.1 orchestrator.

Given one selected row from ``DEV_PILOT_MANIFEST_V1.json`` (a frozen
``page_hash`` canonical id plus the shard it lives in), acquire the
PNG + PDF + annotations JSON for exactly that page from
``docling-project/DocLayNet-v1.2`` pinned at the exact revision
recorded in the selection manifest.

The primitives in :mod:`benchmarks.eval_v1.acquisition.doclaynet_hf`
do the actual HF fetch + verify-cached logic. This adapter's job is
to check that the pinned revision still resolves and that the returned
page truly bears the frozen ``page_hash``. A different revision or a
different page hash is IDENTITY_MISMATCH — never a silent substitution.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.acquisition import doclaynet_hf

from .receipt import (
    ACQUISITION_SCHEMA_VERSION,
    AcquiredAsset,
    AcquisitionReceipt,
    AcquisitionStatus,
    ValidationCheck,
    sha256_file,
)
from .retry_policy import RetryExhaustedError, RetryPolicy, with_retry

DOCLAYNET_LOCKED_DATASET_ID = "docling-project/DocLayNet-v1.2"
DOCLAYNET_LOCKED_REVISION = "0daf93102e2efce76c3e11a274a5e0d0969391d3"
DOCLAYNET_LOCKED_SPLIT = "train"


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


@dataclass
class DoclaynetAcquirer:
    """Per-page DocLayNet acquisition driven by a frozen page_hash + shard_key.

    ``resolve_dataset_ref_fn`` returns the current HF ``ref`` for the
    dataset. If its ``sha`` no longer matches the pinned revision, we
    emit ``UPSTREAM_STATE_CHANGED`` and STOP — we do not fall back to
    any other revision.

    ``fetch_page_fn`` is the primitive that resolves the frozen
    ``page_hash`` inside the pinned shard and returns the page object
    plus the shard's SHA-256. In production this iterates the shard
    Parquet with :func:`doclaynet_hf.open_parquet_shard` +
    :func:`doclaynet_hf.iter_pages_in_row_group`. Tests supply a fake
    that returns a synthetic page pointing at temp files.
    """

    # Callable defaults use ``default_factory`` — see PmcOaAcquirer for
    # why. Runtime behavior unchanged.
    corpus: str = "doclaynet"
    resolve_dataset_ref_fn: Callable[..., doclaynet_hf.HfDatasetRef] = field(
        default_factory=lambda: doclaynet_hf.resolve_dataset_ref,
    )
    # Production default: revision-pinned shard walk that resolves one
    # frozen page_hash inside one frozen shard and returns the page
    # plus the shard's SHA-256. Tests inject a lighter fake so no HF
    # fetch happens during CI.
    fetch_page_fn: Callable[..., tuple[doclaynet_hf.DocLayNetPage, str]] = field(
        default_factory=lambda: doclaynet_hf.resolve_page_in_shard,
    )
    acquire_page_fn: Callable[..., doclaynet_hf.AcquiredPage] = field(
        default_factory=lambda: doclaynet_hf.acquire_page,
    )
    apply_page_filters_fn: Callable[..., doclaynet_hf.EligibilityDecision] = field(
        default_factory=lambda: doclaynet_hf.apply_page_filters,
    )
    locked_dataset_id: str = DOCLAYNET_LOCKED_DATASET_ID
    locked_revision: str = DOCLAYNET_LOCKED_REVISION
    locked_split: str = DOCLAYNET_LOCKED_SPLIT
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)

    def acquire(
        self,
        selected: dict[str, Any],
        corpus_root: Path,
        selection_manifest_sha256: str,
    ) -> AcquisitionReceipt:
        canonical_id = selected["canonical_id"]  # page_hash
        selected_metadata = selected.get("metadata") or {}
        selected_shard_key = selected_metadata.get("shard_key")
        checks: list[ValidationCheck] = []
        provenance: dict[str, Any] = {
            "selected": {
                "canonical_id": canonical_id,
                "shard_key": selected_shard_key,
                "metadata_at_selection": selected_metadata,
            },
            "locked": {
                "dataset_id": self.locked_dataset_id,
                "revision": self.locked_revision,
                "split": self.locked_split,
            },
        }
        if not selected_shard_key:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                "selected row is missing metadata.shard_key; cannot deterministically "
                "resolve the page under the pinned revision",
                checks, provenance,
            )

        # --- Step 1: pinned revision still resolvable
        try:
            ref = with_retry(
                self.resolve_dataset_ref_fn,
                policy=self.retry_policy, sleep=self.sleep,
            )
        except RetryExhaustedError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.SELECTED_ACQUISITION_FAILURE,
                f"dataset_ref_resolve_retries_exhausted: {e}",
                checks, provenance,
            )
        revision_matches = ref.sha == self.locked_revision
        checks.append(
            ValidationCheck(
                "dataset.pinned_revision_resolves",
                revision_matches,
                reason=None if revision_matches else f"current_sha={ref.sha}",
            )
        )
        if not revision_matches:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.UPSTREAM_STATE_CHANGED,
                f"dataset revision drift: current={ref.sha} vs locked={self.locked_revision}",
                checks, provenance,
            )

        # --- Step 2: resolve the page inside the pinned shard
        try:
            page, shard_sha = with_retry(
                lambda: self.fetch_page_fn(
                    canonical_id, selected_shard_key, ref,
                ),
                policy=self.retry_policy, sleep=self.sleep,
            )
        except RetryExhaustedError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.SELECTED_ACQUISITION_FAILURE,
                f"shard_walk_retries_exhausted: {e}",
                checks, provenance,
            )
        except KeyError:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"page_hash={canonical_id!r} not present in pinned shard "
                f"{selected_shard_key!r} at revision {self.locked_revision}",
                checks, provenance,
            )
        page_hash_matches = page.page_hash == canonical_id
        checks.append(
            ValidationCheck(
                "shard.returned_page_hash_matches_selected",
                page_hash_matches,
                reason=None if page_hash_matches else f"got_page_hash={page.page_hash}",
            )
        )
        if not page_hash_matches:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.IDENTITY_MISMATCH,
                f"shard returned page_hash={page.page_hash!r}, selected={canonical_id!r}",
                checks, provenance,
            )

        # --- Step 3: current eligibility (structural filters)
        decision = self.apply_page_filters_fn({
            "category_id": [a.category_id for a in page.annotations],
        })
        checks.append(
            ValidationCheck(
                "eligibility.page_still_matches_selection_contract",
                decision.ok,
                reason=None if decision.ok else decision.reason,
            )
        )
        if not decision.ok:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.UPSTREAM_STATE_CHANGED,
                f"page_no_longer_eligible: {decision.reason}",
                checks, provenance,
            )

        # --- Step 4: acquire under retry (idempotent verify-cached inside)
        try:
            acquired: doclaynet_hf.AcquiredPage = with_retry(
                lambda: self.acquire_page_fn(
                    page, ref, selected_shard_key, shard_sha, corpus_root,
                    selection_role="b1a-5b.1 pilot acquisition",
                ),
                policy=self.retry_policy, sleep=self.sleep,
            )
        except RetryExhaustedError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.SELECTED_ACQUISITION_FAILURE,
                f"acquire_page_retries_exhausted: {e}",
                checks, provenance,
            )
        except doclaynet_hf.AcquisitionError as e:
            return self._fail(
                canonical_id, selection_manifest_sha256,
                AcquisitionStatus.INTEGRITY_FAILURE,
                f"acquire_page_integrity_error: {e}",
                checks, provenance,
            )

        # --- Step 5: build ACQUIRED receipt
        assets: list[AcquiredAsset] = []
        assets.append(
            AcquiredAsset(
                role="png",
                source_url=None,
                source_key=selected_shard_key,
                source_revision=self.locked_revision,
                local_path=str(acquired.png_path),
                byte_size=acquired.png_path.stat().st_size,
                sha256=sha256_file(acquired.png_path),
                source_side_integrity={"shard_sha256": shard_sha},
            )
        )
        if acquired.pdf_path is not None and acquired.pdf_path.exists():
            assets.append(
                AcquiredAsset(
                    role="pdf",
                    source_url=None,
                    source_key=selected_shard_key,
                    source_revision=self.locked_revision,
                    local_path=str(acquired.pdf_path),
                    byte_size=acquired.pdf_path.stat().st_size,
                    sha256=sha256_file(acquired.pdf_path),
                    source_side_integrity={"shard_sha256": shard_sha},
                )
            )
        assets.append(
            AcquiredAsset(
                role="annotations",
                source_url=None,
                source_key=selected_shard_key,
                source_revision=self.locked_revision,
                local_path=str(acquired.annotations_path),
                byte_size=acquired.annotations_path.stat().st_size,
                sha256=sha256_file(acquired.annotations_path),
                source_side_integrity={"shard_sha256": shard_sha},
            )
        )
        provenance["upstream_at_acquisition"] = {
            "dataset_id": self.locked_dataset_id,
            "revision": ref.sha,
            "last_modified_utc": ref.last_modified,
            "shard_key": selected_shard_key,
            "shard_sha256": shard_sha,
            "n_annotations": len(page.annotations),
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
    "DOCLAYNET_LOCKED_DATASET_ID",
    "DOCLAYNET_LOCKED_REVISION",
    "DOCLAYNET_LOCKED_SPLIT",
    "DoclaynetAcquirer",
]
