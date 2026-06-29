from __future__ import annotations
import hashlib
from enum import Enum
from pydantic import BaseModel, Field, model_validator


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CODE_BLOCK = "code_block"
    IMAGE = "image"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
    METADATA = "metadata"
    PAGE_BREAK = "page_break"
    UNKNOWN = "unknown"


class Block(BaseModel):
    id: str = ""
    type: BlockType
    content: str
    level: int | None = None        # heading level 1-6
    language: str | None = None     # code block language identifier
    page: int | None = None         # source page number (1-indexed)
    index: int = 0                  # position in document block list
    metadata: dict = Field(default_factory=dict)
    checksum: str = ""

    @model_validator(mode="after")
    def _compute_derived(self) -> "Block":
        if not self.checksum:
            self.checksum = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        if not self.id:
            raw = f"{self.type}:{self.index}:{self.checksum}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self
