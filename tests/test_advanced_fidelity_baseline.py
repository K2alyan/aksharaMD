"""Advanced-fidelity baseline harness (Issue #43).

Reproduces the 2026-07-18 baseline run against the public corpus in
`benchmarks/.public_corpus/`. Locks in the current per-document
readiness band and non-experimental warning codes so any future
change to parser output or scoring produces a visible test failure
that must be reviewed intentionally.

The harness intentionally:

- exercises the CLI end-to-end (via the installed wheel or
  `aksharamd` on PATH), NOT the source-tree Python API,
- skips cleanly when the CLI is not installed on PATH,
- skips cleanly when the public corpus is not present (some
  distributions may ship without it),
- excludes the ParseBench binary corpus (`text_dense__de`, `letter3`,
  `myctophidae`, `simple2`, `strikeUnderline`, Japanese case) — those
  assets are not in this repository, per §Phase 2 of the report,
- does NOT check optional-extra paths (Marker vision, Tesseract OCR,
  pix2tex) — those require binaries not guaranteed in a base install
  and are called out as follow-ups.

Full evidence and defect classification live in
`benchmarks/ADVANCED_FIDELITY_2026-07-18.md` +
`benchmarks/ADVANCED_FIDELITY_2026-07-18.json`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_ROOT = _REPO_ROOT / "benchmarks" / ".public_corpus"


def _cli_argv() -> list[str]:
    binary = os.environ.get("AKSHARAMD_E2E_BINARY") or shutil.which("aksharamd")
    if binary is None:
        pytest.skip(
            "aksharamd CLI not installed on PATH; set AKSHARAMD_E2E_BINARY to run"
        )
    return [binary]


def _require_corpus() -> None:
    if not _CORPUS_ROOT.exists():
        pytest.skip(
            "benchmarks/.public_corpus/ not present; skipping advanced-fidelity harness"
        )


# ── Baseline table ────────────────────────────────────────────────────────────

# Locks in the per-document baseline observed 2026-07-18 on main @ 29dbb9d.
# Each row: (relpath, expected_exit, expected_band, expected_warning_subset).
# `expected_warning_subset` is a set that MUST be present in the actual
# warning_codes list; extra warnings are tolerated. Bands are the exact
# post-scoring band we observed.
_BASELINE: tuple[tuple[str, int, str | None, frozenset[str]], ...] = (
    ("pdf/001-trivial/minimal-document.pdf",                            0, "HIGH", frozenset()),
    ("pdf/002-trivial-libre-office-writer/002-trivial-libre-office-writer.pdf",
                                                                        0, "HIGH", frozenset()),
    ("pdf/003-pdflatex-image/pdflatex-image.pdf",                       0, "HIGH", frozenset()),
    ("pdf/004-pdflatex-4-pages/pdflatex-4-pages.pdf",                   0, "OK",   frozenset({"W_MULTICOLUMN_ORDER"})),
    # Encrypted PDF — intentional failure.
    ("pdf/005-libreoffice-writer-password/libreoffice-writer-password.pdf",
                                                                        1, None,   frozenset()),
    ("pdf/006-pdflatex-outline/pdflatex-outline.pdf",                   0, "OK",   frozenset()),
    ("pdf/007-imagemagick-images/imagemagick-images.pdf",               0, "POOR", frozenset({"NEAR_EMPTY_OUTPUT", "LOW_TEXT_DENSITY"})),
    ("pdf/010-pdflatex-forms/pdflatex-forms.pdf",                       0, "RISKY", frozenset({"LOW_TEXT_DENSITY"})),
    ("pdf/015-arabic/habibi.pdf",                                       0, "RISKY", frozenset({"LOW_TEXT_DENSITY"})),
    ("pdf/015-arabic/habibi-rotated.pdf",                               0, "POOR", frozenset({"NEAR_EMPTY_OUTPUT", "LOW_TEXT_DENSITY"})),
    ("pdf/024-annotations/annotated_pdf.pdf",                           0, "RISKY", frozenset({"LOW_TEXT_DENSITY"})),
    # These two are the silent-fidelity concerns flagged in the report;
    # the baseline records their observed HIGH band explicitly so any
    # future change (either a fix or a regression) is visible.
    # F2 update (Issue #51): the omission is no longer silent — the parser
    # now emits W_PDF_ATTACHMENT_IGNORED with count-only metadata. The
    # readiness band remains HIGH because Issue #51 is warning-only; a
    # future scoring-calibration PR may lower the band.
    ("pdf/025-attachment/with-attachment.pdf",                          0, "HIGH", frozenset({"W_PDF_ATTACHMENT_IGNORED"})),
    ("pdf/026-latex-multicolumn/multicolumn.pdf",                       0, "HIGH", frozenset({"HEADING_SKIP"})),
    ("pdf/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf",       0, "RISKY", frozenset({"LOW_TEXT_DENSITY"})),
    # Non-PDF formats — one representative each.
    ("synthetic/sample.docx",                                           0, "OK",   frozenset()),
    ("synthetic/sample.pptx",                                           0, "OK",   frozenset()),
    ("synthetic/sample.xlsx",                                           0, "HIGH", frozenset()),
    ("synthetic/sample.html",                                           0, "HIGH", frozenset()),
    ("synthetic/sample.csv",                                            0, "HIGH", frozenset()),
    ("synthetic/mixed.zip",                                             0, "RISKY", frozenset()),
    ("synthetic/sample.txt",                                            0, "HIGH", frozenset()),
    ("synthetic/sample.md",                                             0, "HIGH", frozenset()),
    ("synthetic/sample.json",                                           0, "HIGH", frozenset()),
    ("synthetic/sample.xml",                                            0, "OK",   frozenset({"HEADING_HIERARCHY"})),
)


# ── Baseline execution + assertions ───────────────────────────────────────────


@pytest.mark.parametrize(
    "relpath,expected_exit,expected_band,expected_warnings",
    _BASELINE,
    ids=[row[0] for row in _BASELINE],
)
def test_advanced_fidelity_baseline(
    tmp_path: Path,
    relpath: str,
    expected_exit: int,
    expected_band: str | None,
    expected_warnings: frozenset[str],
) -> None:
    _require_corpus()
    argv = _cli_argv()
    src = _CORPUS_ROOT / relpath
    if not src.exists():
        pytest.skip(f"asset {relpath} not present")

    out = tmp_path / "out"
    r = subprocess.run(
        [*argv, "compile", str(src), "-o", str(out), "--json", "--quiet"],
        capture_output=True, text=True, timeout=300,
    )

    assert r.returncode == expected_exit, (
        f"exit code mismatch on {relpath}: expected {expected_exit}, got "
        f"{r.returncode}. stderr head: {r.stderr[:200]!r}"
    )

    if expected_exit != 0:
        # Intentional failure — do not decode a JSON payload.
        return

    payload = json.loads(r.stdout)
    band = payload.get("quality_band")
    warnings = set(payload.get("warning_codes") or [])

    assert band == expected_band, (
        f"quality band regression on {relpath}: expected {expected_band!r}, "
        f"got {band!r}. Investigate parser or scoring change before updating "
        "the baseline — this may be a silent-fidelity regression. "
        "See benchmarks/ADVANCED_FIDELITY_2026-07-18.md."
    )
    missing = expected_warnings - warnings
    assert not missing, (
        f"warning-code regression on {relpath}: expected superset "
        f"{expected_warnings}, got {warnings}. Missing: {missing}."
    )


# ── Documented silent-fidelity defects (explicit lock-in) ────────────────────
#
# These two tests do NOT assert that the current behaviour is correct.
# They assert that the CURRENTLY OBSERVED behaviour matches what the
# report described. When a future PR fixes the underlying defect and
# the readiness score drops (as it should), these tests will fail —
# that failure is the trigger to update the baseline AND close the
# corresponding follow-up issue.


def test_multicolumn_currently_scores_high_documented_defect(tmp_path: Path) -> None:
    """Silent-fidelity concern F1 from the 2026-07-18 report:
    `pdf.multicolumn` scores HIGH 85 despite mid-word column interleaving.
    When a future fix drops the band, update the baseline row and close
    the corresponding follow-up issue."""
    _require_corpus()
    argv = _cli_argv()
    src = _CORPUS_ROOT / "pdf/026-latex-multicolumn/multicolumn.pdf"
    if not src.exists():
        pytest.skip("multicolumn.pdf not present")

    out = tmp_path / "out"
    r = subprocess.run(
        [*argv, "compile", str(src), "-o", str(out), "--json", "--quiet"],
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["quality_band"] == "HIGH", (
        "multicolumn LaTeX PDF no longer scores HIGH — probably a good thing. "
        "Update the baseline row + close the multicolumn silent-fidelity issue."
    )


def test_pymupdf_notice_does_not_pollute_json_stdout(tmp_path: Path) -> None:
    """Regression guard for the pymupdf 1.28+ layout-analyzer notice.

    Before the fix, pymupdf printed 'Consider using the pymupdf_layout
    package...' to stdout on first document parse. That broke the
    `aksharamd compile --json` contract: consumers piping to jq or
    json.loads() would see two lines and crash.

    The fix (aksharamd/plugins/parsers/pdf.py) opts out via
    `PYMUPDF_SUGGEST_LAYOUT_ANALYZER=0` and additionally routes pymupdf
    messages to stderr. This test locks that in — if a future pymupdf
    release adds a new stdout-writing notice, or the env-var opt-out is
    removed, this test fails and the JSON contract is defended.
    """
    _require_corpus()
    argv = _cli_argv()
    src = _CORPUS_ROOT / "pdf/026-latex-multicolumn/multicolumn.pdf"
    if not src.exists():
        pytest.skip("multicolumn.pdf not present")

    out = tmp_path / "out"
    r = subprocess.run(
        [*argv, "compile", str(src), "-o", str(out), "--json", "--quiet"],
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0
    # The whole stdout must parse as one JSON object — no leading
    # library-notice lines allowed.
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout is not pure JSON — a library is polluting it. "
            f"First 200 chars: {r.stdout[:200]!r}"
        ) from exc
    assert payload.get("success") is True


def test_pdf_with_attachment_emits_omission_warning(
    tmp_path: Path,
) -> None:
    """Issue #51 lock-in (replaces the 2026-07-18 F2 silent-fidelity pin):
    `pdf.attachment` still scores HIGH 87 (this PR is warning-only) but
    now emits W_PDF_ATTACHMENT_IGNORED so the omission is visible. When a
    future PR calibrates scoring and lowers the band, update this test."""
    _require_corpus()
    argv = _cli_argv()
    src = _CORPUS_ROOT / "pdf/025-attachment/with-attachment.pdf"
    if not src.exists():
        pytest.skip("with-attachment.pdf not present")

    out = tmp_path / "out"
    r = subprocess.run(
        [*argv, "compile", str(src), "-o", str(out), "--json", "--quiet"],
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["quality_band"] == "HIGH", (
        "with-attachment.pdf no longer scores HIGH — probably a scoring "
        "calibration change. Update this test and the baseline row."
    )
    warnings = set(payload.get("warning_codes") or [])
    assert "W_PDF_ATTACHMENT_IGNORED" in warnings, (
        f"attachment omission warning missing: {warnings}. Issue #51 requires "
        "this warning on any PDF carrying embedded file attachments."
    )
