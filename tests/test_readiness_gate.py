from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler


@pytest.fixture
def compiler(tmp_path):
    return Compiler(output_dir=None)


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ── run_gate helper (mirrors the demo's gate logic) ──────────────────────────

def run_gate(source: str, compiler: Compiler, threshold: int) -> dict:
    _, ctx = compiler.compile_to_string(source)
    m = ctx.manifest
    v = ctx.validation
    passed = not v.errors and m.readiness_score >= threshold
    return {
        "readiness_score": m.readiness_score,
        "quality_band": m.quality_band,
        "chunks": m.chunks,
        "warning_codes": [w.code for w in v.warnings],
        "errors": [e.message for e in v.errors],
        "success": passed,
    }


# ── tests ─────────────────────────────────────────────────────────────────────

def test_gate_passes_substantive_document(compiler, tmp_path):
    src = _write(
        tmp_path,
        "handbook.md",
        "# Policy Handbook\n\n"
        "## Code of Conduct\n\n"
        "All employees are expected to act with integrity in all interactions.\n\n"
        "## Remote Work\n\n"
        "Core hours are 10 am to 3 pm.  Equipment is provided by the company.\n",
    )
    result = run_gate(src, compiler, threshold=70)
    assert result["success"] is True
    assert result["readiness_score"] >= 70
    assert result["chunks"] >= 1


def test_gate_blocks_at_threshold_101(compiler, tmp_path):
    src = _write(tmp_path, "doc.md", "# Title\n\nSome content.\n")
    result = run_gate(src, compiler, threshold=101)
    assert result["success"] is False
    assert result["readiness_score"] <= 100


def test_gate_threshold_zero_always_passes(compiler, tmp_path):
    src = _write(tmp_path, "stub.txt", "TODO")
    result = run_gate(src, compiler, threshold=0)
    assert result["success"] is True


def test_gate_result_has_required_fields(compiler, tmp_path):
    src = _write(tmp_path, "doc.txt", "Hello world.\n\nSecond paragraph.\n")
    result = run_gate(src, compiler, threshold=70)
    for field in ("readiness_score", "quality_band", "chunks", "warning_codes", "errors", "success"):
        assert field in result
    assert isinstance(result["readiness_score"], int)
    assert isinstance(result["chunks"], int)
    assert isinstance(result["warning_codes"], list)
    assert isinstance(result["errors"], list)
    assert isinstance(result["success"], bool)
