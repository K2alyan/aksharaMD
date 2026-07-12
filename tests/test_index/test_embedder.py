"""Tests for the local embedding backends."""
from __future__ import annotations

import math

import pytest

sentence_transformers = pytest.importorskip(
    "sentence_transformers",
    reason="sentence-transformers required (pip install 'aksharamd[index]')",
)

from aksharamd.index.embedder import SentenceTransformerEmbedder  # noqa: E402

_MODEL = "all-MiniLM-L6-v2"


@pytest.mark.slow
def test_embed_shape():
    embedder = SentenceTransformerEmbedder(_MODEL)
    results = embedder.embed(["Hello world", "This is a test sentence."])
    assert len(results) == 2
    assert len(results[0]) == embedder.dimension
    assert len(results[1]) == embedder.dimension


@pytest.mark.slow
def test_embed_empty_list_no_model_load():
    embedder = SentenceTransformerEmbedder(_MODEL)
    assert embedder._model is None  # model not yet loaded
    result = embedder.embed([])
    assert result == []
    assert embedder._model is None  # still not loaded


@pytest.mark.slow
def test_normalized_embeddings():
    embedder = SentenceTransformerEmbedder(_MODEL)
    results = embedder.embed(["normalization test sentence"])
    magnitude = math.sqrt(sum(x * x for x in results[0]))
    assert abs(magnitude - 1.0) < 1e-3
