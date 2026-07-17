from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, Field

from .table import BoundingBox


class KeyValueGroupType(StrEnum):
    SCHEDULE = "schedule"
    DIRECTORY = "directory"
    FORM = "form"
    CONTACT = "contact"
    METADATA = "metadata"
    SPECIFICATION = "specification"
    EVENT = "event"
    UNKNOWN = "unknown"


class KeyValueValueType(StrEnum):
    TEXT = "text"
    TIME = "time"
    DATE = "date"
    DATETIME = "datetime"
    PHONE = "phone"
    EMAIL = "email"
    URL = "url"
    CURRENCY = "currency"
    NUMBER = "number"
    PERCENTAGE = "percentage"
    ADDRESS = "address"
    UNKNOWN = "unknown"


class KeyValueEntry(BaseModel):
    key: str
    value: str
    normalized_key: str | None = None
    value_type: KeyValueValueType | None = None
    page: int | None = None
    bbox: BoundingBox | None = None
    source_block_ids: list[str] = Field(default_factory=list)
    confidence: str = "inferred"
    metadata: dict = Field(default_factory=dict)


class KeyValueGroup(BaseModel):
    entries: list[KeyValueEntry]
    title: str | None = None
    group_type: KeyValueGroupType = KeyValueGroupType.UNKNOWN
    page: int | None = None
    bbox: BoundingBox | None = None
    source_block_ids: list[str] = Field(default_factory=list)
    extraction_method: str = "inferred"
    confidence: str = "inferred"
    metadata: dict = Field(default_factory=dict)
    id: str = ""

    def canonical_payload(self) -> dict:
        """Deterministic semantic payload — excludes bbox, confidence, metadata."""
        return {
            "title": self.title,
            "group_type": str(self.group_type),
            "entries": [
                {"key": e.key, "value": e.value}
                for e in self.entries
            ],
        }

    def semantic_checksum(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
