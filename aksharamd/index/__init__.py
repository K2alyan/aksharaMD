"""Local document indexing: watch-folder -> compile -> embed -> ChromaDB.

All data stays on-device. No cloud calls, no uploads.

Quickstart::

    aksharamd watch ~/Documents/inbox
    aksharamd index search "what does the NDA say about IP?"
"""
from __future__ import annotations

from .config import IndexConfig
from .embedder import Embedder, OllamaEmbedder, SentenceTransformerEmbedder, get_embedder
from .queue import IndexQueue, Job
from .store import VectorStore
from .watcher import InboxWatcher
from .worker import process_file

__all__ = [
    "IndexConfig",
    "IndexQueue",
    "Job",
    "VectorStore",
    "Embedder",
    "SentenceTransformerEmbedder",
    "OllamaEmbedder",
    "get_embedder",
    "InboxWatcher",
    "process_file",
]
