"""Default gate safety, replay compatibility, and adapter policy consistency."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from aksharamd.assessment import (
    DEFAULT_ASSESSMENT_POLICY_ID,
    Assessor,
    CandidateArtifact,
    SourceArtifact,
)
from aksharamd.assessment.compiler_binding import save_compiled_assessment
from aksharamd.cli import main
from aksharamd.compiler import Compiler
from aksharamd.index.worker import _source_grounded_assessment


def _artifact(cls, text, media_type="text/plain", **kwargs):
    data = text.encode()
    return cls(content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data),
               media_type=media_type, data=data, **kwargs)


HARMFUL = [
    ("The shipment contains fragile glassware.", "[Content unavailable]"),
    ("The claim is not approved.", "The claim is approved."),
    ("Alice: 100\nBob: 200", "Alice: 200\nBob: 100"),
    ("The shipment is ready. Handle with care.", "The shipment is ready."),
]


@pytest.mark.parametrize("source,candidate", HARMFUL)
def test_default_cli_blocks_harmful_conversion_but_explicit_v1_replays(tmp_path, source, candidate):
    source_path, candidate_path = tmp_path / "source.txt", tmp_path / "candidate.md"
    source_path.write_text(source, encoding="utf-8")
    candidate_path.write_text(candidate, encoding="utf-8")
    args = ["assess", str(candidate_path), "--source", str(source_path), "--json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 2, result.output
    report = json.loads(result.output)
    assert report["policy_id"] == DEFAULT_ASSESSMENT_POLICY_ID
    assert report["disposition"] == "ABSTAIN"
    replay = CliRunner().invoke(main, args + ["--policy", "general-ingestion-v1"])
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output)["policy_id"] == "general-ingestion-v1"


@pytest.mark.parametrize("source,candidate", [
    ("A simple sentence.", "A simple\nsentence."),
    ("Alice: 100\nBob: 200", "Bob: 200\nAlice: 100"),
    ("[Content unavailable]", "[Content unavailable]"),
])
def test_default_accepts_bounded_textual_controls_without_claiming_substance(source, candidate):
    result = Assessor().assess(source=_artifact(SourceArtifact, source),
                               candidate=_artifact(CandidateArtifact, candidate))
    assert result.policy_id == DEFAULT_ASSESSMENT_POLICY_ID
    assert result.disposition == "ACCEPT"
    evidence = result.dimensions["conversion_fidelity"].evidence[-1]
    assert evidence.details["semantic_fidelity_established"] is False


@pytest.mark.parametrize("media_type", ["application/pdf", "text/html", "application/json"])
def test_unsupported_source_evidence_abstains(media_type):
    result = Assessor().assess(source=_artifact(SourceArtifact, "unchanged", media_type),
                               candidate=_artifact(CandidateArtifact, "unchanged"))
    assert result.disposition == "ABSTAIN"


@pytest.mark.parametrize("source", [None, _artifact(SourceArtifact, "binary", "application/pdf")])
def test_declared_truncation_holds_even_without_supported_source(source):
    result = Assessor().assess(source=source,
        candidate=_artifact(CandidateArtifact, "preview", declared_truncated=True))
    assert result.disposition == "HOLD"
    assert result.dimensions["conversion_fidelity"].findings[0].code == "CANDIDATE_DECLARED_TRUNCATED"


@pytest.mark.parametrize("alias", ["truncated", "declared_truncated"])
def test_compiler_binding_and_index_honor_both_truncation_aliases(tmp_path, alias):
    source = tmp_path / "source.txt"
    source.write_text("Complete text.", encoding="utf-8")
    output = tmp_path / "out"
    ctx = Compiler(output_dir=str(output)).compile(str(source))
    report = json.loads((output / "quality_assessment.json").read_text(encoding="utf-8"))
    assert report["assessment"]["policy_id"] == DEFAULT_ASSESSMENT_POLICY_ID
    assert report["assessment"]["disposition"] == "ACCEPT"
    ctx.document.metadata = {"truncated": False, "declared_truncated": False, alias: True}
    path = save_compiled_assessment(ctx)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["assessment"]["disposition"] == "HOLD"
    result, error = _source_grounded_assessment(str(source), "Complete text.", ctx)
    assert error is None
    assert result.policy_id == DEFAULT_ASSESSMENT_POLICY_ID
    assert result.disposition == "HOLD"


@pytest.mark.parametrize("source,candidate", HARMFUL)
def test_index_assessment_uses_conservative_default(tmp_path, source, candidate):
    path = tmp_path / "source.txt"
    path.write_text(source, encoding="utf-8")
    ctx = SimpleNamespace(source_id="source", capture_id=None,
                          manifest=SimpleNamespace(document_id="candidate"),
                          document=SimpleNamespace(metadata={}))
    result, error = _source_grounded_assessment(str(path), candidate, ctx)
    assert error is None
    assert result.policy_id == DEFAULT_ASSESSMENT_POLICY_ID
    assert result.disposition == "ABSTAIN"
