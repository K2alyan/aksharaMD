"""
Compile a local file to Markdown and print key stats.

Usage:
    python examples/01_compile_file.py path/to/document.pdf
    python examples/01_compile_file.py path/to/report.docx
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure Unicode output works on all platforms (including Windows cp1252 consoles)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aksharamd.compiler import Compiler


def main(source: str) -> None:
    compiler = Compiler(output_dir="output")
    text, ctx = compiler.compile_to_string(source)

    m = ctx.manifest
    print(f"Source:        {m.source}")
    print(f"Format:        {m.file_type}")
    print(f"Pages:         {m.pages}")
    print(f"Tables:        {m.tables}")
    print(f"Chunks:        {m.chunks}")
    print(f"Tokens:        {m.optimized_tokens:,}  ({m.token_reduction_percent:.1f}% reduction)")
    print(f"Readiness:     {m.readiness_score}/100")
    print(f"Time:          {m.elapsed_seconds:.2f}s")

    if ctx.validation.errors:
        print("\nValidation errors:")
        for e in ctx.validation.errors:
            print(f"  [{e.code}] {e.message}")

    print("\n--- Compiled Markdown (first 2 000 chars) ---\n")
    print(text[:2000])
    if len(text) > 2000:
        print(f"\n... ({len(text) - 2000:,} more characters)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python 01_compile_file.py <path>")
        sys.exit(1)
    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)
    main(path)
