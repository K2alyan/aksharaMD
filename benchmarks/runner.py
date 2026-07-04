"""
AksharaMD vs MarkItDown benchmark runner.

Usage:
    python -m benchmarks.runner --input-dir path/to/pdfs --output-dir benchmark_results

Runs every PDF in input-dir through both AksharaMD and MarkItDown,
collects metrics, and writes a JSON report + Rich summary table.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.table import Table

from .metrics import compute_metrics

console = Console()


def _run_omnimark(pdf_path: Path, output_dir: Path) -> tuple[str, float, list[str]]:
    from aksharamd.compiler import Compiler
    out = str(output_dir / pdf_path.stem)
    try:
        t0 = time.perf_counter()
        ctx = Compiler(output_dir=out).compile(str(pdf_path))
        elapsed = time.perf_counter() - t0
        md_path = Path(out) / "document.md"
        text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        errors = [e.message for e in ctx.validation.errors]
        return text, elapsed, errors
    except Exception as e:
        return "", 0.0, [str(e)]


def _run_markitdown(pdf_path: Path) -> tuple[str, float]:
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        t0 = time.perf_counter()
        result = md.convert(str(pdf_path))
        elapsed = time.perf_counter() - t0
        return result.text_content, elapsed
    except Exception:
        return "", 0.0


def run_benchmark(
    pdf_paths: list[Path],
    output_dir: Path,
    skip_markitdown: bool = False,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    om_out = output_dir / "omnimark"
    results = []

    for i, pdf in enumerate(pdf_paths, 1):
        console.print(f"[dim]({i}/{len(pdf_paths)})[/] [cyan]{pdf.name}[/]")

        # AksharaMD
        om_text, om_elapsed, om_errors = _run_omnimark(pdf, om_out)
        om_metrics = compute_metrics("omnimark", pdf.name, om_text, om_elapsed)
        om_metrics.errors = om_errors

        row: dict = {"file": pdf.name, "omnimark": asdict(om_metrics)}

        # MarkItDown
        if not skip_markitdown:
            md_text, md_elapsed = _run_markitdown(pdf)
            md_metrics = compute_metrics("markitdown", pdf.name, md_text, md_elapsed)
            row["markitdown"] = asdict(md_metrics)

            token_delta = md_metrics.token_count - om_metrics.token_count
            reduction_pct = round(token_delta / md_metrics.token_count * 100, 1) if md_metrics.token_count else 0
            row["token_reduction_vs_markitdown"] = reduction_pct
            row["omnimark_faster_by"] = round(md_elapsed - om_elapsed, 3)

        results.append(row)

    # Write full JSON report
    report_path = output_dir / "benchmark_report.json"
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    console.print(f"\nFull report saved to [cyan]{report_path}[/]")

    return results


def print_summary_table(results: list[dict]) -> None:
    t = Table(box=box.ROUNDED, header_style="bold cyan", show_header=True)
    t.add_column("File", max_width=30)
    t.add_column("OM tokens", justify="right")
    t.add_column("MD tokens", justify="right")
    t.add_column("Reduction", justify="right")
    t.add_column("OM heads", justify="right")
    t.add_column("MD heads", justify="right")
    t.add_column("OM tables", justify="right")
    t.add_column("MD tables", justify="right")
    t.add_column("OM noise", justify="right")
    t.add_column("MD noise", justify="right")
    t.add_column("OM time", justify="right")
    t.add_column("MD time", justify="right")
    t.add_column("OM errors", justify="right")

    wins_om = 0
    wins_md = 0

    for r in results:
        om = r["omnimark"]
        md = r.get("markitdown", {})
        reduction = r.get("token_reduction_vs_markitdown", 0)
        color = "green" if reduction > 0 else "red"

        if reduction > 0:
            wins_om += 1
        elif reduction < 0:
            wins_md += 1

        t.add_row(
            r["file"][:30],
            f"{om['token_count']:,}",
            f"{md.get('token_count', 0):,}" if md else "—",
            f"[{color}]{reduction:+.1f}%[/{color}]" if md else "—",
            str(om["heading_count"]),
            str(md.get("heading_count", "—")) if md else "—",
            str(om["table_count"]),
            str(md.get("table_count", "—")) if md else "—",
            str(om["noise_line_count"]),
            str(md.get("noise_line_count", "—")) if md else "—",
            f"{om['elapsed_seconds']:.2f}s",
            f"{md.get('elapsed_seconds', 0):.2f}s" if md else "—",
            str(len(om.get("errors", []))),
        )

    console.print(t)
    if results and "markitdown" in results[0]:
        console.print(
            f"\nAksharaMD wins (fewer tokens): [green]{wins_om}[/]  "
            f"MarkItDown wins: [yellow]{wins_md}[/]  "
            f"Total: {len(results)} files"
        )


@click.command()
@click.option("--input-dir", required=True, type=click.Path(exists=True), help="Directory containing PDF files")
@click.option("--output-dir", default="benchmark_results", show_default=True)
@click.option("--limit", default=0, help="Max PDFs to process (0 = all)")
@click.option("--skip-markitdown", is_flag=True, help="Only run AksharaMD")
def main(input_dir: str, output_dir: str, limit: int, skip_markitdown: bool):
    """Run AksharaMD vs MarkItDown benchmark on a directory of PDFs."""
    pdfs = sorted(Path(input_dir).glob("**/*.pdf"))
    if limit:
        pdfs = pdfs[:limit]

    if not pdfs:
        console.print("[red]No PDFs found in input directory[/]")
        return

    console.print(f"[bold blue]AksharaMD Benchmark[/] — {len(pdfs)} PDFs\n")
    results = run_benchmark(pdfs, Path(output_dir), skip_markitdown=skip_markitdown)
    print_summary_table(results)


if __name__ == "__main__":
    main()
