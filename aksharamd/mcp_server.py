"""AksharaMD MCP Server.

Exposes AksharaMD's document compilation pipeline as Model Context Protocol tools
so any MCP-compatible host (Claude Desktop, Cursor, etc.) can compile documents
directly without writing files to disk.

Tools:
  compile_document      — compile any supported file into AI-ready Markdown
  get_supported_formats — list every format AksharaMD handles
  get_stats             — cumulative token savings across all compilations

Usage:
  # stdio (default — for Claude Desktop / most MCP hosts)
  python -m aksharamd.mcp_server

  # streamable-http (for web-facing deployments)
  python -m aksharamd.mcp_server --transport streamable-http --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Security config ────────────────────────────────────────────────────────────
# AKSHARAMD_MCP_API_KEY — if set, HTTP mode requires X-API-Key: <value> header.
# AKSHARAMD_ALLOWED_ROOT — if set, file_path arguments must resolve inside this
#   directory. Recommended when running in HTTP mode.
# AKSHARAMD_MAX_BODY_BYTES — max HTTP request body size (default 1 MB).
_API_KEY = os.environ.get("AKSHARAMD_MCP_API_KEY", "").strip() or None
_MAX_BODY_BYTES = int(os.environ.get("AKSHARAMD_MAX_BODY_BYTES", str(1 * 1024 * 1024)))  # 1 MB

_allowed_root_raw = os.environ.get("AKSHARAMD_ALLOWED_ROOT", "").strip()
if _allowed_root_raw:
    _allowed_root_path = Path(_allowed_root_raw).resolve()
    if not _allowed_root_path.is_dir():
        raise RuntimeError(
            f"AKSHARAMD_ALLOWED_ROOT={_allowed_root_raw!r} does not exist or is not a directory."
        )
    _ALLOWED_ROOT: Path | None = _allowed_root_path
else:
    _ALLOWED_ROOT = None


def _check_allowed_path(file_path: str) -> str | None:
    """Return an error string if file_path is outside AKSHARAMD_ALLOWED_ROOT, else None."""
    if _ALLOWED_ROOT is None:
        return None
    try:
        resolved = Path(file_path).expanduser().resolve()
        resolved.relative_to(_ALLOWED_ROOT)
        return None
    except ValueError:
        return (
            f"Access denied: {file_path!r} is outside the allowed root "
            f"({_ALLOWED_ROOT}). Set AKSHARAMD_ALLOWED_ROOT to permit it."
        )

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="aksharamd",
    instructions=(
        "AksharaMD compiles documents (PDF, DOCX, HTML, CSV, images, audio, and 30+ more "
        "formats) into token-efficient Markdown optimised for LLM consumption. "
        "Use compile_document to extract and clean document content before passing it to "
        "a language model — you will typically save 20-80% of tokens compared to raw text."
    ),
)

# ── helpers ────────────────────────────────────────────────────────────────────

_SUPPORTED_FORMATS: dict[str, list[str]] = {
    "Text / Markup":   ["md", "txt", "rst", "tex", "html", "htm"],
    "Documents":       ["pdf", "docx", "pptx", "xlsx", "odt", "ods", "odp", "epub", "rtf"],
    "Legacy Office":   ["doc", "ppt", "xls"],
    "Data":            ["json", "jsonl", "csv", "tsv", "xml", "yaml", "toml"],
    "Email":           ["eml", "msg"],
    "Notebooks":       ["ipynb"],
    "Source code":     ["py", "js", "ts", "go", "rs", "java", "c", "cpp", "sql", "sh"],
    "Images (OCR)":    ["jpg", "jpeg", "png", "tiff", "bmp", "webp", "gif"],
    "Audio (Whisper)": ["mp3", "wav", "m4a", "ogg", "flac"],
    "Video":           ["mp4", "webm"],
    "Archives":        ["zip", "tar", "tgz", "gz", "bz2", "xz", "7z"],
    "Feeds":           ["rss", "atom"],
}


def _format_savings_summary(m: Any) -> str:
    """Build a compact savings block to append to compiled output."""
    tokens_saved = max(0, m.original_tokens - m.optimized_tokens)
    conf = m.readiness_score
    conf_label = "high" if conf >= 85 else "medium" if conf >= 65 else "low"

    lines = [
        "",
        "---",
        "**AksharaMD compilation summary**",
        f"- File: `{Path(m.source).name}` ({m.file_type.upper()})",
        f"- Pages / chunks: {m.pages} pages, {m.chunks} chunks",
        f"- Tokens: {m.optimized_tokens:,} (from {m.original_tokens:,} original — saved {tokens_saved:,} / {m.token_reduction_percent:.1f}%)",
        f"- Confidence: {conf}/100 ({conf_label})",
        f"- Time: {m.elapsed_seconds:.2f}s",
    ]

    if m.confidence_notes:
        lines.append("- Notes:")
        for note in m.confidence_notes:
            lines.append(f"  - {note}")

    if m.errors:
        lines.append("- Errors:")
        for err in m.errors:
            lines.append(f"  - {err}")

    return "\n".join(lines)


# ── tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def compile_document(file_path: str) -> str:
    """Compile a document into AI-optimized Markdown.

    Supports 35+ formats including PDF, DOCX, HTML, CSV, JSON, images (OCR),
    audio (Whisper transcription), archives, and more.

    The returned text is the full document content in clean Markdown, followed
    by a summary block showing token savings and confidence score.

    Args:
        file_path: Absolute or relative path to the document to compile.

    Returns:
        Compiled Markdown content with an appended AksharaMD summary block.
    """
    denied = _check_allowed_path(file_path)
    if denied:
        return denied

    from aksharamd.compiler import Compiler

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return f"Error: file not found: {file_path}"
    if not path.is_file():
        return f"Error: path is not a file: {file_path}"
    if path.stat().st_size == 0:
        return f"Error: file is empty: {file_path}"

    try:
        compiler = Compiler(output_dir=str(path.parent / ".aksharamd_cache"))
        text, ctx = compiler.compile_to_string(str(path))
    except Exception as e:
        return f"Error compiling {path.name}: {e}"

    if ctx.validation.errors and not text.strip():
        error_msgs = "; ".join(i.message for i in ctx.validation.errors)
        return f"Compilation failed for {path.name}: {error_msgs}"

    if not text.strip():
        return f"No content could be extracted from {path.name}. The file may be empty, encrypted, or in an unsupported encoding."

    summary = _format_savings_summary(ctx.manifest) if ctx.manifest else ""
    return text + summary


@mcp.tool()
def compile_document_multimodal(file_path: str) -> list:
    """Compile a document into an interleaved text+image content sequence for multimodal LLMs.

    Like compile_document but returns the document as an ordered mix of text and image blocks.
    Images appear at their exact position in the document — Figure 4 appears right where it
    sits in the source, not appended at the end. The LLM can see each chart or diagram in
    context with the surrounding text that references it.

    Use this instead of compile_document when the document contains charts, diagrams, or
    figures that are important to understand alongside the surrounding text (e.g. reports,
    presentations, scientific papers).

    Args:
        file_path: Absolute or relative path to the document to compile.

    Returns:
        Interleaved sequence of text strings and images in document order.
    """
    denied = _check_allowed_path(file_path)
    if denied:
        return [denied]

    import base64

    from mcp.server.fastmcp import Image as MCPImage

    from aksharamd.compiler import Compiler

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return [f"Error: file not found: {file_path}"]
    if not path.is_file():
        return [f"Error: path is not a file: {file_path}"]
    if path.stat().st_size == 0:
        return [f"Error: file is empty: {file_path}"]

    try:
        compiler = Compiler(output_dir=str(path.parent / ".aksharamd_cache"))
        content_array, ctx = compiler.compile_to_multimodal(str(path))
    except Exception as e:
        return [f"Error compiling {path.name}: {e}"]

    if not content_array:
        return [f"No content could be extracted from {path.name}."]

    result = []
    for item in content_array:
        if item["type"] == "text":
            if item["text"].strip():
                result.append(item["text"])
        elif item["type"] == "image":
            try:
                img_bytes = base64.b64decode(item["source"]["data"])
                media_type = item["source"]["media_type"]
                fmt = media_type.split("/")[-1] if "/" in media_type else "png"
                result.append(MCPImage(data=img_bytes, format=fmt))
            except Exception as _img_err:
                logger.warning("Skipping malformed image in %s: %s", path.name, _img_err)

    if ctx.manifest:
        result.append(_format_savings_summary(ctx.manifest))

    return result if result else [f"No content extracted from {path.name}."]


@mcp.tool()
def get_supported_formats() -> str:
    """List all file formats AksharaMD can compile.

    Returns a Markdown table of supported formats grouped by category,
    including notes on optional dependencies (OCR, Whisper, LibreOffice).

    Returns:
        Markdown string listing all supported formats.
    """
    lines = ["## AksharaMD Supported Formats\n"]
    total = 0
    for category, exts in _SUPPORTED_FORMATS.items():
        ext_str = ", ".join(f"`.{e}`" for e in exts)
        lines.append(f"**{category}**: {ext_str}")
        total += len(exts)

    lines += [
        "",
        f"**Total: {total}+ formats**",
        "",
        "### Optional dependencies",
        "- **Image OCR**: requires `pytesseract` + Tesseract binary",
        "- **Audio transcription**: requires `openai-whisper` + ffmpeg",
        "- **Legacy Office** (.doc/.ppt): requires LibreOffice on PATH",
    ]
    return "\n".join(lines)


@mcp.tool()
def get_stats() -> str:
    """Get cumulative token savings across all AksharaMD compilations.

    Reads the persistent ledger (~/.aksharamd/ledger.jsonl) and returns a
    summary of total tokens saved, dollar savings per model, and recent
    compilation history.

    Returns:
        Markdown summary of lifetime AksharaMD savings.
    """
    try:
        from aksharamd import ledger as _ledger
        from aksharamd.utils import DISPLAY_MODELS, TOKEN_PRICES, tokens_to_dollars

        data = _ledger.get_stats()
        if not data:
            return "No compilations recorded yet. Run `compile_document` to start."

        total_saved = data["total_saved_tokens"]
        lines = [
            "## AksharaMD Lifetime Savings\n",
            f"- **Compilations**: {data['total_compilations']:,}",
            f"- **Tokens processed**: {data['total_original_tokens']:,}",
            f"- **Tokens delivered**: {data['total_optimized_tokens']:,}",
            f"- **Tokens saved**: {total_saved:,} ({data['reduction_percent']:.1f}%)",
            f"- **Total time**: {data['total_elapsed_seconds']:.1f}s",
            "",
            "### Dollar savings (input tokens)",
        ]

        for model in DISPLAY_MODELS:
            saved_usd = tokens_to_dollars(total_saved, model)
            price = TOKEN_PRICES[model]
            lines.append(f"- **{model}** (${price:.3f}/1M): saved **${saved_usd:.4f}**")

        by_type = data.get("by_file_type", {})
        if by_type:
            lines += ["", "### By file type"]
            for ftype, info in sorted(by_type.items(), key=lambda x: -x[1]["saved"])[:10]:
                lines.append(f"- `.{ftype}`: {info['count']} compilations, {info['saved']:,} tokens saved")

        recent = data.get("recent", [])
        if recent:
            lines += ["", "### Recent compilations"]
            for e in reversed(recent[-5:]):
                ts = e["ts"][:19].replace("T", " ")
                lines.append(
                    f"- `{e['source'][:40]}` ({e['file_type']}) — "
                    f"{e['saved_tokens']:,} tokens saved in {e['elapsed_seconds']:.2f}s at {ts} UTC"
                )

        return "\n".join(lines)

    except Exception as e:
        return f"Error reading stats: {e}"


# ── entry point ────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AksharaMD MCP Server")
    p.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"],
                   help="Transport mode (default: stdio)")
    p.add_argument("--host", default="127.0.0.1", help="Host for HTTP mode (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="Port for HTTP mode (default: 8000)")
    return p


def _run_http(host: str, port: int) -> None:
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    app = mcp.streamable_http_app()

    class _BodySizeMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > _MAX_BODY_BYTES:
                return JSONResponse({"error": "Request body too large"}, status_code=413)
            return await call_next(request)

    app.add_middleware(_BodySizeMiddleware)

    if _API_KEY:
        class _APIKeyMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if request.headers.get("X-API-Key") != _API_KEY:
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
                return await call_next(request)

        app.add_middleware(_APIKeyMiddleware)
        logger.info("HTTP mode: API key authentication enabled.")
    else:
        logger.warning(
            "HTTP mode: AKSHARAMD_MCP_API_KEY is not set — server is unauthenticated. "
            "Set this variable or restrict access via a reverse proxy."
        )

    if _ALLOWED_ROOT:
        logger.info("HTTP mode: file access restricted to %s", _ALLOWED_ROOT)
    else:
        logger.warning(
            "HTTP mode: AKSHARAMD_ALLOWED_ROOT is not set — compile_document accepts "
            "any file path on the server. Set this variable to restrict access."
        )

    uvicorn.run(app, host=host, port=port)


def serve() -> None:
    """Start the AksharaMD MCP server (reads --transport / --host / --port from argv)."""
    args = _build_parser().parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "streamable-http":
        _run_http(args.host, args.port)
    else:
        raise ValueError(f"Unknown transport: {args.transport!r}. Use 'stdio' or 'streamable-http'.")


if __name__ == "__main__":
    serve()
