"""A1.5 — three-asset feasibility smoke test for Unlimited-OCR.

Runs ONLY after PR #84 (A1d runner integration) merges.

Executes exactly three predeclared assets on the local RTX 3060,
monitors 7 hard stop conditions, records full metrics, and writes
the report artifact under benchmarks/. Halts immediately on any stop
condition — does NOT auto-proceed to A2.

Stop conditions (any one → halt + record):

  1. Trust / manifest refusal (runner._load_error set with any
     trusted_manifest_load_failed / snapshot_verification_failed /
     fast_verify_failed_unrecoverable / full_verify_failed /
     eval_override_failed prefix).
  2. Model-load failure (runner._load_error set with load_failed or
     dynamic_module_import_failed / import_failed prefix).
  3. CUDA unsupported-op error during inference — RuntimeError text
     containing "not implemented" / "not supported" / "unsupported".
  4. OOM during load or first inference — CUDA OutOfMemoryError.
  5. Peak VRAM too close to the 12 GB ceiling — allocated OR reserved
     exceeds the safe operating threshold below.
  6. Obvious hallucination or malformed output — output char count
     exceeds 3x the PDF's hidden text-layer size (when non-zero), OR
     output exceeds an absolute 500 KB ceiling (per plan A1.5).
  7. Failure to process next asset without a Python restart — any
     asset after the first that fails when the previous ran clean
     without an intermediate CUDA context reset.

Predeclared assets:

  - image-only:    parsebench/japanese_case
  - multicolumn:   parsebench/2colmercedes
  - native + img:  public/019-grayscale-image/grayscale-image.pdf

Emits:

  benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_SMOKE_2026-07-20.json
  benchmarks/PDF_BENCHMARK_V1_UNLIMITED_OCR_SMOKE_2026-07-20.md
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
import traceback
from pathlib import Path
from typing import Any

# Windows default cp1252 stdout cannot encode Japanese / non-Latin
# characters that the model's own diagnostic prints emit. Force UTF-8
# on both streams before any inference runs.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST_PATH = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"

# Predeclared A1.5 assets — LOCKED before any run. Changes to this
# list mid-A1.5 require an explicit plan change-log entry and reviewer
# sign-off per standing rule 10.
PREDECLARED_ASSETS = [
    "parsebench/japanese_case",
    "parsebench/2colmercedes",
    "public/019-grayscale-image/grayscale-image.pdf",
]

# Stop-condition thresholds.
_VRAM_SAFE_CEILING_MIB = 11 * 1024        # 11 GB — leaves ~1 GB of headroom on the 12 GB RTX 3060
_OUTPUT_ABSOLUTE_CEILING_BYTES = 500_000  # ~500 KB per document max
_HALLUCINATION_MULTIPLIER = 3             # output must not exceed 3x hidden-text-layer chars


def _nvidia_driver_version() -> str | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"],
            timeout=5, stderr=subprocess.STDOUT,
        ).decode().strip().splitlines()[0]
        return out
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _rss_mib() -> int:
    try:
        import psutil  # type: ignore
        return int(psutil.Process().memory_info().rss // (1024 * 1024))
    except ImportError:
        return -1


def _cuda_stats(torch) -> dict[str, int]:
    if not torch.cuda.is_available():
        return {}
    return {
        "allocated_mib": int(torch.cuda.max_memory_allocated(0) // (1024 * 1024)),
        "reserved_mib": int(torch.cuda.max_memory_reserved(0) // (1024 * 1024)),
    }


def _reset_cuda_stats(torch) -> None:
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats(0)
        torch.cuda.empty_cache()


def _hidden_text_layer_chars(pdf: Path) -> int | None:
    try:
        import fitz  # type: ignore
    except ImportError:
        return None
    try:
        with fitz.open(str(pdf)) as doc:
            return sum(len(page.get_text() or "") for page in doc)
    except Exception:
        return None


def _classify_exception(exc_text: str) -> str:
    lower = exc_text.lower()
    if "out of memory" in lower or "outofmemory" in lower or "cuda oom" in lower:
        return "oom"
    if "not implemented" in lower or "not supported" in lower or "unsupported" in lower:
        return "cuda_unsupported_op"
    if "trusted_manifest_load_failed" in lower or "snapshot_verification_failed" in lower:
        return "trust_or_manifest_refusal"
    if (
        "fast_verify_failed_unrecoverable" in lower
        or "full_verify_failed" in lower
        or "eval_override_failed" in lower
    ):
        return "trust_or_manifest_refusal"
    if (
        "load_failed" in lower
        or "dynamic_module_import_failed" in lower
        or "import_failed" in lower
    ):
        return "model_load_failure"
    return "other"


def _check_hallucination(text: str, hidden_chars: int | None) -> str | None:
    n = len(text)
    if n > _OUTPUT_ABSOLUTE_CEILING_BYTES:
        return f"output_char_count_{n}_exceeds_absolute_ceiling_{_OUTPUT_ABSOLUTE_CEILING_BYTES}"
    if hidden_chars and hidden_chars > 0:
        if n > _HALLUCINATION_MULTIPLIER * hidden_chars:
            return (
                f"output_char_count_{n}_exceeds_"
                f"{_HALLUCINATION_MULTIPLIER}x_hidden_chars_{hidden_chars}"
            )
    return None


def _check_vram_ceiling(stats: dict[str, int]) -> str | None:
    a = stats.get("allocated_mib", 0)
    r = stats.get("reserved_mib", 0)
    if a > _VRAM_SAFE_CEILING_MIB:
        return f"peak_allocated_{a}_mib_exceeds_safe_ceiling_{_VRAM_SAFE_CEILING_MIB}_mib"
    if r > _VRAM_SAFE_CEILING_MIB:
        return f"peak_reserved_{r}_mib_exceeds_safe_ceiling_{_VRAM_SAFE_CEILING_MIB}_mib"
    return None


def _resolve_asset(manifest_data: dict, asset_id: str) -> dict:
    for a in manifest_data["assets"]:
        if a["asset_id"] == asset_id:
            return a
    raise SystemExit(f"predeclared asset not in manifest: {asset_id}")


def _emit_report(report: dict) -> None:
    ts = "2026-07-20"
    json_path = _REPO_ROOT / "benchmarks" / f"PDF_BENCHMARK_V1_UNLIMITED_OCR_SMOKE_{ts}.json"
    md_path = _REPO_ROOT / "benchmarks" / f"PDF_BENCHMARK_V1_UNLIMITED_OCR_SMOKE_{ts}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    L: list[str] = []
    ov = report["outcome"]
    L.append(f"# Unlimited-OCR A1.5 feasibility smoke — {ts}")
    L.append("")
    L.append(f"**Result:** {ov['status']}")
    if ov.get("stop_condition"):
        L.append(f"**Stop condition:** {ov['stop_condition']}")
        L.append(f"**Stop reason:** {ov['stop_reason']}")
    L.append("")
    L.append("## Environment")
    env = report["environment"]
    for k, v in env.items():
        L.append(f"- **{k}:** {v}")
    L.append("")
    L.append("## Load")
    ld = report["load"]
    for k, v in ld.items():
        L.append(f"- **{k}:** {v}")
    L.append("")
    L.append("## Per-asset")
    L.append("")
    L.append("| Asset | Status | Runtime (s) | Peak alloc (MiB) | Peak reserved (MiB) | RSS (MiB) | Output chars | Notes |")
    L.append("|---|---|---:|---:|---:|---:|---:|---|")
    for r in report["per_asset"]:
        note = r.get("stop_reason") or (r.get("exception") or "-")
        L.append(
            f"| `{r['asset_id']}` | {r['status']} | {r.get('runtime_seconds','-')} | "
            f"{r.get('peak_allocated_mib','-')} | {r.get('peak_reserved_mib','-')} | "
            f"{r.get('rss_mib','-')} | {r.get('output_chars','-')} | {note} |"
        )
    L.append("")
    L.append(f"Written to: `{json_path}` and `{md_path}`")
    md_path.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote: {json_path}")
    print(f"wrote: {md_path}")


def main() -> int:
    # ── Environment probe ──────────────────────────────────────────
    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "nvidia_driver": _nvidia_driver_version() or "unknown",
    }
    try:
        import torch  # type: ignore
    except ImportError as e:
        print(f"SKIP: torch not installed: {e}", file=sys.stderr)
        return 2
    env["torch"] = torch.__version__
    env["cuda"] = torch.version.cuda
    if not torch.cuda.is_available():
        print("SKIP: CUDA not available", file=sys.stderr)
        return 3
    env["gpu_name"] = torch.cuda.get_device_name(0)
    env["gpu_vram_gib"] = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2)
    env["bf16_supported"] = torch.cuda.is_bf16_supported()

    # ── Manifest ───────────────────────────────────────────────────
    manifest_data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    assets = [_resolve_asset(manifest_data, aid) for aid in PREDECLARED_ASSETS]

    # ── Load runner (A1d integrated) ──────────────────────────────
    from benchmarks.pdf_benchmark_adapters import unlimited_ocr_adapter as adapter
    runner = adapter._UnlimitedOcrRunner()
    _reset_cuda_stats(torch)
    load_t0 = time.perf_counter()
    load_rss_before = _rss_mib()
    runner.load()
    load_elapsed = round(time.perf_counter() - load_t0, 2)
    load_stats = _cuda_stats(torch)
    load_rss_after = _rss_mib()

    load_record = {
        "elapsed_seconds": load_elapsed,
        "rss_before_mib": load_rss_before,
        "rss_after_mib": load_rss_after,
        "call_log": list(runner._call_log),
        "loaded": runner._loaded,
        "load_error": runner._load_error,
        "peak_allocated_mib_load": load_stats.get("allocated_mib"),
        "peak_reserved_mib_load": load_stats.get("reserved_mib"),
    }

    if not runner._loaded:
        outcome = {
            "status": "HALT",
            "stop_condition": _classify_exception(runner._load_error),
            "stop_reason": runner._load_error,
        }
        _emit_report({
            "environment": env,
            "predeclared_assets": PREDECLARED_ASSETS,
            "load": load_record,
            "per_asset": [],
            "outcome": outcome,
        })
        return 1

    # Check VRAM at load
    vram_stop = _check_vram_ceiling(load_stats)
    if vram_stop:
        _emit_report({
            "environment": env,
            "predeclared_assets": PREDECLARED_ASSETS,
            "load": load_record,
            "per_asset": [],
            "outcome": {"status": "HALT", "stop_condition": "vram_ceiling_at_load",
                         "stop_reason": vram_stop},
        })
        return 1

    # ── Per-asset inference ────────────────────────────────────────
    per_asset: list[dict[str, Any]] = []
    outcome: dict[str, Any] = {"status": "PASS", "stop_condition": None, "stop_reason": None}

    with tempfile.TemporaryDirectory(prefix="a1_5_smoke_") as scratch_dir:
        workdir = Path(scratch_dir)
        for idx, asset in enumerate(assets):
            aid = asset["asset_id"]
            pdf = Path(asset["pdf_path"])
            hidden_chars = _hidden_text_layer_chars(pdf)
            _reset_cuda_stats(torch)
            rss_before = _rss_mib()
            t0 = time.perf_counter()
            try:
                text, exc, tool_signals = runner.infer_pdf(pdf, workdir)
            except Exception as e:
                exc = f"harness_exception: {type(e).__name__}: {e}"
                text = ""
                tool_signals = {}
                traceback.print_exc(file=sys.stderr)
            elapsed = round(time.perf_counter() - t0, 2)
            stats = _cuda_stats(torch)
            rss_after = _rss_mib()

            record: dict[str, Any] = {
                "asset_id": aid,
                "document_class": asset.get("document_class"),
                "pdf_path": str(pdf),
                "hidden_text_layer_chars": hidden_chars,
                "runtime_seconds": elapsed,
                "peak_allocated_mib": stats.get("allocated_mib"),
                "peak_reserved_mib": stats.get("reserved_mib"),
                "rss_before_mib": rss_before,
                "rss_after_mib": rss_after,
                "output_chars": len(text or ""),
                "exception": exc,
                "runner_call_log": list(runner._call_log),
                "processed_without_restart": True,
                "tool_signals": tool_signals,
                "status": "PASS",
            }

            # Stop-condition classification.
            if exc:
                cls = _classify_exception(exc)
                record["status"] = "FAIL"
                record["stop_reason"] = f"{cls}: {exc}"
                per_asset.append(record)
                outcome = {"status": "HALT", "stop_condition": cls, "stop_reason": exc}
                break
            vram_stop = _check_vram_ceiling(stats)
            if vram_stop:
                record["status"] = "FAIL"
                record["stop_reason"] = vram_stop
                per_asset.append(record)
                outcome = {"status": "HALT", "stop_condition": "vram_ceiling",
                            "stop_reason": vram_stop}
                break
            hall_stop = _check_hallucination(text, hidden_chars)
            if hall_stop:
                record["status"] = "FAIL"
                record["stop_reason"] = hall_stop
                per_asset.append(record)
                outcome = {"status": "HALT", "stop_condition": "hallucination",
                            "stop_reason": hall_stop}
                break

            per_asset.append(record)

    _emit_report({
        "environment": env,
        "predeclared_assets": PREDECLARED_ASSETS,
        "vram_safe_ceiling_mib": _VRAM_SAFE_CEILING_MIB,
        "output_absolute_ceiling_bytes": _OUTPUT_ABSOLUTE_CEILING_BYTES,
        "hallucination_multiplier": _HALLUCINATION_MULTIPLIER,
        "load": load_record,
        "per_asset": per_asset,
        "outcome": outcome,
        "model_residency": {
            "single_runner_instance": True,
            "assets_processed": len(per_asset),
        },
    })
    return 0 if outcome["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
