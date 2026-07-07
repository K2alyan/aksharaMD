#!/usr/bin/env python3
"""Multi-tool comparison benchmark on the public reproducible corpus.

Runs AksharaMD, MarkItDown, and Docling on every file in the public corpus
and records token counts, success rate, and elapsed time per tool.

This produces reproducible comparison numbers against the committed corpus.

Usage:
    python benchmarks/run_comparison_benchmark.py [--smoke] [--skip-pdf]
    python benchmarks/run_comparison_benchmark.py --tools aksharamd,markitdown
    python benchmarks/run_comparison_benchmark.py --smoke --skip-docling

Run build_public_corpus.py first to populate .public_corpus/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BENCHMARKS = Path(__file__).parent
MANIFEST_PATH = BENCHMARKS / "public_corpus_manifest.json"

sys.path.insert(0, str(REPO_ROOT))

# ── Tool format support ───────────────────────────────────────────────────────
# MarkItDown 0.1.x: PDF, DOCX, PPTX, XLSX, HTML, CSV, JSON, XML, TXT, MD, ZIP
# Docling: PDF, DOCX, PPTX, XLSX, HTML (no CSV, JSON, XML, TXT, MD, ZIP)

MARKITDOWN_FORMATS = {"pdf", "docx", "pptx", "xlsx", "html", "csv", "json", "xml", "txt", "md", "zip"}
DOCLING_FORMATS = {"pdf", "docx", "pptx", "xlsx", "html"}


def _load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _build_entries(manifest: dict, smoke: bool = False) -> list[dict]:
    from benchmarks.run_public_benchmark import _build_entries
    return _build_entries(manifest, smoke=smoke)


def _count_tokens(text: str) -> int:
    return max(0, len(text) // 4)


# ── Per-tool runners ──────────────────────────────────────────────────────────

def _run_aksharamd(file_path: Path) -> dict:
    from aksharamd.compiler import Compiler
    t0 = time.monotonic()
    try:
        ctx = Compiler(output_dir=str(BENCHMARKS / ".public_corpus" / "_out")).compile(str(file_path))
        elapsed = time.monotonic() - t0
        if ctx.document is None:
            errs = [{"code": e.code, "message": e.message} for e in ctx.validation.errors]
            return {"outcome": "error", "tokens": 0, "chars": 0, "elapsed": round(elapsed, 3), "errors": errs}
        text = "\n".join(b.content for b in ctx.document.blocks if b.content)
        return {
            "outcome": "success",
            "tokens": _count_tokens(text),
            "chars": len(text),
            "elapsed": round(elapsed, 3),
            "blocks": len(ctx.document.blocks),
            "errors": [],
        }
    except Exception as exc:
        return {"outcome": "exception", "tokens": 0, "chars": 0, "elapsed": round(time.monotonic() - t0, 3), "exception": str(exc)}


def _run_markitdown(file_path: Path) -> dict:
    try:
        from markitdown import MarkItDown
    except ImportError:
        return {"outcome": "not_installed", "tokens": 0, "chars": 0, "elapsed": 0}
    t0 = time.monotonic()
    try:
        md = MarkItDown()
        result = md.convert(str(file_path))
        elapsed = time.monotonic() - t0
        text = result.text_content or ""
        return {"outcome": "success", "tokens": _count_tokens(text), "chars": len(text), "elapsed": round(elapsed, 3)}
    except Exception as exc:
        return {"outcome": "exception", "tokens": 0, "chars": 0, "elapsed": round(time.monotonic() - t0, 3), "exception": str(exc)}


def _run_docling(file_path: Path) -> dict:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return {"outcome": "not_installed", "tokens": 0, "chars": 0, "elapsed": 0}
    t0 = time.monotonic()
    try:
        converter = DocumentConverter()
        result = converter.convert(str(file_path))
        elapsed = time.monotonic() - t0
        text = result.document.export_to_markdown() if result.document else ""
        return {"outcome": "success", "tokens": _count_tokens(text), "chars": len(text), "elapsed": round(elapsed, 3)}
    except Exception as exc:
        return {"outcome": "exception", "tokens": 0, "chars": 0, "elapsed": round(time.monotonic() - t0, 3), "exception": str(exc)}


_TOOL_RUNNERS = {
    "aksharamd": _run_aksharamd,
    "markitdown": _run_markitdown,
    "docling": _run_docling,
}

_TOOL_FORMAT_SUPPORT: dict[str, set[str]] = {
    "aksharamd": set(),  # empty = all formats
    "markitdown": MARKITDOWN_FORMATS,
    "docling": DOCLING_FORMATS,
}


def _tool_supports(tool: str, fmt: str) -> bool:
    supported = _TOOL_FORMAT_SUPPORT[tool]
    return not supported or fmt in supported


def _run_one(corpus_root: Path, entry: dict, tools: list[str]) -> dict:
    file_path = corpus_root / entry["local_path"]
    record: dict = {
        "id": entry["id"],
        "label": entry["label"],
        "format": entry["format"],
        "source": entry["source"],
        "file_exists": file_path.exists(),
    }
    if not file_path.exists():
        for tool in tools:
            record[tool] = {"outcome": "skipped", "tokens": 0, "chars": 0, "elapsed": 0}
        return record

    for tool in tools:
        if not _tool_supports(tool, entry["format"]):
            record[tool] = {"outcome": "unsupported", "tokens": 0, "chars": 0, "elapsed": 0}
        else:
            record[tool] = _TOOL_RUNNERS[tool](file_path)

    return record


def _write_results(records: list[dict], tools: list[str], path: Path, elapsed_total: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_summary(records: list[dict], tools: list[str], path: Path, elapsed_total: float) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# AksharaMD Multi-Tool Comparison Benchmark",
        "",
        f"**Generated:** {ts}  ",
        f"**Total files:** {len(records)}  ",
        f"**Elapsed:** {elapsed_total:.1f}s  ",
        f"**Tools:** {', '.join(tools)}  ",
        "",
        "## Overall Summary",
        "",
    ]

    # Overall stats per tool
    header = "| Metric | " + " | ".join(t.capitalize() for t in tools) + " |"
    sep = "| --- |" + " --- |" * len(tools)
    lines += [header, sep]

    def stat_row(label: str, vals: list[str]) -> str:
        return f"| {label} | " + " | ".join(vals) + " |"

    successes = {t: sum(1 for r in records if r.get(t, {}).get("outcome") == "success") for t in tools}
    total = len(records)
    lines.append(stat_row("Files attempted", [str(total)] * len(tools)))
    lines.append(stat_row("Succeeded", [str(successes[t]) for t in tools]))
    lines.append(stat_row("Success rate", [f"{successes[t]/total*100:.0f}%" for t in tools]))

    # Token stats (success only)
    for t in tools:
        tok = [r[t]["tokens"] for r in records if r.get(t, {}).get("outcome") == "success"]
        if tok:
            pass  # computed below in table

    tok_rows = []
    for t in tools:
        tok = [r[t]["tokens"] for r in records if r.get(t, {}).get("outcome") == "success"]
        tok_rows.append(f"{int(sum(tok)/len(tok)):,}" if tok else "-")
    lines.append(stat_row("Avg tokens (success)", tok_rows))

    char_rows = []
    for t in tools:
        chars = [r[t]["chars"] for r in records if r.get(t, {}).get("outcome") == "success"]
        char_rows.append(f"{int(sum(chars)/len(chars)):,}" if chars else "-")
    lines.append(stat_row("Avg chars (success)", char_rows))

    elapsed_rows = []
    for t in tools:
        els = [r[t]["elapsed"] for r in records if r.get(t, {}).get("outcome") in ("success", "error", "exception")]
        elapsed_rows.append(f"{sum(els)/len(els):.2f}s" if els else "-")
    lines.append(stat_row("Avg elapsed", elapsed_rows))
    lines.append("")

    # Token ratio vs AksharaMD (if aksharamd is one of the tools)
    if "aksharamd" in tools and len(tools) > 1:
        lines += ["## Token Efficiency vs AksharaMD", ""]
        lines += [
            "| Format | " + " | ".join(t.capitalize() + " tokens" for t in tools) + " | Ratio (AksharaMD÷other) |",
            "| --- |" + " --- |" * len(tools) + " --- |",
        ]
        formats = sorted({r["format"] for r in records})
        for fmt in formats:
            fmt_records = [r for r in records if r["format"] == fmt]
            aksha_tok = [r["aksharamd"]["tokens"] for r in fmt_records if r.get("aksharamd", {}).get("outcome") == "success"]
            avg_aksha = sum(aksha_tok) / len(aksha_tok) if aksha_tok else None

            row_parts = [fmt]
            ratios = []
            for t in tools:
                tok = [r[t]["tokens"] for r in fmt_records if r.get(t, {}).get("outcome") == "success"]
                avg = sum(tok) / len(tok) if tok else None
                row_parts.append(f"{int(avg):,}" if avg is not None else "-")
                if t != "aksharamd" and avg_aksha is not None and avg is not None and avg > 0:
                    ratios.append(f"{avg/avg_aksha:.1f}×")
            row_parts.append(", ".join(ratios) if ratios else "-")
            lines.append("| " + " | ".join(row_parts) + " |")
        lines.append("")

    # Per-file results
    lines += ["## Per-File Results", ""]
    col_header = "| ID | Format | " + " | ".join(f"{t.capitalize()} tokens" for t in tools) + " | " + " | ".join(f"{t.capitalize()} ok?" for t in tools) + " |"
    col_sep = "| --- | --- |" + " --- |" * len(tools) + " --- |" * len(tools)
    lines += [col_header, col_sep]

    for r in records:
        tok_cols = []
        ok_cols = []
        for t in tools:
            tr = r.get(t, {})
            outcome = tr.get("outcome", "?")
            tok = tr.get("tokens", 0)
            tok_cols.append(f"{tok:,}" if outcome == "success" else f"— ({outcome})")
            ok_cols.append("✓" if outcome == "success" else outcome)
        lines.append("| " + r["id"] + " | " + r["format"] + " | " + " | ".join(tok_cols) + " | " + " | ".join(ok_cols) + " |")
    lines.append("")

    # Failures
    failures = []
    for r in records:
        for t in tools:
            tr = r.get(t, {})
            if tr.get("outcome") in ("error", "exception"):
                failures.append((r["id"], r["format"], t, tr))
    if failures:
        lines += ["## Failures", ""]
        for fid, fmt, tool, tr in failures:
            lines.append(f"**{fid}** ({fmt}) — {tool}: {tr.get('outcome')}")
            if tr.get("exception"):
                lines.append(f"  - {tr['exception'][:200]}")
            for e in tr.get("errors", []):
                lines.append(f"  - `{e['code']}`: {e['message'][:200]}")
        lines.append("")

    lines += [
        "## Reproducibility",
        "",
        "```",
        "python benchmarks/build_public_corpus.py",
        "python benchmarks/run_comparison_benchmark.py",
        "```",
        "",
        "PDF corpus: https://github.com/py-pdf/sample-files (CC-BY-SA-4.0).",
        "Synthetic files generated locally with no external dependencies.",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    tools: list[str],
    smoke: bool = False,
    output_dir: Path | None = None,
) -> list[dict]:
    manifest = _load_manifest()
    corpus_root = BENCHMARKS / manifest["corpus_dir"]
    results_dir = output_dir or (BENCHMARKS / "results")

    entries = _build_entries(manifest, smoke=smoke)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    jsonl_path = results_dir / f"comparison_benchmark_{ts}.jsonl"
    md_path = results_dir / f"comparison_benchmark_{ts}.md"
    mode_label = "smoke" if smoke else "full"

    print(f"Corpus:  {corpus_root}")
    print(f"Mode:    {mode_label} ({len(entries)} files)")
    print(f"Tools:   {', '.join(tools)}")
    print(f"Results: {jsonl_path}")
    print()

    records: list[dict] = []
    t_start = time.monotonic()

    for entry in entries:
        file_path = corpus_root / entry["local_path"]
        exists = file_path.exists()
        print(f"[{entry['id']}] {entry['label']} ({entry['format']}) {'...' if exists else '— MISSING'}", end=" ", flush=True)

        record = _run_one(corpus_root, entry, tools)
        records.append(record)

        parts = []
        for t in tools:
            tr = record.get(t, {})
            outcome = tr.get("outcome", "?")
            if outcome == "success":
                parts.append(f"{t}={tr['tokens']:,}tok")
            elif outcome == "unsupported":
                parts.append(f"{t}=n/a")
            elif outcome == "skipped":
                parts.append(f"{t}=skipped")
            else:
                parts.append(f"{t}=FAIL({outcome})")
        print("  ".join(parts))

    elapsed_total = time.monotonic() - t_start

    _write_results(records, tools, jsonl_path, elapsed_total)
    _write_summary(records, tools, md_path, elapsed_total)

    print(f"\nTotal: {len(records)} files in {elapsed_total:.1f}s")
    print(f"JSONL: {jsonl_path}")
    print(f"MD:    {md_path}")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true", help="20-file smoke subset")
    mode.add_argument("--full", action="store_true", help="All 134 files (default)")
    parser.add_argument(
        "--tools", default="aksharamd,markitdown,docling",
        help="Comma-separated list of tools to run (default: aksharamd,markitdown,docling)"
    )
    parser.add_argument("--skip-docling", action="store_true", help="Exclude Docling (faster; Docling is slow on PDFs)")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    tools = [t.strip() for t in args.tools.split(",")]
    if args.skip_docling and "docling" in tools:
        tools.remove("docling")

    unknown = set(tools) - set(_TOOL_RUNNERS)
    if unknown:
        print(f"Unknown tools: {unknown}. Valid: {list(_TOOL_RUNNERS)}")
        sys.exit(1)

    run(tools=tools, smoke=args.smoke, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
