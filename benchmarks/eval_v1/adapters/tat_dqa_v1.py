"""TAT-DQA V1 corpus adapter — QA-pair ground truth over financial tables."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..corpus_adapter import (
    CorpusCapabilities,
    GroundTruth,
    SourceIngestion,
    V1CorpusAdapter,
)


_CACHE_ROOT = Path(".cache/tat_dqa")
_DEV_JSON = _CACHE_ROOT / "tatdqa_dataset_dev.json"


class TatDqaV1Adapter(V1CorpusAdapter):
    def __init__(self, doc_id_to_path: dict[str, Path]):
        self._paths = dict(doc_id_to_path)
        self._index_cache: dict[str, dict[str, Any]] | None = None

    def _index(self) -> dict[str, dict[str, Any]]:
        if self._index_cache is None:
            data = json.loads(_DEV_JSON.read_text(encoding="utf-8"))
            index: dict[str, dict[str, Any]] = {}
            for record in data:
                doc = record.get("doc", {}) or {}
                doc_id = doc.get("uid") or record.get("doc_uid") or ""
                if not doc_id:
                    continue
                bucket = index.setdefault(doc_id, {"doc": doc, "qas": []})
                bucket["qas"].extend(record.get("questions", []) or [])
            self._index_cache = index
        return self._index_cache

    def capabilities(self) -> CorpusCapabilities:
        return CorpusCapabilities(
            corpus_name="tat_dqa",
            on_v1_manifest=True,
            supports_textual_gt=False,
            supports_layout_gt=False,
            supports_clause_span_gt=False,
            supports_downstream_qa_gt=True,
            supports_clean_native_fpr=False,
            not_applicable_reasons={
                "textual_gt": "TAT-DQA does not provide XML full-text.",
                "layout_gt": "TAT-DQA does not provide DocLayNet-style bboxes.",
                "clause_span_gt": "TAT-DQA does not provide clause span annotations.",
                "clean_native_fpr": "TAT-DQA is a table-heavy financial corpus, not the FPR baseline.",
            },
        )

    def acquire(self, doc_id: str) -> Path:
        path = self._paths.get(doc_id)
        if path is None:
            raise KeyError(f"TAT-DQA doc_id {doc_id!r} has no cached PDF path")
        if not path.exists():
            raise FileNotFoundError(
                f"TAT-DQA doc_id {doc_id!r} maps to {path} which does not exist. "
                f"Authorization A.1 does not authorize corpus acquisition beyond "
                f"the three development documents."
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
            provenance={"corpus": "tat_dqa", "source_kind": "financial_report_cached"},
        )

    def ingest_ground_truth(self, doc_id: str) -> GroundTruth | None:
        idx = self._index()
        record = idx.get(doc_id)
        if not record:
            return None
        pairs: list[dict[str, str]] = []
        for q in record.get("qas", []):
            question_text = q.get("question", "")
            answer = q.get("answer", "")
            if isinstance(answer, list):
                answer = " | ".join(str(a) for a in answer)
            pairs.append({
                "question": question_text,
                "gold_answer": str(answer),
                "answer_type": q.get("answer_type", "numeric"),
            })
        return GroundTruth(
            doc_id=doc_id,
            kind="qa_pairs",
            data={"qa_pairs": pairs, "doc": record.get("doc", {})},
            provenance={"corpus": "tat_dqa", "split": "dev", "pair_count": len(pairs)},
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "corpus_name": "tat_dqa",
            "split": "dev",
            "cache_root": str(_CACHE_ROOT),
        }
