"""Acquisition & corpus-provenance tests for the OCR Auto Policy harness.

Covers the per-document ``DocumentSummary.acquisition`` field and the
top-level ``RunReport.corpus_provenance`` envelope, including:

* ParseBench sha match / mismatch against the lockfile-recorded sha.
* Local optional assets that are absent on disk (skipped with a marker).
* Local optional assets that are present on disk.
* The lockfile-checksum envelope populated against the real lockfile.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.ocr_auto_calibration import corpus as corpus_mod
from benchmarks.ocr_auto_calibration import harness as harness_mod
from benchmarks.ocr_auto_calibration.corpus import CorpusEntry
from benchmarks.ocr_auto_calibration.harness import run_harness


def _write_pdf(path: Path, body: bytes = b"%PDF-1.4\n%stub\n") -> str:
    """Write a stub PDF and return its SHA-256 hex."""
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


# ── ParseBench provenance ────────────────────────────────────────────


def test_parsebench_acquisition_records_lockfile_sha_and_match(
    tmp_path: Path,
) -> None:
    """When on-disk bytes match the lockfile sha, sha256_matches is True."""
    pdf_path = tmp_path / "pb_asset.pdf"
    on_disk_sha = _write_pdf(pdf_path, b"%PDF-1.4\n%parsebench matching bytes\n")
    entry = CorpusEntry(
        document_id="pb_matching",
        path=pdf_path,
        sha256=on_disk_sha,  # lockfile-recorded sha equals on-disk sha
        profile_class="parsebench_test",
        expected_backend_by_policy=None,
        source="parsebench",
        extra={"hf_repo_path": "docs/text/pb_matching.pdf"},
    )
    report = run_harness(
        entries=[entry],
        dry_run=True,
        use_cache=False,
        aksharamd_commit="t",
        model_revision="t",
        out_root=tmp_path / "out",
    )
    assert len(report.documents) == 1
    acq = report.documents[0].acquisition
    assert acq["source"] == "parsebench"
    assert acq["on_disk_sha256"] == on_disk_sha
    assert acq["lockfile_sha256"] == on_disk_sha
    assert acq["sha256_matches"] is True
    assert acq["hf_repo_path"] == "docs/text/pb_matching.pdf"


def test_parsebench_acquisition_records_mismatch(tmp_path: Path) -> None:
    """When on-disk bytes differ from lockfile sha, sha256_matches is False."""
    pdf_path = tmp_path / "pb_asset.pdf"
    on_disk_sha = _write_pdf(pdf_path, b"%PDF-1.4\n%actual bytes on disk\n")
    bogus_sha = "0" * 64
    entry = CorpusEntry(
        document_id="pb_mismatch",
        path=pdf_path,
        sha256=bogus_sha,  # lockfile-recorded sha DOES NOT match on-disk
        profile_class="parsebench_test",
        expected_backend_by_policy=None,
        source="parsebench",
        extra={"hf_repo_path": "docs/text/pb_mismatch.pdf"},
    )
    report = run_harness(
        entries=[entry],
        dry_run=True,
        use_cache=False,
        aksharamd_commit="t",
        model_revision="t",
        out_root=tmp_path / "out",
    )
    acq = report.documents[0].acquisition
    assert acq["source"] == "parsebench"
    assert acq["on_disk_sha256"] == on_disk_sha
    assert acq["lockfile_sha256"] == bogus_sha
    assert acq["sha256_matches"] is False


# ── Local optional assets ────────────────────────────────────────────


def test_local_missing_asset_produces_skipped_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing local asset -> 3 skipped RunResults; compile is never invoked.

    We intercept the CLI subprocess (``aksharamd compile ...``) but allow
    non-compile subprocess calls (e.g. ``nvidia-smi`` machine-metadata
    probing) to pass through — the guarantee is that the *compile step*
    is skipped, not that the harness performs zero subprocess work.
    """
    real_run = harness_mod.subprocess.run

    def _guarded_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(cmd, list) and cmd and cmd[0] == "aksharamd":
            raise AssertionError(
                "aksharamd compile must not be invoked for a skipped local asset"
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(harness_mod.subprocess, "run", _guarded_run)

    entry = CorpusEntry(
        document_id="missing_local",
        path=tmp_path / "not_on_disk.pdf",
        sha256=None,
        profile_class="local_missing",
        expected_backend_by_policy=None,
        source="local",
    )
    report = run_harness(
        entries=[entry],
        dry_run=False,  # NOT dry-run: prove the skip path runs without compile
        use_cache=False,
        aksharamd_commit="t",
        model_revision="t",
        out_root=tmp_path / "out",
    )
    doc = report.documents[0]
    for run_result in (doc.tesseract, doc.unlimited_ocr, doc.auto):
        assert run_result.exit_status == 64
        assert run_result.error_message == "skipped_missing_local_asset"
        assert run_result.document_sha256 == ""
        assert run_result.runtime_seconds == 0.0
        assert run_result.stderr_tail == ""
    # Acquisition envelope reflects the skip.
    acq = doc.acquisition
    assert acq["source"] == "local"
    assert acq["resolved"] is False
    assert acq["skipped"] is True
    assert acq["expected_path"] == str(entry.path)
    # Envelope-level counter is incremented.
    assert report.corpus_provenance["skipped_missing_local_count"] == 1


def test_local_present_asset_records_source_local(tmp_path: Path) -> None:
    """Present local asset -> acquisition.source == 'local' with a real sha."""
    pdf_path = tmp_path / "present_local.pdf"
    on_disk_sha = _write_pdf(pdf_path, b"%PDF-1.4\n%local present\n")
    entry = CorpusEntry(
        document_id="present_local",
        path=pdf_path,
        sha256=None,  # user provided no expected sha
        profile_class="local_present",
        expected_backend_by_policy="tesseract",
        source="local",
    )
    report = run_harness(
        entries=[entry],
        dry_run=True,
        use_cache=False,
        aksharamd_commit="t",
        model_revision="t",
        out_root=tmp_path / "out",
    )
    acq = report.documents[0].acquisition
    assert acq["source"] == "local"
    assert acq["resolved"] is True
    assert acq["on_disk_sha256"] == on_disk_sha
    assert acq["expected_sha256"] is None
    assert acq["sha256_matches"] is None


# ── Corpus-level provenance envelope ─────────────────────────────────


def test_corpus_provenance_envelope_populated(tmp_path: Path) -> None:
    """Real ParseBench lockfile -> envelope has non-empty sha + revision + counts."""
    # Fabricate a small, deterministic corpus that mixes sources.
    pb_pdf = tmp_path / "pb.pdf"
    _write_pdf(pb_pdf, b"%PDF-1.4\n%pb entry\n")
    syn_pdf = tmp_path / "syn.pdf"
    _write_pdf(syn_pdf, b"%PDF-1.4\n%syn entry\n")

    entries = [
        CorpusEntry(
            document_id="pb_env_a",
            path=pb_pdf,
            sha256=hashlib.sha256(pb_pdf.read_bytes()).hexdigest(),
            profile_class="parsebench_test",
            expected_backend_by_policy=None,
            source="parsebench",
            extra={"hf_repo_path": "docs/text/pb_env_a.pdf"},
        ),
        CorpusEntry(
            document_id="syn_env_a",
            path=syn_pdf,
            sha256=None,
            profile_class="synthetic_test",
            expected_backend_by_policy=None,
            source="synthetic",
        ),
        CorpusEntry(
            document_id="local_missing_env",
            path=tmp_path / "absent.pdf",
            sha256=None,
            profile_class="local_missing",
            expected_backend_by_policy=None,
            source="local",
        ),
    ]

    report = run_harness(
        entries=entries,
        dry_run=True,
        use_cache=False,
        aksharamd_commit="t",
        model_revision="t",
        out_root=tmp_path / "out",
    )
    prov = report.corpus_provenance

    # Real lockfile at the default path -> non-empty sha and pinned revision.
    assert prov["parsebench_lockfile_sha256"], "lockfile sha should be non-empty"
    assert len(prov["parsebench_lockfile_sha256"]) == 64
    assert prov["parsebench_dataset_revision"], "dataset revision should be non-empty"
    # Sanity: the pinned revision matches what the lockfile actually records.
    with corpus_mod._LOCKFILE_DEFAULT.open("r", encoding="utf-8") as fh:
        lockfile_payload = json.load(fh)
    expected_revision = lockfile_payload["dataset_source"]["dataset_revision"]
    assert prov["parsebench_dataset_revision"] == expected_revision

    # counts_by_source sums to corpus_size.
    counts = prov["counts_by_source"]
    assert sum(counts.values()) == report.corpus_size
    assert counts["parsebench"] == 1
    assert counts["synthetic"] == 1
    assert counts["local"] == 1
    # resolved_counts_by_source excludes the missing local entry.
    resolved = prov["resolved_counts_by_source"]
    assert resolved["parsebench"] == 1
    assert resolved["synthetic"] == 1
    assert resolved["local"] == 0
    assert prov["skipped_missing_local_count"] == 1
