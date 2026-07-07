"""Tests for the public benchmark corpus infrastructure.

These tests do NOT require network access or a populated .public_corpus/.
They verify schema validity, synthetic file generation, runner error handling,
and Markdown summary writing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"
sys.path.insert(0, str(REPO_ROOT))


# ── Manifest schema ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def manifest() -> dict:
    path = BENCHMARKS / "public_corpus_manifest.json"
    assert path.exists(), "public_corpus_manifest.json not found"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_manifest_has_required_top_level_keys(manifest):
    for key in ("version", "description", "license_note", "corpus_dir", "files"):
        assert key in manifest, f"Missing top-level key: {key!r}"


def test_manifest_files_is_nonempty(manifest):
    assert len(manifest["files"]) > 0


def test_manifest_every_entry_has_required_fields(manifest):
    required = {"id", "format", "label", "source", "local_path", "license", "expected_outcome"}
    for entry in manifest["files"]:
        missing = required - entry.keys()
        assert not missing, f"Entry {entry.get('id', '?')} missing fields: {missing}"


def test_manifest_ids_are_unique(manifest):
    ids = [e["id"] for e in manifest["files"]]
    assert len(ids) == len(set(ids)), "Duplicate IDs in manifest"


def test_manifest_pdf_entries_have_url(manifest):
    for entry in manifest["files"]:
        if entry["source"] == "py-pdf/sample-files":
            assert entry.get("url"), f"PDF entry {entry['id']} missing url"
            assert entry["url"].startswith("https://"), f"Entry {entry['id']} url must be https"


def test_manifest_pdf_entries_have_py_pdf_meta(manifest):
    for entry in manifest["files"]:
        if entry["source"] == "py-pdf/sample-files":
            assert "py_pdf_meta" in entry, f"PDF entry {entry['id']} missing py_pdf_meta"
            meta = entry["py_pdf_meta"]
            assert "pages" in meta and "encrypted" in meta


def test_manifest_synthetic_entries_have_no_url(manifest):
    for entry in manifest["files"]:
        if entry["source"] == "synthetic":
            assert entry.get("url") is None, f"Synthetic entry {entry['id']} should have url=null"


def test_manifest_has_at_least_25_pdf_entries(manifest):
    pdf_entries = [e for e in manifest["files"] if e["source"] == "py-pdf/sample-files"]
    assert len(pdf_entries) >= 25, f"Expected >= 25 PDF entries, got {len(pdf_entries)}"


def test_manifest_has_expected_synthetic_formats(manifest):
    syn_formats = {e["format"] for e in manifest["files"] if e["source"] == "synthetic"}
    required = {"docx", "xlsx", "pptx", "html", "csv", "json", "xml", "txt", "md", "zip"}
    missing = required - syn_formats
    assert not missing, f"Missing synthetic formats: {missing}"


def test_manifest_encrypted_pdf_has_error_outcome(manifest):
    encrypted = [e for e in manifest["files"] if e.get("py_pdf_meta", {}).get("encrypted")]
    for entry in encrypted:
        assert entry["expected_outcome"] == "error", (
            f"Encrypted entry {entry['id']} should have expected_outcome='error'"
        )


def test_manifest_license_note_mentions_cc_by_sa(manifest):
    assert "CC-BY-SA-4.0" in manifest["license_note"]


# ── Corpus builder — synthetic file generation ────────────────────────────────

def test_synthetic_html_created(tmp_path):
    from benchmarks.build_public_corpus import _build_html
    dest = tmp_path / "test.html"
    _build_html(dest)
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "<html" in content
    assert "<h1" in content
    assert "<table" in content


def test_synthetic_csv_created(tmp_path):
    from benchmarks.build_public_corpus import _build_csv
    dest = tmp_path / "test.csv"
    _build_csv(dest)
    assert dest.exists()
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    assert "id" in lines[0].lower() or "format" in lines[0].lower()


def test_synthetic_json_created(tmp_path):
    from benchmarks.build_public_corpus import _build_json
    dest = tmp_path / "test.json"
    _build_json(dest)
    assert dest.exists()
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert "benchmark" in data
    assert "results" in data
    assert isinstance(data["results"], list)


def test_synthetic_xml_created(tmp_path):
    from benchmarks.build_public_corpus import _build_xml
    dest = tmp_path / "test.xml"
    _build_xml(dest)
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "<?xml" in content
    assert "<benchmark" in content


def test_synthetic_txt_created(tmp_path):
    from benchmarks.build_public_corpus import _build_txt
    dest = tmp_path / "test.txt"
    _build_txt(dest)
    assert dest.exists()
    assert dest.stat().st_size > 100


def test_synthetic_md_created(tmp_path):
    from benchmarks.build_public_corpus import _build_md
    dest = tmp_path / "test.md"
    _build_md(dest)
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert content.startswith("#")


def test_synthetic_zip_created(tmp_path):
    import zipfile as _zipfile

    from benchmarks.build_public_corpus import _build_zip
    dest = tmp_path / "test.zip"
    _build_zip(dest)
    assert dest.exists()
    with _zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert len(names) >= 3
    assert any(n.endswith(".md") for n in names)
    assert any(n.endswith(".py") for n in names)


def test_build_dry_run_no_files_written(tmp_path, monkeypatch):
    """Dry-run mode must not write any files."""
    import benchmarks.build_public_corpus as bpc
    monkeypatch.setattr(bpc, "BENCHMARKS", tmp_path)
    manifest_copy = BENCHMARKS / "public_corpus_manifest.json"
    import shutil
    shutil.copy(manifest_copy, tmp_path / "public_corpus_manifest.json")
    monkeypatch.setattr(bpc, "MANIFEST_PATH", tmp_path / "public_corpus_manifest.json")

    counts = bpc.build(dry_run=True)
    corpus_dir = tmp_path / ".public_corpus"
    written = list(corpus_dir.rglob("*")) if corpus_dir.exists() else []
    assert not [f for f in written if f.is_file()], "Dry-run must not write files"
    assert counts["failed"] == 0


# ── Runner — error handling and output ───────────────────────────────────────

def test_runner_handles_missing_file_gracefully(tmp_path):
    """_run_one must return outcome='skipped' for a non-existent file."""
    from benchmarks.run_public_benchmark import _run_one
    corpus_root = tmp_path / ".public_corpus"
    corpus_root.mkdir()
    entry = {
        "id": "test-missing",
        "label": "missing-file",
        "format": "txt",
        "source": "synthetic",
        "local_path": "synthetic/does_not_exist.txt",
        "expected_outcome": "success",
    }
    result = _run_one(corpus_root, entry)
    assert result["outcome"] == "skipped"
    assert result["skip_reason"] == "file_not_found"


def test_runner_succeeds_on_txt_file(tmp_path):
    """_run_one must return outcome='success' for a readable plain-text file."""
    from benchmarks.build_public_corpus import _build_txt
    from benchmarks.run_public_benchmark import _run_one

    corpus_root = tmp_path / ".public_corpus"
    syn_dir = corpus_root / "synthetic"
    syn_dir.mkdir(parents=True)
    txt_path = syn_dir / "sample.txt"
    _build_txt(txt_path)

    entry = {
        "id": "test-txt",
        "label": "synthetic-txt",
        "format": "txt",
        "source": "synthetic",
        "local_path": "synthetic/sample.txt",
        "expected_outcome": "success",
    }
    result = _run_one(corpus_root, entry)
    assert result["outcome"] == "success", f"Expected success, got: {result}"
    assert result["block_count"] > 0
    assert result["output_chars"] > 0


def test_runner_writes_markdown_summary(tmp_path):
    """_write_markdown must produce a non-empty .md file with key sections."""
    from benchmarks.run_public_benchmark import _write_markdown
    manifest = json.loads((BENCHMARKS / "public_corpus_manifest.json").read_text())
    results = [
        {"id": "pdf-001", "label": "minimal-pdflatex", "format": "pdf", "source": "py-pdf/sample-files",
         "outcome": "success", "expected_outcome": "success", "block_count": 5, "output_chars": 300,
         "estimated_tokens": 75, "elapsed_seconds": 0.12, "errors": [], "warnings": []},
        {"id": "pdf-005", "label": "libreoffice-encrypted", "format": "pdf", "source": "py-pdf/sample-files",
         "outcome": "error", "expected_outcome": "error", "errors": [{"code": "PDF_ENCRYPTED", "message": "encrypted"}],
         "elapsed_seconds": 0.04, "warnings": []},
        {"id": "syn-001", "label": "synthetic-docx", "format": "docx", "source": "synthetic",
         "outcome": "success", "expected_outcome": "success", "block_count": 8, "output_chars": 450,
         "estimated_tokens": 112, "elapsed_seconds": 0.08, "errors": [], "warnings": []},
    ]
    out = tmp_path / "summary.md"
    _write_markdown(results, manifest, out, elapsed_total=0.24)

    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "# AksharaMD Public Benchmark Results" in content
    assert "Success" in content
    assert "Reproducibility" in content


def test_runner_writes_jsonl(tmp_path):
    """_write_jsonl must produce valid JSONL with one record per result."""
    from benchmarks.run_public_benchmark import _write_jsonl
    results = [
        {"id": "a", "outcome": "success"},
        {"id": "b", "outcome": "error"},
    ]
    path = tmp_path / "out.jsonl"
    _write_jsonl(results, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "a"
    assert json.loads(lines[1])["outcome"] == "error"
