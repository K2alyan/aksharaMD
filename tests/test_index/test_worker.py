"""Tests for the indexing worker — compile + embed + store pipeline."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from aksharamd.index.config import IndexConfig
from aksharamd.index.queue import IndexQueue
from aksharamd.index.worker import process_file
from aksharamd.models.block import Block, BlockType


def _fake_ctx(score: int = 85, blocks: list | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.manifest.readiness_score = score
    if blocks is None:
        blocks = [
            Block(type=BlockType.PARAGRAPH, content="Revenue rose twelve percent.", page=1, index=0),
            Block(type=BlockType.PARAGRAPH, content="Operating expenses were flat.", page=1, index=1),
        ]
    ctx.document.blocks = blocks
    return ctx


def _make_file(tmp_path, name: str = "test.pdf") -> str:
    f = tmp_path / name
    f.write_bytes(b"fake pdf bytes")
    return str(f)


def test_success(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path)
    q.enqueue(path)
    q.dequeue()  # mark processing

    store = MagicMock()
    store.add_chunks.return_value = 2
    embedder = MagicMock()
    embedder.embed.return_value = [[0.1] * 384, [0.2] * 384]
    cfg = IndexConfig(index_dir=tmp_path, min_readiness_score=50)

    with patch("aksharamd.index.worker.Compiler") as MockCompiler:
        MockCompiler.return_value.compile_to_string.return_value = ("text", _fake_ctx(score=85))
        process_file(path, q, store, embedder, cfg)

    done = q.list_all(status="done")
    assert len(done) == 1
    assert done[0].chunk_count == 2
    assert store.add_chunks.called
    assert embedder.embed.called


def test_low_quality_skips_store(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path)
    q.enqueue(path)
    q.dequeue()

    store = MagicMock()
    embedder = MagicMock()
    cfg = IndexConfig(index_dir=tmp_path, min_readiness_score=70)

    with patch("aksharamd.index.worker.Compiler") as MockCompiler:
        MockCompiler.return_value.compile_to_string.return_value = ("text", _fake_ctx(score=40))
        process_file(path, q, store, embedder, cfg)

    assert len(q.list_all(status="low_quality")) == 1
    store.add_chunks.assert_not_called()
    embedder.embed.assert_not_called()


def test_compile_error_marks_error(tmp_path):
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path)
    q.enqueue(path)
    q.dequeue()

    store = MagicMock()
    embedder = MagicMock()
    cfg = IndexConfig(index_dir=tmp_path, min_readiness_score=50)

    with patch("aksharamd.index.worker.Compiler") as MockCompiler:
        MockCompiler.return_value.compile_to_string.side_effect = RuntimeError("parse failed")
        process_file(path, q, store, embedder, cfg)

    errors = q.list_all(status="error")
    assert len(errors) == 1
    assert "parse failed" in errors[0].error
    store.add_chunks.assert_not_called()


def test_image_and_page_break_blocks_skipped(tmp_path):
    """IMAGE and PAGE_BREAK blocks must not be embedded or indexed."""
    q = IndexQueue(tmp_path / "queue.db")
    path = _make_file(tmp_path)
    q.enqueue(path)
    q.dequeue()

    store = MagicMock()
    store.add_chunks.return_value = 1
    embedder = MagicMock()
    embedder.embed.return_value = [[0.1] * 384]
    cfg = IndexConfig(index_dir=tmp_path, min_readiness_score=50)

    mixed_blocks = [
        Block(type=BlockType.IMAGE, content="[image]", page=1, index=0),
        Block(type=BlockType.PAGE_BREAK, content="", page=1, index=1),
        Block(type=BlockType.PARAGRAPH, content="Only this should be indexed.", page=2, index=2),
    ]

    with patch("aksharamd.index.worker.Compiler") as MockCompiler:
        MockCompiler.return_value.compile_to_string.return_value = (
            "text", _fake_ctx(score=80, blocks=mixed_blocks)
        )
        process_file(path, q, store, embedder, cfg)

    # embed should have been called with exactly 1 text (the paragraph)
    call_args = embedder.embed.call_args[0][0]
    assert len(call_args) == 1
    assert "Only this" in call_args[0]
