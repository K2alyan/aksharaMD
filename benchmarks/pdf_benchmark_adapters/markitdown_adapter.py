"""MarkItDown adapter for PDF Benchmark v1 Phase 2 (Issue #68).

Runs MarkItDown's ``convert`` on the same 45 eligible assets that
AksharaMD Phase 1 and PyMuPDF4LLM (Phase 2 first adapter) consumed.
Reports **tool-neutral** metrics only. No comparison ranking is
produced here — Phase 3 will combine adapters after each is
independently reviewed.

**No AksharaMD production code changes.** No parser, validator,
scoring, warning-penalty, or ``SCORING_POLICY`` modifications.
``SCORING_POLICY_VERSION`` remains ``"1.0"``.

## Configuration

- MarkItDown constructor: ``MarkItDown()`` with all defaults.
  ``enable_builtins`` and ``enable_plugins`` left at ``None`` (=
  library default; builtins registered, plugins disabled unless
  installed).
- No LLM client, no LLM model, no LLM prompt configured — the
  ``_llm_client`` attribute is ``None``. MarkItDown does NOT call any
  external service; the run is fully offline.
- PDF backend: MarkItDown's built-in ``PdfConverter`` (uses pdfminer
  under the hood). No OCR / vision configuration is enabled here; if
  MarkItDown had optional Document-Intelligence extras, they are NOT
  activated for this run.

Records the pinned MarkItDown version + PDF backend version in every
output artifact.

## Evaluation semantics differences vs. AksharaMD Phase 1

- **No AksharaMD readiness score, quality band, or warning codes.**
- **`near_empty_equivalent` and `low_density_equivalent`** substitute
  for `NEAR_EMPTY_OUTPUT` / `LOW_TEXT_DENSITY`, using the same
  thresholds as the PyMuPDF4LLM adapter for cross-competitor parity.
- **Runtime boundary** is a single in-process ``convert`` call; the
  deterministic-check recompile is timed separately (same discipline
  as PyMuPDF4LLM).

## Human-review parity

Uses the same reviewed asset set as PR #70; the primary comparison in
the report is on the intersection of assets reviewed for all three
adapters (AksharaMD, PyMuPDF4LLM, MarkItDown). Tool-specific
supplementary reviews are preserved separately.

Refuses to run if MarkItDown is not installed.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"
_AKSHARAMD_REVIEWS = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_human_reviews.json"
_PYMUPDF4LLM_REVIEWS = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_pymupdf4llm_human_reviews.json"


# ── Metrics (identical to PyMuPDF4LLM adapter for cross-competitor parity) ──


_MIN_MEANINGFUL_CHARS = 200
_IMG_PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

_USABLE_ENUM = {"usable", "usable_with_minor_defects"}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _repeat_content_ratio(text: str, ngram: int = 4) -> float:
    tokens = text.split()
    if len(tokens) < ngram * 2:
        return 0.0
    counts: dict[tuple[str, ...], int] = {}
    for i in range(len(tokens) - ngram + 1):
        key = tuple(tokens[i:i + ngram])
        counts[key] = counts.get(key, 0) + 1
    dup_windows = sum(c for c in counts.values() if c > 1)
    total = len(tokens) - ngram + 1
    return dup_windows / total if total else 0.0


def _image_placeholder_ratio(text: str) -> float | None:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    imgs = sum(1 for ln in lines if _IMG_PLACEHOLDER_RE.search(ln))
    return round(imgs / len(lines), 4)


def _hidden_text_layer_chars(p: Path) -> tuple[bool | None, int | None]:
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError:
        return None, None
    try:
        with fitz.open(str(p)) as doc:
            total = sum(len(page.get_text() or "") for page in doc)
            return (total > 0, total)
    except Exception:
        return None, None


# ── Result records ───────────────────────────────────────────────────────


@dataclass
class RunResult:
    asset_id: str
    corpus_source: str
    document_class: str
    execution_success: bool
    exception: str
    output_package_created: bool
    content_extracted: bool
    structurally_usable: bool
    human_review_status: str
    human_usability: str
    human_review_evidence: str
    runtime_seconds: float
    output_chars: int
    non_whitespace_chars: int
    estimated_tokens: int
    output_size_inflation: float
    deterministic: bool | None
    page_count_pdf: int | None
    hidden_text_layer: bool | None
    hidden_text_layer_chars: int | None
    image_placeholder_ratio: float | None
    repeat_content_ratio: float | None = None
    near_empty_equivalent: bool = False
    low_density_equivalent: bool = False
    tool_signals: dict[str, Any] = field(default_factory=dict)


# ── Adapter ──────────────────────────────────────────────────────────────


def _load_manifest() -> dict[str, Any]:
    if not _MANIFEST.exists():
        raise RuntimeError(f"manifest not present: {_MANIFEST}")
    with _MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dependency_versions() -> dict[str, str]:
    keys = ("markitdown", "pdfminer.six", "pymupdf", "aksharamd")
    out: dict[str, str] = {}
    for k in keys:
        try:
            from importlib.metadata import version
            out[k] = version(k)
        except Exception:
            out[k] = "unknown"
    return out


# Reuse one MarkItDown instance across all assets — avoids per-file
# converter re-registration + confirms no cross-file state leaks.
_MD_INSTANCE = None


def _get_markitdown():
    global _MD_INSTANCE
    if _MD_INSTANCE is None:
        import markitdown  # type: ignore[import-untyped]
        _MD_INSTANCE = markitdown.MarkItDown()
    return _MD_INSTANCE


def _convert_once(pdf: Path) -> tuple[str, str, float, str | None]:
    """Call ``MarkItDown.convert`` once. Returns
    ``(text_content, exception_or_empty, elapsed, title)``.
    """
    md = _get_markitdown()
    t0 = time.perf_counter()
    exc = ""
    text = ""
    title: str | None = None
    try:
        result = md.convert(str(pdf))
        text = result.text_content or ""
        title = getattr(result, "title", None)
    except Exception as e:
        exc = f"{type(e).__name__}: {e}"[:400]
    return text, exc, time.perf_counter() - t0, title


def run_one(
    asset: dict[str, Any],
    *,
    do_deterministic_check: bool,
    human_reviews: dict[str, dict[str, str]] | None,
) -> RunResult:
    aid = asset["asset_id"]
    pdf = Path(asset["pdf_path"])
    text, exc, elapsed, title = _convert_once(pdf)
    execution_success = (exc == "" and text is not None)
    doc_md = text or ""
    output_package_created = execution_success and bool(doc_md)
    non_ws = sum(1 for c in doc_md if not c.isspace())
    output_chars = len(doc_md)
    tokens = _estimate_tokens(doc_md)
    size_bytes = int(asset.get("size_bytes") or 0)
    inflation = (output_chars / size_bytes) if size_bytes else 0.0

    near_empty_equivalent = non_ws < 50
    low_density_equivalent = (
        size_bytes > 0 and inflation < 0.0005 and non_ws < 400
    )

    content_extracted = (
        output_package_created
        and non_ws >= _MIN_MEANINGFUL_CHARS
        and not near_empty_equivalent
    )

    repeat_ratio = _repeat_content_ratio(doc_md)
    repeat_gate_ok = (len(doc_md.split()) < 100 or repeat_ratio < 0.50)
    has_text_layer, hidden_text_chars = _hidden_text_layer_chars(pdf)
    structurally_usable = (
        content_extracted
        and repeat_gate_ok
        and not (low_density_equivalent and (has_text_layer is not False))
    )

    deterministic: bool | None = None
    if output_package_created and do_deterministic_check:
        text2, _exc2, _e2, _t2 = _convert_once(pdf)
        deterministic = (doc_md == text2)

    review = (human_reviews or {}).get(aid, {})
    review_status = "reviewed" if review else "not_reviewed"

    return RunResult(
        asset_id=aid,
        corpus_source=asset.get("corpus_source", ""),
        document_class=asset.get("document_class", "unknown"),
        execution_success=execution_success,
        exception=exc,
        output_package_created=output_package_created,
        content_extracted=content_extracted,
        structurally_usable=structurally_usable,
        human_review_status=review_status,
        human_usability=review.get("usability", "not_reviewed"),
        human_review_evidence=review.get("evidence", ""),
        runtime_seconds=round(elapsed, 3),
        output_chars=output_chars,
        non_whitespace_chars=non_ws,
        estimated_tokens=tokens,
        output_size_inflation=round(inflation, 4),
        deterministic=deterministic,
        page_count_pdf=asset.get("page_count"),
        hidden_text_layer=has_text_layer,
        hidden_text_layer_chars=hidden_text_chars,
        image_placeholder_ratio=_image_placeholder_ratio(doc_md),
        repeat_content_ratio=round(repeat_ratio, 4),
        near_empty_equivalent=near_empty_equivalent,
        low_density_equivalent=low_density_equivalent,
        tool_signals={
            "markdown_line_count": doc_md.count("\n") + (1 if doc_md else 0),
            "title": title,
        },
    )


# ── Aggregation ─────────────────────────────────────────────────────────


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    k = (len(ys) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ys) - 1)
    return ys[lo] + (ys[hi] - ys[lo]) * (k - lo)


def _bucket(rows: list[RunResult]) -> dict[str, Any]:
    n = len(rows)
    exec_ok = sum(1 for r in rows if r.execution_success)
    pkg = sum(1 for r in rows if r.output_package_created)
    content = sum(1 for r in rows if r.content_extracted)
    struct = sum(1 for r in rows if r.structurally_usable)
    near_empty = sum(1 for r in rows if r.near_empty_equivalent)
    low_density = sum(1 for r in rows if r.low_density_equivalent)
    runtimes = [r.runtime_seconds for r in rows if r.execution_success]
    tokens = [r.estimated_tokens for r in rows if r.execution_success]
    inflations = [r.output_size_inflation for r in rows if r.execution_success]
    dets = [r.deterministic for r in rows if r.deterministic is not None]
    review_rows = [r for r in rows if r.human_review_status == "reviewed"]
    usable = sum(1 for r in review_rows if r.human_usability == "usable")
    usable_minor = sum(1 for r in review_rows if r.human_usability == "usable_with_minor_defects")
    materially_damaged = sum(1 for r in review_rows if r.human_usability == "materially_damaged")
    unusable = sum(1 for r in review_rows if r.human_usability == "unusable")
    return {
        "n": n,
        "execution_success_count": exec_ok,
        "execution_success_rate": round(exec_ok / n, 4) if n else 0.0,
        "output_package_created_count": pkg,
        "output_package_created_rate": round(pkg / n, 4) if n else 0.0,
        "content_extracted_count": content,
        "meaningful_content_rate": round(content / n, 4) if n else 0.0,
        "structurally_usable_count": struct,
        "structurally_usable_rate": round(struct / n, 4) if n else 0.0,
        "near_empty_equivalent_count": near_empty,
        "low_density_equivalent_count": low_density,
        "human_reviewed_count": len(review_rows),
        "human_usable_count": usable,
        "human_usable_with_minor_defects_count": usable_minor,
        "human_materially_damaged_count": materially_damaged,
        "human_unusable_count": unusable,
        "human_usable_rate": round((usable + usable_minor) / len(review_rows), 4) if review_rows else None,
        "runtime_seconds_mean": round(statistics.mean(runtimes), 3) if runtimes else None,
        "runtime_seconds_p50": round(statistics.median(runtimes), 3) if runtimes else None,
        "runtime_seconds_p95": round(_pct(runtimes, 95), 3) if runtimes else None,
        "tokens_mean": int(statistics.mean(tokens)) if tokens else None,
        "tokens_p50": int(statistics.median(tokens)) if tokens else None,
        "output_size_inflation_mean": round(statistics.mean(inflations), 4) if inflations else None,
        "deterministic_rate": round(sum(dets) / len(dets), 4) if dets else None,
    }


def _matched_pair_paired(
    results: list[RunResult],
    other_reviews_path: Path,
    other_label: str,
) -> dict[str, Any]:
    """Two-way paired outcome on the intersection of MarkItDown reviews
    and one other adapter's reviews (AksharaMD or PyMuPDF4LLM).
    """
    if not other_reviews_path.exists():
        return {"error": f"{other_label} reviews not found at {other_reviews_path}"}
    with other_reviews_path.open("r", encoding="utf-8") as f:
        other = json.load(f)
    other_ids = {k for k in other if not k.startswith("_")}
    md_reviewed = {r.asset_id: r.human_usability for r in results
                   if r.human_review_status == "reviewed"}
    matched = other_ids & set(md_reviewed)
    both = 0
    other_only: list[str] = []
    md_only: list[str] = []
    neither: list[str] = []
    for aid in sorted(matched):
        ou = other[aid].get("usability", "not_reviewed")
        mu = md_reviewed[aid]
        ook = ou in _USABLE_ENUM
        mok = mu in _USABLE_ENUM
        if ook and mok:
            both += 1
        elif ook and not mok:
            other_only.append(aid)
        elif not ook and mok:
            md_only.append(aid)
        else:
            neither.append(aid)
    return {
        "matched_sample_size": len(matched),
        f"{other_label}_usable_count": both + len(other_only),
        "markitdown_usable_count": both + len(md_only),
        "both_usable": both,
        f"{other_label}_only_usable": other_only,
        "markitdown_only_usable": md_only,
        "neither_usable": neither,
    }


def _three_way_paired(results: list[RunResult]) -> dict[str, Any]:
    """Paired outcome on the intersection of AksharaMD + PyMuPDF4LLM +
    MarkItDown human reviews. Reports 8 buckets (2^3).
    """
    if not (_AKSHARAMD_REVIEWS.exists() and _PYMUPDF4LLM_REVIEWS.exists()):
        return {"error": "either aksharamd or pymupdf4llm reviews missing"}
    with _AKSHARAMD_REVIEWS.open("r", encoding="utf-8") as f:
        ax = json.load(f)
    with _PYMUPDF4LLM_REVIEWS.open("r", encoding="utf-8") as f:
        pm = json.load(f)
    md_reviewed = {r.asset_id: r.human_usability for r in results
                   if r.human_review_status == "reviewed"}
    ax_ids = {k for k in ax if not k.startswith("_")}
    pm_ids = {k for k in pm if not k.startswith("_")}
    matched = ax_ids & pm_ids & set(md_reviewed)
    buckets: dict[str, list[str]] = {
        "all_three_usable": [],
        "aksharamd_and_pymupdf4llm_only": [],
        "aksharamd_and_markitdown_only": [],
        "pymupdf4llm_and_markitdown_only": [],
        "only_aksharamd_usable": [],
        "only_pymupdf4llm_usable": [],
        "only_markitdown_usable": [],
        "none_usable": [],
    }
    for aid in sorted(matched):
        a = ax[aid].get("usability", "not_reviewed") in _USABLE_ENUM
        p = pm[aid].get("usability", "not_reviewed") in _USABLE_ENUM
        m = md_reviewed[aid] in _USABLE_ENUM
        if a and p and m:
            buckets["all_three_usable"].append(aid)
        elif a and p and not m:
            buckets["aksharamd_and_pymupdf4llm_only"].append(aid)
        elif a and m and not p:
            buckets["aksharamd_and_markitdown_only"].append(aid)
        elif p and m and not a:
            buckets["pymupdf4llm_and_markitdown_only"].append(aid)
        elif a and not p and not m:
            buckets["only_aksharamd_usable"].append(aid)
        elif p and not a and not m:
            buckets["only_pymupdf4llm_usable"].append(aid)
        elif m and not a and not p:
            buckets["only_markitdown_usable"].append(aid)
        else:
            buckets["none_usable"].append(aid)
    return {
        "three_way_matched_sample_size": len(matched),
        **{k: v for k, v in buckets.items()},
        **{f"{k}_count": len(v) for k, v in buckets.items()},
    }


def _aggregate(results: list[RunResult]) -> dict[str, Any]:
    ag: dict[str, Any] = {"overall": _bucket(results)}
    ag["by_corpus"] = {
        s: _bucket([r for r in results if r.corpus_source == s])
        for s in sorted({r.corpus_source for r in results})
    }
    ag["by_document_class"] = {
        c: _bucket([r for r in results if r.document_class == c])
        for c in sorted({r.document_class for r in results})
    }
    ag["matched_sample_vs_aksharamd_phase1"] = _matched_pair_paired(
        results, _AKSHARAMD_REVIEWS, "aksharamd"
    )
    ag["matched_sample_vs_pymupdf4llm"] = _matched_pair_paired(
        results, _PYMUPDF4LLM_REVIEWS, "pymupdf4llm"
    )
    ag["three_way_paired_vs_aksharamd_and_pymupdf4llm"] = _three_way_paired(results)
    ag["execution_failures"] = [
        {"asset_id": r.asset_id, "exception": r.exception}
        for r in results if not r.execution_success
    ]
    ag["content_failures"] = [
        {
            "asset_id": r.asset_id,
            "document_class": r.document_class,
            "output_chars": r.output_chars,
            "near_empty_equivalent": r.near_empty_equivalent,
            "low_density_equivalent": r.low_density_equivalent,
            "hidden_text_layer": r.hidden_text_layer,
        }
        for r in results
        if r.execution_success and not r.content_extracted
    ]
    ag["structural_failures"] = [
        {
            "asset_id": r.asset_id,
            "document_class": r.document_class,
            "repeat_content_ratio": r.repeat_content_ratio,
            "low_density_equivalent": r.low_density_equivalent,
        }
        for r in results
        if r.content_extracted and not r.structurally_usable
    ]
    return ag


# ── Report ───────────────────────────────────────────────────────────────


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _render_report(manifest: dict, results: list[RunResult], aggregate: dict, path: Path,
                    tool_version: str) -> None:
    L: list[str] = []
    ov = aggregate["overall"]

    def add(s: str = "") -> None:
        L.append(s)

    add(f"# PDF Benchmark v1 — MarkItDown adapter ({time.strftime('%Y-%m-%d')})")
    add()
    add(f"**Tool:** MarkItDown `{tool_version}`")
    add(f"**Commit under evaluation:** `{manifest['commit_under_evaluation']}`")
    add(f"**Python:** {manifest['python_version']} · **Platform:** {manifest['platform']}")
    add()
    add("**No AksharaMD production code changes.** `SCORING_POLICY_VERSION` remains `\"1.0\"`. Phase 2 of Issue #68 — second competitor adapter in isolation, no cross-parser ranking here.")
    add()
    add("## Configuration")
    add()
    add("- `MarkItDown()` constructor with all defaults (builtins registered, plugins disabled unless installed).")
    add("- **No LLM client** — `_llm_client` is `None`. No external service call.")
    add("- **No OCR / vision extras enabled** for this run.")
    add("- **No Document-Intelligence extras** activated.")
    add("- PDF backend: MarkItDown's built-in `PdfConverter`.")
    add("- Fully offline — checked via `_llm_client is None` at run time.")
    add()
    add("## Evaluation semantics — differences from AksharaMD Phase 1")
    add()
    add("This adapter deliberately does NOT reuse AksharaMD-specific fields:")
    add()
    add("- **No readiness score / quality band / warning codes.**")
    add("- **`near_empty_equivalent`** = non-whitespace char count < 50 (same as PyMuPDF4LLM adapter).")
    add("- **`low_density_equivalent`** = `output_size_inflation < 0.0005` AND `non_whitespace_chars < 400`.")
    add("- All four success levels: identical to Phase 1 subject to those substitutions.")
    add("- Test `test_artifact_four_success_levels_are_monotone` enforces `execution ≥ package ≥ content ≥ structural` per row.")
    add()
    add("## Interpretation guardrails")
    add()
    add("- **`meaningful_content` and `structurally_usable` are benchmark-rule classifications** — tool-neutral deterministic gates. NOT substitutes for human judgment.")
    add("- **AksharaMD readiness scores / warning codes are NOT applied to MarkItDown.**")
    add("- **Human-usable rate is a sample rate.** See § Matched human-review parity.")
    add("- **No cross-parser winner declaration.** Phase 3 will discuss slice-level differences.")
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
    add(f"| Near-empty-equivalent files | {ov['near_empty_equivalent_count']} |")
    add(f"| Low-density-equivalent files | {ov['low_density_equivalent_count']} |")
    add(f"| Runtime p50 / p95 (s) | {ov['runtime_seconds_p50']} / {ov['runtime_seconds_p95']} |")
    add(f"| Deterministic rate | {ov['deterministic_rate']} |")
    if ov["human_reviewed_count"]:
        add(f"| Human-usable rate (sample) | {ov['human_usable_count'] + ov['human_usable_with_minor_defects_count']} / {ov['human_reviewed_count']} ({(ov['human_usable_rate'] or 0) * 100:.1f} %) |")
    add()

    add("## Matched human-review parity vs. AksharaMD Phase 1")
    add()
    mp = aggregate.get("matched_sample_vs_aksharamd_phase1", {})
    if mp and "error" not in mp:
        add("| Metric | Value |")
        add("|---|---:|")
        add(f"| Matched sample size | **{mp['matched_sample_size']}** |")
        add(f"| AksharaMD usable (matched) | {mp['aksharamd_usable_count']} |")
        add(f"| MarkItDown usable (matched) | {mp['markitdown_usable_count']} |")
        add(f"| Both usable | {mp['both_usable']} |")
        add(f"| AksharaMD only usable | {len(mp['aksharamd_only_usable'])} |")
        add(f"| MarkItDown only usable | {len(mp['markitdown_only_usable'])} |")
        add(f"| Neither usable | {len(mp['neither_usable'])} |")
        add()
        if mp["aksharamd_only_usable"]:
            add("**AksharaMD-only usable:**")
            for aid in mp["aksharamd_only_usable"]:
                add(f"- `{aid}`")
            add()
        if mp["markitdown_only_usable"]:
            add("**MarkItDown-only usable:**")
            for aid in mp["markitdown_only_usable"]:
                add(f"- `{aid}`")
            add()

    add("## Matched human-review parity vs. PyMuPDF4LLM")
    add()
    mp2 = aggregate.get("matched_sample_vs_pymupdf4llm", {})
    if mp2 and "error" not in mp2:
        add("| Metric | Value |")
        add("|---|---:|")
        add(f"| Matched sample size | **{mp2['matched_sample_size']}** |")
        add(f"| PyMuPDF4LLM usable (matched) | {mp2['pymupdf4llm_usable_count']} |")
        add(f"| MarkItDown usable (matched) | {mp2['markitdown_usable_count']} |")
        add(f"| Both usable | {mp2['both_usable']} |")
        add(f"| PyMuPDF4LLM only usable | {len(mp2['pymupdf4llm_only_usable'])} |")
        add(f"| MarkItDown only usable | {len(mp2['markitdown_only_usable'])} |")
        add(f"| Neither usable | {len(mp2['neither_usable'])} |")
        add()

    add("## Three-way paired outcome — AksharaMD ∩ PyMuPDF4LLM ∩ MarkItDown")
    add()
    tw = aggregate.get("three_way_paired_vs_aksharamd_and_pymupdf4llm", {})
    if tw and "error" not in tw:
        add(f"Three-way matched sample size: **{tw['three_way_matched_sample_size']}**.")
        add()
        add("| Bucket | Count |")
        add("|---|---:|")
        add(f"| All three usable | {tw['all_three_usable_count']} |")
        add(f"| AksharaMD + PyMuPDF4LLM only | {tw['aksharamd_and_pymupdf4llm_only_count']} |")
        add(f"| AksharaMD + MarkItDown only | {tw['aksharamd_and_markitdown_only_count']} |")
        add(f"| PyMuPDF4LLM + MarkItDown only | {tw['pymupdf4llm_and_markitdown_only_count']} |")
        add(f"| Only AksharaMD | {tw['only_aksharamd_usable_count']} |")
        add(f"| Only PyMuPDF4LLM | {tw['only_pymupdf4llm_usable_count']} |")
        add(f"| Only MarkItDown | {tw['only_markitdown_usable_count']} |")
        add(f"| None usable | {tw['none_usable_count']} |")
        add()

    add("## Runtime-boundary parity")
    add()
    add("Reported `runtime_seconds` is one primary parse per asset. Millisecond-level comparison across adapters is not defensible; the tools use different process boundaries.")
    add()
    add("| Included in runtime | AksharaMD Phase 1 | PyMuPDF4LLM | MarkItDown (this run) |")
    add("|---|:---:|:---:|:---:|")
    add("| Process startup | **yes** (subprocess) | no | no |")
    add("| Import + package loading | **yes** | no | no |")
    add("| Converter/backend init | in subprocess | no (reused) | no (reused instance) |")
    add("| PDF parsing | yes | yes | yes |")
    add("| OCR | yes (Marker) | no | no |")
    add("| LLM call | no | no | no |")
    add("| Markdown generation | yes | yes | yes |")
    add("| Output serialisation | yes (disk write) | no (in-memory string) | no (in-memory string) |")
    add("| Checksum verification | no | no | no |")
    add("| Deterministic second parse | no | no | no |")
    add()
    add("**Consequence.** AksharaMD's per-asset runtime includes CLI subprocess startup + package load + disk write; PyMuPDF4LLM and MarkItDown are direct in-process library calls with a single shared instance re-used across assets. **No exact speed ratio is claimed** between adapters. Latency-category discussion is deferred to Phase 3.")
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
        add(f"- near-empty: {agg['near_empty_equivalent_count']}, low-density: {agg['low_density_equivalent_count']}")
        if agg["human_reviewed_count"] > 0:
            add(f"- human-reviewed: {agg['human_reviewed_count']} · usable-rate: {agg['human_usable_rate']}")
        add()

    add("## Image-only audit")
    add()
    add("| asset | hidden-text? | text-layer chars | output chars | tokens | placeholder ratio | warnings-equivalent |")
    add("|---|:---:|---:|---:|---:|---:|---|")
    for r in results:
        if r.document_class != "image-only":
            continue
        htx = r.hidden_text_layer_chars if r.hidden_text_layer_chars is not None else "n/a"
        htp = ("yes" if r.hidden_text_layer else "no") if r.hidden_text_layer is not None else "n/a"
        ph = r.image_placeholder_ratio if r.image_placeholder_ratio is not None else "—"
        w_parts: list[str] = []
        if r.near_empty_equivalent:
            w_parts.append("near_empty_equivalent")
        if r.low_density_equivalent:
            w_parts.append("low_density_equivalent")
        w = ", ".join(w_parts) or "—"
        add(f"| `{r.asset_id}` | {htp} | {htx} | {r.output_chars} | {r.estimated_tokens} | {ph} | {w} |")
    add()

    add("## Failure catalogues")
    add()
    if aggregate["execution_failures"]:
        add("### Execution failures (function raised)")
        for f in aggregate["execution_failures"]:
            add(f"- `{f['asset_id']}` — {f['exception']}")
        add()
    else:
        add(f"No execution failures across {ov['n']} files.")
        add()
    if aggregate["content_failures"]:
        add("### Content failures (returned but content-poor)")
        for f in aggregate["content_failures"]:
            add(f"- `{f['asset_id']}` (class {f['document_class']}) — chars={f['output_chars']}, near-empty-equiv={f['near_empty_equivalent']}, low-density-equiv={f['low_density_equivalent']}, hidden-text-layer={f['hidden_text_layer']}")
        add()
    if aggregate["structural_failures"]:
        add("### Structural failures (content but not structurally usable)")
        for f in aggregate["structural_failures"]:
            add(f"- `{f['asset_id']}` (class {f['document_class']}) — repeat={f['repeat_content_ratio']}, low-density-equiv={f['low_density_equivalent']}")
        add()

    add("## Human review — stratified sample")
    add()
    reviewed_rows = [r for r in results if r.human_review_status == "reviewed"]
    if not reviewed_rows:
        add("_No files reviewed in this run._")
        add()
    else:
        add(f"Reviewed: {len(reviewed_rows)} files (same asset ids as AksharaMD Phase 1 / PyMuPDF4LLM where available; every judgment is on MarkItDown's own output).")
        add()
        add("| asset | class | usability | evidence |")
        add("|---|---|---|---|")
        for r in reviewed_rows:
            ev = (r.human_review_evidence or "—")[:180].replace("|", "\\|")
            add(f"| `{r.asset_id}` | {r.document_class} | {r.human_usability} | {ev} |")
        add()

    add("## Constraints observed")
    add()
    add("- No AksharaMD parser / validator / scoring / warning-penalty / packaging / model code changed.")
    add("- `SCORING_POLICY_VERSION` remains `\"1.0\"`.")
    add("- Same 45-asset frozen manifest as AksharaMD Phase 1 and PyMuPDF4LLM.")
    add("- Same checksum-verified ParseBench cache.")
    add("- No network fetch.")
    add("- No LLM configured.")
    add("- Per-file errors preserved; single failures do not abort the run.")
    add("- Tool-specific raw output NOT committed (only aggregated / sampled records live in git).")
    add("- No cross-parser ranking or winner declaration.")
    add()

    path.write_text("\n".join(L), encoding="utf-8")


# ── Orchestration ───────────────────────────────────────────────────────


def _run(
    output_json: Path,
    output_md: Path,
    *,
    only: str | None,
    do_deterministic_check: bool,
    human_reviews: dict[str, dict[str, str]] | None,
) -> int:
    manifest = _load_manifest()
    assets = [a for a in manifest["assets"] if a["eligibility"] == "eligible"]
    if only:
        assets = [a for a in assets if a["asset_id"] == only or a["asset_id"].endswith(only)]
        if not assets:
            print(f"--only {only!r} matched no assets", file=sys.stderr)
            return 43

    try:
        import markitdown  # type: ignore[import-untyped]
        tool_version = getattr(markitdown, "__version__", "unknown")
    except ImportError:
        print("MarkItDown not installed — pip install 'markitdown[pdf]'", file=sys.stderr)
        return 44

    md = _get_markitdown()
    # Verify no LLM configured (offline invariant).
    assert getattr(md, "_llm_client", None) is None, (
        "MarkItDown adapter refuses to run with an LLM client configured — "
        "benchmark must be offline. Set LLM client to None."
    )

    results: list[RunResult] = []
    for a in sorted(assets, key=lambda a: a["asset_id"]):
        print(f"running {a['asset_id']}", file=sys.stderr)
        results.append(run_one(a,
                                do_deterministic_check=do_deterministic_check,
                                human_reviews=human_reviews))

    aggregate = _aggregate(results)

    payload = {
        "harness_version": "markitdown_adapter.py@2026-07-20",
        "adapter_target": "markitdown",
        "adapter_target_version": tool_version,
        "adapter_configuration": {
            "constructor_kwargs": "MarkItDown()",
            "llm_client": None,
            "llm_model": None,
            "ocr_enabled": False,
            "vision_enabled": False,
            "document_intelligence_enabled": False,
            "plugins_enabled": bool(getattr(md, "_plugins_enabled", False)),
            "instance_reuse": True,
        },
        "manifest_source": _MANIFEST.name,
        "manifest_commit_under_evaluation": manifest.get("commit_under_evaluation"),
        "run_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": _dependency_versions(),
        "evaluation_semantics_notes": {
            "aksharamd_readiness_score_used": False,
            "aksharamd_warning_codes_used": False,
            "near_empty_equivalent_definition": "non-whitespace chars < 50",
            "low_density_equivalent_definition": "output_size_inflation < 0.0005 AND non_whitespace_chars < 400",
            "no_cross_parser_ranking": True,
            "runtime_boundary_vs_aksharamd": "in-process library call vs subprocess; no exact speed ratio claimed",
        },
        "aggregate": aggregate,
        "per_asset": [asdict(r) for r in results],
    }
    _write_json(output_json, payload)
    print(f"wrote {output_json}", file=sys.stderr)

    _render_report(manifest, results, aggregate, output_md, tool_version=tool_version)
    print(f"wrote {output_md}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-json", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_MARKITDOWN_2026-07-20.json")
    ap.add_argument("--output-md", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_MARKITDOWN_2026-07-20.md")
    ap.add_argument("--only", type=str, default=None,
                    help="Only run assets whose id matches this suffix")
    ap.add_argument("--no-deterministic-check", action="store_true")
    ap.add_argument("--human-reviews", type=Path, default=None,
                    help="Path to JSON dict {asset_id: {usability, evidence}}")
    args = ap.parse_args()
    reviews: dict[str, dict[str, str]] | None = None
    if args.human_reviews is not None:
        with args.human_reviews.open("r", encoding="utf-8") as f:
            reviews = json.load(f)
    return _run(
        args.output_json,
        args.output_md,
        only=args.only,
        do_deterministic_check=not args.no_deterministic_check,
        human_reviews=reviews,
    )


if __name__ == "__main__":
    raise SystemExit(main())
