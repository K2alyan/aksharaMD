from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 500 MB default; override with AKSHARAMD_MAX_FILE_BYTES env var
_MAX_FILE_BYTES = int(os.environ.get("AKSHARAMD_MAX_FILE_BYTES", str(500 * 1024 * 1024)))

import filetype
import requests
from requests.adapters import BaseAdapter, HTTPAdapter

from . import ledger as _ledger
from .context import CompilationContext
from .models.manifest import Manifest

# Import all built-in plugins to trigger registration (side-effect imports)
from .plugins import parsers as _parsers_pkg  # noqa: F401
from .plugins import registry
from .plugins.base import (
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
from .plugins.validators import header_footer_table as _hft_validator_pkg  # noqa: F401
from .plugins.validators import multicolumn as _multicolumn_validator_pkg  # noqa: F401
from .plugins.validators import structure as _validator_pkg  # noqa: F401
from .scoring import compute_confidence
from .utils import count_tokens


@dataclass
class CorpusCompilationResult:
    """Summary of a compile_corpus() run.

    chunks            — packed token-budget groups, same format as the old list[dict] return.
    processed         — count of files successfully compiled with non-empty output.
    failed            — files that raised an exception or had compile errors;
                        each entry has keys 'source', 'error', and 'category' = 'failed'.
    skipped_duplicates — count of near-duplicate files skipped by MinHash LSH.
    low_quality       — files that compiled but produced empty text (no usable content);
                        each entry has keys 'source', 'reason', and 'category' = 'low_quality'.
    unsupported       — files whose extension has no registered parser;
                        each entry has keys 'source', 'extension', and 'category' = 'unsupported'.
    """
    chunks: list[dict] = field(default_factory=list)
    processed: int = 0
    failed: list[dict] = field(default_factory=list)
    skipped_duplicates: int = 0
    low_quality: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)

    @property
    def total_scanned(self) -> int:
        return (
            self.processed
            + len(self.failed)
            + self.skipped_duplicates
            + len(self.low_quality)
            + len(self.unsupported)
        )

    @property
    def indexed(self) -> int:
        """Docs that made it into at least one corpus chunk."""
        return sum(len(c["documents"]) for c in self.chunks)


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


class _PinnedIPAdapter(BaseAdapter):
    """Thread-safe requests transport adapter that connects to a pre-validated IP.

    TCP connects to *pinned_ip*.  TLS SNI and certificate verification use the
    original *hostname*.  The Host header is set to *hostname*, not the IP.

    No global state is modified — safe for concurrent use across threads.
    Each send() call creates its own connection pool; there is no shared state
    between adapter instances or between concurrent calls on the same instance.
    """

    def __init__(self, hostname: str, pinned_ip: str) -> None:
        super().__init__()
        self._hostname = hostname
        self._pinned_ip = pinned_ip

    def send(  # type: ignore[override]
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | None = None,
        verify: bool | str = True,
        cert: str | tuple[str, str] | None = None,
        proxies: dict[str, str] | None = None,
    ) -> requests.Response:
        import ssl as _ssl
        from urllib.parse import urlparse

        import urllib3

        parsed = urlparse(request.url)

        if parsed.hostname != self._hostname:
            return HTTPAdapter().send(request, stream=stream, timeout=timeout,
                                      verify=verify, cert=cert, proxies=proxies)

        scheme = parsed.scheme
        port = parsed.port or (443 if scheme == "https" else 80)

        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        # Ensure the Host header carries the original hostname, not the pinned IP.
        # http.client would otherwise derive Host from the pool's host attribute.
        headers: dict[str, str] = {k: str(v) for k, v in request.headers.items()}
        if "Host" not in headers:
            port_suffix = (
                f":{parsed.port}"
                if parsed.port and parsed.port not in (80, 443)
                else ""
            )
            headers["Host"] = f"{self._hostname}{port_suffix}"

        # Normalise timeout for urllib3
        if isinstance(timeout, (int, float)):
            u3_timeout: urllib3.Timeout | None = urllib3.Timeout(connect=timeout, read=timeout)
        elif isinstance(timeout, tuple) and len(timeout) == 2:
            u3_timeout = urllib3.Timeout(connect=timeout[0], read=timeout[1])
        else:
            u3_timeout = None

        if scheme == "https":
            # Build an SSL context that verifies the cert against the original
            # hostname even though the TCP socket connects to the pinned IP.
            # server_hostname (passed as conn_kw) controls TLS SNI + verification.
            if isinstance(verify, str):
                ssl_ctx = _ssl.create_default_context(cafile=verify)
            elif verify:
                ssl_ctx = _ssl.create_default_context()
            else:
                ssl_ctx = _ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl.CERT_NONE

            pool: urllib3.HTTPConnectionPool = urllib3.HTTPSConnectionPool(
                host=self._pinned_ip,
                port=port,
                timeout=u3_timeout,
                ssl_context=ssl_ctx,
                server_hostname=self._hostname,  # SNI + cert-verification hostname
            )
        else:
            pool = urllib3.HTTPConnectionPool(
                host=self._pinned_ip,
                port=port,
                timeout=u3_timeout,
            )

        body = request.body
        urllib3_resp = pool.urlopen(
            method=request.method or "GET",
            url=path,
            headers=headers,
            body=body,  # type: ignore[arg-type]
            preload_content=not stream,
            decode_content=False,
            redirect=False,
        )

        return HTTPAdapter().build_response(request, urllib3_resp)

    def close(self) -> None:
        pass  # per-request pools are not retained; nothing to release


def _fetch_url_to_temp(url: str) -> str:
    """Download *url* to a NamedTemporaryFile; return the temp file path.

    SSRF mitigations applied:
    - Only http/https schemes allowed.
    - All resolved IP addresses (A + AAAA) must be publicly routable.
    - HTTP redirects are disabled; a redirect response is treated as an error.
    - Total download size is capped at _MAX_FILE_BYTES.
    - Validated IP is pinned via _PinnedIPAdapter — no global state modified;
      safe for concurrent use across threads.
    - Partial temp files are deleted on any failure path.
    - Response body is always closed after streaming.
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

    pinned_ip: str | None = None
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = str(sockaddr[0])
        if not _is_safe_ip(ip_str):
            raise ValueError(
                f"Requests to private/internal addresses are not allowed "
                f"(host {hostname!r} resolved to {ip_str})."
            )
        if pinned_ip is None:
            pinned_ip = ip_str

    # _PinnedIPAdapter connects to the validated IP directly via a per-request
    # urllib3 pool — no global getaddrinfo override, safe for concurrent fetches.
    assert pinned_ip is not None
    session = requests.Session()
    adapter = _PinnedIPAdapter(hostname=hostname, pinned_ip=pinned_ip)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    resp: requests.Response | None = None
    try:
        try:
            # allow_redirects=False prevents a redirect to an internal address bypassing
            # the IP check above (e.g., server responds 301 -> http://169.254.169.254/).
            resp = session.get(
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
        tmp_name = tmp.name
        download_ok = False
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
            download_ok = True
        finally:
            tmp.close()
            if not download_ok:
                Path(tmp_name).unlink(missing_ok=True)
    finally:
        session.close()
        if resp is not None:
            resp.close()

    return tmp_name


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
    tmp_name = tmp.name
    body = response["Body"]
    download_ok = False
    try:
        downloaded = 0
        for chunk in iter(lambda: body.read(8192), b""):
            downloaded += len(chunk)
            if downloaded > _MAX_FILE_BYTES:
                raise ValueError(
                    f"S3 object exceeds size limit of {_MAX_FILE_BYTES:,} bytes. "
                    "Increase AKSHARAMD_MAX_FILE_BYTES to allow larger files."
                )
            tmp.write(chunk)
        download_ok = True
    finally:
        tmp.close()
        body.close()
        if not download_ok:
            Path(tmp_name).unlink(missing_ok=True)
    return tmp_name


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
        safe_mode: bool = False,
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
        self.safe_mode = safe_mode

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
    ) -> CorpusCompilationResult:
        """Compile every supported file under *source_dir* and pack the results into
        token-budget-aware groups ready for downstream LLM processing (e.g. Graphify).

        Files are grouped by their immediate parent directory first (keeping related
        artefacts together), then packed greedily into chunks up to *token_budget*.
        If a single document exceeds the budget it is placed alone.  Groups that
        exceed the budget are bisected recursively (up to *max_bisect_depth* times).

        Near-duplicate documents (Jaccard ≥ *dedup_threshold* across the whole
        corpus) are skipped automatically via MinHash LSH.

        Returns a CorpusCompilationResult. The .chunks attribute contains the packed
        chunk dicts (same schema as before). .failed lists every file that errored
        so callers know exactly what was dropped.
        """
        from .dedup.minhash import CorpusDeduplicator
        from .plugins.registry import get_registered_extensions

        source_path = Path(source_dir).resolve()
        supported_exts = {f".{e}" for e in get_registered_extensions()}

        dedup = CorpusDeduplicator(threshold=dedup_threshold)
        result = CorpusCompilationResult()
        chunks: list[dict] = []

        # ── Scan all files, split supported vs unsupported ────────────────────
        all_files = sorted(
            (p for p in source_path.glob(glob) if p.is_file()),
            key=lambda p: (p.parent, p.name),
        )
        files = [p for p in all_files if p.suffix.lower() in supported_exts]
        for p in all_files:
            if p.suffix.lower() not in supported_exts:
                rel_unsup = str(p.relative_to(source_path))
                result.unsupported.append({
                    "source": rel_unsup,
                    "extension": p.suffix.lower() or "(none)",
                    "category": "unsupported",
                })

        total_files = len(files)
        compiled: list[dict] = []
        for idx, file_path in enumerate(files):
            if on_file:
                on_file(file_path.name, idx, total_files)
            rel = str(file_path.relative_to(source_path))
            try:
                text, ctx = self.compile_to_string(str(file_path))
            except Exception as exc:
                logger.debug("Corpus: failed to compile %s", file_path, exc_info=True)
                result.failed.append({"source": rel, "error": str(exc)})
                continue

            # Compilation errors stored in context (not raised as exceptions)
            if ctx.validation.errors:
                err_msg = "; ".join(e.message for e in ctx.validation.errors)
                logger.debug("Corpus: compile errors in %s: %s", file_path, err_msg)
                result.failed.append({"source": rel, "error": err_msg})
                continue

            if not text.strip():
                logger.debug("Corpus: empty output for %s", file_path)
                result.low_quality.append({
                    "source": rel,
                    "reason": "empty output after compilation",
                    "category": "low_quality",
                })
                continue

            dupes = dedup.add(rel, text)
            if dupes:
                logger.debug("Corpus: skipping near-duplicate %s (matches %s)", rel, dupes[0])
                result.skipped_duplicates += 1
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
            result.processed += 1

        # ── Pack into token-budget chunks by directory ─────────────────────────
        def _pack(docs: list[dict], depth: int) -> list[dict]:
            """Greedily pack docs into chunks ≤ token_budget; bisect if oversized."""
            packed: list[dict] = []
            current: list[dict] = []
            current_tokens = 0

            def flush() -> None:
                if current:
                    packed.append({
                        "chunk_index": len(chunks) + len(packed),
                        "token_count": sum(d["tokens"] for d in current),
                        "documents": list(current),
                    })

            for doc in docs:
                t = doc["tokens"]
                if current and current_tokens + t > token_budget:
                    if depth < max_bisect_depth and current_tokens > token_budget:
                        mid = len(current) // 2
                        packed.extend(_pack(current[:mid], depth + 1))
                        packed.extend(_pack(current[mid:], depth + 1))
                        current.clear()
                        current_tokens = 0
                    else:
                        flush()
                        current.clear()
                        current_tokens = 0
                current.append(doc)
                current_tokens += t
            flush()
            return packed

        # Group by immediate parent directory
        from itertools import groupby
        compiled.sort(key=lambda d: str(Path(d["source"]).parent))
        for _dir, group in groupby(compiled, key=lambda d: str(Path(d["source"]).parent)):
            chunks.extend(_pack(list(group), 0))

        # Re-index chunks sequentially
        for i, chunk in enumerate(chunks):
            chunk["chunk_index"] = i

        result.chunks = chunks
        return result

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
            if self.safe_mode:
                ctx = CompilationContext(source=source, output_dir=self.output_dir, safe_mode=True)
                ctx.error("SAFE_MODE_BLOCKED", "URL fetching is disabled in safe mode.")
                return ctx, stage_timings, t0
            try:
                source = _fetch_url_to_temp(source)
                _temp_path = source
            except ValueError as exc:
                ctx = CompilationContext(source=_original_source, output_dir=self.output_dir)
                ctx.error("URL_FETCH_ERROR", str(exc))
                return ctx, stage_timings, t0
        elif source.startswith("s3://"):
            if self.safe_mode:
                ctx = CompilationContext(source=source, output_dir=self.output_dir, safe_mode=True)
                ctx.error("SAFE_MODE_BLOCKED", "S3 fetching is disabled in safe mode.")
                return ctx, stage_timings, t0
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
            ctx = CompilationContext(source=source, output_dir=self.output_dir, safe_mode=self.safe_mode)
            ctx.progress = on_stage  # parsers can call ctx.progress() for fine-grained events

            def timed(name: str) -> _StageTimer:
                return _StageTimer(stage_timings, name)

            # 0. File size gate — reject before any I/O-heavy parsing
            _file_modified_at: str | None = None
            try:
                _stat = Path(source).stat()
                file_size = _stat.st_size
                _file_modified_at = datetime.fromtimestamp(_stat.st_mtime, tz=UTC).isoformat()
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
                file_modified_at=_file_modified_at,
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
