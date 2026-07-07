from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import click
from rich import box
from rich.console import Console
from rich.live import Live
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
from rich.text import Text

from . import ledger as _ledger
from .compiler import Compiler
from .utils import DISPLAY_MODELS, TOKEN_PRICES, tokens_to_dollars

console = Console(highlight=False)

# ── First-run onboarding ──────────────────────────────────────────────────────

_FIRST_RUN_MARKER = Path.home() / ".aksharamd" / ".initialized"

_EXTRAS_ROWS = [
    ("Scanned PDFs, image files",        "[ocr]",    'pip install "aksharamd[ocr]"',    "Tesseract OCR extracts text from pages with no text layer"),
    ("Scanned PDFs with tables/layouts", "[vision]", 'pip install "aksharamd[vision]"', "Marker (neural layout model) reconstructs table structure — ~3 GB download"),
    ("PDFs with math equations",         "[math]",   'pip install "aksharamd[math]"',   "pix2tex converts equation images to LaTeX — ~500 MB download"),
    ("Audio or video files",             "[audio]",  'pip install "aksharamd[audio]"',  "Whisper transcribes speech — requires ffmpeg on PATH"),
    ("Files in S3 buckets",              "[cloud]",  'pip install "aksharamd[cloud]"',  "Read directly from s3:// URIs"),
]


def _show_first_run_onboarding() -> None:
    """Show the extras welcome panel once, on first use."""
    if _FIRST_RUN_MARKER.exists():
        return
    try:
        _FIRST_RUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _FIRST_RUN_MARKER.touch()
    except OSError:
        pass  # non-fatal — just show it every time if we can't write

    t = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    t.add_column("If your documents include...", style="bold", min_width=38)
    t.add_column("Install", style="cyan", min_width=9)

    for doc_type, extra, _cmd, _ in _EXTRAS_ROWS:
        t.add_row(doc_type, escape(extra))

    body = Text()
    body.append(
        "AksharaMD is installed. The base install handles PDFs (text layer), "
        "Word, Excel, PowerPoint, HTML, EPUB, email, archives, and 35+ other formats "
        "with no additional setup.\n\n",
        style="dim",
    )
    body.append("For harder document types, install only what you need:\n\n")

    lines = [body, t, Text()]

    footer = Text()
    footer.append("Install an extra:  ", style="bold")
    footer.append('pip install "aksharamd[ocr]"', style="cyan bold")
    footer.append("  (replace [ocr] with the extra you need)\n\n")
    footer.append("Install everything:  ", style="bold")
    footer.append('pip install "aksharamd[full]"\n\n', style="cyan bold")
    footer.append(
        "You can skip extras for now. When AksharaMD encounters content it cannot\n"
        "fully extract, it flags it with a warning code and a lower readiness score\n"
        "so you know exactly which extra to add - before bad data reaches your LLM.",
        style="dim",
    )
    lines.append(footer)

    from rich.console import Group
    console.print(Panel(
        Group(*lines),
        title="[bold]AksharaMD - Optional Extras[/]",
        border_style="blue",
        padding=(1, 2),
    ))
    console.print()


# ── Live compile progress ─────────────────────────────────────────────────────

class _LiveProgress:
    """Accumulates completed steps as Rich Text lines with elapsed times.

    Usage::
        with _LiveProgress(source) as lp:
            ctx = compiler.compile(source, on_stage=lp.update)
    """

    def __init__(self, source: str) -> None:
        self._source = Path(source).name if not source.startswith(("http", "s3")) else source
        self._lines: list[str] = []
        self._current: str = ""
        self._t_step = time.perf_counter()
        self._t_start = time.perf_counter()
        self._lock = threading.Lock()
        self._live: Live | None = None

    def _render(self) -> Text:
        from rich.text import Text as RText
        out = RText()
        out.append("  Compiling ", style="dim")
        out.append(self._source, style="bold cyan")
        out.append("\n\n")
        for line in self._lines:
            # lines are plain strings with embedded Rich markup — parse them
            out.append_text(Text.from_markup(line + "\n"))
        if self._current:
            out.append("  >>  ", style="cyan")
            out.append(self._current, style="")
            out.append("...\n", style="dim")
        return out

    def update(self, message: str) -> None:
        with self._lock:
            now = time.perf_counter()
            elapsed = now - self._t_step
            if self._current:
                # store as markup string; rendered in _render()
                self._lines.append(
                    f"  [bold green]OK[/bold green]  {escape(self._current)}"
                    f"  [dim]{elapsed:.1f}s[/dim]"
                )
            self._current = message
            self._t_step = now
            if self._live:
                self._live.update(self._render())

    def __enter__(self) -> _LiveProgress:
        self._live = Live(self._render(), console=console, refresh_per_second=8, transient=False)
        self._live.__enter__()
        return self

    def __exit__(self, *args) -> None:
        with self._lock:
            # Mark final step as complete
            if self._current:
                elapsed = time.perf_counter() - self._t_step
                self._lines.append(
                    f"  [bold green]OK[/bold green]  {escape(self._current)}"
                    f"  [dim]{elapsed:.1f}s[/dim]"
                )
                self._current = ""
            if self._live:
                self._live.update(self._render())
        if self._live:
            self._live.__exit__(*args)


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


def _check_optional_deps() -> list[dict]:
    """Return status of every optional aksharamd dependency."""
    import importlib.util
    import shutil

    def _has(spec: str) -> bool:
        return importlib.util.find_spec(spec) is not None

    # OCR
    has_pytesseract = _has("pytesseract")
    tesseract_bin = shutil.which("tesseract")
    ocr_ok = False
    if has_pytesseract and tesseract_bin:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            ocr_ok = True
        except Exception:
            pass

    # Vision (Marker)
    marker_ok = _has("marker")

    # Math OCR (pix2tex)
    math_ok = _has("pix2tex")

    # Audio (Whisper)
    whisper_ok = _has("whisper")
    ffmpeg_ok = shutil.which("ffmpeg") is not None

    # Cloud (S3)
    cloud_ok = _has("boto3")

    # LibreOffice / Pandoc (system binaries only)
    lo_ok = bool(shutil.which("libreoffice") or shutil.which("soffice"))
    pandoc_ok = bool(shutil.which("pandoc"))

    return [
        {
            "name": "OCR (Tesseract)",
            "ok": ocr_ok,
            "what": "Extract text from scanned PDFs and image files",
            "install": 'pip install "aksharamd[ocr]"',
            "note": f"Tesseract binary: {'found' if tesseract_bin else 'NOT FOUND - install from https://github.com/tesseract-ocr/tesseract'}",
        },
        {
            "name": "Vision / tables (Marker)",
            "ok": marker_ok,
            "what": "Reconstruct table structure from image-based PDF pages",
            "install": 'pip install "aksharamd[vision]"',
            "note": "Requires PyTorch, downloads ~3 GB of models on first run",
        },
        {
            "name": "Audio (Whisper)",
            "ok": whisper_ok,
            "what": "Transcribe MP3, WAV, M4A, MP4 audio / video",
            "install": 'pip install "aksharamd[audio]"',
            "note": f"ffmpeg binary: {'found' if ffmpeg_ok else 'NOT FOUND - install from https://ffmpeg.org'}",
        },
        {
            "name": "Cloud / S3 (boto3)",
            "ok": cloud_ok,
            "what": "Read documents directly from s3://bucket/key URIs",
            "install": 'pip install "aksharamd[cloud]"',
            "note": None,
        },
        {
            "name": "LibreOffice (system)",
            "ok": lo_ok,
            "what": "Parse legacy .doc and .ppt Office files",
            "install": "https://www.libreoffice.org  (OS-level install, not pip)",
            "note": None,
        },
        {
            "name": "Pandoc (system)",
            "ok": pandoc_ok,
            "what": "Parse AsciiDoc, Org-mode, Textile, MediaWiki, DocBook, man/roff",
            "install": "https://pandoc.org/installing.html  (OS-level install, not pip)",
            "note": None,
        },
        {
            "name": "Math OCR (pix2tex)",
            "ok": math_ok,
            "what": "Recover math equations from PDFs with unembedded font maps (LaTeX output)",
            "install": 'pip install "aksharamd[math]"',
            "note": "Requires PyTorch, downloads ~100 MB model on first run",
        },
    ]


_AUDIO_TYPES = frozenset({"mp3", "wav", "m4a", "ogg", "flac", "mp4", "webm", "opus", "aac"})


def _build_upgrade_hints(m) -> list[dict]:
    """Return hints for optional deps that would improve the result of this specific compilation.

    Each hint has: desc (plain-English why), cmd (exact install command), note (optional caveat).
    Only returns hints where the feature is missing AND would directly help with this document.
    """
    import importlib.util

    hints = []
    image_pages = m.image_pages or 0

    if m.file_type == "pdf" and image_pages > 0:
        # Only suggest OCR when vision is also absent — if [vision] is installed,
        # Surya handles image pages without needing a Tesseract binary.
        if m.ocr_available is False and m.vision_available is not True:
            hints.append({
                "desc": f"{image_pages} image-only page(s) were skipped - OCR is not installed",
                "cmd": 'pip install "aksharamd[ocr]"',
                "note": "Also install Tesseract 5+ at the OS level: https://github.com/tesseract-ocr/tesseract",
            })
        if m.vision_available is False:
            if m.ocr_available is True:
                desc = "Image pages extracted as flat text - table column structure may be lost"
            else:
                desc = "No binary needed: installs Surya neural OCR + table reconstruction for image pages"
            hints.append({
                "desc": desc,
                "cmd": 'pip install "aksharamd[vision]"',
                "note": "Requires PyTorch, ~3 GB model download on first run, air-gapped caching supported",
            })

    if m.file_type in _AUDIO_TYPES and importlib.util.find_spec("whisper") is None:
        hints.append({
            "desc": "Audio transcription requires Whisper - file was not transcribed",
            "cmd": 'pip install "aksharamd[audio]"',
            "note": "Also install ffmpeg on PATH: https://ffmpeg.org",
        })

    # Math hint: show when PDF has math pages but pix2tex is absent.
    # We detect this via pdf_math_available=False AND pdf_math_equations=0 but the
    # document has pages where fitz extracted very little (proxy for math-heavy content).
    if (
        m.file_type == "pdf"
        and importlib.util.find_spec("pix2tex") is None
        and getattr(m, "math_available", None) is False
    ):
        hints.append({
            "desc": "PDF may contain math equations in unembedded fonts that could not be extracted",
            "cmd": 'pip install "aksharamd[math]"',
            "note": "Recovers LaTeX equations via pix2tex; ~100 MB model download on first run",
        })

    return hints


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


class _AksharaMDGroup(click.Group):
    """Click Group that gives a helpful error when a file path is passed without a subcommand."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            name = args[0] if args else ""
            looks_like_source = (
                name.startswith(("http://", "https://"))
                or "." in name
                or "/" in name
                or "\\" in name
            )
            if looks_like_source:
                raise click.UsageError(
                    f"'{name}' is not a subcommand.\n\n"
                    f"  To compile:   aksharamd compile {name}\n"
                    f"  To validate:  aksharamd validate {name}\n\n"
                    f"Run 'aksharamd --help' for all available commands.",
                    ctx=ctx,
                )
            raise


@click.group(cls=_AksharaMDGroup)
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

    if not quiet:
        _show_first_run_onboarding()

    if quiet:
        ctx = compiler.compile(source)
    else:
        with _LiveProgress(source) as lp:
            ctx = compiler.compile(source, on_stage=lp.update)
        console.print()

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
            panel_title = "[bold red]Compilation Complete - Review Warnings[/]"
        else:
            score_str = f"[bold red]{score}/100  {band}[/]"
            panel_color = "red"
            panel_title = "[bold red]Compilation Complete - Poor Extraction[/]"

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
                title=f"[bold]Extraction Quality  {score}/100 - {band}[/]",
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

        # Show upgrade hints for optional deps that would improve this specific result
        hints = _build_upgrade_hints(m)
        if hints:
            hint_lines = []
            for h in hints:
                hint_lines.append(f"  {escape(h['desc'])}")
                hint_lines.append(f"  [bold cyan]  {h['cmd']}[/]")
                if h.get("note"):
                    hint_lines.append(f"  [dim]  {h['note']}[/]")
                hint_lines.append("")
            hint_lines.append("  [dim]Run [bold]aksharamd doctor[/bold] to see all optional features.[/dim]")
            console.print(Panel(
                "\n".join(hint_lines).rstrip(),
                title="[bold blue]Optional extras that would improve this result[/]",
                border_style="blue",
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
def doctor():
    """Check which optional features are installed and show install commands for missing ones."""
    deps = _check_optional_deps()

    t = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
    t.add_column("Optional Feature", min_width=22)
    t.add_column("Status", justify="center", min_width=13)
    t.add_column("What it enables")
    t.add_column("Install command / URL", min_width=36)

    all_ok = True
    for dep in deps:
        if dep["ok"]:
            status = "[bold green] installed [/]"
            install_cell = "[dim]-[/]"
        else:
            status = "[bold red]  missing  [/]"
            install_cell = dep["install"]
            all_ok = False
        note = f"\n  [dim]{dep['note']}[/]" if dep.get("note") and not dep["ok"] else ""
        t.add_row(dep["name"], status, dep["what"], install_cell + note)

    border = "green" if all_ok else "blue"
    title = "[bold green]All optional features installed[/]" if all_ok else "[bold]AksharaMD - Optional Features[/]"
    console.print(Panel(t, title=title, border_style=border))

    if not all_ok:
        missing = [d for d in deps if not d["ok"]]
        console.print(
            f"[dim]{len(missing)} optional feature(s) not installed. "
            "Install only the ones you need - none are required for core usage.[/dim]"
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
