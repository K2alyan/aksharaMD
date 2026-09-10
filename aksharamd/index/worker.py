from __future__ import annotations

import hashlib
import logging
import tempfile
from typing import TYPE_CHECKING, Any

from aksharamd.assessment import (
    AssessmentDisposition,
    AssessmentResult,
    Assessor,
    CandidateArtifact,
    SourceArtifact,
)
from aksharamd.compiler import Compiler
from aksharamd.plugins.exporters.markdown import _block_to_md

if TYPE_CHECKING:
    from aksharamd.index.config import IndexConfig
    from aksharamd.index.embedder import Embedder
    from aksharamd.index.queue import IndexQueue
    from aksharamd.index.store import VectorStore

logger = logging.getLogger(__name__)

_SKIP_TYPES = {"image", "page_break"}


def _source_grounded_assessment(
    source_path: str, candidate_text: str, ctx: Any,
) -> tuple[AssessmentResult | None, str | None]:
    """Assess the exact joined text payload that will be embedded and stored."""
    try:
        source = SourceArtifact.from_path(source_path, logical_id=ctx.source_id)
        # The candidate has no durable path in this worker.  Supplying its
        # exact bytes directly avoids accidentally assessing a re-rendered or
        # stale output artifact.
        candidate_artifact = CandidateArtifact(
            content_hash=hashlib.sha256(candidate_text.encode("utf-8")).hexdigest(),
            byte_size=len(candidate_text.encode("utf-8")),
            media_type="text/markdown",
            logical_id=ctx.manifest.document_id,
            data=candidate_text.encode("utf-8"),
            parser_name=getattr(ctx, "parser_name", None),
            parser_version=getattr(ctx, "parser_version", None),
            parser_configuration_id=getattr(ctx, "parser_configuration_id", None),
            original_source_hash=ctx.capture_id or source.content_hash,
            declared_truncated=any(
                (ctx.document.metadata if ctx.document else {}).get(key)
                for key in ("truncated", "declared_truncated")
            ),
        )
        return Assessor().assess(candidate=candidate_artifact, source=source), None
    except (OSError, ValueError, TypeError) as exc:
        return None, f"could not create source-grounded assessment: {exc}"


def process_file(
    path: str,
    queue: IndexQueue,
    store: VectorStore,
    embedder: Embedder,
    config: IndexConfig,
) -> None:
    """Compile path through AksharaMD, embed blocks, and store in the local index.

    Updates queue status (done / low_quality / error) regardless of outcome.
    """
    assessment: AssessmentResult | None = None
    assessment_error: str | None = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _, ctx = Compiler(output_dir=tmp).compile_to_string(path)
    except Exception as exc:
        logger.error("Compile failed for %s: %s", path, exc)
        queue.mark_error(path, str(exc))
        return

    score: int = (ctx.manifest.readiness_score or 0) if ctx.manifest else 0

    if score < config.min_readiness_score:
        logger.warning("Low quality (%d/100) for %s — skipping index", score, path)
        queue.mark_low_quality(path, score)
        return

    if ctx.document is None or not ctx.document.blocks:
        queue.mark_error(path, "no document blocks produced")
        return

    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for block in ctx.document.blocks:
        if block.type.value in _SKIP_TYPES:
            continue
        content = _block_to_md(block) if config.require_assessment_accept else block.content.strip()
        if not content:
            continue
        texts.append(content)
        metadatas.append({
            "source": path,
            "block_type": block.type.value,
            "page": block.page or 0,
            "readiness_score": score,
        })

    if not texts:
        queue.mark_error(path, "no indexable blocks after filtering")
        return

    if config.require_assessment_accept:
        assessment, assessment_error = _source_grounded_assessment(path, "\n\n".join(texts), ctx)
    if config.require_assessment_accept:
        if assessment_error:
            reason = f"assessment gate: {assessment_error}"
        elif assessment is None:
            reason = "assessment gate: no assessment result was produced"
        elif assessment.disposition != AssessmentDisposition.ACCEPT:
            reason = f"assessment gate: {assessment.disposition} ({assessment.next_action})"
        else:
            reason = None
        if reason:
            logger.warning("Assessment gate blocked indexing %s: %s", path, reason)
            queue.mark_low_quality(path, score, reason)
            return

    try:
        embeddings = embedder.embed(texts)
    except Exception as exc:
        logger.error("Embedding failed for %s: %s", path, exc)
        queue.mark_error(path, f"embedding failed: {exc}")
        return

    chunk_count = store.add_chunks(path, texts, embeddings, metadatas)
    queue.mark_done(path, chunk_count)
    logger.info("Indexed %s — %d chunks, readiness %d/100", path, chunk_count, score)
