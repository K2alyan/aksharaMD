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


# ── Manifest schema (v2) ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def manifest() -> dict:
    path = BENCHMARKS / "public_corpus_manifest.json"
    assert path.exists(), "public_corpus_manifest.json not found"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_manifest_has_required_top_level_keys(manifest):
    for key in ("version", "description", "license_note", "corpus_dir", "pdf_corpus", "synthetic_corpus"):
        assert key in manifest, f"Missing top-level key: {key!r}"


def test_manifest_pdf_corpus_has_required_keys(manifest):
    pdf = manifest["pdf_corpus"]
    for key in ("files", "smoke_ids", "total", "max_download_mb_default"):
        assert key in pdf, f"pdf_corpus missing key: {key!r}"


def test_manifest_synthetic_corpus_has_required_keys(manifest):
    syn = manifest["synthetic_corpus"]
    for key in ("formats", "variants_per_format", "smoke_variants_per_format", "total", "local_path_template"):
        assert key in syn, f"synthetic_corpus missing key: {key!r}"


def test_manifest_has_34_pdf_entries(manifest):
    pdf_files = manifest["pdf_corpus"]["files"]
    assert len(pdf_files) == 34, f"Expected 34 PDF entries, got {len(pdf_files)}"


def test_manifest_pdf_files_is_nonempty(manifest):
    assert len(manifest["pdf_corpus"]["files"]) > 0


def test_manifest_pdf_every_entry_has_required_fields(manifest):
    required = {"id", "label", "source", "url", "local_path", "license", "expected_outcome"}
    for entry in manifest["pdf_corpus"]["files"]:
        missing = required - entry.keys()
        assert not missing, f"PDF entry {entry.get('id', '?')} missing fields: {missing}"


def test_manifest_pdf_ids_are_unique(manifest):
    ids = [e["id"] for e in manifest["pdf_corpus"]["files"]]
    assert len(ids) == len(set(ids)), "Duplicate IDs in pdf_corpus.files"


def test_manifest_pdf_entries_have_https_url(manifest):
    for entry in manifest["pdf_corpus"]["files"]:
        assert entry.get("url", "").startswith("https://"), (
            f"PDF entry {entry['id']} url must be https"
        )


def test_manifest_pdf_entries_have_py_pdf_meta(manifest):
    for entry in manifest["pdf_corpus"]["files"]:
        assert "py_pdf_meta" in entry, f"PDF entry {entry['id']} missing py_pdf_meta"
        meta = entry["py_pdf_meta"]
        assert "pages" in meta and "encrypted" in meta


def test_manifest_encrypted_pdf_has_error_outcome(manifest):
    for entry in manifest["pdf_corpus"]["files"]:
        if entry.get("py_pdf_meta", {}).get("encrypted"):
            assert entry["expected_outcome"] == "error", (
                f"Encrypted entry {entry['id']} should have expected_outcome='error'"
            )


def test_manifest_smoke_ids_are_valid_pdf_ids(manifest):
    all_ids = {e["id"] for e in manifest["pdf_corpus"]["files"]}
    smoke_ids = manifest["pdf_corpus"]["smoke_ids"]
    assert len(smoke_ids) > 0, "smoke_ids must not be empty"
    for sid in smoke_ids:
        assert sid in all_ids, f"Smoke ID {sid!r} not found in pdf_corpus.files"


def test_manifest_smoke_ids_are_unique(manifest):
    smoke_ids = manifest["pdf_corpus"]["smoke_ids"]
    assert len(smoke_ids) == len(set(smoke_ids)), "Duplicate smoke IDs"


def test_manifest_has_10_synthetic_formats(manifest):
    formats = manifest["synthetic_corpus"]["formats"]
    assert len(formats) == 10, f"Expected 10 synthetic formats, got {len(formats)}"


def test_manifest_synthetic_corpus_has_10_variants_per_format(manifest):
    assert manifest["synthetic_corpus"]["variants_per_format"] == 10


def test_manifest_synthetic_total_matches_spec(manifest):
    syn = manifest["synthetic_corpus"]
    expected = syn["variants_per_format"] * len(syn["formats"])
    assert syn["total"] == expected, (
        f"synthetic_corpus.total={syn['total']} but {syn['variants_per_format']} "
        f"variants × {len(syn['formats'])} formats = {expected}"
    )


def test_manifest_has_expected_synthetic_formats(manifest):
    syn_formats = set(manifest["synthetic_corpus"]["formats"])
    required = {"docx", "xlsx", "pptx", "html", "csv", "json", "xml", "txt", "md", "zip"}
    missing = required - syn_formats
    assert not missing, f"Missing synthetic formats: {missing}"


def test_manifest_variant_labels_cover_all_formats(manifest):
    syn = manifest["synthetic_corpus"]
    labels = syn.get("variant_labels", {})
    for fmt in syn["formats"]:
        assert fmt in labels, f"No variant_labels for format {fmt!r}"
        assert len(labels[fmt]) == syn["variants_per_format"], (
            f"Format {fmt!r} has {len(labels[fmt])} labels, "
            f"expected {syn['variants_per_format']}"
        )


def test_manifest_path_template_contains_placeholders(manifest):
    template = manifest["synthetic_corpus"]["local_path_template"]
    assert "{format}" in template
    assert "{variant" in template
    assert "{ext}" in template


def test_manifest_license_note_mentions_cc_by_sa(manifest):
    assert "CC-BY-SA-4.0" in manifest["license_note"]


# ── build_entries — runner entry construction ─────────────────────────────────

def test_build_entries_full_count(manifest):
    from benchmarks.run_public_benchmark import _build_entries
    entries = _build_entries(manifest, smoke=False)
    pdf_count = len(manifest["pdf_corpus"]["files"])
    syn_count = manifest["synthetic_corpus"]["total"]
    assert len(entries) == pdf_count + syn_count


def test_build_entries_smoke_count(manifest):
    from benchmarks.run_public_benchmark import _build_entries
    entries = _build_entries(manifest, smoke=True)
    smoke_pdf = len(manifest["pdf_corpus"]["smoke_ids"])
    smoke_syn = manifest["synthetic_corpus"]["smoke_total"]
    assert len(entries) == smoke_pdf + smoke_syn


def test_build_entries_max_pdfs(manifest):
    from benchmarks.run_public_benchmark import _build_entries
    entries = _build_entries(manifest, max_pdfs=5)
    pdf_entries = [e for e in entries if e["format"] == "pdf"]
    assert len(pdf_entries) == 5


def test_build_entries_synthetic_path_matches_template(manifest):
    from benchmarks.run_public_benchmark import _build_entries
    entries = _build_entries(manifest, smoke=False)
    template = manifest["synthetic_corpus"]["local_path_template"]
    for e in entries:
        if e["source"] == "synthetic":
            fmt = e["format"]
            v = int(e["id"].split("-")[-1])
            expected = template.format(format=fmt, variant=v, ext=fmt)
            assert e["local_path"] == expected, (
                f"Entry {e['id']} has local_path={e['local_path']!r}, "
                f"expected {expected!r}"
            )


def test_build_entries_ids_are_unique(manifest):
    from benchmarks.run_public_benchmark import _build_entries
    entries = _build_entries(manifest)
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "Duplicate IDs in _build_entries output"


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


def test_synthetic_html_variants_differ(tmp_path):
    from benchmarks.build_public_corpus import _build_html
    variants = {}
    for v in range(1, 11):
        dest = tmp_path / f"test-{v}.html"
        _build_html(dest, variant=v)
        variants[v] = dest.read_text(encoding="utf-8")
    assert len(set(variants.values())) > 1, "All HTML variants produced identical output"


def test_synthetic_csv_created(tmp_path):
    from benchmarks.build_public_corpus import _build_csv
    dest = tmp_path / "test.csv"
    _build_csv(dest)
    assert dest.exists()
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    assert "id" in lines[0].lower() or "format" in lines[0].lower()


def test_synthetic_csv_large_variant(tmp_path):
    from benchmarks.build_public_corpus import _build_csv
    dest = tmp_path / "large.csv"
    _build_csv(dest, variant=4)
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 51, f"large-50rows variant should have 50+ data rows, got {len(lines) - 1}"


def test_synthetic_json_created(tmp_path):
    from benchmarks.build_public_corpus import _build_json
    dest = tmp_path / "test.json"
    _build_json(dest)
    assert dest.exists()
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert isinstance(data, (dict, list))


def test_synthetic_json_array_variant(tmp_path):
    from benchmarks.build_public_corpus import _build_json
    dest = tmp_path / "array.json"
    _build_json(dest, variant=3)
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 5


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


def test_synthetic_txt_variants_differ(tmp_path):
    from benchmarks.build_public_corpus import _build_txt
    contents = set()
    for v in range(1, 11):
        dest = tmp_path / f"txt-{v}.txt"
        _build_txt(dest, variant=v)
        contents.add(dest.read_text(encoding="utf-8"))
    assert len(contents) > 1, "All TXT variants produced identical output"


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


def test_synthetic_zip_minimal_variant(tmp_path):
    import zipfile as _zipfile

    from benchmarks.build_public_corpus import _build_zip
    dest = tmp_path / "minimal.zip"
    _build_zip(dest, variant=10)
    assert dest.exists()
    with _zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert len(names) == 1


def test_build_dry_run_no_files_written(tmp_path, monkeypatch):
    """Dry-run mode must not write any files."""
    import shutil

    import benchmarks.build_public_corpus as bpc
    monkeypatch.setattr(bpc, "BENCHMARKS", tmp_path)
    manifest_copy = BENCHMARKS / "public_corpus_manifest.json"
    shutil.copy(manifest_copy, tmp_path / "public_corpus_manifest.json")
    monkeypatch.setattr(bpc, "MANIFEST_PATH", tmp_path / "public_corpus_manifest.json")

    counts = bpc.build(dry_run=True, smoke=True)
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
        "local_path": "synthetic/txt/does_not_exist.txt",
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
    syn_dir = corpus_root / "synthetic" / "txt"
    syn_dir.mkdir(parents=True)
    txt_path = syn_dir / "sample-01.txt"
    _build_txt(txt_path)

    entry = {
        "id": "syn-txt-01",
        "label": "paragraphs",
        "format": "txt",
        "source": "synthetic",
        "local_path": "synthetic/txt/sample-01.txt",
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
        {
            "id": "pdf-001", "label": "minimal-pdflatex", "format": "pdf",
            "source": "py-pdf/sample-files", "outcome": "success",
            "expected_outcome": "success", "block_count": 5, "output_chars": 300,
            "estimated_tokens": 75, "elapsed_seconds": 0.12, "errors": [], "warnings": [],
        },
        {
            "id": "pdf-005", "label": "libreoffice-encrypted", "format": "pdf",
            "source": "py-pdf/sample-files", "outcome": "error",
            "expected_outcome": "error",
            "errors": [{"code": "PDF_ENCRYPTED", "message": "encrypted"}],
            "elapsed_seconds": 0.04, "warnings": [],
        },
        {
            "id": "syn-docx-01", "label": "headings-paragraphs", "format": "docx",
            "source": "synthetic", "outcome": "success", "expected_outcome": "success",
            "block_count": 8, "output_chars": 450, "estimated_tokens": 112,
            "elapsed_seconds": 0.08, "errors": [], "warnings": [],
        },
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
