"""
Fetch a URL and compile the result to Markdown.

Works with HTML pages, PDF links, and any URL whose Content-Type maps
to a registered AksharaMD parser.

Usage:
    python examples/02_compile_url.py https://example.com/paper.pdf
    python examples/02_compile_url.py https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aksharamd.compiler import Compiler


def main(url: str) -> None:
    print(f"Fetching and compiling: {url}\n")

    compiler = Compiler(output_dir="output")
    text, ctx = compiler.compile_to_string(url)

    if ctx.validation.errors:
        print("Errors during compilation:")
        for e in ctx.validation.errors:
            print(f"  [{e.code}] {e.message}")
        return

    m = ctx.manifest
    print(f"Format:    {m.file_type}")
    print(f"Tokens:    {m.optimized_tokens:,}  ({m.token_reduction_percent:.1f}% reduction)")
    print(f"Readiness: {m.readiness_score}/100")
    print(f"Time:      {m.elapsed_seconds:.2f}s\n")

    print("--- Compiled Markdown ---\n")
    print(text[:3000])
    if len(text) > 3000:
        print(f"\n... ({len(text) - 3000:,} more characters)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python 02_compile_url.py <url>")
        sys.exit(1)
    main(sys.argv[1])
