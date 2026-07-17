from __future__ import annotations

import hashlib
import json
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalize_for_hash(text: str) -> str:
    """NFC Unicode + LF newline normalization applied before any content hashing."""
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")


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
    KEY_VALUE_GROUP = "key_value_group"
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
    table_data: TableData | None = None
    key_value_group: KeyValueGroup | None = None

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 6):
            raise ValueError(f"Heading level must be 1–6, got {v}")
        return v

    @model_validator(mode="after")
    def _compute_derived(self) -> Block:
        # 1. Always derive content from table_data (enforces canonical invariant)
        if self.type == BlockType.TABLE and self.table_data is not None:
            from ..renderers.table_markdown import render_table_markdown
            self.content = render_table_markdown(self.table_data)

        # 1b. Derive content from key_value_group (like TABLE derives from table_data)
        if self.type == BlockType.KEY_VALUE_GROUP and self.key_value_group is not None:
            from ..renderers.key_value_markdown import render_key_value_group
            self.content = render_key_value_group(self.key_value_group)

        # 2. Compute checksum
        if not self.checksum:
            if self.type == BlockType.TABLE and self.table_data is not None:
                payload = json.dumps(
                    self.table_data.canonical_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                self.checksum = hashlib.sha256(payload.encode()).hexdigest()[:16]
            elif self.type == BlockType.KEY_VALUE_GROUP and self.key_value_group is not None:
                payload = json.dumps(
                    self.key_value_group.canonical_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                self.checksum = hashlib.sha256(payload.encode()).hexdigest()[:16]
            else:
                self.checksum = hashlib.sha256(
                    _normalize_for_hash(self.content).encode()
                ).hexdigest()[:16]

        # 3. Compute block ID (unchanged)
        if not self.id:
            raw = f"{self.type}:{self.page or 0}:{self.index}:{self.checksum}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]

        return self

    @classmethod
    def from_table(
        cls,
        table_data: TableData,
        *,
        page: int | None = None,
        index: int = 0,
        confidence: ExtractionConfidence = ExtractionConfidence.EXTRACTED,
        metadata: dict | None = None,
    ) -> Block:
        """Canonical constructor for structured table blocks.

        Parsers MUST use this method (not Block(type=TABLE, ...)) to ensure
        block.content is always derived from table_data.
        """
        return cls(
            type=BlockType.TABLE,
            content="",          # _compute_derived will overwrite from table_data
            table_data=table_data,
            page=page,
            index=index,
            confidence=confidence,
            metadata=metadata or {},
        )

    @classmethod
    def from_key_value_group(
        cls,
        group: KeyValueGroup,
        *,
        page: int | None = None,
        index: int = 0,
        confidence: ExtractionConfidence = ExtractionConfidence.INFERRED,
        metadata: dict | None = None,
    ) -> Block:
        """Canonical constructor for key-value group blocks.

        Parsers MUST use this method to ensure block.content is always
        derived from key_value_group.
        """
        return cls(
            type=BlockType.KEY_VALUE_GROUP,
            content="",      # _compute_derived fills this from key_value_group
            key_value_group=group,
            page=page,
            index=index,
            confidence=confidence,
            metadata=metadata or {},
        )


# Avoid circular import: TableData is defined in .table which imports nothing from .block
from .key_value import KeyValueGroup  # noqa: E402
from .table import TableData  # noqa: E402

Block.model_rebuild()
