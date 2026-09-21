from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from benchmarks.eval_v1.stage2 import track_c_v2_diagnostics as diagnostics_module
from benchmarks.eval_v1.stage2.track_c_v2_diagnostics import (
    DIAGNOSTIC_CONTRACT_ID,
    build_report,
    execute,
    main,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pdf(path: Path, words: str) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(36, 36, 560, 800), words, fontsize=10)
    document.save(path)
    document.close()


def _write_pair(
    run_dir: Path,
    *,
    corpus: str,
    canonical_id: str,
    parser_id: str,
    markdown: str,
    primary_score: float,
) -> Path:
    pair_dir = run_dir / corpus / canonical_id / parser_id
    pair_dir.mkdir(parents=True)
    candidate_path = pair_dir / "parser_output.md"
    candidate_path.write_text(markdown, encoding="utf-8")
    candidate_data = candidate_path.read_bytes()
    prediction = "cached answer"
    result = {
        "schema_version": "3",
        "metric_schema_version": "3",
        "scoring_contract_id": "markdown_only_v1",
        "corpus": corpus,
        "canonical_id": canonical_id,
        "parser_id": parser_id,
        "execution_status": "EXECUTED",
        "n_qa_pairs": 1,
        "n_answered": 1,
        "n_llm_errors": 0,
        "em_score": 0.0,
        "primary_score": primary_score,
        "primary_metric": "token_f1",
        "readiness_score": 95,
        "warning_codes": [],
        "prompt_sha256": "2bf3f2511d9346adfe626018b64888a0e84de6ff97ccfdbd1a28604f8954b601",
        "model": "claude-sonnet-4-6",
        "input_sha256": _sha(candidate_path.read_text(encoding="utf-8").encode("utf-8")),
        "llm_evaluated": True,
        "qa_results": [{
            "question_id": 0,
            "status": "answered",
            "prediction": prediction,
            "prediction_sha256": _sha(prediction.encode()),
            "em_score": 0.0,
            "primary_score": primary_score,
        }],
    }
    result_path = pair_dir / "track_c_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    if parser_id != "corpus_gold":
        execution = {
            "corpus": corpus,
            "canonical_id": canonical_id,
            "parser_id": parser_id,
            "exit_status": "EXECUTED",
            "output_bytes": len(candidate_data),
            "output_sha256": _sha(candidate_data),
            "stage1_execution_manifest_sha256": (
                "4b5116f4a965f2b4e653705827d3f0a4115b2b4b4e3a1b2af4f7e234bd26b3db"
            ),
            "parser_execution_contract_config_sha256": (
                "a0ca496e562cee200c393c146c7ed16efee9b01ee2f94ebdd4e623c936f9baa4"
            ),
        }
        (pair_dir / "execution_record.json").write_text(json.dumps(execution), encoding="utf-8")
    return result_path


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_dir = tmp_path / "v1"
    cache_root = tmp_path / "cache"
    qasper = cache_root / "qasper"
    qasper.mkdir(parents=True)
    words = " ".join(f"sourceword{index}" for index in range(40))
    _pdf(qasper / "doc-a-123456789abc.pdf", words)
    _pdf(qasper / "doc-b-123456789abc.pdf", words)
    _write_pair(
        run_dir, corpus="qasper", canonical_id="doc-a", parser_id="marker",
        markdown=words, primary_score=0.9,
    )
    _write_pair(
        run_dir, corpus="qasper", canonical_id="doc-a", parser_id="docling",
        markdown=" ".join(words.split()[:20]), primary_score=0.4,
    )
    _write_pair(
        run_dir, corpus="qasper", canonical_id="doc-b", parser_id="marker",
        markdown=" ".join(words.split()[:24]), primary_score=0.3,
    )
    _write_pair(
        run_dir, corpus="qasper", canonical_id="doc-b", parser_id="docling",
        markdown=words, primary_score=0.8,
    )
    return run_dir, cache_root, words


def test_dry_run_validates_without_writing_or_assessing(tmp_path: Path, monkeypatch) -> None:
    run_dir, cache_root, _ = _fixture(tmp_path)
    output = tmp_path / "v2"
    monkeypatch.setattr(
        diagnostics_module,
        "assess_source_candidate",
        lambda **_: (_ for _ in ()).throw(AssertionError("assessment called during dry run")),
    )

    report, exit_code = execute(
        run_dir, cache_root, output, dry_run=True, enforce_frozen_inventory=False,
    )

    assert exit_code == 0
    assert report["dry_run"] is True
    assert report["n_validated"] == 4
    assert not output.exists()


def test_execute_writes_distinct_sidecars_and_deterministic_report(tmp_path: Path) -> None:
    run_dir, cache_root, _ = _fixture(tmp_path)
    output = tmp_path / "v2"
    v1_before = {
        path: path.read_bytes() for path in run_dir.rglob("track_c_result.json")
    }

    first, exit_code = execute(
        run_dir, cache_root, output, n_bootstrap=50, enforce_frozen_inventory=False,
    )
    second, second_code = execute(
        run_dir, cache_root, tmp_path / "v2-second", n_bootstrap=50,
        enforce_frozen_inventory=False,
    )

    assert exit_code == second_code == 0
    assert first == second
    assert first["contract_id"] == DIAGNOSTIC_CONTRACT_ID
    assert first["completeness"] == {
        "n_v1_records_discovered": 4,
        "n_v2_assessed": 4,
        "n_excluded": 0,
        "n_v1_ineligible": 0,
        "n_validation_failures": 0,
        "complete": True,
        "exclusions": [],
    }
    assert first["within_document_pairwise"]["n_documents"] == 2
    assert first["parser_selection"]["n_documents"] == 2
    assert first["parser_selection"]["oracle_mean_regret"] == 0.0
    assert first["parser_selection"]["source_evidence_selector_top_one_accuracy"] == 1.0
    assert first["parser_selection"]["source_evidence_selector_accuracy_document_bootstrap_95_ci"] == [
        1.0, 1.0,
    ]
    assert [item["threshold"] for item in first["risk_coverage"]["thresholds"]] == [80, 90, 95]
    assert all(
        "false_accept_rate_document_bootstrap_95_ci" in item
        for item in first["risk_coverage"]["thresholds"]
    )
    assert len(list((output / "sidecars").rglob("track_c_v2_source_candidate.json"))) == 4
    assert (output / "track_c_v2_diagnostics.json").is_file()
    for entry in first["sidecar_inventory"]:
        sidecar_bytes = (output / entry["relative_path"]).read_bytes()
        assert _sha(sidecar_bytes) == entry["sha256"]
    assert {path: path.read_bytes() for path in v1_before} == v1_before


def test_mismatch_is_excluded_and_returns_fail_closed_code(tmp_path: Path) -> None:
    run_dir, cache_root, _ = _fixture(tmp_path)
    result_path = next(run_dir.rglob("track_c_result.json"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["input_sha256"] = "f" * 64
    result_path.write_text(json.dumps(result), encoding="utf-8")

    report, exit_code = execute(
        run_dir, cache_root, tmp_path / "v2", dry_run=True,
        enforce_frozen_inventory=False,
    )

    assert exit_code == 2
    assert report["n_excluded"] == 1
    assert "input_sha256 mismatch" in report["exclusions"][0]["reason"]
    assert report["exclusions"][0]["category"] == "validation_failure"


def test_missing_source_and_cached_metric_tamper_fail_closed(tmp_path: Path) -> None:
    run_dir, cache_root, _ = _fixture(tmp_path)
    (cache_root / "qasper" / "doc-b-123456789abc.pdf").unlink()
    result_path = next((run_dir / "qasper" / "doc-a").rglob("track_c_result.json"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["primary_score"] = 0.123456
    result_path.write_text(json.dumps(result), encoding="utf-8")

    report, exit_code = execute(
        run_dir, cache_root, tmp_path / "v2", dry_run=True,
        enforce_frozen_inventory=False,
    )

    assert exit_code == 2
    reasons = [item["reason"] for item in report["exclusions"]]
    assert any("primary_score does not equal" in reason for reason in reasons)
    assert sum("expected one QASPER source" in reason for reason in reasons) == 2


def test_receipt_contract_hash_and_score_bounds_fail_closed(tmp_path: Path) -> None:
    run_dir, cache_root, _ = _fixture(tmp_path)
    receipt_path = next(run_dir.rglob("execution_record.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["stage1_execution_manifest_sha256"] = "f" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result_path = next(
        path for path in run_dir.rglob("track_c_result.json")
        if path.parent != receipt_path.parent
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["primary_score"] = 1.1
    result["qa_results"][0]["primary_score"] = 1.1
    result_path.write_text(json.dumps(result), encoding="utf-8")

    report, exit_code = execute(
        run_dir, cache_root, tmp_path / "v2", dry_run=True,
        enforce_frozen_inventory=False,
    )

    assert exit_code == 2
    reasons = [item["reason"] for item in report["exclusions"]]
    assert any("stage1_execution_manifest_sha256 mismatch" in reason for reason in reasons)
    assert any("scores must be between 0 and 1" in reason for reason in reasons)


def test_strict_frozen_inventory_rejects_incomplete_run(tmp_path: Path) -> None:
    run_dir, cache_root, _ = _fixture(tmp_path)

    report, exit_code = execute(run_dir, cache_root, tmp_path / "v2", dry_run=True)

    assert exit_code == 2
    assert any(
        item["category"] == "validation_failure" and "frozen V1 inventory mismatch" in item["reason"]
        for item in report["exclusions"]
    )


def test_corpus_gold_is_explicit_virtual_arm(tmp_path: Path) -> None:
    run_dir, cache_root, words = _fixture(tmp_path)
    _write_pair(
        run_dir, corpus="qasper", canonical_id="doc-a", parser_id="corpus_gold",
        markdown=words, primary_score=1.0,
    )

    report, exit_code = execute(
        run_dir, cache_root, tmp_path / "v2", n_bootstrap=0,
        enforce_frozen_inventory=False,
    )

    assert exit_code == 0
    assert report["completeness"]["n_v2_assessed"] == 5
    sidecar_path = (
        tmp_path / "v2" / "sidecars" / "qasper" / "doc-a" / "corpus_gold"
        / "track_c_v2_source_candidate.json"
    )
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["identity"]["arm_kind"] == "corpus_gold"
    assert sidecar["input_bindings"]["execution_record_sha256"] is None
    assert "corpus_gold" in report["per_corpus_parser"]["qasper"]
    # Virtual gold is reported but cannot participate in parser selection.
    assert report["parser_selection"]["n_documents"] == 2


def test_build_report_records_abstention_and_unavailable_baselines() -> None:
    sidecar = {
        "identity": {
            "corpus": "qasper", "canonical_id": "one", "parser_id": "marker",
            "arm_kind": "parser",
        },
        "cached_qa_metrics": {"primary_score": 0.5},
        "assessment": {
            "candidate_intrinsic": {"verdict": "pass", "detectors": []},
            "source_comparison": {
                "verdict": "undetermined",
                "detectors": [{
                    "detector_id": "source.pdf_text_token_retention",
                    "status": "abstained", "eligible": False, "verdict": "undetermined",
                    "score": None, "abstention_reason": "test abstention",
                }],
            },
        },
    }

    report = build_report([sidecar], [], 1, n_bootstrap=0)

    detector = report["detectors"]["source.pdf_text_token_retention"]
    assert detector["abstained"] == 1
    assert detector["abstention_reasons"] == {"test abstention": 1}
    assert report["parser_selection"]["source_evidence_selector_mean_regret"] is None
    assert report["parser_selection"]["best_fixed_parser"] is None


def _diagnostic_sidecar(doc: str, parser_id: str, evidence: int, qa: float) -> dict:
    return {
        "identity": {
            "corpus": "qasper", "canonical_id": doc, "parser_id": parser_id,
            "arm_kind": "parser",
        },
        "cached_qa_metrics": {"primary_score": qa},
        "assessment": {
            "candidate_intrinsic": {"verdict": "pass", "detectors": []},
            "source_comparison": {
                "verdict": "pass",
                "detectors": [{
                    "detector_id": "source.pdf_text_token_retention",
                    "status": "activated", "eligible": True, "verdict": "pass",
                    "score": evidence, "abstention_reason": None,
                }],
            },
        },
    }


def test_hand_computable_diagnostics_cover_ties_regret_and_false_accepts() -> None:
    sidecars = [
        _diagnostic_sidecar("doc-1", "aksharamd-reference", 100, 0.9),
        _diagnostic_sidecar("doc-1", "docling", 90, 0.4),
        _diagnostic_sidecar("doc-1", "marker", 90, 0.4),
        _diagnostic_sidecar("doc-2", "aksharamd-reference", 100, 0.2),
        _diagnostic_sidecar("doc-2", "docling", 80, 0.8),
        _diagnostic_sidecar("doc-2", "marker", 70, 0.8),
    ]

    report = build_report(
        sidecars, [], 6, thresholds=(90, 95), bad_regret_margin=0.05,
        n_bootstrap=200,
    )
    repeated = build_report(
        sidecars, [], 6, thresholds=(90, 95), bad_regret_margin=0.05,
        n_bootstrap=200,
    )

    assert report == repeated
    pairwise = report["within_document_pairwise"]
    assert pairwise["total"] == 6
    assert pairwise["evidence_ties"] == 1
    assert pairwise["qa_ties"] == 1
    assert pairwise["comparable"] == 4
    assert pairwise["concordant"] == pairwise["discordant"] == 2
    assert pairwise["tie_rate"] == 0.166667
    assert pairwise["concordance"] == 0.5
    assert pairwise["document_bootstrap_95_ci"] == [0.0, 1.0]

    selection = report["parser_selection"]
    assert selection["source_evidence_selector_mean_regret"] == 0.3
    assert selection["source_evidence_selector_top_one_accuracy"] == 0.5
    assert selection["best_fixed_parser"] == "docling"
    assert selection["best_fixed_parser_mean_regret"] == 0.25
    assert selection["random_parser_expected_mean_regret"] == 0.266667
    assert selection["random_parser_expected_top_one_accuracy"] == 0.5
    assert selection["oracle_mean_regret"] == 0.0
    assert selection["oracle_top_one_accuracy"] == 1.0

    at_90, at_95 = report["risk_coverage"]["thresholds"]
    assert (at_90["n_accepted"], at_90["n_scorable"]) == (4, 6)
    assert at_90["coverage"] == 0.666667
    assert at_90["risk_mean_oracle_regret"] == 0.4
    assert at_90["false_accept_count"] == 3
    assert at_90["false_accept_rate"] == 0.75
    assert (at_95["coverage"], at_95["risk_mean_oracle_regret"]) == (0.333333, 0.3)
    assert at_95["false_accept_rate"] == 0.5
    for result in (at_90, at_95):
        assert len(result["coverage_document_bootstrap_95_ci"]) == 2
        assert len(result["risk_document_bootstrap_95_ci"]) == 2
        assert len(result["false_accept_rate_document_bootstrap_95_ci"]) == 2


def test_cli_help_and_output_dir_guard(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--help"])
    assert "--dry-run" in capsys.readouterr().out

    run_dir, cache_root, _ = _fixture(tmp_path)
    assert main([
        "--run-dir", str(run_dir), "--cache-root", str(cache_root),
        "--output-dir", str(run_dir), "--dry-run",
    ]) == 2
    assert "must not overlap" in capsys.readouterr().err

    occupied = tmp_path / "occupied-v2"
    occupied.mkdir()
    (occupied / "stale.json").write_text("{}", encoding="utf-8")
    assert main([
        "--run-dir", str(run_dir), "--cache-root", str(cache_root),
        "--output-dir", str(occupied), "--dry-run",
    ]) == 2
    assert "absent or empty" in capsys.readouterr().err

    assert main([
        "--run-dir", str(run_dir), "--cache-root", str(cache_root),
        "--output-dir", str(run_dir / "exploratory-v2"), "--dry-run",
    ]) == 2
    assert "must not overlap" in capsys.readouterr().err
