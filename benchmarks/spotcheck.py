#!/usr/bin/env python3
"""
Manual spot-check evaluator: verifies ground-truth facts survive document conversion.

Usage:
    python -m benchmarks.spotcheck --facts benchmarks/spotcheck_facts.yaml
    python -m benchmarks.spotcheck --facts benchmarks/spotcheck_facts.yaml --tools aksharamd markitdown
    python -m benchmarks.spotcheck --facts benchmarks/spotcheck_facts.yaml --llm
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import yaml

# Windows cp1252 terminal chokes on Unicode in fact text — force UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from benchmarks.corpus_benchmark import (
    _run_aksharamd,
    _run_markitdown,
    _run_docling,
    _run_naive,
)

RUNNERS = {
    "aksharamd":  _run_aksharamd,
    "markitdown": _run_markitdown,
    "docling":    _run_docling,
    "naive":      _run_naive,
}

DEFAULT_TOOLS = ["aksharamd", "markitdown", "docling"]
RESULTS_PATH = Path("benchmark_results/spotcheck_results.json")


# ── fact checking ─────────────────────────────────────────────────────────────

def _check_substring(text: str, fact: dict) -> bool:
    t = text.lower()
    keywords = fact.get("keywords")
    if keywords:
        return all(k.lower() in t for k in keywords)
    return fact["text"].lower() in t


def _check_llm(text: str, fact: dict) -> bool:
    import anthropic
    client = anthropic.Anthropic()
    prompt = (
        "You are a document fact checker. Given a document excerpt and a fact, "
        "determine if the fact is preserved.\n\n"
        f"Fact: {fact['text']}\n\n"
        f"Document excerpt (first 4000 chars):\n{text[:4000]}\n\n"
        "Answer with exactly one word: PRESERVED or LOST"
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    answer = msg.content[0].text.strip().upper()
    return answer.startswith("PRESERVED")


# ── ASCII table ───────────────────────────────────────────────────────────────

def _print_table(doc_path: str, facts: list[dict], tools: list[str],
                 results: dict[str, list[bool | None]]) -> None:
    fact_col = 42
    tool_col = 12
    header = f"{'Fact':<{fact_col}}" + "".join(f"{t[:tool_col-2]:^{tool_col}}" for t in tools)
    sep = "-" * len(header)
    print(f"\n  {doc_path}")
    print("  " + sep)
    print("  " + header)
    print("  " + sep)
    for i, fact in enumerate(facts):
        label = fact["text"][:fact_col - 1]
        row = f"{label:<{fact_col}}"
        for tool in tools:
            r = results[tool][i]
            cell = " OK" if r is True else (" -- " if r is False else " ERR")
            row += f"{cell:^{tool_col}}"
        print("  " + row)
    print("  " + sep)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Spot-check fact preservation across conversion tools")
    parser.add_argument("--facts", required=True, help="Path to YAML facts file")
    parser.add_argument("--tools", nargs="+", default=DEFAULT_TOOLS,
                        choices=list(RUNNERS), help="Tools to run")
    parser.add_argument("--llm", action="store_true",
                        help="Use Claude as judge (requires ANTHROPIC_API_KEY)")
    args = parser.parse_args()

    if args.llm:
        try:
            import anthropic as _a; _a.Anthropic()
        except Exception as _e:
            print(f"ERROR: --llm requires Anthropic API access: {_e}", file=sys.stderr)
            sys.exit(1)

    facts_path = Path(args.facts)
    if not facts_path.exists():
        print(f"ERROR: facts file not found: {facts_path}", file=sys.stderr)
        sys.exit(1)

    with open(facts_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    documents = config.get("documents", [])
    if not documents:
        print("No documents found in facts file.", file=sys.stderr)
        sys.exit(1)

    all_results = []
    tool_totals: dict[str, int] = {t: 0 for t in args.tools}
    tool_hits:   dict[str, int] = {t: 0 for t in args.tools}

    for doc_entry in documents:
        doc_path = Path(doc_entry["path"])
        if not doc_path.exists():
            print(f"\n  SKIP (file not found): {doc_path}")
            continue

        facts = doc_entry.get("facts", [])
        if not facts:
            continue

        # Normalise facts to dicts
        facts = [{"text": f} if isinstance(f, str) else f for f in facts]

        # Convert with each tool
        tool_texts: dict[str, str] = {}
        for tool in args.tools:
            runner = RUNNERS[tool]
            try:
                text, _ = runner(doc_path)
                tool_texts[tool] = text
            except Exception:
                tool_texts[tool] = ""

        # Check each fact per tool
        results: dict[str, list[bool | None]] = {t: [] for t in args.tools}
        check_fn = _check_llm if args.llm else _check_substring

        for fact in facts:
            for tool in args.tools:
                text = tool_texts[tool]
                if not text:
                    results[tool].append(None)
                    continue
                try:
                    hit = check_fn(text, fact)
                    results[tool].append(hit)
                    tool_totals[tool] += 1
                    if hit:
                        tool_hits[tool] += 1
                except Exception:
                    results[tool].append(None)
                    tool_totals[tool] += 1

        _print_table(str(doc_path), facts, args.tools, results)

        # Collect for JSON output
        all_results.append({
            "document": str(doc_path),
            "description": doc_entry.get("description", ""),
            "facts": [
                {
                    "text": f["text"],
                    "results": {t: results[t][i] for t in args.tools},
                }
                for i, f in enumerate(facts)
            ],
        })

    # Summary
    print("\n  Overall scores:")
    for tool in args.tools:
        hits = tool_hits[tool]
        total = tool_totals[tool]
        pct = 100 * hits / total if total else 0
        print(f"    {tool:<20} {hits}/{total}  ({pct:.0f}%)")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "mode": "llm" if args.llm else "substring",
        "tools": args.tools,
        "documents": all_results,
        "summary": {t: {"hits": tool_hits[t], "total": tool_totals[t]} for t in args.tools},
    }, indent=2))
    print(f"\n  Results saved → {RESULTS_PATH}")


if __name__ == "__main__":
    main()
