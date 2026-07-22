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
        "Word, Excel, PowerPoint, HTML, EPUB, email, archives, and more — "
        "40+ document categories, 118 registered extensions, with no additional setup.\n\n",
        style="dim",
    )
    body.append("For harder document types, install only what you need:\n\n")

    from rich.console import ConsoleRenderable
    lines: list[ConsoleRenderable] = [body, t, Text()]

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
    """Click argument type that accepts a local file path, http(s)://, or s3:// URI."""
    name = "source"

    def convert(self, value, param, ctx):
        if value.startswith(("http://", "https://", "s3://")):
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
    if source.startswith(("http://", "https://", "s3://")):
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
                name.startswith(("http://", "https://", "s3://"))
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
@click.option(
    "--min-readiness-score", "min_readiness_score", type=int, default=None,
    metavar="INTEGER",
    help=(
        "Exit non-zero if the readiness score is below this threshold. "
        "Output files are still written. Useful as a CI/CD ingestion gate — "
        "e.g. --min-readiness-score 70 blocks low-quality documents from entering "
        "your vector store."
    ),
)
@click.option(
    "--json", "output_json", is_flag=True,
    help=(
        "Print a single JSON object to stdout instead of Rich panels. "
        "Suppresses all progress output. Compatible with --min-readiness-score."
    ),
)
@click.option(
    "--chunk-size", "chunk_size", type=int, default=512, show_default=True,
    metavar="INTEGER",
    help=(
        "Maximum tokens per chunk. Tune for your embedding model's context window. "
        "Default 512 preserves current behaviour."
    ),
)
@click.option(
    "--chunk-overlap", "chunk_overlap", type=int, default=0, show_default=True,
    metavar="INTEGER",
    help=(
        "Tokens of overlap carried from the end of one chunk into the start of the next. "
        "Must be less than --chunk-size. Default 0 preserves current behaviour."
    ),
)
@click.option(
    "--safe-mode", "safe_mode", is_flag=True, default=False,
    help=(
        "Restrict parsing to safe, deterministic operations only. "
        "Disables: URL/S3 fetching, LibreOffice/Pandoc subprocesses, "
        "Whisper ML inference, and OCR. Use when processing untrusted input."
    ),
)
@click.option(
    "--package", "run_package", is_flag=True, default=False,
    help=(
        "Generate a document package alongside standard output. "
        "Writes tables/, images/, regions/, package_plan.json, and token_report.json."
    ),
)
@click.option(
    "--package-mode", "package_mode",
    type=click.Choice(["text_first", "fidelity_first", "adaptive"], case_sensitive=False),
    default="adaptive", show_default=True,
    help="Package representation strategy. Requires --package.",
)
@click.option(
    "--ocr-backend", "ocr_backend",
    type=click.Choice(["tesseract", "unlimited_ocr"], case_sensitive=False),
    default="tesseract", show_default=True,
    help=(
        "OCR backend for pages classified as needing OCR. "
        "'unlimited_ocr' requires a CUDA-capable NVIDIA GPU with bfloat16 "
        "support, sufficient VRAM, and the model installed locally."
    ),
)
def compile(
    source: str,
    output: str,
    quiet: bool,
    timings: bool,
    verbose: bool,
    min_readiness_score: int | None,
    output_json: bool,
    chunk_size: int,
    chunk_overlap: int,
    safe_mode: bool,
    run_package: bool,
    package_mode: str,
    ocr_backend: str,
):
    """Compile a document or URL into AI-optimized Markdown, JSON, and chunks."""
    import json as _json

    _setup_logging(verbose)

    # --json implies quiet (suppress all Rich output)
    _suppress_rich = quiet or output_json

    # ── OCR backend availability probe ────────────────────────────────────
    # PR 94c: fail-early when a non-default backend is explicitly selected
    # but is not available on this machine. NO silent fallback — the user
    # asked for a specific backend and deserves an actionable error if the
    # environment cannot satisfy the request. The default value
    # ("tesseract") skips this probe to preserve current behaviour: any
    # Tesseract-availability issues are still handled per-page by the
    # existing _ocr_available()/OCR_UNAVAILABLE_MSG path in pdf.py.
    _ocr_backend_normalized = (ocr_backend or "tesseract").lower()
    if _ocr_backend_normalized != "tesseract":
        from aksharamd.plugins.ocr_backends import get_backend as _get_backend
        try:
            _probe_backend = _get_backend(_ocr_backend_normalized)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        _avail = _probe_backend.availability()
        if not _avail.is_available:
            msg = (
                f"OCR backend {_ocr_backend_normalized!r} unavailable: "
                f"{_avail.reason}"
            )
            if not _avail.hardware_compatible:
                msg += "  (Hardware requirements not met.)"
            elif not _avail.model_installed:
                msg += "  (Backend components not installed.)"
            raise click.ClickException(msg)

    file_output = str(Path(output) / _output_stem(source))
    try:
        compiler = Compiler(
            output_dir=file_output,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            safe_mode=safe_mode,
            ocr_backend=_ocr_backend_normalized,
        )
    except ValueError as exc:
        if output_json:
            import json as _json
            click.echo(_json.dumps({"error": str(exc)}))
        else:
            console.print(f"[red]Error:[/red] {exc}")
        raise SystemExit(1) from exc

    if not _suppress_rich:
        _show_first_run_onboarding()

    if run_package:
        from .packaging import PackageMode, PackageProfile
        profile = PackageProfile(mode=PackageMode(package_mode.lower()))
        if _suppress_rich:
            ctx = compiler.compile_package(source, profile=profile)
        else:
            with _LiveProgress(source) as lp:
                ctx = compiler.compile_package(source, profile=profile, on_stage=lp.update)
            console.print()
    else:
        if _suppress_rich:
            ctx = compiler.compile(source)
        else:
            with _LiveProgress(source) as lp:
                ctx = compiler.compile(source, on_stage=lp.update)
            console.print()

    # Evaluate readiness threshold (only when manifest was produced)
    _below_threshold = (
        min_readiness_score is not None
        and ctx.manifest is not None
        and ctx.manifest.readiness_score < min_readiness_score
    )

    # ── JSON output mode ───────────────────────────────────────────────────────
    if output_json:
        m = ctx.manifest
        if m:
            warning_codes = [w.code for w in ctx.validation.warnings]
            error_msgs = [f"[{e.code}] {e.message}" for e in ctx.validation.errors]
            result: dict = {
                "success": not bool(ctx.validation.errors) and not _below_threshold,
                "source": m.source,
                "output_dir": file_output,
                "readiness_score": m.readiness_score,
                "quality_band": m.quality_band,
                "scoring_policy_version": m.scoring_policy_version,
                "deductions": m.deductions,
                "informational": m.informational,
                "warning_codes": warning_codes,
                "errors": error_msgs,
                "chunks": m.chunks,
                "chunk_size": m.chunk_size,
                "chunk_overlap": m.chunk_overlap,
                "pages": m.pages,
                "optimized_tokens": m.optimized_tokens,
                "elapsed_seconds": m.elapsed_seconds,
            }
        else:
            warning_codes = [w.code for w in ctx.validation.warnings]
            error_msgs = [f"[{e.code}] {e.message}" for e in ctx.validation.errors]
            result = {
                "success": False,
                "source": source,
                "output_dir": file_output,
                "readiness_score": None,
                "quality_band": None,
                "warning_codes": warning_codes,
                "errors": error_msgs,
                "chunks": None,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "pages": None,
                "optimized_tokens": None,
                "elapsed_seconds": None,
            }
        click.echo(_json.dumps(result))
        if ctx.validation.errors or _below_threshold:
            sys.exit(1)
        return

    # ── Rich output mode ───────────────────────────────────────────────────────
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

        # Show structured deductions (active, suppressed, and informational)
        active_deds = [d for d in m.deductions if not d.get("suppressed")]
        suppressed_deds = [d for d in m.deductions if d.get("suppressed")]
        if active_deds or suppressed_deds or m.informational:
            ded_lines = []
            for d in active_deds:
                penalty_str = f"-{d['penalty']}" if d["penalty"] else " 0"
                ded_lines.append(
                    f"  [red]{penalty_str:>4}[/]  [bold]{escape(d['rule_id'])}[/]  "
                    f"[dim]{escape(d['description'])}[/]"
                )
            for d in suppressed_deds:
                reason = d.get("suppression_reason", "")
                ded_lines.append(
                    f"  [dim]  ~~  {escape(d['rule_id'])}  "
                    f"suppressed — {escape(reason)}[/]"
                )
            for d in m.informational:
                ded_lines.append(
                    f"  [cyan]info[/]  [bold]{escape(d['rule_id'])}[/]  "
                    f"[dim]{escape(d['description'])}[/]"
                )
            console.print(Panel(
                "\n".join(ded_lines),
                title=f"[bold]Score Deductions  (policy v{escape(m.scoring_policy_version)})[/]",
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

        # Package summary (only when --package was used)
        if run_package and ctx.package_plan is not None:
            from .packaging.models import RepresentationType as _RT
            pkg = ctx.package_plan
            total_elems = len(pkg.elements)
            preserved = sum(1 for e in pkg.elements if e.representation != _RT.OMIT)
            in_default = sum(1 for e in pkg.elements if e.include_by_default)
            tables = sum(1 for e in pkg.elements if e.representation == _RT.STRUCTURED_TABLE)
            visual = sum(
                1 for e in pkg.elements
                if e.representation == _RT.IMAGE
                and e.source_kind.value in ("page_region", "page")
            )
            ref_only = sum(1 for e in pkg.elements if e.representation == _RT.REFERENCE_ONLY)
            sel_tokens = pkg.estimated_tokens
            unresolved = sum(
                1 for e in pkg.elements
                if e.source_kind.value in ("page_region", "page")
                and e.element_type == "table"
            )

            pt = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
            pt.add_column(style="bold")
            pt.add_column()
            pt.add_row("Package mode",          pkg.mode)
            pt.add_row("Planner version",        pkg.planner_version)
            pt.add_row("Elements preserved",     f"{preserved} / {total_elems}")
            pt.add_row("Default payload",        str(in_default))
            pt.add_row("Structured tables",      str(tables))
            pt.add_row("Visual fallbacks",       str(visual))
            pt.add_row("Reference-only assets",  str(ref_only))
            pt.add_row("Selected text tokens",   f"{sel_tokens:,}")
            if unresolved:
                pt.add_row("Unresolved warnings", f"[yellow]{unresolved}[/]  (no source PDF for crop)")
            console.print(Panel(pt, title="[bold]Document Package[/]", border_style="cyan"))

            if hasattr(ctx, 'package_payload') and ctx.package_payload is not None:
                payload = ctx.package_payload
                pt2 = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
                pt2.add_column(style="bold")
                pt2.add_column()
                pt2.add_row("Payload items", str(len(payload.items)))
                pt2.add_row("Actual text tokens", f"{payload.actual_text_token_count:,}")
                if payload.unresolved_element_ids:
                    pt2.add_row("Unresolved", f"[yellow]{len(payload.unresolved_element_ids)}[/]")
                console.print(Panel(pt2, title="[bold]LLM Payload[/]", border_style="cyan"))

    # When the pipeline fails before building a manifest (e.g. encrypted PDF),
    # still surface any actionable warnings so the user knows what to do.
    if not quiet and ctx.manifest is None and ctx.validation.warnings:
        for w in ctx.validation.warnings:
            console.print(f"[yellow]WARNING[/] [{w.code}] {escape(w.message)}")

    if ctx.validation.errors:
        for err in ctx.validation.errors:
            if not quiet:
                console.print(f"[red]ERROR[/] [{err.code}] {err.message}")
        sys.exit(1)

    # Readiness threshold gate (after validation errors, before success)
    if _below_threshold and ctx.manifest is not None:
        _m = ctx.manifest
        band = _m.quality_band or (
            "HIGH" if _m.readiness_score >= 85
            else "OK" if _m.readiness_score >= 70
            else "RISKY" if _m.readiness_score >= 50
            else "POOR"
        )
        warning_codes = [w.code for w in ctx.validation.warnings]
        if not quiet:
            console.print(
                f"\n[red]READINESS GATE FAILED[/]  "
                f"score [bold]{_m.readiness_score}/100[/] ({band}) "
                f"is below threshold [bold]{min_readiness_score}[/]\n"
                f"  Output directory:  {file_output}\n"
                f"  Warnings:          {', '.join(warning_codes) or 'none'}\n"
                f"  Raise the threshold or fix the extraction issues before ingesting."
            )
        sys.exit(1)


@main.command()
@click.argument("source", type=_SourceArg())
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help=(
        "Print a single JSON object to stdout instead of Rich panels. "
        "Human-readable output is suppressed. Exit code is preserved: 0 "
        "on validation success, 1 on validation failure. Compatible with "
        "the same top-level field names used by `compile --json` so "
        "downstream tooling can share a schema."
    ),
)
def validate(source: str, verbose: bool, output_json: bool):
    """Validate a document or URL without writing any output files."""
    _setup_logging(verbose)
    # compile_to_string runs the full pipeline (parse → validate → chunk) but
    # skips the export stage, so no files are written to disk.
    compiler = Compiler(output_dir=str(Path("output") / _output_stem(source)))
    _, ctx = compiler.compile_to_string(source)

    if output_json:
        import json as _json
        m = ctx.manifest
        warning_codes = [w.code for w in ctx.validation.warnings]
        error_msgs = [f"[{e.code}] {e.message}" for e in ctx.validation.errors]
        result: dict = {
            "success":                ctx.validation.passed,
            "source":                 (m.source if m else source),
            "readiness_score":        (m.readiness_score if m else None),
            "quality_band":           (m.quality_band if m else None),
            "scoring_policy_version": (m.scoring_policy_version if m else ""),
            "deductions":             (m.deductions if m else []),
            "informational":          (m.informational if m else []),
            "warning_codes":          warning_codes,
            "errors":                 error_msgs,
        }
        # stdout stays JSON-only; operational chatter (Rich panels, logs)
        # is not written in --json mode.
        click.echo(_json.dumps(result))
        sys.exit(0 if ctx.validation.passed else 1)

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
        label = source if source.startswith(("http://", "https://", "s3://")) else Path(source).name
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
@click.option("--fail-on-error", is_flag=True,
              help="Exit with non-zero status if any file fails to compile")
@click.option("--failure-report", default=None, metavar="FILE",
              help="Write a JSON list of failed files to FILE")
@click.option("--quiet", is_flag=True, help="Suppress progress output")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def corpus(source_dir: str, output: str | None, budget: int, dedup_threshold: float,
           fail_on_error: bool, failure_report: str | None, quiet: bool, verbose: bool):
    """Compile every supported file under SOURCE_DIR into token-budget chunks.

    Files are grouped by directory and packed greedily up to --budget tokens.
    Near-duplicate documents are skipped automatically via MinHash LSH.

    Example: aksharamd corpus ./docs/ --budget 60000 -o corpus_chunks.json
    """
    import json as _json

    _setup_logging(verbose)

    compiler = Compiler(output_dir=str(Path(source_dir) / ".aksharamd_cache"))

    if quiet:
        result = compiler.compile_corpus(
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

            result = compiler.compile_corpus(
                source_dir,
                token_budget=budget,
                dedup_threshold=dedup_threshold,
                on_file=_on_file,
            )

    chunks = result.chunks
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
            f"[green]{result.indexed} documents[/] packed into [bold]{len(chunks)} chunks[/], "
            f"[cyan]{total_tokens:,} total tokens[/]"
        )

        # Corpus summary row
        summary = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        summary.add_column("Processed", justify="right", style="green")
        summary.add_column("Indexed",   justify="right", style="green")
        summary.add_column("Low quality", justify="right", style="yellow")
        summary.add_column("Duplicates",  justify="right", style="yellow")
        summary.add_column("Failed",      justify="right", style="red")
        summary.add_column("Unsupported", justify="right", style="dim")
        summary.add_row(
            str(result.processed),
            str(result.indexed),
            str(len(result.low_quality)),
            str(result.skipped_duplicates),
            str(len(result.failed)),
            str(len(result.unsupported)),
        )
        console.print(summary)

        if result.failed:
            console.print(f"[red]{len(result.failed)} file(s) failed to compile.[/]  "
                          "Use --failure-report to save details.")

    if output:
        out_path = Path(output)
        out_path.write_text(_json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
        if not quiet:
            console.print(f"[dim]Chunks written to {out_path}[/]")

    all_drops = result.failed + result.low_quality + result.unsupported
    if failure_report and all_drops:
        fr_path = Path(failure_report)
        fr_path.write_text(_json.dumps(all_drops, indent=2, ensure_ascii=False), encoding="utf-8")
        if not quiet:
            console.print(f"[dim]Failure report written to {fr_path}[/]")

    if fail_on_error and result.failed:
        raise SystemExit(1)


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
@click.argument("output_path", type=click.Path(exists=True))
def show_manifest(output_path: str):
    """Print the manifest from a previous compilation.

    Accepts any of:

    \b
    - a `manifest.json` file directly,
    - a per-source output directory containing `manifest.json`,
    - a parent output directory whose immediate children hold exactly one
      `manifest.json` (auto-resolved).

    If multiple immediate children hold `manifest.json`, the command lists
    them and exits non-zero so the caller can pick.  Discovery is bounded
    to the supplied directory and its immediate children only.
    """
    p = Path(output_path)
    manifest = _resolve_manifest_path(p)
    if isinstance(manifest, Path):
        console.print_json(manifest.read_text())
        return
    # manifest is an error message string.
    console.print(f"[red]{manifest}[/]")
    sys.exit(1)


def _resolve_manifest_path(p: Path) -> Path | str:
    """Locate the manifest.json for a `show-manifest` invocation.

    Returns the resolved `Path` on success or a human-readable error
    string on failure.  Discovery is limited to `p` itself and its
    immediate children — no recursive scan.
    """
    if p.is_file():
        if p.name == "manifest.json":
            return p
        return (
            f"{p} is not a manifest.json file. "
            "Point at manifest.json directly, at the per-source output "
            "directory that contains it, or at the parent output directory."
        )
    direct = p / "manifest.json"
    if direct.exists():
        return direct
    # Immediate children only — do not descend deeper.
    candidates = [
        child / "manifest.json"
        for child in sorted(p.iterdir())
        if child.is_dir() and (child / "manifest.json").exists()
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        listing = "\n  - ".join(str(c.relative_to(p)) for c in candidates)
        return (
            f"{p} contains multiple manifest-bearing subdirectories. "
            f"Point at exactly one:\n  - {listing}"
        )
    return (
        f"No manifest.json found at {p} or in its immediate children. "
        "Run `aksharamd compile <source> -o <dir>` first, then point "
        "show-manifest at either the resulting per-source subdirectory "
        "or its parent."
    )


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


def _probe_ocr_backends() -> dict[str, dict]:
    """Probe every registered OCR backend and return a JSON-friendly
    map keyed by backend name.

    Each value has ``capabilities`` and ``availability`` sub-dicts
    matching the frozen ``doctor --json`` schema. All heavy imports
    stay lazy inside each backend's ``availability()`` — failing
    imports become structured availability output, not exceptions.
    """
    from dataclasses import asdict

    from aksharamd.plugins.ocr_backends import (
        available_backends,
        get_backend,
    )

    out: dict[str, dict] = {}
    for name in available_backends():
        try:
            backend = get_backend(name)
        except Exception as exc:  # pragma: no cover - defensive
            out[name] = {
                "capabilities": None,
                "availability": {
                    "is_available": False,
                    "reason": f"backend registry raised: {type(exc).__name__}: {exc}",
                    "hardware_compatible": None,
                    "model_installed": None,
                    "runnable_now": None,
                    "details": None,
                },
            }
            continue
        try:
            caps = backend.capabilities()
            caps_out = asdict(caps)
        except Exception as exc:  # pragma: no cover - defensive
            caps_out = {"error": f"capabilities() raised: {type(exc).__name__}: {exc}"}
        try:
            avail = backend.availability()
            avail_out = asdict(avail)
        except Exception as exc:
            # Reviewer's rule: a backend probe failure becomes
            # structured availability output, not an exception.
            avail_out = {
                "is_available": False,
                "reason": f"availability() raised: {type(exc).__name__}: {exc}",
                "hardware_compatible": None,
                "model_installed": None,
                "runnable_now": None,
                "details": None,
            }
        out[name] = {"capabilities": caps_out, "availability": avail_out}
    return out


@main.command()
@click.option("--strict", is_flag=True, default=False,
              help="Exit with code 1 if any optional feature is missing.")
@click.option("--json", "json_out", is_flag=True, default=False,
              help="Emit a single deterministic JSON object with python, "
                   "optional_dependencies, and ocr_backends sections. "
                   "Suppresses all Rich formatting.")
def doctor(strict: bool, json_out: bool):
    """Check system readiness: Python version, optional features, registered
    OCR backends, and supported format count."""
    import json
    import sys

    from aksharamd import __version__

    # ── Python version ────────────────────────────────────────────────────────
    py = sys.version_info
    py_str = f"{py.major}.{py.minor}.{py.micro}"
    py_ok = py >= (3, 11)

    # ── Optional features ─────────────────────────────────────────────────────
    deps = _check_optional_deps()
    all_ok = all(dep["ok"] for dep in deps)

    # ── OCR backends (registered) ─────────────────────────────────────────────
    # Registered != runnable. This section reports every backend that ships
    # with aksharamd and its independent readiness state.
    backends = _probe_ocr_backends()

    # ── Format coverage summary ───────────────────────────────────────────────
    import aksharamd.plugins.registry as _reg

    from .plugins import parsers as _parsers_pkg  # noqa: F401 — trigger registration
    n_parsers = len(_reg._parsers)

    if json_out:
        # Deterministic machine-readable output. No decorative strings,
        # no ANSI, only booleans / integers / nulls / plain strings.
        # The schema is stable — see docs.
        payload = {
            "python": {
                "version": py_str,
                "version_info": [py.major, py.minor, py.micro],
                "meets_minimum": py_ok,
                "minimum": "3.11",
            },
            "optional_dependencies": {
                dep["name"]: {
                    "installed": bool(dep["ok"]),
                    "purpose": dep["what"],
                    "install_command": dep["install"],
                    "note": dep.get("note"),
                }
                for dep in deps
            },
            "ocr_backends": backends,
            "registered_format_extensions": n_parsers,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        if strict and not (all_ok and py_ok):
            raise SystemExit(1)
        return

    py_status = "[bold green] ok [/]" if py_ok else "[bold red] too old [/]"
    py_note = "" if py_ok else "  [dim]AksharaMD requires Python >=3.11[/]"

    console.print(
        Panel(
            f"[bold]AksharaMD[/] v{__version__}   "
            f"Python {py_str} {py_status}{py_note}",
            border_style="green" if py_ok else "red",
        )
    )

    t = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
    t.add_column("Optional Feature", min_width=22)
    t.add_column("Status", justify="center", min_width=13)
    t.add_column("What it enables")
    t.add_column("Install command / URL", min_width=36)

    for dep in deps:
        if dep["ok"]:
            status = "[bold green] installed [/]"
            install_cell = "[dim]-[/]"
        else:
            status = "[bold red]  missing  [/]"
            install_cell = dep["install"]
        note = f"\n  [dim]{dep['note']}[/]" if dep.get("note") and not dep["ok"] else ""
        t.add_row(dep["name"], status, dep["what"], install_cell + note)

    border = "green" if all_ok else "blue"
    title = "[bold green]All optional features installed[/]" if all_ok else "[bold]AksharaMD — Optional Features[/]"
    console.print(Panel(t, title=title, border_style=border))

    if not all_ok:
        missing = [d for d in deps if not d["ok"]]
        console.print(
            f"[dim]{len(missing)} optional feature(s) not installed. "
            "Install only the ones you need — none are required for core usage.[/dim]"
        )

    # ── OCR Backends (Registered) — Rich rendering ────────────────────────────
    bt = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
    bt.add_column("Backend", min_width=14)
    bt.add_column("Runnable now", justify="center", min_width=13)
    bt.add_column("Hardware", justify="center", min_width=10)
    bt.add_column("Model", justify="center", min_width=10)
    bt.add_column("Details / reason")

    for name, info in backends.items():
        avail = info["availability"]
        runnable = avail.get("runnable_now")
        hw = avail.get("hardware_compatible")
        mdl = avail.get("model_installed")

        def _mark(v):
            if v is True:
                return "[bold green] yes [/]"
            if v is False:
                return "[bold red] no [/]"
            return "[dim] — [/]"

        det = avail.get("details") or {}
        det_parts: list[str] = []
        if det.get("device_name"):
            det_parts.append(f"device={det['device_name']}")
        if det.get("vram_mib_total") is not None:
            det_parts.append(f"vram={det['vram_mib_total']}MiB")
        if det.get("min_vram_mib") is not None:
            det_parts.append(f"min_vram={det['min_vram_mib']}MiB")
        if det.get("bf16_supported") is not None:
            det_parts.append(f"bf16={det['bf16_supported']}")
        if det.get("model_snapshot_present") is not None:
            det_parts.append(f"snapshot_present={det['model_snapshot_present']}")
        if det.get("model_snapshot_verified") is not None:
            det_parts.append(f"snapshot_verified={det['model_snapshot_verified']}")
        det_line = ", ".join(det_parts)
        reason = avail.get("reason") or ""
        cell = det_line
        if reason:
            cell = (cell + "\n  [dim]" + reason + "[/]") if cell else f"[dim]{reason}[/]"
        if not cell:
            cell = "[dim]—[/]"

        bt.add_row(name, _mark(runnable), _mark(hw), _mark(mdl), cell)

    console.print(Panel(
        bt,
        title="[bold]OCR Backends (Registered)[/]",
        subtitle="[dim]Registered != runnable. Use --ocr-backend to opt in explicitly.[/]",
        border_style="blue",
    ))

    console.print(
        f"[dim]Registered format extensions: [bold]{n_parsers}[/bold]  "
        "(run [bold]aksharamd formats[/bold] for the full list)[/dim]"
    )

    if strict and not (all_ok and py_ok):
        raise SystemExit(1)


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


# ── Local index ────────────────────────────────────────────────────────────────

def _require_index_extra() -> None:
    """Exit with an install hint if the [index] extra packages are missing."""
    import importlib.util

    missing = [
        pkg for pkg in ("watchdog", "chromadb", "sentence_transformers")
        if importlib.util.find_spec(pkg) is None
    ]
    if missing:
        console.print(
            f"[red]Missing packages for the index feature: {', '.join(missing)}[/red]\n"
            '[dim]Install with: [bold]pip install "aksharamd[index]"[/bold][/dim]'
        )
        raise SystemExit(1)


def _get_index_config(index_dir: str | None):
    from aksharamd.index import IndexConfig
    if index_dir:
        return IndexConfig(index_dir=Path(index_dir))
    return IndexConfig()


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, dir_okay=True, resolve_path=True))
@click.option("--model", "embedding_model", default="all-MiniLM-L6-v2", show_default=True,
              help="Embedding model name, or 'ollama:<model>' for a local Ollama server.")
@click.option("--threshold", default=None, show_default=True, type=int,
              help="Minimum readiness score (0-100) required to index a document (default: 70).")
@click.option("--index-dir", default=None, metavar="DIR",
              help="Index storage directory (default: ~/.aksharamd/index/).")
def watch(folder: str, embedding_model: str, threshold: int | None, index_dir: str | None) -> None:
    """Watch FOLDER and auto-index documents as they arrive.

    Compiles each new or changed file through AksharaMD, embeds the content
    blocks, and stores them in a local ChromaDB. All data stays on-device.

    Runs in the foreground — press Ctrl+C to stop. For background operation
    use your OS tools (launchd / systemd / nohup).
    """
    _require_index_extra()
    import threading

    from aksharamd.index import (
        EmbeddingConfigMismatch,
        InboxWatcher,
        IndexConfig,
        IndexQueue,
        VectorStore,
        get_embedder,
        process_file,
    )

    cfg = IndexConfig(
        index_dir=Path(index_dir) if index_dir else IndexConfig().index_dir,
        embedding_model=embedding_model,
        min_readiness_score=threshold if threshold is not None else IndexConfig().min_readiness_score,
    )

    console.print(Panel(
        f"[bold]Folder:[/bold]    {folder}\n"
        f"[bold]Index:[/bold]     {cfg.index_dir}\n"
        f"[bold]Threshold:[/bold] {cfg.min_readiness_score}/100 readiness\n"
        f"[bold]Embedder:[/bold]  {embedding_model}\n\n"
        "[dim]Press Ctrl+C to stop.[/dim]",
        title="[bold]AksharaMD Watch[/]",
        border_style="blue",
    ))

    queue = IndexQueue(cfg.db_path)

    # Load embedder first so we know the dimension before opening the store.
    console.print("[dim]Loading embedding model...[/dim] ", end="")
    try:
        embedder = get_embedder(embedding_model)
        dim = embedder.dimension  # force model load now so the watcher starts fast
        console.print("[green]ready[/green]")
    except Exception as exc:
        console.print(f"\n[red]Failed to load embedder:[/red] {exc}")
        raise SystemExit(1) from exc

    try:
        store = VectorStore(cfg.chromadb_path, embedding_model=embedding_model,
                            vector_dimension=dim, distance_metric=cfg.distance_metric)
    except EmbeddingConfigMismatch as exc:
        console.print(f"[red]Embedding config mismatch:[/red] {exc}")
        raise SystemExit(1) from exc

    stop_event = threading.Event()

    def _enqueue(path: str) -> bool:
        try:
            queued = queue.enqueue(path)
        except FileNotFoundError:
            return False
        if queued:
            console.print(f"  [cyan]+[/cyan] queued   {Path(path).name}")
        else:
            console.print(f"  [dim]~ skip    {Path(path).name} (unchanged)[/dim]")
        return queued

    def _worker_loop() -> None:
        while not stop_event.is_set():
            path = queue.dequeue()
            if path is None:
                stop_event.wait(timeout=1.0)
                continue
            name = Path(path).name
            console.print(f"  [yellow]>[/yellow] indexing {name}...")
            process_file(path, queue, store, embedder, cfg)
            cur_stats = queue.stats()
            jobs_done = cur_stats.get("done", 0)
            jobs_total = sum(cur_stats.values())
            finished = queue.list_all(status="done")
            if finished and finished[0].path == path:
                console.print(f"  [green]done[/green]     {name}  [{jobs_done}/{jobs_total}]")
            else:
                console.print(f"  [yellow]flagged[/yellow]  {name}  [{jobs_done}/{jobs_total}]")

    with InboxWatcher(Path(folder), _enqueue):
        worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        worker_thread.start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopping...[/dim]")
            stop_event.set()
            worker_thread.join(timeout=5)
            console.print("[dim]Stopped.[/dim]")


@main.group()
def index() -> None:
    """Manage the local document index."""


@index.command("status")
@click.option("--index-dir", default=None, metavar="DIR")
def index_status(index_dir: str | None) -> None:
    """Show queue and index statistics."""
    _require_index_extra()
    from aksharamd.index import IndexQueue, VectorStore

    cfg = _get_index_config(index_dir)
    queue = IndexQueue(cfg.db_path)
    store = VectorStore(cfg.chromadb_path)  # no embedding config — read-only stats

    q_stats = queue.stats()
    i_stats = store.stats()

    t = Table(box=box.ROUNDED, header_style="bold cyan", show_header=True)
    t.add_column("Metric")
    t.add_column("Value", justify="right")

    status_colors = {"done": "green", "error": "red", "low_quality": "yellow",
                     "pending": "cyan", "processing": "blue"}
    for status, count in sorted(q_stats.items()):
        color = status_colors.get(status, "white")
        t.add_row(f"Queue — [{color}]{status}[/{color}]", str(count))

    t.add_row("[bold]Index — files[/bold]", str(i_stats["total_files"]))
    t.add_row("[bold]Index — chunks[/bold]", str(i_stats["total_chunks"]))
    if i_stats.get("embedding_model", "unknown") != "unknown":
        t.add_row("[dim]Embedding model[/dim]", str(i_stats["embedding_model"]))
        t.add_row("[dim]Distance metric[/dim]", str(i_stats["distance_metric"]))
    t.add_row("[dim]Location[/dim]", str(cfg.index_dir))

    console.print(Panel(t, title="[bold]Index Status[/]", border_style="blue"))


@index.command("list")
@click.option("--status", default=None, metavar="STATUS",
              help="Filter by status: done, pending, error, low_quality")
@click.option("--index-dir", default=None, metavar="DIR")
def index_list(status: str | None, index_dir: str | None) -> None:
    """List files tracked by the index queue."""
    _require_index_extra()
    from aksharamd.index import IndexQueue

    cfg = _get_index_config(index_dir)
    queue = IndexQueue(cfg.db_path)
    jobs = queue.list_all(status=status)

    if not jobs:
        console.print("[dim]No files found.[/dim]")
        return

    t = Table(box=box.SIMPLE, header_style="bold cyan", show_header=True)
    t.add_column("File", min_width=30)
    t.add_column("Status", min_width=12)
    t.add_column("Score", justify="right")
    t.add_column("Chunks", justify="right")

    status_colors = {"done": "green", "error": "red", "low_quality": "yellow",
                     "pending": "cyan", "processing": "blue"}
    for job in jobs:
        color = status_colors.get(job.status, "white")
        t.add_row(
            Path(job.path).name,
            f"[{color}]{job.status}[/{color}]",
            str(job.readiness_score) if job.readiness_score is not None else "-",
            str(job.chunk_count) if job.chunk_count is not None else "-",
        )
    console.print(t)


@index.command("clear")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.option("--index-dir", default=None, metavar="DIR")
def index_clear(yes: bool, index_dir: str | None) -> None:
    """Remove all indexed documents and reset the queue."""
    if not yes:
        click.confirm("This will delete all indexed data. Continue?", abort=True)
    _require_index_extra()
    from aksharamd.index import IndexQueue, VectorStore

    cfg = _get_index_config(index_dir)
    IndexQueue(cfg.db_path).reset_all_jobs()
    VectorStore(cfg.chromadb_path).clear()
    console.print("[green]Index cleared.[/green]")


@index.command("search")
@click.argument("query")
@click.option("-n", "--results", default=5, show_default=True, type=int,
              help="Number of results to return.")
@click.option("--index-dir", default=None, metavar="DIR")
def index_search(query: str, results: int, index_dir: str | None) -> None:
    """Search the local document index with a natural-language query."""
    _require_index_extra()
    from aksharamd.index import EmbeddingConfigMismatch, VectorStore, get_embedder

    cfg = _get_index_config(index_dir)
    store = VectorStore(cfg.chromadb_path)  # read-only check first

    if store.count() == 0:
        console.print(
            "[yellow]Index is empty.[/yellow]  "
            "Run [bold]aksharamd watch <folder>[/bold] to index documents first."
        )
        return

    embedder = get_embedder(cfg.embedding_model)
    try:
        store = VectorStore(cfg.chromadb_path, embedding_model=cfg.embedding_model,
                            vector_dimension=embedder.dimension,
                            distance_metric=cfg.distance_metric)
    except EmbeddingConfigMismatch as exc:
        console.print(f"[red]Embedding config mismatch:[/red] {exc}")
        raise SystemExit(1) from exc

    query_emb = embedder.embed([query])[0]
    hits = store.search(query_emb, n_results=results)

    if not hits:
        console.print("[dim]No results found.[/dim]")
        return

    console.print(f"\n[bold]Results for:[/bold] {query}\n")
    for i, hit in enumerate(hits, 1):
        meta = hit["metadata"]
        source = Path(meta.get("source", "unknown")).name
        page = meta.get("page", "?")
        btype = meta.get("block_type", "?")
        score = meta.get("readiness_score", "?")
        snippet = hit["text"][:300] + ("..." if len(hit["text"]) > 300 else "")
        console.print(
            f"[bold cyan][{i}][/bold cyan] [dim]{source}[/dim]"
            f"  p.{page} · {btype} · readiness {score}/100"
        )
        console.print(f"    {snippet}\n")


@main.command("build-payload")
@click.argument("package_dir", type=click.Path(exists=True))
@click.option(
    "--mode", "package_mode",
    type=click.Choice(["text_first", "fidelity_first", "adaptive"], case_sensitive=False),
    default="adaptive", show_default=True,
    help="Package mode (used for profile defaults).",
)
def build_payload(package_dir: str, package_mode: str) -> None:
    """Build an LLM payload from an existing document package."""

    from .packaging import PackageMode, PackageProfile
    from .packaging.models import DocumentPackagePlan
    from .packaging.payload_builder import build_llm_payload

    pkg_dir = Path(package_dir)
    plan_path = pkg_dir / "package_plan.json"
    doc_path = pkg_dir / "document.json"

    if not plan_path.exists():
        console.print(f"[red]No package_plan.json found in {package_dir}[/]")
        raise SystemExit(1)

    try:
        plan = DocumentPackagePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Failed to load package_plan.json: {exc}[/]")
        raise SystemExit(1) from exc

    if not doc_path.exists():
        console.print(f"[red]No document.json found in {package_dir}[/]")
        raise SystemExit(1)

    try:
        from .models.document import Document
        doc = Document.model_validate_json(doc_path.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Failed to load document.json: {exc}[/]")
        raise SystemExit(1) from exc

    # Load existing asset refs if available
    from .packaging.models import PackageAssetReference
    asset_refs: list[PackageAssetReference] = []

    profile = PackageProfile(mode=PackageMode(package_mode.lower()))

    try:
        payload = build_llm_payload(plan, doc, pkg_dir, asset_refs, profile)
    except Exception as exc:
        console.print(f"[red]Failed to build payload: {exc}[/]")
        raise SystemExit(1) from exc

    out_path = pkg_dir / "llm_payload.json"
    out_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"[green]Payload written to[/] {out_path}")
    console.print(f"  Items: {len(payload.items)}")
    console.print(f"  Actual text tokens: {payload.actual_text_token_count:,}")
    if payload.unresolved_element_ids:
        console.print(f"  [yellow]Unresolved: {len(payload.unresolved_element_ids)}[/]")


@main.command("benchmark-representations")
@click.argument("corpus_manifest", type=click.Path(exists=True))
@click.option("--split", required=True, type=click.Choice(["dev", "held_out"]),
              help="Required: which corpus split to run. Mixed-split runs are not allowed.")
@click.option("--output-dir", default="benchmarks/document_package/results",
              show_default=True, type=click.Path())
@click.option("--max-documents", default=None, type=int,
              help="Limit number of documents (for testing the harness)")
def benchmark_representations(
    corpus_manifest: str,
    split: str,
    output_dir: str,
    max_documents: int | None,
) -> None:
    """Run representation-efficiency benchmark on a corpus split.

    Generates baseline and candidate representations for each document
    and writes metric files to --output-dir/<split>_<timestamp>/.
    """
    import hashlib
    import json
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent.parent))

    from benchmarks.document_package.harness import run_corpus, write_benchmark_results
    from benchmarks.document_package.schema import CorpusEntry

    manifest_path = Path(corpus_manifest)
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    corpus_entries = {
        e.document_id: e
        for e in (CorpusEntry.model_validate(x) for x in manifest_data)
        if e.split.value == split
    }

    with console.status(f"Running {split} corpus from {corpus_manifest}..."):
        captures, run_dir = run_corpus(
            corpus_manifest_path=corpus_manifest,
            output_dir=output_dir,
            split=split,
            max_documents=max_documents,
        )

        manifest_checksum = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        failed_ids = [
            e.document_id for e in corpus_entries.values()
            if not any(c.document_id == e.document_id for c in captures)
        ]

        write_benchmark_results(captures, run_dir, corpus_entries, failed_ids, manifest_checksum)

    console.print(f"[green]Results written to {run_dir}[/]")
    console.print(f"Processed: {len(captures)} / {len(corpus_entries)} documents")
    if failed_ids:
        console.print(f"[yellow]Failed: {', '.join(failed_ids)}[/]")


@main.command("inspect-payload")
@click.argument("payload_path", type=click.Path(exists=True))
def inspect_payload(payload_path: str) -> None:
    """Print a summary of an LLM payload file."""
    from .packaging.payload import LLMPayload

    try:
        payload = LLMPayload.model_validate_json(Path(payload_path).read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Failed to load payload: {exc}[/]")
        raise SystemExit(1) from exc


    # Count by type
    type_counts: dict[str, int] = {}
    for item in payload.items:
        key = item.content_type.value
        type_counts[key] = type_counts.get(key, 0) + 1

    pt = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    pt.add_column(style="bold")
    pt.add_column()
    pt.add_row("Document ID", payload.document_id)
    pt.add_row("Package mode", payload.package_mode)
    pt.add_row("Planner version", payload.planner_version)
    pt.add_row("Schema version", payload.payload_schema_version)
    pt.add_row("Total items", str(len(payload.items)))
    pt.add_row("Planned tokens", f"{payload.planned_text_tokens:,}")
    pt.add_row("Actual text tokens", f"{payload.actual_text_token_count:,}")
    pt.add_row("Token delta", str(payload.token_delta))
    for ct, count in sorted(type_counts.items()):
        pt.add_row(f"  {ct}", str(count))
    if payload.unresolved_element_ids:
        pt.add_row("[yellow]Unresolved[/]", str(len(payload.unresolved_element_ids)))
    console.print(Panel(pt, title="[bold]LLM Payload Summary[/]", border_style="cyan"))


# ── Models lifecycle (PR 98) ──────────────────────────────────────────────
#
# The four ``aksharamd models`` subcommands cover install / verify / status /
# remove for the Unlimited-OCR snapshot. All heavy work lives in
# ``aksharamd.plugins.ocr_backends.unlimited_ocr.models``; the CLI shell only
# renders, prompts, and maps outcomes to exit codes. NO heavy imports here —
# ``import aksharamd.cli`` must stay cheap.


_KNOWN_MODEL_NAMES = frozenset({"unlimited_ocr"})


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    if n >= 1024 ** 3:
        return f"{n / (1024 ** 3):.2f} GiB"
    if n >= 1024 ** 2:
        return f"{n / (1024 ** 2):.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n} B"


@main.group()
def models() -> None:
    """Install, verify, inspect, or remove local OCR models."""


def _validate_model_name_or_exit(model_name: str) -> None:
    """Reject anything except the single supported model with exit 2."""
    if model_name in _KNOWN_MODEL_NAMES:
        return
    # Print a helpful message on stderr and exit with code 2. Using
    # ClickException keeps behaviour consistent with click's own
    # usage errors.
    known = ", ".join(sorted(_KNOWN_MODEL_NAMES))
    click.echo(
        f"Error: unknown model {model_name!r}. Known models: {known}.",
        err=True,
    )
    raise SystemExit(2)


@models.command("status")
@click.argument("model_name")
@click.option("--json", "json_out", is_flag=True, default=False,
              help="Emit a deterministic JSON object; no ANSI, no Rich.")
def models_status_cmd(model_name: str, json_out: bool) -> None:
    """Show installation and verification status for MODEL_NAME."""
    _validate_model_name_or_exit(model_name)
    # Lazy import — keeps ``models --help`` free of the lifecycle module.
    from aksharamd.plugins.ocr_backends.unlimited_ocr.models import (
        get_model_status,
        status_to_dict,
    )
    status = get_model_status()

    if json_out:
        import json as _json
        # sort_keys=True for deterministic output; no Rich, no ANSI.
        print(_json.dumps(status_to_dict(status), indent=2, sort_keys=True))
        return

    console.print(Panel(
        (
            f"[bold]{status.name}[/]  "
            f"repo=[cyan]{status.repo_id}[/]  "
            f"revision=[dim]{status.revision[:12]}[/]"
        ),
        border_style="blue",
    ))
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column(style="bold")
    t.add_column()
    t.add_row("Snapshot present", "yes" if status.snapshot_present else "no")
    t.add_row("Manifest present", "yes" if status.manifest_present else "no")
    t.add_row("Byte verified", "yes" if status.byte_verified else "no")
    hw = (
        "yes" if status.hardware_compatible is True
        else "no" if status.hardware_compatible is False
        else "unknown"
    )
    t.add_row("Hardware compatible", hw)
    t.add_row("Runnable now", "yes" if status.runnable_now else "no")
    t.add_row(
        "Expected download size",
        f"{_fmt_bytes(status.download_size_bytes)} "
        f"(source: {status.download_size_source})",
    )
    if status.snapshot_path:
        t.add_row("Snapshot path", str(status.snapshot_path))
    if status.receipt_path:
        t.add_row("Receipt path", str(status.receipt_path))
    if status.reason:
        t.add_row("Reason", status.reason)
    console.print(t)


@models.command("install")
@click.argument("model_name")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip the interactive confirmation prompt.")
def models_install_cmd(model_name: str, yes: bool) -> None:
    """Download and byte-verify MODEL_NAME."""
    _validate_model_name_or_exit(model_name)
    from aksharamd.plugins.ocr_backends.unlimited_ocr.models import (
        get_model_info,
        install_model,
    )

    info = get_model_info()
    # Preview panel — always printed regardless of --yes so a user
    # can review what --yes just committed them to.
    console.print(Panel(
        (
            f"[bold]Install {info.name}[/]\n"
            f"  Repo:       [cyan]{info.repo_id}[/]\n"
            f"  Revision:   [dim]{info.revision}[/]\n"
            f"  Size:       {_fmt_bytes(info.download_size_bytes)} "
            f"(source: {info.download_size_source})\n"
            f"  Destination:[dim] {info.snapshot_path or '(HF cache)'}[/]\n\n"
            f"[dim]{info.license_notice}[/]"
        ),
        title="[bold]Model install preview[/]",
        border_style="cyan",
    ))
    if not yes:
        if not click.confirm(
            "Proceed with the download?", default=False,
        ):
            click.echo("aborted by user", err=True)
            raise SystemExit(1)

    def _cb(phase: str) -> None:
        console.print(f"[dim]-> {phase}[/dim]")

    outcome = install_model(assume_yes=yes, progress_callback=_cb)
    if outcome.exit_code == 0:
        console.print(f"[green]{outcome.note}[/green]")
    else:
        click.echo(f"install failed: {outcome.note}", err=True)
    raise SystemExit(outcome.exit_code)


@models.command("verify")
@click.argument("model_name")
@click.option("--json", "json_out", is_flag=True, default=False,
              help="Emit a deterministic JSON object; no ANSI, no Rich.")
def models_verify_cmd(model_name: str, json_out: bool) -> None:
    """Byte-verify the installed snapshot for MODEL_NAME. No network."""
    _validate_model_name_or_exit(model_name)
    from aksharamd.plugins.ocr_backends.unlimited_ocr.models import (
        verify_model,
        verify_outcome_to_dict,
    )
    out = verify_model()
    if json_out:
        import json as _json
        print(_json.dumps(verify_outcome_to_dict(out), indent=2, sort_keys=True))
    else:
        if out.ok:
            console.print(f"[green]{out.note}[/green]")
            console.print(
                f"[dim]{len(out.files_hashed)} files hashed; receipt at "
                f"{out.receipt_path}[/dim]"
            )
        else:
            click.echo(f"verification failed: {out.note}", err=True)
    raise SystemExit(out.exit_code)


@models.command("remove")
@click.argument("model_name")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip the interactive confirmation prompt.")
@click.option("--clear-runtime-cache", is_flag=True, default=False,
              help="Also clear the small aksharamd-managed safe-size cache.")
def models_remove_cmd(
    model_name: str,
    yes: bool,
    clear_runtime_cache: bool,
) -> None:
    """Remove the local snapshot for MODEL_NAME."""
    _validate_model_name_or_exit(model_name)
    from aksharamd.plugins.ocr_backends.unlimited_ocr.models import (
        get_model_info,
        remove_model,
    )

    info = get_model_info()
    console.print(Panel(
        (
            f"[bold]Remove {info.name}[/]\n"
            f"  Repo:      [cyan]{info.repo_id}[/]\n"
            f"  Revision:  [dim]{info.revision}[/]\n"
            f"  Snapshot:  [dim]{info.snapshot_path or '(not present)'}[/]\n"
            f"  Runtime cache: "
            f"{'will be cleared' if clear_runtime_cache else 'kept'}"
        ),
        title="[bold]Model remove preview[/]",
        border_style="yellow",
    ))
    if not yes:
        if not click.confirm(
            "Proceed with the removal?", default=False,
        ):
            click.echo("aborted by user", err=True)
            raise SystemExit(1)

    outcome = remove_model(clear_runtime_cache=clear_runtime_cache)
    if outcome.exit_code == 0:
        console.print(f"[green]{outcome.note}[/green]")
        if outcome.runtime_cache_cleared:
            console.print("[dim]runtime cache cleared[/dim]")
    else:
        click.echo(f"remove failed: {outcome.note}", err=True)
    raise SystemExit(outcome.exit_code)
