from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from .models.document import Document
from .models.chunk import Chunk
from .models.manifest import Manifest
from .models.validation import ValidationReport, ValidationIssue, Severity


@dataclass
class CompilationContext:
    source: str
    output_dir: str = "output"

    document: Document | None = None
    chunks: list[Chunk] = field(default_factory=list)
    manifest: Manifest | None = None
    validation: ValidationReport = field(default_factory=ValidationReport)

    # stats accumulated during pipeline
    original_tokens: int = 0
    duplicate_blocks_removed: int = 0
    headers_removed: int = 0
    footers_removed: int = 0

    def add_issue(self, issue: ValidationIssue) -> None:
        self.validation.issues.append(issue)
        if issue.severity == Severity.ERROR:
            self.validation.passed = False

    def warn(self, code: str, message: str, **kwargs) -> None:
        self.add_issue(ValidationIssue(severity=Severity.WARNING, code=code, message=message, **kwargs))

    def error(self, code: str, message: str, **kwargs) -> None:
        self.add_issue(ValidationIssue(severity=Severity.ERROR, code=code, message=message, **kwargs))
