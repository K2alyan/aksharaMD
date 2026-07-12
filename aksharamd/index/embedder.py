from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""


class SentenceTransformerEmbedder(Embedder):
    """Local CPU/GPU embedder using sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model: object | None = None  # lazy-loaded on first embed()

    def _load(self) -> None:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ImportError(
                    'sentence-transformers is required for local indexing. '
                    'Install with: pip install "aksharamd[index]"'
                ) from exc
            self._model = SentenceTransformer(self._model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        embeddings = self._model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]
        return [e.tolist() for e in embeddings]

    @property
    def dimension(self) -> int:
        self._load()
        return self._model.get_sentence_embedding_dimension()  # type: ignore[union-attr]


class OllamaEmbedder(Embedder):
    """Embedder backed by a local Ollama server (http://localhost:11434)."""

    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dim: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import requests  # already in base deps

        results = []
        for text in texts:
            resp = requests.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
                timeout=30,
            )
            resp.raise_for_status()
            results.append(resp.json()["embedding"])
        return results

    @property
    def dimension(self) -> int:
        if self._dim is None:
            result = self.embed([" "])
            self._dim = len(result[0]) if result else 768
        return self._dim


def get_embedder(model: str = "all-MiniLM-L6-v2") -> Embedder:
    """Factory: 'ollama:<model>' routes to OllamaEmbedder, everything else to SentenceTransformer.

    Optionally specify a custom base URL: 'ollama:<model>@http://host:port'.
    """
    if model.startswith("ollama:"):
        remainder = model[len("ollama:"):]
        if "@" in remainder:
            ollama_model, base_url = remainder.split("@", 1)
        else:
            ollama_model, base_url = remainder, "http://localhost:11434"
        return OllamaEmbedder(model=ollama_model, base_url=base_url)
    return SentenceTransformerEmbedder(model_name=model)
