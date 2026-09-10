"""Delivery gates must assess and activate the bytes they actually deliver."""
import hashlib
import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from aksharamd.assessment import Assessor, CandidateArtifact, SourceArtifact, TaskProfile
from aksharamd.cli import main
from aksharamd.index import worker
from aksharamd.index.config import IndexConfig
from aksharamd.index.queue import IndexQueue
from aksharamd.plugins import registry
from aksharamd.plugins.base import ExporterPlugin


def _artifact(kind, text):
    data = text.encode()
    return kind(content_hash=hashlib.sha256(data).hexdigest(), byte_size=len(data),
                media_type="text/markdown", data=data)


@pytest.mark.parametrize("change", ["candidate", "source", "provenance"])
def test_gate_rechecks_identity_after_late_exporter(tmp_path, monkeypatch, change):
    source = tmp_path / "source.md"
    source.write_text("The claim is not approved.", encoding="utf-8")

    class LateExporter(ExporterPlugin):
        name = "late-test"
        priority = 93

        def execute(self, ctx):
            from pathlib import Path
            if change == "candidate":
                (Path(ctx.output_dir) / "document.md").write_text("The claim is approved.", encoding="utf-8")
            elif change == "source":
                source.write_text("The claim is approved.", encoding="utf-8")
            else:
                path = Path(ctx.output_dir) / "quality_assessment.json"
                report = json.loads(path.read_text(encoding="utf-8"))
                report["candidate"]["original_source_hash"] = "0" * 64
                path.write_text(json.dumps(report), encoding="utf-8")
            return ctx

    monkeypatch.setattr(registry, "_plugin_classes", [*registry._plugin_classes, LateExporter])
    monkeypatch.setattr(registry, "_plugin_cache", {})
    result = CliRunner().invoke(main, ["compile", str(source), "-o", str(tmp_path / "out"),
                                      "--json", "--require-assessment-accept"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert not payload["assessment_gate"]["activated"]
    assert payload["assessment_gate"]["activation_error"]
    assert not (tmp_path / "out" / "source").exists()


def test_successful_gate_paths_resolve_after_activation(tmp_path):
    from pathlib import Path
    source = tmp_path / "source.md"
    source.write_text("The shipment is ready.", encoding="utf-8")
    result = CliRunner().invoke(main, ["compile", str(source), "-o", str(tmp_path / "out"),
                                      "--json", "--require-assessment-accept"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    active = Path(payload["output_dir"])
    assert active == Path(payload["assessment_gate"]["final_output_dir"])
    assert active.is_dir()
    report = json.loads((active / "quality_assessment.json").read_text(encoding="utf-8"))
    assert (active / report["candidate"]["storage_reference"]).read_text() == source.read_text()


@pytest.mark.parametrize("extra", [
    {"schema_version": "2.0"}, {"required_regex": "must match"},
    {"required_relationships": [{"first_literal": "A", "second_literal": "B", "relation": "same row"}]},
])
def test_unsupported_task_requirements_are_rejected(extra):
    with pytest.raises(ValidationError):
        TaskProfile.model_validate({"purpose": "test", "required_literals": ["A"], **extra})


@pytest.mark.parametrize("source", [
    "> The claim is approved.",
    "# Status\n\n> The claim is approved.",
    "```python\nif ready:\n    ship()\n```",
])
def test_gated_index_assesses_exact_rendered_embedding_payload(tmp_path, monkeypatch, source):
    path = tmp_path / "source.md"
    path.write_text(source, encoding="utf-8")
    queue = IndexQueue(tmp_path / "queue.db")
    queue.enqueue(str(path))
    queue.dequeue()
    store, embedder = MagicMock(), MagicMock()
    store.add_chunks.return_value = 1
    embedder.embed.return_value = [[0.0]]
    observed = []
    real_assess = worker._source_grounded_assessment

    def capture(path, candidate, ctx):
        observed.append(candidate)
        return real_assess(path, candidate, ctx)

    monkeypatch.setattr(worker, "_source_grounded_assessment", capture)
    worker.process_file(str(path), queue, store, embedder,
                        IndexConfig(index_dir=tmp_path, min_readiness_score=0, require_assessment_accept=True))
    if "```" in source and observed != [source]:
        # A renderer that changes literal layout must abstain. A renderer that
        # preserves it exactly must deliver those same bytes (assertions below).
        assert len(queue.list_all(status="low_quality")) == 1
        assert observed == ["```python\nif ready:\n    ship()\n\n```"]
        embedder.embed.assert_not_called()
        store.add_chunks.assert_not_called()
        return
    assert len(queue.list_all(status="done")) == 1
    texts = embedder.embed.call_args.args[0]
    assert observed == ["\n\n".join(texts)]
    assert store.add_chunks.call_args.args[1] == texts
    if ">" in source:
        assert any(text.startswith("> ") for text in texts)
    if "```" in source:
        assert texts == [source]


@pytest.mark.parametrize("text,closed", [
    ("````python\n```\n````", True),
    ("~~~python\n```\n~~~", True),
    ("```\nbody\n`````", True),
    ("````\nbody\n```", False),
    ("~~~\nbody", False),
    ("```", False),
    ("paragraph\n```\nbody", False),
    ("paragraph\n```\nbody\n```", True),
    ("~~~\nbody\u2028more\n~~~", True),
    ("> ````\n> ```\n> ````", True),
    ("- ````\n  ```\n  ````", True),
    ("> ```\n> body\nplain", False),
    ("- ```\n  body\n\nnext", False),
    ("~~~\n\n\n~~~\n", True),
    ("~~~\nbody\n    ~~~", False),
    ("```\n&grave;&grave;&grave;\n```", True),
])
def test_v2_fence_boundaries_follow_commonmark(text, closed):
    result = Assessor().assess(source=_artifact(SourceArtifact, text), candidate=_artifact(CandidateArtifact, text))
    assert (result.disposition == "ACCEPT") is closed
    assert result.dimensions["structural_usability"].evidence[0].check_version == "2"


@pytest.mark.parametrize("policy", ["general-ingestion-v1", "source-text-preservation-v1"])
def test_historical_fence_policy_is_unchanged(policy):
    text = "````\n```\n````"
    result = Assessor().assess(source=_artifact(SourceArtifact, text), candidate=_artifact(CandidateArtifact, text),
                               policy_id=policy)
    assert result.disposition == "HOLD"
    assert result.dimensions["structural_usability"].evidence[0].check_version == "1"


@pytest.mark.parametrize("invalid", [
    {"schema_version": "999"},
    {"required_regex": "approved"},
    {"required_relationships": [{"first_literal": "A", "second_literal": "B", "relation": "same row"}]},
])
def test_cli_rejects_unsupported_profile_before_compilation(tmp_path, invalid):
    source = tmp_path / "source.md"
    source.write_text("A B", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"purpose": "task", "required_literals": ["A"], **invalid}), encoding="utf-8")
    result = CliRunner().invoke(main, ["compile", str(source), "-o", str(tmp_path / "out"),
                                      "--require-assessment-accept", "--task-profile", str(profile)])
    assert result.exit_code != 0
    assert "Invalid task profile" in result.output
    assert not (tmp_path / "out").exists()


def test_rich_output_paths_resolve_after_activation(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    Path("source.md").write_text("The shipment is ready.", encoding="utf-8")
    result = CliRunner().invoke(main, ["compile", "source.md", "-o", "out", "--require-assessment-accept"])
    assert result.exit_code == 0, result.output
    active = Path("out") / "source"
    assert active.is_dir()
    assert f"{active}/document.md" in result.output
    assert ".source.staging-" not in result.output
