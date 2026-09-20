"""Exploratory V2 evidence contract for a source and external-parser output.

This module is deliberately separate from the frozen readiness score.  It
does not pretend that Markdown inherited a PDF format baseline.  Instead it
reports candidate-intrinsic evidence and source-comparison evidence as two
independent, auditable groups.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import Literal

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
_HASH_PATTERN = r"^[0-9a-f]{64}$"

# Deterministic policy ceilings for cooperative/local inputs. PyMuPDF may
# allocate inside get_text/get_drawings before these post-call checks run, so
# these are not a hostile-input memory or time sandbox.
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_EXTRACTED_CHARS = 2_000_000
MAX_SOURCE_TOKENS = 250_000
MAX_TABLE_GEOMETRY_PAGES = 100
MAX_DRAWING_COMMANDS = 20_000
MAX_SOURCE_TABLES = 200
MAX_TABLE_CELLS = 10_000
MAX_CANDIDATE_BYTES = 20 * 1024 * 1024
MAX_CANDIDATE_TOKENS = 250_000


class EvidenceScope(StrEnum):
    CANDIDATE_INTRINSIC = "candidate_intrinsic"
    SOURCE_COMPARISON = "source_comparison"


class DetectorStatus(StrEnum):
    ACTIVATED = "activated"
    ABSTAINED = "abstained"


class DetectorResult(BaseModel):
    """One detector receipt, including explicit activation or abstention."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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
    quality_role: Literal["substantive", "provenance"] = "substantive"
    required_for_group: bool = True

    @model_validator(mode="after")
    def _status_is_coherent(self):
        if self.quality_role == "provenance" and self.required_for_group:
            raise ValueError("provenance detectors cannot be required for a quality verdict")
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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: EvidenceScope
    verdict: Verdict
    detectors: list[DetectorResult]

    @model_validator(mode="after")
    def _detector_scopes_match(self):
        if any(detector.scope != self.scope for detector in self.detectors):
            raise ValueError("all detector scopes must match their evidence group")
        return self


class SourceCandidateProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    implementation: Literal["aksharamd.assessment.source_candidate"] = "aksharamd.assessment.source_candidate"
    implementation_version: Literal["1"] = SOURCE_CANDIDATE_IMPLEMENTATION_VERSION
    policy_id: Literal["source-candidate-preservation-v2-exploratory"] = SOURCE_CANDIDATE_POLICY_ID
    source_hash: str = Field(pattern=_HASH_PATTERN)
    source_media_type: str
    candidate_hash: str = Field(pattern=_HASH_PATTERN)
    candidate_media_type: str
    candidate_declared_source_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    parser_name: str | None = None
    parser_version: str | None = None
    parser_configuration_id: str | None = None


class SourceCandidateAssessment(BaseModel):
    """Deterministic receipt with no combined source/candidate scalar."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["2.0-exploratory"] = SOURCE_CANDIDATE_SCHEMA_VERSION
    policy_id: Literal["source-candidate-preservation-v2-exploratory"] = SOURCE_CANDIDATE_POLICY_ID
    provenance: SourceCandidateProvenance
    candidate_intrinsic: EvidenceGroup
    source_comparison: EvidenceGroup

    @model_validator(mode="after")
    def _groups_and_policy_are_coherent(self):
        if self.candidate_intrinsic.scope != EvidenceScope.CANDIDATE_INTRINSIC:
            raise ValueError("candidate_intrinsic has the wrong scope")
        if self.source_comparison.scope != EvidenceScope.SOURCE_COMPARISON:
            raise ValueError("source_comparison has the wrong scope")
        if self.provenance.policy_id != self.policy_id:
            raise ValueError("provenance policy_id must match assessment policy_id")
        return self


def _group(scope: EvidenceScope, detectors: list[DetectorResult]) -> EvidenceGroup:
    considered = (
        [item for item in detectors if item.quality_role == "substantive"]
        if scope == EvidenceScope.SOURCE_COMPARISON else detectors
    )
    activated = [item for item in considered if item.status == DetectorStatus.ACTIVATED]
    incomplete = any(
        item.required_for_group and item.status == DetectorStatus.ABSTAINED
        for item in considered
    )
    if not activated:
        verdict = Verdict.UNDETERMINED
    elif any(item.verdict == Verdict.FAIL for item in activated):
        verdict = Verdict.FAIL
    elif incomplete:
        # A provenance-only PASS or one partial quality signal cannot establish
        # source preservation when another applicable detector abstained.
        verdict = Verdict.UNDETERMINED
    elif any(item.verdict == Verdict.CONCERN for item in activated):
        verdict = Verdict.CONCERN
    else:
        verdict = Verdict.PASS
    return EvidenceGroup(scope=scope, verdict=verdict, detectors=detectors)


def _revalidate_artifact_identity(artifact: SourceArtifact | CandidateArtifact, label: str) -> None:
    """Recheck mutable input models at the trust boundary.

    ``Artifact`` validates at construction, but its historical model is not
    frozen. Callers can therefore reassign fields before invoking this newer
    API. Never trust the earlier validation here.
    """
    if not isinstance(artifact.data, bytes):
        raise ValueError(f"{label}.data must be bytes at assessment time")
    if artifact.byte_size != len(artifact.data):
        raise ValueError(f"{label}.byte_size does not match artifact data at assessment time")
    digest = hashlib.sha256(artifact.data).hexdigest()
    if not re.fullmatch(_HASH_PATTERN, artifact.content_hash) or artifact.content_hash != digest:
        raise ValueError(f"{label}.content_hash does not match artifact data at assessment time")


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


def _candidate_intrinsic(candidate: CandidateArtifact, candidate_error: str | None) -> EvidenceGroup:
    # Reuse the established checks, but intentionally ignore its source-fidelity
    # dimension: the source-comparison group below owns that evidence.
    if candidate_error is not None:
        detectors = [
            DetectorResult(
                detector_id=detector_id,
                detector_version="1",
                scope=EvidenceScope.CANDIDATE_INTRINSIC,
                status=DetectorStatus.ABSTAINED,
                eligible=False,
                verdict=Verdict.UNDETERMINED,
                abstention_reason=candidate_error,
            )
            for detector_id in ("candidate.markdown_structure", "candidate.text_integrity")
        ]
        return _group(EvidenceScope.CANDIDATE_INTRINSIC, detectors)
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
            quality_role="provenance",
            required_for_group=False,
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
        quality_role="provenance",
        required_for_group=False,
    )


def _candidate_text(candidate: CandidateArtifact) -> tuple[str | None, str | None]:
    if candidate.media_type not in {"text/markdown", "text/plain"}:
        return None, "candidate must be UTF-8 text/markdown or text/plain"
    if candidate.byte_size > MAX_CANDIDATE_BYTES:
        return None, f"candidate byte size exceeds limit {MAX_CANDIDATE_BYTES}"
    try:
        return candidate.data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "candidate must be valid UTF-8"


def _token_counts(text: str, limit: int) -> tuple[Counter[str], int, bool]:
    counts: Counter[str] = Counter()
    total = 0
    for match in _TOKEN_RE.finditer(text):
        total += 1
        if total > limit:
            return Counter(), total, True
        counts[match.group(0).casefold()] += 1
    return counts, total, False


@dataclass(frozen=True)
class _TextObservation:
    text: str = ""
    page_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class _TableSignature:
    cells: tuple[str, ...]

    @property
    def digest(self) -> str:
        payload = json.dumps(self.cells, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _TableObservation:
    signatures: tuple[_TableSignature, ...] = ()
    pages: tuple[int, ...] = ()
    page_count: int = 0
    error: str | None = None


def _normalize_cell(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _pdf_observations(source: SourceArtifact) -> tuple[_TextObservation, _TableObservation]:
    """Inspect bounded PDF evidence with independent text/table failures."""
    if source.media_type != "application/pdf":
        error = "source media type is not application/pdf"
        return _TextObservation(error=error), _TableObservation(error=error)
    if source.byte_size > MAX_SOURCE_BYTES:
        error = f"source byte size exceeds limit {MAX_SOURCE_BYTES}"
        return _TextObservation(error=error), _TableObservation(error=error)
    try:
        import pymupdf
    except ImportError:
        error = "PyMuPDF is unavailable"
        return _TextObservation(error=error), _TableObservation(error=error)

    fragments: list[str] = []
    signatures: list[_TableSignature] = []
    table_pages: list[int] = []
    try:
        with pymupdf.open(stream=source.data, filetype="pdf") as pdf:
            page_count = len(pdf)
            if page_count > MAX_PDF_PAGES:
                error = f"PDF page count {page_count} exceeds limit {MAX_PDF_PAGES}"
                return (_TextObservation(page_count=page_count, error=error),
                        _TableObservation(page_count=page_count, error=error))

            text_error: str | None = None
            table_error = (f"PDF page count {page_count} exceeds table geometry limit "
                           f"{MAX_TABLE_GEOMETRY_PAGES}"
                           if page_count > MAX_TABLE_GEOMETRY_PAGES else None)
            extracted_chars = 0
            drawing_commands = 0
            table_cells = 0
            for page_number, page in enumerate(pdf, start=1):
                if text_error is None:
                    try:
                        fragment = page.get_text("text") or ""
                    except Exception as exc:  # detector-specific failure
                        text_error = f"PDF text extraction failed on page {page_number}: {type(exc).__name__}"
                        fragments.clear()
                    else:
                        extracted_chars += len(fragment)
                        if extracted_chars > MAX_EXTRACTED_CHARS:
                            text_error = f"PDF extracted characters exceed limit {MAX_EXTRACTED_CHARS}"
                            fragments.clear()
                        else:
                            fragments.append(fragment)

                if table_error is None:
                    try:
                        drawings = page.get_drawings()
                        drawing_commands += sum(len(drawing.get("items", ())) for drawing in drawings)
                        if drawing_commands > MAX_DRAWING_COMMANDS:
                            raise OverflowError(
                                f"drawing command count exceeds limit {MAX_DRAWING_COMMANDS}"
                            )
                        tables = page.find_tables(paths=drawings).tables
                        for table in tables:
                            rows = table.extract()
                            cells = tuple(_normalize_cell(cell) for row in rows for cell in row)
                            table_cells += len(cells)
                            if len(signatures) + 1 > MAX_SOURCE_TABLES:
                                raise OverflowError(f"source table count exceeds limit {MAX_SOURCE_TABLES}")
                            if table_cells > MAX_TABLE_CELLS:
                                raise OverflowError(f"source table cells exceed limit {MAX_TABLE_CELLS}")
                            signatures.append(_TableSignature(cells=cells))
                            table_pages.append(page_number)
                    except Exception as exc:  # table failure never suppresses text evidence
                        detail = str(exc) if isinstance(exc, OverflowError) else type(exc).__name__
                        table_error = f"PDF table inspection failed on page {page_number}: {detail}"
                        signatures.clear()
                        table_pages.clear()
    except Exception as exc:  # malformed/encrypted sources cannot be compared
        error = f"PDF source open failed: {type(exc).__name__}"
        return _TextObservation(error=error), _TableObservation(error=error)
    return (
        _TextObservation(text="\n".join(fragments), page_count=page_count, error=text_error),
        _TableObservation(
            signatures=tuple(signatures), pages=tuple(table_pages), page_count=page_count,
            error=table_error,
        ),
    )


def _text_retention(
    observation: _TextObservation,
    candidate_text: str | None,
    candidate_error: str | None,
) -> DetectorResult:
    detector_id = "source.pdf_text_token_retention"
    if observation.error is not None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason=observation.error,
            raw_evidence={"pdf_page_count": observation.page_count},
        )
    if candidate_text is None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason=candidate_error or "candidate text unavailable",
        )

    source_counts, source_total, source_over_limit = _token_counts(observation.text, MAX_SOURCE_TOKENS)
    if source_over_limit:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=True, verdict=Verdict.UNDETERMINED,
            abstention_reason=f"PDF source token count exceeds limit {MAX_SOURCE_TOKENS}",
            raw_evidence={"pdf_page_count": observation.page_count},
        )
    if source_total < _TEXT_RETENTION_MIN_TOKENS:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=True, verdict=Verdict.UNDETERMINED,
            abstention_reason=(f"source text has {source_total} tokens; "
                               f"minimum is {_TEXT_RETENTION_MIN_TOKENS}"),
            raw_evidence={"source_token_count": source_total, "pdf_page_count": observation.page_count},
        )

    candidate_counts, candidate_total, candidate_over_limit = _token_counts(
        candidate_text, MAX_CANDIDATE_TOKENS,
    )
    if candidate_over_limit:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=True, verdict=Verdict.UNDETERMINED,
            abstention_reason=f"candidate token count exceeds limit {MAX_CANDIDATE_TOKENS}",
            raw_evidence={"source_token_count": source_total, "pdf_page_count": observation.page_count},
        )
    retained = sum(min(count, candidate_counts[token]) for token, count in source_counts.items())
    coverage = retained / source_total
    missing_counts = source_counts - candidate_counts
    missing_count = sum(missing_counts.values())
    missing_sample = list(islice(missing_counts.elements(), 20))
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
            "source_token_count": source_total,
            "candidate_token_count": candidate_total,
            "retained_source_token_count": retained,
            "retention_ratio": coverage,
            "pass_threshold": _TEXT_RETENTION_PASS,
            "fail_threshold": _TEXT_RETENTION_FAIL,
            "missing_source_token_count": missing_count,
            "missing_token_sample": missing_sample,
            "pdf_page_count": observation.page_count,
            "tokenization": "unicode_alphanumeric_casefolded_multiset_v1",
        },
        finding_codes=[] if verdict == Verdict.PASS else ["SOURCE_TEXT_TOKEN_LOSS"],
    )


def _visible_inline_text(token) -> str:
    """Return rendered inline text, excluding Markdown representation tokens."""
    children = token.children or []
    visible: list[str] = []
    for child in children:
        if child.type in {"text", "code_inline"}:
            visible.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            visible.append(" ")
        elif child.type == "image":
            visible.append(child.content)
        elif child.type == "html_inline" and re.fullmatch(
            r"<br\s*/?>", child.content.strip(), flags=re.IGNORECASE,
        ):
            visible.append(" ")
        # Emphasis/link wrappers and non-separating inline HTML tags are
        # representation only; their rendered text arrives in nested or
        # adjacent text tokens.
    return "".join(visible)


def _markdown_table_signatures(text: str) -> tuple[tuple[_TableSignature, ...], str | None]:
    signatures: list[_TableSignature] = []
    cells: list[str] | None = None
    total_cells = 0
    try:
        tokens = MarkdownIt().enable("table").parse(text)
    except Exception as exc:
        return (), f"candidate Markdown table parsing failed: {type(exc).__name__}"
    for token in tokens:
        if token.type == "table_open":
            cells = []
        elif token.type == "inline" and cells is not None:
            cells.append(_normalize_cell(_visible_inline_text(token)))
            total_cells += 1
            if total_cells > MAX_TABLE_CELLS:
                return (), f"candidate table cells exceed limit {MAX_TABLE_CELLS}"
        elif token.type == "table_close" and cells is not None:
            signatures.append(_TableSignature(cells=tuple(cells)))
            cells = None
            if len(signatures) > MAX_SOURCE_TABLES:
                return (), f"candidate table count exceeds limit {MAX_SOURCE_TABLES}"
    return tuple(signatures), None


def _table_retention(
    observation: _TableObservation,
    candidate_text: str | None,
    candidate_error: str | None,
) -> DetectorResult:
    detector_id = "source.pdf_table_structure_retention"
    if observation.error is not None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason=observation.error,
            raw_evidence={"pdf_page_count": observation.page_count},
        )
    if candidate_text is None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=False, verdict=Verdict.UNDETERMINED,
            abstention_reason=candidate_error or "candidate text unavailable",
        )
    if not observation.signatures:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=True, verdict=Verdict.UNDETERMINED,
            abstention_reason="no source tables were detected",
            raw_evidence={
                "source_table_count": 0, "source_table_pages": [],
                "pdf_page_count": observation.page_count,
            },
            required_for_group=False,
        )

    candidate_signatures, candidate_table_error = _markdown_table_signatures(candidate_text)
    if candidate_table_error is not None:
        return DetectorResult(
            detector_id=detector_id, detector_version="1", scope=EvidenceScope.SOURCE_COMPARISON,
            status=DetectorStatus.ABSTAINED, eligible=True, verdict=Verdict.UNDETERMINED,
            abstention_reason=candidate_table_error,
            raw_evidence={"source_table_count": len(observation.signatures)},
        )

    source_counter = Counter(signature.cells for signature in observation.signatures)
    candidate_counter = Counter(signature.cells for signature in candidate_signatures)
    matched = sum((source_counter & candidate_counter).values())
    source_count = len(observation.signatures)
    candidate_count = len(candidate_signatures)
    missing = source_count - matched
    extra = candidate_count - matched
    denominator = max(source_count, candidate_count)
    ratio = matched / denominator if denominator else 0.0
    if missing:
        verdict = Verdict.FAIL
        finding_codes = ["SOURCE_TABLE_STRUCTURE_MISSING"]
        if extra:
            finding_codes.append("CANDIDATE_UNMATCHED_TABLE_STRUCTURE")
    elif extra:
        verdict = Verdict.CONCERN
        finding_codes = ["CANDIDATE_EXTRA_TABLE_STRUCTURE"]
    else:
        verdict = Verdict.PASS
        finding_codes = []
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
            "source_table_pages": list(observation.pages),
            "candidate_markdown_table_count": candidate_count,
            "matched_source_table_count": matched,
            "missing_source_table_count": missing,
            "unmatched_candidate_table_count": extra,
            "retention_ratio": ratio,
            "source_table_signature_sha256": [item.digest for item in observation.signatures],
            "candidate_table_signature_sha256": [item.digest for item in candidate_signatures],
            "source_backend": "pymupdf.find_tables",
            "candidate_syntax": "markdown_it_table_v1",
            "scope_note": (
                "Exact normalized cell-sequence signature matching; reading order, semantics, "
                "and visual fidelity are not established."
            ),
        },
        finding_codes=finding_codes,
    )


def assess_source_candidate(
    *, source: SourceArtifact, candidate: CandidateArtifact,
) -> SourceCandidateAssessment:
    """Assess exact source/candidate bytes under the exploratory V2 policy."""
    _revalidate_artifact_identity(source, "source")
    _revalidate_artifact_identity(candidate, "candidate")
    candidate_text, candidate_error = _candidate_text(candidate)
    text_observation, table_observation = _pdf_observations(source)
    comparison = _group(EvidenceScope.SOURCE_COMPARISON, [
        _source_identity(source, candidate),
        _text_retention(text_observation, candidate_text, candidate_error),
        _table_retention(table_observation, candidate_text, candidate_error),
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
        candidate_intrinsic=_candidate_intrinsic(candidate, candidate_error),
        source_comparison=comparison,
    )
