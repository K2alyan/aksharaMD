"""End-to-end product validation harness.

Exercises the same 18-file corpus and CLI surface as the manual validation
run on 2026-07-17 (see docs/validation/E2E_2026-07-17.md).

By default the tests run against the current source tree via
`python -m aksharamd.cli`. If the environment variable
`AKSHARAMD_E2E_BINARY` is set, tests shell out to that binary instead —
so CI can point it at an installed wheel to catch packaging regressions.

The harness intentionally avoids network, GPU, and API-key dependencies.
Extras like [vision], [ocr], [audio], and [math] are not exercised here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


def _cli() -> list[str]:
    """Locate the aksharamd CLI to exercise.

    Priority:
    1. `AKSHARAMD_E2E_BINARY` env var (CI wheel-smoke job points this at
       the installed script).
    2. `aksharamd` on PATH (dev-install case).
    3. Skip: the CLI script is not installed in this environment.
    """
    binary = os.environ.get("AKSHARAMD_E2E_BINARY")
    if binary:
        return [binary]
    on_path = shutil.which("aksharamd")
    if on_path:
        return [on_path]
    pytest.skip("aksharamd CLI not installed on PATH; set AKSHARAMD_E2E_BINARY to run")


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_cli(), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Small self-contained corpus written to a temp dir.

    All files are text-based, deterministic, and free to redistribute. No
    binary fixtures. No network fetch.
    """
    root = tmp_path_factory.mktemp("e2e_corpus")
    (root / "small.md").write_text("# Small\n\nHello world.\n", encoding="utf-8")
    (root / "headings.md").write_text(
        "# Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n\n## Gamma\n\n"
        "Gamma body with **bold** and _italic_.\n\n### Delta\n\nNested.\n",
        encoding="utf-8",
    )
    (root / "unicode.md").write_text(
        "# 日本語 テスト\n\nमराठी परीक्षा — русский — العربية — 中文.\n",
        encoding="utf-8",
    )
    (root / "lists.md").write_text(
        "# Lists\n\n- one\n- two\n\n1. first\n2. second\n\n> A blockquote.\n",
        encoding="utf-8",
    )
    (root / "table.md").write_text(
        "# Table\n\n| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n"
        "| 4 | 5 | 6 |\n",
        encoding="utf-8",
    )
    (root / "empty.md").write_text("", encoding="utf-8")
    (root / "tiny.md").write_text(".", encoding="utf-8")
    (root / "plain.txt").write_text(
        "This is plain text.\nSecond line.\n", encoding="utf-8"
    )
    (root / "basic.html").write_text(
        "<html><body><h1>H1</h1><p>Para1</p>"
        "<table><tr><th>a</th><th>b</th></tr>"
        "<tr><td>1</td><td>2</td></tr></table></body></html>",
        encoding="utf-8",
    )
    (root / "dl.html").write_text(
        "<html><body><h1>KV</h1>"
        "<dl><dt>Author</dt><dd>K</dd>"
        "<dt>Year</dt><dd>2026</dd></dl></body></html>",
        encoding="utf-8",
    )
    (root / "data.csv").write_text(
        "name,age,city\nAlice,30,NYC\nBob,25,LA\n", encoding="utf-8"
    )
    (root / "obj.json").write_text(
        json.dumps({"name": "Test", "items": [1, 2, 3]}), encoding="utf-8"
    )
    (root / "doc.xml").write_text(
        '<?xml version="1.0"?><root><item>A</item></root>', encoding="utf-8"
    )
    (root / "bad.json").write_text('{"broken": ', encoding="utf-8")
    (root / "bad.xml").write_text("<open>no close", encoding="utf-8")
    (root / "unknown.xyz").write_text("garbage", encoding="utf-8")
    with zipfile.ZipFile(root / "archive.zip", "w") as z:
        z.writestr("a.md", "# ZipA\nbody")
        z.writestr("sub/b.md", "# ZipB\nnested")
    return root


def _read_manifest(out_dir: Path, stem: str) -> dict:
    manifest = out_dir / stem / "manifest.json"
    assert manifest.exists(), f"expected {manifest} to exist"
    return json.loads(manifest.read_text(encoding="utf-8"))


# ── CLI smoke ─────────────────────────────────────────────────────────────────


def test_version_prints_semver() -> None:
    r = _run("--version")
    assert r.returncode == 0
    assert "aksharamd" in r.stdout.lower()
    assert "0." in r.stdout  # 0.x.y


def test_help_lists_expected_subcommands() -> None:
    r = _run("--help")
    assert r.returncode == 0
    for sub in ("compile", "validate", "corpus", "formats", "doctor"):
        assert sub in r.stdout


def test_formats_command_lists_registered_parsers() -> None:
    r = _run("formats")
    assert r.returncode == 0
    # A handful of extensions we know are registered
    for ext in (".md", ".csv", ".html", ".json", ".xml"):
        assert ext in r.stdout


# ── Per-format compile ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename,expect_success,min_readiness",
    [
        ("small.md", True, 90),
        ("headings.md", True, 90),
        ("unicode.md", True, 80),
        ("lists.md", True, 80),
        ("table.md", True, 80),
        ("tiny.md", True, 80),
        ("plain.txt", True, 80),
        ("data.csv", True, 80),
        ("basic.html", True, 80),
        ("dl.html", True, 80),
        ("obj.json", True, 80),
        ("doc.xml", True, 60),
        ("archive.zip", True, 40),
        ("empty.md", True, 0),
        # Non-parseable input
        ("bad.xml", False, 0),
        ("unknown.xyz", False, 0),
    ],
)
def test_compile_matrix(
    corpus: Path, tmp_path: Path, filename: str, expect_success: bool, min_readiness: int
) -> None:
    src = corpus / filename
    out = tmp_path / "out"
    r = _run("compile", str(src), "-o", str(out), "--json", "--quiet")
    if not expect_success:
        assert r.returncode != 0, f"expected non-zero exit for {filename}, got 0"
        return
    assert r.returncode == 0, f"compile {filename} failed:\n{r.stderr}"
    payload = json.loads(r.stdout)
    assert payload.get("success") is True
    assert payload.get("readiness_score", 0) >= min_readiness


def test_bad_json_survives_as_plain_text(corpus: Path, tmp_path: Path) -> None:
    """Documents the current lenient behaviour of the JSON parser on
    malformed input. See docs/validation/E2E_2026-07-17.md finding 1.

    NOT a claim that this is the desired end state — a future
    W_PARSE_FALLBACK warning is proposed as a follow-up. This test locks
    in the current behaviour so any regression is intentional."""
    src = corpus / "bad.json"
    out = tmp_path / "out"
    r = _run("compile", str(src), "-o", str(out), "--json", "--quiet")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload.get("success") is True
    # Currently produces HIGH band with no warning; roadmap item is to
    # add W_PARSE_FALLBACK so this becomes OK / warns.
    assert payload.get("readiness_score") is not None


# ── Identity + determinism ───────────────────────────────────────────────────


def test_identity_fields_present_on_manifest(corpus: Path, tmp_path: Path) -> None:
    src = corpus / "headings.md"
    out = tmp_path / "out"
    r = _run("compile", str(src), "-o", str(out), "--quiet")
    assert r.returncode == 0
    m = _read_manifest(out, "headings")
    # These four fields are the identity contract from PR #34.
    assert m["source_id"], "manifest.source_id must be non-empty"
    assert m["capture_id"], "manifest.capture_id must be non-empty"
    assert m["document_id"], "manifest.document_id must be non-empty"
    assert m["scoring_policy_version"], "scoring_policy_version must be set"


def test_content_derived_fields_are_deterministic(corpus: Path, tmp_path: Path) -> None:
    """Compiling the same source twice must yield identical content-derived
    identifiers. Timestamps (compiled_at, elapsed_seconds, stage_timings)
    are wall-clock and intentionally excluded from this contract."""
    src = corpus / "headings.md"
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    for out in (out1, out2):
        r = _run("compile", str(src), "-o", str(out), "--quiet")
        assert r.returncode == 0

    m1 = _read_manifest(out1, "headings")
    m2 = _read_manifest(out2, "headings")

    # Identity contract from PR #34: content-derived fields must match.
    for field in ("source_id", "capture_id", "document_id", "scoring_policy_version"):
        assert m1[field] == m2[field], f"manifest.{field} not deterministic"

    # Chunk filenames encode content hashes and must be stable.
    chunks1 = sorted(p.name for p in (out1 / "headings" / "chunks").iterdir())
    chunks2 = sorted(p.name for p in (out2 / "headings" / "chunks").iterdir())
    assert chunks1 == chunks2, "chunk filenames not deterministic"


# ── show-manifest surfacing ──────────────────────────────────────────────────


def test_show_manifest_prints_manifest_when_pointed_at_output_stem(
    corpus: Path, tmp_path: Path
) -> None:
    """`show-manifest` needs the per-source subdirectory, not the parent
    -o directory. Documents the current UX (see finding 3)."""
    src = corpus / "headings.md"
    out = tmp_path / "out"
    r = _run("compile", str(src), "-o", str(out), "--quiet")
    assert r.returncode == 0
    r = _run("show-manifest", str(out / "headings"))
    assert r.returncode == 0
    assert "source_id" in r.stdout
    assert "readiness_score" in r.stdout


# ── package mode + payload inspection ────────────────────────────────────────


def test_compile_package_emits_expected_artifacts(corpus: Path, tmp_path: Path) -> None:
    src = corpus / "table.md"
    out = tmp_path / "out"
    r = _run(
        "compile", str(src), "-o", str(out),
        "--package", "--package-mode", "adaptive", "--quiet",
    )
    assert r.returncode == 0
    art = out / "table"
    for name in (
        "manifest.json",
        "document.json",
        "document.md",
        "validation.json",
        "llm_payload.json",
        "package_plan.json",
        "token_report.json",
        "chunks",
    ):
        assert (art / name).exists(), f"missing package artifact: {name}"


def test_inspect_payload_prints_structured_summary(corpus: Path, tmp_path: Path) -> None:
    src = corpus / "headings.md"
    out = tmp_path / "out"
    r = _run(
        "compile", str(src), "-o", str(out),
        "--package", "--package-mode", "adaptive", "--quiet",
    )
    assert r.returncode == 0
    payload_path = out / "headings" / "llm_payload.json"
    r = _run("inspect-payload", str(payload_path))
    assert r.returncode == 0
    # The Rich table wraps its output; the token labels always appear.
    for label in ("Document ID", "Package mode", "Total items"):
        assert label in r.stdout


# ── malformed + unsupported input ────────────────────────────────────────────


def test_unsupported_extension_fails_non_zero(corpus: Path, tmp_path: Path) -> None:
    src = corpus / "unknown.xyz"
    r = _run("compile", str(src), "-o", str(tmp_path / "out"), "--json", "--quiet")
    assert r.returncode != 0


def test_empty_markdown_produces_empty_document_warning(
    corpus: Path, tmp_path: Path
) -> None:
    src = corpus / "empty.md"
    r = _run("compile", str(src), "-o", str(tmp_path / "out"), "--json", "--quiet")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    # Expect low band + EMPTY_DOCUMENT warning surface.
    assert payload.get("quality_band") == "POOR"
    assert "EMPTY_DOCUMENT" in (payload.get("warning_codes") or [])
