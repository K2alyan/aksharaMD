from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class IndexConfig:
    index_dir: Path = field(default_factory=lambda: Path.home() / ".aksharamd" / "index")
    embedding_model: str = "all-MiniLM-L6-v2"
    distance_metric: str = "cosine"
    min_readiness_score: int = 70
    debounce_seconds: float = 2.0
    worker_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        self.index_dir = Path(self.index_dir)

    @property
    def db_path(self) -> Path:
        return self.index_dir / "queue.db"

    @property
    def chromadb_path(self) -> Path:
        return self.index_dir / "chromadb"
