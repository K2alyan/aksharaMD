#!/usr/bin/env python3
"""
Full benchmark runner: build corpus (if needed) then compare tools.

Usage:
  python -m benchmarks.run_full_benchmark
  python -m benchmarks.run_full_benchmark --skip-build --corpus-dir ../Downloads/benchmark_corpus_v5
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description="Build corpus + run benchmark in one shot")
    p.add_argument("--corpus-dir", default="../Downloads/benchmark_corpus_v5")
    p.add_argument("--output-dir", default="benchmark_results/corpus_v5")
    p.add_argument("--count", type=int, default=20, help="Files per type for corpus build")
    p.add_argument("--skip-build", action="store_true", help="Skip corpus build, use existing")
    p.add_argument("--tools", nargs="*",
                   default=["omnimark", "markitdown", "docling"],
                   help="Tools to benchmark")
    p.add_argument("--types", nargs="*", default=None, help="Limit to these file types")
    args = p.parse_args()

    corpus = Path(args.corpus_dir)
    out = Path(args.output_dir)

    # ── Step 1: build corpus ───────────────────────────────────────────────────
    if not args.skip_build:
        print(f"\n{'='*60}")
        print("STEP 1: Build benchmark corpus")
        print(f"{'='*60}")
        cmd = [
            sys.executable, "-m", "benchmarks.corpus_builder",
            "--output-dir", str(corpus),
            "--count", str(args.count),
        ]
        t0 = time.perf_counter()
        ret = subprocess.run(cmd).returncode
        elapsed = time.perf_counter() - t0
        if ret != 0:
            print(f"\nCorpus build failed (exit {ret})")
            return ret
        print(f"\nCorpus built in {elapsed/60:.1f} min")
    else:
        print(f"\nSkipping corpus build, using: {corpus}")

    # ── Step 2: run benchmark ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("STEP 2: Run benchmark")
    print(f"{'='*60}")
    cmd = [
        sys.executable, "-m", "benchmarks.corpus_benchmark",
        "--corpus-dir", str(corpus),
        "--output-dir", str(out),
        "--tools", *args.tools,
    ]
    if args.types:
        cmd += ["--types", *args.types]

    t0 = time.perf_counter()
    ret = subprocess.run(cmd).returncode
    elapsed = time.perf_counter() - t0
    print(f"\nBenchmark completed in {elapsed/60:.1f} min")
    print(f"Results: {out.resolve()}")
    return ret


if __name__ == "__main__":
    raise SystemExit(main())
