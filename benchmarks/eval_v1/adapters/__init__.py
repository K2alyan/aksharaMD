"""Concrete V1CorpusAdapter subclasses shipped under Authorization A.1.

Only corpora the A.1 rerun actually exercises are implemented:

- QASPER (D1) — full V1 adapter with QA-pair GT.
- DocBench-non-V1 (D2) — explicit non-V1 handling; GT stage returns
  NOT_APPLICABLE with the reason "DocBench is not on the V1 corpus
  manifest (§2.2)".
- TAT-DQA (D3) — full V1 adapter with QA-pair GT.

PMC-OA, DocLayNet, Federal Register/SEC, and CUAD have no adapters
under A.1 per the user's correction ("no speculative corpus stubs").
An interface is enough; adapters ship when a subsequent authorization
actually exercises those corpora.
"""
from .qasper_v1 import QasperV1Adapter
from .docbench_non_v1 import DocBenchNonV1Adapter
from .tat_dqa_v1 import TatDqaV1Adapter

__all__ = ["QasperV1Adapter", "DocBenchNonV1Adapter", "TatDqaV1Adapter"]
