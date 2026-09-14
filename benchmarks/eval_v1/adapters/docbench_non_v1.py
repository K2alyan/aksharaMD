"""DocBench non-V1 adapter — the NOT_APPLICABLE demonstrator.

DocBench is NOT on the V1 corpus manifest (PROTOCOL_V1.md §2.2). D2 is
kept in the smoke set to prove the machinery correctly reports capability
boundaries. Ground-truth ingestion and V1 conventional metrics return
``None`` (which the runner translates to ``NOT_APPLICABLE``); the source
still ingests so parser execution and AksharaMD evaluation can run.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..corpus_adapter import (
    CorpusCapabilities,
    GroundTruth,
    SourceIngestion,
    V1CorpusAdapter,
)


class DocBenchNonV1Adapter(V1CorpusAdapter):
    NOT_APPLICABLE_REASON = "DocBench is not on the V1 corpus manifest (PROTOCOL_V1.md §2.2)."

    def __init__(self, doc_id_to_path: dict[str, Path]):
        self._paths = dict(doc_id_to_path)

    def capabilities(self) -> CorpusCapabilities:
        return CorpusCapabilities(
            corpus_name="docbench",
            on_v1_manifest=False,
            supports_textual_gt=False,
            supports_layout_gt=False,
            supports_clause_span_gt=False,
            supports_downstream_qa_gt=False,
            supports_clean_native_fpr=False,
            not_applicable_reasons={
                "textual_gt": self.NOT_APPLICABLE_REASON,
                "layout_gt": self.NOT_APPLICABLE_REASON,
                "clause_span_gt": self.NOT_APPLICABLE_REASON,
                "downstream_qa_gt": self.NOT_APPLICABLE_REASON,
                "clean_native_fpr": self.NOT_APPLICABLE_REASON,
            },
        )

    def acquire(self, doc_id: str) -> Path:
        path = self._paths.get(doc_id)
        if path is None:
            raise KeyError(f"DocBench doc_id {doc_id!r} has no cached PDF path")
        if not path.exists():
            raise FileNotFoundError(
                f"DocBench doc_id {doc_id!r} maps to {path} which does not exist."
            )
        return path

    def ingest_source(self, doc_id: str) -> SourceIngestion:
        path = self.acquire(doc_id)
        data = path.read_bytes()
        return SourceIngestion(
            doc_id=doc_id,
            path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            media_type="application/pdf",
            provenance={
                "corpus": "docbench",
                "on_v1_manifest": False,
                "source_kind": "acl_paper_cached",
            },
        )

    def ingest_ground_truth(self, doc_id: str) -> GroundTruth | None:
        """DocBench supplies no V1 ground truth by design (non-V1 corpus)."""
        return None

    def provenance(self) -> dict[str, Any]:
        return {
            "corpus_name": "docbench",
            "on_v1_manifest": False,
            "note": self.NOT_APPLICABLE_REASON,
        }
