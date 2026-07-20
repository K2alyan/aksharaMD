"""A1c — module-local eval override + malicious-output regression tests
+ forced use_safetensors=True.

Uses fake modules (types.ModuleType) rather than importing the real
downloaded ``modeling_unlimitedocr``. That way the tests run in CI
without the 6.67 GB weights present, and they never execute any code
from the trust-remote-code surface.
"""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from aksharamd.plugins.ocr_backends.eval_override import (
    MODELING_UNLIMITEDOCR_BASELINE,
    OverrideNotActive,
    count_risky_calls,
    install_module_local_eval_override,
)

# ── install_module_local_eval_override ──────────────────────────────────


def _make_fake_module(name: str = "fake_modeling") -> types.ModuleType:
    """Fresh module with builtins.eval resolvable via LEGB."""
    return types.ModuleType(name)


def test_override_installs_ast_literal_eval():
    mod = _make_fake_module()
    install_module_local_eval_override(mod)
    assert mod.eval is ast.literal_eval


def test_override_is_module_local_only():
    """Installing the override on one module must not affect another."""
    mod_a = _make_fake_module("mod_a")
    mod_b = _make_fake_module("mod_b")
    install_module_local_eval_override(mod_a)
    assert mod_a.eval is ast.literal_eval
    # mod_b was not overridden. It has no local `eval` attribute; a
    # function defined inside it that calls `eval(...)` would resolve
    # to builtins.eval via LEGB — not our override.
    assert not hasattr(mod_b, "eval")


def test_override_shadows_builtin_for_unqualified_lookups():
    """The critical property: unqualified ``eval(...)`` inside the
    module resolves to the overridden version, not builtins.eval."""
    mod = _make_fake_module()
    src = "def parse(s):\n    return eval(s)\n"
    exec(compile(src, "<test>", "exec"), mod.__dict__)  # noqa: S102 - test setup only
    # Before override: uses builtins.eval — will execute arbitrary code.
    # This would be a hazard in production; we sanity-check via a
    # benign expression only.
    assert mod.parse("1 + 1") == 2  # builtins.eval works on arithmetic
    # After override: only literals accepted.
    install_module_local_eval_override(mod)
    assert mod.parse("[1, 2, 3]") == [1, 2, 3]
    with pytest.raises((ValueError, SyntaxError)):
        mod.parse("1 + 1")  # ast.literal_eval refuses arithmetic


# ── Malicious-output regression tests ───────────────────────────────────


_MALICIOUS_PAYLOADS = [
    # Direct arbitrary-code invocation attempts.
    "__import__('os').system('echo unsafe')",
    "os.system('rm -rf /')",
    "exec('print(1)')",
    "()__class__.__bases__[0].__subclasses__()",
    # Coordinate-tuple shape (line 1112/1113 site input): benign-looking
    # tuple but with hidden call.
    "(1, __import__('os').getcwd())",
    # dict-literal shape (line 1099 site input).
    "{'Line': __import__('os').environ}",
    # Endpoint string (line 1128 site input).
    "'name': __import__('os').listdir('.')",
    # Attribute-access forms.
    "().__class__.__mro__[1].__subclasses__()",
    # String concatenation that decodes to an import.
    "chr(95)*2 + 'import' + chr(95)*2 + '(\\'os\\')'",
]


@pytest.mark.parametrize("payload", _MALICIOUS_PAYLOADS)
def test_malicious_payload_refused_after_override(payload):
    """Every payload must raise a controlled exception (ValueError or
    SyntaxError) rather than execute. If ast.literal_eval ever accepts
    one as a literal, that's the test we WANT to fail loudly."""
    mod = _make_fake_module()
    src = "def parse_ref(s):\n    return eval(s)\n"
    exec(compile(src, "<test>", "exec"), mod.__dict__)  # noqa: S102 - test setup only
    install_module_local_eval_override(mod)
    with pytest.raises((ValueError, SyntaxError, TypeError)):
        mod.parse_ref(payload)


def test_literal_shapes_still_accepted():
    """Legitimate model output that looks like literals must still
    parse — otherwise the override breaks normal inference paths.
    Each shape mirrors one of the 7 real eval-site call patterns."""
    mod = _make_fake_module()
    src = "def parse(s):\n    return eval(s)\n"
    exec(compile(src, "<test>", "exec"), mod.__dict__)  # noqa: S102 - test setup only
    install_module_local_eval_override(mod)
    # Line 66 site: cor_list = eval(ref_text[2]) — expects a list.
    assert mod.parse("[1.0, 2.0, 3.0, 4.0]") == [1.0, 2.0, 3.0, 4.0]
    # Line 1099 site: eval(outputs) — expects a dict.
    assert mod.parse("{'Line': {'line': [1, 2], 'line_type': ['-']}}") == {
        "Line": {"line": [1, 2], "line_type": ["-"]}
    }
    # Line 1112 site: eval(line.split(' -- ')[0]) — expects a tuple.
    assert mod.parse("(1, 2)") == (1, 2)
    # Nested literals.
    assert mod.parse("[(1, 2), (3, 4)]") == [(1, 2), (3, 4)]


# ── Fail-closed baseline check ──────────────────────────────────────────


def test_override_refuses_when_source_baseline_drifts(tmp_path: Path):
    """If ``source_path`` + ``baseline_counts`` are supplied, the
    override refuses when the counts don't match — defense-in-depth
    on top of SHA-256 file verification."""
    mod = _make_fake_module()
    fake_src = tmp_path / "modeling.py"
    # Baseline says 7 evals; source has 8 → refuse.
    fake_src.write_text(
        "\n".join([f"x = eval('[1, {i}]')" for i in range(8)]),
        encoding="utf-8",
    )
    with pytest.raises(OverrideNotActive, match="baseline drift"):
        install_module_local_eval_override(
            mod,
            baseline_counts={"eval": 7, "exec": 0, "compile": 0, "__import__": 0},
            source_path=fake_src,
        )


def test_override_accepts_when_source_baseline_matches(tmp_path: Path):
    mod = _make_fake_module()
    fake_src = tmp_path / "modeling.py"
    # Exactly 7 evals, 0 exec/compile/__import__.
    body = "\n".join([f"x = eval('[1, {i}]')" for i in range(7)])
    fake_src.write_text(body, encoding="utf-8")
    install_module_local_eval_override(
        mod,
        baseline_counts=MODELING_UNLIMITEDOCR_BASELINE,
        source_path=fake_src,
    )
    assert mod.eval is ast.literal_eval


def test_override_refuses_on_missing_source_file(tmp_path: Path):
    mod = _make_fake_module()
    with pytest.raises(OverrideNotActive, match="cannot read module source"):
        install_module_local_eval_override(
            mod,
            baseline_counts=MODELING_UNLIMITEDOCR_BASELINE,
            source_path=tmp_path / "does_not_exist.py",
        )


# ── count_risky_calls ───────────────────────────────────────────────────


def test_count_risky_calls_matches_baseline_for_placeholder():
    """Direct baseline check: a synthesized file with exactly the
    expected counts round-trips."""
    src = "\n".join([
        *["x = eval('[1, 2]')" for _ in range(7)],
        # 0 exec / compile / __import__
    ])
    counts = count_risky_calls(src)
    for k, v in MODELING_UNLIMITEDOCR_BASELINE.items():
        assert counts[k] == v, f"{k}: expected {v}, got {counts[k]}"


def test_count_risky_calls_ignores_method_calls():
    """``x.eval()`` and ``foo.exec()`` are method calls on an object,
    not the builtins we care about. The regex must not match them."""
    src = "self.eval()\nsome.exec()\nre.compile('x')\nfoo.__import__('bar')"
    counts = count_risky_calls(src)
    assert counts["eval"] == 0
    assert counts["exec"] == 0
    # compile alone is expected in re.compile — accept the false positive
    # is possible for `compile(` unqualified; but here it's `re.compile`
    # which the regex rejects.
    assert counts["compile"] == 0
    assert counts["__import__"] == 1  # __import__( always looks the same


def test_count_risky_calls_ignores_hash_comments():
    """Naive # stripping: a `# call eval(x)` in a comment should not
    contribute to the count."""
    src = "# this comment mentions eval(x)\n# and exec(y)\nx = 1"
    counts = count_risky_calls(src)
    assert counts["eval"] == 0
    assert counts["exec"] == 0


def test_count_risky_calls_finds_all_seven_in_synthetic_source():
    """Full baseline: mirror the exact call-site shapes from the real
    modeling_unlimitedocr.py."""
    src = """
cor_list = eval(ref_text[2])
if 'line_type' in outputs:
    lines = eval(outputs)['Line']['line']
    line_type = eval(outputs)['Line']['line_type']
    endpoints = eval(outputs)['Line']['line_endpoint']
    for line in lines:
        p0 = eval(line.split(' -- ')[0])
        p1 = eval(line.split(' -- ')[-1])
    for endpoint in endpoints:
        (x, y) = eval(endpoint.split(': ')[1])
"""
    counts = count_risky_calls(src)
    assert counts["eval"] == 7
    assert counts["exec"] == 0
    assert counts["compile"] == 0
    assert counts["__import__"] == 0


# ── Real-file consistency (only runs when the snapshot is downloaded) ──


# Resolve the real HF cache location at import time — the autouse
# conftest fixture patches USERPROFILE/HOME per-test for ledger
# isolation, which would otherwise redirect Path.home() to a tmp dir
# and cause these tests to always skip.
_REAL_HF_HOME = Path(__file__).resolve().home()
_REAL_CACHE_ROOT = _REAL_HF_HOME / ".cache" / "huggingface" / "hub"


def _snapshot_modeling_path() -> Path | None:
    import os
    cache_root = Path(os.environ.get("HF_HOME") or _REAL_CACHE_ROOT)
    p = (cache_root / "models--baidu--Unlimited-OCR" / "snapshots"
         / "d549bb9d6a055dbe291408916d66acc2cd5920f6" / "modeling_unlimitedocr.py")
    return p if p.exists() else None


def test_real_modeling_unlimitedocr_matches_baseline_when_downloaded():
    """When the reviewer has the snapshot present locally, verify the
    real modeling_unlimitedocr.py exactly matches the static-review
    baseline counts. Skipped in CI (weights not downloaded)."""
    p = _snapshot_modeling_path()
    if p is None:
        pytest.skip("Unlimited-OCR snapshot not downloaded")
    src = p.read_text(encoding="utf-8", errors="replace")
    counts = count_risky_calls(src)
    for name, expected in MODELING_UNLIMITEDOCR_BASELINE.items():
        assert counts[name] == expected, (
            f"{name}: expected {expected}, got {counts[name]} — "
            "static-review baseline has drifted vs the downloaded source"
        )


# ── Forced use_safetensors=True ─────────────────────────────────────────


def test_adapter_source_forces_use_safetensors_true():
    """The adapter must never call AutoModel.from_pretrained without
    use_safetensors=True. This test greps the adapter source to
    prevent regressions that would let a future maintainer accept
    pickle-based weights."""
    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "use_safetensors=True" in src, (
        "adapter source must contain use_safetensors=True on the AutoModel load path"
    )
    # And the negative form must be absent.
    assert "use_safetensors=False" not in src


# ── Safetensors index consistency (only when snapshot downloaded) ──────


def test_safetensors_index_references_only_approved_shard_when_downloaded():
    """When the snapshot is present locally, verify every tensor
    entry in model.safetensors.index.json points to the single shard
    listed as ``class: weights`` in the runtime trusted manifest.
    Skipped in CI (weights not downloaded)."""
    import json
    p = _snapshot_modeling_path()
    if p is None:
        pytest.skip("Unlimited-OCR snapshot not downloaded")
    from aksharamd.plugins.ocr_backends import UNLIMITED_OCR_TRUSTED_MANIFEST_PATH
    manifest = json.loads(UNLIMITED_OCR_TRUSTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    approved_shards = {
        rel for rel, meta in manifest["files"].items()
        if meta.get("class") == "weights"
    }
    assert approved_shards, "trusted manifest declares no weights"
    idx_path = p.parent / "model.safetensors.index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    referenced = set(idx.get("weight_map", {}).values())
    assert referenced.issubset(approved_shards), (
        f"safetensors index references shards not in trusted manifest: "
        f"{referenced - approved_shards}"
    )
