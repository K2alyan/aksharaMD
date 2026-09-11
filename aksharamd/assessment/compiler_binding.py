"""Bind a saved compiler artifact to an independent assessment.

This adapter intentionally sits at the export boundary: it reads the raw input
and the already-written ``document.md`` bytes.  It does not invoke a parser,
repair either artifact, alter readiness, or activate any output.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .models import AssessmentResult, CandidateArtifact, SourceArtifact
from .service import Assessor

if TYPE_CHECKING:
    from ..context import CompilationContext


_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".json", ".xml"}


def _is_text_source(source: SourceArtifact) -> bool:
    return source.media_type.startswith("text/") or source.media_type in {
        "application/json", "application/xml",
    }


def save_compiled_assessment(ctx: CompilationContext) -> Path | None:
    """Write ``quality_assessment.json`` for a text-like compiler input.

    ``None`` means the raw source is outside this deliberately narrow adapter
    scope.  Binary sources are not converted into output-only assessments,
    because that could be mistaken for source-grounded assurance.
    """
    if ctx.manifest is None or not ctx.source:
        return None

    source_path = Path(ctx.source)
    if source_path.suffix.lower() not in _TEXT_SUFFIXES or not source_path.is_file():
        return None

    output_dir = Path(ctx.output_dir)
    candidate_path = output_dir / "document.md"
    if not candidate_path.is_file():
        return None

    source = SourceArtifact.from_path(
        source_path,
        logical_id=ctx.source_id,
    )
    if not _is_text_source(source):
        return None
    candidate = CandidateArtifact.from_path(
        candidate_path,
        logical_id=ctx.manifest.document_id,
    ).model_copy(update={
        "parser_name": getattr(ctx, "parser_name", None),
        "parser_version": getattr(ctx, "parser_version", None),
        "parser_configuration_id": getattr(ctx, "parser_configuration_id", None),
        "original_source_hash": ctx.capture_id or source.content_hash,
        # A parser-declared preview can never satisfy full-preservation
        # assessment, even when its omitted tail happens to contain no signal
        # covered by the current deterministic checks.
        "declared_truncated": any(
            (ctx.document.metadata if ctx.document else {}).get(key)
            for key in ("truncated", "declared_truncated")
        ),
    })
    result = Assessor().assess(candidate=candidate, source=source, task_profile=ctx.task_profile)
    payload = {
        "binding_schema_version": "1.0",
        "assessment": result.model_dump(mode="json"),
        "source": {
            "logical_id": source.logical_id,
            "capture_id": source.content_hash,
            "byte_size": source.byte_size,
            "media_type": source.media_type,
            "storage_reference": ctx.manifest.source,
        },
        "candidate": {
            "logical_id": candidate.logical_id,
            "content_hash": candidate.content_hash,
            "byte_size": candidate.byte_size,
            "media_type": candidate.media_type,
            "storage_reference": "document.md",
            "original_source_hash": candidate.original_source_hash,
            "parser_name": candidate.parser_name,
            "parser_version": candidate.parser_version,
            "parser_configuration_id": candidate.parser_configuration_id,
        },
    }
    if ctx.task_profile is not None:
        payload["task_profile"] = ctx.task_profile.model_dump(mode="json")
    path = output_dir / "quality_assessment.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def verify_compiled_assessment(ctx: CompilationContext, report: dict, assessment: AssessmentResult) -> None:
    """Recheck staged bytes and captured provenance immediately before activation."""
    source = SourceArtifact.from_path(ctx.source)
    candidate = CandidateArtifact.from_path(Path(ctx.output_dir) / "document.md")
    if source.content_hash != ctx.capture_id or assessment.source_hash != source.content_hash:
        raise ValueError("Source bytes no longer match the assessed capture")
    if assessment.candidate_hash != candidate.content_hash:
        raise ValueError("Staged candidate bytes no longer match the assessment")
    if (report["source"]["capture_id"] != source.content_hash
            or report["source"]["byte_size"] != source.byte_size
            or report["candidate"]["original_source_hash"] != source.content_hash
            or report["candidate"]["content_hash"] != candidate.content_hash
            or report["candidate"]["byte_size"] != candidate.byte_size):
        raise ValueError("Assessment provenance does not match staged source and candidate")
