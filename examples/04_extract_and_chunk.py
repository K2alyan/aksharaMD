"""
Access the structured block model and semantic chunks directly.

Shows how to work with the CompilationContext returned by compile_to_string,
including iterating over typed blocks and chunks for downstream processing.

Usage:
    python examples/04_extract_and_chunk.py path/to/document.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aksharamd.compiler import Compiler
from aksharamd.models.block import BlockType


def main(source: str) -> None:
    compiler = Compiler(output_dir="output")
    _, ctx = compiler.compile_to_string(source)

    if ctx.document is None:
        print("Compilation failed:")
        for e in ctx.validation.errors:
            print(f"  [{e.code}] {e.message}")
        return

    doc = ctx.document
    blocks = doc.blocks
    chunks = ctx.chunks or []

    # ── Block-level inspection ──────────────────────────────────────────────────

    print(f"Document: {doc.title or Path(source).name}")
    print(f"Author:   {doc.author or 'unknown'}")
    print(f"Format:   {doc.file_type}  |  {doc.pages} page(s)\n")

    type_counts: dict[str, int] = {}
    for b in blocks:
        type_counts[b.type.value] = type_counts.get(b.type.value, 0) + 1

    print("Block composition:")
    for btype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {btype:<20} {count}")

    print()

    headings = [b for b in blocks if b.type == BlockType.HEADING]
    if headings:
        print(f"Document outline ({len(headings)} heading(s)):")
        for h in headings[:20]:
            indent = "  " * (h.level - 1) if h.level else ""
            level_str = f"H{h.level}" if h.level else "H?"
            print(f"  {indent}[{level_str}] {h.content[:80]}")
        if len(headings) > 20:
            print(f"  ... ({len(headings) - 20} more headings)")

    print()

    tables = [b for b in blocks if b.type == BlockType.TABLE]
    if tables:
        print(f"Tables ({len(tables)}):")
        for i, t in enumerate(tables[:3], 1):
            preview = t.content[:120].replace("\n", " ")
            print(f"  [{i}] {preview}...")
        print()

    # ── Chunk-level inspection ──────────────────────────────────────────────────

    if chunks:
        print(f"Semantic chunks ({len(chunks)}):")
        for i, chunk in enumerate(chunks[:5], 1):
            heading = chunk.heading or ""
            preview = chunk.content[:100].replace("\n", " ")
            label = f"  [{i}] {heading[:40]!r}" if heading else f"  [{i}]"
            print(f"{label}  ({chunk.token_count} tokens)")
            if preview:
                print(f"       {preview}...")
        if len(chunks) > 5:
            print(f"  ... ({len(chunks) - 5} more chunks)")
    else:
        print("No chunks produced.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python 04_extract_and_chunk.py <path>")
        sys.exit(1)
    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)
    main(path)
