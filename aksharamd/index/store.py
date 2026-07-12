from __future__ import annotations

import hashlib
from pathlib import Path

_COLLECTION_NAME = "aksharamd_index"

# Metadata key names stored in the ChromaDB collection.
_META_MODEL = "em_model"
_META_DIM = "em_dim"
_META_METRIC = "em_metric"


class EmbeddingConfigMismatch(ValueError):
    """Raised when the index was built with a different embedding configuration."""


class VectorStore:
    """ChromaDB-backed local vector store.

    All data lives on disk at chromadb_path — nothing is uploaded or shared.

    Pass embedding_model / vector_dimension / distance_metric to enable
    embedding-space enforcement: VectorStore will store these in the collection
    metadata on first creation and validate them on every subsequent open.
    Mismatch raises EmbeddingConfigMismatch so callers know they must rebuild
    the index rather than silently mixing incompatible vectors.

    Omit all three (or pass None) for legacy / read-only access (e.g. status,
    clear) where no vectors are written or queried.
    """

    def __init__(
        self,
        chromadb_path: Path,
        embedding_model: str | None = None,
        vector_dimension: int | None = None,
        distance_metric: str = "cosine",
    ) -> None:
        try:
            import chromadb  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                'chromadb is required for local indexing. '
                'Install with: pip install "aksharamd[index]"'
            ) from exc

        chromadb_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chromadb_path))
        self._embedding_model = embedding_model
        self._vector_dimension = vector_dimension
        self._distance_metric = distance_metric

        hnsw_space = distance_metric if distance_metric in ("cosine", "l2", "ip") else "cosine"

        if embedding_model is not None:
            # Build the collection metadata we expect to see.
            new_meta: dict = {
                "hnsw:space": hnsw_space,
                _META_MODEL: embedding_model,
                _META_METRIC: distance_metric,
            }
            if vector_dimension is not None:
                new_meta[_META_DIM] = vector_dimension

            existing = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata=new_meta,
            )
            stored = existing.metadata or {}

            # If the collection already has model metadata, validate it.
            stored_model = stored.get(_META_MODEL, "")
            if stored_model and stored_model != embedding_model:
                raise EmbeddingConfigMismatch(
                    f"Index was built with embedding model {stored_model!r} but you are using "
                    f"{embedding_model!r}. Run `aksharamd index clear` to rebuild the index "
                    "with the new model."
                )

            stored_metric = stored.get(_META_METRIC, "")
            if stored_metric and stored_metric != distance_metric:
                raise EmbeddingConfigMismatch(
                    f"Index was built with distance metric {stored_metric!r} but config uses "
                    f"{distance_metric!r}. Run `aksharamd index clear` to rebuild."
                )

            stored_dim = stored.get(_META_DIM)
            if stored_dim is not None and vector_dimension is not None and int(stored_dim) != vector_dimension:
                raise EmbeddingConfigMismatch(
                    f"Index was built with vector dimension {stored_dim} but model produces "
                    f"{vector_dimension}. Run `aksharamd index clear` to rebuild."
                )

            self._collection = existing
        else:
            # No embedding config provided — open without validation (status / clear).
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": hnsw_space},
            )

    def add_chunks(
        self,
        path: str,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> int:
        """Upsert chunks for path: removes all prior chunks then adds the new batch.

        Returns the number of chunks stored (0 if texts is empty).
        """
        if not texts:
            return 0

        self.delete_file(path)

        path_hash = hashlib.sha256(path.encode()).hexdigest()[:12]
        ids = [f"{path_hash}_{i}" for i in range(len(texts))]

        self._collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,  # type: ignore[arg-type]
            metadatas=metadatas,  # type: ignore[arg-type]
        )
        return len(texts)

    def search(
        self,
        query_embedding: list[float],
        n_results: int = 5,
        where: dict | None = None,
    ) -> list[dict]:
        """Semantic search. Returns up to n_results hits ordered by similarity.

        Each result dict has keys: id, text, metadata, distance.
        Returns [] if the index is empty.
        """
        if self._vector_dimension is not None and len(query_embedding) != self._vector_dimension:
            raise EmbeddingConfigMismatch(
                f"Query embedding has dimension {len(query_embedding)} but index expects "
                f"{self._vector_dimension}. Ensure the same embedding model is used for "
                "indexing and querying."
            )

        total = self._collection.count()
        if total == 0:
            return []

        n = min(n_results, total)
        kwargs: dict = {
            "query_embeddings": [query_embedding],
            "n_results": n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        out = []
        ids = (results.get("ids") or [[]])[0]  # type: ignore[index]
        docs = (results.get("documents") or [[]])[0]  # type: ignore[index]
        metas = (results.get("metadatas") or [[]])[0]  # type: ignore[index]
        dists = (results.get("distances") or [[]])[0]  # type: ignore[index]

        for i, doc_id in enumerate(ids):
            out.append({
                "id": doc_id,
                "text": docs[i],
                "metadata": metas[i],
                "distance": dists[i],
            })
        return out

    def delete_file(self, path: str) -> None:
        """Remove all chunks belonging to path. No-op if path not indexed."""
        try:
            existing = self._collection.get(where={"source": path})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
        except Exception:
            pass

    def count(self) -> int:
        return self._collection.count()

    def stats(self) -> dict:
        total = self._collection.count()
        if total == 0:
            return {"total_chunks": 0, "total_files": 0}
        all_meta = self._collection.get(include=["metadatas"])
        sources = {m.get("source", "") for m in (all_meta["metadatas"] or [])}  # type: ignore[union-attr]
        stored = self._collection.metadata or {}
        return {
            "total_chunks": total,
            "total_files": len(sources),
            "embedding_model": stored.get(_META_MODEL, "unknown"),
            "distance_metric": stored.get(_META_METRIC, "unknown"),
            "vector_dimension": stored.get(_META_DIM, "unknown"),
        }

    def clear(self) -> None:
        """Delete and recreate the collection, removing all indexed data."""
        stored_meta = self._collection.metadata or {}
        self._client.delete_collection(_COLLECTION_NAME)

        # Rebuild metadata for the new empty collection: preserve stored fields
        # but override with current config if provided.
        hnsw_space = self._distance_metric if self._distance_metric in ("cosine", "l2", "ip") else "cosine"
        new_meta: dict = {**stored_meta, "hnsw:space": hnsw_space}
        if self._embedding_model is not None:
            new_meta[_META_MODEL] = self._embedding_model
            new_meta[_META_METRIC] = self._distance_metric
        if self._vector_dimension is not None:
            new_meta[_META_DIM] = self._vector_dimension

        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata=new_meta,
        )
