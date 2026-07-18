"""Tests for the W_PDF_ATTACHMENT_IGNORED warning (Issue #51).

Detection is warning-only in this phase:
- a PDF with attachments emits the warning with count-only safe metadata;
- a PDF without attachments does not emit it;
- the warning fires at most once per document;
- readiness score and quality band are unchanged relative to the baseline;
- no filenames, bytes, or filesystem paths appear in metadata.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from aksharamd.compiler import Compiler
from aksharamd.scoring.models import SCORING_POLICY

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATTACHMENT_PDF = _REPO_ROOT / "benchmarks/.public_corpus/pdf/025-attachment/with-attachment.pdf"
_TRIVIAL_PDF = _REPO_ROOT / "benchmarks/.public_corpus/pdf/001-trivial/minimal-document.pdf"


def _compile(src: Path, tmp_path: Path):
    if not src.exists():
        pytest.skip(f"corpus asset {src} not present")
    import os
    prev = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        return Compiler().compile(str(src))
    finally:
        os.chdir(prev)


def _attachment_warnings(ctx) -> list:
    return [w for w in ctx.validation.warnings if w.code == "W_PDF_ATTACHMENT_IGNORED"]


# ── Emission ───────────────────────────────────────────────────────────────


def test_pdf_with_attachment_emits_warning(tmp_path: Path) -> None:
    ctx = _compile(_ATTACHMENT_PDF, tmp_path)
    warnings = _attachment_warnings(ctx)
    assert len(warnings) == 1, (
        f"expected exactly one W_PDF_ATTACHMENT_IGNORED, got {len(warnings)}: "
        f"{[w.model_dump() for w in warnings]}"
    )


def test_pdf_with_attachment_reports_correct_count(tmp_path: Path) -> None:
    ctx = _compile(_ATTACHMENT_PDF, tmp_path)
    diag = ctx.document.metadata.get("pdf_attachment_diagnostics")
    assert diag is not None
    assert diag["attachment_count"] == 1, (
        f"expected 1 attachment, got {diag['attachment_count']}"
    )


def test_pdf_without_attachment_does_not_emit(tmp_path: Path) -> None:
    ctx = _compile(_TRIVIAL_PDF, tmp_path)
    assert _attachment_warnings(ctx) == []
    diag = ctx.document.metadata.get("pdf_attachment_diagnostics")
    # Diagnostics still recorded so consumers can distinguish "no attachments"
    # from "detector not run", but count is zero and no warning fires.
    assert diag is not None
    assert diag["attachment_count"] == 0


# ── Dedup ──────────────────────────────────────────────────────────────────


def test_warning_emitted_only_once(tmp_path: Path) -> None:
    """Even after the parallel Phase 3 workers process every page, the
    attachment probe fires at most once at the document level."""
    ctx = _compile(_ATTACHMENT_PDF, tmp_path)
    codes = [w.code for w in ctx.validation.warnings]
    assert codes.count("W_PDF_ATTACHMENT_IGNORED") == 1


def test_pdfplumber_fallback_does_not_double_emit(tmp_path: Path) -> None:
    """The pdfplumber fallback path is triggered only when PyMuPDF reports 0
    pages. It must not run attachment detection (documented limitation) and
    therefore must not add a duplicate warning if the primary path already
    emitted one."""
    from aksharamd.context import CompilationContext
    from aksharamd.plugins.parsers.pdf import _pdfplumber_fallback

    ctx = CompilationContext(source=str(_ATTACHMENT_PDF))
    ctx.warn(
        "W_PDF_ATTACHMENT_IGNORED",
        "primary-path emission (simulated)",
        metadata={"attachment_count": 1, "backend": "pymupdf",
                  "warning_maturity": "candidate"},
    )
    _pdfplumber_fallback(_ATTACHMENT_PDF, ctx)
    codes = [w.code for w in ctx.validation.warnings]
    assert codes.count("W_PDF_ATTACHMENT_IGNORED") == 1


# ── Privacy: no bytes, no paths ────────────────────────────────────────────


def test_warning_metadata_contains_no_attachment_bytes(tmp_path: Path) -> None:
    ctx = _compile(_ATTACHMENT_PDF, tmp_path)
    warn = _attachment_warnings(ctx)[0]
    meta = warn.metadata
    for k, v in meta.items():
        assert not isinstance(v, (bytes, bytearray, io.BytesIO)), (
            f"metadata field {k!r} carries binary data: {type(v).__name__}"
        )
    # A serialization round-trip must not surface any binary blob either.
    dumped = json.dumps(warn.model_dump(), default=str)
    assert "\\x" not in dumped and "bytes" not in dumped.lower()


def test_warning_metadata_contains_no_filesystem_paths(tmp_path: Path) -> None:
    ctx = _compile(_ATTACHMENT_PDF, tmp_path)
    warn = _attachment_warnings(ctx)[0]
    meta = warn.metadata
    dumped = json.dumps(meta, default=str).lower()
    # No absolute paths, drive letters, or path separators anywhere.
    for token in ("/", "\\", "c:", "d:", "with-attachment", ".pdf", "image.png"):
        assert token not in dumped, (
            f"metadata leaked a path/filename token {token!r}: {dumped}"
        )
    # Only the three documented safe keys should be present.
    assert set(meta.keys()) == {"attachment_count", "backend", "warning_maturity"}


def test_warning_maturity_is_candidate(tmp_path: Path) -> None:
    ctx = _compile(_ATTACHMENT_PDF, tmp_path)
    warn = _attachment_warnings(ctx)[0]
    assert warn.metadata.get("warning_maturity") == "candidate"
    diag = ctx.document.metadata["pdf_attachment_diagnostics"]
    assert diag["warning_maturity"] == "candidate"


# ── Scoring invariants ─────────────────────────────────────────────────────


def test_readiness_score_and_band_unchanged_by_warning(tmp_path: Path) -> None:
    """This PR is warning-only: the pre-warning score (87 / HIGH) is
    intentionally preserved. If this changes, the scoring boundary of the
    PR has been violated."""
    ctx = _compile(_ATTACHMENT_PDF, tmp_path)
    assert ctx.manifest is not None
    assert ctx.manifest.readiness_score == 87, (
        f"score drift: got {ctx.manifest.readiness_score}; W_PDF_ATTACHMENT_IGNORED "
        f"must not modify readiness in Issue #51 scope."
    )
    assert ctx.manifest.quality_band == "HIGH", (
        f"band drift: got {ctx.manifest.quality_band!r}"
    )


def test_scoring_policy_registers_zero_penalty(tmp_path: Path) -> None:
    """The rule must appear in SCORING_POLICY with max_penalty=0. This is
    the machine-readable claim that the warning is observational."""
    rule = SCORING_POLICY.get("W_PDF_ATTACHMENT_IGNORED")
    assert rule is not None, "W_PDF_ATTACHMENT_IGNORED missing from SCORING_POLICY"
    assert rule.max_penalty == 0
    assert "observational" in rule.formula.lower() or rule.formula == "0"
