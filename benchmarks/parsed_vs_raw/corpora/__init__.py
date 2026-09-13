"""Corpus adapters for the parsed-vs-raw harness."""
from __future__ import annotations

from .docbench import DocBenchCorpus
from .qasper import QasperCorpus
from .tat_dqa import TatDqaCorpus

__all__ = ["DocBenchCorpus", "QasperCorpus", "TatDqaCorpus", "get_corpus"]

_SUPPORTED = ("qasper", "docbench", "tat-dqa")


def get_corpus(name: str):
    """Factory that returns a corpus adapter by name.

    Adds one indirection so the driver can route ``--corpus X`` without
    the CLI having to know about optional-dep import failures. Corpus
    adapters raise at ``iter_documents`` time, not at import time, so a
    missing ``gdown`` install (docbench/tat-dqa) or ``datasets`` install
    (legacy code paths) fails helpfully at run rather than at
    ``from benchmarks.parsed_vs_raw import ...``.
    """
    lowered = name.lower().strip()
    if lowered == "qasper":
        return QasperCorpus()
    if lowered == "docbench":
        return DocBenchCorpus()
    if lowered in {"tat-dqa", "tat_dqa", "tatdqa"}:
        return TatDqaCorpus()
    raise ValueError(
        f"Unknown corpus: {name!r}. Supported: {', '.join(_SUPPORTED)}"
    )
