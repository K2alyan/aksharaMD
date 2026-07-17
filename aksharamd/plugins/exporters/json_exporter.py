from __future__ import annotations

from pathlib import Path

from ...context import CompilationContext
from ..base import ExporterPlugin
from ..registry import register_plugin


class JSONExporter(ExporterPlugin):
    name = "json_exporter"
    priority = 91

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        out = Path(ctx.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        chunks_dir = out / "chunks"
        chunks_dir.mkdir(exist_ok=True)

        # document.json
        (out / "document.json").write_text(
            ctx.document.model_dump_json(indent=2), encoding="utf-8"
        )

        # manifest.json
        if ctx.manifest:
            (out / "manifest.json").write_text(
                ctx.manifest.model_dump_json(indent=2), encoding="utf-8"
            )

        # validation.json
        (out / "validation.json").write_text(
            ctx.validation.model_dump_json(indent=2), encoding="utf-8"
        )

        # chunks/
        chunk_meta: dict = {}
        if ctx.manifest:
            chunk_meta["source_path"] = ctx.manifest.source
            chunk_meta["compiled_at"] = ctx.manifest.compiled_at
            if ctx.manifest.file_modified_at is not None:
                chunk_meta["file_modified_at"] = ctx.manifest.file_modified_at
            if ctx.manifest.source_id:
                chunk_meta["source_id"] = ctx.manifest.source_id
            if ctx.manifest.capture_id:
                chunk_meta["capture_id"] = ctx.manifest.capture_id
            if ctx.manifest.document_id:
                chunk_meta["document_id"] = ctx.manifest.document_id

        for chunk in ctx.chunks:
            if chunk_meta:
                chunk = chunk.model_copy(update={"metadata": {**chunk_meta, **chunk.metadata}})
            chunk_path = chunks_dir / f"{chunk.id}.json"
            chunk_path.write_text(chunk.model_dump_json(indent=2), encoding="utf-8")

        return ctx


register_plugin(JSONExporter)
