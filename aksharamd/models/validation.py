from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationIssue(BaseModel):
    severity: Severity
    code: str
    message: str
    page: int | None = None
    block_id: str | None = None
    source: str | None = None
    metadata: dict = Field(default_factory=dict)


class ValidationReport(BaseModel):
    passed: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)
    schema_version: str = "1.1"

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]
