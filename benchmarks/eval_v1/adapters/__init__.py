"""Concrete V1CorpusAdapter subclasses.

- QASPER (D1) — A.1 adapter with QA-pair GT.
- DocBench-non-V1 (D2) — explicit non-V1 handling; GT stage returns
  NOT_APPLICABLE with the reason "DocBench is not on the V1 corpus
  manifest (§2.2)".
- TAT-DQA (D3) — A.1 adapter with QA-pair GT.
- PMC-OA (B1a) — textual G1 adapter; consumes acquisition-side manifests
  and returns JATS full-text ground truth.

DocLayNet, Federal Register/SEC, and CUAD adapters ship when the
corresponding authorization exercises them.
"""
from .docbench_non_v1 import DocBenchNonV1Adapter
from .pmc_oa_v1 import PmcOaAsset, PmcOaV1Adapter
from .qasper_v1 import QasperV1Adapter
from .tat_dqa_v1 import TatDqaV1Adapter

__all__ = [
    "DocBenchNonV1Adapter",
    "PmcOaAsset",
    "PmcOaV1Adapter",
    "QasperV1Adapter",
    "TatDqaV1Adapter",
]
