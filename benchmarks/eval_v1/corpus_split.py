"""Deterministic corpus split + provenance utilities.

Under Authorization A.1 this ships LOGIC only. No pilot or held-out
document is selected; no CORPUS_MANIFEST.md is written. The functions
here define how a downstream authorization will assign splits.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Partition(StrEnum):
    DEV = "dev"
    CAL = "cal"
    HELD_OUT = "held_out"


# Boundaries per PROTOCOL_V1.md §8.1: ~10% dev, ~20% cal, ~70% held-out.
_DEV_BOUNDARY = 10
_CAL_BOUNDARY = 30


def hash_document(path: Path) -> str:
    """SHA-256 of a document's raw bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assign_partition(doc_id: str) -> Partition:
    """Deterministic ``doc_id`` → ``{dev, cal, held_out}`` per §8.2.

    Uses ``SHA-256(doc_id) mod 100`` with published boundaries so the
    split is reproducible across runs, machines, and reviewers.
    """
    bucket = int(hashlib.sha256(doc_id.encode("utf-8")).hexdigest(), 16) % 100
    if bucket < _DEV_BOUNDARY:
        return Partition.DEV
    if bucket < _CAL_BOUNDARY:
        return Partition.CAL
    return Partition.HELD_OUT


@dataclass(frozen=True)
class DocumentProvenance:
    doc_id: str
    sha256: str
    size_bytes: int
    source_url: str | None
    partition: Partition
    corpus_name: str


def record_provenance(
    doc_id: str,
    path: Path,
    corpus_name: str,
    source_url: str | None = None,
) -> DocumentProvenance:
    return DocumentProvenance(
        doc_id=doc_id,
        sha256=hash_document(path),
        size_bytes=path.stat().st_size,
        source_url=source_url,
        partition=assign_partition(doc_id),
        corpus_name=corpus_name,
    )


def validate_no_overlap(
    dev: Iterable[str], cal: Iterable[str], held_out: Iterable[str]
) -> None:
    """Raise if any doc_id appears in more than one split."""
    dev_s, cal_s, held_s = set(dev), set(cal), set(held_out)
    overlaps: list[str] = []
    if dev_s & cal_s:
        overlaps.append(f"dev∩cal={sorted(dev_s & cal_s)}")
    if dev_s & held_s:
        overlaps.append(f"dev∩held_out={sorted(dev_s & held_s)}")
    if cal_s & held_s:
        overlaps.append(f"cal∩held_out={sorted(cal_s & held_s)}")
    if overlaps:
        raise ValueError("partition overlap detected: " + "; ".join(overlaps))
