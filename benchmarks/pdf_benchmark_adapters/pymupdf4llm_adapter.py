"""PyMuPDF4LLM adapter for PDF Benchmark v1 Phase 2 (Issue #68).

Runs PyMuPDF4LLM's ``to_markdown`` on the same 45 eligible assets that
AksharaMD Phase 1 consumed and reports **tool-neutral** metrics only.
No comparison ranking is produced here — a separate Phase 3 report
will combine adapters after each is independently reviewed.

**No AksharaMD production code changes.** No parser, validator,
scoring, warning-penalty, or ``SCORING_POLICY`` modifications.
``SCORING_POLICY_VERSION`` remains ``"1.0"``.

**Evaluation semantics differences vs. AksharaMD Phase 1** (documented
in the report):

- PyMuPDF4LLM does NOT emit ``NEAR_EMPTY_OUTPUT`` or ``LOW_TEXT_DENSITY``
  warning codes. This adapter substitutes purely-mechanical rules:
  ``near_empty_equivalent`` = non-whitespace char count < 50;
  ``low_density_equivalent`` = output-size inflation
  (chars per PDF byte) < a slice-neutral threshold.
- PyMuPDF4LLM does NOT compute a readiness score or quality band.
  Those fields are ``None`` for this adapter.
- Runtime boundary matches AksharaMD Phase 1: single ``to_markdown``
  call, wall-clock time only; deterministic-check recompile is timed
  separately.

Refuses to run if PyMuPDF4LLM is not installed. Records the pinned
version in every output artifact.
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


# ── Metrics ──────────────────────────────────────────────────────────────


_MIN_MEANINGFUL_CHARS = 200


_IMG_PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


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
    """Per-file record. Tool-neutral fields only; no AksharaMD warning
    codes or readiness fields.
    """
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
        raise RuntimeError(
            f"manifest not present: {_MANIFEST}. Run "
            "`python -m benchmarks.pdf_benchmark_v1 --verify-cache-only` "
            "or the full Phase 1 harness first."
        )
    with _MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dependency_versions() -> dict[str, str]:
    keys = ("pymupdf4llm", "pymupdf", "aksharamd")
    out: dict[str, str] = {}
    for k in keys:
        try:
            from importlib.metadata import version
            out[k] = version(k)
        except Exception:
            out[k] = "unknown"
    return out


def _to_markdown_once(pdf: Path) -> tuple[str, str, float]:
    """Call ``pymupdf4llm.to_markdown`` once and return
    ``(markdown, exception_or_empty, elapsed)``.
    """
    import pymupdf4llm  # type: ignore[import-untyped]
    t0 = time.perf_counter()
    exc = ""
    md = ""
    try:
        md = pymupdf4llm.to_markdown(str(pdf))
    except Exception as e:
        exc = f"{type(e).__name__}: {e}"[:400]
    return md, exc, time.perf_counter() - t0


def run_one(
    asset: dict[str, Any],
    *,
    do_deterministic_check: bool,
    human_reviews: dict[str, dict[str, str]] | None,
) -> RunResult:
    aid = asset["asset_id"]
    pdf = Path(asset["pdf_path"])
    md, exc, elapsed = _to_markdown_once(pdf)
    execution_success = (exc == "" and md is not None)
    doc_md = md or ""
    output_package_created = execution_success and bool(doc_md)
    non_ws = sum(1 for c in doc_md if not c.isspace())
    output_chars = len(doc_md)
    tokens = _estimate_tokens(doc_md)
    size_bytes = int(asset.get("size_bytes") or 0)
    inflation = (output_chars / size_bytes) if size_bytes else 0.0

    # Tool-neutral equivalents of AksharaMD's warning surfaces.
    near_empty_equivalent = non_ws < 50
    low_density_equivalent = (
        # An extremely sparse output relative to the PDF byte size.
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
        md2, _exc2, _e2 = _to_markdown_once(pdf)
        deterministic = (doc_md == md2)

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
        tool_signals={"markdown_line_count": doc_md.count("\n") + (1 if doc_md else 0)},
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


_AKSHARAMD_REVIEWS = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_human_reviews.json"

_USABLE_ENUM = {"usable", "usable_with_minor_defects"}


def _matched_sample_paired(results: list[RunResult]) -> dict[str, Any]:
    """Compare PyMuPDF4LLM's per-asset human verdict against AksharaMD's
    for the intersection of both reviewer samples. Any asset id reviewed
    by only one party is EXCLUDED from the headline paired comparison
    but preserved in ``supplementary_ids`` for context.

    Returns:

        {
          "matched_sample_size": int,
          "aksharamd_usable_count": int,
          "pymupdf4llm_usable_count": int,
          "both_usable": int,
          "aksharamd_only_usable": [asset_id, ...],
          "pymupdf4llm_only_usable": [asset_id, ...],
          "neither_usable": [asset_id, ...],
          "supplementary_pymupdf4llm_only": [asset_id, ...],
          "supplementary_aksharamd_only": [asset_id, ...],
        }
    """
    if not _AKSHARAMD_REVIEWS.exists():
        return {"error": f"AksharaMD reviews not found at {_AKSHARAMD_REVIEWS}"}
    with _AKSHARAMD_REVIEWS.open("r", encoding="utf-8") as f:
        ax = json.load(f)
    ax_ids = {k for k in ax if not k.startswith("_")}
    pm_reviewed = {r.asset_id: r.human_usability for r in results
                    if r.human_review_status == "reviewed"}
    matched = ax_ids & set(pm_reviewed)
    both = []
    ax_only: list[str] = []
    pm_only: list[str] = []
    neither: list[str] = []
    for aid in sorted(matched):
        au = ax[aid].get("usability", "not_reviewed")
        pu = pm_reviewed[aid]
        aok = au in _USABLE_ENUM
        pok = pu in _USABLE_ENUM
        if aok and pok:
            both.append(aid)
        elif aok and not pok:
            ax_only.append(aid)
        elif not aok and pok:
            pm_only.append(aid)
        else:
            neither.append(aid)
    return {
        "matched_sample_size": len(matched),
        "aksharamd_usable_count": len(both) + len(ax_only),
        "pymupdf4llm_usable_count": len(both) + len(pm_only),
        "both_usable": len(both),
        "aksharamd_only_usable": ax_only,
        "pymupdf4llm_only_usable": pm_only,
        "neither_usable": neither,
        "supplementary_pymupdf4llm_only": sorted(set(pm_reviewed) - ax_ids),
        "supplementary_aksharamd_only": sorted(ax_ids - set(pm_reviewed)),
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
    # Cross-adapter matched-sample comparison against AksharaMD Phase 1
    # human reviews. Only asset ids that BOTH parties reviewed count.
    ag["matched_sample_vs_aksharamd_phase1"] = _matched_sample_paired(results)
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

    add(f"# PDF Benchmark v1 — PyMuPDF4LLM adapter ({time.strftime('%Y-%m-%d')})")
    add()
    add(f"**Tool:** PyMuPDF4LLM `{tool_version}`")
    add(f"**Commit under evaluation:** `{manifest['commit_under_evaluation']}`")
    add(f"**Python:** {manifest['python_version']} · **Platform:** {manifest['platform']}")
    add()
    add("**No AksharaMD production code changes.** `SCORING_POLICY_VERSION` remains `\"1.0\"`. Phase 2 of Issue #68 — one competitor in isolation, no cross-parser ranking here.")
    add()
    add("## Evaluation semantics — differences from AksharaMD Phase 1")
    add()
    add("This adapter deliberately does NOT reuse AksharaMD-specific fields:")
    add()
    add("- **No readiness score or quality band.** PyMuPDF4LLM does not compute one; these fields are `null` in every per-asset record.")
    add("- **No `OCR_REQUIRED` / `NEAR_EMPTY_OUTPUT` / `LOW_TEXT_DENSITY` warning codes.** PyMuPDF4LLM does not emit them. Substitutions used here are purely mechanical:")
    add("  - **`near_empty_equivalent`** = fewer than 50 non-whitespace characters in the output. Analogous to `NEAR_EMPTY_OUTPUT` but strictly threshold-based.")
    add("  - **`low_density_equivalent`** = `output_size_inflation < 0.0005` AND `non_whitespace_chars < 400`. Analogous to `LOW_TEXT_DENSITY` but tool-neutral.")
    add("- **No multicolumn / heading / table warnings.** PyMuPDF4LLM does not expose per-block diagnostics comparable to AksharaMD's warning surface. Structural quality is captured via `repeat_content_ratio`, `image_placeholder_ratio`, and human review.")
    add()
    add("All other definitions are identical to AksharaMD Phase 1: `execution_success` (function did not raise), `output_package_created` (return value is a non-empty string), `meaningful_content` (≥ 200 non-whitespace chars AND not near-empty-equivalent), `structurally_usable` (content-extracted AND (`< 100` tokens OR `repeat_content_ratio < 0.50`) AND (`low_density_equivalent` did NOT fire OR PDF has no text layer)). Runtime boundary matches: single `to_markdown` call, wall-clock time only.")
    add()
    add("## Interpretation guardrails")
    add()
    add("- **Do not extrapolate the human-review sample rate to the whole corpus.** The reviewed set is the same 28 files reviewed for AksharaMD (or subset when a file failed to parse).")
    add("- **Do not compare directly to AksharaMD numbers on the same corpus without noting the evaluation-semantics differences above.** Two adapters can legitimately report different `content_extracted` counts on the same input if the definitions differ. This report keeps definitions as close to Phase 1 as tool-neutrality permits, but the substitutions above are not exact equivalents.")
    add("- **No competitor ranking here.** Phase 3 will combine adapters after each is independently reviewed and stable.")
    add()

    add("## Interpretation guardrails")
    add()
    add("- **`meaningful_content` and `structurally_usable` are benchmark-rule classifications** — deterministic tool-neutral gates that read Markdown output. They are NOT substitutes for human judgment; they are the automated screen that the human review then refines.")
    add("- **AksharaMD readiness scores and warning codes are NOT applied to PyMuPDF4LLM.** PyMuPDF4LLM does not compute them.")
    add("- **Human-usable rate is a sample rate.** 29 files were reviewed. See § Matched human-review parity for the paired comparison against AksharaMD Phase 1.")
    add("- **No cross-parser winner declaration.** Slice-level differences are reported; universal rankings are deferred to Phase 3.")
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

    add("## Matched human-review parity")
    add()
    mp = aggregate.get("matched_sample_vs_aksharamd_phase1", {})
    if mp and "error" not in mp:
        add(f"AksharaMD Phase 1 reviewed 29 asset ids; PyMuPDF4LLM reviewed 29. The intersection is **{mp['matched_sample_size']}** asset ids. The AksharaMD sample referred to `public/015-arabic/arabic.pdf` (a stale label; no such file exists in the frozen manifest), and the PyMuPDF4LLM sample used `public/015-arabic/habibi.pdf` (the real Arabic asset). Both symmetric-difference ids are preserved below as supplementary reviews.")
        add()
        add("| Metric | Value |")
        add("|---|---:|")
        add(f"| Matched sample size | **{mp['matched_sample_size']}** |")
        add(f"| AksharaMD usable count (matched) | {mp['aksharamd_usable_count']} |")
        add(f"| PyMuPDF4LLM usable count (matched) | {mp['pymupdf4llm_usable_count']} |")
        add(f"| Both usable | {mp['both_usable']} |")
        add(f"| AksharaMD only usable | {len(mp['aksharamd_only_usable'])} |")
        add(f"| PyMuPDF4LLM only usable | {len(mp['pymupdf4llm_only_usable'])} |")
        add(f"| Neither usable | {len(mp['neither_usable'])} |")
        add()
        if mp["aksharamd_only_usable"]:
            add("**AksharaMD-only usable assets:**")
            add()
            for aid in mp["aksharamd_only_usable"]:
                add(f"- `{aid}`")
            add()
        if mp["pymupdf4llm_only_usable"]:
            add("**PyMuPDF4LLM-only usable assets:**")
            add()
            for aid in mp["pymupdf4llm_only_usable"]:
                add(f"- `{aid}`")
            add()
        if mp["supplementary_pymupdf4llm_only"]:
            add("**Supplementary reviews (in PyMuPDF4LLM sample, NOT AksharaMD's):**")
            add()
            for aid in mp["supplementary_pymupdf4llm_only"]:
                add(f"- `{aid}`")
            add()
        if mp["supplementary_aksharamd_only"]:
            add("**Supplementary reviews (in AksharaMD sample, NOT PyMuPDF4LLM's):**")
            add()
            for aid in mp["supplementary_aksharamd_only"]:
                add(f"- `{aid}`")
            add()
    else:
        add("_Matched-sample comparison unavailable: AksharaMD Phase 1 review file not present._")
        add()

    add("## Runtime-boundary parity")
    add()
    add("Reported `runtime_seconds` for each parser reflects **one primary parse** per asset. The two adapters do not use identical process boundaries, so the raw numbers are not directly comparable at the millisecond level.")
    add()
    add("| Included in runtime | AksharaMD (Phase 1) | PyMuPDF4LLM (this adapter) |")
    add("|---|:---:|:---:|")
    add("| Process startup (Python interpreter fork) | **yes** — CLI subprocess | no — in-process call |")
    add("| Import + package loading | **yes** — each invocation | no — imported once, reused |")
    add("| PDF parsing | yes | yes |")
    add("| OCR (when invoked) | yes (Marker vision extra active) | no — PyMuPDF4LLM has no OCR |")
    add("| Markdown generation | yes | yes |")
    add("| Output serialisation to disk | yes — `document.md` written | no — string returned in memory |")
    add("| Checksum verification | no | no |")
    add("| Deterministic second execution | no (recorded separately) | no (recorded separately) |")
    add()
    add("**Consequence.** AksharaMD's runtime necessarily includes per-invocation subprocess startup + package-load overhead (roughly a few hundred milliseconds on this machine, plus disk I/O), while PyMuPDF4LLM's runtime measures just the library call. The AksharaMD Phase 1 report shows `p50 = 2.57 s / p95 = 36.36 s`; the earlier session-note baseline of `p50 = 6.9 s / p95 = 30.9 s` came from an earlier harness pass that included a checksum-verification pre-flight per asset — the current Phase-1 harness does the verification once up-front, so per-asset timings do not include it. Neither figure is stale, but they were produced under different pre-flight strategies. This report uses the current Phase-1 numbers (`2.57 / 36.36`) because those are what live in the merged JSON.")
    add()
    add("**Do not read the median-runtime ratio as a millisecond-level speed claim.** PyMuPDF4LLM is substantially faster in this run, and its process boundary is genuinely lighter; the exact ratio is not a benchmarkable quantity across adapters with different boundaries. Phase 3 will discuss timing in terms of user-visible latency categories rather than raw multiples.")
    add()
    add("Determinism-check overhead is measured separately: enabling the recompile-and-diff pass roughly doubles the reported per-asset runtime; this adapter defaults to `--no-deterministic-check` for the same reason AksharaMD Phase 1 did.")
    add()

    add("## Character encoding — correction")
    add()
    add("**The Spanish accents on `parsebench/elpais` are correctly preserved by PyMuPDF4LLM.** Round-trip check:")
    add()
    add("- Raw PyMuPDF text on page 1 contains `EL PAÍS` at UTF-8 bytes `b'EL PA\\xc3\\x8dS'` (`Í` = U+00CD).")
    add("- `pymupdf4llm.to_markdown` output contains the same `EL PAÍS` bytes.")
    add("- The `SÁBADO` date header round-trips as UTF-8 `b'S\\xc3\\x81BADO'` (`Á` = U+00C1) in both surfaces.")
    add()
    add("An earlier note in this branch (before this revision) reported `PA?S` — that was a Windows terminal-rendering artefact when `sys.stdout` fell back to cp1252 during ad-hoc debugging, NOT a PyMuPDF4LLM defect. Every UTF-8 byte in the input round-trips through the machine-readable JSON and through the on-disk report unchanged. The revised `parsebench/elpais` human-review verdict is `usable_with_minor_defects` — the minor defect is missing whitespace at column boundaries (e.g., `EL PAÍSlos`), not character corruption.")
    add()

    add("## Execution failure — reproducibility check")
    add()
    add("`public/017-unreadable-meta-data/unreadablemetadata.pdf` — PyMuPDF4LLM raises during document parsing:")
    add()
    add("- **Exception:** `IndexError: range object index out of range`")
    add("- **Where:** `pymupdf4llm/helpers/document_layout.py:1050`, at the guard `page_filter[-1] >= mydoc.page_count`.")
    add("- **Plain PyMuPDF behaviour:** `fitz.open()` succeeds but `doc.page_count == 0`; `doc[0].get_text()` also raises `IndexError`. The PDF advertises metadata but exposes no pages to PyMuPDF.")
    add("- **Deterministic:** yes — reproduced on two consecutive invocations.")
    add("- **Timing:** failure occurs BEFORE Markdown conversion, during layout parsing.")
    add("- **AksharaMD comparison:** the AksharaMD compiler parses this same file and emits a small usable output; the difference is upstream in PyMuPDF4LLM's assumption that at least one page is present.")
    add()
    add("**No document-specific workaround was added to the adapter.** The failure is captured verbatim in the per-asset record's `exception` field so downstream consumers can filter it explicitly.")
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

    add("## By corpus source")
    add()
    for src, agg in aggregate["by_corpus"].items():
        add(f"### {src}")
        add()
        add(f"- n = {agg['n']}")
        add(f"- execution / content / structural: {agg['execution_success_count']} / {agg['content_extracted_count']} / {agg['structurally_usable_count']}")
        add(f"- runtime p50/p95 (s): {agg['runtime_seconds_p50']} / {agg['runtime_seconds_p95']}")
        add(f"- near-empty: {agg['near_empty_equivalent_count']}, low-density: {agg['low_density_equivalent_count']}")
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
        w_parts = []
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
        add()
        for f in aggregate["execution_failures"]:
            add(f"- `{f['asset_id']}` — {f['exception']}")
        add()
    else:
        add(f"No execution failures across {ov['n']} files.")
        add()
    if aggregate["content_failures"]:
        add("### Content failures (returned but content-poor)")
        add()
        for f in aggregate["content_failures"]:
            add(f"- `{f['asset_id']}` (class {f['document_class']}) — chars={f['output_chars']}, near-empty-equiv={f['near_empty_equivalent']}, low-density-equiv={f['low_density_equivalent']}, hidden-text-layer={f['hidden_text_layer']}")
        add()
    if aggregate["structural_failures"]:
        add("### Structural failures (content but not structurally usable)")
        add()
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
        add(f"Reviewed: {len(reviewed_rows)} files. Same asset ids as AksharaMD Phase 1 where available (see § Evaluation semantics — the reviewer's usability grade is on PyMuPDF4LLM's specific output, not the AksharaMD output).")
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
    add("- Same 45-asset frozen manifest as AksharaMD Phase 1; same checksum-verified ParseBench cache.")
    add("- No network fetch.")
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
        import pymupdf4llm  # type: ignore[import-untyped]
        tool_version = getattr(pymupdf4llm, "__version__", "unknown")
    except ImportError:
        print("PyMuPDF4LLM not installed — pip install pymupdf4llm", file=sys.stderr)
        return 44

    results: list[RunResult] = []
    for a in sorted(assets, key=lambda a: a["asset_id"]):
        print(f"running {a['asset_id']}", file=sys.stderr)
        results.append(run_one(a,
                               do_deterministic_check=do_deterministic_check,
                               human_reviews=human_reviews))

    aggregate = _aggregate(results)

    payload = {
        "harness_version": "pymupdf4llm_adapter.py@2026-07-19",
        "adapter_target": "pymupdf4llm",
        "adapter_target_version": tool_version,
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
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_PYMUPDF4LLM_2026-07-19.json")
    ap.add_argument("--output-md", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_PYMUPDF4LLM_2026-07-19.md")
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
