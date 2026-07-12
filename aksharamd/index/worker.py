from __future__ import annotations

import logging
import tempfile
from typing import TYPE_CHECKING, Any

from aksharamd.compiler import Compiler

if TYPE_CHECKING:
    from aksharamd.index.config import IndexConfig
    from aksharamd.index.embedder import Embedder
    from aksharamd.index.queue import IndexQueue
    from aksharamd.index.store import VectorStore

logger = logging.getLogger(__name__)

_SKIP_TYPES = {"image", "page_break"}


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
        content = block.content.strip()
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

    try:
        embeddings = embedder.embed(texts)
    except Exception as exc:
        logger.error("Embedding failed for %s: %s", path, exc)
        queue.mark_error(path, f"embedding failed: {exc}")
        return

    chunk_count = store.add_chunks(path, texts, embeddings, metadatas)
    queue.mark_done(path, chunk_count)
    logger.info("Indexed %s — %d chunks, readiness %d/100", path, chunk_count, score)
