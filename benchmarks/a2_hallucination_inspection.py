"""A2 first-pass hallucination inspection — captures raw model output
for the 3 assets flagged by the automated `output > 3x hidden_chars`
rule and emits a structured manual-review artifact per the reviewer's
fixed classification.

Runs the same loaded runner on each of the three flagged assets in
turn (fresh model output — the first-pass harness's TemporaryDirectory
discarded the text). Saves each raw output to disk and computes
automated repetition signals to inform the classification.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST_PATH = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"
_OUT_DIR = _REPO_ROOT / "benchmarks" / "hallucination_inspection_2026-07-20"

FLAGGED = [
    "public/008-reportlab-inline-image/inline-image.pdf",
    "public/028-image-references-deduplication/wrong-references.pdf",
    "public/027-cropped-rotated-scaled/cropped-rotated-scaled.pdf",  # priority
]


def _repetition_signals(text: str) -> dict:
    """Automated repetition analysis to inform the manual classification."""
    if not text:
        return {"n_chars": 0, "n_lines": 0}
    lines = text.splitlines()
    non_blank_lines = [ln for ln in lines if ln.strip()]
    line_counts = Counter(non_blank_lines)
    duplicate_lines = sum(c for c in line_counts.values() if c > 1)
    unique_lines = len(line_counts)
    # 4-gram repetition
    words = text.split()
    if len(words) >= 8:
        ngrams = [tuple(words[i:i + 4]) for i in range(len(words) - 3)]
        ngram_counts = Counter(ngrams)
        repeated_ngram_windows = sum(c for c in ngram_counts.values() if c > 1)
        ngram_repetition_ratio = repeated_ngram_windows / max(1, len(ngrams))
    else:
        ngram_repetition_ratio = 0.0
    # Longest repeated line
    top_line, top_count = (line_counts.most_common(1) or [("", 0)])[0]
    return {
        "n_chars": len(text),
        "n_lines": len(lines),
        "n_non_blank_lines": len(non_blank_lines),
        "unique_lines": unique_lines,
        "duplicate_lines": duplicate_lines,
        "duplicate_line_fraction": (
            round(duplicate_lines / len(non_blank_lines), 4) if non_blank_lines else 0.0
        ),
        "4gram_repetition_ratio": round(ngram_repetition_ratio, 4),
        "most_repeated_line_count": top_count,
        "most_repeated_line_preview": (top_line[:120] + "…") if len(top_line) > 120 else top_line,
    }


def _resolve_asset(manifest: dict, asset_id: str) -> dict:
    for a in manifest["assets"]:
        if a["asset_id"] == asset_id:
            return a
    raise SystemExit(f"asset not in manifest: {asset_id}")


def _classify_manual(text: str, signals: dict, hidden_chars: int | None) -> tuple[str, str]:
    """Structured pre-classification based on automated signals. The
    reviewer's final classification is documented in the report; this
    function's role is to propose a starting point.

    Returns (proposed_class, reason).
    """
    # Reviewer's fixed enum:
    #   true_hallucination | repetition_or_looping |
    #   legitimate_visual_content | minor_inflation |
    #   automated_rule_false_positive | unreviewable
    if signals["n_chars"] < 100 and hidden_chars is not None and hidden_chars < 20:
        return ("automated_rule_false_positive",
                f"absolute excess {signals['n_chars'] - hidden_chars} chars "
                f"is not material; low-hidden-chars document")
    if signals.get("4gram_repetition_ratio", 0.0) > 0.5:
        return ("repetition_or_looping",
                f"4-gram repetition ratio {signals['4gram_repetition_ratio']} > 0.50")
    if signals.get("duplicate_line_fraction", 0.0) > 0.5:
        return ("repetition_or_looping",
                f"duplicate-line fraction {signals['duplicate_line_fraction']} > 0.50")
    if signals.get("most_repeated_line_count", 0) > 20:
        return ("repetition_or_looping",
                f"one line repeats {signals['most_repeated_line_count']} times")
    if signals["n_chars"] > 5000 and hidden_chars and hidden_chars < 500:
        return ("legitimate_visual_content",
                f"large output ({signals['n_chars']} chars) but only "
                f"{hidden_chars} hidden chars — likely image/figure text extraction")
    return ("minor_inflation",
            f"ratio {signals['n_chars']}/{hidden_chars or 0} does not match "
            "any looping or extraction signal cleanly")


def main() -> int:
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    targets = [_resolve_asset(manifest, aid) for aid in FLAGGED]
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        import torch  # type: ignore  # noqa: F401  - availability probe
    except ImportError as e:
        print(f"REFUSE: torch not installed: {e}", file=sys.stderr)
        return 2

    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as adapter
    print("Loading runner (fresh process)…", file=sys.stderr)
    runner = adapter._UnlimitedOcrRunner()
    load_t0 = time.perf_counter()
    runner.load()
    load_elapsed = round(time.perf_counter() - load_t0, 2)
    if not runner._loaded:
        # runner._load_error is a plain diagnostic string, NOT a credential.
        print(f"REFUSE: runner failed: {runner._load_error}", file=sys.stderr)  # lgtm[py/clear-text-logging-sensitive-data]
        return 3
    print(f"Loaded in {load_elapsed}s", file=sys.stderr)

    results = []
    with tempfile.TemporaryDirectory(prefix="hall_inspect_") as scratch:
        workdir = Path(scratch)
        for asset in targets:
            aid = asset["asset_id"]
            pdf = Path(asset["pdf_path"])
            print(f"Inferring {aid} …", file=sys.stderr, flush=True)
            t0 = time.perf_counter()
            text, exc, tool_signals = runner.infer_pdf(pdf, workdir)
            elapsed = round(time.perf_counter() - t0, 2)
            # Save raw output alongside the report so a reviewer can
            # eyeball the actual text.
            safe_name = re.sub(r"[^a-zA-Z0-9]", "_", aid)
            out_txt = _OUT_DIR / f"{safe_name}.output.md"
            out_txt.write_text(text or "", encoding="utf-8")
            # Hidden text layer chars (for the ratio context).
            hidden = None
            try:
                import fitz  # type: ignore
                with fitz.open(str(pdf)) as doc:
                    hidden = sum(len(page.get_text() or "") for page in doc)
            except Exception:
                # Hidden-chars is an optional context signal; if PyMuPDF
                # cannot parse the file, leave it as None and continue.
                pass
            signals = _repetition_signals(text or "")
            proposed_class, proposed_reason = _classify_manual(text or "", signals, hidden)
            sha = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
            result = {
                "asset_id": aid,
                "pdf_path": str(pdf),
                "output_file": str(out_txt.relative_to(_REPO_ROOT)),
                "elapsed_seconds": elapsed,
                "hidden_text_layer_chars": hidden,
                "output_chars": len(text or ""),
                "output_sha256": sha,
                "exception": exc,
                "automated_repetition_signals": signals,
                "manual_classification": proposed_class,
                "manual_classification_reason": proposed_reason,
                "reviewer_final_classification": None,  # to be filled in the report
                "reviewer_final_notes": None,
            }
            results.append(result)
            print(
                f"  -> {len(text or '')} chars, {elapsed}s, "
                f"proposed={proposed_class} ({proposed_reason})",
                file=sys.stderr,
            )

    report_path = _OUT_DIR / "review.json"
    report_path.write_text(json.dumps({
        "harness_version": "a2_hallucination_inspection.py@2026-07-20",
        "load_elapsed_seconds": load_elapsed,
        "flagged_assets": FLAGGED,
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"wrote: {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
