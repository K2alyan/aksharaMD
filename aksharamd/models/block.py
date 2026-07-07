from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CODE_BLOCK = "code_block"
    IMAGE = "image"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    ADMONITION = "admonition"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
    METADATA = "metadata"
    PAGE_BREAK = "page_break"
    MATH = "math"
    UNKNOWN = "unknown"


class ExtractionConfidence(StrEnum):
    EXTRACTED = "extracted"   # cleanly parsed from native structure (text layer, proper DOM, schema)
    INFERRED  = "inferred"    # derived with moderate uncertainty (whitespace tables, font-size headings)
    AMBIGUOUS = "ambiguous"   # low-fidelity path (OCR, olefile stream, binary fallback)


class Block(BaseModel):
    id: str = ""
    type: BlockType
    content: str
    level: int | None = None        # heading level 1-6
    language: str | None = None     # code block language identifier
    page: int | None = None         # source page number (1-indexed)
    index: int = 0                  # position in document block list
    confidence: ExtractionConfidence = ExtractionConfidence.EXTRACTED
    metadata: dict = Field(default_factory=dict)
    checksum: str = ""

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 6):
            raise ValueError(f"Heading level must be 1–6, got {v}")
        return v

    @model_validator(mode="after")
    def _compute_derived(self) -> Block:
        if not self.checksum:
            self.checksum = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        if not self.id:
            raw = f"{self.type}:{self.index}:{self.checksum}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self
