"""Typed dataclasses for the OCR Auto Policy v1 calibration harness.

The schema is versioned via :data:`HARNESS_SCHEMA_VERSION` — any additive or
breaking change bumps this and naturally invalidates all cached results
(see :mod:`benchmarks.ocr_auto_calibration.cache`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

HARNESS_SCHEMA_VERSION = "1"

Treatment = Literal["tesseract", "unlimited_ocr", "auto"]
PreferenceLabel = Literal["tesseract", "unlimited_ocr", "undetermined"]
HumanPreferenceLabel = Literal["tesseract", "unlimited_ocr"]


@dataclass(frozen=True)
class RunKey:
    """Stable identity for a (document, treatment, code, model, schema) tuple."""

    document_id: str
    treatment: Treatment
    aksharamd_commit: str
    model_revision: str
    harness_schema_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "document_id": self.document_id,
            "treatment": self.treatment,
            "aksharamd_commit": self.aksharamd_commit,
            "model_revision": self.model_revision,
            "harness_schema_version": self.harness_schema_version,
        }


@dataclass
class RunResult:
    """Everything measured for one (document, treatment) execution."""

    key: RunKey
    document_path: str
    document_sha256: str
    profile_class: str
    total_pages: int
    ocr_required_pages: int
    ocr_required_fraction: float
    auto_preferred_backend: str | None
    auto_selected_backend: str | None
    fallback_reason: str | None
    exit_status: int
    runtime_seconds: float
    peak_vram_mib: int | None
    output_sha256: str | None
    readiness_score: int | None
    quality_band: str | None
    warning_codes: list[str] = field(default_factory=list)
    output_markdown_length: int = 0
    output_paragraph_count: int = 0
    output_heading_count: int = 0
    output_image_ref_count: int = 0
    output_table_count: int = 0
    max_repeated_ngram_count: int = 0
    repetition_flag: bool = False
    source_page_provenance_complete: bool = True
    stderr_tail: str = ""
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Serialise nested RunKey as a plain dict for JSON round-trip.
        d["key"] = self.key.to_dict()
        return d


@dataclass
class DocumentSummary:
    """Roll-up of the three treatment RunResults for one document.

    ``acquisition`` records per-document provenance: source (parsebench /
    synthetic / failure / local), on-disk sha256, the sha256 recorded in
    the lockfile (ParseBench only), whether they match, and whether the
    document was actually fetched. For local optional assets absent on
    this machine, all three treatments will carry ``exit_status=64`` and
    ``error_message="skipped_missing_local_asset"``.
    """

    document_id: str
    profile_class: str
    tesseract: RunResult
    unlimited_ocr: RunResult
    auto: RunResult
    automatic_preference: PreferenceLabel
    human_preference: HumanPreferenceLabel | None
    final_preference: PreferenceLabel
    auto_matched_final_preference: bool | None
    review_reasons: list[str] = field(default_factory=list)
    acquisition: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "profile_class": self.profile_class,
            "tesseract": self.tesseract.to_dict(),
            "unlimited_ocr": self.unlimited_ocr.to_dict(),
            "auto": self.auto.to_dict(),
            "automatic_preference": self.automatic_preference,
            "human_preference": self.human_preference,
            "final_preference": self.final_preference,
            "auto_matched_final_preference": self.auto_matched_final_preference,
            "review_reasons": list(self.review_reasons),
            "acquisition": dict(self.acquisition),
        }


@dataclass
class RunReport:
    """Top-level harness output covering an entire corpus pass.

    ``corpus_provenance`` records envelope-level acquisition metadata:
    the ParseBench lockfile checksum used, the pinned ParseBench dataset
    revision, and counts of resolved/skipped documents by source. This
    lets a reviewer verify that the calibration was run against the
    lockfile snapshot they expect.
    """

    harness_schema_version: str
    aksharamd_commit: str
    model_revision: str
    run_started_at: str  # ISO8601 UTC
    run_completed_at: str  # ISO8601 UTC
    machine: dict[str, Any]
    corpus_size: int
    documents: list[DocumentSummary] = field(default_factory=list)
    corpus_provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness_schema_version": self.harness_schema_version,
            "aksharamd_commit": self.aksharamd_commit,
            "model_revision": self.model_revision,
            "run_started_at": self.run_started_at,
            "run_completed_at": self.run_completed_at,
            "machine": dict(self.machine),
            "corpus_size": self.corpus_size,
            "documents": [d.to_dict() for d in self.documents],
            "corpus_provenance": dict(self.corpus_provenance),
        }


# ── Round-trip helpers ────────────────────────────────────────────────


def run_key_from_dict(d: dict[str, Any]) -> RunKey:
    return RunKey(
        document_id=str(d["document_id"]),
        treatment=d["treatment"],  # type: ignore[arg-type]
        aksharamd_commit=str(d["aksharamd_commit"]),
        model_revision=str(d["model_revision"]),
        harness_schema_version=str(d["harness_schema_version"]),
    )


def run_result_from_dict(d: dict[str, Any]) -> RunResult:
    key_dict = d["key"]
    return RunResult(
        key=run_key_from_dict(key_dict),
        document_path=str(d["document_path"]),
        document_sha256=str(d["document_sha256"]),
        profile_class=str(d["profile_class"]),
        total_pages=int(d["total_pages"]),
        ocr_required_pages=int(d["ocr_required_pages"]),
        ocr_required_fraction=float(d["ocr_required_fraction"]),
        auto_preferred_backend=d.get("auto_preferred_backend"),
        auto_selected_backend=d.get("auto_selected_backend"),
        fallback_reason=d.get("fallback_reason"),
        exit_status=int(d["exit_status"]),
        runtime_seconds=float(d["runtime_seconds"]),
        peak_vram_mib=d.get("peak_vram_mib"),
        output_sha256=d.get("output_sha256"),
        readiness_score=d.get("readiness_score"),
        quality_band=d.get("quality_band"),
        warning_codes=list(d.get("warning_codes", [])),
        output_markdown_length=int(d.get("output_markdown_length", 0)),
        output_paragraph_count=int(d.get("output_paragraph_count", 0)),
        output_heading_count=int(d.get("output_heading_count", 0)),
        output_image_ref_count=int(d.get("output_image_ref_count", 0)),
        output_table_count=int(d.get("output_table_count", 0)),
        max_repeated_ngram_count=int(d.get("max_repeated_ngram_count", 0)),
        repetition_flag=bool(d.get("repetition_flag", False)),
        source_page_provenance_complete=bool(
            d.get("source_page_provenance_complete", True)
        ),
        stderr_tail=str(d.get("stderr_tail", "")),
        error_message=d.get("error_message"),
    )


def document_summary_from_dict(d: dict[str, Any]) -> DocumentSummary:
    return DocumentSummary(
        document_id=str(d["document_id"]),
        profile_class=str(d["profile_class"]),
        tesseract=run_result_from_dict(d["tesseract"]),
        unlimited_ocr=run_result_from_dict(d["unlimited_ocr"]),
        auto=run_result_from_dict(d["auto"]),
        automatic_preference=d["automatic_preference"],
        human_preference=d.get("human_preference"),
        final_preference=d["final_preference"],
        auto_matched_final_preference=d.get("auto_matched_final_preference"),
        review_reasons=list(d.get("review_reasons", [])),
        acquisition=dict(d.get("acquisition", {})),
    )


def run_report_from_dict(d: dict[str, Any]) -> RunReport:
    return RunReport(
        harness_schema_version=str(d["harness_schema_version"]),
        aksharamd_commit=str(d["aksharamd_commit"]),
        model_revision=str(d["model_revision"]),
        run_started_at=str(d["run_started_at"]),
        run_completed_at=str(d["run_completed_at"]),
        machine=dict(d["machine"]),
        corpus_size=int(d["corpus_size"]),
        documents=[document_summary_from_dict(x) for x in d.get("documents", [])],
        corpus_provenance=dict(d.get("corpus_provenance", {})),
    )
