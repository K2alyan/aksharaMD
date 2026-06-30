from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .asset import Asset
from .block import Block


class Document(BaseModel):
    id: str = ""
    source: str
    file_type: str = ""
    title: str | None = None
    author: str | None = None
    created: str | None = None
    pages: int = 0
    blocks: list[Block] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    compiled_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def compute_id(self) -> Document:
        raw = f"{self.source}:{self.file_type}:{self.pages}"
        self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self
