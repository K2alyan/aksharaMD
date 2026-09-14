"""Manifest for the 3 development documents used by Authorization A.

These three documents are permanently DEVELOPMENT-ONLY. They are
prohibited from any future held-out V1 evaluation subset for their
originating corpora (QASPER, TAT-DQA). DocBench is not currently on
the V1 corpus list; its inclusion here is a smoke-test-only choice.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SmokeDoc:
    doc_id: str
    path: Path
    sha256: str
    size_bytes: int
    origin_corpus: str
    v1_corpus_member: bool
    stress_axis: str
    rationale: str


SMOKE_DOCS: tuple[SmokeDoc, ...] = (
    SmokeDoc(
        doc_id="D1_qasper_1503_00841",
        path=Path(".cache/qasper/1503.00841-29290de61c95.pdf"),
        sha256="9ca21eaff3c946e95cc649e627db3a4b9e69d0f7397bae3e30d39be495261ba5",
        size_bytes=956266,
        origin_corpus="QASPER (arXiv 1503.00841)",
        v1_corpus_member=True,
        stress_axis="prose",
        rationale=(
            "Prose-heavy academic paper. Exercises text extraction plus "
            "content-substance detectors (W_GIBBERISH, W_ENCODING_ARTIFACTS, "
            "W_PLACEHOLDER_STUB) and the §6.2 QASPER-downstream loader seam. "
            "Permanently excluded from held-out V1 QASPER subset."
        ),
    ),
    SmokeDoc(
        doc_id="D2_docbench_P19_1598",
        path=Path(".cache/docbench/0/P19-1598.pdf"),
        sha256="1074c93b17355fc3b9175697f2cf5dbdd062a9d22f394c29856e154f91199563",
        size_bytes=322546,
        origin_corpus="DocBench (ACL paper P19-1598)",
        v1_corpus_member=False,
        stress_axis="mixed",
        rationale=(
            "ACL-style paper from DocBench. Exercises mixed structural "
            "content (equations, figures, references). CAVEAT: DocBench is "
            "not on the V1 corpus list per PROTOCOL_V1.md §2.2. Using it "
            "here documents the fact that the V1 corpus-adapter seam is "
            "unexercised for a V1 corpus — a smoke-test finding."
        ),
    ),
    SmokeDoc(
        doc_id="D3_tatdqa_003755794b",
        path=Path(".cache/tat_dqa/tat_docs/dev/003755794bbbbcffd0667b9600aeb0be.pdf"),
        sha256="b59bdb9725ac5345899a107f6cc894d5763521e3dbe2d5febf46ff31920d21c4",
        size_bytes=1515862,
        origin_corpus="TAT-DQA (financial report)",
        v1_corpus_member=True,
        stress_axis="tables",
        rationale=(
            "Financial-report page(s) with tables. Exercises structural "
            "detectors (W_TABLE_MISSING, W_MULTICOLUMN_ORDER, "
            "W_HEADER_FOOTER_TABLE_GARBLED). Permanently excluded from "
            "held-out V1 TAT-DQA subset."
        ),
    ),
)


V1_PARSERS: tuple[str, ...] = (
    "aksharamd-reference",
    "marker",
    "docling",
    "markitdown",
)
