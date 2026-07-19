"""AksharaMD PDF Benchmark v1 — Phase 1 baseline harness (Issue #68).

End-to-end measurement of AksharaMD's PDF parsing quality, reliability,
speed, and AI-readiness across the frozen public corpus + verified
ParseBench assets. This is Phase 1 — AksharaMD only. Competitor
adapters land in Phase 2 as separate PRs.

**No production code changes.** No parser, validator, scoring,
warning-penalty, or ``SCORING_POLICY`` modifications. Everything in
``benchmarks/`` and ``tests/``.

Corpus:

- Public: every ``*.pdf`` under ``benchmarks/.public_corpus/pdf/**``.
- ParseBench: every asset in ``benchmarks/parsebench_assets.lock.json``
  that has a checksum and a locally cached copy at
  ``%LOCALAPPDATA%\\aksharamd\\parsebench\\<revision>\\``.

The manifest is rebuilt on every run from these two sources; it is
deterministic and depends only on on-disk state.

Runs the ``aksharamd`` binary via ``subprocess`` (same isolation pattern
as ``benchmarks/multicolumn_recalibration.py`` and
``benchmarks/parsebench_recalibration.py``). Per-file errors are
captured; the harness does not abort on a single failure. Result
ordering is deterministic (sorted by asset id).

**Offline.** No network fetch.

Exit codes:
- ``0`` — success.
- ``40`` — a labelled asset is missing from disk / cache.
- ``41`` — sha256 or size mismatch on a ParseBench asset.
- ``42`` — ``aksharamd`` binary not on PATH.
- ``43`` — invalid ``--only`` selector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess  # nosec B404 - orchestrates aksharamd CLI + git rev-parse only
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PUBLIC_ROOT = _REPO_ROOT / "benchmarks" / ".public_corpus" / "pdf"
_LOCKFILE = _REPO_ROOT / "benchmarks" / "parsebench_assets.lock.json"
_PUBLIC_LABELS = _REPO_ROOT / "benchmarks" / "multicolumn_recalibration_labels.json"


# ── Manifest data model ─────────────────────────────────────────────────


@dataclass
class Asset:
    """One frozen benchmark corpus entry."""

    asset_id: str
    corpus_source: str  # "public" | "parsebench" | "synthetic"
    path_strategy: str  # "on-disk" | "cache"
    pdf_path: Path
    sha256: str
    size_bytes: int
    page_count: int | None
    document_class: str  # "native-text" | "image-only" | "table-heavy" | "multicolumn" | "mixed-layout" | "multilingual" | "malformed" | "unknown"
    ground_truth_available: bool
    licensing: str
    eligibility: str  # "eligible" | "excluded"
    exclusion_reason: str = ""


# ── Corpus resolution ───────────────────────────────────────────────────


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_count(p: Path) -> int | None:
    try:
        import fitz  # type: ignore[import-untyped] # PyMuPDF
    except ImportError:
        return None
    try:
        with fitz.open(str(p)) as doc:
            return doc.page_count
    except Exception:
        return None


def _classify_public(rel: str, labels_map: dict) -> str:
    """Classify a public-corpus PDF using the multicolumn labels
    manifest + naming heuristics.
    """
    # Try label lookup.
    entry = labels_map.get(rel) or labels_map.get(rel.replace("\\", "/"))
    if entry:
        layout = (entry.get("layout") or "").lower()
        if "image" in layout:
            return "image-only"
        if "multicolumn" in layout or "two-column" in layout or "three-column" in layout:
            return "multicolumn"
        if layout == "encrypted":
            return "malformed"
    # Heuristics from the directory name.
    lower = rel.lower()
    if any(k in lower for k in ("imagemagick", "image", "grayscale", "cmyk", "base64", "png", "jpg")):
        return "image-only"
    if "arabic" in lower or "multilingual" in lower:
        return "multilingual"
    if "multicolumn" in lower:
        return "multicolumn"
    if any(k in lower for k in ("form", "password", "corrupt")):
        return "malformed"
    return "native-text"


def _classify_parsebench(entry: dict) -> str:
    """Classify a ParseBench asset from its lockfile fields."""
    aid = entry["id"]
    defect = entry.get("defect_kind") or ""
    if aid in ("letter3", "myctophidae", "japanese_case"):
        return "image-only"
    if aid == "japanese_case":
        return "multilingual"
    if aid == "text_dense__de":
        return "multilingual"
    if defect in ("mixed", "block-level", "span-level") and aid != "strikeUnderline":
        return "multicolumn"
    return "native-text"


def _resolve_public_assets() -> list[Asset]:
    """Enumerate every PDF under the frozen public corpus."""
    with _PUBLIC_LABELS.open("r", encoding="utf-8") as f:
        labels_map: dict = json.load(f).get("labels", {})
    out: list[Asset] = []
    for pdf in sorted(_PUBLIC_ROOT.rglob("*.pdf")):
        rel = str(pdf.relative_to(_PUBLIC_ROOT)).replace("\\", "/")
        rel_display = f"public/{rel}"
        cls = _classify_public(rel, labels_map)
        entry = labels_map.get(rel) or {}
        excluded_reason = entry.get("excluded_reason", "")
        eligible = "eligible"
        # Exclude only if the label author flagged unavailable / encrypted /
        # image-only-untestable — otherwise every PDF participates.
        if entry.get("unavailable"):
            eligible = "excluded"
        out.append(Asset(
            asset_id=rel_display,
            corpus_source="public",
            path_strategy="on-disk",
            pdf_path=pdf,
            sha256=_sha256(pdf),
            size_bytes=pdf.stat().st_size,
            page_count=_page_count(pdf),
            document_class=cls,
            ground_truth_available=bool(entry.get("expected_positive") is not None),
            licensing=entry.get("license", "corpus-license-unknown"),
            eligibility=eligible,
            exclusion_reason=excluded_reason if not eligible == "eligible" else "",
        ))
    return out


def _resolve_parsebench_assets() -> list[Asset]:
    """Enumerate every ParseBench asset with a verified local cache."""
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        lock = json.load(f)
    revision = lock["dataset_source"]["dataset_revision"]
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        raise RuntimeError("LOCALAPPDATA unset; cannot locate ParseBench cache")
    cache_dir = Path(la) / "aksharamd" / "parsebench" / revision
    out: list[Asset] = []
    for entry in lock["assets"]:
        aid = entry["id"]
        pdf = cache_dir / f"{aid}.pdf"
        if not pdf.exists():
            raise RuntimeError(f"ParseBench cache missing: {pdf}")
        actual_sha = _sha256(pdf)
        if actual_sha != entry["sha256"]:
            raise RuntimeError(
                f"ParseBench sha256 mismatch on {aid}: cached {actual_sha[:12]} vs promoted {entry['sha256'][:12]}"
            )
        if pdf.stat().st_size != entry["size_bytes"]:
            raise RuntimeError(f"ParseBench size mismatch on {aid}")
        out.append(Asset(
            asset_id=f"parsebench/{aid}",
            corpus_source="parsebench",
            path_strategy="cache",
            pdf_path=pdf,
            sha256=entry["sha256"],
            size_bytes=entry["size_bytes"],
            page_count=_page_count(pdf),
            document_class=_classify_parsebench(entry),
            ground_truth_available=True,
            licensing="Apache-2.0 (ParseBench dataset); reference-fetch-only for PDFs",
            eligibility="eligible",
        ))
    return out


def _freeze_manifest(assets: list[Asset]) -> dict[str, Any]:
    sorted_assets = sorted(assets, key=lambda a: a.asset_id)
    return {
        "harness_version": "pdf_benchmark_v1.py@2026-07-19",
        "commit_under_evaluation": _current_commit(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "asset_count_total": len(sorted_assets),
        "asset_count_eligible": sum(1 for a in sorted_assets if a.eligibility == "eligible"),
        "assets": [{**asdict(a), "pdf_path": str(a.pdf_path)} for a in sorted_assets],
        "corpus_counts": _corpus_counts(sorted_assets),
        "class_counts": _class_counts(sorted_assets),
    }


def _corpus_counts(assets: list[Asset]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in assets:
        out[a.corpus_source] = out.get(a.corpus_source, 0) + 1
    return out


def _class_counts(assets: list[Asset]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in assets:
        out[a.document_class] = out.get(a.document_class, 0) + 1
    return out


# ── Execution ───────────────────────────────────────────────────────────


@dataclass
class RunResult:
    asset_id: str
    corpus_source: str
    document_class: str
    parse_success: bool
    exit_code: int
    runtime_seconds: float
    output_chars: int
    estimated_tokens: int
    output_size_inflation: float  # chars per PDF byte
    deterministic: bool | None
    page_count_pdf: int | None
    page_count_output: int | None
    missing_pages: bool
    readiness_score: int | None
    quality_band: str | None
    warning_codes: list[str] = field(default_factory=list)
    informational: list[str] = field(default_factory=list)
    repeat_content_ratio: float | None = None
    ocr_required: bool = False
    stdout_head: str = ""
    stderr_head: str = ""
    fidelity_flags: dict[str, Any] = field(default_factory=dict)


def _compile_once(binary: str, pdf: Path, out_root: Path) -> tuple[dict[str, Any], subprocess.CompletedProcess, float]:
    stem = pdf.stem
    out_dir = out_root / stem
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    t0 = time.perf_counter()
    proc = subprocess.run(  # nosec B603 - binary from shutil.which; args are local paths
        [binary, "compile", str(pdf), "-o", str(out_dir), "--json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    elapsed = time.perf_counter() - t0
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"_stdout_parse_error": True, "_stdout_head": proc.stdout[:400]}
    payload["_out_dir"] = out_dir
    return payload, proc, elapsed


def _read_document_md(out_dir: Path) -> str:
    stem = out_dir.name
    p = out_dir / stem / "document.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def _read_manifest_json(out_dir: Path) -> dict[str, Any]:
    stem = out_dir.name
    p = out_dir / stem / "manifest.json"
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _repeat_content_ratio(text: str, ngram: int = 4) -> float:
    """Fraction of n-word windows that are duplicated. 0.0 for a
    clean output; approaches 1.0 for pathological repetition.
    """
    tokens = text.split()
    if len(tokens) < ngram * 2:
        return 0.0
    counts: dict[tuple[str, ...], int] = {}
    for i in range(len(tokens) - ngram + 1):
        key = tuple(tokens[i:i + ngram])
        counts[key] = counts.get(key, 0) + 1
    dup_windows = sum(c for c in counts.values() if c > 1)
    total_windows = len(tokens) - ngram + 1
    return dup_windows / total_windows if total_windows else 0.0


_TOKEN_APPROX_CHARS = 4  # rough char-to-token ratio for English prose


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _TOKEN_APPROX_CHARS) if text else 0


def _apply_fidelity_flags(asset: Asset, doc_md: str, manifest: dict, payload: dict) -> dict[str, Any]:
    warns = payload.get("warning_codes") or []
    infos = payload.get("informational") or []
    ocr_required = "OCR_REQUIRED" in warns
    heading_skip = "HEADING_SKIP" in warns or "HEADING_HIERARCHY" in warns or any("HEADING" in w for w in warns)
    table_missing = any("TABLE_EXPECTED_NOT_EXTRACTED" in w for w in warns)
    multicolumn_warn = "W_MULTICOLUMN_ORDER" in warns
    return {
        "ocr_required": ocr_required,
        "heading_skip_signal": heading_skip,
        "table_missing_signal": table_missing,
        "multicolumn_order_warning": multicolumn_warn,
        "expected_multicolumn_class": asset.document_class == "multicolumn",
        "expected_image_only_class": asset.document_class == "image-only",
        "warnings_count": len(warns),
        "informational_count": len(infos),
    }


def run_one(binary: str, asset: Asset, workdir: Path, *, do_deterministic_check: bool = True) -> RunResult:
    payload1, proc1, elapsed1 = _compile_once(binary, asset.pdf_path, workdir / "run1")
    doc_md_1 = _read_document_md(payload1["_out_dir"])
    manifest_1 = _read_manifest_json(payload1["_out_dir"])
    exit_code = proc1.returncode
    parse_ok = exit_code == 0 and bool(doc_md_1)
    output_chars = len(doc_md_1)
    tokens = _estimate_tokens(doc_md_1)
    inflation = (output_chars / asset.size_bytes) if asset.size_bytes else 0.0
    repeat_ratio = _repeat_content_ratio(doc_md_1)
    warns = payload1.get("warning_codes") or []
    infos = payload1.get("informational") or []
    # Deterministic check: recompile once and hash the produced document.md.
    deterministic: bool | None = None
    if parse_ok and do_deterministic_check:
        payload2, _proc2, _elapsed2 = _compile_once(binary, asset.pdf_path, workdir / "run2")
        doc_md_2 = _read_document_md(payload2["_out_dir"])
        deterministic = (doc_md_1 == doc_md_2)
    # Page-count outputs: aksharamd manifest carries `pages`.
    output_pages: int | None = None
    for candidate_key in ("pages", "page_count", "n_pages"):
        v = payload1.get(candidate_key)
        if isinstance(v, int):
            output_pages = v
            break
    missing_pages = False
    if asset.page_count is not None and output_pages is not None:
        missing_pages = output_pages < asset.page_count
    fidelity = _apply_fidelity_flags(asset, doc_md_1, manifest_1, payload1)
    return RunResult(
        asset_id=asset.asset_id,
        corpus_source=asset.corpus_source,
        document_class=asset.document_class,
        parse_success=parse_ok,
        exit_code=exit_code,
        runtime_seconds=round(elapsed1, 3),
        output_chars=output_chars,
        estimated_tokens=tokens,
        output_size_inflation=round(inflation, 4),
        deterministic=deterministic,
        page_count_pdf=asset.page_count,
        page_count_output=output_pages,
        missing_pages=missing_pages,
        readiness_score=payload1.get("readiness_score"),
        quality_band=payload1.get("quality_band"),
        warning_codes=list(warns),
        informational=list(infos),
        repeat_content_ratio=round(repeat_ratio, 4),
        ocr_required=fidelity["ocr_required"],
        stdout_head=proc1.stdout[:400] if proc1.returncode != 0 else "",
        stderr_head=proc1.stderr[:400] if proc1.returncode != 0 else "",
        fidelity_flags=fidelity,
    )


# ── Aggregation ─────────────────────────────────────────────────────────


def _aggregate(results: list[RunResult]) -> dict[str, Any]:
    def _bucket(rows: list[RunResult]) -> dict[str, Any]:
        n = len(rows)
        succ = sum(1 for r in rows if r.parse_success)
        runtimes = [r.runtime_seconds for r in rows if r.parse_success]
        tokens = [r.estimated_tokens for r in rows if r.parse_success]
        inflations = [r.output_size_inflation for r in rows if r.parse_success]
        dets = [r.deterministic for r in rows if r.deterministic is not None]
        ocr = sum(1 for r in rows if r.ocr_required)
        missing = sum(1 for r in rows if r.missing_pages)
        multicolumn = sum(1 for r in rows
                          if r.fidelity_flags.get("multicolumn_order_warning"))
        repeats = [r.repeat_content_ratio for r in rows if r.repeat_content_ratio is not None]
        bands: dict[str, int] = {}
        for r in rows:
            if r.quality_band:
                bands[r.quality_band] = bands.get(r.quality_band, 0) + 1
        warning_counts: dict[str, int] = {}
        for r in rows:
            for w in r.warning_codes:
                warning_counts[w] = warning_counts.get(w, 0) + 1
        return {
            "n": n,
            "parse_success_count": succ,
            "parse_success_rate": round(succ / n, 4) if n else 0.0,
            "runtime_seconds_mean": round(statistics.mean(runtimes), 3) if runtimes else None,
            "runtime_seconds_p50": round(statistics.median(runtimes), 3) if runtimes else None,
            "runtime_seconds_p95": round(_pct(runtimes, 95), 3) if runtimes else None,
            "tokens_mean": int(statistics.mean(tokens)) if tokens else None,
            "tokens_p50": int(statistics.median(tokens)) if tokens else None,
            "output_size_inflation_mean": round(statistics.mean(inflations), 4) if inflations else None,
            "deterministic_rate": round(sum(dets) / len(dets), 4) if dets else None,
            "ocr_required_count": ocr,
            "missing_pages_count": missing,
            "multicolumn_warning_count": multicolumn,
            "repeat_content_ratio_mean": round(statistics.mean(repeats), 4) if repeats else None,
            "quality_band_distribution": bands,
            "warning_code_distribution": warning_counts,
        }

    aggregates: dict[str, Any] = {
        "overall": _bucket(results),
        "by_corpus": {
            src: _bucket([r for r in results if r.corpus_source == src])
            for src in sorted({r.corpus_source for r in results})
        },
        "by_document_class": {
            cls: _bucket([r for r in results if r.document_class == cls])
            for cls in sorted({r.document_class for r in results})
        },
    }
    # Failure catalogue.
    failures = [
        {
            "asset_id": r.asset_id,
            "exit_code": r.exit_code,
            "stdout_head": r.stdout_head,
            "stderr_head": r.stderr_head,
        }
        for r in results
        if not r.parse_success
    ]
    aggregates["failures"] = failures
    return aggregates


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    k = (len(ys) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ys) - 1)
    return ys[lo] + (ys[hi] - ys[lo]) * (k - lo)


# ── Orchestration ───────────────────────────────────────────────────────


def _current_commit() -> str:
    try:
        return subprocess.check_output(  # nosec B603 B607 - local git head
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, text=True,
        ).strip()
    except Exception:
        return "unknown"


def _resolve_binary() -> str:
    binary = shutil.which("aksharamd")
    if binary is None:
        raise RuntimeError("aksharamd binary not found on PATH")
    return binary


def _dependency_versions() -> dict[str, str]:
    keys = ("aksharamd", "pymupdf", "click", "rich")
    out: dict[str, str] = {}
    for k in keys:
        try:
            from importlib.metadata import version
            out[k] = version(k)
        except Exception:
            out[k] = "unknown"
    return out


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _render_report(manifest: dict, results: list[RunResult], aggregate: dict, path: Path) -> None:
    lines: list[str] = []
    lines.append(f"# AksharaMD PDF Benchmark v1 — Phase 1 baseline ({time.strftime('%Y-%m-%d')})")
    lines.append("")
    lines.append(f"**Commit under evaluation:** `{manifest['commit_under_evaluation']}`")
    lines.append(f"**AksharaMD version:** `{_dependency_versions().get('aksharamd', 'unknown')}`")
    lines.append(f"**Python:** {manifest['python_version']} · **Platform:** {manifest['platform']}")
    lines.append("")
    lines.append("**No production code changes.** No parser, validator, scoring, warning-penalty, or `SCORING_POLICY` modifications. This is Phase 1 of the AksharaMD PDF Benchmark v1 milestone (Issue #68) — AksharaMD alone, no competitor adapters.")
    lines.append("")
    lines.append("## Corpus")
    lines.append("")
    lines.append(f"- Total assets: **{manifest['asset_count_total']}**")
    lines.append(f"- Eligible: **{manifest['asset_count_eligible']}**")
    lines.append(f"- By corpus source: {manifest['corpus_counts']}")
    lines.append(f"- By document class: {manifest['class_counts']}")
    lines.append("")
    lines.append("Manifest artifact: `benchmarks/pdf_benchmark_v1_manifest.json`.")
    lines.append("")
    lines.append("## Overall metrics")
    lines.append("")
    ov = aggregate["overall"]
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Files evaluated | {ov['n']} |")
    lines.append(f"| Parse success | {ov['parse_success_count']} / {ov['n']} ({ov['parse_success_rate'] * 100:.1f} %) |")
    lines.append(f"| Runtime mean (s) | {ov['runtime_seconds_mean']} |")
    lines.append(f"| Runtime p50 (s) | {ov['runtime_seconds_p50']} |")
    lines.append(f"| Runtime p95 (s) | {ov['runtime_seconds_p95']} |")
    lines.append(f"| Tokens mean | {ov['tokens_mean']} |")
    lines.append(f"| Output-size inflation (chars per PDF byte) | {ov['output_size_inflation_mean']} |")
    lines.append(f"| Deterministic rate | {ov['deterministic_rate']} |")
    lines.append(f"| OCR-required files | {ov['ocr_required_count']} |")
    lines.append(f"| Missing-pages files | {ov['missing_pages_count']} |")
    lines.append(f"| Multicolumn-warning files | {ov['multicolumn_warning_count']} |")
    lines.append(f"| Repeat-content mean ratio | {ov['repeat_content_ratio_mean']} |")
    lines.append("")
    lines.append("### Quality-band distribution (overall)")
    lines.append("")
    for band, n in sorted(ov["quality_band_distribution"].items()):
        lines.append(f"- **{band}**: {n}")
    lines.append("")
    lines.append("### Warning-code distribution (top 15)")
    lines.append("")
    for code, n in sorted(ov["warning_code_distribution"].items(), key=lambda kv: -kv[1])[:15]:
        lines.append(f"- `{code}`: {n}")
    lines.append("")
    lines.append("## By corpus source")
    for src, agg in aggregate["by_corpus"].items():
        lines.append(f"### {src}")
        lines.append("")
        lines.append(f"- Files: {agg['n']}, Success: {agg['parse_success_count']} ({agg['parse_success_rate'] * 100:.1f}%)")
        lines.append(f"- Runtime mean/p50/p95 (s): {agg['runtime_seconds_mean']} / {agg['runtime_seconds_p50']} / {agg['runtime_seconds_p95']}")
        lines.append(f"- Tokens mean/p50: {agg['tokens_mean']} / {agg['tokens_p50']}")
        lines.append(f"- Deterministic rate: {agg['deterministic_rate']}")
        lines.append(f"- OCR-required: {agg['ocr_required_count']} · Missing pages: {agg['missing_pages_count']} · Multicolumn-warn: {agg['multicolumn_warning_count']}")
        lines.append(f"- Quality bands: {agg['quality_band_distribution']}")
        lines.append("")
    lines.append("## By document class")
    for cls, agg in aggregate["by_document_class"].items():
        lines.append(f"### {cls}")
        lines.append("")
        lines.append(f"- Files: {agg['n']}, Success: {agg['parse_success_count']} ({agg['parse_success_rate'] * 100:.1f}%)")
        lines.append(f"- Runtime mean (s): {agg['runtime_seconds_mean']}, tokens mean: {agg['tokens_mean']}")
        lines.append(f"- OCR-required: {agg['ocr_required_count']}, missing pages: {agg['missing_pages_count']}")
        lines.append(f"- Quality bands: {agg['quality_band_distribution']}")
        lines.append("")
    lines.append("## Failures")
    lines.append("")
    if aggregate["failures"]:
        lines.append(f"{len(aggregate['failures'])} of {ov['n']} files failed to parse cleanly:")
        for f in aggregate["failures"]:
            lines.append(f"- `{f['asset_id']}` (exit {f['exit_code']}) — stderr: `{(f['stderr_head'] or 'empty')[:200]}`")
    else:
        lines.append(f"No parse failures across {ov['n']} files.")
    lines.append("")
    lines.append("## Highest-impact failure classes (rule-based)")
    lines.append("")
    classes = {
        "ocr_required": sum(1 for r in results if r.ocr_required),
        "missing_pages": sum(1 for r in results if r.missing_pages),
        "multicolumn_order_warning": sum(1 for r in results if r.fidelity_flags.get("multicolumn_order_warning")),
        "heading_skip_signal": sum(1 for r in results if r.fidelity_flags.get("heading_skip_signal")),
        "table_missing_signal": sum(1 for r in results if r.fidelity_flags.get("table_missing_signal")),
        "parse_failure": sum(1 for r in results if not r.parse_success),
        "repeat_content_over_10pct": sum(1 for r in results
                                         if r.repeat_content_ratio and r.repeat_content_ratio > 0.10),
    }
    for k, v in sorted(classes.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {k}: {v} / {ov['n']} ({v / ov['n'] * 100:.1f}%)")
    lines.append("")
    lines.append("## Constraints observed")
    lines.append("")
    lines.append("- No parser / validator / scoring / warning-penalty / packaging / model code changed.")
    lines.append("- `SCORING_POLICY_VERSION` remains `\"1.0\"`.")
    lines.append("- No PDF bytes added to git. Public corpus lives at `benchmarks/.public_corpus/pdf/**`; ParseBench PDFs live in the local cache outside the repo.")
    lines.append("- Deterministic result ordering (assets sorted by id).")
    lines.append("- No network fetch during benchmark execution.")
    lines.append("- Per-file errors preserved; single failures do not abort the run.")
    lines.append("")
    lines.append("## Human-reviewed quality (scaffold)")
    lines.append("")
    lines.append("Rule-based fidelity signals above are captured automatically. Human-reviewed quality per file (correct / usable-with-minor-defects / materially-damaged / unusable) has not been executed for this Phase 1 baseline — it appears here as a scaffold column for future reviewers. A subsequent PR under the umbrella issue will land the reviewer-graded ratings.")
    lines.append("")
    lines.append("## Next steps")
    lines.append("")
    lines.append("- Phase 2: competitor adapters (MarkItDown, Docling, Unstructured, PyMuPDF4LLM) — one PR each with pinned versions.")
    lines.append("- Phase 3: comparison report — strengths by document class, no universal-winner declaration.")
    path.write_text("\n".join(lines), encoding="utf-8")


def _run(
    output_json: Path,
    output_md: Path,
    manifest_json: Path,
    *,
    only: str | None = None,
    workdir: Path | None = None,
    do_deterministic_check: bool = True,
) -> int:
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="pdf_benchmark_v1_"))

    # Corpus resolution.
    print("resolving corpus...", file=sys.stderr)
    try:
        pb_assets = _resolve_parsebench_assets()
    except RuntimeError as e:
        print(f"ParseBench corpus resolution failed: {e}", file=sys.stderr)
        return 40
    pub_assets = _resolve_public_assets()
    assets = pub_assets + pb_assets
    if only:
        selected = [a for a in assets if a.asset_id == only or a.asset_id.endswith(only)]
        if not selected:
            print(f"--only {only!r} matched no assets", file=sys.stderr)
            return 43
        assets = selected

    manifest = _freeze_manifest(assets)
    _write_json(manifest_json, manifest)
    print(f"wrote {manifest_json}", file=sys.stderr)

    # Execution.
    try:
        binary = _resolve_binary()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 42

    results: list[RunResult] = []
    for a in sorted(assets, key=lambda a: a.asset_id):
        if a.eligibility != "eligible":
            continue
        print(f"running {a.asset_id}", file=sys.stderr)
        try:
            r = run_one(binary, a, workdir, do_deterministic_check=do_deterministic_check)
        except Exception as e:
            r = RunResult(
                asset_id=a.asset_id,
                corpus_source=a.corpus_source,
                document_class=a.document_class,
                parse_success=False,
                exit_code=-1,
                runtime_seconds=0.0,
                output_chars=0,
                estimated_tokens=0,
                output_size_inflation=0.0,
                deterministic=None,
                page_count_pdf=a.page_count,
                page_count_output=None,
                missing_pages=False,
                readiness_score=None,
                quality_band=None,
                warning_codes=[],
                stderr_head=f"harness exception: {e}"[:400],
            )
        results.append(r)

    aggregate = _aggregate(results)

    payload = {
        "harness_version": manifest["harness_version"],
        "commit_under_evaluation": manifest["commit_under_evaluation"],
        "python_version": manifest["python_version"],
        "platform": manifest["platform"],
        "dependencies": _dependency_versions(),
        "aggregate": aggregate,
        "per_asset": [asdict(r) for r in results],
    }
    _write_json(output_json, payload)
    print(f"wrote {output_json}", file=sys.stderr)

    _render_report(manifest, results, aggregate, output_md)
    print(f"wrote {output_md}", file=sys.stderr)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-json", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_BASELINE_2026-07-19.json")
    ap.add_argument("--output-md", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_BASELINE_2026-07-19.md")
    ap.add_argument("--manifest", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json")
    ap.add_argument("--workdir", type=Path, default=None)
    ap.add_argument("--only", type=str, default=None,
                    help="Only run assets whose id matches this suffix")
    ap.add_argument("--no-deterministic-check", action="store_true",
                    help="Skip the recompile+diff determinism check to halve runtime.")
    args = ap.parse_args()
    return _run(
        args.output_json,
        args.output_md,
        args.manifest,
        only=args.only,
        workdir=args.workdir,
        do_deterministic_check=not args.no_deterministic_check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
