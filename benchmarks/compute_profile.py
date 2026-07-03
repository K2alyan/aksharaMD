#!/usr/bin/env python3
"""
benchmarks/compute_profile.py

Self-hosted / open-source model throughput analysis.

Reads per-document token counts from benchmark_results/llm_qa_results.json
and computes three metrics that matter when you run your own models:

  1. KV-cache footprint per request — two model families (8B and 70B class)
  2. Maximum concurrent requests at realistic VRAM budgets
  3. Prefill time-to-first-token (TTFT) speedup — O(n²) attention scaling

No API keys needed. All calculations are deterministic from token counts.

Usage:
    # Read from the default results file
    python -m benchmarks.compute_profile

    # Read from a specific results file
    python -m benchmarks.compute_profile --results path/to/llm_qa_results.json

    # Emit the markdown section for LLM_QA_BENCHMARK.md
    python -m benchmarks.compute_profile --markdown
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

# ── Model KV-cache parameters ──────────────────────────────────────────────────
# KV cache bytes per input token = 2 (K+V) × layers × kv_heads × head_dim × dtype_bytes
# Using fp16 KV cache (most frameworks default; some support fp8/int8 KV cache separately
# from weight quantization — conservatively use fp16 here).
#
# Architecture sources:
#   Llama 3 8B:  https://huggingface.co/meta-llama/Meta-Llama-3-8B
#   Llama 3 70B: https://huggingface.co/meta-llama/Meta-Llama-3-70B
#   Mistral 7B and Qwen2.5 7B share the same GQA config as Llama 3 8B.

_MODEL_FAMILIES: dict[str, dict] = {
    "8B class\n(Llama 3 8B / Mistral 7B / Qwen2.5 7B)": {
        "layers": 32,
        "kv_heads": 8,   # GQA: 8 KV heads, 32 query heads
        "head_dim": 128,
        "kv_dtype_bytes": 2,   # fp16
    },
    "70B class\n(Llama 3 70B / Qwen2.5 72B)": {
        "layers": 80,
        "kv_heads": 8,   # GQA: 8 KV heads, 64 query heads
        "head_dim": 128,
        "kv_dtype_bytes": 2,   # fp16
    },
}


def _kv_bytes_per_token(cfg: dict) -> int:
    """KV cache bytes consumed per input token for a given model config."""
    return 2 * cfg["layers"] * cfg["kv_heads"] * cfg["head_dim"] * cfg["kv_dtype_bytes"]


# ── Realistic deployment VRAM scenarios ────────────────────────────────────────
# "KV budget" = total GPU VRAM minus model weight VRAM minus ~2 GB framework overhead.
# Weight VRAM estimates use common quantization levels:
#   8B  fp16 ≈ 16 GB  |  8B  int4 ≈ 4.5 GB
#   70B fp16 ≈ 140 GB |  70B int8 ≈ 70 GB  |  70B int4 ≈ 37 GB

_VRAM_SCENARIOS: list[dict] = [
    {
        "label": "8B int4 · RTX 4090 (24 GB)",
        "model_family": "8B class\n(Llama 3 8B / Mistral 7B / Qwen2.5 7B)",
        "kv_budget_gb": 19.0,   # 24 − 4.5 (int4 weights) − 0.5 (overhead)
        "note": "Consumer GPU, most affordable self-hosted option",
    },
    {
        "label": "8B fp16 · A100 40 GB",
        "model_family": "8B class\n(Llama 3 8B / Mistral 7B / Qwen2.5 7B)",
        "kv_budget_gb": 22.0,   # 40 − 16 (fp16 weights) − 2 (overhead)
        "note": "Full-precision small model on datacenter GPU",
    },
    {
        "label": "70B int4 · A100 80 GB",
        "model_family": "70B class\n(Llama 3 70B / Qwen2.5 72B)",
        "kv_budget_gb": 41.0,   # 80 − 37 (int4 weights) − 2 (overhead)
        "note": "Standard large-model production config",
    },
    {
        "label": "70B int4 · H100 80 GB",
        "model_family": "70B class\n(Llama 3 70B / Qwen2.5 72B)",
        "kv_budget_gb": 41.0,   # same model sizing, faster HBM3
        "note": "Same KV budget as A100 80 GB, faster memory bandwidth",
    },
]

# ── Hardcoded fallback token counts (from the July 2026 benchmark run) ─────────
# Used when no results JSON is available (e.g., CI without API keys).
_FALLBACK_AVG_TOKENS: dict[str, float] = {
    "aksharamd":   6_114,
    "markitdown":  34_909,
    "llamaparse":  35_322,
    "pymupdf4llm": 46_523,
    "docling":     46_765,
}

_TOOL_LABELS: dict[str, str] = {
    "aksharamd":   "AksharaMD",
    "markitdown":  "MarkItDown",
    "llamaparse":  "LlamaParse",
    "pymupdf4llm": "PyMuPDF4LLM",
    "docling":     "Docling",
}


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_avg_tokens(results_path: Path) -> dict[str, float]:
    """Return {tool: avg_tokens} from a benchmark results JSON file."""
    if not results_path.exists():
        return _FALLBACK_AVG_TOKENS.copy()

    with results_path.open(encoding="utf-8") as f:
        data = json.load(f)

    totals: dict[str, list[float]] = defaultdict(list)
    for row in data.get("results", []):
        tool = row.get("tool", "")
        tokens = row.get("doc_tokens")
        if tool and tokens is not None:
            totals[tool].append(float(tokens))

    if not totals:
        return _FALLBACK_AVG_TOKENS.copy()

    return {tool: sum(vals) / len(vals) for tool, vals in totals.items()}


# ── Metric calculations ────────────────────────────────────────────────────────

def _kv_gb(tokens: float, cfg: dict) -> float:
    """KV cache GB consumed by a single request with the given token count."""
    return tokens * _kv_bytes_per_token(cfg) / (1024 ** 3)


def _max_concurrent(tokens: float, cfg: dict, budget_gb: float) -> int:
    """Max simultaneous in-flight requests given a KV-cache VRAM budget."""
    request_gb = _kv_gb(tokens, cfg)
    if request_gb <= 0:
        return 0
    return max(1, int(budget_gb / request_gb))


def _prefill_ttft_ratio(baseline_tokens: float, other_tokens: float) -> float:
    """
    Relative TTFT for 'other' vs 'baseline', based on O(n²) attention FLOPs.
    A ratio of 32 means the other tool's first token takes 32× longer to arrive.
    Flash Attention reduces memory from O(n²) to O(n) but FLOPs remain O(n²).
    """
    if baseline_tokens <= 0:
        return 0.0
    return (other_tokens / baseline_tokens) ** 2


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _bar(value: float, max_value: float, width: int = 20) -> str:
    filled = round(value / max_value * width) if max_value > 0 else 0
    return "█" * filled + "░" * (width - filled)


def _col(s: str, width: int) -> str:
    return str(s).ljust(width)[:width]


def _rcol(s: str, width: int) -> str:
    return str(s).rjust(width)[:width]


# ── Report sections ────────────────────────────────────────────────────────────

def _section_kv_cache(avg_tokens: dict[str, float]) -> str:
    lines: list[str] = []
    lines.append("KV-CACHE FOOTPRINT PER REQUEST")
    lines.append("(fp16 KV cache; lower is better)")
    lines.append("")

    tools = sorted(avg_tokens, key=lambda t: avg_tokens[t])

    for family_name, cfg in _MODEL_FAMILIES.items():
        label = family_name.split("\n")[0]
        bpt = _kv_bytes_per_token(cfg)
        lines.append(f"  {label}  ({bpt // 1024} KB per token)")
        lines.append(f"  {'Tool':<16}  {'Avg tokens':>12}  {'KV / request':>14}  {'vs AksharaMD':>14}")
        lines.append(f"  {'─'*16}  {'─'*12}  {'─'*14}  {'─'*14}")

        baseline = avg_tokens.get("aksharamd", 1)
        for tool in tools:
            n = avg_tokens[tool]
            kv = _kv_gb(n, cfg)
            ratio = kv / _kv_gb(baseline, cfg)
            marker = " ◀ baseline" if tool == "aksharamd" else f"  {ratio:.1f}×"
            label_str = _TOOL_LABELS.get(tool, tool)
            lines.append(
                f"  {label_str:<16}  {n:>12,.0f}  {kv:>12.2f} GB  {marker}"
            )
        lines.append("")

    return "\n".join(lines)


def _section_concurrent(avg_tokens: dict[str, float]) -> str:
    lines: list[str] = []
    lines.append("MAX CONCURRENT REQUESTS AT FIXED VRAM BUDGET")
    lines.append("(more is better; limited by KV-cache VRAM)")
    lines.append("")

    tools = sorted(avg_tokens, key=lambda t: avg_tokens[t])
    tool_labels = [_TOOL_LABELS.get(t, t) for t in tools]

    header = _col("Deployment scenario", 32) + "  " + "  ".join(_rcol(lbl, 12) for lbl in tool_labels)
    lines.append("  " + header)
    lines.append("  " + "─" * len(header))

    for scenario in _VRAM_SCENARIOS:
        model_key = scenario["model_family"]
        cfg = _MODEL_FAMILIES[model_key]
        budget = scenario["kv_budget_gb"]
        row = _col(scenario["label"], 32) + "  "
        row += "  ".join(
            _rcol(str(_max_concurrent(avg_tokens[t], cfg, budget)), 12)
            for t in tools
        )
        lines.append("  " + row)

    lines.append("")
    lines.append("  Throughput multiplier (AksharaMD concurrent / competitor concurrent):")
    baseline = avg_tokens.get("aksharamd", 1.0)
    for scenario in _VRAM_SCENARIOS[:2]:   # show for the two 8B scenarios
        cfg = _MODEL_FAMILIES[scenario["model_family"]]
        budget = scenario["kv_budget_gb"]
        base_c = _max_concurrent(baseline, cfg, budget)
        parts = []
        for t in tools:
            if t == "aksharamd":
                continue
            c = _max_concurrent(avg_tokens[t], cfg, budget)
            if c > 0:
                parts.append(f"{_TOOL_LABELS.get(t,t)}: {base_c/c:.1f}×")
        lines.append(f"  {scenario['label']}: " + ", ".join(parts))

    lines.append("")
    return "\n".join(lines)


def _section_ttft(avg_tokens: dict[str, float]) -> str:
    lines: list[str] = []
    lines.append("PREFILL TIME-TO-FIRST-TOKEN (TTFT) — relative to AksharaMD")
    lines.append("(based on O(n²) attention FLOPs; lower is better for competitors)")
    lines.append("")

    baseline = avg_tokens.get("aksharamd", 1.0)
    max_ratio = max(
        _prefill_ttft_ratio(baseline, n)
        for t, n in avg_tokens.items() if t != "aksharamd"
    )

    lines.append(f"  {'Tool':<16}  {'Avg tokens':>12}  {'TTFT ratio':>12}  Relative cost")
    lines.append(f"  {'─'*16}  {'─'*12}  {'─'*12}  {'─'*20}")

    for tool in sorted(avg_tokens, key=lambda t: avg_tokens[t]):
        n = avg_tokens[tool]
        ratio = _prefill_ttft_ratio(baseline, n)
        bar = _bar(ratio, max_ratio, 20) if tool != "aksharamd" else _bar(1, max_ratio, 20)
        label = _TOOL_LABELS.get(tool, tool)
        ratio_str = "1.0× (baseline)" if tool == "aksharamd" else f"{ratio:.1f}×"
        lines.append(f"  {label:<16}  {n:>12,.0f}  {ratio_str:>12}  {bar}")

    lines.append("")
    lines.append("  Example: if AksharaMD TTFT = 0.3 s, MarkItDown TTFT ≈ "
                 f"{0.3 * _prefill_ttft_ratio(baseline, avg_tokens.get('markitdown', baseline)):.0f} s")
    lines.append("  (Actual numbers depend on GPU, batch size, and attention implementation.)")
    lines.append("")
    return "\n".join(lines)


def _section_docs_per_gpu_hour(avg_tokens: dict[str, float]) -> str:
    lines: list[str] = []
    lines.append("RELATIVE DOCS / GPU-HOUR  (AksharaMD = 1.0)")
    lines.append("(prefill-dominated workload; assumes output ≤ 200 tokens)")
    lines.append("")

    baseline = avg_tokens.get("aksharamd", 1.0)

    lines.append(f"  {'Tool':<16}  {'Relative throughput':>20}  {'Docs/GPU-hr if AksharaMD=1000':>30}")
    lines.append(f"  {'─'*16}  {'─'*20}  {'─'*30}")

    for tool in sorted(avg_tokens, key=lambda t: avg_tokens[t]):
        n = avg_tokens[tool]
        # Throughput ∝ 1 / prefill_FLOPs ∝ 1 / n²
        rel = (baseline / n) ** 2
        docs_if_base_1000 = round(rel * 1000)
        label = _TOOL_LABELS.get(tool, tool)
        rel_str = "1.00 (baseline)" if tool == "aksharamd" else f"{rel:.4f}"
        lines.append(f"  {label:<16}  {rel_str:>20}  {docs_if_base_1000:>30,}")

    lines.append("")
    lines.append("  Note: this metric assumes prefill dominates total GPU time, which holds")
    lines.append("  for document QA and extraction. For long-form generation (>500 output")
    lines.append("  tokens), the decode phase reduces — but does not eliminate — this gap.")
    lines.append("")
    return "\n".join(lines)


# ── Markdown output ─────────────────────────────────────────────────────────────

def _to_markdown(avg_tokens: dict[str, float]) -> str:
    tools = sorted(avg_tokens, key=lambda t: avg_tokens[t])
    tool_labels = [_TOOL_LABELS.get(t, t) for t in tools]
    baseline = avg_tokens.get("aksharamd", 1.0)

    md: list[str] = []
    md.append("## Self-Hosted Model Impact")
    md.append("")
    md.append("API cost only tells part of the story. When you run your own models, token count")
    md.append("drives three hardware costs: **KV-cache VRAM** (limits how many requests you can")
    md.append("serve in parallel), **prefill compute** (determines time-to-first-token), and")
    md.append("**effective GPU throughput** (documents processed per GPU-hour).")
    md.append("")

    # ── KV cache table ──
    md.append("### KV-cache footprint per request")
    md.append("")
    md.append("KV-cache size scales linearly with token count and determines maximum batch size.")
    md.append("Values below use fp16 KV cache (framework default for most vLLM / SGLang deployments).")
    md.append("")

    for family_name, cfg in _MODEL_FAMILIES.items():
        short_name = family_name.split("\n")[0]
        bpt = _kv_bytes_per_token(cfg)
        md.append(f"**{short_name}** ({bpt // 1024} KB per token, fp16 KV)")
        md.append("")
        header = "| Tool | Avg tokens | KV / request | vs AksharaMD |"
        sep    = "|------|:----------:|:------------:|:------------:|"
        md.append(header)
        md.append(sep)
        for tool in tools:
            n = avg_tokens[tool]
            kv = _kv_gb(n, cfg)
            if tool == "aksharamd":
                vs = "— (baseline)"
                row_fmt = f"| **{_TOOL_LABELS[tool]}** | **{n:,.0f}** | **{kv:.2f} GB** | **{vs}** |"
            else:
                ratio = kv / _kv_gb(baseline, cfg)
                vs = f"{ratio:.1f}×"
                row_fmt = f"| {_TOOL_LABELS.get(tool, tool)} | {n:,.0f} | {kv:.2f} GB | {vs} |"
            md.append(row_fmt)
        md.append("")

    # ── Concurrent requests table ──
    md.append("### Maximum concurrent requests at fixed VRAM budgets")
    md.append("")
    md.append("Concurrent capacity = available KV-cache VRAM ÷ KV-cache per request.")
    md.append("\"Available\" = total GPU VRAM minus model weight VRAM minus ~2 GB overhead.")
    md.append("")

    header_cols = ["Deployment scenario"] + [_TOOL_LABELS.get(t, t) for t in tools]
    md.append("| " + " | ".join(header_cols) + " |")
    sep_cols = ["---"] + [":---:" for _ in tools]
    md.append("| " + " | ".join(sep_cols) + " |")

    for scenario in _VRAM_SCENARIOS:
        cfg = _MODEL_FAMILIES[scenario["model_family"]]
        budget = scenario["kv_budget_gb"]
        row_vals = []
        for t in tools:
            c = _max_concurrent(avg_tokens[t], cfg, budget)
            if t == "aksharamd":
                row_vals.append(f" **{c}**")
            else:
                row_vals.append(f" {c}")
        md.append("| " + scenario["label"] + " |" + " |".join(row_vals) + " |")

    md.append("")
    md.append("Throughput multiplier (AksharaMD ÷ next-best at each tier):")
    md.append("")
    for scenario in _VRAM_SCENARIOS:
        cfg = _MODEL_FAMILIES[scenario["model_family"]]
        budget = scenario["kv_budget_gb"]
        base_c = _max_concurrent(baseline, cfg, budget)
        others = {
            t: _max_concurrent(avg_tokens[t], cfg, budget)
            for t in tools if t != "aksharamd"
        }
        best_other = max(others.values()) if others else 1
        mult = base_c / max(best_other, 1)
        md.append(f"- **{scenario['label']}**: AksharaMD serves **{mult:.1f}× more requests** than the next-best tool")

    md.append("")

    # ── TTFT table ──
    md.append("### Prefill time-to-first-token (TTFT)")
    md.append("")
    md.append("Self-attention in the prefill phase is O(n²) in FLOPs. Flash Attention reduces")
    md.append("memory from O(n²) to O(n) but the compute cost remains quadratic. A document")
    md.append("with 5.7× more tokens takes **32×** longer to prefill.")
    md.append("")
    md.append("| Tool | Avg tokens | TTFT ratio vs AksharaMD |")
    md.append("|------|:----------:|:-----------------------:|")
    for tool in tools:
        n = avg_tokens[tool]
        ratio = _prefill_ttft_ratio(baseline, n)
        label = _TOOL_LABELS.get(tool, tool)
        if tool == "aksharamd":
            md.append(f"| **{label}** | **{n:,.0f}** | **1× (baseline)** |")
        else:
            md.append(f"| {label} | {n:,.0f} | {ratio:.1f}× slower |")
    md.append("")
    md.append("*TTFT ratios are theoretical upper bounds based on attention FLOPs. Actual numbers*")
    md.append("*depend on GPU, batch size, Flash Attention version, and sequence-packing strategy.*")
    md.append("")

    # ── Docs per GPU-hour ──
    md.append("### Relative docs per GPU-hour")
    md.append("")
    md.append("For prefill-dominated workloads (document QA, extraction, classification),")
    md.append("GPU throughput scales as 1/n². Normalized to AksharaMD = 1,000 docs/GPU-hr:")
    md.append("")
    md.append("| Tool | Relative throughput | Docs/GPU-hr (if AksharaMD = 1,000) |")
    md.append("|------|:-------------------:|:-----------------------------------:|")
    for tool in tools:
        n = avg_tokens[tool]
        rel = (baseline / n) ** 2
        docs = round(rel * 1000)
        label = _TOOL_LABELS.get(tool, tool)
        if tool == "aksharamd":
            md.append(f"| **{label}** | **1.00** | **1,000** |")
        else:
            md.append(f"| {label} | {rel:.4f} | {docs:,} |")
    md.append("")
    md.append("> For long-form generation (summarisation, rewriting), the decode phase reduces")
    md.append("> but does not eliminate this gap — the prefill advantage holds for any input-heavy workload.")
    md.append("")

    # ── Summary ──
    md.append("### What this means in practice")
    md.append("")
    # Compute the best-case numbers for the summary
    md_tokens = avg_tokens.get("markitdown", 34909)
    pym_tokens = avg_tokens.get("pymupdf4llm", 46523)
    md_ratio = _prefill_ttft_ratio(baseline, md_tokens)
    pym_ratio = _prefill_ttft_ratio(baseline, pym_tokens)
    cfg_8b = _MODEL_FAMILIES["8B class\n(Llama 3 8B / Mistral 7B / Qwen2.5 7B)"]
    s0 = _VRAM_SCENARIOS[0]
    base_c0 = _max_concurrent(baseline, cfg_8b, s0["kv_budget_gb"])
    md_c0 = _max_concurrent(md_tokens, cfg_8b, s0["kv_budget_gb"])

    md.append(
        f"Running a Llama 3 8B model on an RTX 4090 with MarkItDown context, "
        f"you can serve **{md_c0} concurrent user** at a time before VRAM is exhausted. "
        f"With AksharaMD context, the same GPU serves **{base_c0} concurrent users** — "
        f"a **{base_c0 // max(md_c0,1)}× throughput increase with no hardware change**."
    )
    md.append("")
    md.append(
        f"Time-to-first-token on MarkItDown's average context is **{md_ratio:.0f}× longer** "
        f"than on AksharaMD's. For interactive applications where users wait for a response, "
        "this is the difference between a 0.3-second and a 10-second wait."
    )
    md.append("")

    return "\n".join(md)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute self-hosted model throughput metrics from benchmark token counts."
    )
    parser.add_argument(
        "--results",
        default="benchmark_results/llm_qa_results.json",
        help="Path to llm_qa_results.json (default: benchmark_results/llm_qa_results.json)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Emit the benchmark section as Markdown instead of the console report",
    )
    args = parser.parse_args()

    results_path = Path(args.results)
    avg_tokens = _load_avg_tokens(results_path)

    source = str(results_path) if results_path.exists() else "hardcoded benchmark defaults"
    if not args.markdown:
        print(f"\nAksharaMD — Self-Hosted Model Compute Profile")
        print(f"Source: {source}")
        print(f"Tools: {', '.join(_TOOL_LABELS.get(t, t) for t in sorted(avg_tokens))}")
        print("=" * 72)
        print()
        print(_section_kv_cache(avg_tokens))
        print(_section_concurrent(avg_tokens))
        print(_section_ttft(avg_tokens))
        print(_section_docs_per_gpu_hour(avg_tokens))
    else:
        print(_to_markdown(avg_tokens))


if __name__ == "__main__":
    main()
