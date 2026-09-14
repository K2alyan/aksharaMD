"""QASPER V1 corpus adapter — QA-pair ground truth.

QASPER supplies information-seeking QA pairs per paper. It does NOT
supply XML full-text (that's PMC-OA), so its supported evidence tier is
G2 downstream QA, not G1 textual. Stages that require textual G1 or
layout G1 return ``NOT_APPLICABLE`` with reason.
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

from ..corpus_adapter import (
    CorpusCapabilities,
    GroundTruth,
    SourceIngestion,
    V1CorpusAdapter,
)


_CACHE_ROOT = Path(".cache/qasper")
_DEV_JSON_NAME = "qasper-dev-v0.3.json"


def _load_dev_index() -> dict[str, Any]:
    """Load QASPER dev JSON, extracting from tarball if not present."""
    json_path = _CACHE_ROOT / _DEV_JSON_NAME
    if not json_path.exists():
        tar_path = _CACHE_ROOT / "qasper-train-dev-v0.3.tgz"
        if not tar_path.exists():
            raise FileNotFoundError(
                f"QASPER dev JSON not found; expected {json_path} or the "
                f"tarball at {tar_path}"
            )
        with tarfile.open(tar_path, "r:gz") as tar:
            member = None
            for m in tar.getmembers():
                if m.name.endswith(_DEV_JSON_NAME):
                    member = m
                    break
            if member is None:
                raise FileNotFoundError(f"{_DEV_JSON_NAME} not in {tar_path}")
            f = tar.extractfile(member)
            if f is None:
                raise RuntimeError("could not read QASPER JSON from tarball")
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_bytes(f.read())
    return json.loads(json_path.read_text(encoding="utf-8"))


def _extract_qa_pairs(paper: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten a QASPER paper into (question, gold_answer) pairs."""
    qas = paper.get("qas", []) or []
    pairs: list[dict[str, str]] = []
    for qa in qas:
        question = qa.get("question", "")
        for ans in qa.get("answers", []) or []:
            a = ans.get("answer", {}) or {}
            if a.get("unanswerable"):
                pairs.append({"question": question, "gold_answer": "unanswerable", "answer_type": "unanswerable"})
                continue
            if a.get("free_form_answer"):
                pairs.append({
                    "question": question,
                    "gold_answer": a["free_form_answer"],
                    "answer_type": "abstractive",
                })
                continue
            if a.get("yes_no") is not None:
                pairs.append({
                    "question": question,
                    "gold_answer": "yes" if a["yes_no"] else "no",
                    "answer_type": "boolean",
                })
                continue
            spans = a.get("extractive_spans") or []
            if spans:
                pairs.append({
                    "question": question,
                    "gold_answer": " | ".join(spans),
                    "answer_type": "extractive",
                })
    # Deduplicate identical (question, gold_answer) tuples per paper.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for p in pairs:
        key = (p["question"], p["gold_answer"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


class QasperV1Adapter(V1CorpusAdapter):
    def __init__(self, doc_id_to_path: dict[str, Path]):
        """``doc_id_to_path`` maps QASPER paper_id (arxiv id) → cached PDF path."""
        self._paths = dict(doc_id_to_path)
        self._index_cache: dict[str, Any] | None = None

    def _index(self) -> dict[str, Any]:
        if self._index_cache is None:
            self._index_cache = _load_dev_index()
        return self._index_cache

    def capabilities(self) -> CorpusCapabilities:
        return CorpusCapabilities(
            corpus_name="qasper",
            on_v1_manifest=True,
            supports_textual_gt=False,
            supports_layout_gt=False,
            supports_clause_span_gt=False,
            supports_downstream_qa_gt=True,
            supports_clean_native_fpr=False,
            not_applicable_reasons={
                "textual_gt": "QASPER does not provide XML full-text; use PMC-OA for W_DROPPED_CONTENT G1 evaluation.",
                "layout_gt": "QASPER does not provide DocLayNet-style bboxes.",
                "clause_span_gt": "QASPER does not provide clause span annotations; use CUAD.",
                "clean_native_fpr": "QASPER is not the §2.2 FPR-baseline corpus; use Federal Register / SEC.",
            },
        )

    def acquire(self, doc_id: str) -> Path:
        path = self._paths.get(doc_id)
        if path is None:
            raise KeyError(f"QASPER doc_id {doc_id!r} has no cached PDF path")
        if not path.exists():
            raise FileNotFoundError(
                f"QASPER doc_id {doc_id!r} maps to {path} which does not exist. "
                f"Authorization A.1 does not authorize corpus acquisition beyond "
                f"the three development documents."
            )
        return path

    def ingest_source(self, doc_id: str) -> SourceIngestion:
        import hashlib
        path = self.acquire(doc_id)
        data = path.read_bytes()
        return SourceIngestion(
            doc_id=doc_id,
            path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            media_type="application/pdf",
            provenance={"corpus": "qasper", "source_kind": "arxiv_pdf_cached"},
        )

    def ingest_ground_truth(self, doc_id: str) -> GroundTruth | None:
        index = self._index()
        paper = index.get(doc_id)
        if not paper:
            return None
        pairs = _extract_qa_pairs(paper)
        return GroundTruth(
            doc_id=doc_id,
            kind="qa_pairs",
            data={"paper_title": paper.get("title", ""), "qa_pairs": pairs},
            provenance={
                "corpus": "qasper",
                "release": "v0.3",
                "split": "dev",
                "pair_count": len(pairs),
            },
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "corpus_name": "qasper",
            "release": "v0.3",
            "split": "dev",
            "cache_root": str(_CACHE_ROOT),
        }
