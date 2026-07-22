"""Cache round-trip tests."""
from __future__ import annotations

from pathlib import Path

from benchmarks.ocr_auto_calibration import cache as cache_mod
from benchmarks.ocr_auto_calibration.schema import RunKey, RunResult


def _key(commit: str = "commit-a") -> RunKey:
    return RunKey(
        document_id="doc1",
        treatment="tesseract",
        aksharamd_commit=commit,
        model_revision="rev-x",
        harness_schema_version="1",
    )


def _result(key: RunKey) -> RunResult:
    return RunResult(
        key=key,
        document_path="/tmp/doc.pdf",
        document_sha256="doc-sha",
        profile_class="test",
        total_pages=3,
        ocr_required_pages=1,
        ocr_required_fraction=0.33,
        auto_preferred_backend=None,
        auto_selected_backend=None,
        fallback_reason=None,
        exit_status=0,
        runtime_seconds=1.5,
        peak_vram_mib=None,
        output_sha256="out-sha",
        readiness_score=90,
        quality_band="Ready",
        warning_codes=["OK"],
        output_markdown_length=42,
        output_paragraph_count=1,
        output_heading_count=1,
        output_image_ref_count=0,
        output_table_count=0,
        max_repeated_ngram_count=1,
        repetition_flag=False,
        source_page_provenance_complete=True,
        stderr_tail="",
        error_message=None,
    )


def test_cache_key_stable_across_calls() -> None:
    k = _key()
    key_one = cache_mod.cache_key_for("doc-sha", k)
    key_two = cache_mod.cache_key_for("doc-sha", k)
    assert key_one == key_two


def test_different_commit_produces_different_key() -> None:
    a = cache_mod.cache_key_for("doc-sha", _key("commit-a"))
    b = cache_mod.cache_key_for("doc-sha", _key("commit-b"))
    assert a != b


def test_different_document_sha_produces_different_key() -> None:
    k = _key()
    a = cache_mod.cache_key_for("sha-1", k)
    b = cache_mod.cache_key_for("sha-2", k)
    assert a != b


def test_store_and_load_round_trips(tmp_path: Path) -> None:
    k = _key()
    result = _result(k)
    cache_mod.store("doc-sha", result, cache_dir=tmp_path)
    reloaded = cache_mod.load("doc-sha", k, cache_dir=tmp_path)
    assert reloaded is not None
    assert reloaded.key == k
    assert reloaded.readiness_score == 90
    assert reloaded.runtime_seconds == 1.5


def test_load_returns_none_on_missing_key(tmp_path: Path) -> None:
    result = cache_mod.load("doc-sha", _key(), cache_dir=tmp_path)
    assert result is None


def test_load_returns_none_on_corrupted_file(tmp_path: Path) -> None:
    k = _key()
    path = cache_mod.cache_path_for("doc-sha", k, cache_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not json at all!!!", encoding="utf-8")
    assert cache_mod.load("doc-sha", k, cache_dir=tmp_path) is None


def test_load_returns_none_on_schema_mismatch(tmp_path: Path) -> None:
    k = _key()
    path = cache_mod.cache_path_for("doc-sha", k, cache_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Valid JSON but missing required keys -> treated as miss.
    path.write_text('{"foo": "bar"}', encoding="utf-8")
    assert cache_mod.load("doc-sha", k, cache_dir=tmp_path) is None
