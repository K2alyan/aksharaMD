"""Corpus adapters for the parsed-vs-raw harness."""
from __future__ import annotations

from .qasper import QasperCorpus

__all__ = ["QasperCorpus", "get_corpus"]


def get_corpus(name: str):
    """Factory that returns a corpus adapter by name.

    Adds one indirection so the driver can route ``--corpus X`` without
    the CLI having to know about optional-dep import failures. Corpus
    adapters raise at ``iter_documents`` time, not at import time, so a
    missing ``datasets`` install fails helpfully at run rather than at
    ``from benchmarks.parsed_vs_raw import ...``.
    """
    lowered = name.lower().strip()
    if lowered == "qasper":
        return QasperCorpus()
    raise ValueError(f"Unknown corpus: {name!r}. Supported: qasper")
