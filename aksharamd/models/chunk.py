from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    id: str = ""
    index: int = 0
    heading: str | None = None      # nearest heading above this chunk
    content: str = ""
    token_count: int = 0
    block_ids: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict = Field(default_factory=dict)

    def compute_id(self) -> Chunk:
        digest = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        raw = f"{self.index}:{digest}"
        self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self
