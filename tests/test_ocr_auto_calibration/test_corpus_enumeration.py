"""Corpus enumeration tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.ocr_auto_calibration import corpus as corpus_mod
from benchmarks.ocr_auto_calibration.corpus import (
    CorpusEntry,
    enumerate_corpus,
    list_failure_fixtures,
    list_local_fixtures,
    list_synthetic_fixtures,
    load_parsebench_corpus,
)


def test_parsebench_lockfile_returns_twelve_entries() -> None:
    entries = load_parsebench_corpus()
    assert len(entries) == 12
    ids = [e.document_id for e in entries]
    assert len(set(ids)) == 12
    # Every entry carries a sha256 (from the lockfile) and a source label.
    for entry in entries:
        assert entry.sha256, f"missing sha256 for {entry.document_id}"
        assert entry.source == "parsebench"


def test_synthetic_fixture_enumeration_matches_disk(tmp_path: Path) -> None:
    synth_dir = tmp_path / "synth"
    synth_dir.mkdir()

    # Two dummy PDFs + one with a sibling label
    for name in ("a.pdf", "b.pdf"):
        (synth_dir / name).write_bytes(b"%PDF-1.4\n%stub\n")
    (synth_dir / "b.json").write_text(
        json.dumps({"profile_class": "custom_b", "expected_backend_by_policy": "tesseract"})
    )

    entries = list_synthetic_fixtures(synth_dir=synth_dir)
    assert len(entries) == 2
    by_id = {e.document_id: e for e in entries}
    assert by_id["a"].profile_class == "a"
    assert by_id["b"].profile_class == "custom_b"
    assert by_id["b"].expected_backend_by_policy == "tesseract"


def test_failure_fixture_enumeration_when_dir_absent() -> None:
    # A non-existent directory should not raise; it returns an empty list.
    entries = list_failure_fixtures(failure_dir=Path("/nonexistent/failure/dir"))
    assert entries == []


def test_enumerate_corpus_deduplicates_by_document_id(tmp_path: Path) -> None:
    synth_dir = tmp_path / "synth"
    synth_dir.mkdir()
    # Deliberately name the synthetic PDF the same as one of the ParseBench ids
    # so we can prove dedup keeps the first observed entry (ParseBench wins).
    (synth_dir / "3colpres.pdf").write_bytes(b"%PDF-1.4\n%stub\n")

    entries = enumerate_corpus(synth_dir=synth_dir)
    # Only one entry per document_id in the combined view.
    ids = [e.document_id for e in entries]
    assert len(ids) == len(set(ids))
    # The ParseBench entry (first-seen) wins.
    matching = [e for e in entries if e.document_id == "3colpres"]
    assert len(matching) == 1
    assert matching[0].source == "parsebench"


def test_corpus_entry_resolved_property_reflects_disk_state(tmp_path: Path) -> None:
    p = tmp_path / "present.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    present = CorpusEntry(
        document_id="present",
        path=p,
        sha256=None,
        profile_class="x",
        expected_backend_by_policy=None,
        source="synthetic",
    )
    absent = CorpusEntry(
        document_id="absent",
        path=tmp_path / "missing.pdf",
        sha256=None,
        profile_class="x",
        expected_backend_by_policy=None,
        source="synthetic",
    )
    assert present.resolved is True
    assert absent.resolved is False


def test_load_parsebench_corpus_missing_file_returns_empty(tmp_path: Path) -> None:
    entries = load_parsebench_corpus(lockfile=tmp_path / "does_not_exist.json")
    assert entries == []


def test_default_cache_root_honors_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AKSHARAMD_PARSEBENCH_CACHE", "/some/override/path")
    root = corpus_mod._default_parsebench_cache_root()
    assert str(root).replace("\\", "/") == "/some/override/path"


# ── list_local_fixtures ─────────────────────────────────────────────────


def test_list_local_fixtures_empty_when_dir_missing(tmp_path: Path) -> None:
    """Absent local-fixtures dir must not raise; returns empty list."""
    missing = tmp_path / "does_not_exist"
    assert list_local_fixtures(local_dir=missing) == []


def test_list_local_fixtures_reads_labels(tmp_path: Path) -> None:
    """Labels with resolvable and missing PDF paths both surface as entries."""
    local_dir = tmp_path / "local"
    local_dir.mkdir()

    present_pdf = tmp_path / "present.pdf"
    present_pdf.write_bytes(b"%PDF-1.4\n%stub present\n")

    (local_dir / "present.json").write_text(
        json.dumps(
            {
                "document_id": "present_asset",
                "path": str(present_pdf),
                "profile_class": "local_present",
                "expected_backend_by_policy": "tesseract",
            }
        ),
        encoding="utf-8",
    )
    (local_dir / "missing.json").write_text(
        json.dumps(
            {
                "document_id": "missing_asset",
                "path": str(tmp_path / "not_on_disk.pdf"),
                "profile_class": "local_missing",
            }
        ),
        encoding="utf-8",
    )

    entries = list_local_fixtures(local_dir=local_dir)
    assert len(entries) == 2
    by_id = {e.document_id: e for e in entries}
    assert by_id["present_asset"].resolved is True
    assert by_id["present_asset"].source == "local"
    assert by_id["present_asset"].profile_class == "local_present"
    assert by_id["missing_asset"].resolved is False
    assert by_id["missing_asset"].source == "local"


def test_enumerate_corpus_dedupes_across_sources_including_local(
    tmp_path: Path,
) -> None:
    """A local entry sharing an id with a ParseBench entry loses to ParseBench.

    Dedup is first-observed; ParseBench comes first in the enumeration order,
    so its entry wins and the local one is dropped.
    """
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    dupe_pdf = tmp_path / "3colpres_local.pdf"
    dupe_pdf.write_bytes(b"%PDF-1.4\n%local dupe\n")
    (local_dir / "3colpres.json").write_text(
        json.dumps(
            {
                "document_id": "3colpres",  # collides with a ParseBench asset id
                "path": str(dupe_pdf),
                "profile_class": "local_dupe",
            }
        ),
        encoding="utf-8",
    )

    entries = enumerate_corpus(local_dir=local_dir)
    ids = [e.document_id for e in entries]
    assert len(ids) == len(set(ids)), "ids must remain unique after dedup"
    matching = [e for e in entries if e.document_id == "3colpres"]
    assert len(matching) == 1
    assert matching[0].source == "parsebench"
