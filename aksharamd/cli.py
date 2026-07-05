from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import click
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
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
    """AksharaMD — LLM Document Ingestion Pipeline"""


@main.command()
@click.argument("source", type=_SourceArg())
@click.option("-o", "--output", default="output", show_default=True, help="Output directory")
@click.option("--quiet", is_flag=True, help="Suppress progress output")
@click.option("--timings", is_flag=True, help="Show per-stage timing breakdown")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging from all plugins")
def compile(source: str, output: str, quiet: bool, timings: bool, verbose: bool):
    """Compile a document or URL into AI-optimized Markdown, JSON, and chunks."""
    _setup_logging(verbose)

    file_output = str(Path(output) / _output_stem(source))
    compiler = Compiler(output_dir=file_output)

    if quiet:
        ctx = compiler.compile(source)
    else:
        with console.status(
            f"[bold blue]AksharaMD[/] compiling [cyan]{source}[/]...",
            spinner="dots",
        ) as status:
            ctx = compiler.compile(source, on_stage=lambda s: status.update(
                f"[bold blue]AksharaMD[/]  [dim]{s}[/]"
            ))

    if not quiet and ctx.manifest:
        m = ctx.manifest
        tokens_saved = max(0, m.original_tokens - m.optimized_tokens)
        pages_per_sec = round(m.pages / m.elapsed_seconds, 1) if m.elapsed_seconds > 0 and m.pages > 0 else 0
        tokens_per_sec = round(m.original_tokens / m.elapsed_seconds) if m.elapsed_seconds > 0 and m.original_tokens > 0 else 0

        score = m.readiness_score
        band = m.quality_band or ("HIGH" if score >= 85 else "OK" if score >= 70 else "RISKY" if score >= 50 else "POOR")

        if score >= 85:
            score_str = f"[bold green]{score}/100  {band}[/]"
            panel_color = "green"
            panel_title = "[bold green]Compilation Complete[/]"
        elif score >= 70:
            score_str = f"[bold yellow]{score}/100  {band}[/]"
            panel_color = "yellow"
            panel_title = "[bold yellow]Compilation Complete[/]"
        elif score >= 50:
            score_str = f"[bold red]{score}/100  {band}[/]"
            panel_color = "red"
            panel_title = "[bold red]Compilation Complete — Review Warnings[/]"
        else:
            score_str = f"[bold red]{score}/100  {band}[/]"
            panel_color = "red"
            panel_title = "[bold red]Compilation Complete — Poor Extraction[/]"

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        t.add_column(style="bold")
        t.add_column()

        t.add_row("Source",           m.source)

        # For PDFs show the classification alongside the type
        type_label = m.file_type
        if m.pdf_classification:
            _cls_labels = {
                "native_text": "native text",
                "scanned": "scanned / image-only",
                "hybrid": "hybrid (text + scanned pages)",
                "table_heavy": "table-heavy",
                "layout_heavy": "multi-column layout",
                "low_confidence": "low-confidence",
            }
            type_label = f"{m.file_type}  [{_cls_labels.get(m.pdf_classification, m.pdf_classification)}]"

        t.add_row("Type",             type_label)
        t.add_row("Pages",            str(m.pages))
        if m.image_pages:
            t.add_row("Image pages",  f"[yellow]{m.image_pages}[/]  (no text layer)")
        t.add_row("Chunks",           str(m.chunks))
        t.add_row("Tables",           str(m.tables))
        t.add_row("Images",           str(m.images))
        t.add_row("", "")

        t.add_row("Before (tokens)",  f"{m.original_tokens:,}")
        t.add_row("After  (tokens)",  f"{m.optimized_tokens:,}")
        t.add_row("Tokens saved",     f"[bold green]{tokens_saved:,}[/]  ({m.token_reduction_percent:.1f}%)")
        if tokens_saved > 0:
            t.add_row("Cost saved",    f"[green]{_dollar_row(tokens_saved)}[/]")
        t.add_row("", "")
        t.add_row("Readiness",        score_str)
        t.add_row("Total time",       f"{m.elapsed_seconds:.2f}s")
        if m.pages > 0:
            t.add_row("Throughput",   f"{pages_per_sec} pages/s  /{tokens_per_sec:,} tokens/s")
        if m.errors:
            t.add_row("Errors",       f"[red]{len(m.errors)}[/]")

        console.print(Panel(t, title=panel_title, border_style=panel_color))

        # Show extraction notes (quality signals from the scoring engine)
        if m.confidence_notes:
            note_lines = "\n".join(f"  {escape(n)}" for n in m.confidence_notes)
            console.print(Panel(
                note_lines,
                title=f"[bold]Extraction Quality  {score}/100 — {band}[/]",
                border_style=panel_color,
            ))

        # Show validation warnings if score is below HIGH — these contain
        # actionable guidance that scoring notes may not cover
        actionable_codes = {"ENCRYPTED_PDF", "OCR_REQUIRED", "GLYPH_ARTIFACTS",
                            "NEAR_EMPTY_OUTPUT", "LOW_TEXT_DENSITY", "TOKEN_BLOAT",
                            "REPEATED_CONTENT"}
        visible_warnings = [
            w for w in ctx.validation.warnings
            if w.code in actionable_codes
        ]
        if visible_warnings and score < 85:
            warn_lines = "\n".join(
                f"  [yellow]![/]  [{w.code}] {escape(w.message)}" for w in visible_warnings
            )
            console.print(Panel(
                warn_lines,
                title="[bold yellow]Warnings[/]",
                border_style="yellow",
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

        # Always show output locations so user knows where to look
        output_files = [
            ("document.md",     "compiled Markdown"),
            ("document.json",   "structured block model"),
            ("manifest.json",   "token counts, score, metadata"),
            ("validation.json", "all validation issues"),
        ]
        file_lines = "\n".join(
            f"  [cyan]{file_output}/{name}[/]  [dim]{desc}[/]"
            for name, desc in output_files
        )
        console.print(Panel(file_lines, title="[bold]Output Files[/]", border_style="dim"))

    # When the pipeline fails before building a manifest (e.g. encrypted PDF),
    # still surface any actionable warnings so the user knows what to do.
    if not quiet and ctx.manifest is None and ctx.validation.warnings:
        for w in ctx.validation.warnings:
            console.print(f"[yellow]WARNING[/] [{w.code}] {escape(w.message)}")

    if ctx.validation.errors:
        for err in ctx.validation.errors:
            console.print(f"[red]ERROR[/] [{err.code}] {err.message}")
        sys.exit(1)


@main.command()
@click.argument("source", type=_SourceArg())
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def validate(source: str, verbose: bool):
    """Validate a document or URL without writing any output files."""
    _setup_logging(verbose)
    # compile_to_string runs the full pipeline (parse → validate → chunk) but
    # skips the export stage, so no files are written to disk.
    compiler = Compiler(output_dir=str(Path("output") / _output_stem(source)))
    _, ctx = compiler.compile_to_string(source)

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
            str(r["name"]),
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
@click.argument("source_dir", type=click.Path(exists=True, file_okay=False))
@click.option("-o", "--output", default=None, help="Write corpus chunks to this JSON file")
@click.option("--budget", default=60_000, show_default=True, help="Max tokens per corpus chunk")
@click.option("--dedup-threshold", default=0.5, show_default=True,
              help="Jaccard similarity threshold for near-duplicate skipping (0–1)")
@click.option("--quiet", is_flag=True, help="Suppress progress output")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def corpus(source_dir: str, output: str | None, budget: int, dedup_threshold: float,
           quiet: bool, verbose: bool):
    """Compile every supported file under SOURCE_DIR into token-budget chunks.

    Files are grouped by directory and packed greedily up to --budget tokens.
    Near-duplicate documents are skipped automatically via MinHash LSH.

    Example: aksharamd corpus ./docs/ --budget 60000 -o corpus_chunks.json
    """
    import json as _json

    _setup_logging(verbose)

    compiler = Compiler(output_dir=str(Path(source_dir) / ".aksharamd_cache"))

    if quiet:
        chunks = compiler.compile_corpus(
            source_dir,
            token_budget=budget,
            dedup_threshold=dedup_threshold,
        )
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"Compiling {source_dir}", total=None)

            def _on_file(name: str, idx: int, total: int) -> None:
                progress.update(task, description=f"[bold blue]{name}[/]", total=total, completed=idx)

            chunks = compiler.compile_corpus(
                source_dir,
                token_budget=budget,
                dedup_threshold=dedup_threshold,
                on_file=_on_file,
            )

    total_docs = sum(len(c["documents"]) for c in chunks)
    total_tokens = sum(c["token_count"] for c in chunks)

    if not quiet:
        t = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
        t.add_column("Chunk", justify="right")
        t.add_column("Documents", justify="right")
        t.add_column("Tokens", justify="right")
        t.add_column("Files")
        for chunk in chunks:
            files_preview = ", ".join(d["source"] for d in chunk["documents"][:3])
            if len(chunk["documents"]) > 3:
                files_preview += f" +{len(chunk['documents']) - 3} more"
            t.add_row(
                str(chunk["chunk_index"]),
                str(len(chunk["documents"])),
                f"{chunk['token_count']:,}",
                files_preview,
            )
        console.print(t)
        console.print(
            f"[green]{total_docs} documents[/] packed into [bold]{len(chunks)} chunks[/], "
            f"[cyan]{total_tokens:,} total tokens[/]"
        )

    if output:
        out_path = Path(output)
        out_path.write_text(_json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
        if not quiet:
            console.print(f"[dim]Chunks written to {out_path}[/]")


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
            ts = e["ts"].split("+")[0].split(".")[0].replace("T", " ")
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


@main.command("mcp-config")
@click.option("--write", is_flag=True,
              help="Write directly into Claude Desktop's config file (merges safely with existing config)")
def mcp_config(write: bool):
    """Generate the MCP server config for Claude Desktop.

    Run once after installation. Use --write to apply the config automatically,
    or copy the printed JSON into your Claude Desktop config manually.
    """
    import json
    import platform

    # Locate the aksharamd-mcp script. On Mac/Linux (including venvs) it sits
    # in the same directory as python. On Windows system installs it lives in a
    # Scripts\ subdirectory; venvs put python.exe and scripts in the same place.
    exe_dir = Path(sys.executable).parent
    search_dirs = [exe_dir, exe_dir / "Scripts"]  # Scripts\ is a no-op on non-Windows
    script_path: Path | None = None
    for scripts_dir in search_dirs:
        for candidate in ("aksharamd-mcp", "aksharamd-mcp.exe"):
            p = scripts_dir / candidate
            if p.exists():
                script_path = p
                break
        if script_path:
            break

    if script_path:
        server_config: dict = {"command": str(script_path)}
    else:
        # Fallback: explicit python -m invocation (always works)
        server_config = {"command": sys.executable, "args": ["-m", "aksharamd.mcp_server"]}

    config_block = {"mcpServers": {"aksharamd": server_config}}

    system = platform.system()
    if system == "Darwin":
        config_path = (
            Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
    elif system == "Windows":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        config_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
    else:
        config_path = Path.home() / ".config" / "claude" / "claude_desktop_config.json"

    if write:
        existing: dict = {}
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            import shutil
            import time as _time
            backup = config_path.with_suffix(f".{int(_time.time())}.bak.json")
            shutil.copy2(config_path, backup)
            console.print(f"[dim]Backup saved to {backup}[/dim]")
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                console.print(
                    f"[yellow]Warning:[/] could not parse existing config at {config_path}. "
                    f"The original is backed up at {backup}. "
                    "A fresh config will be written."
                )
        if not isinstance(existing.get("mcpServers"), dict):
            existing["mcpServers"] = {}
        existing.setdefault("mcpServers", {})["aksharamd"] = server_config
        new_content = json.dumps(existing, indent=2)
        # Validate before writing — catch any serialization edge cases
        json.loads(new_content)
        config_path.write_text(new_content, encoding="utf-8")
        console.print(f"[green]Config written to[/] {config_path}")
        console.print("Restart Claude Desktop — AksharaMD will appear in the tools panel.")
    else:
        console.print("\nPaste this into your Claude Desktop config:\n")
        console.print_json(json.dumps(config_block))
        console.print(f"\n[dim]Config file location:[/] {config_path}")
        console.print(
            "[dim]Or run [bold]aksharamd mcp-config --write[/bold] "
            "to apply the config automatically.[/dim]\n"
        )


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
