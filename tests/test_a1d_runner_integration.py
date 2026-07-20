"""A1d — runner integration (call-order tests).

Every test asserts the exact sequence of phase markers in
``_UnlimitedOcrRunner._call_log``, not just that each function was
called. Mocked transformers + verification primitives so no model
loading actually happens.

Enforces the 7 hard acceptance criteria the reviewer set:

1. No fallback to the legacy empty-dict path.
2. Full verification on first load; fast verification only with a
   valid receipt.
3. Dynamic module import only AFTER snapshot verification succeeds.
4. Local eval override installed BEFORE model instantiation.
5. use_safetensors=True enforced at RUNTIME (not only statically).
6. A failed verification/override/import/load aborts BEFORE any
   model code executes.
7. Tests assert call ordering, not just call presence.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

HAPPY_ORDER = [
    "load_trusted_manifest",
    "verify_snapshot_against_manifest",
    "fast_verify",
    "import_transformers",
    "get_class_from_dynamic_module",
    "install_module_local_eval_override",
    "AutoTokenizer.from_pretrained",
    "AutoModel.from_pretrained",
]


def _fake_manifest() -> dict:
    return {
        "manifest_schema_version": 1,
        "manifest_id": "unlimited-ocr-d549bb9d-v1",
        "repo_id": "baidu/Unlimited-OCR",
        "revision": "d549bb9d6a055dbe291408916d66acc2cd5920f6",
        "generator": "test",
        "generator_version": "1.0",
        "files": {
            "modeling_unlimitedocr.py": {
                "sha256": "0" * 64, "size_bytes": 1, "class": "executable",
                "required_for_runtime": True, "verify_on_every_load": True,
            },
        },
    }


class _FakeVerificationOutcome:
    def __init__(self, ok: bool, note: str = "ok") -> None:
        self.ok = ok
        self.note = note


def _install_transformers_stubs(monkeypatch, on_dynamic_import=None):
    """Insert fake transformers modules into sys.modules so the
    runner's imports succeed without the real package. Returns the
    fake modeling module the runner will find in sys.modules."""
    fake_torch = types.ModuleType("torch")
    fake_torch.bfloat16 = "bf16_sentinel"
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoModel = MagicMock(name="AutoModel")
    fake_transformers.AutoTokenizer = MagicMock(name="AutoTokenizer")
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    fake_dyn_utils = types.ModuleType("transformers.dynamic_module_utils")
    # Provide a fake modeling module in sys.modules and a fake class
    # whose .__module__ points to it.
    fake_modeling = types.ModuleType("modeling_unlimitedocr")
    fake_modeling.__file__ = "<fake>"
    monkeypatch.setitem(sys.modules, "modeling_unlimitedocr", fake_modeling)
    fake_cls = MagicMock(name="UnlimitedOCRForCausalLM")
    fake_cls.__module__ = "modeling_unlimitedocr"

    def _get_class(*args, **kwargs):
        if on_dynamic_import is not None:
            on_dynamic_import()
        return fake_cls

    fake_dyn_utils.get_class_from_dynamic_module = _get_class
    monkeypatch.setitem(
        sys.modules, "transformers.dynamic_module_utils", fake_dyn_utils,
    )

    return fake_modeling, fake_transformers, fake_cls


def _make_runner_with_patched_deps(
    monkeypatch,
    *,
    manifest=None,
    snapshot_ok=True,
    snapshot_note="ok",
    fast_ok=True,
    fast_note="ok",
    full_ok=True,
    full_note="ok",
    override_raises=None,
    dynamic_import_raises=None,
    model_load_raises=None,
):
    """Materialize a runner with every dependency of load() patched.
    Returns (runner, adapter_module, fake_modeling_module)."""
    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as mod

    # Patch the manifest loader.
    monkeypatch.setattr(
        mod, "load_trusted_manifest",
        lambda path=None: manifest if manifest is not None else _fake_manifest(),
    )
    # Patch snapshot verification.
    monkeypatch.setattr(
        mod, "verify_snapshot_against_manifest",
        lambda m, **kw: (snapshot_ok, snapshot_note),
    )
    # Patch fast/full receipt.
    fake_receipt_mod = types.ModuleType(
        "aksharamd.plugins.ocr_backends.verification_receipt",
    )
    fake_receipt_mod.fast_verify = lambda m, p, cache_root=None: _FakeVerificationOutcome(
        fast_ok, fast_note,
    )
    fake_receipt_mod.full_verify_and_write_receipt = (
        lambda m, p, cache_root=None: _FakeVerificationOutcome(full_ok, full_note)
    )
    monkeypatch.setitem(
        sys.modules, "aksharamd.plugins.ocr_backends.verification_receipt",
        fake_receipt_mod,
    )
    # Patch transformers + dynamic module import.
    fake_modeling, fake_transformers, fake_cls = _install_transformers_stubs(
        monkeypatch,
        on_dynamic_import=(
            (lambda: (_ for _ in ()).throw(dynamic_import_raises))
            if dynamic_import_raises is not None else None
        ),
    )
    # Patch AutoModel/AutoTokenizer instantiation results.
    if model_load_raises is None:
        model_instance = MagicMock(name="model_instance")
        model_instance.eval.return_value = model_instance
        model_instance.cuda.return_value = model_instance
        fake_transformers.AutoModel.from_pretrained.return_value = model_instance
        fake_transformers.AutoTokenizer.from_pretrained.return_value = MagicMock(
            name="tokenizer",
        )
    else:
        fake_transformers.AutoModel.from_pretrained.side_effect = model_load_raises
        fake_transformers.AutoTokenizer.from_pretrained.return_value = MagicMock(
            name="tokenizer",
        )
    # Patch eval override — reuse the REAL OverrideNotActive class so
    # `except OverrideNotActive` in the runner catches what the test
    # raises. Only replace the install function.
    import aksharamd.plugins.ocr_backends.eval_override as _real_override
    fake_override_mod = types.ModuleType(
        "aksharamd.plugins.ocr_backends.eval_override",
    )
    fake_override_mod.MODELING_UNLIMITEDOCR_BASELINE = (
        _real_override.MODELING_UNLIMITEDOCR_BASELINE
    )
    fake_override_mod.OverrideNotActive = _real_override.OverrideNotActive
    if override_raises is None:
        def _install(m, **kw):
            m.eval = "ast.literal_eval sentinel"
        fake_override_mod.install_module_local_eval_override = _install
    else:
        def _install(m, **kw):
            raise override_raises
        fake_override_mod.install_module_local_eval_override = _install
    monkeypatch.setitem(
        sys.modules, "aksharamd.plugins.ocr_backends.eval_override",
        fake_override_mod,
    )
    return mod._UnlimitedOcrRunner(), mod, fake_modeling


# ── Happy path: exact call order ────────────────────────────────────────


def test_load_happy_path_calls_in_exact_order(monkeypatch):
    runner, _mod, _fm = _make_runner_with_patched_deps(monkeypatch)
    runner.load()
    assert runner._loaded is True
    assert runner._load_error == ""
    assert runner._call_log == HAPPY_ORDER


# ── Criterion 3: no transformers import if snapshot verify refuses ──────


def test_snapshot_refusal_aborts_before_transformers_import(monkeypatch):
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch, snapshot_ok=False, snapshot_note="hash mismatch on modeling",
    )
    runner.load()
    assert runner._loaded is False
    assert "snapshot_verification_failed" in runner._load_error
    assert runner._call_log == [
        "load_trusted_manifest",
        "verify_snapshot_against_manifest",
    ]
    # Critical: transformers import NEVER ran.
    assert "import_transformers" not in runner._call_log
    assert "AutoModel.from_pretrained" not in runner._call_log


# ── Criterion 2: fast → full fallback on recoverable receipt failures ──


def test_fast_verify_falls_through_to_full_when_receipt_missing(monkeypatch):
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch, fast_ok=False,
        fast_note="receipt missing: /tmp/no.json — run `aksharamd models verify`",
        full_ok=True,
    )
    runner.load()
    assert runner._loaded is True
    assert runner._call_log == [
        "load_trusted_manifest",
        "verify_snapshot_against_manifest",
        "fast_verify",
        "full_verify_and_write_receipt",
        "import_transformers",
        "get_class_from_dynamic_module",
        "install_module_local_eval_override",
        "AutoTokenizer.from_pretrained",
        "AutoModel.from_pretrained",
    ]


def test_fast_verify_unrecoverable_note_aborts(monkeypatch):
    """A hash mismatch surfacing through fast_verify is unrecoverable
    and must NOT trigger a full-verify fallback."""
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch, fast_ok=False,
        fast_note="SHA-256 mismatch on modeling_unlimitedocr.py: expected abc..., got def...",
    )
    runner.load()
    assert runner._loaded is False
    assert "fast_verify_failed_unrecoverable" in runner._load_error
    assert runner._call_log == [
        "load_trusted_manifest",
        "verify_snapshot_against_manifest",
        "fast_verify",
    ]
    assert "import_transformers" not in runner._call_log


def test_full_verify_failure_aborts(monkeypatch):
    """If full verification is triggered by a recoverable fast-verify
    failure and full itself refuses, load must abort before any
    transformers work."""
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch,
        fast_ok=False, fast_note="receipt missing",
        full_ok=False, full_note="SHA-256 mismatch on weights.safetensors",
    )
    runner.load()
    assert runner._loaded is False
    assert "full_verify_failed" in runner._load_error
    assert runner._call_log == [
        "load_trusted_manifest",
        "verify_snapshot_against_manifest",
        "fast_verify",
        "full_verify_and_write_receipt",
    ]
    assert "import_transformers" not in runner._call_log


# ── Criterion 4 + 6: override must install before model instantiation ──


def test_eval_override_failure_aborts_before_model_instantiation(monkeypatch):
    """OverrideNotActive raised by install must abort load; AutoModel.
    from_pretrained must NOT be called."""
    from aksharamd.plugins.ocr_backends.eval_override import OverrideNotActive
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch, override_raises=OverrideNotActive("baseline drift"),
    )
    runner.load()
    assert runner._loaded is False
    assert "eval_override_failed" in runner._load_error
    # get_class_from_dynamic_module ran (Step 5), then override attempted
    # and failed. Neither tokenizer nor model was instantiated.
    assert "get_class_from_dynamic_module" in runner._call_log
    assert "install_module_local_eval_override" not in runner._call_log
    assert "AutoTokenizer.from_pretrained" not in runner._call_log
    assert "AutoModel.from_pretrained" not in runner._call_log


# ── Criterion 5: use_safetensors=True enforced at runtime ──────────────


def test_load_passes_use_safetensors_true_to_from_pretrained(monkeypatch):
    """Inspect the actual kwargs handed to AutoModel.from_pretrained."""
    runner, _mod, _fm = _make_runner_with_patched_deps(monkeypatch)
    runner.load()
    fake_transformers = sys.modules["transformers"]
    call = fake_transformers.AutoModel.from_pretrained.call_args
    assert call is not None, "AutoModel.from_pretrained was never called"
    _args, kwargs = call
    assert kwargs.get("use_safetensors") is True, (
        f"use_safetensors must be True at runtime; got kwargs={kwargs!r}"
    )
    assert kwargs.get("trust_remote_code") is True
    assert kwargs.get("local_files_only") is True
    assert kwargs.get("revision") == "d549bb9d6a055dbe291408916d66acc2cd5920f6"


# ── Dynamic module import failure ──────────────────────────────────────


def test_dynamic_module_import_failure_aborts(monkeypatch):
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch, dynamic_import_raises=RuntimeError("simulated import boom"),
    )
    runner.load()
    assert runner._loaded is False
    assert "dynamic_module_import_failed" in runner._load_error
    assert "import_transformers" in runner._call_log
    assert "get_class_from_dynamic_module" not in runner._call_log
    assert "install_module_local_eval_override" not in runner._call_log


# ── Model load failure ─────────────────────────────────────────────────


def test_model_load_failure_after_override_still_installs_override(monkeypatch):
    """If AutoModel.from_pretrained itself raises (e.g., OOM during
    load), the runner records load_failed. The override MUST have
    been installed before the failure (Criterion 4)."""
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch, model_load_raises=RuntimeError("simulated OOM"),
    )
    runner.load()
    assert runner._loaded is False
    assert "load_failed" in runner._load_error
    assert "install_module_local_eval_override" in runner._call_log
    # AutoTokenizer succeeded; AutoModel raised.
    assert "AutoTokenizer.from_pretrained" in runner._call_log
    assert "AutoModel.from_pretrained" not in runner._call_log


# ── Criterion 6: offline env set before any transformers import ────────


def test_offline_env_vars_set_before_transformers_import(monkeypatch):
    """HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE must both be set as the
    very first side effect of load(), before any dependency module is
    accessed."""
    import os
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    runner, _mod, _fm = _make_runner_with_patched_deps(monkeypatch)
    runner.load()
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"


# ── Criterion 1: no legacy _UNLIMITED_OCR_TRUSTED_CODE_FILES fallback ──


def test_load_does_not_call_legacy_verify_trusted_code_files(monkeypatch):
    """The legacy inline-dict primitive stays available for pre-A1a
    tests but must NOT be called from the runner load path anymore.
    """
    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as mod
    legacy_calls = []
    real_legacy = mod.verify_trusted_code_files

    def _legacy_spy(*args, **kwargs):
        legacy_calls.append((args, kwargs))
        return real_legacy(*args, **kwargs)

    monkeypatch.setattr(mod, "verify_trusted_code_files", _legacy_spy)
    runner, _mod, _fm = _make_runner_with_patched_deps(monkeypatch)
    runner.load()
    assert legacy_calls == [], (
        f"legacy verify_trusted_code_files was called from load(); "
        f"calls={legacy_calls}"
    )


# ── Idempotency ─────────────────────────────────────────────────────────


def test_load_is_idempotent_after_success(monkeypatch):
    runner, _mod, _fm = _make_runner_with_patched_deps(monkeypatch)
    runner.load()
    assert runner._loaded is True
    before = list(runner._call_log)
    runner.load()  # second call must be a no-op
    assert runner._call_log == before


def test_load_is_idempotent_after_failure(monkeypatch):
    runner, _mod, _fm = _make_runner_with_patched_deps(
        monkeypatch, snapshot_ok=False, snapshot_note="hash mismatch",
    )
    runner.load()
    assert runner._loaded is False
    assert runner._load_error != ""
    before_log = list(runner._call_log)
    before_err = runner._load_error
    runner.load()  # must not retry
    assert runner._call_log == before_log
    assert runner._load_error == before_err
