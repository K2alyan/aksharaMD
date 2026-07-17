from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .asset import Asset
from .block import Block


class Document(BaseModel):
    id: str = ""
    source_id: str = ""     # stable logical source identity (SHA-256 of normalized locator)
    capture_id: str = ""    # SHA-256 of raw source bytes at ingest time
    document_id: str = ""   # content-derived; excludes compiled_at and source path
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
    schema_version: str = "1.2"

    def compute_id(self) -> Document:
        canonical = (
            f"{self.file_type}:{self.pages}:"
            + ";".join(
                f"{b.type}:{b.page or 0}:{b.index}:{b.checksum}"
                for b in sorted(self.blocks, key=lambda b: b.index)
            )
        )
        self.document_id = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        self.id = self.document_id  # backward-compat alias
        return self
