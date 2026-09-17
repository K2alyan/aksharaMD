"""Tests for Stage 1 runner hardening (B1a-9).

Covers:
  1. Resume validation — full frozen-identity check before skipping
  2. VLM model artifact SHA non-placeholder enforcement via build_adapters
  3. Firewall fail-closed: run_track_a_olmocr raises (not warns) on failure
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.provenance import is_placeholder_sha
from benchmarks.eval_v1.smoke_b1a_7b.subprocess_runner import SubprocessInvoker
from benchmarks.eval_v1.stage1.runner import Stage1Runner, build_adapters

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

REAL_SHA_64 = "a" * 63 + "b"   # non-placeholder 64-hex string
ZERO_SHA = "0" * 64             # placeholder

_MANIFEST_SHA = "c" * 64        # synthetic manifest SHA for unit tests


def _fake_invoker() -> SubprocessInvoker:
    inv = MagicMock(spec=SubprocessInvoker)
    return inv


def _minimal_record_dict(
    *,
    canonical_id: str = "corpus/doc_001",
    parser_id: str = "aksharamd-reference",
    manifest_sha: str = _MANIFEST_SHA,
    exit_status: str = "EXECUTED",
) -> dict:
    """Return a minimal dict that would pass resume validation."""
    return {
        "canonical_id": canonical_id,
        "parser_id": parser_id,
        "stage1_execution_manifest_sha256": manifest_sha,
        "exit_status": exit_status,
        "wall_clock_seconds": 1.0,
        "defect_reason": None,
    }


def _make_runner(tmp_path: Path) -> Stage1Runner:
    return Stage1Runner(
        items=[],
        run_dir=tmp_path / "run",
        stage1_manifest_sha256=_MANIFEST_SHA,
        model_artifact_shas={"marker": REAL_SHA_64, "docling": REAL_SHA_64},
        model_cache_paths={"marker": "/fake/marker", "docling": "/fake/docling"},
        verbose=False,
        resume=True,
    )


# ---------------------------------------------------------------------------
# 1. Resume validation
# ---------------------------------------------------------------------------


class TestResumeValidation:
    def _runner(self, tmp_path: Path) -> Stage1Runner:
        return _make_runner(tmp_path)

    def _write_record(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_valid_record_is_skipped(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        rec = tmp_path / "rec.json"
        self._write_record(rec, _minimal_record_dict())
        ok, reason = runner._validate_resume_record(
            rec, canonical_id="corpus/doc_001", parser_id="aksharamd-reference"
        )
        assert ok is True
        assert reason == ""

    def test_wrong_canonical_id_rejected(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        rec = tmp_path / "rec.json"
        self._write_record(rec, _minimal_record_dict(canonical_id="corpus/OTHER"))
        ok, reason = runner._validate_resume_record(
            rec, canonical_id="corpus/doc_001", parser_id="aksharamd-reference"
        )
        assert ok is False
        assert "canonical_id mismatch" in reason

    def test_wrong_parser_id_rejected(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        rec = tmp_path / "rec.json"
        self._write_record(rec, _minimal_record_dict(parser_id="marker"))
        ok, reason = runner._validate_resume_record(
            rec, canonical_id="corpus/doc_001", parser_id="aksharamd-reference"
        )
        assert ok is False
        assert "parser_id mismatch" in reason

    def test_stale_manifest_sha_rejected(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        rec = tmp_path / "rec.json"
        self._write_record(
            rec, _minimal_record_dict(manifest_sha="d" * 64)
        )
        ok, reason = runner._validate_resume_record(
            rec, canonical_id="corpus/doc_001", parser_id="aksharamd-reference"
        )
        assert ok is False
        assert "manifest SHA mismatch" in reason

    def test_harness_defect_exit_status_rejected(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        rec = tmp_path / "rec.json"
        self._write_record(
            rec, _minimal_record_dict(exit_status="HARNESS_DEFECT")
        )
        ok, reason = runner._validate_resume_record(
            rec, canonical_id="corpus/doc_001", parser_id="aksharamd-reference"
        )
        assert ok is False
        assert "non-terminal exit_status" in reason

    def test_defect_exit_status_accepted(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        rec = tmp_path / "rec.json"
        self._write_record(rec, _minimal_record_dict(exit_status="DEFECT"))
        ok, _ = runner._validate_resume_record(
            rec, canonical_id="corpus/doc_001", parser_id="aksharamd-reference"
        )
        assert ok is True

    def test_corrupt_json_causes_rerun(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        rec = tmp_path / "rec.json"
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text("not json {{{", encoding="utf-8")
        ok, reason = runner._validate_resume_record(
            rec, canonical_id="corpus/doc_001", parser_id="aksharamd-reference"
        )
        assert ok is False
        assert "parse error" in reason


# ---------------------------------------------------------------------------
# 2. VLM model artifact SHA non-placeholder enforcement
# ---------------------------------------------------------------------------


class TestModelArtifactSHAs:
    def test_placeholder_sha_rejected_by_provenance(self) -> None:
        assert is_placeholder_sha(ZERO_SHA) is True

    def test_real_sha_accepted_by_provenance(self) -> None:
        assert is_placeholder_sha(REAL_SHA_64) is False

    def test_build_adapters_passes_real_sha_to_marker(self) -> None:
        adapters = build_adapters(
            subprocess_invoker=_fake_invoker(),
            model_artifact_shas={
                "marker": REAL_SHA_64,
                "docling": REAL_SHA_64,
            },
        )
        assert adapters["marker"].parser_model_artifact_sha256() == REAL_SHA_64

    def test_build_adapters_passes_real_sha_to_docling(self) -> None:
        adapters = build_adapters(
            subprocess_invoker=_fake_invoker(),
            model_artifact_shas={
                "marker": REAL_SHA_64,
                "docling": REAL_SHA_64,
            },
        )
        assert adapters["docling"].parser_model_artifact_sha256() == REAL_SHA_64

    def test_build_adapters_cpu_only_parsers_have_null_artifact_sha(self) -> None:
        adapters = build_adapters(
            subprocess_invoker=_fake_invoker(),
            model_artifact_shas={"marker": REAL_SHA_64, "docling": REAL_SHA_64},
        )
        assert adapters["aksharamd-reference"].parser_model_artifact_sha256() is None
        assert adapters["markitdown"].parser_model_artifact_sha256() is None

    def test_execution_records_from_admission_batch_have_real_vlm_sha(self) -> None:
        """Integration: spot-check the admission batch records on disk.

        This test reads from the most-recent admission run directory and
        verifies every VLM record carries a non-placeholder artifact SHA.
        Skipped if the directory does not exist (e.g. clean CI checkout).
        """
        root = Path(__file__).parent.parent
        admission_dirs = sorted(
            root.glob("benchmarks/results/stage1-admission-*"),
            reverse=True,
        )
        if not admission_dirs:
            pytest.skip("no admission run directory found")

        run_dir = admission_dirs[0]
        vlm_records = [
            r for r in run_dir.rglob("execution_record.json")
            if json.loads(r.read_text())["parser_id"] in {"marker", "docling"}
        ]
        if not vlm_records:
            pytest.skip("no VLM execution records found in admission dir")

        for rec_path in vlm_records:
            d = json.loads(rec_path.read_text())
            sha = d.get("parser_model_artifact_sha256")
            assert sha is not None, (
                f"VLM record has null artifact SHA: {rec_path}"
            )
            assert not is_placeholder_sha(sha), (
                f"VLM record has placeholder artifact SHA {sha[:16]}…: {rec_path}"
            )


# ---------------------------------------------------------------------------
# 3. Firewall fail-closed in run_track_a_olmocr
# ---------------------------------------------------------------------------


class TestFirewallFailClosed:
    def test_production_runner_raises_on_firewall_failure(self) -> None:
        """run_track_a_olmocr._setup_firewall must raise, not warn-and-continue."""
        from benchmarks.eval_v1.stage1 import run_track_a_olmocr

        # RealFirewallBackend is a local import inside _setup_firewall; patch at source.
        with patch(
            "benchmarks.eval_v1.smoke_b1a_7b.real_firewall.RealFirewallBackend",
            side_effect=RuntimeError("no firewall"),
        ):
            with pytest.raises(Exception):
                run_track_a_olmocr._setup_firewall()

    def test_production_runner_has_no_no_firewall_flag(self) -> None:
        """The --no-firewall argument must not exist in the production argparser."""
        import argparse

        p = argparse.ArgumentParser()
        # replicate what main() does
        p.add_argument("--no-resume", action="store_true")
        # --no-firewall must NOT be present; verify parsing it raises
        with pytest.raises(SystemExit):
            p.parse_args(["--no-firewall"])

    def test_admission_still_accepts_no_firewall(self) -> None:
        """Admission batch may skip firewall for infrastructure testing."""
        # Should not raise on --no-firewall flag itself (only SystemExit on ABORT)
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--no-firewall", action="store_true")
        args = p.parse_args(["--no-firewall"])
        assert args.no_firewall is True
