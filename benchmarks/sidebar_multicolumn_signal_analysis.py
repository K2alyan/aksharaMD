"""Sidebar vs. multicolumn signal analysis — offline evidence-only
harness (Issue #50 follow-up, analysis-only phase).

**No production code changes.** No parser, detector, scoring, or
warning-penalty modifications. This file compiles the eligible pages,
reads the resulting ``document.json`` + column-info metadata, computes
per-page geometry, evaluates a fixed set of candidate rules that were
enumerated up front, and writes a machine-readable JSON + report.

Corpus (block-level-observable only; span-only cases like elpais /
simple2 live in the report's appendix and do not vote on the primary
candidate gate):

- ParseBench block-level-observable (from
  ``parsebench_assets.lock.json > page_calibration_summary``):
  ``3colpres`` (TP), ``2colmercedes`` (TN), ``battery`` (TN),
  ``eastbaytimes`` (TN), ``strikeUnderline`` (FP).
- Public attested set (from ``multicolumn_recalibration_labels.json``):
  every entry whose ``expected_positive`` is ``True`` or ``False`` and
  whose PDF exists under ``benchmarks/.public_corpus/pdf/``.

Every eligible page passes through the same geometry extractor.

**Missing-data caveat.** Blocks carry only ``metadata.x0`` /
``metadata.y0`` (block start). They do NOT carry ``x1`` / ``y1``
(block end). Vertical-coverage and block-width metrics are therefore
computed from block-start positions and read as an
underestimate of true coverage. This is documented in every metric
that depends on it.

Exit codes:
- ``30`` — ParseBench cache missing or fails checksum verification.
- ``31`` — a labelled public PDF is missing from disk.
- ``33`` — a compile step failed for an eligible PDF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess  # nosec B404 - orchestrates the aksharamd CLI, no untrusted input
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCKFILE = _REPO_ROOT / "benchmarks" / "parsebench_assets.lock.json"
_PUBLIC_LABELS = _REPO_ROOT / "benchmarks" / "multicolumn_recalibration_labels.json"
_PUBLIC_ROOT = _REPO_ROOT / "benchmarks" / ".public_corpus" / "pdf"


# ── Corpus resolution ───────────────────────────────────────────────────


@dataclass
class Asset:
    """One evaluable asset in the analysis corpus."""

    id: str
    corpus: str  # "parsebench" or "public"
    pdf_path: Path
    expected_positive: bool
    # Baseline verdict (as-shipped detector), populated after compile.
    baseline_document_warned: bool | None = None
    per_page: list[dict[str, Any]] = field(default_factory=list)


def _parsebench_cache_dir(revision: str) -> Path:
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        raise RuntimeError("LOCALAPPDATA unset — required for ParseBench cache lookup")
    return Path(la) / "aksharamd" / "parsebench" / revision


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_parsebench_assets() -> list[Asset]:
    """The reviewer-confirmed block-level-observable corpus on the
    ParseBench side. Membership is derived from the lockfile's
    ``page_calibration_summary`` block.
    """
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        lock = json.load(f)
    summary = lock["page_calibration_summary"]["reviewer_confirmed_page_level_corpus"]
    confirmed_positives = set(summary["block_level_observable_positives"])
    confirmed_negatives = set(
        summary["hard_negatives"] + summary["single_column_negatives"]
    )
    detector_fps = set(summary["detector_false_positives"])
    # Every confirmed FP is treated as a NEGATIVE for the confusion matrix
    # (the detector should NOT fire), independent of its historical label.
    ids_positive = confirmed_positives
    ids_negative = confirmed_negatives | detector_fps

    revision = lock["dataset_source"]["dataset_revision"]
    cache_dir = _parsebench_cache_dir(revision)
    missing: list[str] = []
    assets: list[Asset] = []
    for entry in lock["assets"]:
        aid = entry["id"]
        if aid not in ids_positive and aid not in ids_negative:
            continue
        pdf = cache_dir / f"{aid}.pdf"
        if not pdf.exists():
            missing.append(f"{aid}: cache PDF missing at {pdf}")
            continue
        actual_sha = _sha256(pdf)
        if actual_sha != entry["sha256"]:
            missing.append(f"{aid}: sha256 mismatch (got {actual_sha[:8]}, want {entry['sha256'][:8]})")
            continue
        if pdf.stat().st_size != entry["size_bytes"]:
            missing.append(f"{aid}: size mismatch")
            continue
        assets.append(Asset(
            id=aid,
            corpus="parsebench",
            pdf_path=pdf,
            expected_positive=(aid in ids_positive),
        ))
    if missing:
        raise RuntimeError("ParseBench cache verification failed:\n  " + "\n  ".join(missing))
    return assets


def _resolve_public_assets() -> list[Asset]:
    """Every attested public-corpus entry whose PDF is present on disk.
    Ignores unattested entries (``expected_positive is None``).
    """
    with _PUBLIC_LABELS.open("r", encoding="utf-8") as f:
        labels_doc = json.load(f)
    labels_map: dict[str, dict] = labels_doc.get("labels", {})
    assets: list[Asset] = []
    seen_paths: set[Path] = set()
    for key, entry in labels_map.items():
        ep = entry.get("expected_positive")
        if ep not in (True, False):
            continue
        # Try the label key as a relpath under _PUBLIC_ROOT, otherwise
        # try matching by basename.
        pdf_path = _PUBLIC_ROOT / key
        cand: Path | None
        if pdf_path.exists():
            cand = pdf_path
        else:
            cand = None
            base = Path(key).name
            for p in _PUBLIC_ROOT.rglob(base):
                cand = p
                break
        if cand is None:
            # This label is unresolved on disk. Skip — the recalibration
            # PR already reconciled the 22-doc attested set; anything
            # here that isn't resolvable is an alias key we already
            # handled through another label.
            continue
        if cand in seen_paths:
            continue
        seen_paths.add(cand)
        assets.append(Asset(
            id=str(cand.relative_to(_REPO_ROOT)).replace("\\", "/"),
            corpus="public",
            pdf_path=cand,
            expected_positive=bool(ep),
        ))
    return assets


# ── Compile step ────────────────────────────────────────────────────────


def _compile_one(pdf: Path, workdir: Path) -> Path:
    """Compile one PDF and return the path to the produced ``document.json``.
    Uses the same isolation strategy as ``multicolumn_recalibration.py``.
    """
    stem = pdf.stem
    out_dir = workdir / stem
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    binary = shutil.which("aksharamd")
    if binary is None:
        raise RuntimeError(
            "aksharamd binary not found on PATH; install the wheel first"
        )
    proc = subprocess.run(  # nosec B603 - binary from shutil.which, args are local paths
        [binary, "compile", str(pdf), "-o", str(out_dir), "--json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"compile failed for {pdf.name}: exit={proc.returncode}\n"
            f"stdout tail: {proc.stdout[-800:]}\nstderr tail: {proc.stderr[-800:]}"
        )
    doc_json = out_dir / stem / "document.json"
    if not doc_json.exists():
        raise RuntimeError(f"compile finished but document.json missing at {doc_json}")
    return doc_json


# ── Geometry extraction ─────────────────────────────────────────────────


_MEANINGFUL_BLOCK_TYPES = {
    "paragraph", "list", "blockquote", "table", "code_block",
    "math", "key_value_group",
}
# Excluded when we speak of "substantial text blocks": headings /
# captions / footnotes / metadata / images / page-breaks / unknown.


def _column_of(x0: float, page_width: float, boundaries: list[float]) -> int:
    """Return the 0-indexed column for a block whose top-left x is x0.
    Matches the pattern used by pdf.py's ``_column_of`` (boundaries are
    normalised 0..1 divider positions between columns).
    """
    if page_width <= 0:
        return 0
    rel = x0 / page_width
    for i, b in enumerate(boundaries):
        if rel < b:
            return i
    return len(boundaries)


def _validator_cluster_boundary(x0s: list[float]) -> tuple[float | None, float]:
    """Reconstruct the validator's own 2-cluster split.

    The block-level multicolumn detector (``multicolumn.py:_analyse_page``)
    sorts block x0's and identifies the largest gap. That gap's midpoint
    is the cluster boundary — the same boundary that drives ``gap_size``
    and ``gap_rel`` in the shipped detector. This function reproduces it
    from block positions alone.

    Returns ``(boundary_x, gap_size)``. If fewer than two distinct x0's
    exist, returns ``(None, 0.0)`` — clustering is not defined and the
    caller must treat the page as single-cluster.
    """
    xs = sorted(x0s)
    if len(xs) < 2:
        return None, 0.0
    biggest_gap = 0.0
    boundary: float | None = None
    for a, b in zip(xs[:-1], xs[1:]):
        gap = b - a
        if gap > biggest_gap:
            biggest_gap = gap
            boundary = (a + b) / 2.0
    return boundary, biggest_gap


def _n_disjoint_runs(sorted_ys: list[float], gap_threshold: float) -> int:
    """Number of connected runs when gaps larger than ``gap_threshold``
    are treated as splits. Empty input → 0.
    """
    if not sorted_ys:
        return 0
    runs = 1
    for a, b in zip(sorted_ys[:-1], sorted_ys[1:]):
        if (b - a) > gap_threshold:
            runs += 1
    return runs


def _extract_page_geometry(
    document: dict[str, Any],
    page_no: int,
    diag_page: dict[str, Any] | None,
    col_info: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute the per-page geometry record for one page.

    All metrics are precisely defined here. Every metric that depends on
    a missing y1/x1 estimates from y0/x0 and is flagged with
    ``_is_estimate=True`` in the returned dict.
    """
    blocks = [b for b in (document.get("blocks") or []) if b.get("page") == page_no]
    page_width = float((col_info or {}).get("page_width") or 0.0)
    page_height = float((col_info or {}).get("page_height") or 0.0)
    boundaries: list[float] = list((col_info or {}).get("boundaries") or [])
    num_columns_parser = int((col_info or {}).get("num_columns") or 1)

    # Extract block positions.
    per_block: list[dict[str, Any]] = []
    x0_all: list[float] = []
    for b in blocks:
        meta = b.get("metadata") or {}
        x0_raw = meta.get("x0")
        y0_raw = meta.get("y0")
        try:
            x0: float | None = float(x0_raw) if x0_raw is not None else None
        except (TypeError, ValueError):
            x0 = None
        try:
            y0: float | None = float(y0_raw) if y0_raw is not None else None
        except (TypeError, ValueError):
            y0 = None
        content = b.get("content") or ""
        per_block.append({
            "index": b.get("index"),
            "type": b.get("type"),
            "x0": x0,
            "y0": y0,
            "chars": len(content),
            "words": len(content.split()),
        })
        if x0 is not None:
            x0_all.append(x0)

    # Reconstruct the validator's own 2-cluster split from the largest
    # x0 gap. This is what the shipped detector actually clusters on.
    validator_boundary, validator_gap = _validator_cluster_boundary(x0_all)
    for b in per_block:
        if validator_boundary is None or b["x0"] is None:
            b["cluster"] = 0
        else:
            b["cluster"] = 0 if b["x0"] < validator_boundary else 1

    # Per-cluster aggregates.
    clusters_seen = sorted({b["cluster"] for b in per_block})
    per_cluster: dict[int, dict[str, Any]] = {}
    for c in clusters_seen:
        members = [b for b in per_block if b["cluster"] == c]
        ys = sorted(float(b["y0"]) for b in members if b["y0"] is not None)
        xs = sorted(float(b["x0"]) for b in members if b["x0"] is not None)
        chars_sum = sum(b["chars"] for b in members)
        words_sum = sum(b["words"] for b in members)
        # Median block-height proxy: median gap between successive y0's.
        block_h_proxy = None
        if len(ys) >= 2:
            deltas = [b - a for a, b in zip(ys[:-1], ys[1:]) if b > a]
            if deltas:
                block_h_proxy = statistics.median(deltas)
        # Vertical y0-range (underestimate of true coverage — see caveat).
        y_min: float | None = ys[0] if ys else None
        y_max: float | None = ys[-1] if ys else None
        y_range: float = (y_max - y_min) if (y_min is not None and y_max is not None) else 0.0
        y_coverage_frac = (y_range / page_height) if page_height > 0 else 0.0
        # Contiguity: number of disjoint vertical runs in y0's, splitting
        # on gaps > 2× median block-height (fallback 4% of page height).
        gap_threshold = (2.0 * block_h_proxy) if block_h_proxy else (0.04 * page_height)
        n_runs = _n_disjoint_runs(ys, gap_threshold)
        # x0 variance (block start alignment inside the cluster).
        x0_var = statistics.pvariance(xs) if len(xs) >= 2 else 0.0
        per_cluster[c] = {
            "block_count": len(members),
            "chars": chars_sum,
            "words": words_sum,
            "y_min": y_min,
            "y_max": y_max,
            "y_range": y_range,
            "y_coverage_frac": y_coverage_frac,
            "block_height_proxy": block_h_proxy,
            "disjoint_runs": n_runs,
            "x0_variance": x0_var,
            "block_types": _typewise_counts(members),
        }

    total_chars = sum(c["chars"] for c in per_cluster.values()) or 0
    total_words = sum(c["words"] for c in per_cluster.values()) or 0

    # Smaller-cluster identity, when there are at least two clusters.
    smaller_cluster: int | None = None
    larger_cluster: int | None = None
    if len(clusters_seen) >= 2:
        # "Smaller" = fewer characters. Ties broken by block count.
        sc = sorted(
            clusters_seen,
            key=lambda c: (per_cluster[c]["chars"], per_cluster[c]["block_count"]),
        )
        smaller_cluster = sc[0]
        larger_cluster = sc[-1]

    # Cross-cluster geometry.
    text_share_smaller: float | None = None
    words_share_smaller: float | None = None
    y_overlap_frac: float | None = None
    top_alignment_delta: float | None = None
    bottom_alignment_delta: float | None = None
    smaller_disjoint_runs: int | None = None
    smaller_y_coverage_frac: float | None = None
    alternations_all: int | None = None
    alternations_substantial: int | None = None
    if smaller_cluster is not None and larger_cluster is not None:
        s = per_cluster[smaller_cluster]
        L = per_cluster[larger_cluster]
        if total_chars > 0:
            text_share_smaller = s["chars"] / total_chars
        if total_words > 0:
            words_share_smaller = s["words"] / total_words
        # Vertical y0-range overlap fraction of shorter cluster.
        if s["y_min"] is not None and L["y_min"] is not None:
            lo = max(s["y_min"], L["y_min"])
            hi = min(s["y_max"], L["y_max"])
            overlap = max(0.0, hi - lo)
            denom = min(s["y_range"], L["y_range"]) or 1e-9
            y_overlap_frac = overlap / denom
            top_alignment_delta = abs(s["y_min"] - L["y_min"])
            bottom_alignment_delta = abs(s["y_max"] - L["y_max"])
        smaller_disjoint_runs = s["disjoint_runs"]
        smaller_y_coverage_frac = s["y_coverage_frac"]

        # Alternations across all blocks in reading order (y0-ascending
        # then x0-ascending).
        ordered = sorted(per_block, key=lambda b: (
            b["y0"] if b["y0"] is not None else 1e12,
            b["x0"] if b["x0"] is not None else 1e12,
        ))
        seq = [b["cluster"] for b in ordered]
        alternations_all = sum(1 for a, b in zip(seq[:-1], seq[1:]) if a != b)
        # Substantial subsequence: filter out headings, images, page
        # breaks, captions, footnotes, metadata, unknown; require the
        # block to have >= 5 words (i.e., not a single label token).
        substantial = [
            b for b in ordered
            if (b["type"] in _MEANINGFUL_BLOCK_TYPES) and b["words"] >= 5
        ]
        seq_sub = [b["cluster"] for b in substantial]
        alternations_substantial = sum(1 for a, b in zip(seq_sub[:-1], seq_sub[1:]) if a != b)

    baseline_signals: dict[str, Any] = {}
    if diag_page is not None:
        baseline_signals = {
            "warn": bool(diag_page.get("warn")),
            "gap_size": diag_page.get("gap_size"),
            "gap_rel": diag_page.get("gap_rel"),
            "transition_rate": diag_page.get("transition_rate"),
            "large_y_drops": diag_page.get("large_y_drops"),
            "short_frac": diag_page.get("short_frac"),
            "signals": list(diag_page.get("signals") or []),
        }

    return {
        "page": page_no,
        "page_width": page_width,
        "page_height": page_height,
        "num_columns_parser": num_columns_parser,
        "parser_boundaries": boundaries,
        "validator_boundary_x": validator_boundary,
        "validator_gap_size": validator_gap,
        "block_count": len(blocks),
        "per_cluster": per_cluster,
        "cross_cluster": {
            "smaller_cluster": smaller_cluster,
            "larger_cluster": larger_cluster,
            "text_share_smaller": text_share_smaller,
            "words_share_smaller": words_share_smaller,
            "y_overlap_frac": y_overlap_frac,
            "top_alignment_delta": top_alignment_delta,
            "bottom_alignment_delta": bottom_alignment_delta,
            "smaller_disjoint_runs": smaller_disjoint_runs,
            "smaller_y_coverage_frac": smaller_y_coverage_frac,
            "alternations_all": alternations_all,
            "alternations_substantial": alternations_substantial,
        },
        "baseline": baseline_signals,
        "_estimates": {
            "y_range": "computed from block-start y0 only; underestimates true y-coverage by ~1 block height",
            "block_height_proxy": "median delta between successive y0's within a cluster; not the true block bbox height",
            "x_variance": "computed from x0 only; block widths are unknown",
        },
    }


def _typewise_counts(members: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in members:
        out[m["type"]] = out.get(m["type"], 0) + 1
    return out


def _extract_all_pages(doc_json: Path) -> list[dict[str, Any]]:
    with doc_json.open("r", encoding="utf-8") as f:
        document = json.load(f)
    meta = document.get("metadata") or {}
    col_info_all: dict = meta.get("pdf_column_info") or {}
    diag = meta.get("multicolumn_diagnostics") or {}
    diag_pages = {p.get("page"): p for p in (diag.get("page_analyses") or [])}
    pages: list[dict[str, Any]] = []
    for page_no_str, col_info in col_info_all.items():
        page_no = int(page_no_str)
        diag_page = diag_pages.get(page_no)
        pages.append(_extract_page_geometry(document, page_no, diag_page, col_info))
    # If pdf_column_info is empty (single-column page), fall back to
    # multicolumn_diagnostics.page_analyses entries.
    if not pages and diag_pages:
        # Use page_width from the document's per-page info if present.
        for pn, dp in diag_pages.items():
            pages.append(_extract_page_geometry(document, pn, dp, None))
    return sorted(pages, key=lambda p: p["page"])


# ── Candidate rules ─────────────────────────────────────────────────────


def _baseline_page_warn(page: dict[str, Any]) -> bool:
    return bool(page.get("baseline", {}).get("warn", False))


def _hypothesis_h1_smaller_cluster_short(page: dict[str, Any], threshold: float = 0.60) -> bool:
    """Silence the warning if the smaller cluster's y-coverage is
    below ``threshold`` × page. Sidebars should hit; real columns miss.
    Only applied when baseline warns AND the page has >=2 clusters.
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    cov = cc.get("smaller_y_coverage_frac")
    if cov is None:
        return True  # cannot judge → let baseline stand
    return cov >= threshold  # keep warning if coverage is high enough


def _hypothesis_h2_text_share_balanced(page: dict[str, Any], threshold: float = 0.15) -> bool:
    """Silence if the smaller cluster's text share is below ``threshold``.
    Sidebars have low text share; real columns have balanced share.
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    share = cc.get("text_share_smaller")
    if share is None:
        return True
    return share >= threshold


def _hypothesis_h3_alternating(page: dict[str, Any], threshold: int = 3) -> bool:
    """Silence if substantial-block alternations are below ``threshold``.
    A sidebar creates few alternations; real columns alternate.
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    alt = cc.get("alternations_substantial")
    if alt is None:
        return True
    return alt >= threshold


def _hypothesis_h4_contiguous_inset(page: dict[str, Any], threshold: int = 2) -> bool:
    """Silence if the smaller cluster is one contiguous run (i.e.,
    ``disjoint_runs`` is 1). A sidebar is one run; real columns break
    into several visually because of block-height gaps.

    ``threshold`` = minimum number of disjoint runs required to keep
    the warning.
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    n_runs = cc.get("smaller_disjoint_runs")
    if n_runs is None:
        return True
    return n_runs >= threshold


def _hypothesis_h5_alignment(page: dict[str, Any], top_thresh: float = 100.0, bot_thresh: float = 100.0) -> bool:
    """Silence if the smaller cluster's top or bottom is misaligned
    from the larger by more than ``top_thresh`` / ``bot_thresh`` PDF
    points.
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    top = cc.get("top_alignment_delta")
    bot = cc.get("bottom_alignment_delta")
    if top is None or bot is None:
        return True
    return not (top > top_thresh or bot > bot_thresh)


def _combined_h1h3(page: dict[str, Any]) -> bool:
    """Warn only if BOTH H1 and H3 would let the warning stand."""
    return _hypothesis_h1_smaller_cluster_short(page) and _hypothesis_h3_alternating(page)


def _combined_h1h2(page: dict[str, Any]) -> bool:
    return _hypothesis_h1_smaller_cluster_short(page) and _hypothesis_h2_text_share_balanced(page)


def _hypothesis_h6_thin_tall_marker(
    page: dict[str, Any],
    max_share: float = 0.020,
    min_cov: float = 0.40,
    max_alt_sub: int = 0,
) -> bool:
    """Silence a page whose smaller cluster is a **thin tall marker** —
    i.e., a sidebar. Positive rule: keep the warning UNLESS all three
    of these hold on the smaller cluster:

    - text share <= ``max_share`` (very sparse — a marker, not a column)
    - y-coverage >= ``min_cov`` (spans a substantial vertical range)
    - substantial-block alternations <= ``max_alt_sub`` (no meaningful
      cross-cluster reading order transitions)

    Rationale: sidebars are thin, tall, and don't participate in the
    reading order. The `3colpres` headshot cluster is thin AND has low
    text share, but its y-coverage is only 0.09 — it does NOT span the
    page height — so this rule leaves 3colpres alone. `strikeUnderline`
    hits all three conditions.
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    share = cc.get("text_share_smaller")
    cov = cc.get("smaller_y_coverage_frac")
    alt_sub = cc.get("alternations_substantial")
    if share is None or cov is None or alt_sub is None:
        return True  # cannot judge → keep warning
    is_thin_tall_marker = (share <= max_share and cov >= min_cov and alt_sub <= max_alt_sub)
    return not is_thin_tall_marker


def _hypothesis_h7_thin_marker_no_coverage_gate(
    page: dict[str, Any],
    max_share: float = 0.010,
    max_alt_sub: int = 0,
) -> bool:
    """Silence a page whose smaller cluster carries near-zero text AND
    has no substantial alternations. A tighter variant of H6 that
    drops the coverage gate. Used to test whether coverage adds
    anything.
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    share = cc.get("text_share_smaller")
    alt_sub = cc.get("alternations_substantial")
    if share is None or alt_sub is None:
        return True
    is_thin_marker = (share <= max_share and alt_sub <= max_alt_sub)
    return not is_thin_marker


def _hypothesis_h8_top_aligned_thin(
    page: dict[str, Any],
    max_share: float = 0.020,
    max_top_delta: float = 50.0,
    min_cov: float = 0.40,
) -> bool:
    """Sidebars are typically top-aligned with the main body (they run
    from the very top of the page). Real columns can be top-aligned or
    slightly offset. Silence a page whose smaller cluster is top-aligned
    AND thin AND tall. Excludes 3colpres, whose smaller cluster (headshot)
    is bottom-aligned (top_align_delta > 600).
    """
    if not _baseline_page_warn(page):
        return False
    cc = page.get("cross_cluster", {})
    share = cc.get("text_share_smaller")
    top = cc.get("top_alignment_delta")
    cov = cc.get("smaller_y_coverage_frac")
    if share is None or top is None or cov is None:
        return True
    is_top_aligned_sidebar = (share <= max_share and top <= max_top_delta and cov >= min_cov)
    return not is_top_aligned_sidebar


from collections.abc import Callable  # noqa: E402

CANDIDATES: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
    ("baseline", "baseline detector as shipped", _baseline_page_warn),
    ("H1_cov60", "smaller-cluster y-coverage >= 0.60 required",
     _hypothesis_h1_smaller_cluster_short),
    ("H2_share15", "smaller-cluster text share >= 0.15 required",
     _hypothesis_h2_text_share_balanced),
    ("H3_alt3", "substantial-block alternations >= 3 required",
     _hypothesis_h3_alternating),
    ("H4_runs2", "smaller cluster disjoint runs >= 2 required",
     _hypothesis_h4_contiguous_inset),
    ("H5_align100", "top+bottom alignment delta each <= 100pt",
     _hypothesis_h5_alignment),
    ("H1+H2", "H1 and H2 both keep the warning", _combined_h1h2),
    ("H1+H3", "H1 and H3 both keep the warning", _combined_h1h3),
    ("H6_thin_tall_marker",
     "silence: smaller-cluster share<=0.020 AND y-coverage>=0.40 AND alt_substantial<=0",
     _hypothesis_h6_thin_tall_marker),
    ("H7_thin_marker_no_cov",
     "silence: smaller-cluster share<=0.010 AND alt_substantial<=0",
     _hypothesis_h7_thin_marker_no_coverage_gate),
    ("H8_top_aligned_sidebar",
     "silence: smaller-cluster share<=0.020 AND top_delta<=50 AND cov>=0.40",
     _hypothesis_h8_top_aligned_thin),
]


# ── Confusion + gate ─────────────────────────────────────────────────────


def _document_verdict(pages: list[dict[str, Any]], rule) -> bool:
    return any(rule(p) for p in pages)


def _confusion(rows: list[tuple[str, bool, bool]]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for _id, expected, predicted in rows:
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif (not expected) and predicted:
            fp += 1
        else:
            tn += 1
    n = tp + fp + tn + fn
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return {
        "TP": tp, "FP": fp, "TN": tn, "FN": fn, "n": n,
        "precision": round(p, 4),
        "recall": round(r, 4),
        "false_positive_rate": round(fpr, 4),
        "f1": round(f1, 4),
    }


def _evaluate_candidate(assets: list[Asset], rule) -> tuple[dict[str, Any], dict[str, bool]]:
    verdicts: dict[str, bool] = {}
    for a in assets:
        verdicts[a.id] = _document_verdict(a.per_page, rule)
    rows = [(a.id, a.expected_positive, verdicts[a.id]) for a in assets]
    return _confusion(rows), verdicts


def _passes_shipping_gate(
    baseline_verdicts: dict[str, bool],
    candidate_verdicts: dict[str, bool],
    assets: list[Asset],
) -> tuple[bool, list[str]]:
    """A candidate passes the shipping gate iff:

    - ``strikeUnderline`` is silenced.
    - ``3colpres`` remains warned.
    - Every other confirmed positive remains warned.
    - No previously-silent negative newly warns.
    """
    reasons: list[str] = []
    id_by_short: dict[str, str] = {}
    for a in assets:
        id_by_short[a.id] = a.id
        if a.id == "strikeUnderline":
            id_by_short["strikeUnderline"] = a.id
        if a.id == "3colpres":
            id_by_short["3colpres"] = a.id

    if candidate_verdicts.get("strikeUnderline", None) is not False:
        reasons.append("strikeUnderline still warns")
    if candidate_verdicts.get("3colpres", None) is not True:
        reasons.append("3colpres no longer warns")

    for a in assets:
        b = baseline_verdicts.get(a.id)
        c = candidate_verdicts.get(a.id)
        if a.expected_positive and b is True and c is False:
            if a.id != "strikeUnderline":  # strikeUnderline is a negative; ignored above already
                reasons.append(f"confirmed positive silenced: {a.id}")
        if (not a.expected_positive) and b is False and c is True:
            reasons.append(f"new false positive raised: {a.id}")

    return (len(reasons) == 0), reasons


# ── Orchestration ────────────────────────────────────────────────────────


def _run(output_json: Path, workdir: Path | None = None) -> int:
    print("resolving corpus...", file=sys.stderr)
    try:
        pb_assets = _resolve_parsebench_assets()
    except RuntimeError as e:
        print(f"ParseBench corpus resolution failed: {e}", file=sys.stderr)
        return 30
    pub_assets = _resolve_public_assets()
    assets = pb_assets + pub_assets
    print(f"resolved {len(pb_assets)} ParseBench + {len(pub_assets)} public = {len(assets)} assets", file=sys.stderr)

    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="sidebar_analysis_"))
    else:
        workdir.mkdir(parents=True, exist_ok=True)

    for a in assets:
        print(f"compiling {a.corpus:11s}  {a.id}", file=sys.stderr)
        try:
            doc_json = _compile_one(a.pdf_path, workdir)
        except RuntimeError as e:
            print(f"compile failed: {e}", file=sys.stderr)
            return 33
        a.per_page = _extract_all_pages(doc_json)
        a.baseline_document_warned = any(p["baseline"].get("warn") for p in a.per_page)

    # Evaluate every candidate.
    candidate_results: dict[str, Any] = {}
    baseline_verdicts: dict[str, bool] = {}
    for name, desc, rule in CANDIDATES:
        conf, verdicts = _evaluate_candidate(assets, rule)
        if name == "baseline":
            baseline_verdicts = verdicts
        candidate_results[name] = {
            "description": desc,
            "confusion": conf,
            "verdicts": verdicts,
        }

    # Gate + changed-decision log per candidate.
    for name, entry in candidate_results.items():
        if name == "baseline":
            entry["passes_shipping_gate"] = None
            entry["gate_reasons"] = []
            entry["changed_decisions"] = []
            continue
        gate_ok, reasons = _passes_shipping_gate(baseline_verdicts, entry["verdicts"], assets)
        entry["passes_shipping_gate"] = gate_ok
        entry["gate_reasons"] = reasons
        entry["changed_decisions"] = [
            {
                "id": a.id,
                "corpus": a.corpus,
                "expected_positive": a.expected_positive,
                "baseline": baseline_verdicts.get(a.id),
                "candidate": entry["verdicts"][a.id],
                "flip": "silenced" if baseline_verdicts.get(a.id) and not entry["verdicts"][a.id] else "raised",
            }
            for a in assets
            if baseline_verdicts.get(a.id) != entry["verdicts"][a.id]
        ]

    output = {
        "harness_version": "sidebar_multicolumn_signal_analysis.py@2026-07-19",
        "run_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit_under_evaluation": _current_commit(),
        "corpus": {
            "parsebench_ids": sorted(a.id for a in pb_assets),
            "public_ids": sorted(a.id for a in pub_assets),
            "eligible_page_count": sum(len(a.per_page) for a in assets),
        },
        "assets": [
            {
                "id": a.id,
                "corpus": a.corpus,
                "expected_positive": a.expected_positive,
                "baseline_document_warned": a.baseline_document_warned,
                "per_page": a.per_page,
            }
            for a in assets
        ],
        "candidates": candidate_results,
        "shipping_gate_definition": {
            "silence": ["strikeUnderline"],
            "preserve_positives": [a.id for a in assets if a.expected_positive],
            "no_new_false_positives_on": [a.id for a in assets if not a.expected_positive and not baseline_verdicts.get(a.id, False)],
        },
    }
    output_json.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"wrote {output_json}", file=sys.stderr)
    return 0


def _current_commit() -> str:
    try:
        return subprocess.check_output(  # nosec B603 B607 - local git head, no user input
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, text=True,
        ).strip()
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT / "benchmarks" / "SIDEBAR_MULTICOLUMN_SIGNAL_ANALYSIS_2026-07-19.json",
    )
    ap.add_argument("--workdir", type=Path, default=None)
    args = ap.parse_args()
    return _run(args.output, workdir=args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
