"""Abstract V1 corpus adapter.

Each V1 corpus (QASPER, TAT-DQA, PMC-OA, DocLayNet, Federal Register/SEC,
CUAD, and any future V1 corpus) provides a subclass. A.1 ships concrete
subclasses only for corpora the smoke rerun actually exercises
(QASPER, DocBench-as-non-V1, TAT-DQA). Speculative subclasses are
deliberately absent per the Authorization-A.1 correction; corpora are
implemented when B or a subsequent authorization needs them.

The adapter surface is intentionally narrow: acquisition, source
ingestion, ground-truth ingestion, capability declaration, and
provenance. Everything downstream — normalization, conventional metric,
AksharaMD scoring, adjudication — takes the adapter's outputs and lives
in its own module.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CorpusCapabilities:
    """What ground-truth and metric evidence this corpus can supply.

    Fields mirror the PROTOCOL_V1.md §2.3 detector × corpus matrix rows.
    A ``True`` value means the corpus can adjudicate the stage; a
    ``False`` value means the stage should return ``NOT_APPLICABLE`` for
    documents from this corpus with the accompanying ``reason``.
    """

    corpus_name: str
    on_v1_manifest: bool
    supports_textual_gt: bool
    supports_layout_gt: bool
    supports_clause_span_gt: bool
    supports_downstream_qa_gt: bool
    supports_clean_native_fpr: bool
    not_applicable_reasons: dict[str, str] = field(default_factory=dict)
    """Per-stage explanation used when a stage returns NOT_APPLICABLE."""


@dataclass(frozen=True)
class SourceIngestion:
    """The parsed-ready source, plus provenance."""

    doc_id: str
    path: Path
    sha256: str
    size_bytes: int
    media_type: str
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroundTruth:
    """Corpus-supplied ground truth for a single document.

    ``kind`` names the evidence flavor (e.g. ``"qa_pairs"``,
    ``"xml_full_text"``, ``"bbox_layout"``, ``"clause_spans"``). ``data``
    is the corpus-native representation; downstream code inspects
    ``kind`` before consuming ``data``.
    """

    doc_id: str
    kind: str
    data: Any
    provenance: dict[str, Any] = field(default_factory=dict)


class V1CorpusAdapter(ABC):
    """Base class every V1 corpus implements."""

    @abstractmethod
    def capabilities(self) -> CorpusCapabilities:
        """Declare what evidence the corpus can supply for which stages."""

    @abstractmethod
    def acquire(self, doc_id: str) -> Path:
        """Return the local path to the raw source, acquiring if needed."""

    @abstractmethod
    def ingest_source(self, doc_id: str) -> SourceIngestion:
        """Load the source and record provenance."""

    @abstractmethod
    def ingest_ground_truth(self, doc_id: str) -> GroundTruth | None:
        """Return the corpus ground truth for the document, or None if
        the corpus does not supply ground truth for it."""

    def provenance(self) -> dict[str, Any]:
        """Free-form corpus-level provenance (version, URLs, splits).
        Overridable; default is empty."""
        return {}
