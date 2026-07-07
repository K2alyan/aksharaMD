from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
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


def _is_safe_ip(ip_str: str) -> bool:
    """Return True if *ip_str* resolves to a publicly routable address."""
    import ipaddress
    addr = ipaddress.ip_address(ip_str)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _fetch_url_to_temp(url: str) -> str:
    """Download *url* to a NamedTemporaryFile; return the temp file path.

    SSRF mitigations applied:
    - Only http/https schemes allowed.
    - All resolved IP addresses (A + AAAA) must be publicly routable.
    - HTTP redirects are disabled; a redirect response is treated as an error.
    - Total download size is capped at _MAX_FILE_BYTES.
    """
    import mimetypes
    import socket
    import tempfile
    from urllib.parse import urlparse

    import requests

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme {parsed.scheme!r} is not allowed; use http or https.")

    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("URL is missing a hostname.")

    # Resolve all address families (A + AAAA) and reject any private/loopback result.
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except Exception as exc:
        raise ValueError(f"Could not resolve host {hostname!r}: {exc}") from exc

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = str(sockaddr[0])
        if not _is_safe_ip(ip_str):
            raise ValueError(
                f"Requests to private/internal addresses are not allowed "
                f"(host {hostname!r} resolved to {ip_str})."
            )

    try:
        # allow_redirects=False prevents a redirect to an internal address bypassing
        # the IP check above (e.g., server responds 301 -> http://169.254.169.254/).
        resp = requests.get(
            url,
            timeout=30,
            stream=True,
            allow_redirects=False,
            headers={"User-Agent": "AksharaMD/0.1"},
        )
    except Exception as exc:
        raise ValueError(f"Failed to fetch {url!r}: {exc}") from exc

    if resp.is_redirect or resp.is_permanent_redirect or resp.status_code in (301, 302, 303, 307, 308):
        raise ValueError(
            f"URL {url!r} returned a redirect ({resp.status_code}). "
            "Redirects are not followed for security."
        )

    resp.raise_for_status()

    # Reject early if Content-Length exceeds limit
    content_length_hdr = resp.headers.get("Content-Length")
    if content_length_hdr:
        try:
            if int(content_length_hdr) > _MAX_FILE_BYTES:
                raise ValueError(
                    f"Remote file too large ({int(content_length_hdr):,} bytes > "
                    f"{_MAX_FILE_BYTES:,} byte limit)."
                )
        except (TypeError, ValueError) as exc:
            if "Remote file too large" in str(exc):
                raise
            # malformed Content-Length — ignore and enforce via byte counting below

    # Prefer the URL path extension; fall back to Content-Type
    url_ext = Path(urlparse(url).path).suffix
    if url_ext and 2 <= len(url_ext) <= 6:
        ext = url_ext
    else:
        content_type = resp.headers.get("Content-Type", "text/html").split(";")[0].strip()
        ext = mimetypes.guess_extension(content_type) or ".html"
        # mimetypes gives odd results for common types on some platforms
        _CT_EXT_MAP = {
            "text/html": ".html",
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "application/json": ".json",
            "text/xml": ".xml",
            "text/csv": ".csv",
        }
        ext = _CT_EXT_MAP.get(content_type, ext)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                downloaded += len(chunk)
                if downloaded > _MAX_FILE_BYTES:
                    raise ValueError(
                        f"Download exceeded size limit of {_MAX_FILE_BYTES:,} bytes. "
                        "Increase AKSHARAMD_MAX_FILE_BYTES to allow larger files."
                    )
                tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


def _fetch_s3_to_temp(uri: str) -> str:
    """Download an s3://bucket/key URI to a NamedTemporaryFile; return the temp file path.

    Requires boto3 (pip install aksharamd[cloud]). Credentials are resolved by the
    standard boto3 chain (env vars, ~/.aws/credentials, IAM role, etc.).
    """
    import tempfile
    from urllib.parse import urlparse

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        raise ValueError(
            "S3 input requires boto3: pip install aksharamd[cloud]"
        )

    parsed = urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI {uri!r}: expected s3://bucket/key")

    try:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError) as exc:
        raise ValueError(f"Failed to fetch {uri!r}: {exc}") from exc

    suffix = Path(key).suffix or ""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        body = response["Body"]
        downloaded = 0
        for chunk in iter(lambda: body.read(8192), b""):
            downloaded += len(chunk)
            if downloaded > _MAX_FILE_BYTES:
                raise ValueError(
                    f"S3 object exceeds size limit of {_MAX_FILE_BYTES:,} bytes. "
                    "Increase AKSHARAMD_MAX_FILE_BYTES to allow larger files."
                )
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
    def __init__(
        self,
        output_dir: str = "output",
        chunk_size: int = 512,
        chunk_overlap: int = 0,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be >= 0, got {chunk_overlap}")
        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must be less than chunk_size ({chunk_size})"
            )
        self.output_dir = output_dir
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    # ── Public API ─────────────────────────────────────────────────────────────

    def stream(self, source: str, on_stage: Callable[[str], None] | None = None) -> Iterator:
        """Stream blocks from a document as they become available.

        Runs detect → parse → clean → optimize, then yields each Block one at
        a time.  The validate, chunk, manifest, and export stages are skipped,
        so there is no manifest or disk output.  Use compile() when you need
        those.

        Blocks are already cleaned and optimized when yielded.  Callers
        should still apply readiness checks, chunking policy, source/citation
        metadata, and retrieval evaluation before embedding into a vector store
        or RAG pipeline.  For streaming MCP responses, blocks can be forwarded
        as they arrive.

        Example::

            for block in compiler.stream("report.pdf"):
                if block.type == BlockType.TABLE:
                    index_table(block)
                else:
                    embed(block.content)
        """
        from .models.block import Block as _Block  # noqa: F401 — used in type annotation

        ctx, _, _ = self._run_pipeline(source, on_stage=on_stage)
        if ctx.document:
            yield from ctx.document.blocks

    def compile(self, source: str, on_stage: Callable[[str], None] | None = None) -> CompilationContext:
        """Full compilation: parse → optimise → export to disk. Returns context."""
        ctx, stage_timings, t0 = self._run_pipeline(source, on_stage=on_stage)

        if on_stage:
            on_stage("Writing output files")
        with _StageTimer(stage_timings, "export"):
            for plugin in registry.get_plugins_of_type(ExporterPlugin):  # type: ignore[type-abstract]
                ctx = plugin.execute(ctx)

        return self._finalise(ctx, stage_timings, t0)

    def compile_to_multimodal(self, source: str, on_stage: Callable[[str], None] | None = None) -> tuple[list[dict], CompilationContext]:
        """Compile to an interleaved text+image content array for multimodal LLMs.

        Returns (content_array, ctx) where content_array is a list of Anthropic-compatible
        content dicts: {"type": "text", "text": ...} and {"type": "image", "source": {...}}.
        Images appear inline at their document position — Figure 4 is right there between the
        paragraphs that reference it, not appended at the end.
        """
        from .plugins.exporters.multimodal import build_multimodal_content

        ctx, stage_timings, t0 = self._run_pipeline(source, on_stage=on_stage)

        if ctx.document:
            content = build_multimodal_content(ctx.document)
        else:
            content = [{"type": "text", "text": ""}]

        return content, self._finalise(ctx, stage_timings, t0)

    def compile_to_string(self, source: str, on_stage: Callable[[str], None] | None = None) -> tuple[str, CompilationContext]:
        """Compile to a markdown string without writing any files to disk.

        Ideal for MCP server and programmatic usage where disk I/O is unwanted.
        Returns (markdown_text, ctx) — ctx.manifest has the full stats.
        """
        from .plugins.exporters.markdown import _block_to_md

        ctx, stage_timings, t0 = self._run_pipeline(source, on_stage=on_stage)

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
        on_file: Callable[[str, int, int], None] | None = None,
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

        total_files = len(files)
        compiled: list[dict] = []
        for idx, file_path in enumerate(files):
            if on_file:
                on_file(file_path.name, idx, total_files)
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
        self, source: str, on_stage: Callable[[str], None] | None = None
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
        elif source.startswith("s3://"):
            try:
                source = _fetch_s3_to_temp(source)
                _temp_path = source
            except ValueError as exc:
                ctx = CompilationContext(source=_original_source, output_dir=self.output_dir)
                ctx.error("URL_FETCH_ERROR", str(exc))
                return ctx, stage_timings, t0
        elif "://" in source:
            ctx = CompilationContext(source=source, output_dir=self.output_dir)
            ctx.error("URL_FETCH_ERROR", f"Unsupported URL scheme in {source!r}. Only http, https, and s3 are supported.")
            return ctx, stage_timings, t0

        try:
            ctx = CompilationContext(source=source, output_dir=self.output_dir)
            ctx.progress = on_stage  # parsers can call ctx.progress() for fine-grained events

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
            if on_stage:
                on_stage("Detecting file type")
            file_type = _detect_file_type(source)

            # 2. Parse
            parser = registry.get_parser(file_type)
            if parser is None:
                ctx.error("NO_PARSER", f"No parser registered for file type: {file_type}")
                return ctx, stage_timings, t0
            if on_stage:
                on_stage(f"Parsing {file_type.upper()} document")
            with timed("parse"):
                ctx = parser.execute(ctx)
            if ctx.document is None:
                # Don't add a redundant PARSE_FAILED when the parser already
                # set a specific error explaining why (e.g., ENCRYPTED_PDF).
                if not ctx.validation.errors:
                    ctx.error("PARSE_FAILED", "Parser produced no document")
                return ctx, stage_timings, t0
            ctx.document = ctx.document.model_copy(update={"file_type": file_type})

            # 3. Clean
            if on_stage:
                pages = ctx.document.pages if ctx.document else 0
                page_info = f" ({pages} pages)" if pages > 0 else ""
                on_stage(f"Cleaning blocks{page_info}")
            with timed("clean"):
                for plugin in registry.get_plugins_of_type(CleanerPlugin):  # type: ignore[type-abstract]
                    ctx = plugin.execute(ctx)

            # 4. Optimise
            if on_stage:
                on_stage("Optimizing tokens")
            with timed("optimize"):
                for plugin in registry.get_plugins_of_type(OptimizerPlugin):  # type: ignore[type-abstract]
                    ctx = plugin.execute(ctx)

            # 5. Validate
            if on_stage:
                on_stage("Validating structure")
            with timed("validate"):
                for plugin in registry.get_plugins_of_type(ValidatorPlugin):  # type: ignore[type-abstract]
                    ctx = plugin.execute(ctx)

            # 6. Chunk
            if on_stage:
                on_stage("Chunking for context windows")
            with timed("chunk"):
                # SemanticChunker is instantiated directly rather than via the plugin
                # registry because the registry caches no-arg instances and cannot
                # propagate per-compilation parameters (chunk_size, chunk_overlap).
                # This is intentional minimal-scope behaviour for the first pass.
                # Revisit when --chunk-strategy is added and a registry-level
                # configuration mechanism is designed.
                from .plugins.chunkers.semantic import SemanticChunker as _SemanticChunker
                ctx = _SemanticChunker(
                    max_tokens=self._chunk_size,
                    overlap_tokens=self._chunk_overlap,
                ).execute(ctx)

            # 7. Count tokens
            if on_stage:
                on_stage("Counting tokens")
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

            pdf_meta = doc.metadata if doc else {}
            pdf_classification = pdf_meta.get("pdf_classification", "")
            ocr_available = pdf_meta.get("pdf_ocr_available")
            image_pages = pdf_meta.get("pdf_stats", {}).get("image_pages", 0) if pdf_meta else 0
            vision_available = pdf_meta.get("pdf_vision_available")
            vision_pages = pdf_meta.get("pdf_vision_pages", 0)

            ctx.manifest = Manifest(
                source=source,
                file_type=file_type,
                pages=doc.pages if doc else 0,
                chunks=len(ctx.chunks),
                chunk_size=self._chunk_size,
                chunk_overlap=self._chunk_overlap,
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
                pdf_classification=pdf_classification,
                ocr_available=ocr_available,
                image_pages=image_pages,
                vision_available=vision_available,
                vision_pages=vision_pages,
                warnings=[i.message for i in ctx.validation.warnings],
                warning_codes=[i.code for i in ctx.validation.warnings],
                errors=[i.message for i in ctx.validation.errors],
            )

            # 9. Extraction Confidence Score
            from .models.manifest import _quality_band
            confidence = compute_confidence(ctx)
            ctx.manifest = ctx.manifest.model_copy(update={
                "readiness_score": confidence.score,
                "quality_band": _quality_band(confidence.score),
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
