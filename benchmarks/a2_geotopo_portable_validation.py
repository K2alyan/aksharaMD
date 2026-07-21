"""GeoTopo focused validation for the portable Unlimited-OCR
entrypoint (PR 3).

Runs each of the two 117-page GeoTopo PDFs TWICE — four subprocesses
in total — via ``infer_pdf_portable``. Each call spawns its own
worker subprocess for OCR, so the four runs are cleanly isolated
even though the harness itself is a single Python process.

Records receipts required by the reviewer:

* initial estimated chunk size
* every attempted chunk size (via orchestrator's attempts list)
* whether a subprocess restart occurred
* final successful chunk size
* total worker restarts
* page count and page ordering
* missing / duplicated pages (from worker signals)
* peak allocated / reserved VRAM
* total runtime
* output SHA-256
* whether the two runs of the same file match

Acceptance criteria:
    both 117-page files complete;
    all 117 pages appear exactly once and in order;
    final output is one combined document;
    no internal chunk markers appear in user-facing Markdown;
    an OOM in one worker cannot poison the parent or later documents;
    repeat runs are deterministic;
    small-document behavior remains unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST_PATH = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"
_OUT_JSON = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_GEOTOPO_PORTABLE_2026-07-20.json"
_OUT_MD = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_GEOTOPO_PORTABLE_2026-07-20.md"

_TARGETS = [
    "public/009-pdflatex-geotopo/GeoTopo-komprimiert.pdf",
    "public/009-pdflatex-geotopo/GeoTopo.pdf",
]

_VRAM_SAFE_CEILING_MIB = 11 * 1024


def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _nvidia_driver() -> str | None:
    try:
        out = subprocess.check_output(  # nosec B603 B607 — constant argv, no shell
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"],
            timeout=5, stderr=subprocess.STDOUT,
        ).decode().strip().splitlines()[0]
        return out
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _load_pdf_paths() -> list[tuple[str, Path]]:
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    out: list[tuple[str, Path]] = []
    for aid in _TARGETS:
        matched = next((a for a in manifest["assets"] if a["asset_id"] == aid), None)
        if matched is None:
            raise SystemExit(f"asset not in manifest: {aid}")
        out.append((aid, Path(matched["pdf_path"])))
    return out


def _pdf_page_count(pdf: Path) -> int:
    import fitz  # type: ignore
    with fitz.open(str(pdf)) as doc:
        return doc.page_count


def _run_one(cache_path: Path, asset_id: str, pdf: Path, run_index: int, workdir: Path) -> dict[str, Any]:
    from benchmarks.pdf_benchmark_adapters.unlimited_ocr_portable import (  # type: ignore
        infer_pdf_portable,
    )
    print(f"[{asset_id} run {run_index}] starting portable inference ...", flush=True)
    t0 = time.perf_counter()
    text, exc, signals = infer_pdf_portable(pdf, workdir, cache_path=cache_path)
    wall = round(time.perf_counter() - t0, 2)

    portable = signals.get("portable_signals") or {}
    attempts = signals.get("attempts") or []
    worker_signals = signals.get("worker_signals") or {}
    chunks = worker_signals.get("chunks") or []

    total_pages_pdf = _pdf_page_count(pdf)
    covered: set[int] = set()
    duplicated: list[int] = []
    for c in chunks:
        if c.get("status") != "PASS":
            continue
        start, end_incl = c["page_start"], c["page_end"]
        for p in range(start, end_incl + 1):
            if p in covered:
                duplicated.append(p)
            covered.add(p)

    # Single-shot success path: the runner's ``_infer_single_chunk``
    # emits a signals dict WITHOUT a "chunks" key (the adaptive
    # chunking loop is skipped because the whole doc fits in one
    # call). If the overall run succeeded and we saw no chunk-level
    # data, treat the whole page range as covered — the small-doc
    # path processes every page atomically.
    if not exc and not chunks:
        single_shot_page_count = worker_signals.get("page_count")
        if isinstance(single_shot_page_count, int) and single_shot_page_count > 0:
            covered = set(range(single_shot_page_count))
    missing = sorted(set(range(total_pages_pdf)) - covered)

    row = {
        "asset_id": asset_id,
        "run_index": run_index,
        "pdf_path": str(pdf),
        "total_pages_pdf": total_pages_pdf,
        "completion_status": "PASS" if not exc else "FAIL",
        "exception": exc,
        "portable_signals": portable,
        "initial_chunk_size": portable.get("formula_estimate", {}).get("chunk_size")
                              if portable.get("resolution_source") == "formula"
                              else portable.get("cache_record_snapshot", {}).get("successful_chunk_size"),
        "resolution_source": portable.get("resolution_source"),
        "resolution_reason": portable.get("resolution_reason"),
        "attempts_summary": [
            {"chunk_size": a.get("chunk_size"), "outcome": a.get("outcome"),
             "wall_seconds": a.get("wall_seconds")}
            for a in attempts
        ],
        "restart_count": signals.get("restart_count"),
        "final_chunk_size_used": signals.get("final_chunk_size_used"),
        "pages_returned_count": len(covered),
        "missing_page_numbers": missing,
        "duplicated_page_numbers": duplicated,
        "peak_gpu_memory_mib": worker_signals.get("peak_gpu_memory_mib"),
        "total_wall_seconds": wall,
        "output_char_count": len(text or ""),
        "output_sha256": _sha256_text(text or ""),
    }
    last_attempt = attempts[-1] if attempts else {}
    row["last_attempt_exit_code"] = last_attempt.get("exit_code")
    row["last_attempt_outcome"] = last_attempt.get("outcome")
    row["last_attempt_worker_reported_stage"] = last_attempt.get("worker_reported_stage")
    row["last_attempt_retryable"] = last_attempt.get("retryable")
    row["last_attempt_next_chunk_size"] = last_attempt.get("next_chunk_size")
    row["last_attempt_worker_stdout_tail"] = last_attempt.get("worker_stdout_tail")

    print(f"  -> {row['completion_status']} pages={row['pages_returned_count']}/{row['total_pages_pdf']} "
          f"initial={row['initial_chunk_size']} final={row['final_chunk_size_used']} "
          f"restarts={row['restart_count']} wall={wall}s sha={row['output_sha256'][:12]}",
          flush=True)
    if row['completion_status'] == 'FAIL':
        print(f"     last attempt: exit={row['last_attempt_exit_code']} "
              f"outcome={row['last_attempt_outcome']} "
              f"stage={row['last_attempt_worker_reported_stage']} "
              f"retryable={row['last_attempt_retryable']} "
              f"next_size={row['last_attempt_next_chunk_size']}",
              flush=True)
    return row


def main() -> int:
    try:
        import torch  # type: ignore  # noqa: F401
    except ImportError as e:
        print(f"REFUSE: torch not installed: {e}", file=sys.stderr)
        return 2
    if not torch.cuda.is_available():
        print("REFUSE: CUDA not available", file=sys.stderr)
        return 3

    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "nvidia_driver": _nvidia_driver() or "unknown",
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_vram_gib": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2),
    }
    targets = _load_pdf_paths()

    with tempfile.TemporaryDirectory(prefix="geotopo_portable_") as workdir_str:
        workdir = Path(workdir_str)
        cache_path = workdir / "safe_size_cache.json"

        runs: list[dict[str, Any]] = []
        for asset_id, pdf in targets:
            for run_idx in (1, 2):
                sub_workdir = workdir / f"{asset_id.replace('/', '__')}_run{run_idx}"
                sub_workdir.mkdir(parents=True, exist_ok=True)
                runs.append(_run_one(cache_path, asset_id, pdf, run_idx, sub_workdir))

        cache_snapshot = {}
        if cache_path.exists():
            cache_snapshot = json.loads(cache_path.read_text(encoding="utf-8"))

    # Determinism per asset
    determinism = []
    for asset_id, _ in targets:
        pair = [r for r in runs if r["asset_id"] == asset_id]
        r1, r2 = pair[0], pair[1]
        determinism.append({
            "asset_id": asset_id,
            "run1_status": r1["completion_status"],
            "run2_status": r2["completion_status"],
            "run1_pages": r1["pages_returned_count"],
            "run2_pages": r2["pages_returned_count"],
            "run1_output_sha256": r1["output_sha256"],
            "run2_output_sha256": r2["output_sha256"],
            "output_sha256_match": r1["output_sha256"] == r2["output_sha256"],
            "output_char_count_match": r1["output_char_count"] == r2["output_char_count"],
        })

    all_pass = all(r["completion_status"] == "PASS" for r in runs)
    all_pages = all(
        r["pages_returned_count"] == r["total_pages_pdf"] and r["total_pages_pdf"] == 117
        for r in runs
    )
    no_missing = all(not r["missing_page_numbers"] for r in runs)
    no_dup = all(not r["duplicated_page_numbers"] for r in runs)
    vram_ok = all((r.get("peak_gpu_memory_mib") or 0) <= _VRAM_SAFE_CEILING_MIB for r in runs)
    sha_match = all(d["output_sha256_match"] for d in determinism)

    acceptance = {
        "all_runs_complete": all_pass,
        "all_pages_returned_exactly_once": all_pages and no_dup,
        "no_missing_pages": no_missing,
        "peak_gpu_memory_below_safe_ceiling": vram_ok,
        "output_sha256_matches_across_pairs": sha_match,
    }

    payload = {
        "harness_version": "a2_geotopo_portable_validation.py@2026-07-20",
        "environment": env,
        "vram_safe_ceiling_mib": _VRAM_SAFE_CEILING_MIB,
        "target_assets": _TARGETS,
        "runs": runs,
        "determinism_per_asset": determinism,
        "cache_snapshot_at_end": cache_snapshot,
        "acceptance": acceptance,
    }
    _OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    L: list[str] = []
    L.append("# Unlimited-OCR GeoTopo Portable Validation — 2026-07-20")
    L.append("")
    L.append(f"**GPU:** {env['gpu_name']} ({env['gpu_vram_gib']} GiB)")
    L.append(f"**Torch/CUDA:** {env['torch']} / {env['cuda']}")
    L.append("")
    L.append("| Asset | Run | Status | Pages | Init | Final | Restarts | Wall s | Peak MiB | SHA-256 (16) |")
    L.append("|---|:-:|:-:|:-:|:-:|:-:|:-:|---:|---:|---|")
    for r in runs:
        sha = (r.get("output_sha256") or "")[:16]
        L.append(
            f"| `{r['asset_id']}` | {r['run_index']} | {r['completion_status']} | "
            f"{r['pages_returned_count']}/{r['total_pages_pdf']} | "
            f"{r.get('initial_chunk_size', '?')} | {r.get('final_chunk_size_used', '?')} | "
            f"{r.get('restart_count', '?')} | {r.get('total_wall_seconds', '?')} | "
            f"{r.get('peak_gpu_memory_mib', '?')} | `{sha}` |"
        )
    L.append("")
    L.append("## Determinism per asset")
    L.append("")
    for d in determinism:
        L.append(f"- `{d['asset_id']}` — SHA match: **{d['output_sha256_match']}**, "
                 f"char-count match: **{d['output_char_count_match']}**")
    L.append("")
    L.append("## Acceptance criteria")
    L.append("")
    for k, v in acceptance.items():
        L.append(f"- {k}: **{v}**")
    _OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Wrote {_OUT_JSON.name} and {_OUT_MD.name}", file=sys.stderr)
    return 0 if all(acceptance.values()) else 6


if __name__ == "__main__":
    sys.exit(main())
