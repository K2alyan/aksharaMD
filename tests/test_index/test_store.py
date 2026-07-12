"""Tests for VectorStore — ChromaDB local vector store wrapper."""
from __future__ import annotations

import pytest

chromadb = pytest.importorskip("chromadb", reason="chromadb required (pip install 'aksharamd[index]')")

from aksharamd.index.store import EmbeddingConfigMismatch, VectorStore  # noqa: E402

_DIM = 384
_FAKE_EMB = [0.1] * _DIM


def _meta(source: str, page: int = 1) -> dict:
    return {"source": source, "block_type": "paragraph", "page": page, "readiness_score": 85}


def test_add_and_search(tmp_path):
    store = VectorStore(tmp_path / "chroma")
    store.add_chunks(
        "/tmp/report.pdf",
        ["Revenue increased by twelve percent this quarter."],
        [_FAKE_EMB],
        [_meta("/tmp/report.pdf")],
    )
    results = store.search(_FAKE_EMB, n_results=1)
    assert len(results) == 1
    assert "Revenue" in results[0]["text"]
    assert results[0]["metadata"]["source"] == "/tmp/report.pdf"


def test_delete_file(tmp_path):
    store = VectorStore(tmp_path / "chroma")
    store.add_chunks("/tmp/a.pdf", ["some content"], [_FAKE_EMB], [_meta("/tmp/a.pdf")])
    assert store.count() == 1
    store.delete_file("/tmp/a.pdf")
    assert store.count() == 0


def test_upsert_replaces_old_chunks(tmp_path):
    store = VectorStore(tmp_path / "chroma")
    store.add_chunks("/tmp/doc.pdf", ["old content"], [_FAKE_EMB], [_meta("/tmp/doc.pdf")])
    assert store.count() == 1
    # Re-index with 2 chunks — old 1 chunk should be replaced
    store.add_chunks(
        "/tmp/doc.pdf",
        ["new chunk one", "new chunk two"],
        [_FAKE_EMB, _FAKE_EMB],
        [_meta("/tmp/doc.pdf", 1), _meta("/tmp/doc.pdf", 2)],
    )
    assert store.count() == 2


def test_clear(tmp_path):
    store = VectorStore(tmp_path / "chroma")
    store.add_chunks("/tmp/x.pdf", ["text"], [_FAKE_EMB], [_meta("/tmp/x.pdf")])
    store.clear()
    assert store.count() == 0


def test_stats_two_files(tmp_path):
    store = VectorStore(tmp_path / "chroma")
    store.add_chunks(
        "/tmp/a.pdf",
        ["chunk1", "chunk2"],
        [_FAKE_EMB, _FAKE_EMB],
        [_meta("/tmp/a.pdf", 1), _meta("/tmp/a.pdf", 2)],
    )
    store.add_chunks("/tmp/b.pdf", ["chunk3"], [_FAKE_EMB], [_meta("/tmp/b.pdf")])
    s = store.stats()
    assert s["total_chunks"] == 3
    assert s["total_files"] == 2


def test_search_empty_store_returns_empty(tmp_path):
    store = VectorStore(tmp_path / "chroma")
    results = store.search(_FAKE_EMB, n_results=5)
    assert results == []


def test_search_n_results_larger_than_index(tmp_path):
    store = VectorStore(tmp_path / "chroma")
    store.add_chunks("/tmp/a.pdf", ["only one chunk"], [_FAKE_EMB], [_meta("/tmp/a.pdf")])
    # Asking for 10 results when only 1 exists — should not raise
    results = store.search(_FAKE_EMB, n_results=10)
    assert len(results) == 1


# ── Embedding-space enforcement ───────────────────────────────────────────────

def test_embedding_metadata_stored_on_creation(tmp_path):
    store = VectorStore(tmp_path / "chroma", embedding_model="test-model", vector_dimension=384)
    store.add_chunks("/tmp/a.pdf", ["text"], [_FAKE_EMB], [_meta("/tmp/a.pdf")])
    s = store.stats()
    assert s["embedding_model"] == "test-model"
    assert s["vector_dimension"] == 384


def test_embedding_model_mismatch_raises(tmp_path):
    # Create index with model-A
    VectorStore(tmp_path / "chroma", embedding_model="model-A", vector_dimension=384)
    # Re-open with model-B — must fail
    with pytest.raises(EmbeddingConfigMismatch, match="model-A"):
        VectorStore(tmp_path / "chroma", embedding_model="model-B", vector_dimension=384)


def test_embedding_dimension_mismatch_raises(tmp_path):
    VectorStore(tmp_path / "chroma", embedding_model="model-A", vector_dimension=384)
    with pytest.raises(EmbeddingConfigMismatch, match="384"):
        VectorStore(tmp_path / "chroma", embedding_model="model-A", vector_dimension=768)


def test_no_config_skips_validation(tmp_path):
    # Create with config
    VectorStore(tmp_path / "chroma", embedding_model="model-A", vector_dimension=384)
    # Re-open without config (e.g., for status/clear) — must not raise
    store = VectorStore(tmp_path / "chroma")
    assert store.count() == 0


def test_stats_includes_embedding_info(tmp_path):
    store = VectorStore(tmp_path / "chroma", embedding_model="all-MiniLM-L6-v2",
                        vector_dimension=384, distance_metric="cosine")
    store.add_chunks("/tmp/a.pdf", ["text"], [_FAKE_EMB], [_meta("/tmp/a.pdf")])
    s = store.stats()
    assert s["embedding_model"] == "all-MiniLM-L6-v2"
    assert s["distance_metric"] == "cosine"
    assert s["vector_dimension"] == 384


def test_clear_preserves_embedding_metadata(tmp_path):
    store = VectorStore(tmp_path / "chroma", embedding_model="model-A", vector_dimension=384)
    store.add_chunks("/tmp/a.pdf", ["text"], [_FAKE_EMB], [_meta("/tmp/a.pdf")])
    store.clear()
    # Re-open with same model should succeed after clear
    store2 = VectorStore(tmp_path / "chroma", embedding_model="model-A", vector_dimension=384)
    assert store2.count() == 0
