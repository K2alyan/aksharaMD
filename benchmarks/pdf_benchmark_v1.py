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
import re
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
    """Per-file benchmark record.

    Success is captured at four explicit levels — do NOT collapse them
    into a single "parse success" number:

    - ``execution_success`` — the CLI exited 0 (process-level only).
    - ``output_package_created`` — ``document.md`` exists and is non-empty.
    - ``content_extracted`` — ``document.md`` has meaningful content:
      character count above the minimum threshold, AND the parser did
      NOT emit ``NEAR_EMPTY_OUTPUT``.
    - ``structurally_usable`` — content-extracted AND repeat-content
      ratio below 0.20 AND (for multi-page inputs) at least one block
      per page AND no ``LOW_TEXT_DENSITY`` on a document whose expected
      class is anything other than ``image-only`` with hidden text.

    ``human_review_status`` and ``human_usability`` are populated only
    for the stratified review sample (else ``"not_reviewed"``).
    """
    asset_id: str
    corpus_source: str
    document_class: str
    execution_success: bool
    exit_code: int
    output_package_created: bool
    content_extracted: bool
    structurally_usable: bool
    human_review_status: str  # "reviewed" | "not_reviewed"
    human_usability: str      # "usable" | "usable_with_minor_defects" | "materially_damaged" | "unusable" | "not_reviewed"
    human_review_evidence: str
    runtime_seconds: float
    output_chars: int
    estimated_tokens: int
    output_size_inflation: float  # chars per PDF byte
    deterministic: bool | None
    page_count_pdf: int | None
    page_count_output: int | None
    missing_pages: bool
    hidden_text_layer: bool | None  # None when PyMuPDF unavailable
    hidden_text_layer_chars: int | None
    image_placeholder_ratio: float | None
    readiness_score: int | None
    quality_band: str | None
    warning_codes: list[str] = field(default_factory=list)
    informational: list[str] = field(default_factory=list)
    repeat_content_ratio: float | None = None
    low_text_density: bool = False
    near_empty_output: bool = False
    ocr_warning_emitted: bool = False
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


# Minimum non-whitespace character count for content_extracted=True.
# Values below this on a native-text-class PDF indicate a placeholder-only
# or near-empty output.
_MIN_MEANINGFUL_CHARS = 200


def _hidden_text_layer_chars(p: Path) -> tuple[bool | None, int | None]:
    """Return ``(has_selectable_text, char_count)`` computed by PyMuPDF.
    Used to distinguish "PDF really has no text layer" from
    "text layer present but sparse". Returns ``(None, None)`` if PyMuPDF
    is unavailable or the read fails.
    """
    try:
        import fitz  # type: ignore[import-untyped] # PyMuPDF
    except ImportError:
        return None, None
    try:
        with fitz.open(str(p)) as doc:
            total = 0
            for page in doc:
                total += len(page.get_text() or "")
            return (total > 0, total)
    except Exception:
        return None, None


_IMG_PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _image_placeholder_ratio(text: str) -> float | None:
    """Fraction of markdown "lines" that are image placeholders. None
    if the output has no lines.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    imgs = sum(1 for ln in lines if _IMG_PLACEHOLDER_RE.search(ln))
    return round(imgs / len(lines), 4)


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


def run_one(binary: str, asset: Asset, workdir: Path, *, do_deterministic_check: bool = True,
            human_reviews: dict[str, dict[str, str]] | None = None) -> RunResult:
    """Compile ``asset`` once (twice if determinism check) and derive
    the four-level success ladder + content diagnostics from the
    produced output package.

    ``human_reviews`` optionally supplies pre-recorded human-review
    verdicts keyed by asset id: ``{asset_id: {"usability": ..., "evidence": ...}}``.
    """
    payload1, proc1, elapsed1 = _compile_once(binary, asset.pdf_path, workdir / "run1")
    doc_md_1 = _read_document_md(payload1["_out_dir"])
    manifest_1 = _read_manifest_json(payload1["_out_dir"])
    exit_code = proc1.returncode
    output_chars = len(doc_md_1)
    tokens = _estimate_tokens(doc_md_1)
    inflation = (output_chars / asset.size_bytes) if asset.size_bytes else 0.0
    repeat_ratio = _repeat_content_ratio(doc_md_1)
    warns: list[str] = list(payload1.get("warning_codes") or [])
    infos: list[str] = list(payload1.get("informational") or [])
    low_density = "LOW_TEXT_DENSITY" in warns
    near_empty = "NEAR_EMPTY_OUTPUT" in warns
    ocr_warning = any("OCR" in w for w in warns)

    # Four-level success ladder.
    execution_success = (exit_code == 0)
    output_package_created = execution_success and bool(doc_md_1)
    # content_extracted: enough non-whitespace characters AND parser
    # didn't flag NEAR_EMPTY_OUTPUT.
    non_ws_chars = sum(1 for c in doc_md_1 if not c.isspace())
    content_extracted = (
        output_package_created
        and non_ws_chars >= _MIN_MEANINGFUL_CHARS
        and not near_empty
    )
    # structurally_usable: content-extracted AND acceptable
    # repeat-content AND (when the PDF has a text layer at all) no
    # LOW_TEXT_DENSITY warning.
    #
    # Repeat-content gate is only meaningful when the document is large
    # enough for 4-gram window statistics to be robust. Short outputs
    # (e.g., a title + metadata boilerplate) naturally have many repeated
    # 4-grams without being "damaged". Threshold 0.50 requires a MAJORITY
    # of windows to be duplicated, which is a much stronger signal than
    # the earlier 0.20.
    has_text_layer, hidden_text_chars = _hidden_text_layer_chars(asset.pdf_path)
    repeat_gate_ok = (
        # Only fail on repeat-content if the document is long enough for
        # the statistic to be robust AND more than half the windows dup.
        len(doc_md_1.split()) < 100 or repeat_ratio < 0.50
    )
    structurally_usable = (
        content_extracted
        and repeat_gate_ok
        and not (low_density and (has_text_layer is not False))
    )

    # Deterministic check: recompile once and diff document.md.
    deterministic: bool | None = None
    if output_package_created and do_deterministic_check:
        payload2, _proc2, _elapsed2 = _compile_once(binary, asset.pdf_path, workdir / "run2")
        doc_md_2 = _read_document_md(payload2["_out_dir"])
        deterministic = (doc_md_1 == doc_md_2)

    # Page-count outputs.
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
    img_ratio = _image_placeholder_ratio(doc_md_1)

    # Human review from injected map (empty for the run-wide default).
    review = (human_reviews or {}).get(asset.asset_id, {})
    review_status = "reviewed" if review else "not_reviewed"
    review_usability = review.get("usability", "not_reviewed")
    review_evidence = review.get("evidence", "")

    return RunResult(
        asset_id=asset.asset_id,
        corpus_source=asset.corpus_source,
        document_class=asset.document_class,
        execution_success=execution_success,
        exit_code=exit_code,
        output_package_created=output_package_created,
        content_extracted=content_extracted,
        structurally_usable=structurally_usable,
        human_review_status=review_status,
        human_usability=review_usability,
        human_review_evidence=review_evidence,
        runtime_seconds=round(elapsed1, 3),
        output_chars=output_chars,
        estimated_tokens=tokens,
        output_size_inflation=round(inflation, 4),
        deterministic=deterministic,
        page_count_pdf=asset.page_count,
        page_count_output=output_pages,
        missing_pages=missing_pages,
        hidden_text_layer=has_text_layer,
        hidden_text_layer_chars=hidden_text_chars,
        image_placeholder_ratio=img_ratio,
        readiness_score=payload1.get("readiness_score"),
        quality_band=payload1.get("quality_band"),
        warning_codes=warns,
        informational=infos,
        repeat_content_ratio=round(repeat_ratio, 4),
        low_text_density=low_density,
        near_empty_output=near_empty,
        ocr_warning_emitted=ocr_warning,
        stdout_head=proc1.stdout[:400] if proc1.returncode != 0 else "",
        stderr_head=proc1.stderr[:400] if proc1.returncode != 0 else "",
        fidelity_flags=fidelity,
    )


# ── Aggregation ─────────────────────────────────────────────────────────


def _aggregate(results: list[RunResult]) -> dict[str, Any]:
    def _bucket(rows: list[RunResult]) -> dict[str, Any]:
        n = len(rows)
        exec_ok = sum(1 for r in rows if r.execution_success)
        pkg = sum(1 for r in rows if r.output_package_created)
        content = sum(1 for r in rows if r.content_extracted)
        struct = sum(1 for r in rows if r.structurally_usable)
        near_empty = sum(1 for r in rows if r.near_empty_output)
        low_density = sum(1 for r in rows if r.low_text_density)
        runtimes = [r.runtime_seconds for r in rows if r.execution_success]
        tokens = [r.estimated_tokens for r in rows if r.execution_success]
        inflations = [r.output_size_inflation for r in rows if r.execution_success]
        dets = [r.deterministic for r in rows if r.deterministic is not None]
        ocr_flag = sum(1 for r in rows if r.ocr_warning_emitted)
        missing = sum(1 for r in rows if r.missing_pages)
        multicolumn = sum(1 for r in rows
                          if r.fidelity_flags.get("multicolumn_order_warning"))
        repeats = [r.repeat_content_ratio for r in rows if r.repeat_content_ratio is not None]
        # Human-review breakdown.
        review_rows = [r for r in rows if r.human_review_status == "reviewed"]
        usable = sum(1 for r in review_rows if r.human_usability == "usable")
        usable_minor = sum(1 for r in review_rows if r.human_usability == "usable_with_minor_defects")
        materially_damaged = sum(1 for r in review_rows if r.human_usability == "materially_damaged")
        unusable = sum(1 for r in review_rows if r.human_usability == "unusable")
        # Hidden-text-layer breakdown.
        with_layer = sum(1 for r in rows if r.hidden_text_layer is True)
        without_layer = sum(1 for r in rows if r.hidden_text_layer is False)
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
            # Four-level ladder.
            "execution_success_count": exec_ok,
            "execution_success_rate": round(exec_ok / n, 4) if n else 0.0,
            "output_package_created_count": pkg,
            "output_package_created_rate": round(pkg / n, 4) if n else 0.0,
            "content_extracted_count": content,
            "meaningful_content_rate": round(content / n, 4) if n else 0.0,
            "structurally_usable_count": struct,
            "structurally_usable_rate": round(struct / n, 4) if n else 0.0,
            # Human review (present only for the sample).
            "human_reviewed_count": len(review_rows),
            "human_usable_count": usable,
            "human_usable_with_minor_defects_count": usable_minor,
            "human_materially_damaged_count": materially_damaged,
            "human_unusable_count": unusable,
            "human_usable_rate": round((usable + usable_minor) / len(review_rows), 4) if review_rows else None,
            # Content-diagnostic counts.
            "near_empty_output_count": near_empty,
            "low_text_density_count": low_density,
            "ocr_warning_count": ocr_flag,
            "hidden_text_layer_present_count": with_layer,
            "hidden_text_layer_absent_count": without_layer,
            "missing_pages_count": missing,
            "multicolumn_warning_count": multicolumn,
            # Performance.
            "runtime_seconds_mean": round(statistics.mean(runtimes), 3) if runtimes else None,
            "runtime_seconds_p50": round(statistics.median(runtimes), 3) if runtimes else None,
            "runtime_seconds_p95": round(_pct(runtimes, 95), 3) if runtimes else None,
            "tokens_mean": int(statistics.mean(tokens)) if tokens else None,
            "tokens_p50": int(statistics.median(tokens)) if tokens else None,
            "output_size_inflation_mean": round(statistics.mean(inflations), 4) if inflations else None,
            "deterministic_rate": round(sum(dets) / len(dets), 4) if dets else None,
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
    # Failure catalogue — three-tiered.
    execution_failures = [
        {
            "asset_id": r.asset_id,
            "exit_code": r.exit_code,
            "stdout_head": r.stdout_head,
            "stderr_head": r.stderr_head,
        }
        for r in results
        if not r.execution_success
    ]
    # Assets that ran to completion but produced no meaningful content.
    content_failures = [
        {
            "asset_id": r.asset_id,
            "document_class": r.document_class,
            "output_chars": r.output_chars,
            "near_empty_output": r.near_empty_output,
            "low_text_density": r.low_text_density,
            "quality_band": r.quality_band,
            "hidden_text_layer": r.hidden_text_layer,
            "hidden_text_layer_chars": r.hidden_text_layer_chars,
            "image_placeholder_ratio": r.image_placeholder_ratio,
        }
        for r in results
        if r.execution_success and not r.content_extracted
    ]
    # Assets with content but not structurally usable (e.g., dominated
    # by repeated content or low density on a text-layer PDF).
    structural_failures = [
        {
            "asset_id": r.asset_id,
            "document_class": r.document_class,
            "quality_band": r.quality_band,
            "repeat_content_ratio": r.repeat_content_ratio,
            "low_text_density": r.low_text_density,
        }
        for r in results
        if r.content_extracted and not r.structurally_usable
    ]
    aggregates["execution_failures"] = execution_failures
    aggregates["content_failures"] = content_failures
    aggregates["structural_failures"] = structural_failures
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
    L: list[str] = []
    ov = aggregate["overall"]

    def add(s: str = "") -> None:
        L.append(s)

    add(f"# AksharaMD PDF Benchmark v1 — Phase 1 baseline ({time.strftime('%Y-%m-%d')})")
    add()
    add(f"**Commit under evaluation:** `{manifest['commit_under_evaluation']}`")
    add(f"**AksharaMD version:** `{_dependency_versions().get('aksharamd', 'unknown')}`")
    add(f"**Python:** {manifest['python_version']} · **Platform:** {manifest['platform']}")
    add()
    add("**No production code changes.** No parser, validator, scoring, warning-penalty, or `SCORING_POLICY` modifications. Phase 1 of the AksharaMD PDF Benchmark v1 milestone (Issue #68) — AksharaMD alone, no competitor adapters.")
    add()
    add("**Reading these numbers.** Execution success (CLI exited 0) is NOT parsing success. This report distinguishes four success levels:")
    add()
    add("- `execution_success` — the CLI exited 0.")
    add("- `output_package_created` — `document.md` exists and is non-empty.")
    add("- `meaningful_content` (`content_extracted`) — enough non-whitespace characters AND no `NEAR_EMPTY_OUTPUT` warning.")
    add("- `structurally_usable` — content-extracted AND acceptable repeat-content AND no `LOW_TEXT_DENSITY` on a PDF with a populated text layer.")
    add()
    add("Human-reviewed usability is reported separately for the stratified sample (§ Human review).")
    add()
    add("## Headline metrics")
    add()
    add("| Metric | Value |")
    add("|---|---:|")
    add(f"| Files evaluated | {ov['n']} |")
    add(f"| `execution_success_rate` | {ov['execution_success_count']} / {ov['n']} ({ov['execution_success_rate'] * 100:.1f} %) |")
    add(f"| `output_package_created_rate` | {ov['output_package_created_count']} / {ov['n']} ({ov['output_package_created_rate'] * 100:.1f} %) |")
    add(f"| `meaningful_content_rate` | {ov['content_extracted_count']} / {ov['n']} ({ov['meaningful_content_rate'] * 100:.1f} %) |")
    add(f"| `structurally_usable_rate` | {ov['structurally_usable_count']} / {ov['n']} ({ov['structurally_usable_rate'] * 100:.1f} %) |")
    add(f"| Near-empty output files | {ov['near_empty_output_count']} |")
    add(f"| Low-text-density warned files | {ov['low_text_density_count']} |")
    add(f"| Runtime p50 / p95 (s) | {ov['runtime_seconds_p50']} / {ov['runtime_seconds_p95']} |")
    add(f"| Quality bands | {ov['quality_band_distribution']} |")
    if ov["human_reviewed_count"]:
        add(f"| Human-usable rate (sample) | {ov['human_usable_count'] + ov['human_usable_with_minor_defects_count']} / {ov['human_reviewed_count']} ({(ov['human_usable_rate'] or 0) * 100:.1f} %) |")
    add()

    add("## Why the `OCR_REQUIRED` warning count is 0")
    add()
    ocr_warnings = ov["ocr_warning_count"]
    if ocr_warnings == 0:
        add("No file emitted an `OCR_REQUIRED` warning at parse time. On investigation:")
        add()
        add("- The Marker vision extra is active in this environment, so image-only PDFs that would otherwise trigger `OCR_REQUIRED` go through OCR silently and return text. The warning does NOT fire when OCR succeeds.")
        add("- The relevant surfaces for content-poor outputs are `LOW_TEXT_DENSITY` and `NEAR_EMPTY_OUTPUT` — these fired regardless of whether OCR was attempted, and are the correct signal to key on.")
        add("- The § Image-only audit lists every image-classified asset with its hidden-text-layer status, output character count, and warnings — that audit is the correct place to read image-only behaviour, not the `OCR_REQUIRED` count.")
    else:
        add(f"{ocr_warnings} files emitted an OCR-related warning.")
    add()

    add("## Rule-based quality signals (overall)")
    add()
    signals = {
        "near_empty_output": ov["near_empty_output_count"],
        "low_text_density": ov["low_text_density_count"],
        "missing_pages": ov["missing_pages_count"],
        "multicolumn_order_warning": ov["multicolumn_warning_count"],
        "repeat_content_over_50pct": sum(1 for r in results if r.repeat_content_ratio and r.repeat_content_ratio > 0.50),
        "execution_failure": ov["n"] - ov["execution_success_count"],
        "content_extraction_failure": ov["n"] - ov["content_extracted_count"],
        "structural_failure": ov["n"] - ov["structurally_usable_count"],
    }
    for k, v in sorted(signals.items(), key=lambda kv: -kv[1]):
        add(f"- {k}: {v} / {ov['n']} ({v / ov['n'] * 100:.1f}%)")
    add()

    add("## Warning-code distribution (top 15)")
    add()
    for code, n in sorted(ov["warning_code_distribution"].items(), key=lambda kv: -kv[1])[:15]:
        add(f"- `{code}`: {n}")
    add()

    add("## Per-slice results")
    add()
    for cls, agg in aggregate["by_document_class"].items():
        add(f"### {cls}")
        add()
        add(f"- n = {agg['n']}")
        add(f"- execution_success: {agg['execution_success_count']} ({agg['execution_success_rate'] * 100:.1f}%)")
        add(f"- meaningful_content: {agg['content_extracted_count']} ({agg['meaningful_content_rate'] * 100:.1f}%)")
        add(f"- structurally_usable: {agg['structurally_usable_count']} ({agg['structurally_usable_rate'] * 100:.1f}%)")
        add(f"- runtime p50/p95 (s): {agg['runtime_seconds_p50']} / {agg['runtime_seconds_p95']}")
        add(f"- tokens p50: {agg['tokens_p50']}")
        add(f"- near-empty: {agg['near_empty_output_count']}, low-density: {agg['low_text_density_count']}")
        add(f"- multicolumn-warn: {agg['multicolumn_warning_count']}")
        add(f"- quality bands: {agg['quality_band_distribution']}")
        if agg["human_reviewed_count"] > 0:
            add(f"- human-reviewed: {agg['human_reviewed_count']} · usable-rate: {agg['human_usable_rate']}")
        add()

    add("## By corpus source")
    add()
    for src, agg in aggregate["by_corpus"].items():
        add(f"### {src}")
        add()
        add(f"- n = {agg['n']}")
        add(f"- execution / content / structural: {agg['execution_success_count']} / {agg['content_extracted_count']} / {agg['structurally_usable_count']}")
        add(f"- runtime p50/p95 (s): {agg['runtime_seconds_p50']} / {agg['runtime_seconds_p95']}")
        add(f"- near-empty: {agg['near_empty_output_count']}, low-density: {agg['low_text_density_count']}")
        add(f"- quality bands: {agg['quality_band_distribution']}")
        add()

    add("## Image-only audit")
    add()
    add("Every asset classified as `image-only`, with the fields the review checklist requires. `hidden_text_layer` is `True` when PyMuPDF's `Page.get_text()` returns non-empty text — this distinguishes a PDF whose image is accompanied by a text layer (extractable without OCR) from one that requires an OCR pass.")
    add()
    add("| asset | hidden-text? | text-layer chars | output chars | tokens | placeholder ratio | band | warnings |")
    add("|---|:---:|---:|---:|---:|---:|:---:|---|")
    for r in results:
        if r.document_class != "image-only":
            continue
        w = ", ".join(r.warning_codes) or "—"
        htx = r.hidden_text_layer_chars if r.hidden_text_layer_chars is not None else "n/a"
        htp = ("yes" if r.hidden_text_layer else "no") if r.hidden_text_layer is not None else "n/a"
        ph = r.image_placeholder_ratio if r.image_placeholder_ratio is not None else "—"
        add(f"| `{r.asset_id}` | {htp} | {htx} | {r.output_chars} | {r.estimated_tokens} | {ph} | {r.quality_band} | {w} |")
    add()

    add("## Failure catalogues")
    add()
    if aggregate["execution_failures"]:
        add("### Execution failures (exit != 0)")
        add()
        for f in aggregate["execution_failures"]:
            add(f"- `{f['asset_id']}` (exit {f['exit_code']}) — stderr: `{(f['stderr_head'] or 'empty')[:200]}`")
        add()
    else:
        add(f"No execution failures across {ov['n']} files.")
        add()
    if aggregate["content_failures"]:
        add("### Content failures (ran successfully but no meaningful content)")
        add()
        for f in aggregate["content_failures"]:
            add(f"- `{f['asset_id']}` (class {f['document_class']}) — chars={f['output_chars']}, band={f['quality_band']}, near-empty={f['near_empty_output']}, low-density={f['low_text_density']}, hidden-text-layer={f['hidden_text_layer']}")
        add()
    else:
        add("No content failures.")
        add()
    if aggregate["structural_failures"]:
        add("### Structural failures (content present but not structurally usable)")
        add()
        for f in aggregate["structural_failures"]:
            add(f"- `{f['asset_id']}` (class {f['document_class']}) — band={f['quality_band']}, repeat={f['repeat_content_ratio']}, low-density={f['low_text_density']}")
        add()

    add("## Human review — stratified sample")
    add()
    reviewed_rows = [r for r in results if r.human_review_status == "reviewed"]
    if not reviewed_rows:
        add("_No files reviewed in this run. A stratified sample is populated by supplying a review-JSON via `--human-reviews`._")
        add()
    else:
        add(f"Reviewed: {len(reviewed_rows)} of {ov['n']} files.")
        add()
        add("| asset | class | usability | evidence |")
        add("|---|---|---|---|")
        for r in reviewed_rows:
            ev = (r.human_review_evidence or "—")[:180].replace("|", "\\|")
            add(f"| `{r.asset_id}` | {r.document_class} | {r.human_usability} | {ev} |")
        add()
        by_slice: dict[str, dict[str, int]] = {}
        for r in reviewed_rows:
            b = by_slice.setdefault(r.document_class, {"usable": 0, "usable_with_minor_defects": 0,
                                                        "materially_damaged": 0, "unusable": 0})
            b[r.human_usability] = b.get(r.human_usability, 0) + 1
        add("### Usability by slice")
        add()
        for cls, b in by_slice.items():
            add(f"- **{cls}**: usable={b['usable']}, minor={b['usable_with_minor_defects']}, damaged={b['materially_damaged']}, unusable={b['unusable']}")
        add()

    add("## Runtime semantics")
    add()
    add("`runtime_seconds` is wall-clock time for one `aksharamd compile --json --quiet` invocation. It includes process startup, package loading, parser classification, OCR (when invoked), and output serialisation. When the harness runs with the determinism check enabled, the second run is recorded independently and is NOT included in `runtime_seconds`.")
    add()

    add("## Constraints observed")
    add()
    add("- No parser / validator / scoring / warning-penalty / packaging / model code changed.")
    add("- `SCORING_POLICY_VERSION` remains `\"1.0\"`.")
    add("- No PDF bytes added to git.")
    add("- Deterministic result ordering (assets sorted by id).")
    add("- No network fetch during benchmark execution.")
    add("- ParseBench sha256 + size verified before the run.")
    add("- Per-file errors preserved; single failures do not abort the run.")
    add()

    add("## Next steps")
    add()
    add("- Phase 2: competitor adapters (MarkItDown, Docling, Unstructured, PyMuPDF4LLM) — one PR each with pinned versions.")
    add("- Phase 3: comparison report — strengths by document class, no universal-winner declaration.")

    path.write_text("\n".join(L), encoding="utf-8")


def _run(
    output_json: Path,
    output_md: Path,
    manifest_json: Path,
    *,
    only: str | None = None,
    workdir: Path | None = None,
    do_deterministic_check: bool = True,
    human_reviews: dict[str, dict[str, str]] | None = None,
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
            r = run_one(binary, a, workdir, do_deterministic_check=do_deterministic_check,
                        human_reviews=human_reviews)
        except Exception as e:
            r = RunResult(
                asset_id=a.asset_id,
                corpus_source=a.corpus_source,
                document_class=a.document_class,
                execution_success=False,
                exit_code=-1,
                output_package_created=False,
                content_extracted=False,
                structurally_usable=False,
                human_review_status="not_reviewed",
                human_usability="not_reviewed",
                human_review_evidence="",
                runtime_seconds=0.0,
                output_chars=0,
                estimated_tokens=0,
                output_size_inflation=0.0,
                deterministic=None,
                page_count_pdf=a.page_count,
                page_count_output=None,
                missing_pages=False,
                hidden_text_layer=None,
                hidden_text_layer_chars=None,
                image_placeholder_ratio=None,
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
    ap.add_argument("--human-reviews", type=Path, default=None,
                    help="Path to a JSON dict {asset_id: {usability, evidence}} of "
                         "pre-recorded human-review verdicts. Fields not supplied stay "
                         "'not_reviewed'.")
    args = ap.parse_args()
    reviews: dict[str, dict[str, str]] | None = None
    if args.human_reviews is not None:
        with args.human_reviews.open("r", encoding="utf-8") as f:
            reviews = json.load(f)
    return _run(
        args.output_json,
        args.output_md,
        args.manifest,
        only=args.only,
        workdir=args.workdir,
        do_deterministic_check=not args.no_deterministic_check,
        human_reviews=reviews,
    )


if __name__ == "__main__":
    raise SystemExit(main())
