"""Multicolumn recalibration harness (Issue #50, phase 1: measurement only).

Runs the installed AksharaMD CLI against every PDF in the public corpus
and captures per-page detector diagnostics for W_MULTICOLUMN_ORDER. Emits
a machine-readable JSON result file that the accompanying report reads
to compute precision / recall / FPR / F1 against the frozen labels.

**No production code changes here.** The harness is evidence gathering
only — later PRs may propose detector improvements.

Usage (from repo root, with an installed `aksharamd` on PATH or
AKSHARAMD_E2E_BINARY set):

    python benchmarks/multicolumn_recalibration.py \
        --corpus benchmarks/.public_corpus/pdf \
        --labels benchmarks/multicolumn_recalibration_labels.json \
        --output benchmarks/MULTICOLUMN_RECALIBRATION_2026-07-18.json

Determinism: two consecutive runs on the same commit and corpus must
produce identical JSON output (bytes) except for the top-level
``run_started_at`` timestamp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _cli_binary() -> str:
    binary = os.environ.get("AKSHARAMD_E2E_BINARY") or shutil.which("aksharamd")
    if binary is None:
        raise SystemExit(
            "aksharamd CLI not installed on PATH; set AKSHARAMD_E2E_BINARY or "
            "pip install the wheel into a venv on PATH before running."
        )
    return binary


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _installed_wheel_version(binary: str) -> str:
    r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    return (r.stdout or r.stderr).strip()


def _compile_one(binary: str, pdf: Path, out_root: Path) -> dict:
    """Run `aksharamd compile <pdf> -o <out_root>/<stem> --json --quiet` and
    return a snapshot of the interesting artefacts.
    """
    stem = pdf.stem
    out_dir = out_root / stem
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    started = time.time()
    r = subprocess.run(
        [binary, "compile", str(pdf), "-o", str(out_dir), "--json", "--quiet"],
        capture_output=True, text=True, timeout=600,
    )
    elapsed = round(time.time() - started, 3)

    result: dict = {
        "asset": pdf.name,
        "relpath": str(pdf.relative_to(pdf.parents[2])) if len(pdf.parents) >= 3 else pdf.name,
        "sha256": _sha256(pdf),
        "cli_exit": r.returncode,
        "cli_elapsed_s": elapsed,
    }
    if r.returncode != 0:
        result["cli_stderr_head"] = (r.stderr or "")[:400]
        return result

    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        result["stdout_parse_error"] = str(exc)
        result["stdout_head"] = r.stdout[:400]
        return result
    result["compile_summary"] = {
        "readiness_score": payload.get("readiness_score"),
        "quality_band": payload.get("quality_band"),
        "warning_codes": payload.get("warning_codes") or [],
        "informational": payload.get("informational") or [],
        "pages": payload.get("pages"),
        "chunks": payload.get("chunks"),
        "optimized_tokens": payload.get("optimized_tokens"),
    }

    # Pull richer diagnostics from the per-file document.json (metadata carries the
    # validator diagnostics + column info) and manifest.json.
    doc_json_path = out_dir / stem / "document.json"
    manifest_path = out_dir / stem / "manifest.json"
    if doc_json_path.exists():
        with doc_json_path.open("r", encoding="utf-8") as f:
            document = json.load(f)
        meta = document.get("metadata") or {}
        result["multicolumn_diagnostics"] = meta.get("multicolumn_diagnostics") or None
        col_info = meta.get("pdf_column_info") or {}
        result["column_info_by_page"] = {
            str(k): {
                "boundaries": v.get("boundaries"),
                "num_columns": v.get("num_columns"),
                "page_width": v.get("page_width"),
            }
            for k, v in col_info.items()
        }
        result["blocks_total"] = len(document.get("blocks") or [])
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        result["pdf_classification"] = manifest.get("pdf_classification")
        result["duplicate_blocks_removed"] = manifest.get("duplicate_blocks_removed")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True, type=Path, help="Directory containing PDF/ subdirs")
    ap.add_argument("--labels", required=True, type=Path, help="Path to labels JSON")
    ap.add_argument("--output", required=True, type=Path, help="Where to write the result JSON")
    ap.add_argument("--out-root", type=Path, default=None,
                    help="Working directory for per-doc CLI outputs (defaults to a temp path)")
    args = ap.parse_args()

    binary = _cli_binary()
    version_line = _installed_wheel_version(binary)

    with args.labels.open("r", encoding="utf-8") as f:
        labels_doc = json.load(f)
    labels: dict[str, dict] = labels_doc.get("labels", {})

    default_root = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "aksharamd_mc_recalibration"
    out_root = args.out_root or default_root
    out_root.mkdir(parents=True, exist_ok=True)

    # Enumerate PDFs directly under corpus/<id>/*.pdf.
    pdfs: list[Path] = sorted(p for p in args.corpus.rglob("*.pdf"))
    print(f"Corpus root: {args.corpus}", file=sys.stderr)
    print(f"Discovered {len(pdfs)} PDF(s).", file=sys.stderr)
    print(f"Installed CLI: {binary} ({version_line})", file=sys.stderr)

    results: list[dict] = []
    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(args.corpus).as_posix()
        label = labels.get(rel) or labels.get(pdf.name) or {}
        print(f"[{i}/{len(pdfs)}] {rel}", file=sys.stderr)
        snap = _compile_one(binary, pdf, out_root)
        snap["label"] = label
        results.append(snap)

    document = {
        "harness_version": "1",
        "run_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cli_version": version_line,
        "corpus_root": str(args.corpus),
        "labels_source": str(args.labels),
        "commit": os.environ.get("AKSHARAMD_COMMIT_UNDER_TEST", ""),
        "detector": {
            "name": "W_MULTICOLUMN_ORDER (MultiColumnOrderValidator)",
            "maturity": "candidate",
            "penalty": 0,
            "implementation": "aksharamd/plugins/validators/multicolumn.py",
            "boundary_helper": "aksharamd/plugins/parsers/pdf.py::_detect_column_boundaries",
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, sort_keys=False)
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
