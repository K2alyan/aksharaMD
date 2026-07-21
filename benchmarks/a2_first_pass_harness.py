"""A2 first pass — Unlimited-OCR against all 45 eligible assets.

Per the reviewer's Phase A2 directive:

- UTF-8 stdout by default.
- Per-page runtime + page count per row.
- Atomic checkpoint after every asset (interruption leaves a valid
  resumable artifact; resume skips completed assets).
- 20-minute (1200 s) soft per-asset timeout — records as
  ``TIMEOUT`` in the result, does NOT silently retry.
- No threshold changes.
- Records: status, failure_category, elapsed_time, page_count,
  seconds_per_page, peak_allocated_mib, peak_reserved_mib,
  output_size, runner_healthy_after (via a 1-element CUDA alloc
  probe after each call).

After the 45-asset first pass this harness STOPS. The deterministic
second pass is a separate invocation (``--second-pass``) gated on
reviewer approval of the first-pass artifact.
"""
from __future__ import annotations

import argparse
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

# UTF-8 stdout by default (Windows cp1252 would truncate model text).
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST_PATH = _REPO_ROOT / "benchmarks" / "pdf_benchmark_v1_manifest.json"
_ARTIFACT_JSON = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.json"
_ARTIFACT_MD = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_2026-07-20.md"
_CHECKPOINT_JSON = _REPO_ROOT / "benchmarks" / "PDF_BENCHMARK_V1_UNLIMITED_OCR_FIRST_PASS.checkpoint.json"

DEFAULT_TIMEOUT_S = 1200  # 20 min per asset — accommodates the 583 s Japanese case
DEFAULT_OUTPUT_CEILING = 500_000  # hallucination flag: > 500 KB output
HALLUCINATION_MULTIPLIER = 3


# ── Utilities ───────────────────────────────────────────────────────────


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


def _pdf_page_count(pdf: Path) -> int | None:
    try:
        import fitz  # type: ignore
    except ImportError:
        return None
    try:
        with fitz.open(str(pdf)) as doc:
            return doc.page_count
    except Exception:
        return None


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


def _runner_health_probe(torch) -> bool:
    """Tiny CUDA allocation to prove the context still works after an
    inference. Returns False if CUDA raises — indicates a poisoned
    context that should stop subsequent inference."""
    if not torch.cuda.is_available():
        return False
    try:
        x = torch.zeros(1, device="cuda")
        _ = x.sum().item()
        del x
        torch.cuda.empty_cache()
        return True
    except Exception:
        return False


def _classify_failure(exc_text: str, timeout_hit: bool) -> str:
    if timeout_hit:
        return "timeout"
    lower = exc_text.lower()
    if not lower:
        return "success"
    if "out of memory" in lower or "outofmemory" in lower or "cuda oom" in lower:
        return "oom"
    if "not implemented" in lower or "not supported" in lower or "unsupported" in lower:
        return "cuda_unsupported_op"
    if "harness_exception" in lower:
        return "harness_exception"
    return "other_exception"


def _hallucination_flag(text: str, hidden_chars: int | None) -> str | None:
    n = len(text)
    if n > DEFAULT_OUTPUT_CEILING:
        return f"output_{n}_exceeds_{DEFAULT_OUTPUT_CEILING}"
    if hidden_chars and hidden_chars > 0 and n > HALLUCINATION_MULTIPLIER * hidden_chars:
        return f"output_{n}_gt_{HALLUCINATION_MULTIPLIER}x_hidden_{hidden_chars}"
    return None


# ── Atomic checkpoint I/O ───────────────────────────────────────────────


def _write_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, default=str) + "\n"
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        # Best-effort tempfile cleanup on write failure; ignore secondary
        # errors from the unlink itself so we can re-raise the original.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_checkpoint() -> dict:
    if not _CHECKPOINT_JSON.exists():
        return {"rows": [], "pass_index": 1, "started_at": None, "meta": {}}
    return json.loads(_CHECKPOINT_JSON.read_text(encoding="utf-8"))


# ── Environment probe ───────────────────────────────────────────────────


def _capture_env() -> dict[str, Any]:
    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "nvidia_driver": _nvidia_driver_version() or "unknown",
    }
    try:
        import torch  # type: ignore
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


# ── Per-asset execution ─────────────────────────────────────────────────


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
        "elapsed_seconds": elapsed,
        "seconds_per_page": (
            round(elapsed / page_count, 3) if page_count and page_count > 0 else None
        ),
        "peak_allocated_mib": stats.get("allocated_mib"),
        "peak_reserved_mib": stats.get("reserved_mib"),
        "rss_before_mib": rss_before,
        "rss_after_mib": rss_after,
        "output_chars": len(text or ""),
        "exception": exc,
        "hallucination_flag": hallucination,
        "runner_healthy_after": healthy_after,
        "tool_signals": tool_signals,
        "timeout_hit": timeout_hit,
    }


# ── Aggregation for the MD report ───────────────────────────────────────


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    sv = sorted(values)
    if p >= 100:
        return sv[-1]
    idx = int(round((p / 100.0) * (len(sv) - 1)))
    return sv[idx]


def _emit_report(payload: dict) -> None:
    _write_atomic(_ARTIFACT_JSON, payload)
    rows = payload["per_asset"]
    L: list[str] = []
    ov = payload["aggregate"]["overall"]
    L.append("# PDF Benchmark v1 — Unlimited-OCR (first pass, 2026-07-20)")
    L.append("")
    L.append(f"**Assets attempted:** {ov['n']}  ·  **PASS:** {ov['pass_count']}  ·  **FAIL:** {ov['fail_count']}")
    L.append(f"**Timeouts:** {ov['timeout_count']}  ·  **OOM:** {ov['oom_count']}  ·  **Other exceptions:** {ov['other_exception_count']}")
    L.append(f"**Hallucination flags:** {ov['hallucination_count']}")
    L.append("")
    L.append("## Runtime (per document, from the reporting-revision plan update)")
    L.append("")
    L.append(f"- Median elapsed: {ov['runtime_p50']} s")
    L.append(f"- p95 elapsed: {ov['runtime_p95']} s")
    L.append(f"- Median s/page: {ov['s_per_page_p50']}")
    L.append(f"- p95 s/page: {ov['s_per_page_p95']}")
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
    L.append("## Slowest five assets")
    L.append("")
    slow = sorted(rows, key=lambda r: r.get("elapsed_seconds") or 0, reverse=True)[:5]
    L.append("| Asset | Class | Pages | Elapsed (s) | s/page | Peak reserved MiB | Status |")
    L.append("|---|---|---:|---:|---:|---:|---|")
    for r in slow:
        L.append(
            f"| `{r['asset_id']}` | {r['document_class']} | {r.get('page_count','-')} | "
            f"{r.get('elapsed_seconds','-')} | {r.get('seconds_per_page','-')} | "
            f"{r.get('peak_reserved_mib','-')} | {r['status']} |"
        )
    L.append("")
    L.append("## Failures")
    L.append("")
    fails = [r for r in rows if r["status"] == "FAIL"]
    if not fails:
        L.append("_None._")
    else:
        L.append("| Asset | Category | Elapsed (s) | Runner healthy after | Exception |")
        L.append("|---|---|---:|:-:|---|")
        for r in fails:
            L.append(
                f"| `{r['asset_id']}` | {r['failure_category']} | {r.get('elapsed_seconds','-')} | "
                f"{'yes' if r.get('runner_healthy_after') else 'NO'} | {(r.get('exception') or '')[:100]} |"
            )
    L.append("")
    L.append("## Cold load")
    L.append("")
    ld = payload["load"]
    for k, v in ld.items():
        if k != "call_log":
            L.append(f"- **{k}:** {v}")
    L.append("")
    _ARTIFACT_MD.write_text("\n".join(L), encoding="utf-8")


def _compute_aggregate(rows: list[dict]) -> dict:
    def _bucket(subset: list[dict]) -> dict:
        n = len(subset)
        elapsed = [r["elapsed_seconds"] for r in subset if r.get("elapsed_seconds") is not None]
        spp = [r["seconds_per_page"] for r in subset if r.get("seconds_per_page") is not None]
        return {
            "n": n,
            "pass_count": sum(1 for r in subset if r["status"] == "PASS"),
            "fail_count": sum(1 for r in subset if r["status"] == "FAIL"),
            "timeout_count": sum(1 for r in subset if r.get("failure_category") == "timeout"),
            "oom_count": sum(1 for r in subset if r.get("failure_category") == "oom"),
            "other_exception_count": sum(
                1 for r in subset
                if r.get("failure_category") in ("cuda_unsupported_op", "harness_exception", "other_exception")
            ),
            "hallucination_count": sum(1 for r in subset if r.get("hallucination_flag")),
            "runtime_p50": _percentile(elapsed, 50),
            "runtime_p95": _percentile(elapsed, 95),
            "s_per_page_p50": _percentile(spp, 50),
            "s_per_page_p95": _percentile(spp, 95),
        }
    overall = _bucket(rows)
    classes = sorted({r.get("document_class", "unknown") for r in rows})
    by_class = {c: _bucket([r for r in rows if r.get("document_class") == c]) for c in classes}
    return {"overall": overall, "by_class": by_class}


# ── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S,
                    help="Soft per-asset timeout in seconds (default 1200)")
    ap.add_argument("--force", action="store_true",
                    help="Ignore existing checkpoint and re-run all assets")
    args = ap.parse_args()

    manifest_data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    eligible = sorted(
        [a for a in manifest_data["assets"] if a["eligibility"] == "eligible"],
        key=lambda a: a["asset_id"],
    )
    print(f"eligible assets: {len(eligible)}", file=sys.stderr)

    # Resume from checkpoint if present.
    checkpoint = {"rows": [], "pass_index": 1, "started_at": None, "meta": {}}
    if not args.force:
        checkpoint = _load_checkpoint()
    completed = {r["asset_id"] for r in checkpoint["rows"]}
    print(f"resuming with {len(completed)} assets already completed", file=sys.stderr)

    env = _capture_env()

    # Load runner ONCE. Cold-load metrics captured separately from
    # per-asset runtime.
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
        # runner._load_error is a plain diagnostic string (e.g., "trusted_
        # manifest_load_failed: ..."), NOT a credential. CodeQL's taint
        # tracker false-positives here.
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
        "harness_version": "a2_first_pass_harness.py@2026-07-20",
        "timeout_s": args.timeout_s,
        "environment": env,
        "load": load_record,
    }

    # Per-asset loop with atomic checkpoint after every asset.
    remaining = [a for a in eligible if a["asset_id"] not in completed]
    print(f"remaining to run: {len(remaining)}", file=sys.stderr)
    healthy = True
    with tempfile.TemporaryDirectory(prefix="a2_first_pass_") as workdir_str:
        workdir = Path(workdir_str)
        for i, asset in enumerate(remaining, start=1):
            aid = asset["asset_id"]
            print(f"[{i}/{len(remaining)}] {aid} ...", file=sys.stderr, flush=True)
            row = _run_one_asset(runner, asset, torch, workdir, args.timeout_s)
            checkpoint["rows"].append(row)
            _write_atomic(_CHECKPOINT_JSON, checkpoint)
            healthy = row.get("runner_healthy_after", False)
            print(
                f"  -> {row['status']} cat={row['failure_category']} "
                f"elapsed={row['elapsed_seconds']}s pages={row.get('page_count')} "
                f"reserved={row.get('peak_reserved_mib')} MiB healthy={healthy}",
                file=sys.stderr, flush=True,
            )
            if not healthy:
                print(
                    "WARN: runner health probe failed after this asset — halting first pass "
                    "so subsequent results are not tainted by a poisoned CUDA context.",
                    file=sys.stderr,
                )
                break

    # Final report. Includes schema fields required by
    # tests/test_pdf_benchmark_unlimited_ocr.py so the artifact stays
    # compatible with the pre-existing schema assertions.
    all_rows = checkpoint["rows"]
    # Backfill per-row execution_mode for the schema check.
    for row in all_rows:
        row.setdefault("execution_mode", "real_inference")
    aggregate = _compute_aggregate(all_rows)
    payload = {
        "harness_version": checkpoint["meta"]["harness_version"],
        "adapter_target": "unlimited-ocr",
        "adapter_target_repo": adapter._UNLIMITED_OCR_MODEL_REPO,
        "adapter_target_revision": adapter._UNLIMITED_OCR_MODEL_REVISION,
        "manifest_source": _MANIFEST_PATH.name,
        "pass_index": 1,
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
        "execution_mode_decision": {
            "mode": "real_inference",
            "note": "A2 first pass; all assets attempted real inference",
        },
        "dependencies": {
            "torch": env.get("torch"),
            "cuda": env.get("cuda"),
        },
        "evaluation_semantics_notes": {
            "aksharamd_readiness_score_used": False,
            "aksharamd_warning_codes_used": False,
            "no_cross_parser_ranking": True,
            "near_empty_equivalent_definition": "not applied in A2; tool-neutral output only",
            "low_density_equivalent_definition": "not applied in A2; tool-neutral output only",
        },
        "security_notes": {
            "trust_remote_code": True,
            "revision_pinned": adapter._UNLIMITED_OCR_MODEL_REVISION is not None,
            "safetensors_only": True,
            "offline_enforcement": {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
            "trusted_code_files_verified": True,
        },
        "load": load_record,
        "timeout_s": args.timeout_s,
        "per_asset": all_rows,
        "aggregate": aggregate,
        "runner_healthy_at_end": healthy,
        "stopped_early_due_to_health_probe_failure": not healthy,
    }
    _emit_report(payload)
    print(f"wrote: {_ARTIFACT_JSON}", file=sys.stderr)
    print(f"wrote: {_ARTIFACT_MD}", file=sys.stderr)

    ov = aggregate["overall"]
    print(
        f"SUMMARY: n={ov['n']} pass={ov['pass_count']} fail={ov['fail_count']} "
        f"timeouts={ov['timeout_count']} oom={ov['oom_count']} "
        f"p50={ov['runtime_p50']}s p95={ov['runtime_p95']}s",
        file=sys.stderr,
    )
    return 0 if ov["fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
