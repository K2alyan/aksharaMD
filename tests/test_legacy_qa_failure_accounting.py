from __future__ import annotations

import json

import pytest
import yaml

from benchmarks import llm_qa_eval as qa


@pytest.mark.parametrize("response", [
    qa.LLMResponse("", error="rate limit"), qa.LLMResponse(""),
    qa.LLMResponse("10 because correct"), qa.LLMResponse("11"), qa.LLMResponse("-1"),
])
def test_judge_operational_or_malformed_response_is_unscored(monkeypatch, response):
    monkeypatch.setattr(qa, "_call_claude", lambda *a, **k: response)
    result = qa._judge("Amount?", "100", "100")
    assert result.score == -1
    assert result.error


def test_bracketed_correct_answer_is_judged(monkeypatch):
    monkeypatch.setattr(qa, "_call_claude", lambda *a, **k: qa.LLMResponse("10"))
    assert qa._judge("List?", "[1, 2]", "[1, 2]").score == 10


@pytest.fixture
def cli(monkeypatch, tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Amount 100", encoding="utf-8")
    output = tmp_path / "results.json"
    monkeypatch.setattr(qa, "_CONVERTERS", {"aksharamd": lambda p: ("Amount 100", 0.0)})
    monkeypatch.setattr(qa, "_convert_aksharamd", lambda p: ("Amount 100", 0.0))
    monkeypatch.setattr(qa, "_LLM_FNS", {"openai": lambda *a, **k: qa.LLMResponse("100")})
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    monkeypatch.setattr(qa, "_print_cost_summary", lambda *a: None)

    def run(extra, pairs=None, provenance=None):
        if pairs is not None:
            entry = {"path": str(source), "qa": pairs}
            if provenance:
                entry["qa_provenance"] = provenance
            config = tmp_path / "questions.yaml"
            config.write_text(yaml.safe_dump({"documents": [entry]}), encoding="utf-8")
            docs = ["--qa", str(config)]
        else:
            docs = [str(source)]
        monkeypatch.setattr("sys.argv", ["qa", *docs, "--tools", "aksharamd",
                                       "--llms", "openai", "--out", str(output), *extra])
        qa.main()
        return json.loads(output.read_text(encoding="utf-8"))
    return run


def test_cli_retains_failed_judgments_and_uses_scored_denominator(monkeypatch, cli, capsys):
    responses = iter([qa.LLMResponse("", error="rate limit"),
                      qa.LLMResponse("malformed"), qa.LLMResponse("10"), qa.LLMResponse("0")])
    monkeypatch.setattr(qa, "_call_claude", lambda *a, **k: next(responses))
    result = cli([], [{"q": f"Amount {i}?", "a": "100"} for i in range(4)])
    rows = result["results"]
    assert [r["score"] for r in rows] == [-1, -1, 10, 0]
    assert [r["status"] for r in rows] == ["judge_error", "judge_error", "scored", "scored"]
    assert rows[0]["answer"] == "100" and rows[0]["judge_error"] == "rate limit"
    assert result["quality_summary"] == {"attempted": 4, "scored": 2, "unscored": 2,
                                          "operational_failures": 2, "mean_score": 5.0}
    assert "operational failures: 2" in capsys.readouterr().out


@pytest.mark.parametrize("pairs", [None, []])
def test_candidate_generation_requires_optin_before_api(monkeypatch, cli, pairs):
    def forbidden(*a, **k):
        pytest.fail("API or conversion called before diagnostic guard")
    monkeypatch.setattr(qa, "_call_claude", forbidden)
    monkeypatch.setattr(qa, "_convert_aksharamd", forbidden)
    with pytest.raises(SystemExit) as exc:
        cli([], pairs)
    assert exc.value.code == 2


def test_no_llm_needs_no_questions_or_api(monkeypatch, cli):
    monkeypatch.setattr(qa, "_call_claude", lambda *a, **k: pytest.fail("API called"))
    result = cli(["--no-llm"])
    assert result["results"] == []
    assert result["quality_summary"]["scored"] == 0


def test_diagnostic_provenance_saved_and_reuse_requires_optin(monkeypatch, cli, tmp_path):
    responses = iter([qa.LLMResponse('qa: [{q: "Amount?", a: "100"}]'), qa.LLMResponse("10")])
    monkeypatch.setattr(qa, "_call_claude", lambda *a, **k: next(responses))
    saved = tmp_path / "saved.yaml"
    result = cli(["--allow-candidate-derived-qa", "--save-qa", str(saved)])
    assert result["config"]["diagnostic"]
    assert result["results"][0]["qa_provenance"] == "candidate_derived_diagnostic"
    entry = yaml.safe_load(saved.read_text())["documents"][0]
    assert entry["qa_provenance"] == "candidate_derived_diagnostic"
    with pytest.raises(SystemExit):
        cli([], entry["qa"], provenance=entry["qa_provenance"])


def test_conversion_exception_retained_without_answer_api(monkeypatch, cli):
    def fail(p):
        raise ValueError("broken parser")
    monkeypatch.setattr(qa, "_CONVERTERS", {"aksharamd": fail})
    monkeypatch.setattr(qa, "_LLM_FNS", {"openai": lambda *a, **k: pytest.fail("answer API called")})
    result = cli([], [{"q": "Amount?", "a": "100"}])
    assert result["results"][0]["status"] == "conversion_error"
    assert "broken parser" in result["results"][0]["error"]
    assert result["quality_summary"]["mean_score"] is None


@pytest.mark.parametrize(("answer_error", "expected", "status"), [
    ("answer rate limit", "100", "answer_error"), ("", "", "missing_reference"),
])
def test_answer_failure_and_missing_reference_are_distinct(monkeypatch, cli, answer_error, expected, status):
    monkeypatch.setattr(qa, "_LLM_FNS", {"openai": lambda *a, **k: qa.LLMResponse("100", error=answer_error)})
    monkeypatch.setattr(qa, "_call_claude", lambda *a, **k: pytest.fail("judge API called"))
    result = cli([], [{"q": "Amount?", "a": expected}])
    assert result["results"][0]["status"] == status
    assert result["results"][0]["score"] == -1
    assert result["quality_summary"]["operational_failures"] == bool(answer_error)
    assert result["quality_by_tool_llm"][0]["scored"] == 0
    assert result["quality_by_tool_llm"][0]["mean_score"] is None
