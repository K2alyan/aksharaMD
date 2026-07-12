from __future__ import annotations

import hashlib
from pathlib import Path

_COLLECTION_NAME = "aksharamd_index"


class VectorStore:
    """ChromaDB-backed local vector store.

    All data lives on disk at chromadb_path — nothing is uploaded or shared.
    """

    def __init__(self, chromadb_path: Path) -> None:
        try:
            import chromadb  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                'chromadb is required for local indexing. '
                'Install with: pip install "aksharamd[index]"'
            ) from exc

        chromadb_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chromadb_path))
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
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
        return {"total_chunks": total, "total_files": len(sources)}

    def clear(self) -> None:
        """Delete and recreate the collection, removing all indexed data."""
        self._client.delete_collection(_COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
