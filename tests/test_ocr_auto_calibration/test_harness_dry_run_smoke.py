"""Dry-run smoke test for the harness orchestrator."""
from __future__ import annotations

import json
from pathlib import Path

from benchmarks.ocr_auto_calibration.corpus import CorpusEntry
from benchmarks.ocr_auto_calibration.harness import run_harness
from benchmarks.ocr_auto_calibration.schema import (
    HARNESS_SCHEMA_VERSION,
    run_report_from_dict,
)


def _tiny_entries(tmp_path: Path) -> list[CorpusEntry]:
    """Two dummy corpus entries with paths that need not exist in dry-run."""
    return [
        CorpusEntry(
            document_id="dryrun_doc_a",
            path=tmp_path / "a.pdf",
            sha256="sha_a",
            profile_class="dryrun_test",
            expected_backend_by_policy="tesseract",
            source="synthetic",
        ),
        CorpusEntry(
            document_id="dryrun_doc_b",
            path=tmp_path / "b.pdf",
            sha256="sha_b",
            profile_class="dryrun_test",
            expected_backend_by_policy="unlimited_ocr",
            source="synthetic",
        ),
    ]


def test_dry_run_returns_schema_conformant_report(tmp_path: Path) -> None:
    entries = _tiny_entries(tmp_path)
    report = run_harness(
        entries=entries,
        dry_run=True,
        use_cache=False,
        aksharamd_commit="test-commit",
        model_revision="test-revision",
        out_root=tmp_path / "out",
    )
    assert report.corpus_size == 2
    assert report.harness_schema_version == HARNESS_SCHEMA_VERSION
    assert len(report.documents) == 2
    for doc in report.documents:
        assert doc.tesseract.exit_status == 0
        assert doc.unlimited_ocr.exit_status == 0
        assert doc.auto.exit_status == 0


def test_dry_run_report_json_round_trips(tmp_path: Path) -> None:
    entries = _tiny_entries(tmp_path)
    report = run_harness(
        entries=entries,
        dry_run=True,
        use_cache=False,
        aksharamd_commit="test-commit",
        model_revision="test-revision",
        out_root=tmp_path / "out",
    )
    payload = report.to_dict()
    # Full JSON round-trip.
    serialised = json.dumps(payload)
    reloaded = json.loads(serialised)
    round_tripped = run_report_from_dict(reloaded)
    assert round_tripped.corpus_size == report.corpus_size
    assert round_tripped.aksharamd_commit == report.aksharamd_commit
    assert len(round_tripped.documents) == len(report.documents)
    original_id = report.documents[0].document_id
    assert round_tripped.documents[0].document_id == original_id


def test_dry_run_never_invokes_subprocess(tmp_path: Path, monkeypatch) -> None:
    """Sanity check: dry-run must not call subprocess.run under our module."""
    from benchmarks.ocr_auto_calibration import harness as harness_mod

    call_count = {"n": 0}

    def _fake_run(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("subprocess.run must not be called in dry-run mode")

    monkeypatch.setattr(harness_mod.subprocess, "run", _fake_run)
    entries = _tiny_entries(tmp_path)
    _ = run_harness(
        entries=entries,
        dry_run=True,
        use_cache=False,
        aksharamd_commit="t",
        model_revision="t",
        out_root=tmp_path / "out",
    )
    assert call_count["n"] == 0
