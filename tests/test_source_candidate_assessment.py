from __future__ import annotations

import hashlib

import pymupdf
import pytest
from pydantic import ValidationError

from aksharamd.assessment import (
    CandidateArtifact,
    DetectorStatus,
    SourceArtifact,
    Verdict,
    assess_source_candidate,
)
from aksharamd.assessment import source_candidate as source_candidate_module


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
    # Source identity is provenance evidence, not a preservation-quality vote.
    assert result.source_comparison.verdict == Verdict.PASS


@pytest.mark.parametrize("field,value", [
    ("data", b"tampered"),
    ("byte_size", 1),
    ("content_hash", "0" * 64),
])
def test_assessment_boundary_revalidates_mutated_artifact_identity(field, value):
    pdf_bytes, table_markdown = _table_pdf()
    source = _source(pdf_bytes)
    setattr(source, field, value)

    with pytest.raises(ValueError, match=r"source\.(data|byte_size|content_hash)"):
        assess_source_candidate(source=source, candidate=_candidate(table_markdown))


def test_assessment_boundary_revalidates_mutated_candidate_identity():
    pdf_bytes, table_markdown = _table_pdf()
    candidate = _candidate(table_markdown)
    candidate.content_hash = "f" * 64

    with pytest.raises(ValueError, match="candidate.content_hash"):
        assess_source_candidate(source=_source(pdf_bytes), candidate=candidate)


def test_receipt_models_are_frozen_and_reject_spoofed_contract_identifiers():
    pdf_bytes, first, second = _text_pdf()
    result = assess_source_candidate(
        source=_source(pdf_bytes), candidate=_candidate(f"{first}\n{second}"),
    )
    with pytest.raises(ValidationError, match="frozen"):
        result.policy_id = "replacement-policy"

    payload = result.model_dump(mode="python")
    payload["schema_version"] = "999"
    with pytest.raises(ValidationError, match="2.0-exploratory"):
        type(result).model_validate(payload)

    payload = result.model_dump(mode="python")
    payload["provenance"]["source_hash"] = "not-a-hash"
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        type(result).model_validate(payload)


def test_unrelated_table_with_equal_count_does_not_pass():
    pdf_bytes, _ = _table_pdf()
    unrelated = "\n".join([
        "| Product | Units | Owner |",
        "| --- | ---: | --- |",
        "| Widget | 400 | Alice |",
        "| Gear | 700 | Bob |",
    ])
    result = assess_source_candidate(source=_source(pdf_bytes), candidate=_candidate(unrelated))
    detector = _detector(result, "source.pdf_table_structure_retention")

    assert detector.raw_evidence["source_table_count"] == 1
    assert detector.raw_evidence["candidate_markdown_table_count"] == 1
    assert detector.raw_evidence["matched_source_table_count"] == 0
    assert detector.verdict == Verdict.FAIL


def test_duplicated_matching_table_is_a_concern_not_a_pass():
    pdf_bytes, table_markdown = _table_pdf()
    result = assess_source_candidate(
        source=_source(pdf_bytes), candidate=_candidate(f"{table_markdown}\n\n{table_markdown}"),
    )
    detector = _detector(result, "source.pdf_table_structure_retention")

    assert detector.verdict == Verdict.CONCERN
    assert detector.score == 50
    assert detector.raw_evidence["matched_source_table_count"] == 1
    assert detector.raw_evidence["unmatched_candidate_table_count"] == 1
    assert detector.finding_codes == ["CANDIDATE_EXTRA_TABLE_STRUCTURE"]


def test_source_byte_limit_has_stable_explicit_abstentions(monkeypatch):
    pdf_bytes, table_markdown = _table_pdf()
    source = _source(pdf_bytes)
    monkeypatch.setattr(source_candidate_module, "MAX_SOURCE_BYTES", 10)
    result = assess_source_candidate(
        source=source, candidate=_candidate(table_markdown, source_hash=source.content_hash),
    )

    text = _detector(result, "source.pdf_text_token_retention")
    table = _detector(result, "source.pdf_table_structure_retention")
    assert text.status == table.status == DetectorStatus.ABSTAINED
    assert text.abstention_reason == table.abstention_reason == "source byte size exceeds limit 10"
    assert _detector(result, "source.candidate_identity_binding").verdict == Verdict.PASS
    assert result.source_comparison.verdict == Verdict.UNDETERMINED


def test_page_and_token_limits_abstain_instead_of_partial_scoring(monkeypatch):
    pdf_bytes, first, second = _text_pdf()
    source = _source(pdf_bytes)
    candidate = _candidate(f"{first}\n{second}", source_hash=source.content_hash)
    monkeypatch.setattr(source_candidate_module, "MAX_PDF_PAGES", 0)
    page_limited = assess_source_candidate(source=source, candidate=candidate)
    assert "page count 1 exceeds limit 0" in _detector(
        page_limited, "source.pdf_text_token_retention",
    ).abstention_reason
    assert page_limited.source_comparison.verdict == Verdict.UNDETERMINED

    monkeypatch.setattr(source_candidate_module, "MAX_PDF_PAGES", 500)
    monkeypatch.setattr(source_candidate_module, "MAX_SOURCE_TOKENS", 10)
    token_limited = assess_source_candidate(source=source, candidate=candidate)
    assert _detector(token_limited, "source.pdf_text_token_retention").abstention_reason == (
        "PDF source token count exceeds limit 10"
    )
    assert token_limited.source_comparison.verdict == Verdict.UNDETERMINED


def test_text_character_limit_does_not_disable_table_detector(monkeypatch):
    pdf_bytes, table_markdown = _table_pdf()
    source = _source(pdf_bytes)
    monkeypatch.setattr(source_candidate_module, "MAX_EXTRACTED_CHARS", 5)
    result = assess_source_candidate(
        source=source, candidate=_candidate(table_markdown, source_hash=source.content_hash),
    )

    text = _detector(result, "source.pdf_text_token_retention")
    table = _detector(result, "source.pdf_table_structure_retention")
    assert text.status == DetectorStatus.ABSTAINED
    assert text.abstention_reason == "PDF extracted characters exceed limit 5"
    assert table.status == DetectorStatus.ACTIVATED
    assert table.verdict == Verdict.PASS
    assert result.source_comparison.verdict == Verdict.UNDETERMINED


def test_table_geometry_limit_does_not_disable_text_detector(monkeypatch):
    pdf_bytes, first, second = _text_pdf()
    source = _source(pdf_bytes)
    monkeypatch.setattr(source_candidate_module, "MAX_TABLE_GEOMETRY_PAGES", 0)
    result = assess_source_candidate(
        source=source, candidate=_candidate(f"{first}\n{second}", source_hash=source.content_hash),
    )

    text = _detector(result, "source.pdf_text_token_retention")
    table = _detector(result, "source.pdf_table_structure_retention")
    assert text.status == DetectorStatus.ACTIVATED
    assert text.verdict == Verdict.PASS
    assert table.status == DetectorStatus.ABSTAINED
    assert table.abstention_reason == "PDF page count 1 exceeds table geometry limit 0"
    assert result.source_comparison.verdict == Verdict.UNDETERMINED


def test_table_page_failure_is_isolated_from_text_evidence(monkeypatch):
    pdf_bytes, first, second = _text_pdf()
    source = _source(pdf_bytes)
    monkeypatch.setattr(source_candidate_module, "MAX_DRAWING_COMMANDS", -1)
    result = assess_source_candidate(
        source=source, candidate=_candidate(f"{first}\n{second}", source_hash=source.content_hash),
    )

    text = _detector(result, "source.pdf_text_token_retention")
    table = _detector(result, "source.pdf_table_structure_retention")
    assert text.status == DetectorStatus.ACTIVATED
    assert table.status == DetectorStatus.ABSTAINED
    assert table.abstention_reason == (
        "PDF table inspection failed on page 1: drawing command count exceeds limit -1"
    )
    assert result.source_comparison.verdict == Verdict.UNDETERMINED


def test_markdown_table_formatting_is_ignored_for_visible_cell_signatures():
    pdf_bytes, _ = _table_pdf()
    formatted = "\n".join([
        "| **Region** | [Revenue](https://example.test) | `Margin` |",
        "| --- | ---: | ---: |",
        "| North | **120** | 18 *percent* |",
        "| South | `95` | [14 percent](https://example.test/value) |",
    ])
    result = assess_source_candidate(source=_source(pdf_bytes), candidate=_candidate(formatted))
    detector = _detector(result, "source.pdf_table_structure_retention")

    assert detector.verdict == Verdict.PASS
    assert detector.raw_evidence["matched_source_table_count"] == 1


def test_markdown_escapes_and_wrappers_have_equivalent_visible_signatures():
    plain = "| Label | Value |\n| --- | --- |\n| Symbol | A \\| B |"
    formatted = "| **Label** | `Value` |\n| --- | --- |\n| [Symbol](https://example.test) | A \\| **B** |"

    plain_signatures, plain_error = source_candidate_module._markdown_table_signatures(plain)
    formatted_signatures, formatted_error = source_candidate_module._markdown_table_signatures(formatted)

    assert plain_error is formatted_error is None
    assert plain_signatures == formatted_signatures


@pytest.mark.parametrize("line_break", ["<br>", "<br/>", "<br />", "<BR />"])
def test_html_line_breaks_are_visible_whitespace_in_table_signatures(line_break):
    plain = "| Location |\n| --- |\n| North East |"
    with_break = f"| Location |\n| --- |\n| North{line_break}East |"

    plain_signatures, plain_error = source_candidate_module._markdown_table_signatures(plain)
    break_signatures, break_error = source_candidate_module._markdown_table_signatures(with_break)

    assert plain_error is break_error is None
    assert plain_signatures == break_signatures


def test_nonseparating_html_wrappers_remain_representation_only():
    plain = "| Location |\n| --- |\n| North East |"
    wrapped = "| <strong>Location</strong> |\n| --- |\n| North <em>East</em> |"

    plain_signatures, plain_error = source_candidate_module._markdown_table_signatures(plain)
    wrapped_signatures, wrapped_error = source_candidate_module._markdown_table_signatures(wrapped)

    assert plain_error is wrapped_error is None
    assert plain_signatures == wrapped_signatures


def test_matching_identity_cannot_turn_candidate_decode_abstentions_into_quality_pass():
    pdf_bytes, _, _ = _text_pdf()
    source = _source(pdf_bytes)
    data = b"\xff"
    candidate = CandidateArtifact(
        content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data),
        media_type="text/markdown", data=data, original_source_hash=source.content_hash,
    )
    result = assess_source_candidate(source=source, candidate=candidate)

    assert _detector(result, "source.candidate_identity_binding").verdict == Verdict.PASS
    assert all(
        detector.status == DetectorStatus.ABSTAINED
        for detector in result.source_comparison.detectors
        if detector.quality_role == "substantive"
    )
    assert result.source_comparison.verdict == Verdict.UNDETERMINED
