from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    id: str = ""
    source_id: str = ""     # inherited from parent document
    capture_id: str = ""    # inherited from parent document
    document_id: str = ""   # inherited from parent document
    index: int = 0
    heading: str | None = None      # nearest heading above this chunk
    content: str = ""
    token_count: int = 0
    block_ids: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    confidence_summary: dict = Field(default_factory=dict)  # {extracted, inferred, ambiguous} counts
    metadata: dict = Field(default_factory=dict)
    schema_version: str = "1.2"

    def compute_id(self) -> Chunk:
        digest = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        if self.document_id:
            raw = f"{self.document_id}:{self.index}:{digest}"
        else:
            raw = f"{self.index}:{digest}"  # backward compat when document_id not yet set
        self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self
