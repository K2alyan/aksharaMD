"""
Compile a list of files and print a comparison table.

Useful for benchmarking extraction quality across a document corpus,
or for validating a directory of files before ingestion.

Usage:
    python examples/03_batch_compile.py doc1.pdf doc2.docx doc3.html
    python examples/03_batch_compile.py docs/*.pdf
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aksharamd.compiler import Compiler


def main(paths: list[str]) -> None:
    compiler = Compiler(output_dir="output")

    results = []
    total_original = 0
    total_optimized = 0

    print(f"Compiling {len(paths)} file(s)...\n")

    for path in paths:
        if not Path(path).exists():
            print(f"  SKIP  {path}  (not found)")
            continue

        t0 = time.perf_counter()
        _, ctx = compiler.compile_to_string(path)
        elapsed = time.perf_counter() - t0

        m = ctx.manifest
        status = "ERROR" if ctx.validation.errors else "OK"
        results.append({
            "file": Path(path).name,
            "type": m.file_type or "?",
            "tokens": m.optimized_tokens,
            "reduction": m.token_reduction_percent,
            "score": m.readiness_score,
            "time": elapsed,
            "status": status,
        })
        total_original += m.original_tokens or 0
        total_optimized += m.optimized_tokens or 0

    if not results:
        print("No files compiled.")
        return

    col_w = max(len(r["file"]) for r in results) + 2
    header = (
        f"{'File':<{col_w}} {'Type':<7} {'Tokens':>8} {'Reduced':>8} "
        f"{'Score':>6} {'Time':>6}  Status"
    )
    print(header)
    print("-" * len(header))

    for r in results:
        reduction_str = f"{r['reduction']:.1f}%" if r["reduction"] else "n/a"
        print(
            f"{r['file']:<{col_w}} {r['type']:<7} {r['tokens']:>8,} "
            f"{reduction_str:>8} {r['score']:>6} {r['time']:>5.2f}s  {r['status']}"
        )

    if total_original > 0:
        overall_reduction = (1 - total_optimized / total_original) * 100
        print(f"\nTotal tokens: {total_optimized:,} (down from {total_original:,}, "
              f"{overall_reduction:.1f}% reduction)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python 03_batch_compile.py <file1> [file2 ...]")
        sys.exit(1)
    main(sys.argv[1:])
