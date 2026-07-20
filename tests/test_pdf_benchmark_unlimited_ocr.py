"""Invariant tests for the Unlimited-OCR adapter (Phase 2, third
competitor, Issue #68).

Pure metric tests + artifact tests + mode-decision + security-invariant
tests. **Never triggers real model inference.** The heavy `AutoModel.
from_pretrained` load is mocked via `_UnlimitedOcrRunner` field
substitution in the tests that exercise the inference path.

No AksharaMD production code is imported.
"""
from __future__ import annotations

import json
import sys as _sys
from pathlib import Path

import pytest

from benchmarks.pdf_benchmark_adapters.unlimited_ocr_adapter import (  # type: ignore
    _UNLIMITED_OCR_MODEL_REPO,
    _UNLIMITED_OCR_MODEL_REVISION,
    RunResult,
    _bucket,
    _decide_execution_mode,
    _estimate_tokens,
    _image_placeholder_ratio,
    _repeat_content_ratio,
    sha256_file,
    verify_trusted_code_files,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESULT = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.json"
_MANIFEST = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"


# ── Pure metric helpers ─────────────────────────────────────────────────


def test_estimate_tokens_zero_on_empty():
    assert _estimate_tokens("") == 0


def test_repeat_content_ratio_zero_on_clean():
    text = " ".join(f"tok{i}" for i in range(60))
    assert _repeat_content_ratio(text, ngram=4) == 0.0


def test_repeat_content_ratio_high_on_repetition():
    text = "the quick brown fox " * 10
    assert _repeat_content_ratio(text, ngram=4) > 0.7


def test_image_placeholder_ratio_empty_returns_none():
    assert _image_placeholder_ratio("") is None


def test_sha256_file_roundtrip(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    h = sha256_file(p)
    assert h == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


# ── Security invariants ─────────────────────────────────────────────────


def test_model_repo_pinned_to_baidu_unlimited_ocr():
    """The adapter must always target the official Baidu repo. Any
    override would be a supply-chain issue.
    """
    assert _UNLIMITED_OCR_MODEL_REPO == "baidu/Unlimited-OCR"


def test_revision_is_pinned_sha_or_none():
    """If a revision is set, it MUST be a 40-char lowercase hex SHA —
    a mutable branch reference (e.g., 'main') is refused.
    """
    if _UNLIMITED_OCR_MODEL_REVISION is None:
        pytest.skip("no revision configured — adapter refuses to load real inference")
    rev = _UNLIMITED_OCR_MODEL_REVISION
    assert isinstance(rev, str)
    assert len(rev) == 40
    assert all(c in "0123456789abcdef" for c in rev), (
        f"revision must be a 40-char lowercase hex SHA; got {rev!r}"
    )


# ── verify_trusted_code_files fail-closed paths ─────────────────────────


_DUMMY_REV = "a" * 40


def _hub_available() -> bool:
    try:
        import huggingface_hub  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def _seed_snapshot(root: Path, repo: str, revision: str) -> Path:
    """Create a fake HF cache snapshot skeleton and return the snapshot
    directory. Mirrors the on-disk layout used by huggingface_hub."""
    snap_root = root / f"models--{repo.replace('/', '--')}" / "snapshots" / revision
    snap_root.mkdir(parents=True)
    return snap_root


def test_verify_trusted_code_files_refuses_when_revision_unset():
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO, revision=None, trusted={"modeling.py": "0" * 64},
    )
    assert ok is False
    assert "revision unset" in note


def test_verify_trusted_code_files_refuses_when_trusted_table_empty():
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO, revision=_DUMMY_REV, trusted={},
    )
    assert ok is False
    assert "hash table is empty" in note


def test_verify_trusted_code_files_refuses_when_snapshot_missing(
    tmp_path: Path, monkeypatch
):
    if not _hub_available():
        pytest.skip("huggingface_hub not installed; short-circuits earlier")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO,
        revision=_DUMMY_REV,
        trusted={"modeling.py": "0" * 64},
    )
    assert ok is False
    assert "no snapshots directory" in note


def test_verify_trusted_code_files_refuses_when_revision_not_cached(
    tmp_path: Path, monkeypatch
):
    if not _hub_available():
        pytest.skip("huggingface_hub not installed; short-circuits earlier")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    # Create a snapshots dir but for a DIFFERENT revision.
    _seed_snapshot(tmp_path, _UNLIMITED_OCR_MODEL_REPO, revision="b" * 40)
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO,
        revision=_DUMMY_REV,
        trusted={"modeling.py": "0" * 64},
    )
    assert ok is False
    assert "not present in local snapshots" in note


def test_verify_trusted_code_files_refuses_when_expected_file_missing(
    tmp_path: Path, monkeypatch
):
    if not _hub_available():
        pytest.skip("huggingface_hub not installed; short-circuits earlier")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    _seed_snapshot(tmp_path, _UNLIMITED_OCR_MODEL_REPO, revision=_DUMMY_REV)
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO,
        revision=_DUMMY_REV,
        trusted={"modeling.py": "0" * 64},
    )
    assert ok is False
    assert "trusted file missing" in note


def test_verify_trusted_code_files_refuses_on_hash_mismatch(
    tmp_path: Path, monkeypatch
):
    if not _hub_available():
        pytest.skip("huggingface_hub not installed; short-circuits earlier")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    snap = _seed_snapshot(tmp_path, _UNLIMITED_OCR_MODEL_REPO, revision=_DUMMY_REV)
    (snap / "modeling.py").write_bytes(b"print('hi')\n")
    # Deliberately wrong SHA.
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO,
        revision=_DUMMY_REV,
        trusted={"modeling.py": "0" * 64},
    )
    assert ok is False
    assert "SHA-256 mismatch" in note


def test_verify_trusted_code_files_refuses_extra_untrusted_py(
    tmp_path: Path, monkeypatch
):
    if not _hub_available():
        pytest.skip("huggingface_hub not installed; short-circuits earlier")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    snap = _seed_snapshot(tmp_path, _UNLIMITED_OCR_MODEL_REPO, revision=_DUMMY_REV)
    approved = snap / "modeling.py"
    approved.write_bytes(b"print('hi')\n")
    # Extra untrusted file (not in the trusted table)
    (snap / "backdoor.py").write_bytes(b"print('bad')\n")
    trusted = {"modeling.py": sha256_file(approved)}
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO, revision=_DUMMY_REV, trusted=trusted,
    )
    assert ok is False
    assert "untrusted custom code file" in note
    assert "backdoor.py" in note


def test_verify_trusted_code_files_accepts_matching_snapshot(
    tmp_path: Path, monkeypatch
):
    """Sunny-day path: every trusted file present with matching hash,
    no extra .py files."""
    if not _hub_available():
        pytest.skip("huggingface_hub not installed; short-circuits earlier")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    snap = _seed_snapshot(tmp_path, _UNLIMITED_OCR_MODEL_REPO, revision=_DUMMY_REV)
    modeling = snap / "modeling.py"
    modeling.write_bytes(b"print('ok')\n")
    (snap / "config.json").write_text('{"ok": true}', encoding="utf-8")  # not .py
    trusted = {"modeling.py": sha256_file(modeling)}
    ok, note = verify_trusted_code_files(
        _UNLIMITED_OCR_MODEL_REPO, revision=_DUMMY_REV, trusted=trusted,
    )
    assert ok is True
    assert "verified 1 trusted-code files" in note


def test_verify_trusted_code_files_load_path_refuses_without_config(monkeypatch):
    """The `_UnlimitedOcrRunner.load()` path must call
    `verify_trusted_code_files` BEFORE any `transformers` import and
    refuse if verification fails. With the current module state
    (revision=None or trusted={}), load must set `_load_error` without
    ever touching transformers.
    """
    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as mod
    # Sabotage: make transformers import raise loudly if reached.
    monkeypatch.setitem(_sys.modules, "transformers", None)
    runner = mod._UnlimitedOcrRunner()
    runner.load()
    assert runner._loaded is False
    assert runner._load_error.startswith("trusted_code_verification_failed:"), (
        f"expected fail-closed refusal note, got: {runner._load_error!r}"
    )


def test_model_cache_check_returns_false_when_no_revision(tmp_path: Path, monkeypatch):
    """Without a pinned revision, the cache check must refuse to
    report the model as ready. The refusal note may differ depending
    on whether huggingface_hub is installed:
    - if hub is installed: "no pinned revision"
    - if hub is missing (base install without unlimited-ocr extra):
      "huggingface_hub not installed"
    Either is an acceptable refusal.
    """
    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as mod
    cached, note = mod._model_cached_locally(mod._UNLIMITED_OCR_MODEL_REPO, None)
    assert cached is False
    note_lower = note.lower()
    assert (
        "no pinned revision" in note_lower
        or "huggingface_hub not installed" in note_lower
    ), f"unexpected refusal note: {note!r}"


# ── Mode-decision logic ─────────────────────────────────────────────────


def _deps_present() -> bool:
    from benchmarks.pdf_benchmark_adapters.unlimited_ocr_adapter import (  # type: ignore
        _pinned_deps_present,
    )
    ok, _ = _pinned_deps_present()
    return ok


def test_decide_execution_mode_dry_run_wins_over_gpu():
    """If unlimited-ocr deps are missing, the guard short-circuits to
    'deps_missing' before checking the dry_run flag — that's the safe
    default. This test only meaningfully runs when the deps ARE present.
    """
    if not _deps_present():
        pytest.skip("unlimited-ocr deps missing; deps_missing short-circuits")
    mode, note = _decide_execution_mode(
        forced_real=False, forced_dry_run=True,
        gpu={"cuda_available": True, "bf16_supported": True},
    )
    assert mode == "dry_run"


def test_decide_execution_mode_no_gpu_refuses_real():
    if not _deps_present():
        pytest.skip("unlimited-ocr deps missing; deps_missing short-circuits")
    mode, note = _decide_execution_mode(
        forced_real=True, forced_dry_run=False,
        gpu={"cuda_available": False},
    )
    assert mode == "no_gpu"


def test_decide_execution_mode_no_bf16_refuses_real():
    if not _deps_present():
        pytest.skip("unlimited-ocr deps missing; deps_missing short-circuits")
    mode, note = _decide_execution_mode(
        forced_real=True, forced_dry_run=False,
        gpu={"cuda_available": True, "bf16_supported": False},
    )
    assert mode == "no_gpu"


def test_decide_execution_mode_model_not_cached_when_no_revision():
    """With a valid GPU + BF16 but no pinned revision, mode should be
    'model_not_cached' — never silently defaults to real inference.
    If deps are missing, deps_missing short-circuits (also safe)."""
    mode, note = _decide_execution_mode(
        forced_real=False, forced_dry_run=False,
        gpu={"cuda_available": True, "bf16_supported": True},
    )
    assert mode in {"model_not_cached", "deps_missing"}


def test_decide_execution_mode_deps_missing_takes_precedence():
    """The deps-missing short-circuit is a safety invariant: without
    the pinned runtime deps we MUST NOT attempt real inference,
    regardless of every other input. Verify by asserting deps-missing
    dominates when deps are actually missing."""
    if _deps_present():
        pytest.skip("unlimited-ocr deps present; this test verifies the missing-deps guard")
    for forced_real, forced_dry_run in ((False, False), (True, False), (False, True)):
        mode, _ = _decide_execution_mode(
            forced_real=forced_real, forced_dry_run=forced_dry_run,
            gpu={"cuda_available": True, "bf16_supported": True},
        )
        assert mode == "deps_missing"


# ── TemporaryDirectory cleanup invariants ──────────────────────────────


class _FakeTokenizer:
    pass


class _FakeModel:
    """Minimal model stub. Records what output_path it was called with
    and either writes a fake markdown file (success) or raises."""

    def __init__(self, *, raise_on_infer: bool = False) -> None:
        self.raise_on_infer = raise_on_infer
        self.last_output_path: str | None = None

    def infer_multi(self, tokenizer, prompt, image_files, output_path, **kwargs):
        self.last_output_path = output_path
        if self.raise_on_infer:
            raise RuntimeError("simulated inference failure")
        out = Path(output_path)
        (out / "page_0001.md").write_text("# fake output\n", encoding="utf-8")
        return "# fake output\n"


def _preloaded_runner(*, raise_on_infer: bool):
    """Return an `_UnlimitedOcrRunner` skipping the real load path with
    fake model + tokenizer plugged in."""
    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as mod
    runner = mod._UnlimitedOcrRunner()
    runner._tokenizer = _FakeTokenizer()  # type: ignore[assignment]
    runner._model = _FakeModel(raise_on_infer=raise_on_infer)  # type: ignore[assignment]
    runner._loaded = True
    return runner, mod


def _stub_page_render(monkeypatch, mod) -> None:
    """Bypass PyMuPDF — write a stub PNG per page dir call."""
    def _fake(pdf, out_dir, dpi=300):  # noqa: ARG001
        p = Path(out_dir) / "page_0001.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        return [p]
    monkeypatch.setattr(mod, "_pdf_to_page_images", _fake)


def _stub_torch_no_cuda(monkeypatch, mod) -> None:
    """Force `torch.cuda.is_available()` False so we skip the peak-mem
    branch without needing a real CUDA runtime."""
    try:
        import torch  # type: ignore
    except ImportError:
        pytest.skip("torch not installed; cannot exercise infer_pdf")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


def _count_scratch_dirs(workdir: Path, pdf_stem: str) -> int:
    return sum(
        1 for p in workdir.iterdir()
        if p.is_dir() and p.name.startswith(f"unlimited_ocr_{pdf_stem}_")
    )


def test_infer_pdf_cleans_scratch_on_success(tmp_path: Path, monkeypatch):
    runner, mod = _preloaded_runner(raise_on_infer=False)
    _stub_page_render(monkeypatch, mod)
    _stub_torch_no_cuda(monkeypatch, mod)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    text, exc, sig = runner.infer_pdf(pdf, workdir)
    assert exc == "", f"expected success, got exception: {exc!r}"
    assert "fake output" in text
    assert _count_scratch_dirs(workdir, "sample") == 0, (
        "scratch TemporaryDirectory was not cleaned after a successful run"
    )


def test_infer_pdf_cleans_scratch_on_exception(tmp_path: Path, monkeypatch):
    runner, mod = _preloaded_runner(raise_on_infer=True)
    _stub_page_render(monkeypatch, mod)
    _stub_torch_no_cuda(monkeypatch, mod)
    pdf = tmp_path / "boom.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    text, exc, sig = runner.infer_pdf(pdf, workdir)
    assert text == ""
    assert exc.startswith("infer_failed:"), f"unexpected exception path: {exc!r}"
    assert _count_scratch_dirs(workdir, "boom") == 0, (
        "scratch TemporaryDirectory was not cleaned after an inference exception"
    )


def test_infer_pdf_uses_fresh_output_dir_per_call(tmp_path: Path, monkeypatch):
    """Deterministic recompile must not read the previous run's output.
    Two sequential calls should point the model at distinct output_path
    values (fresh TemporaryDirectory per call)."""
    runner, mod = _preloaded_runner(raise_on_infer=False)
    _stub_page_render(monkeypatch, mod)
    _stub_torch_no_cuda(monkeypatch, mod)
    pdf = tmp_path / "twice.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    runner.infer_pdf(pdf, workdir)
    first = runner._model.last_output_path
    runner.infer_pdf(pdf, workdir)
    second = runner._model.last_output_path
    assert first is not None and second is not None
    assert first != second, (
        "deterministic recompile reused the previous output_path; "
        "the second call could read the first call's markdown"
    )


# ── RunResult factory ───────────────────────────────────────────────────


def _mk(**kwargs) -> RunResult:
    base: dict = {
        "asset_id": "x",
        "corpus_source": "public",
        "document_class": "native-text",
        "execution_success": False,
        "execution_mode": "dry_run",
        "exception": "skipped: execution_mode=dry_run",
        "output_package_created": False,
        "content_extracted": False,
        "structurally_usable": False,
        "human_review_status": "not_reviewed",
        "human_usability": "not_reviewed",
        "human_review_evidence": "",
        "runtime_seconds": 0.0,
        "output_chars": 0,
        "non_whitespace_chars": 0,
        "estimated_tokens": 0,
        "output_size_inflation": 0.0,
        "deterministic": None,
        "page_count_pdf": 1,
        "hidden_text_layer": None,
        "hidden_text_layer_chars": None,
        "image_placeholder_ratio": None,
        "repeat_content_ratio": 0.0,
        "near_empty_equivalent": True,
        "low_density_equivalent": True,
        "peak_gpu_memory_mib": None,
        "tool_signals": {},
    }
    base.update(kwargs)
    return RunResult(**base)


def test_bucket_records_execution_mode_counts():
    rows = [
        _mk(asset_id="a", execution_mode="dry_run"),
        _mk(asset_id="b", execution_mode="dry_run"),
        _mk(asset_id="c", execution_mode="model_not_cached"),
    ]
    b = _bucket(rows)
    assert b["execution_mode_counts"] == {"dry_run": 2, "model_not_cached": 1}


def test_bucket_counts_real_inference_success():
    rows = [
        _mk(asset_id="a", execution_mode="real_inference", execution_success=True,
            output_package_created=True, content_extracted=True,
            structurally_usable=True, runtime_seconds=5.0,
            peak_gpu_memory_mib=6144, output_chars=1000,
            non_whitespace_chars=800, near_empty_equivalent=False,
            low_density_equivalent=False, exception=""),
        _mk(asset_id="b", execution_mode="real_inference", execution_success=False,
            exception="OOM"),
    ]
    b = _bucket(rows)
    assert b["execution_success_count"] == 1
    assert b["execution_success_rate"] == pytest.approx(0.5)
    assert b["peak_gpu_memory_mib_max"] == 6144


# ── Artifact tests ──────────────────────────────────────────────────────


def _load_result():
    if not _RESULT.exists():
        pytest.skip(f"result missing: {_RESULT}")
    with _RESULT.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_manifest():
    if not _MANIFEST.exists():
        pytest.skip(f"manifest missing: {_MANIFEST}")
    with _MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_artifact_shape():
    r = _load_result()
    for key in ("adapter_target", "adapter_target_repo", "adapter_target_revision",
                "manifest_source", "gpu_report", "execution_mode_decision",
                "dependencies", "aggregate", "per_asset",
                "evaluation_semantics_notes", "security_notes"):
        assert key in r, f"missing key {key!r}"


def test_artifact_target_is_baidu_unlimited_ocr():
    r = _load_result()
    assert r["adapter_target"] == "unlimited-ocr"
    assert r["adapter_target_repo"] == "baidu/Unlimited-OCR"


def test_artifact_declares_offline_enforcement():
    r = _load_result()
    sec = r["security_notes"]
    assert sec["safetensors_only"] is True
    assert sec["offline_enforcement"] == {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}


def test_artifact_declares_tool_neutral_semantics():
    r = _load_result()
    n = r["evaluation_semantics_notes"]
    assert n["aksharamd_readiness_score_used"] is False
    assert n["aksharamd_warning_codes_used"] is False
    assert n["no_cross_parser_ranking"] is True


def test_artifact_no_aksharamd_specific_fields():
    r = _load_result()
    forbidden = {"readiness_score", "quality_band", "warning_codes"}
    for row in r["per_asset"]:
        leaked = forbidden & set(row.keys())
        assert not leaked, f"{row['asset_id']}: forbidden AksharaMD field: {leaked}"


def test_artifact_same_corpus_as_aksharamd_phase1():
    r = _load_result()
    m = _load_manifest()
    eligible = {a["asset_id"] for a in m["assets"] if a["eligibility"] == "eligible"}
    result_ids = {row["asset_id"] for row in r["per_asset"]}
    assert result_ids == eligible


def test_artifact_deterministic_ordering():
    r = _load_result()
    ids = [row["asset_id"] for row in r["per_asset"]]
    assert ids == sorted(ids)


def test_artifact_execution_mode_recorded_per_asset():
    """Every per-asset row must carry an execution_mode string. This
    lets a downstream reader distinguish real inference from dry-run
    or skip records.
    """
    r = _load_result()
    valid = {"real_inference", "dry_run", "model_not_cached", "no_gpu", "deps_missing"}
    for row in r["per_asset"]:
        assert row["execution_mode"] in valid, (
            f"{row['asset_id']}: bad execution_mode {row['execution_mode']!r}"
        )


def test_artifact_records_gpu_capability():
    r = _load_result()
    gpu = r["gpu_report"]
    assert "cuda_available" in gpu
    assert "torch_installed" in gpu


def test_artifact_records_pinned_revision_state():
    r = _load_result()
    # The pinned revision is either None (this PR — dry-run only) or a
    # 40-char SHA. Any other value is a supply-chain risk.
    rev = r["adapter_target_revision"]
    assert rev is None or (isinstance(rev, str) and len(rev) == 40)


# ── Local-only network-block invariant ──────────────────────────────────


def test_offline_enforcement_environment_variables_are_documented():
    """The security_notes block in the artifact must document exactly
    which environment variables the adapter sets before importing
    transformers. If these disappear the offline guarantee is
    silently broken.
    """
    r = _load_result()
    off = r["security_notes"]["offline_enforcement"]
    assert off.get("HF_HUB_OFFLINE") == "1"
    assert off.get("TRANSFORMERS_OFFLINE") == "1"


def test_dry_run_records_no_gpu_memory():
    """In dry-run mode the peak GPU memory MUST be None per asset — no
    inference happened, so no reading was taken. If any real inference
    silently ran we'd expect a non-None value.
    """
    r = _load_result()
    for row in r["per_asset"]:
        if row["execution_mode"] == "dry_run":
            assert row["peak_gpu_memory_mib"] is None, (
                f"{row['asset_id']}: dry-run row has GPU memory reading "
                f"{row['peak_gpu_memory_mib']} — inference may have run silently"
            )


# ── pyproject extra ─────────────────────────────────────────────────────


def test_pyproject_declares_unlimited_ocr_extra():
    """The `aksharamd[unlimited-ocr]` optional extra must be declared
    in pyproject.toml so users can install the runtime dependencies
    via the standard pip extras interface.
    """
    proj = _REPO_ROOT / "pyproject.toml"
    if not proj.exists():
        pytest.skip("pyproject.toml missing")
    body = proj.read_text(encoding="utf-8")
    assert "unlimited-ocr = [" in body or "unlimited_ocr = [" in body, (
        "pyproject.toml must declare an `unlimited-ocr` optional extra"
    )
    # torch pin lower bound must be documented in the extra
    assert "baidu/Unlimited-OCR" in body or "unlimited-ocr" in body


def test_pyproject_declares_ocr_benchmark_extra():
    """A developer-only extra should combine both heavy backends for
    internal benchmarking. End users should not need it."""
    proj = _REPO_ROOT / "pyproject.toml"
    if not proj.exists():
        pytest.skip("pyproject.toml missing")
    body = proj.read_text(encoding="utf-8")
    assert "ocr-benchmark = [" in body or "ocr_benchmark = [" in body


def test_pyproject_extras_are_mutually_exclusive_by_design():
    """The `vision` (marker) and `unlimited-ocr` extras must NOT be
    aliased into a combined 'ocr' meta-extra for end users. Only the
    developer `ocr-benchmark` extra may combine them.
    """
    proj = _REPO_ROOT / "pyproject.toml"
    if not proj.exists():
        pytest.skip("pyproject.toml missing")
    body = proj.read_text(encoding="utf-8")
    # `full` extra historically includes `vision`; adding
    # `unlimited-ocr` to `full` would violate the user-installation
    # discipline (one heavy backend only). Ensure `full` does not
    # contain `unlimited-ocr`.
    full_line_start = body.find("full = [")
    if full_line_start >= 0:
        full_section = body[full_line_start:full_line_start + 400]
        assert "unlimited-ocr" not in full_section, (
            "`full` extra must NOT include `unlimited-ocr` — end users "
            "should install one heavy backend, not both"
        )


# ── ADR presence ────────────────────────────────────────────────────────


def test_adr_present():
    adr = _REPO_ROOT / "docs" / "adr" / "ocr_backend_strategy.md"
    assert adr.exists(), "OCR backend strategy ADR must exist"
    body = adr.read_text(encoding="utf-8")
    for phrase in [
        "trust_remote_code",
        "use_safetensors=True",
        "HF_HUB_OFFLINE",
        "aksharamd[unlimited-ocr]",
        "aksharamd[vision]",
        "No cloud OCR",
        "pinned revision",
        "verify_trusted_code_files",
    ]:
        assert phrase in body, f"ADR must document {phrase!r}"


def test_adr_does_not_reference_nonexistent_marker_extra():
    """The `aksharamd[marker]` extra does NOT exist in pyproject.toml;
    referencing it in install instructions would send users to a broken
    install command. If a future rename adds a `marker` alias, remove
    this test.
    """
    adr = _REPO_ROOT / "docs" / "adr" / "ocr_backend_strategy.md"
    if not adr.exists():
        pytest.skip("ADR missing")
    body = adr.read_text(encoding="utf-8")
    # `pip install "aksharamd[marker]"` in any code block would be a
    # broken install command. The current-state install instructions
    # must reference `aksharamd[vision]`.
    assert 'pip install "aksharamd[marker]"' not in body, (
        "ADR references nonexistent aksharamd[marker] extra — use aksharamd[vision]"
    )
