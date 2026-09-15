"""DocLayNet population enumeration for B1a-5a (metadata only, all 72 shards).

Locked anchors:
- dataset: docling-project/DocLayNet-v1.2
- revision: 0daf93102e2efce76c3e11a274a5e0d0969391d3
- split: train
- shards: all 72

Reads ONLY the lightweight columns required for identity + eligibility
(metadata + modalities + category_id + area). No image or PDF payload
columns. Per human clarification: this metadata-only pass is NOT
contamination.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmarks.eval_v1.acquisition.doclaynet_hf import apply_page_filters
from benchmarks.eval_v1.selection.pilot_selector import Candidate, make_candidate

DATASET_ID = "docling-project/DocLayNet-v1.2"
REVISION = "0daf93102e2efce76c3e11a274a5e0d0969391d3"
SPLIT = "train"
N_SHARDS = 72
DOCLAYNET_ELIGIBILITY_RULE_VERSION = "1"


def train_shard_keys() -> list[str]:
    return [f"data/train-{i:05d}-of-{N_SHARDS:05d}.parquet" for i in range(N_SHARDS)]


@dataclass(frozen=True)
class DocLayNetPopulationSnapshot:
    dataset_id: str
    revision: str
    split: str
    n_shards_scanned: int
    n_rows_total: int
    per_shard_rows: list[dict[str, Any]]


def enumerate_eligible_train_population(
    on_progress=None,
) -> tuple[list[Candidate], DocLayNetPopulationSnapshot, list[dict[str, Any]]]:
    """Scan all 72 train shards' lightweight columns and return eligible candidates.

    Returns (eligible, snapshot, ineligible_head).
    """
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    fs = HfFileSystem()
    eligible: list[Candidate] = []
    ineligible: list[dict[str, Any]] = []
    per_shard: list[dict[str, Any]] = []
    n_rows_total = 0
    seen_page_hashes: set[str] = set()

    for i, shard_key in enumerate(train_shard_keys()):
        if on_progress:
            on_progress(i, N_SHARDS, len(eligible))
        path = f"datasets/{DATASET_ID}/{shard_key}"
        fh = fs.open(path, mode="rb", revision=REVISION)
        pf = pq.ParquetFile(fh)
        shard_rows = pf.metadata.num_rows
        n_rows_total += shard_rows
        per_shard.append({"shard_key": shard_key, "n_rows": shard_rows})
        # Read the whole shard's lightweight columns.
        tbl = pf.read(columns=["metadata", "modalities", "category_id"])
        md_col = tbl.column("metadata").to_pylist()
        cat_col = tbl.column("category_id").to_pylist()
        for md, cats in zip(md_col, cat_col, strict=True):
            page_hash = (md or {}).get("page_hash")
            if not page_hash:
                continue
            # v1.2 is upstream-deduplicated, but defensive dedup at
            # our selection layer costs nothing.
            if page_hash in seen_page_hashes:
                continue
            seen_page_hashes.add(page_hash)
            decision = apply_page_filters({"category_id": cats or []})
            if not decision.ok:
                if len(ineligible) < 200:
                    ineligible.append(
                        {"canonical_id": page_hash, "reason": decision.reason}
                    )
                continue
            eligible.append(
                make_candidate(
                    canonical_id=page_hash,
                    metadata={
                        "page_hash": page_hash,
                        "shard_key": shard_key,
                        "image_id": md.get("image_id"),
                        "original_filename": md.get("original_filename"),
                        "page_no": md.get("page_no"),
                        "doc_category": md.get("doc_category"),
                        "n_annotations": len(cats or []),
                    },
                    corpus="doclaynet",
                )
            )
    snapshot = DocLayNetPopulationSnapshot(
        dataset_id=DATASET_ID,
        revision=REVISION,
        split=SPLIT,
        n_shards_scanned=N_SHARDS,
        n_rows_total=n_rows_total,
        per_shard_rows=per_shard,
    )
    return eligible, snapshot, ineligible


__all__ = [
    "DATASET_ID",
    "N_SHARDS",
    "REVISION",
    "SPLIT",
    "DocLayNetPopulationSnapshot",
    "enumerate_eligible_train_population",
    "train_shard_keys",
]
