"""Exploratory V2 evidence contract for a source and external-parser output.

This module is deliberately separate from the frozen readiness score.  It
does not pretend that Markdown inherited a PDF format baseline.  Instead it
reports candidate-intrinsic evidence and source-comparison evidence as two
independent, auditable groups.
"""
from __future__ import annotations

import re
from collections import Counter
from enum import StrEnum

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import CandidateArtifact, EvidenceStatus, SourceArtifact, Verdict
from .service import Assessor

SOURCE_CANDIDATE_SCHEMA_VERSION = "2.0-exploratory"
SOURCE_CANDIDATE_POLICY_ID = "source-candidate-preservation-v2-exploratory"
SOURCE_CANDIDATE_IMPLEMENTATION_VERSION = "1"

_TEXT_RETENTION_MIN_TOKENS = 20
_TEXT_RETENTION_PASS = 0.95
_TEXT_RETENTION_FAIL = 0.80
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-./][^\W_]+)*", re.UNICODE)


class EvidenceScope(StrEnum):
    CANDIDATE_INTRINSIC = "candidate_intrinsic"
    SOURCE_COMPARISON = "source_comparison"


class DetectorStatus(StrEnum):
    ACTIVATED = "activated"
    ABSTAINED = "abstained"


class DetectorResult(BaseModel):
    """One detector receipt, including explicit activation or abstention."""

    model_config = ConfigDict(extra="forbid")

    detector_id: str
    detector_version: str
    scope: EvidenceScope
    status: DetectorStatus
    eligible: bool
    verdict: Verdict
    score: int | None = Field(default=None, ge=0, le=100)
    abstention_reason: str | None = None
    raw_evidence: dict[str, object] = Field(default_factory=dict)
    finding_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_is_coherent(self):
        if self.status == DetectorStatus.ACTIVATED:
            if not self.eligible or self.abstention_reason is not None:
                raise ValueError("activated detectors must be eligible and cannot have an abstention reason")
            if self.verdict == Verdict.UNDETERMINED or self.score is None:
                raise ValueError("activated detectors require a determined verdict and score")
        else:
            if self.abstention_reason is None:
                raise ValueError("abstained detectors require an abstention reason")
            if self.verdict != Verdict.UNDETERMINED or self.score is not None:
                raise ValueError("abstained detectors must be undetermined and unscored")
        return self


class EvidenceGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: EvidenceScope
    verdict: Verdict
    detectors: list[DetectorResult]


class SourceCandidateProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation: str = "aksharamd.assessment.source_candidate"
    implementation_version: str = SOURCE_CANDIDATE_IMPLEMENTATION_VERSION
    policy_id: str = SOURCE_CANDIDATE_POLICY_ID
    source_hash: str
    source_media_type: str
    candidate_hash: str
    candidate_media_type: str
    candidate_declared_source_hash: str | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    parser_configuration_id: str | None = None


class SourceCandidateAssessment(BaseModel):
    """Deterministic receipt with no combined source/candidate scalar."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SOURCE_CANDIDATE_SCHEMA_VERSION
    policy_id: str = SOURCE_CANDIDATE_POLICY_ID
    provenance: SourceCandidateProvenance
    candidate_intrinsic: EvidenceGroup
    source_comparison: EvidenceGroup


def _group(scope: EvidenceScope, detectors: list[DetectorResult]) -> EvidenceGroup:
    activated = [item for item in detectors if item.status == DetectorStatus.ACTIVATED]
    if not activated:
        verdict = Verdict.UNDETERMINED
    elif any(item.verdict == Verdict.FAIL for item in activated):
        verdict = Verdict.FAIL
    elif any(item.verdict == Verdict.CONCERN for item in activated):
        verdict = Verdict.CONCERN
    else:
        verdict = Verdict.PASS
    return EvidenceGroup(scope=scope, verdict=verdict, detectors=detectors)


def _mapped_candidate_detector(detector_id: str, dimension) -> DetectorResult:
    eligible = dimension.status == EvidenceStatus.OBSERVED
    if not eligible:
        return DetectorResult(
            detector_id=detector_id,
            detector_version="1",
            scope=EvidenceScope.CANDIDATE_INTRINSIC,
            status=DetectorStatus.ABSTAINED,
            eligible=False,
            verdict=Verdict.UNDETERMINED,
            abstention_reason="candidate media type is not supported by the intrinsic text check",
            raw_evidence={
                "dimension_status": dimension.status.value,
                "findings": [item.model_dump(mode="json") for item in dimension.findings],
            },
        )
    score = 100 if dimension.verdict == Verdict.PASS else 0
    return DetectorResult(
        detector_id=detector_id,
        detector_version="1",
        scope=EvidenceScope.CANDIDATE_INTRINSIC,
        status=DetectorStatus.ACTIVATED,
        eligible=True,
        verdict=dimension.verdict,
        score=score,
        raw_evidence={
            "evidence": [item.model_dump(mode="json") for item in dimension.evidence],
            "findings": [item.model_dump(mode="json") for item in dimension.findings],
        },
        finding_codes=[item.code for item in dimension.findings],
    )


def _candidate_intrinsic(candidate: CandidateArtifact) -> EvidenceGroup:
    # Reuse the established checks, but intentionally ignore its source-fidelity
    # dimension: the source-comparison group below owns that evidence.
    assessment = Assessor().assess(candidate=candidate)
    detectors = [
        _mapped_candidate_detector(
            "candidate.markdown_structure",
            assessment.dimensions["structural_usability"],
        ),
        _mapped_candidate_detector(
            "candidate.text_integrity",
            assessment.dimensions["content_integrity"],
        ),
    ]
    return _group(EvidenceScope.CANDIDATE_INTRINSIC, detectors)


def _source_identity(source: SourceArtifact, candidate: CandidateArtifact) -> DetectorResult:
    detector_id = "source.candidate_identity_binding"
    declared = candidate.original_source_hash
    if declared is None:
        return DetectorResult(
            detector_id=detector_id,
            detector_version="1",
            scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED,
            eligible=False,
            verdict=Verdict.UNDETERMINED,
            abstention_reason="candidate did not declare original_source_hash",
            raw_evidence={"source_hash": source.content_hash, "declared_source_hash": None},
        )
    matches = declared == source.content_hash
    return DetectorResult(
        detector_id=detector_id,
        detector_version="1",
        scope=EvidenceScope.SOURCE_COMPARISON,
        status=DetectorStatus.ACTIVATED,
        eligible=True,
        verdict=Verdict.PASS if matches else Verdict.FAIL,
        score=100 if matches else 0,
        raw_evidence={"source_hash": source.content_hash, "declared_source_hash": declared},
        finding_codes=[] if matches else ["SOURCE_IDENTITY_MISMATCH"],
    )


def _candidate_text(candidate: CandidateArtifact) -> str | None:
    if candidate.media_type not in {"text/markdown", "text/plain"}:
        return None
    try:
        return candidate.data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def _pdf_observations(source: SourceArtifact) -> tuple[list[str], list[int], str | None]:
    """Return source text fragments, one-based table pages, or an error."""
    if source.media_type != "application/pdf":
        return [], [], "source media type is not application/pdf"
    try:
        import pymupdf
    except ImportError:
        return [], [], "PyMuPDF is unavailable"

    fragments: list[str] = []
    table_pages: list[int] = []
    try:
        with pymupdf.open(stream=source.data, filetype="pdf") as pdf:
            for page_number, page in enumerate(pdf, start=1):
                fragments.append(page.get_text("text") or "")
                # PyMuPDF's table finder is source geometry evidence.  Failure
                # on any page makes this detector abstain rather than silently
                # treating the page as table-free.
                tables = page.find_tables(paths=page.get_drawings())
                if len(tables.tables) > 0:
                    table_pages.extend([page_number] * len(tables.tables))
    except Exception as exc:  # malformed/encrypted sources cannot be compared
        return [], [], f"PDF source inspection failed: {type(exc).__name__}"
    return fragments, table_pages, None


def _text_retention(source_text: str, candidate_text: str | None, source_error: str | None) -> DetectorResult:
    detector_id = "source.pdf_text_token_retention"
    if source_error is not None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason=source_error,
        )
    if candidate_text is None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason="candidate must be UTF-8 text/markdown or text/plain",
        )

    source_tokens = _tokens(source_text)
    if len(source_tokens) < _TEXT_RETENTION_MIN_TOKENS:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=True, verdict=Verdict.UNDETERMINED,
            abstention_reason=(f"source text has {len(source_tokens)} tokens; "
                               f"minimum is {_TEXT_RETENTION_MIN_TOKENS}"),
            raw_evidence={"source_token_count": len(source_tokens)},
        )

    source_counts = Counter(source_tokens)
    candidate_counts = Counter(_tokens(candidate_text))
    retained = sum(min(count, candidate_counts[token]) for token, count in source_counts.items())
    coverage = retained / len(source_tokens)
    missing = list((source_counts - candidate_counts).elements())
    verdict = (Verdict.PASS if coverage >= _TEXT_RETENTION_PASS else
               Verdict.CONCERN if coverage >= _TEXT_RETENTION_FAIL else Verdict.FAIL)
    return DetectorResult(
        detector_id=detector_id,
        detector_version="1",
        scope=EvidenceScope.SOURCE_COMPARISON,
        status=DetectorStatus.ACTIVATED,
        eligible=True,
        verdict=verdict,
        score=round(coverage * 100),
        raw_evidence={
            "source_token_count": len(source_tokens),
            "candidate_token_count": sum(candidate_counts.values()),
            "retained_source_token_count": retained,
            "retention_ratio": coverage,
            "pass_threshold": _TEXT_RETENTION_PASS,
            "fail_threshold": _TEXT_RETENTION_FAIL,
            "missing_source_token_count": len(missing),
            "missing_token_sample": missing[:20],
            "tokenization": "unicode_alphanumeric_casefolded_multiset_v1",
        },
        finding_codes=[] if verdict == Verdict.PASS else ["SOURCE_TEXT_TOKEN_LOSS"],
    )


def _markdown_table_count(text: str) -> int:
    return sum(1 for token in MarkdownIt().enable("table").parse(text) if token.type == "table_open")


def _table_retention(table_pages: list[int], candidate_text: str | None, source_error: str | None) -> DetectorResult:
    detector_id = "source.pdf_table_structure_retention"
    if source_error is not None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason=source_error,
        )
    if candidate_text is None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason="candidate must be UTF-8 text/markdown or text/plain",
        )
    if not table_pages:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=True, verdict=Verdict.UNDETERMINED,
            abstention_reason="no source tables were detected",
            raw_evidence={"source_table_count": 0, "source_table_pages": []},
        )

    source_count = len(table_pages)
    candidate_count = _markdown_table_count(candidate_text)
    ratio = min(1.0, candidate_count / source_count)
    verdict = Verdict.PASS if candidate_count >= source_count else Verdict.FAIL
    return DetectorResult(
        detector_id=detector_id,
        detector_version="1",
        scope=EvidenceScope.SOURCE_COMPARISON,
        status=DetectorStatus.ACTIVATED,
        eligible=True,
        verdict=verdict,
        score=round(ratio * 100),
        raw_evidence={
            "source_table_count": source_count,
            "source_table_pages": table_pages,
            "candidate_markdown_table_count": candidate_count,
            "retention_ratio": ratio,
            "source_backend": "pymupdf.find_tables",
            "candidate_syntax": "markdown_it_table_v1",
            "scope_note": "Count preservation only; cell-level fidelity is not established.",
        },
        finding_codes=[] if verdict == Verdict.PASS else ["SOURCE_TABLE_STRUCTURE_MISSING"],
    )


def assess_source_candidate(
    *, source: SourceArtifact, candidate: CandidateArtifact,
) -> SourceCandidateAssessment:
    """Assess exact source/candidate bytes under the exploratory V2 policy."""
    candidate_text = _candidate_text(candidate)
    source_fragments, table_pages, source_error = _pdf_observations(source)
    comparison = _group(EvidenceScope.SOURCE_COMPARISON, [
        _source_identity(source, candidate),
        _text_retention("\n".join(source_fragments), candidate_text, source_error),
        _table_retention(table_pages, candidate_text, source_error),
    ])
    provenance = SourceCandidateProvenance(
        source_hash=source.content_hash,
        source_media_type=source.media_type,
        candidate_hash=candidate.content_hash,
        candidate_media_type=candidate.media_type,
        candidate_declared_source_hash=candidate.original_source_hash,
        parser_name=candidate.parser_name,
        parser_version=candidate.parser_version,
        parser_configuration_id=candidate.parser_configuration_id,
    )
    return SourceCandidateAssessment(
        provenance=provenance,
        candidate_intrinsic=_candidate_intrinsic(candidate),
        source_comparison=comparison,
    )
