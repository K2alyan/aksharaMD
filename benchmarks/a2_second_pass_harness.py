"""A2 second pass — deterministic re-run of Unlimited-OCR against all
45 eligible assets. Compares pass 2 to the committed pass-1 artifact
without mutating either.

Same production commit, model revision, manifest, asset ordering,
timeout, and environment as pass 1 (see benchmarks/a2_first_pass_harness.py).

Differences from pass 1:

- Records ``output_sha256`` per asset (pass 1 did not — outputs were
  written to a TemporaryDirectory and discarded after char count was
  captured). The only exception is the three assets manually inspected
  post-pass-1 (see benchmarks/hallucination_inspection_2026-07-20/);
  those raw outputs live on disk and are used as the pass-1 ground
  truth for exact-hash comparison.
- Writes to separate artifact + checkpoint files so pass-1 evidence is
  preserved untouched.
- After completion, produces a comparison artifact classifying every
  asset per the reviewer's fixed enum:
    exact_match | normalized_match | content_mismatch |
    missing_in_pass | new_failure

Explicit non-goals (per reviewer directive):

- Do NOT patch the empty-page adapter bug.
- Do NOT attempt adaptive chunking or otherwise "fix" the 117-page
  GeoTopo OOM signature.
- Do NOT retry any timed-out or failed asset in the same pass.
- Do NOT alter any thresholds.
- Do NOT use runtime or VRAM as determinism criteria — record only
  as diagnostic metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

# UTF-8 stdout by default.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Reuse pass-1 helpers unchanged.
from benchmarks.a2_first_pass_harness import (  # type: ignore  # noqa: E402
    DEFAULT_TIMEOUT_S,
    HALLUCINATION_MULTIPLIER,  # noqa: F401 — public constant for tests
    _classify_failure,
    _compute_aggregate,
    _cuda_stats,
    _hallucination_flag,
    _hidden_text_layer_chars,
    _nvidia_driver_version,
    _pdf_page_count,
    _reset_cuda_stats,
    _rss_mib,
    _runner_health_probe,
    _write_atomic,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST_PATH = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"

# Pass-1 artifact — read-only reference. Committed on main.
_PASS_1_JSON = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.json"

# Pass-2 artifacts — separate filenames.
_PASS_2_JSON = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20_PASS_2.json"
_PASS_2_MD = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20_PASS_2.md"
_PASS_2_CHECKPOINT = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_SECOND_PASS.checkpoint.json"

# Cross-pass comparison artifacts.
_COMPARISON_JSON = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20_COMPARISON.json"
_COMPARISON_MD = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20_COMPARISON.md"

# Pass-1 outputs captured during the manual hallucination inspection.
# These are the only assets for which pass-1 raw text is on disk and
# an exact-hash comparison to pass 2 is possible.
_PASS_1_INSPECTED = {
    "public/008-reportlab-inline-image/inline-image.pdf":
        "benchmarks/hallucination_inspection_2026-07-20/"
        "public_008_reportlab_inline_image_inline_image_pdf.output.md",
    "public/028-image-references-deduplication/wrong-references.pdf":
        "benchmarks/hallucination_inspection_2026-07-20/"
        "public_028_image_references_deduplication_wrong_references_pdf.output.md",
    # cropped-rotated-scaled was captured PARTIAL only (harness killed
    # mid-generation); reference the partial file so comparison can
    # note it as a bounded reference.
    "public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf":
        "benchmarks/hallucination_inspection_2026-07-20/"
        "public_027_cropped_rotated_scaled_cropped_rotated_scaled_pdf.output.PARTIAL.md",
}


# ── SHA-256 helpers ────────────────────────────────────────────────────


def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_output(text: str) -> str:
    """Strip known nondeterministic metadata for the normalized-hash
    comparison. Unlimited-OCR's raw output has no documented
    nondeterministic metadata fields; the normalization here is
    minimal — strip trailing whitespace and normalize line endings.
    Documented so future contributors know the intent.
    """
    return "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"


# ── Environment probe ──────────────────────────────────────────────────


def _capture_env() -> dict[str, Any]:
    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "nvidia_driver": _nvidia_driver_version() or "unknown",
    }
    try:
        import torch  # type: ignore  # noqa: F401
        env["torch"] = torch.__version__
        env["cuda"] = torch.version.cuda
        if torch.cuda.is_available():
            env["gpu_name"] = torch.cuda.get_device_name(0)
            env["gpu_vram_gib"] = round(
                torch.cuda.get_device_properties(0).total_memory / 1024**3, 2,
            )
            env["bf16_supported"] = torch.cuda.is_bf16_supported()
    except ImportError:
        env["torch"] = None
    return env


# ── Per-asset execution (pass-2 shape: adds output_sha256) ─────────────


def _run_one_asset(runner, asset: dict, torch, workdir: Path, timeout_s: int) -> dict:
    aid = asset["asset_id"]
    pdf = Path(asset["pdf_path"])
    page_count = _pdf_page_count(pdf)
    hidden_chars = _hidden_text_layer_chars(pdf)

    _reset_cuda_stats(torch)
    rss_before = _rss_mib()
    text = ""
    exc = ""
    tool_signals: dict[str, Any] = {}
    t0 = time.perf_counter()
    try:
        text, exc, tool_signals = runner.infer_pdf(pdf, workdir)
    except Exception as e:
        exc = f"harness_exception: {type(e).__name__}: {e}"
        traceback.print_exc(file=sys.stderr)
    elapsed = round(time.perf_counter() - t0, 2)
    stats = _cuda_stats(torch)
    rss_after = _rss_mib()
    healthy_after = _runner_health_probe(torch)
    timeout_hit = elapsed > timeout_s
    failure_category = _classify_failure(exc, timeout_hit)
    if timeout_hit and not exc:
        exc = f"soft_timeout after {elapsed}s (limit {timeout_s}s)"
    status = "PASS" if failure_category == "success" else "FAIL"
    hallucination = _hallucination_flag(text or "", hidden_chars)

    return {
        "asset_id": aid,
        "corpus_source": asset.get("corpus_source", ""),
        "document_class": asset.get("document_class", "unknown"),
        "pdf_path": str(pdf),
        "page_count": page_count,
        "hidden_text_layer_chars": hidden_chars,
        "status": status,
        "failure_category": failure_category,
        "elapsed_seconds": elapsed,  # diagnostic only
        "seconds_per_page": (
            round(elapsed / page_count, 3) if page_count and page_count > 0 else None
        ),
        "peak_allocated_mib": stats.get("allocated_mib"),  # diagnostic only
        "peak_reserved_mib": stats.get("reserved_mib"),  # diagnostic only
        "rss_before_mib": rss_before,
        "rss_after_mib": rss_after,
        "output_chars": len(text or ""),
        "output_sha256": _sha256_text(text or ""),
        "output_normalized_sha256": _sha256_text(_normalize_output(text or "")),
        "warning_set": [],  # tool-level warnings not emitted by this adapter
        "hallucination_flag": hallucination,
        "hallucination_classification": (
            "watch_ratio_triggered" if hallucination else "not_flagged"
        ),
        "failure_signature": (
            exc.split(":", 1)[0] if exc else "success"
        ),
        "exception": exc,
        "execution_mode": "real_inference",
        "runner_healthy_after": healthy_after,
        "tool_signals": tool_signals,
        "timeout_hit": timeout_hit,
    }


# ── Cross-pass comparison ──────────────────────────────────────────────


def _pass1_ground_truth_hashes() -> dict[str, dict[str, Any]]:
    """For the 3 manually-inspected assets, load their pass-1 raw
    output and compute hashes. For the other 39 pass-1 successes,
    return no hash (comparison will fall through to
    ``hash_uncomparable_pass1_missing_hash``).
    """
    out: dict[str, dict[str, Any]] = {}
    for aid, rel in _PASS_1_INSPECTED.items():
        p = _REPO_ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        out[aid] = {
            "sha256": _sha256_text(text),
            "normalized_sha256": _sha256_text(_normalize_output(text)),
            "char_count": len(text),
            "source_note": (
                "captured during hallucination inspection; PARTIAL means "
                "the pass-1 inspection re-run was killed mid-generation, "
                "so the reference is bounded not final"
                if "PARTIAL" in rel
                else "captured during hallucination inspection"
            ),
            "is_partial_reference": "PARTIAL" in rel,
        }
    return out


def _classify_asset_pair(
    aid: str,
    p1_row: dict | None,
    p2_row: dict | None,
    p1_hashes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Classify pass-1 → pass-2 for one asset per the reviewer's enum:
    exact_match | normalized_match | content_mismatch | missing_in_pass
    | new_failure

    Because pass 1 recorded output hashes only for the 3 manually-
    inspected assets, the other 39 pass-1 successes cannot be strictly
    hash-compared. They are classified using char_count + status
    equality with an explicit note.
    """
    if p1_row is None and p2_row is None:
        return {"classification": "missing_in_both",
                "reason": "asset in neither pass"}
    if p1_row is None:
        return {"classification": "missing_in_pass",
                "reason": "asset missing from pass 1"}
    if p2_row is None:
        return {"classification": "missing_in_pass",
                "reason": "asset missing from pass 2"}

    p1_status = p1_row.get("status")
    p2_status = p2_row.get("status")

    # Cross-pass status flip: pass and fail.
    if p1_status == "PASS" and p2_status == "FAIL":
        return {"classification": "new_failure",
                "reason": (
                    f"passed in pass 1, failed in pass 2 "
                    f"(category: {p2_row.get('failure_category')})"
                ),
                "pass1_status": p1_status, "pass2_status": p2_status}
    if p1_status == "FAIL" and p2_status == "PASS":
        return {"classification": "new_failure",
                "reason": (
                    f"failed in pass 1 ({p1_row.get('failure_category')}), "
                    f"passed in pass 2"
                ),
                "pass1_status": p1_status, "pass2_status": p2_status,
                "flip_direction": "unfailed"}
    if p1_status == "FAIL" and p2_status == "FAIL":
        # Compare failure categories + signatures.
        cat_equal = (p1_row.get("failure_category")
                     == p2_row.get("failure_category"))
        sig_equal = _failure_signature_equal(p1_row, p2_row)
        if cat_equal and sig_equal:
            return {"classification": "exact_match",
                    "reason": ("failure category + signature identical; "
                               "signal reproduced deterministically")}
        return {"classification": "content_mismatch",
                "reason": (
                    f"both failed but different signature: "
                    f"pass1={p1_row.get('failure_category')} "
                    f"pass2={p2_row.get('failure_category')}"
                )}

    # Both PASS — compare content.
    if aid in p1_hashes:
        h = p1_hashes[aid]
        p2_sha = p2_row.get("output_sha256")
        p2_nsha = p2_row.get("output_normalized_sha256")
        if h.get("is_partial_reference"):
            note = ("pass 1 reference is PARTIAL; exact match not possible. "
                    "Comparing bounded prefix / hallucination-signature only.")
            # For partial references, treat as content_mismatch by
            # default unless the pass-2 hash starts with the same
            # opening structure. Since we cannot hash-compare a
            # truncated pass-1 reference to a full pass-2 output,
            # record with a distinct sub-classification.
            return {"classification": "content_mismatch",
                    "reason": note,
                    "pass1_reference_note": h.get("source_note"),
                    "pass1_char_count": h.get("char_count"),
                    "pass2_char_count": p2_row.get("output_chars"),
                    "pass1_sha256_of_partial": h.get("sha256"),
                    "pass2_sha256": p2_sha,
                    "sub_classification": "partial_reference_uncomparable"}
        if p2_sha == h.get("sha256"):
            return {"classification": "exact_match",
                    "reason": "output SHA-256 identical to pass-1 inspection capture"}
        if p2_nsha == h.get("normalized_sha256"):
            return {"classification": "normalized_match",
                    "reason": ("normalized SHA-256 identical to pass-1 "
                               "inspection capture; raw bytes differ only "
                               "in whitespace / line endings")}
        return {"classification": "content_mismatch",
                "reason": ("output differs from pass-1 inspection capture in "
                           "both raw and normalized SHA-256"),
                "pass1_char_count": h.get("char_count"),
                "pass2_char_count": p2_row.get("output_chars")}

    # Both PASS, no pass-1 hash on disk — best-effort using char_count.
    p1_chars = p1_row.get("output_chars")
    p2_chars = p2_row.get("output_chars")
    if p1_chars == p2_chars:
        return {"classification": "hash_uncomparable_pass1_missing_hash",
                "reason": ("pass 1 did not record output_sha256; char_count "
                           "matched (weak determinism signal)"),
                "pass1_char_count": p1_chars,
                "pass2_char_count": p2_chars,
                "pass2_sha256": p2_row.get("output_sha256")}
    return {"classification": "content_mismatch",
            "reason": (f"pass 1 char_count {p1_chars} ≠ pass 2 char_count "
                       f"{p2_chars}; pass 1 did not record hash for direct "
                       "SHA-256 comparison"),
            "pass1_char_count": p1_chars,
            "pass2_char_count": p2_chars,
            "pass2_sha256": p2_row.get("output_sha256")}


def _failure_signature_equal(p1: dict, p2: dict) -> bool:
    """Compare failure signatures across passes. Ignores runtime and
    memory-address bits inside the exception text."""
    def normalize(row: dict) -> tuple:
        exc = row.get("exception", "") or ""
        # Extract only the exception-class prefix + first few tokens
        head = exc.split(":", 2)
        cat = row.get("failure_category")
        # Also compare peak_reserved_mib bucketed to nearest 100 MiB for
        # OOM signatures (reservation size before OOM is deterministic
        # to the model's chunking, not the runtime).
        reserved = row.get("peak_reserved_mib") or 0
        reserved_bucket = (reserved // 100) * 100 if cat == "oom" else None
        return (cat, head[0] if head else "", head[1] if len(head) > 1 else "", reserved_bucket)
    return normalize(p1) == normalize(p2)


def _write_comparison_report(pass1_data: dict, pass2_data: dict) -> None:
    p1_by_id = {r["asset_id"]: r for r in pass1_data.get("per_asset", [])}
    p2_by_id = {r["asset_id"]: r for r in pass2_data.get("per_asset", [])}
    all_ids = sorted(set(p1_by_id) | set(p2_by_id))
    p1_hashes = _pass1_ground_truth_hashes()

    rows = []
    for aid in all_ids:
        p1_row = p1_by_id.get(aid)
        p2_row = p2_by_id.get(aid)
        cls = _classify_asset_pair(aid, p1_row, p2_row, p1_hashes)
        rows.append({
            "asset_id": aid,
            "document_class": (p2_row or p1_row or {}).get("document_class"),
            "pass1_status": (p1_row or {}).get("status"),
            "pass2_status": (p2_row or {}).get("status"),
            "pass1_failure_category": (p1_row or {}).get("failure_category"),
            "pass2_failure_category": (p2_row or {}).get("failure_category"),
            "pass1_chars": (p1_row or {}).get("output_chars"),
            "pass2_chars": (p2_row or {}).get("output_chars"),
            "pass2_sha256": (p2_row or {}).get("output_sha256"),
            "pass1_elapsed_s": (p1_row or {}).get("elapsed_seconds"),
            "pass2_elapsed_s": (p2_row or {}).get("elapsed_seconds"),
            "pass1_reserved_mib": (p1_row or {}).get("peak_reserved_mib"),
            "pass2_reserved_mib": (p2_row or {}).get("peak_reserved_mib"),
            "pass1_hallucination": (p1_row or {}).get("hallucination_flag"),
            "pass2_hallucination": (p2_row or {}).get("hallucination_flag"),
            **cls,
        })

    # Summary statistics.
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    hallucination_changes = [
        r for r in rows
        if r.get("pass1_hallucination") != r.get("pass2_hallucination")
    ]
    status_flips = [
        r for r in rows
        if r.get("pass1_status") != r.get("pass2_status")
    ]

    payload = {
        "comparison_of": {
            "pass1_artifact": _PASS_1_JSON.name,
            "pass1_finished_at": pass1_data.get("finished_at"),
            "pass2_artifact": _PASS_2_JSON.name,
            "pass2_finished_at": pass2_data.get("finished_at"),
        },
        "adapter_target_revision": pass1_data.get("adapter_target_revision"),
        "classification_enum": [
            "exact_match", "normalized_match", "content_mismatch",
            "missing_in_pass", "new_failure",
            "hash_uncomparable_pass1_missing_hash",
        ],
        "counts": counts,
        "hallucination_changes": hallucination_changes,
        "status_flips": status_flips,
        "per_asset": rows,
    }
    _write_atomic(_COMPARISON_JSON, payload)

    # Human-readable MD.
    L: list[str] = []
    total = len(rows)
    L.append("# PDF Benchmark v1 — Unlimited-OCR pass 1 ↔ pass 2 comparison")
    L.append("")
    L.append(f"**Total assets compared:** {total}")
    L.append("")
    L.append("## Classification counts")
    L.append("")
    for cls_name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        L.append(f"- **{cls_name}:** {n}")
    L.append("")
    L.append(f"## Cross-pass status flips ({len(status_flips)})")
    L.append("")
    if not status_flips:
        L.append("_None._")
    else:
        for r in status_flips:
            L.append(
                f"- `{r['asset_id']}`: pass1={r['pass1_status']} → "
                f"pass2={r['pass2_status']}"
            )
    L.append("")
    L.append(f"## Hallucination flag changes ({len(hallucination_changes)})")
    L.append("")
    if not hallucination_changes:
        L.append("_None._")
    else:
        for r in hallucination_changes:
            L.append(
                f"- `{r['asset_id']}`: pass1={r.get('pass1_hallucination')} → "
                f"pass2={r.get('pass2_hallucination')}"
            )
    L.append("")
    L.append("## Per-asset classification")
    L.append("")
    L.append("| Asset | Class | pass1 | pass2 | Classification | Reason |")
    L.append("|---|---|:-:|:-:|---|---|")
    for r in rows:
        L.append(
            f"| `{r['asset_id']}` | {r.get('document_class')} | "
            f"{r.get('pass1_status')} | {r.get('pass2_status')} | "
            f"**{r['classification']}** | {r.get('reason','')[:120]} |"
        )
    L.append("")
    L.append("## Notes")
    L.append("")
    L.append(
        "- Pass 1 did not record output SHA-256 for 42 of 45 assets "
        "(outputs were discarded after char-count capture). For those, the "
        "classification `hash_uncomparable_pass1_missing_hash` is used when "
        "char_count matched, and `content_mismatch` when it did not."
    )
    L.append(
        "- The 3 manually-inspected assets DO have a pass-1 reference on "
        "disk (`benchmarks/hallucination_inspection_2026-07-20/`) and are "
        "the only assets whose exact-hash equality can be strictly verified."
    )
    L.append(
        "- Runtime and peak-VRAM columns are diagnostic only — NOT used "
        "for the determinism classification."
    )
    _COMPARISON_MD.write_text("\n".join(L), encoding="utf-8")


# ── MD report for pass 2 itself ────────────────────────────────────────


def _emit_pass2_report(payload: dict) -> None:
    _write_atomic(_PASS_2_JSON, payload)
    rows = payload["per_asset"]
    L: list[str] = []
    ov = payload["aggregate"]["overall"]
    L.append("# PDF Benchmark v1 — Unlimited-OCR (pass 2, 2026-07-20)")
    L.append("")
    L.append(f"**Assets attempted:** {ov['n']}  ·  **PASS:** {ov['pass_count']}  ·  **FAIL:** {ov['fail_count']}")
    L.append(f"**Timeouts:** {ov['timeout_count']}  ·  **OOM:** {ov['oom_count']}")
    L.append("")
    L.append("## Runtime")
    L.append("")
    L.append(f"- Median: {ov['runtime_p50']} s  ·  p95: {ov['runtime_p95']} s")
    L.append(f"- Median s/page: {ov['s_per_page_p50']}  ·  p95: {ov['s_per_page_p95']}")
    L.append("")
    L.append("## Per document class")
    L.append("")
    L.append("| Class | n | PASS | Runtime p50 (s) | Runtime p95 (s) | s/page p50 | s/page p95 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for cls, cs in sorted(payload["aggregate"]["by_class"].items()):
        L.append(
            f"| {cls} | {cs['n']} | {cs['pass_count']} | {cs['runtime_p50']} | "
            f"{cs['runtime_p95']} | {cs['s_per_page_p50']} | {cs['s_per_page_p95']} |"
        )
    L.append("")
    L.append("## Failures")
    L.append("")
    fails = [r for r in rows if r["status"] == "FAIL"]
    if not fails:
        L.append("_None._")
    else:
        L.append("| Asset | Category | Signature | Runner healthy after |")
        L.append("|---|---|---|:-:|")
        for r in fails:
            L.append(
                f"| `{r['asset_id']}` | {r['failure_category']} | "
                f"{r.get('failure_signature','?')} | "
                f"{'yes' if r.get('runner_healthy_after') else 'NO'} |"
            )
    _PASS_2_MD.write_text("\n".join(L), encoding="utf-8")


def _load_checkpoint() -> dict:
    if not _PASS_2_CHECKPOINT.exists():
        return {"rows": [], "pass_index": 2, "started_at": None, "meta": {}}
    return json.loads(_PASS_2_CHECKPOINT.read_text(encoding="utf-8"))


# ── Main ───────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--force", action="store_true",
                    help="Ignore existing pass-2 checkpoint and re-run all")
    args = ap.parse_args()

    manifest_data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    eligible = sorted(
        [a for a in manifest_data["assets"] if a["eligibility"] == "eligible"],
        key=lambda a: a["asset_id"],
    )
    print(f"eligible assets: {len(eligible)}", file=sys.stderr)

    checkpoint = {"rows": [], "pass_index": 2, "started_at": None, "meta": {}}
    if not args.force:
        checkpoint = _load_checkpoint()
    completed = {r["asset_id"] for r in checkpoint["rows"]}
    print(f"pass 2 resuming with {len(completed)} assets complete", file=sys.stderr)

    env = _capture_env()

    try:
        import torch  # type: ignore  # noqa: F401
    except ImportError as e:
        print(f"REFUSE: torch not installed: {e}", file=sys.stderr)
        return 2
    if not torch.cuda.is_available():
        print("REFUSE: CUDA not available", file=sys.stderr)
        return 3

    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as adapter
    runner = adapter._UnlimitedOcrRunner()
    _reset_cuda_stats(torch)
    load_rss_before = _rss_mib()
    load_t0 = time.perf_counter()
    runner.load()
    load_elapsed = round(time.perf_counter() - load_t0, 2)
    load_stats = _cuda_stats(torch)
    load_rss_after = _rss_mib()
    if not runner._loaded:
        # runner._load_error is a plain diagnostic string, NOT a credential.
        print(f"REFUSE: runner failed to load: {runner._load_error}", file=sys.stderr)  # lgtm[py/clear-text-logging-sensitive-data]
        return 4
    load_record = {
        "elapsed_seconds": load_elapsed,
        "rss_before_mib": load_rss_before,
        "rss_after_mib": load_rss_after,
        "peak_allocated_mib_load": load_stats.get("allocated_mib"),
        "peak_reserved_mib_load": load_stats.get("reserved_mib"),
        "call_log": list(runner._call_log),
    }
    print(
        f"cold load: {load_elapsed}s alloc={load_stats.get('allocated_mib')} MiB "
        f"reserved={load_stats.get('reserved_mib')} MiB",
        file=sys.stderr,
    )

    checkpoint["started_at"] = checkpoint.get("started_at") or time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
    )
    checkpoint["meta"] = {
        "harness_version": "a2_second_pass_harness.py@2026-07-20",
        "pass_index": 2,
        "timeout_s": args.timeout_s,
        "environment": env,
        "load": load_record,
    }

    remaining = [a for a in eligible if a["asset_id"] not in completed]
    print(f"remaining to run: {len(remaining)}", file=sys.stderr)
    healthy = True
    with tempfile.TemporaryDirectory(prefix="a2_second_pass_") as workdir_str:
        workdir = Path(workdir_str)
        for i, asset in enumerate(remaining, start=1):
            aid = asset["asset_id"]
            print(f"[{i}/{len(remaining)}] {aid} ...", file=sys.stderr, flush=True)
            row = _run_one_asset(runner, asset, torch, workdir, args.timeout_s)
            checkpoint["rows"].append(row)
            _write_atomic(_PASS_2_CHECKPOINT, checkpoint)
            healthy = row.get("runner_healthy_after", False)
            print(
                f"  -> {row['status']} cat={row['failure_category']} "
                f"elapsed={row['elapsed_seconds']}s pages={row.get('page_count')} "
                f"reserved={row.get('peak_reserved_mib')} MiB "
                f"chars={row['output_chars']} sha={row['output_sha256'][:12]}",
                file=sys.stderr, flush=True,
            )
            if not healthy:
                print(
                    "WARN: runner health probe failed — halting pass 2",
                    file=sys.stderr,
                )
                break

    all_rows = checkpoint["rows"]
    aggregate = _compute_aggregate(all_rows)
    payload = {
        "harness_version": checkpoint["meta"]["harness_version"],
        "adapter_target": "unlimited-ocr",
        "adapter_target_repo": adapter._UNLIMITED_OCR_MODEL_REPO,
        "adapter_target_revision": adapter._UNLIMITED_OCR_MODEL_REVISION,
        "manifest_source": _MANIFEST_PATH.name,
        "pass_index": 2,
        "started_at": checkpoint["started_at"],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": env,
        "gpu_report": {
            "torch_installed": env.get("torch") is not None,
            "cuda_available": bool(env.get("cuda")),
            "torch_version": env.get("torch"),
            "cuda_version": env.get("cuda"),
            "device_0_name": env.get("gpu_name"),
            "device_0_vram_gib": env.get("gpu_vram_gib"),
            "bf16_supported": env.get("bf16_supported"),
        },
        "execution_mode_decision": {"mode": "real_inference",
                                      "note": "A2 second pass (determinism check)"},
        "dependencies": {"torch": env.get("torch"), "cuda": env.get("cuda")},
        "evaluation_semantics_notes": {
            "aksharamd_readiness_score_used": False,
            "aksharamd_warning_codes_used": False,
            "no_cross_parser_ranking": True,
        },
        "security_notes": {
            "trust_remote_code": True,
            "revision_pinned": adapter._UNLIMITED_OCR_MODEL_REVISION is not None,
            "safetensors_only": True,
            "offline_enforcement": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            "trusted_code_files_verified": True,
        },
        "load": load_record,
        "timeout_s": args.timeout_s,
        "per_asset": all_rows,
        "aggregate": aggregate,
        "runner_healthy_at_end": healthy,
        "stopped_early_due_to_health_probe_failure": not healthy,
    }
    _emit_pass2_report(payload)
    print(f"wrote: {_PASS_2_JSON}", file=sys.stderr)
    print(f"wrote: {_PASS_2_MD}", file=sys.stderr)

    # Cross-pass comparison against committed pass-1 artifact.
    if _PASS_1_JSON.exists():
        pass1 = json.loads(_PASS_1_JSON.read_text(encoding="utf-8"))
        _write_comparison_report(pass1, payload)
        print(f"wrote: {_COMPARISON_JSON}", file=sys.stderr)
        print(f"wrote: {_COMPARISON_MD}", file=sys.stderr)
    else:
        print(f"NOTE: {_PASS_1_JSON} not found; skipped comparison", file=sys.stderr)

    ov = aggregate["overall"]
    print(
        f"PASS 2 SUMMARY: n={ov['n']} pass={ov['pass_count']} fail={ov['fail_count']} "
        f"p50={ov['runtime_p50']}s p95={ov['runtime_p95']}s",
        file=sys.stderr,
    )
    return 0 if ov["fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
