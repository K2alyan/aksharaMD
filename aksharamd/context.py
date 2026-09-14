from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .models.chunk import Chunk
from .models.document import Document
from .models.manifest import Manifest
from .models.validation import Severity, ValidationIssue, ValidationReport

if TYPE_CHECKING:
    from .assessment.models import TaskProfile
    from .packaging.models import DocumentPackagePlan, PackageAssetReference
    from .packaging.payload import LLMPayload
    from .plugins.ocr_backends.auto_selector import AutoOcrDecision


@dataclass
class CompilationContext:
    source: str
    output_dir: str = "output"

    source_id: str = ""   # populated by compiler after source resolution
    capture_id: str = ""  # SHA-256 of raw source bytes; populated by compiler
    # Parser provenance captured at the compiler boundary.  Versions and
    # configuration identities are deliberately nullable when unavailable.
    parser_name: str | None = None
    parser_version: str | None = None
    parser_configuration_id: str | None = None

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

    # OCR backend selection (PR 94c, extended in PR 100). One of:
    #   * "tesseract" (default) — historical per-page Tesseract path,
    #     byte-for-byte unchanged.
    #   * "unlimited_ocr" — routes OCR-required pages through the
    #     UnlimitedOcrBackend after a hard-fail CLI availability check.
    #   * "auto" (PR 100) — Auto Policy v1 applies AFTER pdf.py has
    #     classified pages; picks "unlimited_ocr" when the document
    #     meets both thresholds and UOC is runnable, otherwise
    #     "tesseract". A loud (informational) warning fires on
    #     fallback. Explicit choices never fall back.
    ocr_backend: str = "tesseract"

    # PR 100: populated only when ``ocr_backend == "auto"``. Carries the
    # structured Auto Policy v1 decision for later manifest
    # serialization. ``None`` for explicit backend choices.
    ocr_auto_decision: AutoOcrDecision | None = field(default=None)

    # An optional, caller-validated purpose contract.  Exporters use it only
    # when producing a source-grounded assessment; it does not alter parsing,
    # optimization, or legacy readiness scoring.
    task_profile: TaskProfile | None = field(default=None)

    # Output Safety Policy v1 milestone: populated only when
    # ``ocr_backend == "auto"`` initially selected UOC AND the UOC
    # output tripped Policy v1 (any anchor page's
    # ``repetition_signal.detected`` was True). Carries the audit
    # payload the compiler propagates into the manifest's
    # ``ocr_output_safety_*`` / ``ocr_final_backend`` /
    # ``ocr_discarded_backend`` fields. Every per-page entry is
    # bounded; no raw markdown, no unbounded ngram text. ``None`` when
    # the fallback did not fire.
    ocr_output_safety_audit: dict | None = field(default=None)

    # True when the compilation used ``Compiler(parser_adapter=...)`` to
    # convert the source into markdown before the built-in MarkdownParser
    # ingested it. In that case the resulting Document is fundamentally
    # markdown, regardless of the on-disk source extension. PDF-geometry
    # detectors (MISSING_PAGE, LOW_TEXT_DENSITY, W_MULTICOLUMN_ORDER,
    # W_TABLE_MISSING, W_TABLE_EXPECTED_NOT_EXTRACTED,
    # W_HEADER_FOOTER_TABLE_GARBLED, W_ENCODING_ARTIFACTS) must skip these
    # Documents cleanly — they lack the block/page geometry those detectors
    # need and would fire false positives if forced to evaluate against a
    # source-detected ``pdf`` file_type. User-facing manifest reporting still
    # records the original source file_type; only the scoring-relevant
    # ``ctx.document.file_type`` stays as ``md``.
    parser_provided_via_adapter: bool = False

    # Raw source bytes captured at the compiler boundary. Populated for
    # both the built-in-parser path (bytes read for capture_id hashing are
    # mirrored here) and the parser-adapter path (ParserInput.data is
    # mirrored here). Consumed by substance-detection Tiers 3+ (geometric
    # cross-reference, dropped-region detection) that need to compare the
    # parsed output against the source. Excluded from repr/compare because
    # a large binary buffer in the default repr would be catastrophic.
    # Access via ``raw_bytes()`` rather than the field directly so unknown
    # local sources can fall back to a lazy read.
    raw_source_bytes: bytes | None = field(default=None, repr=False, compare=False)

    # Per-detector wall-clock measurements populated by the DetectorBudget
    # context manager (P0.2). Keys are rule_ids or detector identifiers;
    # values are elapsed milliseconds. Detectors that do not opt in are
    # simply absent from the dict.
    detector_timings: dict[str, float] = field(default_factory=dict)

    # Names of detectors that exceeded their DetectorBudget in this
    # compile. Populated in the same __exit__ that emits the informational
    # W_DETECTOR_TIMEOUT warning; carried alongside detector_timings for
    # operators that want a quick "which detectors were slow?" view
    # without re-scanning the warnings list.
    detector_timeouts: list[str] = field(default_factory=list)

    def add_issue(self, issue: ValidationIssue) -> None:
        self.validation.issues.append(issue)
        if issue.severity == Severity.ERROR:
            self.validation.passed = False

    def warn(self, code: str, message: str, **kwargs) -> None:
        self.add_issue(ValidationIssue(severity=Severity.WARNING, code=code, message=message, **kwargs))

    def error(self, code: str, message: str, **kwargs) -> None:
        self.add_issue(ValidationIssue(severity=Severity.ERROR, code=code, message=message, **kwargs))

    # 200 MB safety cap on the lazy fallback path — normal compiler-driven
    # population is already gated by AKSHARAMD_MAX_FILE_BYTES upstream.
    _LAZY_BYTES_CAP: int = 200 * 1024 * 1024

    def raw_bytes(self) -> bytes | None:
        """Return the raw source bytes, or None if unavailable.

        Preferred access point for substance detectors that compare parsed
        output against the source. Returns the field when populated
        (the compiler mirrors bytes here at the parse boundary). Falls
        back to a lazy read of ``self.source`` when the field is empty
        and the source is a readable local file within the 200 MB cap.
        Never fetches from URL/S3.
        """
        if self.raw_source_bytes is not None:
            return self.raw_source_bytes
        if not self.source:
            return None
        try:
            from pathlib import Path as _Path
            p = _Path(self.source)
            if not p.is_file():
                return None
            if p.stat().st_size > self._LAZY_BYTES_CAP:
                return None
            data = p.read_bytes()
        except OSError:
            return None
        self.raw_source_bytes = data  # memoize
        return data
