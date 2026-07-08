"""Provider for AksharaMD PARSE.

AksharaMD is an LLM document ingestion pipeline — it optimises output for
downstream LLM consumption, not visual layout reproduction. Implications for
ParseBench dimensions:

  Content Faithfulness   — primary strength: semantic content is preserved
                           with noise (headers, footers, watermarks) removed
  Semantic Formatting    — primary strength: explicit heading levels, tables,
                           code blocks, lists emitted as structured markdown
  Tables                 — text-layer tables extracted accurately; image-based
                           tables require the optional [vision] extra
  Charts                 — chart images stored as asset blobs with an inline
                           markdown reference (![caption](asset://id)); no data
                           values extracted — the LLM receives the image at
                           inference time via compile_to_multimodal()
  Visual Grounding       — bounding-box layout positions are deliberately
                           discarded; AksharaMD does not model spatial layout

These are design choices, not gaps. The pipeline is optimised to minimise
tokens while maximising LLM answer accuracy.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from parse_bench.inference.providers.base import (
    Provider,
    ProviderConfigError,
    ProviderPermanentError,
)
from parse_bench.inference.providers.registry import register_provider
from parse_bench.schemas.parse_output import PageIR, ParseOutput
from parse_bench.schemas.pipeline import PipelineSpec
from parse_bench.schemas.pipeline_io import (
    InferenceRequest,
    InferenceResult,
    RawInferenceResult,
)
from parse_bench.schemas.product import ProductType

# Allow running from a source checkout without a formal install.
_AKSHARAMD_REPO = Path(__file__).parents[7] / "omnimark"
if _AKSHARAMD_REPO.exists() and str(_AKSHARAMD_REPO) not in sys.path:
    sys.path.insert(0, str(_AKSHARAMD_REPO))


def _block_to_md(block: Any) -> str:
    """Render a single AksharaMD Block to markdown text."""
    from aksharamd.models.block import BlockType

    content = block.content or ""
    if block.type == BlockType.HEADING:
        level = block.level or 1
        return f"{'#' * level} {content}"
    if block.type == BlockType.TABLE:
        return content
    if block.type == BlockType.CODE_BLOCK:
        lang = block.language or ""
        return f"```{lang}\n{content}\n```"
    if block.type == BlockType.IMAGE:
        # content is already "![alt](asset://id)" — pass through as-is
        return content
    return content


@register_provider("aksharamd")
class AksharaMDProvider(Provider):
    """Provider for AksharaMD — local, no API key, no ML by default."""

    def __init__(self, provider_name: str, base_config: dict[str, Any] | None = None):
        super().__init__(provider_name, base_config)

    def _extract(self, pdf_path: str) -> dict[str, Any]:
        try:
            from aksharamd.compiler import Compiler
        except ImportError as e:
            raise ProviderConfigError(
                "aksharamd not installed. Run: pip install aksharamd"
            ) from e

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                compiler = Compiler(output_dir=tmpdir)
                text, ctx = compiler.compile_to_string(pdf_path)
        except Exception as e:
            raise ProviderPermanentError(f"AksharaMD error: {e}") from e

        if ctx.document is None:
            errs = [e.message for e in (ctx.validation.errors if ctx.validation else [])]
            raise ProviderPermanentError(
                f"AksharaMD produced no document: {'; '.join(errs) or 'unknown error'}"
            )

        # Group blocks by page for per-page markdown output.
        pages_dict: dict[int, list[Any]] = {}
        for block in ctx.document.blocks:
            pg = block.page or 1
            pages_dict.setdefault(pg, []).append(block)

        pages = []
        for pg in sorted(pages_dict):
            page_md = "\n\n".join(
                md for b in pages_dict[pg] if (md := _block_to_md(b))
            )
            pages.append({"page_index": pg - 1, "text": page_md})

        return {"pages": pages, "full_markdown": text}

    def run_inference(
        self, pipeline: PipelineSpec, request: InferenceRequest
    ) -> RawInferenceResult:
        if request.product_type != ProductType.PARSE:
            raise ProviderPermanentError(
                f"AksharaMDProvider only supports PARSE, got {request.product_type}"
            )
        pdf_path = Path(request.source_file_path)
        if not pdf_path.exists():
            raise ProviderPermanentError(f"File not found: {pdf_path}")

        started_at = datetime.now()
        try:
            raw_output = self._extract(str(pdf_path))
            completed_at = datetime.now()
            return RawInferenceResult(
                request=request,
                pipeline=pipeline,
                pipeline_name=pipeline.pipeline_name,
                product_type=request.product_type,
                raw_output=raw_output,
                started_at=started_at,
                completed_at=completed_at,
                latency_in_ms=int((completed_at - started_at).total_seconds() * 1000),
            )
        except (ProviderPermanentError, ProviderConfigError):
            raise
        except Exception as e:
            raise ProviderPermanentError(f"Unexpected error: {e}") from e

    @staticmethod
    def _convert_md_tables_to_html(content: str) -> str:
        try:
            import markdown2
        except ImportError:
            return content

        lines = content.split("\n")
        result_parts: list[str] = []
        table_lines: list[str] = []
        in_table = False

        def _flush() -> None:
            nonlocal table_lines
            if len(table_lines) >= 2:
                html = markdown2.markdown(
                    "\n".join(table_lines), extras=["tables"]
                ).strip()
                if "<table>" in html.lower():
                    result_parts.append(html)
                else:
                    result_parts.extend(table_lines)
            else:
                result_parts.extend(table_lines)
            table_lines = []

        for line in lines:
            if "|" in line and line.strip().startswith("|"):
                in_table = True
                table_lines.append(line)
            else:
                if in_table:
                    _flush()
                    in_table = False
                result_parts.append(line)
        if in_table:
            _flush()
        return "\n".join(result_parts)

    def normalize(self, raw_result: RawInferenceResult) -> InferenceResult:
        pages: list[PageIR] = []
        page_texts: list[str] = []
        for page_data in raw_result.raw_output.get("pages", []):
            text = self._convert_md_tables_to_html(page_data.get("text", "") or "")
            pages.append(PageIR(page_index=page_data.get("page_index", 0), markdown=text))
            page_texts.append(text)

        full_text = raw_result.raw_output.get("full_markdown") or "\n\n".join(page_texts)
        full_text = self._convert_md_tables_to_html(full_text)

        output = ParseOutput(
            task_type="parse",
            example_id=raw_result.request.example_id,
            pipeline_name=raw_result.pipeline_name,
            pages=pages,
            markdown=full_text,
        )
        return InferenceResult(
            request=raw_result.request,
            pipeline_name=raw_result.pipeline_name,
            product_type=raw_result.product_type,
            raw_output=raw_result.raw_output,
            output=output,
            started_at=raw_result.started_at,
            completed_at=raw_result.completed_at,
            latency_in_ms=raw_result.latency_in_ms,
        )
