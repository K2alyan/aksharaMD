"""Allowlist projection — the ONLY code path the smoke review reads."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.allowlist import (
    INSPECTABLE_EXECUTION_RECORD_FIELDS,
    PROHIBITED_FIELDS,
    ProhibitedFieldError,
    project_execution_record,
    project_pair,
    project_reviewer_artifact,
)


def test_allowlist_matches_smoke_spec_json_declaration() -> None:
    """The module-level frozensets are the enforcement source of
    truth; the JSON declares them. Any drift between the two is
    a harness misconfiguration."""
    spec = json.loads(Path("benchmarks/eval_v1/config/smoke_spec_v1.json").read_bytes())
    ra = spec["review_allowlist"]
    declared_exec = set(ra["inspectable_execution_record_fields"])
    assert declared_exec == set(INSPECTABLE_EXECUTION_RECORD_FIELDS), (
        f"drift: only in module={set(INSPECTABLE_EXECUTION_RECORD_FIELDS) - declared_exec}, "
        f"only in json={declared_exec - set(INSPECTABLE_EXECUTION_RECORD_FIELDS)}"
    )


def test_projection_returns_only_allowlisted() -> None:
    record = {name: "value" for name in INSPECTABLE_EXECUTION_RECORD_FIELDS}
    # No prohibited field: projection returns all inspectable fields.
    projected = project_execution_record(record)
    assert set(projected.keys()) == INSPECTABLE_EXECUTION_RECORD_FIELDS


def test_projection_drops_extra_non_prohibited_keys() -> None:
    record = {name: "value" for name in INSPECTABLE_EXECUTION_RECORD_FIELDS}
    record["some_neutral_extra_field"] = "leak?"
    projected = project_execution_record(record)
    assert "some_neutral_extra_field" not in projected


def test_projection_raises_on_prohibited_field_present() -> None:
    record = {name: "value" for name in INSPECTABLE_EXECUTION_RECORD_FIELDS}
    record["aksharamd_readiness_score"] = 42
    with pytest.raises(ProhibitedFieldError, match="aksharamd_readiness_score"):
        project_execution_record(record)


@pytest.mark.parametrize("prohibited", sorted(PROHIBITED_FIELDS))
def test_every_prohibited_field_is_rejected(prohibited: str) -> None:
    record = {name: "value" for name in INSPECTABLE_EXECUTION_RECORD_FIELDS}
    record[prohibited] = "leak"
    with pytest.raises(ProhibitedFieldError, match=prohibited):
        project_execution_record(record)


def test_reviewer_artifact_projection_replaces_questions_with_bool() -> None:
    art = {
        "pair_id": "abc",
        "blinded_parser_hash": "0" * 16,
        "source_pdf_path": "/x",
        "extraction_markdown_path": "/y",
        "normalized_markdown_path": "/z",
        "mapping_reference": "appendix_b_v1::v1",
        "questions": {"Q1_coverage": "...", "Q2_fidelity": "...", "Q3_downstream_usability": "..."},
        "provenance": {"authorization": "smoke"},
    }
    projected = project_reviewer_artifact(art)
    assert "questions" not in projected
    assert projected["questions_populated"] is True
    assert "provenance" not in projected  # not on the inspectable set


def test_reviewer_artifact_projection_refuses_label_leak() -> None:
    art = {
        "pair_id": "abc",
        "q1_answer": "yes",  # leaked!
    }
    with pytest.raises(ProhibitedFieldError, match="q1_answer"):
        project_reviewer_artifact(art)


def test_project_pair_combines_both() -> None:
    record = {name: "value" for name in INSPECTABLE_EXECUTION_RECORD_FIELDS}
    art = {
        "pair_id": "abc",
        "blinded_parser_hash": "0" * 16,
        "source_pdf_path": "/x",
        "extraction_markdown_path": "/y",
        "normalized_markdown_path": "/z",
        "mapping_reference": "appendix_b_v1::v1",
        "questions": {"Q1_coverage": "..."},
    }
    combined = project_pair(execution_record=record, reviewer_artifact=art)
    assert set(combined.keys()) == {"execution_record", "reviewer_artifact"}
    assert combined["reviewer_artifact"]["questions_populated"] is True
