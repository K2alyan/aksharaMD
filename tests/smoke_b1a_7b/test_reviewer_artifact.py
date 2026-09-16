"""Reviewer-artifact construction + blinding invariants."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.reviewer_artifact import (
    ReviewerArtifactBlindingError,
    build_smoke_reviewer_artifact,
)


def _fake_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    src = tmp_path / "source.pdf"
    src.write_bytes(b"synthetic")
    raw = tmp_path / "raw_output.md"
    raw.write_text("# fake\n", encoding="utf-8")
    norm = tmp_path / "normalized_output.md"
    norm.write_text("# fake\n", encoding="utf-8")
    return src, raw, norm


def test_happy_path_returns_expected_fields(tmp_path: Path) -> None:
    src, raw, norm = _fake_paths(tmp_path)
    art = build_smoke_reviewer_artifact(
        canonical_id="SYN-PMC-1",
        parser_id="aksharamd-reference",
        source_pdf_path=src,
        extraction_markdown_path=raw,
        normalized_markdown_path=norm,
    )
    assert set(art.keys()) >= {
        "pair_id", "blinded_parser_hash", "source_pdf_path",
        "extraction_markdown_path", "normalized_markdown_path",
        "questions", "mapping_reference", "provenance",
    }
    assert len(art["blinded_parser_hash"]) == 16
    assert set(art["questions"].keys()) == {
        "Q1_coverage", "Q2_fidelity", "Q3_downstream_usability",
    }
    assert art["mapping_reference"] == "appendix_b_v1::v1"


def test_no_label_answers_present(tmp_path: Path) -> None:
    src, raw, norm = _fake_paths(tmp_path)
    art = build_smoke_reviewer_artifact(
        canonical_id="SYN-DL-1",
        parser_id="docling",
        source_pdf_path=src,
        extraction_markdown_path=raw,
        normalized_markdown_path=norm,
    )
    for k in ("q1_answer", "q2_answer", "q3_answer",
              "severity_label", "derived_label"):
        assert k not in art


def test_blinded_hash_differs_across_parsers(tmp_path: Path) -> None:
    src, raw, norm = _fake_paths(tmp_path)
    a = build_smoke_reviewer_artifact(
        canonical_id="SYN-PMC-1", parser_id="marker",
        source_pdf_path=src, extraction_markdown_path=raw,
        normalized_markdown_path=norm,
    )
    b = build_smoke_reviewer_artifact(
        canonical_id="SYN-PMC-1", parser_id="docling",
        source_pdf_path=src, extraction_markdown_path=raw,
        normalized_markdown_path=norm,
    )
    assert a["blinded_parser_hash"] != b["blinded_parser_hash"]


def test_leakage_of_parser_identity_via_provenance_raises(tmp_path: Path) -> None:
    src, raw, norm = _fake_paths(tmp_path)
    # Malicious caller tries to smuggle parser identity into provenance.
    with pytest.raises(ReviewerArtifactBlindingError, match="parser-identity"):
        build_smoke_reviewer_artifact(
            canonical_id="SYN-PMC-1",
            parser_id="marker",
            source_pdf_path=src,
            extraction_markdown_path=raw,
            normalized_markdown_path=norm,
            extra_provenance={"leak_parser": "docling"},
        )
