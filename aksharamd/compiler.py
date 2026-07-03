from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 500 MB default; override with AKSHARAMD_MAX_FILE_BYTES env var
_MAX_FILE_BYTES = int(os.environ.get("AKSHARAMD_MAX_FILE_BYTES", str(500 * 1024 * 1024)))

import filetype

from . import ledger as _ledger
from .context import CompilationContext
from .models.manifest import Manifest

# Import all built-in plugins to trigger registration (side-effect imports)
from .plugins import parsers as _parsers_pkg  # noqa: F401
from .plugins import registry
from .plugins.base import (
    ChunkerPlugin,
    CleanerPlugin,
    ExporterPlugin,
    OptimizerPlugin,
    ValidatorPlugin,
)
from .plugins.chunkers import semantic as _chunker_pkg  # noqa: F401
from .plugins.cleaners import default as _cleaner_pkg  # noqa: F401
from .plugins.exporters import json_exporter as _json_exporter_pkg  # noqa: F401
from .plugins.exporters import markdown as _md_exporter_pkg  # noqa: F401
from .plugins.optimizers import token as _optimizer_pkg  # noqa: F401
from .plugins.validators import structure as _validator_pkg  # noqa: F401
from .scoring import compute_confidence
from .utils import count_tokens


def _fetch_url_to_temp(url: str) -> str:
    """Download *url* to a NamedTemporaryFile; return the temp file path."""
    import ipaddress
    import mimetypes
    import socket
    import tempfile
    from urllib.parse import urlparse

    import requests

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme {parsed.scheme!r} is not allowed; use http or https.")

    hostname = parsed.hostname or ""
    try:
        resolved_ip = ipaddress.ip_address(socket.gethostbyname(hostname))
    except Exception as exc:
        raise ValueError(f"Could not resolve host {hostname!r}: {exc}") from exc

    if resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local:
        raise ValueError(
            f"Requests to private/internal addresses are not allowed (resolved to {resolved_ip})."
        )

    try:
        resp = requests.get(url, timeout=30, stream=True,
                            headers={"User-Agent": "AksharaMD/0.1"})
        resp.raise_for_status()
    except Exception as exc:
        raise ValueError(f"Failed to fetch {url!r}: {exc}") from exc

    # Prefer the URL path extension; fall back to Content-Type
    url_ext = Path(urlparse(url).path).suffix
    if url_ext and 2 <= len(url_ext) <= 6:
        ext = url_ext
    else:
        content_type = resp.headers.get("Content-Type", "text/html").split(";")[0].strip()
        ext = mimetypes.guess_extension(content_type) or ".html"
        # mimetypes gives odd results for common types on some platforms
        _CT_EXT_MAP = {"text/html": ".html", "application/pdf": ".pdf",
                       "text/plain": ".txt", "application/json": ".json"}
        ext = _CT_EXT_MAP.get(content_type, ext)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


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

    def compile_to_multimodal(self, source: str) -> tuple[list[dict], CompilationContext]:
        """Compile to an interleaved text+image content array for multimodal LLMs.

        Returns (content_array, ctx) where content_array is a list of Anthropic-compatible
        content dicts: {"type": "text", "text": ...} and {"type": "image", "source": {...}}.
        Images appear inline at their document position — Figure 4 is right there between the
        paragraphs that reference it, not appended at the end.
        """
        from .plugins.exporters.multimodal import build_multimodal_content

        ctx, stage_timings, t0 = self._run_pipeline(source)

        if ctx.document:
            content = build_multimodal_content(ctx.document)
        else:
            content = [{"type": "text", "text": ""}]

        return content, self._finalise(ctx, stage_timings, t0)

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

    def compile_corpus(
        self,
        source_dir: str,
        token_budget: int = 60_000,
        glob: str = "**/*",
        dedup_threshold: float = 0.5,
        max_bisect_depth: int = 3,
    ) -> list[dict]:
        """Compile every supported file under *source_dir* and pack the results into
        token-budget-aware groups ready for downstream LLM processing (e.g. Graphify).

        Files are grouped by their immediate parent directory first (keeping related
        artefacts together), then packed greedily into chunks up to *token_budget*.
        If a single document exceeds the budget it is placed alone.  Groups that
        exceed the budget are bisected recursively (up to *max_bisect_depth* times).

        Near-duplicate documents (Jaccard ≥ *dedup_threshold* across the whole
        corpus) are skipped automatically via MinHash LSH.

        Returns a list of corpus chunk dicts::

            [
              {
                "chunk_index": 0,
                "token_count": 4821,
                "documents": [
                  {
                    "source": "reports/q1.pdf",
                    "file_type": "pdf",
                    "tokens": 2314,
                    "confidence": {"extracted": 42, "inferred": 8, "ambiguous": 0},
                    "text": "# Q1 Report\\n\\n...",
                  },
                  ...
                ],
              },
              ...
            ]
        """
        from .dedup.minhash import CorpusDeduplicator
        from .plugins.registry import get_registered_extensions

        source_path = Path(source_dir).resolve()
        supported_exts = {f".{e}" for e in get_registered_extensions()}

        dedup = CorpusDeduplicator(threshold=dedup_threshold)
        results: list[dict] = []

        # ── Compile all supported files, skip duplicates ───────────────────────
        files = sorted(
            (p for p in source_path.glob(glob) if p.is_file() and p.suffix.lower() in supported_exts),
            key=lambda p: (p.parent, p.name),
        )

        compiled: list[dict] = []
        for file_path in files:
            try:
                text, ctx = self.compile_to_string(str(file_path))
            except Exception:
                logger.debug("Corpus: failed to compile %s", file_path, exc_info=True)
                continue
            if not text.strip():
                continue

            rel = str(file_path.relative_to(source_path))
            dupes = dedup.add(rel, text)
            if dupes:
                logger.debug("Corpus: skipping near-duplicate %s (matches %s)", rel, dupes[0])
                continue

            m = ctx.manifest
            doc_entry = {
                "source": rel,
                "file_type": m.file_type if m else file_path.suffix.lstrip("."),
                "tokens": m.optimized_tokens if m else count_tokens(text),
                "confidence": {
                    "extracted": m.blocks_extracted if m else 0,
                    "inferred":  m.blocks_inferred  if m else 0,
                    "ambiguous": m.blocks_ambiguous  if m else 0,
                },
                "text": text,
            }
            compiled.append(doc_entry)

        # ── Pack into token-budget chunks by directory ─────────────────────────
        def _pack(docs: list[dict], depth: int) -> list[dict]:
            """Greedily pack docs into chunks ≤ token_budget; bisect if oversized."""
            chunks: list[dict] = []
            current: list[dict] = []
            current_tokens = 0

            def flush() -> None:
                if current:
                    chunks.append({
                        "chunk_index": len(results) + len(chunks),
                        "token_count": sum(d["tokens"] for d in current),
                        "documents": list(current),
                    })

            for doc in docs:
                t = doc["tokens"]
                if current and current_tokens + t > token_budget:
                    if depth < max_bisect_depth and current_tokens > token_budget:
                        mid = len(current) // 2
                        chunks.extend(_pack(current[:mid], depth + 1))
                        chunks.extend(_pack(current[mid:], depth + 1))
                        current.clear()
                        current_tokens = 0
                    else:
                        flush()
                        current.clear()
                        current_tokens = 0
                current.append(doc)
                current_tokens += t
            flush()
            return chunks

        # Group by immediate parent directory
        from itertools import groupby
        compiled.sort(key=lambda d: str(Path(d["source"]).parent))
        for _dir, group in groupby(compiled, key=lambda d: str(Path(d["source"]).parent)):
            results.extend(_pack(list(group), 0))

        # Re-index chunks sequentially
        for i, chunk in enumerate(results):
            chunk["chunk_index"] = i

        return results

    # ── Internal pipeline ──────────────────────────────────────────────────────

    def _run_pipeline(
        self, source: str
    ) -> tuple[CompilationContext, dict[str, float], float]:
        """Stages 1-9: detect → parse → clean → optimise → validate → chunk →
        tokenise → manifest → readiness score.  Does NOT write to disk."""
        t0 = time.perf_counter()
        stage_timings: dict[str, float] = {}

        # Resolve URL sources before creating context
        _original_source = source
        _temp_path: str | None = None
        if source.startswith(("http://", "https://")):
            try:
                source = _fetch_url_to_temp(source)
                _temp_path = source
            except ValueError as exc:
                ctx = CompilationContext(source=_original_source, output_dir=self.output_dir)
                ctx.error("URL_FETCH_ERROR", str(exc))
                return ctx, stage_timings, t0
        elif "://" in source:
            ctx = CompilationContext(source=source, output_dir=self.output_dir)
            ctx.error("URL_FETCH_ERROR", f"Unsupported URL scheme in {source!r}. Only http and https are supported.")
            return ctx, stage_timings, t0

        try:
            ctx = CompilationContext(source=source, output_dir=self.output_dir)

            def timed(name: str) -> _StageTimer:
                return _StageTimer(stage_timings, name)

            # 0. File size gate — reject before any I/O-heavy parsing
            try:
                file_size = Path(source).stat().st_size
                if file_size > _MAX_FILE_BYTES:
                    ctx.error(
                        "FILE_TOO_LARGE",
                        f"File is {file_size:,} bytes; limit is {_MAX_FILE_BYTES:,} bytes. "
                        f"Set AKSHARAMD_MAX_FILE_BYTES to raise the limit.",
                    )
                    return ctx, stage_timings, t0
            except OSError:
                pass  # missing file handled by parser with a clearer message

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
            from .models.block import ExtractionConfidence
            doc = ctx.document
            images = sum(1 for b in doc.blocks if b.type.value == "image") if doc else 0
            tables = sum(1 for b in doc.blocks if b.type.value == "table") if doc else 0
            blocks_extracted = sum(1 for b in doc.blocks if b.confidence == ExtractionConfidence.EXTRACTED) if doc else 0
            blocks_inferred  = sum(1 for b in doc.blocks if b.confidence == ExtractionConfidence.INFERRED)  if doc else 0
            blocks_ambiguous = sum(1 for b in doc.blocks if b.confidence == ExtractionConfidence.AMBIGUOUS) if doc else 0

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
                blocks_extracted=blocks_extracted,
                blocks_inferred=blocks_inferred,
                blocks_ambiguous=blocks_ambiguous,
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

            # Restore original URL as the canonical source
            if _temp_path is not None:
                if ctx.document:
                    ctx.document = ctx.document.model_copy(update={"source": _original_source})
                if ctx.manifest:
                    ctx.manifest = ctx.manifest.model_copy(update={"source": _original_source})

            return ctx, stage_timings, t0

        finally:
            if _temp_path is not None:
                try:
                    Path(_temp_path).unlink(missing_ok=True)
                except OSError as exc:
                    logger.debug("Failed to delete temp file %s: %s", _temp_path, exc)

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
