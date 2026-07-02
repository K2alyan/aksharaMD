from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import ledger as _ledger
from .compiler import Compiler
from .utils import DISPLAY_MODELS, TOKEN_PRICES, tokens_to_dollars

console = Console(highlight=False)


class _SourceArg(click.ParamType):
    """Click argument type that accepts a local file path OR an http(s):// URL."""
    name = "source"

    def convert(self, value, param, ctx):
        if value.startswith(("http://", "https://")):
            parsed = urlparse(value)
            if not parsed.netloc:
                self.fail(f"Invalid URL — missing hostname: {value!r}", param, ctx)
            return value
        p = Path(value)
        if p.exists():
            return str(p)
        self.fail(f"{value!r} is not a valid file path or URL.", param, ctx)


def _output_stem(source: str) -> str:
    """Derive a filesystem-safe directory name from a file path or URL."""
    if source.startswith(("http://", "https://")):
        parsed = urlparse(source)
        stem = Path(parsed.path).stem or parsed.netloc.split(":")[0]
        return re.sub(r"[^\w\-]", "_", stem) or "url_output"
    return Path(source).stem


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s [%(name)s] %(message)s",
    )


_MODEL_SHORT = {
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o-mini",
    "claude-sonnet-4": "Claude Sonnet",
    "claude-haiku-4": "Claude Haiku",
}

def _dollar_row(tokens_saved: int) -> str:
    """Format compact dollar savings for the two most popular models."""
    parts = []
    for model in ["gpt-4o", "claude-sonnet-4"]:
        d = tokens_to_dollars(tokens_saved, model)
        label = _MODEL_SHORT.get(model, model)
        parts.append(f"{label}: ${d:.4f}")
    return "  /  ".join(parts)


@click.group()
@click.version_option()
def main():
    """AksharaMD — AI Document Compiler"""


@main.command()
@click.argument("source", type=_SourceArg())
@click.option("-o", "--output", default="output", show_default=True, help="Output directory")
@click.option("--quiet", is_flag=True, help="Suppress progress output")
@click.option("--timings", is_flag=True, help="Show per-stage timing breakdown")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging from all plugins")
def compile(source: str, output: str, quiet: bool, timings: bool, verbose: bool):
    """Compile a document or URL into AI-optimized Markdown, JSON, and chunks."""
    _setup_logging(verbose)

    if not quiet:
        console.print(f"[bold blue]AksharaMD[/] compiling [cyan]{source}[/]...")

    file_output = str(Path(output) / _output_stem(source))
    compiler = Compiler(output_dir=file_output)
    ctx = compiler.compile(source)

    if not quiet and ctx.manifest:
        m = ctx.manifest
        tokens_saved = max(0, m.original_tokens - m.optimized_tokens)
        pages_per_sec = round(m.pages / m.elapsed_seconds, 1) if m.elapsed_seconds > 0 and m.pages > 0 else 0
        tokens_per_sec = round(m.original_tokens / m.elapsed_seconds) if m.elapsed_seconds > 0 and m.original_tokens > 0 else 0

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        t.add_column(style="bold")
        t.add_column()

        t.add_row("Source",           m.source)
        t.add_row("Type",             m.file_type)
        t.add_row("Pages",            str(m.pages))
        t.add_row("Chunks",           str(m.chunks))
        t.add_row("Tables",           str(m.tables))
        t.add_row("Images",           str(m.images))
        t.add_row("", "")  # spacer
        t.add_row("Before (tokens)",  f"{m.original_tokens:,}")
        t.add_row("After  (tokens)",  f"{m.optimized_tokens:,}")
        t.add_row("Tokens saved",     f"[bold green]{tokens_saved:,}[/]  ({m.token_reduction_percent:.1f}%)")
        if tokens_saved > 0:
            t.add_row("Cost saved",    f"[green]{_dollar_row(tokens_saved)}[/]")
        t.add_row("", "")
        conf = m.readiness_score
        if conf >= 85:
            conf_str = f"[bold green]{conf}/100[/]"
        elif conf >= 65:
            conf_str = f"[bold yellow]{conf}/100[/]"
        else:
            conf_str = f"[bold red]{conf}/100[/]"
        t.add_row("Confidence",       conf_str)
        t.add_row("Total time",       f"{m.elapsed_seconds:.2f}s")
        if m.pages > 0:
            t.add_row("Throughput",   f"{pages_per_sec} pages/s  /{tokens_per_sec:,} tokens/s")
        if m.errors:
            t.add_row("Errors",       f"[red]{len(m.errors)}[/]")

        console.print(Panel(t, title="[bold green]Compilation Complete[/]", border_style="green"))

        if m.confidence_notes:
            note_lines = "\n".join(f"  - {n}" for n in m.confidence_notes)
            border = "yellow" if conf < 85 else "green"
            console.print(Panel(
                note_lines,
                title=f"[bold]Extraction Notes  {conf}/100[/]",
                border_style=border,
            ))

        if timings and m.stage_timings:
            st = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
            st.add_column("Stage", style="bold")
            st.add_column("Time", justify="right")
            st.add_column("Share", justify="right")
            total = sum(m.stage_timings.values()) or 1
            for stage, secs in m.stage_timings.items():
                share = f"{secs / total * 100:.0f}%"
                st.add_row(stage, f"{secs:.3f}s", share)
            console.print(Panel(st, title="[bold]Stage Timings[/]", border_style="dim"))

        console.print(f"Output written to [cyan]{file_output}/[/]")

    if ctx.validation.errors:
        for err in ctx.validation.errors:
            console.print(f"[red]ERROR[/] {err.code}: {err.message}")
        sys.exit(1)


@main.command()
@click.argument("source", type=_SourceArg())
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def validate(source: str, verbose: bool):
    """Validate a document or URL without full compilation."""
    _setup_logging(verbose)
    compiler = Compiler(output_dir=str(Path("output") / _output_stem(source)))
    ctx = compiler.compile(source)

    if ctx.validation.passed:
        console.print("[green]Validation passed[/]")
    else:
        console.print("[red]Validation failed[/]")

    for issue in ctx.validation.issues:
        color = {"error": "red", "warning": "yellow", "info": "blue"}[issue.severity.value]
        console.print(f"[{color}]{issue.severity.value.upper()}[/] [{issue.code}] {issue.message}")

    sys.exit(0 if ctx.validation.passed else 1)


@main.command()
@click.argument("sources", nargs=-1, type=_SourceArg(), required=True)
@click.option("-o", "--output", default="output", show_default=True)
@click.option("--verbose", "-v", is_flag=True)
def benchmark(sources: tuple[str, ...], output: str, verbose: bool):
    """Compile one or more documents or URLs and print a benchmark summary table."""
    _setup_logging(verbose)
    rows = []
    for source in sources:
        label = source if source.startswith(("http://", "https://")) else Path(source).name
        console.print(f"[dim]Compiling {label}...[/]")
        file_output = str(Path(output) / _output_stem(source))
        ctx = Compiler(output_dir=file_output).compile(source)
        m = ctx.manifest
        if m:
            pages_per_sec = round(m.pages / m.elapsed_seconds, 1) if m.elapsed_seconds > 0 and m.pages > 0 else 0
            rows.append({
                "name": label,
                "pages": m.pages,
                "orig_tokens": m.original_tokens,
                "opt_tokens": m.optimized_tokens,
                "saved": max(0, m.original_tokens - m.optimized_tokens),
                "reduction": m.token_reduction_percent,
                "tables": m.tables,
                "chunks": m.chunks,
                "score": m.readiness_score,
                "elapsed": m.elapsed_seconds,
                "pages_per_sec": pages_per_sec,
                "parse_s": m.stage_timings.get("parse", 0),
            })

    if not rows:
        console.print("[red]No results[/]")
        return

    t = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    t.add_column("File")
    t.add_column("Pages", justify="right")
    t.add_column("Before", justify="right")
    t.add_column("After", justify="right")
    t.add_column("Saved", justify="right")
    t.add_column("Reduction", justify="right")
    t.add_column("Tables", justify="right")
    t.add_column("Score", justify="right")
    t.add_column("Time", justify="right")

    for r in rows:
        t.add_row(
            r["name"],
            str(r["pages"]),
            f"{r['orig_tokens']:,}",
            f"{r['opt_tokens']:,}",
            f"[green]{r['saved']:,}[/]",
            f"{r['reduction']:.1f}%",
            str(r["tables"]),
            f"{r['score']}/100",
            f"{r['elapsed']:.2f}s",
        )

    console.print(t)


@main.command()
@click.option("--reset", is_flag=True, help="Delete the savings ledger (irreversible)")
def stats(reset: bool):
    """Show cumulative token savings across all AksharaMD compilations."""
    from pathlib import Path

    ledger_path = Path.home() / ".aksharamd" / "ledger.jsonl"

    if reset:
        if ledger_path.exists():
            ledger_path.unlink()
            console.print("[yellow]Ledger cleared.[/]")
        return

    data = _ledger.get_stats()
    if not data:
        console.print("[dim]No compilations recorded yet. Run [bold]aksharamd compile <file>[/] to start.[/]")
        return

    total_saved = data["total_saved_tokens"]
    total_orig  = data["total_original_tokens"]
    total_opt   = data["total_optimized_tokens"]

    # ── Summary panel ─────────────────────────────────────────────────────────
    s = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    s.add_column(style="bold")
    s.add_column()
    s.add_row("Total compilations",   f"{data['total_compilations']:,}")
    s.add_row("Tokens processed",     f"{total_orig:,}")
    s.add_row("Tokens delivered",     f"{total_opt:,}")
    s.add_row("Tokens saved",         f"[bold green]{total_saved:,}[/]  ({data['reduction_percent']:.1f}%)")
    s.add_row("Total time spent",     f"{data['total_elapsed_seconds']:.1f}s")
    console.print(Panel(s, title="[bold]AksharaMD Savings Summary[/]", border_style="blue"))

    # ── Dollar savings table ───────────────────────────────────────────────────
    if total_saved > 0:
        dt = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        dt.add_column("Model")
        dt.add_column("Price / 1M tokens", justify="right")
        dt.add_column("You saved", justify="right", style="green")
        for model in DISPLAY_MODELS:
            price = TOKEN_PRICES[model]
            saved_usd = tokens_to_dollars(total_saved, model)
            dt.add_row(model, f"${price:.3f}", f"${saved_usd:.4f}")
        console.print(Panel(dt, title="[bold]Dollar Savings[/]", border_style="green"))

    # ── By file type ───────────────────────────────────────────────────────────
    by_type = data.get("by_file_type", {})
    if by_type:
        ft = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        ft.add_column("File type")
        ft.add_column("Compilations", justify="right")
        ft.add_column("Tokens saved", justify="right")
        for ftype, info in sorted(by_type.items(), key=lambda x: -x[1]["saved"]):
            ft.add_row(ftype, str(info["count"]), f"{info['saved']:,}")
        console.print(Panel(ft, title="[bold]By File Type[/]", border_style="dim"))

    # ── Recent compilations ────────────────────────────────────────────────────
    recent = data.get("recent", [])
    if recent:
        rt = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        rt.add_column("Time (UTC)")
        rt.add_column("File")
        rt.add_column("Type")
        rt.add_column("Saved", justify="right")
        rt.add_column("Elapsed", justify="right")
        for e in reversed(recent):
            ts = e["ts"][:19].replace("T", " ")
            saved = e["saved_tokens"]
            color = "green" if saved > 0 else "dim"
            rt.add_row(ts, e["source"][:40], e["file_type"],
                       f"[{color}]{saved:,}[/{color}]",
                       f"{e['elapsed_seconds']:.2f}s")
        console.print(Panel(rt, title="[bold]Recent Compilations[/]", border_style="dim"))


@main.command("show-manifest")
@click.argument("output_dir", type=click.Path(exists=True))
def show_manifest(output_dir: str):
    """Print the manifest from a previous compilation."""
    p = Path(output_dir) / "manifest.json"
    if not p.exists():
        console.print("[red]No manifest.json found in output dir[/]")
        sys.exit(1)
    console.print_json(p.read_text())


@main.command()
def formats():
    """List all supported input file formats."""
    import aksharamd.plugins.registry as _reg
    from .plugins import parsers as _parsers_pkg  # noqa: F401 — trigger registration

    exts = sorted(_reg._parsers.keys())
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
    t.add_column("Extension")
    t.add_column("Parser")
    for ext in exts:
        cls = _reg._parsers.get(ext)
        parser_name = cls.name if cls and hasattr(cls, "name") else "unknown"
        t.add_row(f".{ext}", parser_name)
    console.print(Panel(t, title="[bold]Supported Formats[/]", border_style="blue"))
