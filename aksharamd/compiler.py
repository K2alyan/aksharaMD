from __future__ import annotations
import time
from pathlib import Path

import filetype

from .context import CompilationContext
from .models.manifest import Manifest
from .plugins import registry
from .plugins.base import (
    CleanerPlugin, OptimizerPlugin, ValidatorPlugin,
    ChunkerPlugin, ExporterPlugin,
)
from .scoring import compute_confidence
from .utils import count_tokens
from . import ledger as _ledger

# Import all built-in plugins to trigger registration
from .plugins import parsers as _parsers_pkg
from .plugins.cleaners import default as _cleaner_pkg
from .plugins.optimizers import token as _optimizer_pkg
from .plugins.validators import structure as _validator_pkg
from .plugins.chunkers import semantic as _chunker_pkg
from .plugins.exporters import markdown as _md_exporter_pkg
from .plugins.exporters import json_exporter as _json_exporter_pkg


def _detect_file_type(path: str) -> str:
    p = Path(path)
    ext = p.suffix.lstrip(".").lower()
    if ext:
        return ext
    kind = filetype.guess(path)
    return kind.extension if kind else "txt"


class Compiler:
    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir

    # ── Public API ─────────────────────────────────────────────────────────────

    def compile(self, source: str) -> CompilationContext:
        """Full compilation: parse → optimise → export to disk. Returns context."""
        ctx, stage_timings, t0 = self._run_pipeline(source)

        # Export to disk
        with _StageTimer(stage_timings, "export"):
            for plugin in registry.get_plugins_of_type(ExporterPlugin):
                ctx = plugin.execute(ctx)

        return self._finalise(ctx, stage_timings, t0)

    def compile_to_string(self, source: str) -> tuple[str, CompilationContext]:
        """Compile to a markdown string without writing any files to disk.

        Ideal for MCP server and programmatic usage where disk I/O is unwanted.
        Returns (markdown_text, ctx) — ctx.manifest has the full stats.
        """
        from .plugins.exporters.markdown import _block_to_md

        ctx, stage_timings, t0 = self._run_pipeline(source)

        if ctx.document:
            lines = [_block_to_md(b) for b in ctx.document.blocks]
            text = "\n\n".join(ln for ln in lines if ln)
        else:
            text = ""

        return text, self._finalise(ctx, stage_timings, t0)

    # ── Internal pipeline ──────────────────────────────────────────────────────

    def _run_pipeline(
        self, source: str
    ) -> tuple[CompilationContext, dict[str, float], float]:
        """Stages 1-9: detect → parse → clean → optimise → validate → chunk →
        tokenise → manifest → readiness score.  Does NOT write to disk."""
        t0 = time.perf_counter()
        stage_timings: dict[str, float] = {}
        ctx = CompilationContext(source=source, output_dir=self.output_dir)

        def timed(name: str) -> _StageTimer:
            return _StageTimer(stage_timings, name)

        # 1. Detect
        file_type = _detect_file_type(source)

        # 2. Parse
        parser = registry.get_parser(file_type)
        if parser is None:
            ctx.error("NO_PARSER", f"No parser registered for file type: {file_type}")
            return ctx, stage_timings, t0
        with timed("parse"):
            ctx = parser.execute(ctx)
        if ctx.document is None:
            ctx.error("PARSE_FAILED", "Parser produced no document")
            return ctx, stage_timings, t0
        ctx.document = ctx.document.model_copy(update={"file_type": file_type})

        # 3. Clean
        with timed("clean"):
            for plugin in registry.get_plugins_of_type(CleanerPlugin):
                ctx = plugin.execute(ctx)

        # 4. Optimise
        with timed("optimize"):
            for plugin in registry.get_plugins_of_type(OptimizerPlugin):
                ctx = plugin.execute(ctx)

        # 5. Validate
        with timed("validate"):
            for plugin in registry.get_plugins_of_type(ValidatorPlugin):
                ctx = plugin.execute(ctx)

        # 6. Chunk
        with timed("chunk"):
            for plugin in registry.get_plugins_of_type(ChunkerPlugin):
                ctx = plugin.execute(ctx)

        # 7. Count tokens
        with timed("tokenize"):
            if ctx.document:
                optimized_text = " ".join(b.content for b in ctx.document.blocks)
                optimized_tokens = count_tokens(optimized_text)
            else:
                optimized_tokens = 0

        original_tokens = ctx.original_tokens or optimized_tokens
        reduction = (
            round((1 - optimized_tokens / original_tokens) * 100, 2)
            if original_tokens > 0 else 0.0
        )

        # 8. Package manifest
        doc = ctx.document
        images = sum(1 for b in doc.blocks if b.type.value == "image") if doc else 0
        tables = sum(1 for b in doc.blocks if b.type.value == "table") if doc else 0

        ctx.manifest = Manifest(
            source=source,
            file_type=file_type,
            pages=doc.pages if doc else 0,
            chunks=len(ctx.chunks),
            images=images,
            tables=tables,
            original_tokens=original_tokens,
            optimized_tokens=optimized_tokens,
            token_reduction_percent=reduction,
            duplicate_blocks_removed=ctx.duplicate_blocks_removed,
            headers_removed=ctx.headers_removed,
            footers_removed=ctx.footers_removed,
            elapsed_seconds=round(time.perf_counter() - t0, 3),
            stage_timings=stage_timings,
            warnings=[i.message for i in ctx.validation.warnings],
            errors=[i.message for i in ctx.validation.errors],
        )

        # 9. Extraction Confidence Score
        confidence = compute_confidence(ctx)
        ctx.manifest = ctx.manifest.model_copy(update={
            "readiness_score": confidence.score,
            "confidence_notes": confidence.notes,
        })

        return ctx, stage_timings, t0

    def _finalise(
        self,
        ctx: CompilationContext,
        stage_timings: dict[str, float],
        t0: float,
    ) -> CompilationContext:
        """Stamp final elapsed time and write ledger entry."""
        final_elapsed = round(time.perf_counter() - t0, 3)
        if ctx.manifest:
            ctx.manifest = ctx.manifest.model_copy(update={
                "elapsed_seconds": final_elapsed,
                "stage_timings": stage_timings,
            })
            _ledger.append_entry(
                source=ctx.manifest.source,
                file_type=ctx.manifest.file_type,
                original_tokens=ctx.manifest.original_tokens,
                optimized_tokens=ctx.manifest.optimized_tokens,
                elapsed_seconds=final_elapsed,
            )
        return ctx


class _StageTimer:
    """Context manager that records elapsed time for a named stage."""

    def __init__(self, store: dict[str, float], name: str):
        self._store = store
        self._name = name
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        self._store[self._name] = round(time.perf_counter() - self._start, 3)
