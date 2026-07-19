"""Known-case parity audit for PDF Benchmark v1 Phase 1.

Recompiles a small set of asset ids and verifies the benchmark JSON
matches what the shipped compiler produced. Answers: is the harness
faithfully reading warning codes, readiness score, quality band, page
count, and character count from the compile output?

Reads from:

- ``benchmarks/PDF_BENCHMARK_V1_BASELINE_2026-07-19.json`` (existing baseline)
- ``benchmarks/pdf_benchmark_v1_manifest.json`` (paths + metadata)

Recompiles each named asset once in a fresh temp workdir and reads:

- shipped ``document.json > metadata > multicolumn_diagnostics`` (if any)
- shipped ``document.md`` (character count)
- shipped stdout JSON (readiness_score, quality_band, warning_codes, pages)

Emits a machine-readable parity report and returns non-zero if any
field drifts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404 - orchestrates aksharamd CLI
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Deliberately picked to cover: strikeUnderline (sidebar FP), 3colpres
# (block-level TP), elpais (span-only TP), imagemagick-CCITTFaxDecode
# (image-only content failure), pdflatex-forms (malformed / form),
# minimal-document (clean native-text control).
_KNOWN_CASES = [
    "parsebench/strikeUnderline",
    "parsebench/3colpres",
    "parsebench/elpais",
    "public/007-imagemagick-images/imagemagick-CCITTFaxDecode.pdf",
    "public/010-pdflatex-forms/pdflatex-forms.pdf",
    "public/001-trivial/minimal-document.pdf",
]


def _compile_once(binary: str, pdf: Path, out_root: Path) -> tuple[dict[str, Any], Path]:
    stem = pdf.stem
    out_dir = out_root / stem
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    proc = subprocess.run(  # nosec B603 - shutil.which resolves binary; args are local paths
        [binary, "compile", str(pdf), "-o", str(out_dir), "--json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"compile failed for {pdf.name}: {proc.stderr[:400]}")
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"_stdout_parse_error": True}
    payload["_out_dir"] = out_dir
    return payload, out_dir


def _read_document_md(out_dir: Path) -> str:
    stem = out_dir.name
    p = out_dir / stem / "document.md"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _run(baseline_json: Path, manifest_json: Path, output: Path) -> int:
    with baseline_json.open("r", encoding="utf-8") as f:
        baseline = json.load(f)
    with manifest_json.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    per_asset_baseline = {row["asset_id"]: row for row in baseline["per_asset"]}
    paths_by_id = {a["asset_id"]: a["pdf_path"] for a in manifest["assets"]}

    binary = shutil.which("aksharamd")
    if binary is None:
        print("aksharamd not on PATH", file=sys.stderr)
        return 42
    workdir = Path(tempfile.mkdtemp(prefix="parity_audit_"))

    audit_rows: list[dict[str, Any]] = []
    all_ok = True
    for aid in _KNOWN_CASES:
        if aid not in per_asset_baseline:
            audit_rows.append({"asset_id": aid, "status": "not_in_baseline"})
            all_ok = False
            continue
        row = per_asset_baseline[aid]
        pdf = Path(paths_by_id[aid])
        payload, out_dir = _compile_once(binary, pdf, workdir)
        doc_md = _read_document_md(out_dir)
        actual = {
            "output_chars": len(doc_md),
            "readiness_score": payload.get("readiness_score"),
            "quality_band": payload.get("quality_band"),
            "warning_codes": sorted(payload.get("warning_codes") or []),
            "pages": payload.get("pages"),
        }
        expected = {
            "output_chars": row["output_chars"],
            "readiness_score": row["readiness_score"],
            "quality_band": row["quality_band"],
            "warning_codes": sorted(row.get("warning_codes") or []),
            "pages": row.get("page_count_output"),
        }
        drift = {k: (expected[k], actual[k]) for k in expected if expected[k] != actual[k]}
        status = "match" if not drift else "drift"
        if drift:
            all_ok = False
        audit_rows.append({
            "asset_id": aid,
            "status": status,
            "expected": expected,
            "actual": actual,
            "drift": drift,
        })

    payload_out = {
        "harness_version": "pdf_benchmark_v1_parity_audit.py@2026-07-19",
        "commit_under_evaluation": baseline.get("commit_under_evaluation"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "known_cases": _KNOWN_CASES,
        "audit_rows": audit_rows,
        "all_match": all_ok,
    }
    output.write_text(json.dumps(payload_out, indent=2), encoding="utf-8")
    print(f"wrote {output} — all_match={all_ok}")
    return 0 if all_ok else 44


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_BASELINE_2026-07-19.json")
    ap.add_argument("--manifest", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json")
    ap.add_argument("--output", type=Path,
                    default=_REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_PARITY_AUDIT_2026-07-19.json")
    args = ap.parse_args()
    return _run(args.baseline, args.manifest, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
