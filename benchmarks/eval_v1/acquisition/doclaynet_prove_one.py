"""Prove-one candidate selection for DocLayNet (B1a-3).

Strategy:

1. Resolve the current ``docling-project/DocLayNet-v1.2`` commit SHA
   from Hugging Face; record it in provenance so the exact snapshot
   consumed is provable.
2. Open one TRAIN shard for row-group-level reads (Parquet metadata
   footer + selected row groups only — no full-shard download).
3. Scan lightweight columns (metadata, modalities, category_id counts)
   for candidate eligibility. No PNG or PDF bytes fetched at this stage.
4. Sort eligible rows in the scanned window by ``SHA-256(page_hash)``
   ascending.
5. First candidate that passes filters wins. Only THEN fetch the full
   row (PNG + PDF + all annotation columns).
6. Write the per-page manifest + acceptance-ready cache under
   ``corpus/eval_v1/doclaynet/<page_hash>/``.
7. Append every page whose row-group bytes were fetched to the
   development-contamination ledger.

Split policy: TRAIN split only. The DocLayNet TEST split is the
paper's / ICDAR-2023's established benchmark surface and must not be
touched during B (see recon report §10 item 5, and B's discipline
framing).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_v1.acquisition.doclaynet_hf import (
    AcquisitionError,
    HfDatasetRef,
    acquire_page,
    apply_page_filters,
    build_page,
    iter_pages_in_row_group,
    open_parquet_shard,
    resolve_dataset_ref,
)

DISCOVERY_METHOD = "aws-doclaynet-v1.2-parquet-rowgroup-walk"
DISCOVERY_METHOD_VERSION = "1"
DEFAULT_SHARD_KEY = "data/train-00000-of-00072.parquet"


@dataclass
class CandidateOutcome:
    page_hash: str | None
    image_id: int | None
    rank_key: str | None
    stage: str  # "lightweight_filter" | "selected"
    reason: str | None
    n_annotations: int | None = None
    png_sha256: str | None = None
    pdf_sha256: str | None = None


@dataclass
class ProveOneRun:
    discovery_method: str = DISCOVERY_METHOD
    discovery_method_version: str = DISCOVERY_METHOD_VERSION
    discovery_started_utc: str = ""
    discovery_finished_utc: str = ""
    shard_key: str = ""
    split: str = "train"
    row_groups_scanned: int = 0
    rows_scanned: int = 0
    rows_excluded_pre_fetch: int = 0
    outcomes: list[CandidateOutcome] = field(default_factory=list)
    selected: CandidateOutcome | None = None
    dataset_ref: HfDatasetRef | None = None


def _rank(page_hash: str) -> str:
    return hashlib.sha256(page_hash.encode("ascii")).hexdigest()


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _append_exclusion_ledger(
    ledger_path: Path,
    outcomes: list[CandidateOutcome],
    selected: CandidateOutcome | None,
    discovery_started_utc: str,
    dataset_ref: HfDatasetRef,
) -> None:
    """Record every page whose row-group bytes were fetched.

    Lightweight-column reads (metadata + modalities + category_id) DO
    fetch bytes from S3-backed parquet row groups, so any page whose
    row was read at all is minimally inspected. We record any such
    row-scan as development-contamination for completeness. If this
    turns out to be too broad, tighten later — but never loosen the
    ledger after the fact.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).isoformat(timespec="seconds")
    entries: list[dict[str, Any]] = []
    for o in outcomes:
        entries.append(
            {
                "page_hash": o.page_hash,
                "image_id": o.image_id,
                "usage": (
                    "B1a-3 corpus-adapter prove-one candidate "
                    "(row-group-lightweight inspected"
                    + (", rejected)" if o.stage == "lightweight_filter" else ")")
                ),
                "evaluation_eligibility": "development_only",
                "held_out_v1_eligible": False,
                "reason": o.reason
                or "row-group-lightweight columns inspected during selection",
                "n_annotations": o.n_annotations,
                "png_sha256": o.png_sha256,
                "pdf_sha256": o.pdf_sha256,
                "dataset_id": dataset_ref.dataset_id,
                "dataset_commit_sha": dataset_ref.sha,
                "recorded_utc": ts,
                "discovery_started_utc": discovery_started_utc,
            }
        )
    if selected is not None:
        entries.append(
            {
                "page_hash": selected.page_hash,
                "image_id": selected.image_id,
                "usage": "B1a-3 corpus-adapter prove-one (selected)",
                "evaluation_eligibility": "development_only",
                "held_out_v1_eligible": False,
                "reason": "inspected during evaluation-infrastructure development",
                "n_annotations": selected.n_annotations,
                "png_sha256": selected.png_sha256,
                "pdf_sha256": selected.pdf_sha256,
                "dataset_id": dataset_ref.dataset_id,
                "dataset_commit_sha": dataset_ref.sha,
                "recorded_utc": ts,
                "discovery_started_utc": discovery_started_utc,
            }
        )
    if not entries:
        return
    with ledger_path.open("a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, sort_keys=True) + "\n")


def run(
    corpus_dir: Path,
    exclusion_ledger: Path,
    report_path: Path,
    shard_key: str,
    max_row_groups: int,
) -> ProveOneRun:
    result = ProveOneRun(
        shard_key=shard_key,
        discovery_started_utc=datetime.now(tz=UTC).isoformat(timespec="seconds"),
    )
    print(f"[prove-one] resolving {shard_key}...")
    ref = resolve_dataset_ref()
    result.dataset_ref = ref
    print(f"[prove-one] dataset SHA: {ref.sha}  (last modified {ref.last_modified})")

    pf = open_parquet_shard(shard_key, revision=ref.sha)
    total_row_groups = pf.metadata.num_row_groups
    print(f"[prove-one] shard has {total_row_groups} row groups, "
          f"{pf.metadata.num_rows} rows total")

    # ---- Pass 1: lightweight scan, rank, filter -------------------
    lightweight: list[tuple[str, int | None, dict[str, Any]]] = []
    row_groups_to_scan = min(max_row_groups, total_row_groups)
    result.row_groups_scanned = row_groups_to_scan
    for rg in range(row_groups_to_scan):
        for row in iter_pages_in_row_group(pf, rg):
            result.rows_scanned += 1
            md = row["metadata"] or {}
            page_hash: str | None = md.get("page_hash")
            image_id: int | None = md.get("image_id")
            if not page_hash:
                continue
            decision = apply_page_filters(row)
            n_annot = len(row["category_id"] or [])
            if not decision.ok:
                result.outcomes.append(
                    CandidateOutcome(
                        page_hash=page_hash,
                        image_id=image_id,
                        rank_key=_rank(page_hash),
                        stage="lightweight_filter",
                        reason=decision.reason,
                        n_annotations=n_annot,
                    )
                )
                result.rows_excluded_pre_fetch += 1
                continue
            lightweight.append((page_hash, image_id, {
                "row_group": rg,
                "n_annotations": n_annot,
            }))
    print(f"[prove-one] eligible after lightweight filter: {len(lightweight)}")

    if not lightweight:
        _append_exclusion_ledger(
            exclusion_ledger, result.outcomes, None, result.discovery_started_utc, ref
        )
        raise AcquisitionError(
            f"DocLayNet prove-one selection failed: no page in shard "
            f"{shard_key} (first {row_groups_to_scan} row groups) passed the "
            f"locked filters. Do NOT broaden filters without human "
            f"authorization."
        )

    # Deterministic candidate ordering.
    lightweight.sort(key=lambda t: _rank(t[0]))

    # ---- Pass 2: fetch the winner's full row ----------------------
    winner_hash, winner_image_id, winner_info = lightweight[0]
    print(f"[prove-one] winner: page_hash={winner_hash[:24]}... "
          f"(rank_key {_rank(winner_hash)[:12]}...)")

    # Read that row group with image + pdf columns.
    rg = winner_info["row_group"]
    heavy_row = None
    for row in iter_pages_in_row_group(pf, rg, include_image=True, include_pdf=True):
        if (row["metadata"] or {}).get("page_hash") == winner_hash:
            heavy_row = row
            break
    if heavy_row is None:
        raise AcquisitionError(
            f"could not re-locate winning page_hash={winner_hash} in "
            f"row group {rg} after lightweight scan"
        )
    page = build_page(heavy_row)

    # Persist to disk + write manifest.
    shard_sha256: str | None = None  # not computed for prove-one (streamed)
    discovery = {
        "discovery_method": DISCOVERY_METHOD,
        "discovery_method_version": DISCOVERY_METHOD_VERSION,
        "discovery_started_utc": result.discovery_started_utc,
        "shard_key": shard_key,
        "row_group": rg,
        "candidate_ordering_rule": "SHA-256(page_hash) ascending",
        "candidate_rank_key": _rank(winner_hash),
        "candidate_page_hash": winner_hash,
        "split": "train",
    }
    acquired = acquire_page(
        page,
        ref,
        shard_key=shard_key,
        shard_sha256=shard_sha256,
        root=corpus_dir,
        discovery_provenance=discovery,
        selection_role="corpus-adapter prove-one",
    )
    result.selected = CandidateOutcome(
        page_hash=winner_hash,
        image_id=winner_image_id,
        rank_key=_rank(winner_hash),
        stage="selected",
        reason=None,
        n_annotations=len(page.annotations),
        png_sha256=_sha256(page.png_bytes),
        pdf_sha256=_sha256(page.pdf_bytes) if page.pdf_bytes else None,
    )
    _png_sha = result.selected.png_sha256 or ""
    _pdf_sha = result.selected.pdf_sha256 or ""
    print(
        f"[prove-one] SELECTED {winner_hash[:24]}: "
        f"n_annotations={len(page.annotations)}, "
        f"png_sha256={_png_sha[:12]}..., "
        f"pdf_sha256={_pdf_sha[:12] if _pdf_sha else 'none'}..."
    )

    result.discovery_finished_utc = datetime.now(tz=UTC).isoformat(timespec="seconds")
    _append_exclusion_ledger(
        exclusion_ledger, result.outcomes, result.selected, result.discovery_started_utc, ref
    )
    _write_report(report_path, result, corpus_dir, acquired)
    return result


def _write_report(
    report_path: Path,
    result: ProveOneRun,
    corpus_dir: Path,
    acquired,
) -> None:
    ref = result.dataset_ref
    if ref is None:
        raise RuntimeError(
            "prove-one report writer requires a resolved HF dataset ref; "
            "run() must set result.dataset_ref before calling _write_report"
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "authorization": "B1a-3",
        "role": "corpus-adapter prove-one",
        "distribution": {
            "source": "huggingface_datasets_parquet",
            "dataset_id": ref.dataset_id,
            "dataset_commit_sha": ref.sha,
            "dataset_last_modified_utc": ref.last_modified,
            "shard_key": result.shard_key,
            "split": result.split,
        },
        "discovery": {
            "method": result.discovery_method,
            "method_version": result.discovery_method_version,
            "started_utc": result.discovery_started_utc,
            "finished_utc": result.discovery_finished_utc,
            "row_groups_scanned": result.row_groups_scanned,
            "rows_scanned": result.rows_scanned,
            "ordering_rule": "SHA-256(page_hash) ascending",
        },
        "filters": {
            "structural": [
                "annotations non-empty",
                "10 <= n_annotations <= 200 (engineering bound, not a claimed population percentile)",
                ">= 1 Table region",
                ">= 1 additional non-Text structural region",
            ],
            "notes": (
                "v1.2 Parquet is upstream-deduplicated on page_hash, so the "
                "paper's `precedence == 0` policy is satisfied by the "
                "distribution itself. The `precedence` field does not exist "
                "in v1.2 and is recorded as null in per-page provenance."
            ),
        },
        "version_policy": (
            "one page_hash -> one document. Test split intentionally left "
            "untouched during B (paper/ICDAR-2023 benchmark surface)."
        ),
        "counts": {
            "rows_excluded_pre_fetch": result.rows_excluded_pre_fetch,
            "outcomes": len(result.outcomes),
            "selected": 1 if result.selected else 0,
        },
        "selected": None
        if not result.selected
        else {
            "page_hash": result.selected.page_hash,
            "image_id": result.selected.image_id,
            "rank_key": result.selected.rank_key,
            "n_annotations": result.selected.n_annotations,
            "png_sha256": result.selected.png_sha256,
            "pdf_sha256": result.selected.pdf_sha256,
            "cached_dir": str(acquired.directory),
            "manifest_path": str(acquired.manifest_path),
        },
        "candidate_trail_head": [
            {
                "page_hash": o.page_hash,
                "image_id": o.image_id,
                "rank_key": o.rank_key,
                "stage": o.stage,
                "reason": o.reason,
                "n_annotations": o.n_annotations,
            }
            for o in result.outcomes[:20]
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"[prove-one] report -> {report_path}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus-dir", type=Path, default=Path("corpus/eval_v1/doclaynet"))
    p.add_argument(
        "--exclusion-ledger",
        type=Path,
        default=Path("docs/evaluation/DOCLAYNET_EXCLUSION_LEDGER.jsonl"),
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("docs/evaluation/DOCLAYNET_PROVE_ONE_SELECTION.json"),
    )
    p.add_argument("--shard-key", default=DEFAULT_SHARD_KEY)
    p.add_argument("--max-row-groups", type=int, default=3)
    args = p.parse_args(argv)
    run(
        corpus_dir=args.corpus_dir,
        exclusion_ledger=args.exclusion_ledger,
        report_path=args.report,
        shard_key=args.shard_key,
        max_row_groups=args.max_row_groups,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
