#!/usr/bin/env python3
"""Run the AksharaMD public reproducible benchmark.

Compiles every file in the public corpus and records parser success/failure,
block counts, output size, and readiness score.

Usage:
    python benchmarks/run_public_benchmark.py [options]

Modes:
    (default / --full)   All 34 PDFs + 10 variants per format = 134 files
    --smoke              10 smoke PDFs + 1 variant per format = 20 files
    --max-pdfs N         Run at most N PDF files (after smoke filtering)

Output (benchmarks/results/):
    public_benchmark_<timestamp>.jsonl   — one JSON record per file
    public_benchmark_<timestamp>.md      — human-readable summary table

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


def _load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _build_entries(
    manifest: dict,
    smoke: bool = False,
    max_pdfs: int | None = None,
) -> list[dict]:
    """Construct the flat list of entries to benchmark from the v2 manifest."""
    entries: list[dict] = []

    # ── PDF entries ────────────────────────────────────────────────────────────
    pdf_corpus = manifest["pdf_corpus"]
    pdf_files: list[dict] = pdf_corpus["files"]

    if smoke:
        smoke_ids = set(pdf_corpus["smoke_ids"])
        pdf_files = [f for f in pdf_files if f["id"] in smoke_ids]

    if max_pdfs is not None:
        pdf_files = pdf_files[:max_pdfs]

    for entry in pdf_files:
        entries.append({
            "id": entry["id"],
            "label": entry["label"],
            "format": "pdf",
            "source": entry.get("source", "py-pdf/sample-files"),
            "local_path": entry["local_path"],
            "license": entry.get("license", "CC-BY-SA-4.0"),
            "expected_outcome": entry.get("expected_outcome", "success"),
        })

    # ── Synthetic entries ──────────────────────────────────────────────────────
    syn = manifest["synthetic_corpus"]
    formats: list[str] = syn["formats"]
    path_template: str = syn["local_path_template"]
    variant_labels: dict[str, list[str]] = syn.get("variant_labels", {})

    variants_per_format = (
        syn["smoke_variants_per_format"] if smoke else syn["variants_per_format"]
    )

    for fmt in formats:
        labels = variant_labels.get(fmt, [])
        for v in range(1, variants_per_format + 1):
            local_path = path_template.format(format=fmt, variant=v, ext=fmt)
            label = labels[v - 1] if v <= len(labels) else f"variant-{v:02d}"
            entries.append({
                "id": f"syn-{fmt}-{v:02d}",
                "label": label,
                "format": fmt,
                "source": "synthetic",
                "local_path": local_path,
                "license": "none",
                "expected_outcome": "success",
            })

    return entries


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _run_one(corpus_root: Path, entry: dict) -> dict:
    from aksharamd.compiler import Compiler

    file_path = corpus_root / entry["local_path"]
    result: dict = {
        "id": entry["id"],
        "label": entry["label"],
        "format": entry["format"],
        "source": entry["source"],
        "expected_outcome": entry.get("expected_outcome", "success"),
        "file_path": str(file_path),
        "file_exists": file_path.exists(),
    }

    if not file_path.exists():
        result["outcome"] = "skipped"
        result["skip_reason"] = "file_not_found"
        return result

    t0 = time.monotonic()
    try:
        ctx = Compiler(output_dir=str(BENCHMARKS / ".public_corpus" / "_out")).compile(str(file_path))
        elapsed = time.monotonic() - t0

        if ctx.document is None:
            result["outcome"] = "error"
            result["errors"] = [{"code": e.code, "message": e.message} for e in ctx.validation.errors]
        else:
            doc = ctx.document
            blocks = doc.blocks
            output_text = "\n".join(b.content for b in blocks if b.content)
            block_type_counts: dict[str, int] = {}
            for b in blocks:
                key = b.type.value if hasattr(b.type, "value") else str(b.type)
                block_type_counts[key] = block_type_counts.get(key, 0) + 1

            result["outcome"] = "success"
            result["block_count"] = len(blocks)
            result["block_types"] = block_type_counts
            result["output_chars"] = len(output_text)
            result["estimated_tokens"] = _estimate_tokens(output_text)
            result["title"] = doc.title or ""
            result["pages"] = doc.pages
            if doc.metadata:
                result["readiness_score"] = doc.metadata.get("readiness_score")

        result["elapsed_seconds"] = round(elapsed, 3)
        warnings = [
            i for i in ctx.validation.issues
            if hasattr(i, "severity") and str(i.severity) in ("WARNING", "Severity.WARNING")
        ]
        result["warnings"] = [{"code": w.code, "message": w.message} for w in warnings]
        result["errors"] = [{"code": e.code, "message": e.message} for e in ctx.validation.errors]

    except Exception as exc:
        elapsed = time.monotonic() - t0
        result["outcome"] = "exception"
        result["exception"] = str(exc)
        result["elapsed_seconds"] = round(elapsed, 3)

    return result


def _write_jsonl(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_markdown(results: list[dict], manifest: dict, path: Path, elapsed_total: float) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    total = len(results)
    success = sum(1 for r in results if r.get("outcome") == "success")
    error = sum(1 for r in results if r.get("outcome") == "error")
    skipped = sum(1 for r in results if r.get("outcome") == "skipped")
    exception = sum(1 for r in results if r.get("outcome") == "exception")

    try:
        import aksharamd
        version = aksharamd.__version__
    except Exception:
        version = "unknown"

    lines = [
        "# AksharaMD Public Benchmark Results",
        "",
        f"**Generated:** {ts}  ",
        f"**AksharaMD version:** {version}  ",
        f"**Total files:** {total}  ",
        f"**Elapsed:** {elapsed_total:.1f}s  ",
        "",
        "## Scope",
        "",
        "This benchmark measures **parser coverage and extraction readiness** across a",
        "publicly reproducible corpus of real and programmatically generated documents.",
        "",
        "It does **not** measure: answer correctness, RAG faithfulness, citation accuracy,",
        "semantic agent performance, or LLM judge scores.",
        "",
        "## Summary",
        "",
        "| Outcome | Count |",
        "| --- | --- |",
        f"| Success | {success} |",
        f"| Error (expected or unexpected) | {error} |",
        f"| Exception | {exception} |",
        f"| Skipped (file not found) | {skipped} |",
        f"| **Total** | **{total}** |",
        "",
    ]

    pdf_results = [r for r in results if r.get("format") == "pdf"]
    syn_results = [r for r in results if r.get("source") == "synthetic"]

    pdf_meta_index: dict[str, dict] = {
        e["id"]: e.get("py_pdf_meta", {})
        for e in manifest.get("pdf_corpus", {}).get("files", [])
    }

    if pdf_results:
        lines += [
            "## PDF Results (py-pdf/sample-files — CC-BY-SA-4.0)",
            "",
            "| ID | Label | Pages | Outcome | Blocks | Chars | Tokens | Elapsed |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in pdf_results:
            meta = pdf_meta_index.get(r["id"], {})
            pages = meta.get("pages", r.get("pages", "?"))
            outcome = r.get("outcome", "?")
            blocks = r.get("block_count", "-")
            chars = r.get("output_chars", "-")
            tokens = r.get("estimated_tokens", "-")
            elapsed = r.get("elapsed_seconds", "-")
            if r.get("expected_outcome") == "error" and outcome == "error":
                outcome = "error (expected)"
            lines.append(
                f"| {r['id']} | {r['label']} | {pages} "
                f"| {outcome} | {blocks} | {chars} | {tokens} | {elapsed}s |"
            )
        lines.append("")

    if syn_results:
        lines += [
            "## Synthetic Format Results",
            "",
            "| ID | Format | Label | Outcome | Blocks | Chars | Tokens | Elapsed |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in syn_results:
            outcome = r.get("outcome", "?")
            blocks = r.get("block_count", "-")
            chars = r.get("output_chars", "-")
            tokens = r.get("estimated_tokens", "-")
            elapsed = r.get("elapsed_seconds", "-")
            lines.append(
                f"| {r['id']} | {r['format']} | {r.get('label', '-')} "
                f"| {outcome} | {blocks} | {chars} | {tokens} | {elapsed}s |"
            )
        lines.append("")

    error_results = [
        r for r in results
        if r.get("outcome") in ("error", "exception") and r.get("expected_outcome") != "error"
    ]
    if error_results:
        lines += ["## Unexpected Failures", ""]
        for r in error_results:
            lines.append(f"**{r['id']} — {r['label']}** ({r['format']})")
            for err in r.get("errors", []):
                lines.append(f"  - `{err['code']}`: {err['message']}")
            if r.get("exception"):
                lines.append(f"  - exception: {r['exception']}")
        lines.append("")

    lines += [
        "## Reproducibility",
        "",
        "To reproduce:",
        "```",
        "python benchmarks/build_public_corpus.py",
        "python benchmarks/run_public_benchmark.py",
        "```",
        "",
        "For a quick smoke run (20 files):",
        "```",
        "python benchmarks/build_public_corpus.py --smoke",
        "python benchmarks/run_public_benchmark.py --smoke",
        "```",
        "",
        "PDF files sourced from https://github.com/py-pdf/sample-files (CC-BY-SA-4.0).",
        "Synthetic files generated by `build_public_corpus.py` with no external dependencies.",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    output_dir: Path | None = None,
    smoke: bool = False,
    max_pdfs: int | None = None,
) -> list[dict]:
    manifest = _load_manifest()
    corpus_root = BENCHMARKS / manifest["corpus_dir"]
    results_dir = output_dir or (BENCHMARKS / "results")

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    jsonl_path = results_dir / f"public_benchmark_{ts}.jsonl"
    md_path = results_dir / f"public_benchmark_{ts}.md"

    entries = _build_entries(manifest, smoke=smoke, max_pdfs=max_pdfs)
    mode_label = "smoke" if smoke else "full"

    print(f"Corpus:  {corpus_root}")
    print(f"Mode:    {mode_label} ({len(entries)} files)")
    print(f"Results: {jsonl_path}")
    print()

    results: list[dict] = []
    t_start = time.monotonic()

    for entry in entries:
        print(f"[{entry['id']}] {entry['label']} ({entry['format']}) ...", end=" ", flush=True)
        r = _run_one(corpus_root, entry)
        results.append(r)
        outcome = r.get("outcome", "?")
        if outcome == "success":
            blocks = r.get("block_count", 0)
            chars = r.get("output_chars", 0)
            print(f"ok  blocks={blocks}  chars={chars}")
        elif outcome == "error":
            expected = r.get("expected_outcome") == "error"
            tag = "(expected)" if expected else "UNEXPECTED"
            errs = ", ".join(e["code"] for e in r.get("errors", []))
            print(f"error {tag}  {errs}")
        elif outcome == "skipped":
            print(f"skipped ({r.get('skip_reason', '?')})")
        else:
            print(f"{outcome}: {r.get('exception', '')}")

    elapsed_total = time.monotonic() - t_start

    _write_jsonl(results, jsonl_path)
    _write_markdown(results, manifest, md_path, elapsed_total)

    success = sum(1 for r in results if r["outcome"] == "success")
    total = len(results)
    print(f"\n{success}/{total} succeeded in {elapsed_total:.1f}s")
    print(f"JSONL: {jsonl_path}")
    print(f"MD:    {md_path}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Directory for result files (default: benchmarks/results/)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--smoke", action="store_true",
        help="10 smoke PDFs + 1 variant per format (20 files)"
    )
    mode.add_argument(
        "--full", action="store_true",
        help="All 34 PDFs + 10 variants per format (134 files, default)"
    )
    parser.add_argument(
        "--max-pdfs", type=int, default=None, metavar="N",
        help="Limit PDF files to at most N (applied after smoke filtering)"
    )
    args = parser.parse_args()

    results = run(output_dir=args.output_dir, smoke=args.smoke, max_pdfs=args.max_pdfs)
    unexpected_failures = [
        r for r in results
        if r.get("outcome") in ("error", "exception") and r.get("expected_outcome") != "error"
    ]
    if unexpected_failures:
        print(f"\nWARNING: {len(unexpected_failures)} unexpected failure(s).")
        sys.exit(1)


if __name__ == "__main__":
    main()
