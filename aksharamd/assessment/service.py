"""Deterministic first-pass checks for saved source and candidate artifacts."""
from __future__ import annotations

import re
from html import unescape

from .models import (
    DEFAULT_ASSESSMENT_POLICY_ID,
    GENERAL_INGESTION_POLICY_ID,
    AssessmentDisposition,
    AssessmentResult,
    CandidateArtifact,
    DimensionResult,
    EvidenceItem,
    EvidenceStatus,
    Finding,
    NextAction,
    SourceArtifact,
    TaskProfile,
    Verdict,
)
from .text_preservation import SOURCE_TEXT_PRESERVATION_POLICY_ID, apply_text_preservation

_CRITICAL_LITERAL = re.compile(
    r"(?<!\w)(?:\d{4}-\d{2}-\d{2}|[A-Z]{2,}[\-_/]?\d{2,}|[-+]?\d+(?:[.,]\d+)?(?:\s?[%$€£]|\s+(?:kg|km|ms|mb|gb|usd|eur))?)(?!\w)",
    re.IGNORECASE,
)


def _text(data: bytes, media_type: str) -> tuple[str, bool]:
    """Decode only text-like inputs; binary content is explicitly unassessed."""
    if not (media_type.startswith("text/") or media_type in {"application/json", "application/xml"}):
        return "", False
    text = data.decode("utf-8", errors="replace")
    if "html" in media_type:
        text = re.sub(r"<[^>]+>", " ", text)
    return unescape(text), True


class Assessor:
    """Assess immutable artifacts without invoking parsers or transformations."""

    def assess(
        self,
        *,
        candidate: CandidateArtifact,
        source: SourceArtifact | None = None,
        task_profile: TaskProfile | None = None,
        policy_id: str = DEFAULT_ASSESSMENT_POLICY_ID,
    ) -> AssessmentResult:
        if policy_id not in {GENERAL_INGESTION_POLICY_ID, DEFAULT_ASSESSMENT_POLICY_ID, SOURCE_TEXT_PRESERVATION_POLICY_ID}:
            raise ValueError(f"Unknown assessment policy: {policy_id}")
        candidate_text, candidate_supported = _text(candidate.data, candidate.media_type)
        source_text, source_supported = _text(source.data, source.media_type) if source else ("", False)
        fidelity = self._fidelity(source, source_text, source_supported, candidate, candidate_text, candidate_supported)
        if policy_id in {DEFAULT_ASSESSMENT_POLICY_ID, SOURCE_TEXT_PRESERVATION_POLICY_ID}:
            fidelity = apply_text_preservation(fidelity, source, candidate)
        if (policy_id == DEFAULT_ASSESSMENT_POLICY_ID and candidate.declared_truncated
                and not any(f.code == "CANDIDATE_DECLARED_TRUNCATED" for f in fidelity.findings)):
            # A declared partial output is concrete evidence even for binary or
            # missing sources. Keep historical v1 replay behavior unchanged.
            fidelity.status = EvidenceStatus.OBSERVED
            fidelity.verdict = Verdict.FAIL
            fidelity.findings.insert(0, Finding(
                code="CANDIDATE_DECLARED_TRUNCATED", dimension="conversion_fidelity", severity="critical",
                message="Candidate declares that it is incomplete.", origin="conversion",
            ))
        if source and candidate.original_source_hash and candidate.original_source_hash != source.content_hash:
            fidelity.status = EvidenceStatus.FAILED
            fidelity.verdict = Verdict.FAIL
            fidelity.findings.insert(0, Finding(
                code="SOURCE_IDENTITY_MISMATCH", dimension="conversion_fidelity", severity="critical",
                message="Candidate provenance names a different source artifact.", origin="unknown",
            ))
        # CommonMark must see the literal Markdown bytes, not entity-decoded
        # text (entities inside code fences are not Markdown delimiters).
        structural_text = (candidate.data.decode("utf-8", errors="replace")
                           if policy_id == DEFAULT_ASSESSMENT_POLICY_ID else candidate_text)
        structure = self._structure(structural_text, candidate_supported, policy_id=policy_id)
        integrity = self._integrity(source_text, source_supported, candidate_text, candidate_supported)
        task_suitability = self._task_suitability(candidate_text, candidate_supported, task_profile)
        dimensions = {
            "conversion_fidelity": fidelity, "structural_usability": structure,
            "content_integrity": integrity,
            "task_suitability": task_suitability,
        }
        disposition, next_action = self._decide(dimensions)
        return AssessmentResult(policy_id=policy_id, source_hash=source.content_hash if source else None,
                                candidate_hash=candidate.content_hash, dimensions=dimensions,
                                disposition=disposition, next_action=next_action)

    def _fidelity(self, source, source_text, source_supported, candidate, candidate_text, candidate_supported):
        if source is None:
            return DimensionResult(status=EvidenceStatus.UNKNOWN, verdict=Verdict.UNDETERMINED, findings=[Finding(
                code="SOURCE_EVIDENCE_UNAVAILABLE", dimension="conversion_fidelity", severity="warning",
                message="No source artifact was supplied; source-preserving fidelity cannot be established.")])
        if not source_supported or not candidate_supported:
            return DimensionResult(status=EvidenceStatus.UNKNOWN, verdict=Verdict.UNDETERMINED)
        expected = sorted(set(_CRITICAL_LITERAL.findall(source_text)))
        # Compare complete extracted literals: a source value of 12 is not
        # preserved by 312, 12.5, or an identifier containing those digits.
        # Keep exact spelling, including units; normalization is not implied.
        observed = set(_CRITICAL_LITERAL.findall(candidate_text))
        missing = [item for item in expected if item not in observed]
        evidence = EvidenceItem(check_id="critical_literal_coverage", status=EvidenceStatus.OBSERVED,
                                measurement=len(expected) - len(missing), unit="literals", denominator=len(expected),
                                details={"missing": missing})
        if candidate.declared_truncated:
            return DimensionResult(status=EvidenceStatus.OBSERVED, verdict=Verdict.FAIL, evidence=[evidence], findings=[Finding(
                code="CANDIDATE_DECLARED_TRUNCATED", dimension="conversion_fidelity", severity="critical",
                message="Candidate declares that it is incomplete.", evidence_ids=[evidence.check_id], origin="conversion")])
        if missing:
            return DimensionResult(status=EvidenceStatus.OBSERVED, verdict=Verdict.FAIL, evidence=[evidence], findings=[Finding(
                code="CRITICAL_LITERAL_MISSING", dimension="conversion_fidelity", severity="critical",
                message="Candidate is missing source literal(s) that may carry a number, unit, date, or identifier.",
                evidence_ids=[evidence.check_id], origin="conversion")])
        return DimensionResult(status=EvidenceStatus.OBSERVED, verdict=Verdict.PASS, evidence=[evidence])

    @staticmethod
    def _structure(text, supported, *, policy_id=GENERAL_INGESTION_POLICY_ID):
        if not supported:
            return DimensionResult(status=EvidenceStatus.UNKNOWN, verdict=Verdict.UNDETERMINED)
        unbalanced = (_unclosed_fences(text) if policy_id == DEFAULT_ASSESSMENT_POLICY_ID
                      else text.count("```") % 2)
        evidence = EvidenceItem(check_id="markdown_fence_balance",
                                check_version="2" if policy_id == DEFAULT_ASSESSMENT_POLICY_ID else "1",
                                status=EvidenceStatus.OBSERVED,
                                measurement=float(unbalanced), unit="unbalanced_fences")
        if unbalanced:
            return DimensionResult(status=EvidenceStatus.OBSERVED, verdict=Verdict.FAIL, evidence=[evidence], findings=[Finding(
                code="UNCLOSED_CODE_FENCE", dimension="structural_usability", severity="major",
                message="Candidate has an unclosed fenced code block.", evidence_ids=[evidence.check_id], origin="representation")])
        return DimensionResult(status=EvidenceStatus.OBSERVED, verdict=Verdict.PASS, evidence=[evidence])

    @staticmethod
    def _integrity(source_text, source_supported, candidate_text, candidate_supported):
        if not candidate_supported:
            return DimensionResult(status=EvidenceStatus.UNKNOWN, verdict=Verdict.UNDETERMINED)
        findings = []
        if "\ufffd" in candidate_text:
            findings.append(Finding(code="REPLACEMENT_CHARACTER", dimension="content_integrity", severity="major",
                                    message="Candidate contains Unicode replacement characters."))
        if source_supported and source_text.strip() and not candidate_text.strip():
            findings.append(Finding(code="CANDIDATE_EMPTY", dimension="content_integrity", severity="critical",
                                    message="Non-empty source produced an empty candidate.", origin="conversion"))
        evidence = EvidenceItem(check_id="candidate_text_integrity", status=EvidenceStatus.OBSERVED,
                                measurement=float(len(candidate_text)), unit="characters")
        return DimensionResult(status=EvidenceStatus.OBSERVED,
                               verdict=Verdict.FAIL if findings else Verdict.PASS,
                               evidence=[evidence], findings=findings)

    @staticmethod
    def _task_suitability(
        candidate_text: str,
        candidate_supported: bool,
        task_profile: TaskProfile | None,
    ) -> DimensionResult:
        """Check only requirements explicitly supplied by the caller.

        A relationship means ``first_literal`` appears before
        ``second_literal`` with no more than the caller-provided number of
        intervening characters.  It is intentionally not semantic parsing.
        """
        if task_profile is None:
            return DimensionResult(status=EvidenceStatus.NOT_REQUESTED, verdict=Verdict.UNDETERMINED)
        if not candidate_supported:
            return DimensionResult(
                status=EvidenceStatus.UNKNOWN,
                verdict=Verdict.UNDETERMINED,
                findings=[Finding(
                    code="TASK_SUITABILITY_CANDIDATE_UNSUPPORTED",
                    dimension="task_suitability",
                    severity="warning",
                    message="Candidate media type cannot be checked against the declared task profile.",
                )],
            )

        def normalized(value: str, case_sensitive: bool) -> str:
            return value if case_sensitive else value.casefold()

        missing_literals = [
            literal for literal in task_profile.required_literals
            if literal.casefold() not in candidate_text.casefold()
        ]
        missing_relationships: list[dict[str, object]] = []
        for relationship in task_profile.required_relationships:
            text = normalized(candidate_text, relationship.case_sensitive)
            first = normalized(relationship.first_literal, relationship.case_sensitive)
            second = normalized(relationship.second_literal, relationship.case_sensitive)
            first_at = text.find(first)
            relationship_found = False
            while first_at >= 0:
                second_at = text.find(second, first_at + len(first))
                if second_at >= 0 and second_at - (first_at + len(first)) <= relationship.max_characters_between:
                    relationship_found = True
                    break
                first_at = text.find(first, first_at + 1)
            if not relationship_found:
                missing_relationships.append({
                    "first_literal": relationship.first_literal,
                    "second_literal": relationship.second_literal,
                    "max_characters_between": relationship.max_characters_between,
                })

        requirement_count = len(task_profile.required_literals) + len(task_profile.required_relationships)
        missing_count = len(missing_literals) + len(missing_relationships)
        evidence = EvidenceItem(
            check_id="task_profile_literal_requirements",
            status=EvidenceStatus.OBSERVED,
            measurement=float(requirement_count - missing_count),
            unit="requirements",
            denominator=float(requirement_count),
            details={
                "task_profile_schema_version": task_profile.schema_version,
                "missing_literals": missing_literals,
                "missing_relationships": missing_relationships,
            },
        )
        findings: list[Finding] = []
        if missing_literals:
            findings.append(Finding(
                code="TASK_REQUIRED_LITERAL_MISSING",
                dimension="task_suitability",
                severity="critical",
                message="Candidate is missing literal fact(s) required by the declared task profile.",
                evidence_ids=[evidence.check_id],
                origin="unknown",
            ))
        if missing_relationships:
            findings.append(Finding(
                code="TASK_REQUIRED_RELATIONSHIP_MISSING",
                dimension="task_suitability",
                severity="critical",
                message=(
                    "Candidate does not preserve literal ordering/proximity required by the declared "
                    "task profile."
                ),
                evidence_ids=[evidence.check_id],
                origin="unknown",
            ))
        return DimensionResult(
            status=EvidenceStatus.OBSERVED,
            verdict=Verdict.FAIL if findings else Verdict.PASS,
            findings=findings,
            evidence=[evidence],
        )

    @staticmethod
    def _decide(dimensions):
        required = ("conversion_fidelity", "structural_usability", "content_integrity", "task_suitability")
        if any(dimensions[name].verdict == Verdict.FAIL for name in required):
            return AssessmentDisposition.HOLD, NextAction.REVIEW
        if any(dimensions[name].status in {EvidenceStatus.UNKNOWN, EvidenceStatus.FAILED} for name in required):
            return AssessmentDisposition.ABSTAIN, NextAction.REVIEW
        return AssessmentDisposition.ACCEPT, NextAction.NONE


def _unclosed_fences(text: str) -> int:
    """Use CommonMark's fence rule while container-adjusted line state is live.

    Parsed token maps alone lose the indentation adjustments made by quotes
    and lists. Inspect the closing line inside the rule wrapper, before those
    containers restore their state. The parser remains responsible for finding
    all opening and closing boundaries.
    """
    from markdown_it import MarkdownIt
    from markdown_it.rules_block import fence

    unclosed = 0

    def checked_fence(state, start_line, end_line, silent):
        nonlocal unclosed
        matched = fence(state, start_line, end_line, silent)
        if matched and not silent:
            token = state.tokens[-1]
            last_line = state.line - 1
            closed = False
            if last_line > start_line:
                start = state.bMarks[last_line] + state.tShift[last_line]
                ending = state.src[start:state.eMarks[last_line]]
                indentation = state.sCount[last_line] - state.blkIndent
                closed = (0 <= indentation < 4 and re.fullmatch(
                    re.escape(token.markup[0]) + "{" + str(len(token.markup)) + ",}[ \t]*", ending
                ) is not None)
            unclosed += int(not closed)
        return matched

    parser = MarkdownIt("commonmark")
    parser.block.ruler.at("fence", checked_fence, {"alt": ["paragraph", "reference", "blockquote", "list"]})
    parser.parse(text)
    return unclosed
