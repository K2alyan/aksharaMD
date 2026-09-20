from __future__ import annotations

import hashlib

import pymupdf

from aksharamd.assessment import (
    CandidateArtifact,
    DetectorStatus,
    SourceArtifact,
    Verdict,
    assess_source_candidate,
)


def _source(pdf_bytes: bytes) -> SourceArtifact:
    return SourceArtifact(
        content_hash=hashlib.sha256(pdf_bytes).hexdigest(),
        byte_size=len(pdf_bytes),
        media_type="application/pdf",
        logical_id="synthetic.pdf",
        data=pdf_bytes,
    )


def _candidate(markdown: str, *, source_hash: str | None = None) -> CandidateArtifact:
    data = markdown.encode("utf-8")
    return CandidateArtifact(
        content_hash=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        media_type="text/markdown",
        logical_id="synthetic.md",
        data=data,
        parser_name="synthetic-parser",
        parser_version="1.2.3",
        parser_configuration_id="test-config",
        original_source_hash=source_hash,
    )


def _text_pdf() -> tuple[bytes, str, str]:
    first = " ".join(f"alpha{i}" for i in range(40))
    second = " ".join(f"beta{i}" for i in range(40))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 240), f"Section Alpha\n{first}", fontsize=10)
    page.insert_textbox(pymupdf.Rect(50, 280, 545, 470), f"Section Beta\n{second}", fontsize=10)
    payload = doc.tobytes()
    doc.close()
    return payload, first, second


def _table_pdf() -> tuple[bytes, str]:
    doc = pymupdf.open()
    page = doc.new_page()
    left, top, width, height = 72, 100, 360, 120
    for row in range(4):
        y = top + row * height / 3
        page.draw_line((left, y), (left + width, y))
    for column in range(4):
        x = left + column * width / 3
        page.draw_line((x, top), (x, top + height))
    cells = [
        ["Region", "Revenue", "Margin"],
        ["North", "120", "18 percent"],
        ["South", "95", "14 percent"],
    ]
    for row, values in enumerate(cells):
        for column, value in enumerate(values):
            page.insert_text((left + column * width / 3 + 8, top + row * height / 3 + 24), value, fontsize=9)
    payload = doc.tobytes()
    doc.close()
    markdown = "\n".join([
        "| Region | Revenue | Margin |",
        "| --- | ---: | ---: |",
        "| North | 120 | 18 percent |",
        "| South | 95 | 14 percent |",
    ])
    return payload, markdown


def _detector(result, detector_id: str):
    return next(item for item in result.source_comparison.detectors if item.detector_id == detector_id)


def test_dropped_section_is_distinguished_from_intact_markdown():
    pdf_bytes, first, second = _text_pdf()
    source = _source(pdf_bytes)

    intact = assess_source_candidate(
        source=source,
        candidate=_candidate(f"# Section Alpha\n\n{first}\n\n# Section Beta\n\n{second}"),
    )
    dropped = assess_source_candidate(
        source=source,
        candidate=_candidate(f"# Section Alpha\n\n{first}"),
    )

    detector_id = "source.pdf_text_token_retention"
    intact_detector = _detector(intact, detector_id)
    dropped_detector = _detector(dropped, detector_id)
    assert intact_detector.verdict == Verdict.PASS
    assert intact_detector.score == 100
    assert dropped_detector.verdict == Verdict.FAIL
    assert dropped_detector.score < 80
    assert "SOURCE_TEXT_TOKEN_LOSS" in dropped_detector.finding_codes
    assert dropped_detector.raw_evidence["missing_source_token_count"] > 0


def test_missing_table_structure_is_distinguished_from_intact_markdown():
    pdf_bytes, table_markdown = _table_pdf()
    source = _source(pdf_bytes)
    intact = assess_source_candidate(source=source, candidate=_candidate(table_markdown))
    flattened = assess_source_candidate(
        source=source,
        candidate=_candidate("Region Revenue Margin\nNorth 120 18 percent\nSouth 95 14 percent"),
    )

    detector_id = "source.pdf_table_structure_retention"
    intact_detector = _detector(intact, detector_id)
    flattened_detector = _detector(flattened, detector_id)
    assert intact_detector.status == DetectorStatus.ACTIVATED
    assert intact_detector.verdict == Verdict.PASS
    assert intact_detector.raw_evidence["source_table_count"] == 1
    assert flattened_detector.verdict == Verdict.FAIL
    assert flattened_detector.score == 0
    assert flattened_detector.finding_codes == ["SOURCE_TABLE_STRUCTURE_MISSING"]


def test_receipt_separates_scopes_and_carries_provenance():
    pdf_bytes, first, second = _text_pdf()
    result = assess_source_candidate(
        source=_source(pdf_bytes),
        candidate=_candidate(f"# Section Alpha\n{first}\n# Section Beta\n{second}"),
    )

    assert result.schema_version == "2.0-exploratory"
    assert result.policy_id.endswith("-exploratory")
    assert result.candidate_intrinsic.scope.value == "candidate_intrinsic"
    assert result.source_comparison.scope.value == "source_comparison"
    assert result.candidate_intrinsic.verdict == Verdict.PASS
    assert result.provenance.parser_name == "synthetic-parser"
    assert len(result.provenance.source_hash) == 64
    assert len(result.provenance.candidate_hash) == 64


def test_unsupported_source_abstains_explicitly():
    data = b"plain source"
    source = SourceArtifact(
        content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data),
        media_type="text/plain", data=data,
    )
    result = assess_source_candidate(source=source, candidate=_candidate("plain source"))

    assert result.source_comparison.verdict == Verdict.UNDETERMINED
    assert all(item.status == DetectorStatus.ABSTAINED for item in result.source_comparison.detectors)
    assert all(item.abstention_reason for item in result.source_comparison.detectors)


def test_declared_source_identity_mismatch_is_a_source_comparison_failure():
    pdf_bytes, first, second = _text_pdf()
    source = _source(pdf_bytes)
    result = assess_source_candidate(
        source=source,
        candidate=_candidate(f"{first}\n{second}", source_hash="0" * 64),
    )

    detector = _detector(result, "source.candidate_identity_binding")
    assert detector.status == DetectorStatus.ACTIVATED
    assert detector.verdict == Verdict.FAIL
    assert detector.finding_codes == ["SOURCE_IDENTITY_MISMATCH"]
    assert result.source_comparison.verdict == Verdict.FAIL
