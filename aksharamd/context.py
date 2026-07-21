from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .models.chunk import Chunk
from .models.document import Document
from .models.manifest import Manifest
from .models.validation import Severity, ValidationIssue, ValidationReport

if TYPE_CHECKING:
    from .packaging.models import DocumentPackagePlan, PackageAssetReference
    from .packaging.payload import LLMPayload


@dataclass
class CompilationContext:
    source: str
    output_dir: str = "output"

    source_id: str = ""   # populated by compiler after source resolution
    capture_id: str = ""  # SHA-256 of raw source bytes; populated by compiler

    document: Document | None = None
    chunks: list[Chunk] = field(default_factory=list)
    manifest: Manifest | None = None
    validation: ValidationReport = field(default_factory=ValidationReport)

    # stats accumulated during pipeline
    original_tokens: int = 0
    duplicate_blocks_removed: int = 0
    headers_removed: int = 0
    footers_removed: int = 0

    # When True: no URL/S3 fetching, no subprocess calls (LibreOffice/Pandoc),
    # no ML inference (Whisper/OCR/Marker/pix2tex), archive listing only.
    safe_mode: bool = False

    # optional progress callback — set by Compiler when on_stage is provided;
    # parsers call ctx.progress("message") to surface fine-grained events
    progress: Callable[[str], None] | None = field(default=None, repr=False, compare=False)

    # package artifacts — populated only when compile_package() is used
    package_plan: DocumentPackagePlan | None = field(default=None)
    package_assets: list[PackageAssetReference] = field(default_factory=list)
    package_payload: LLMPayload | None = field(default=None)

    # KV detection profile — controls which heuristic paths are active for
    # the post-parse KeyValueGroup promoter. Default (None) resolves to
    # KeyValueDetectionProfile() with heuristics disabled. Set to
    # KeyValueDetectionProfile.experimental() to enable inline+adjacent
    # heuristics (calibration/evaluation only).
    kv_profile: object | None = field(default=None)

    # OCR backend selection (PR 94c). "tesseract" (the default) preserves
    # the historical per-page Tesseract path exactly. "unlimited_ocr"
    # routes OCR-required pages through the UnlimitedOcrBackend after an
    # availability check succeeds. No "auto" selection — the compiler
    # never silently falls back between backends.
    ocr_backend: str = "tesseract"

    def add_issue(self, issue: ValidationIssue) -> None:
        self.validation.issues.append(issue)
        if issue.severity == Severity.ERROR:
            self.validation.passed = False

    def warn(self, code: str, message: str, **kwargs) -> None:
        self.add_issue(ValidationIssue(severity=Severity.WARNING, code=code, message=message, **kwargs))

    def error(self, code: str, message: str, **kwargs) -> None:
        self.add_issue(ValidationIssue(severity=Severity.ERROR, code=code, message=message, **kwargs))
