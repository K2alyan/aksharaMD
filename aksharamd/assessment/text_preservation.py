"""Opt-in, conservative textual comparison; never a proof of semantic fidelity."""

from __future__ import annotations

import re
from collections import Counter

from .models import DimensionResult, EvidenceItem, EvidenceStatus, Finding, Verdict

SOURCE_TEXT_PRESERVATION_POLICY_ID = "source-text-preservation-v1"
_SUPPORTED = {"text/plain", "text/markdown"}
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _lines(text: str, media_type: str) -> list[tuple[str, ...]]:
    lines = []
    for line in text.splitlines():
        if media_type == "text/markdown":
            line = re.sub(r"^ {0,3}#{1,6}[ \t]+", "", line)
            # Narrowly recognize paired bold decoration, retaining all other
            # punctuation (including minus signs, quotes and sentence boundaries).
            line = re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"\1", line)
        tokens = tuple(_TOKEN.findall(line))
        if tokens:
            lines.append(tokens)
    return lines


def _explicit_fields(lines: list[tuple[str, ...]]) -> bool:
    """Allow reorder only for unique, explicit single-line label:value fields.

    This syntactic restriction is intentionally narrow. It does not establish
    that a colon expresses a semantic field, or that field order is immaterial.
    """
    labels = set()
    for line in lines:
        if line.count(":") != 1:
            return False
        separator = line.index(":")
        label, value = line[:separator], line[separator + 1:]
        # Restrict labels to words; reject table pipes and ambiguous punctuation.
        if not label or not value or not all(token.isalnum() for token in label):
            return False
        canonical_label = tuple(token.casefold() for token in label)
        if canonical_label in labels:
            return False
        labels.add(canonical_label)
    return bool(lines)


def _indentation_sensitive(text: str) -> bool:
    """Identifiable code/indentation uses exact lines, with no Markdown stripping."""
    return any(
        "```" in line or "~~~" in line or "\t" in line
        or (bool(line.strip()) and line.startswith((" ", "\t")))
        for line in text.splitlines()
    )


def _structured(lines: list[tuple[str, ...]]) -> bool:
    # Colons and table pipes must retain their line binding. List/code markers
    # also make unrestricted prose reflow inappropriate. Ambiguity abstains.
    return any(any(token in {":", "|", "{", "}", ";"} for token in line)
               or line[0] in {"-", "+", "*", ">"} for line in lines)


def apply_text_preservation(fidelity: DimensionResult, source, candidate) -> DimensionResult:
    """Add evidence without weakening a concrete existing failure.

    Unique label:value line reordering is allowed and is only a syntactic
    invariant. Whitespace/layout, emphasis, and headings can carry meaning.
    Acceptance establishes only this bounded textual invariant, not semantics
    or the substantive correctness of the source itself.
    """
    evidence_id = "source_text_preservation"
    comparable = source is not None and source.media_type in _SUPPORTED and candidate.media_type in _SUPPORTED
    match = None
    if comparable:
        try:
            source_text = source.data.decode("utf-8")
            candidate_text = candidate.data.decode("utf-8")
            source_lines = _lines(source_text, source.media_type)
            candidate_lines = _lines(candidate_text, candidate.media_type)
        except UnicodeDecodeError:
            comparable = False
        else:
            source_sequence = tuple(token for line in source_lines for token in line)
            candidate_sequence = tuple(token for line in candidate_lines for token in line)
            if _indentation_sensitive(source_text) or _indentation_sensitive(candidate_text):
                if source_text.splitlines() == candidate_text.splitlines():
                    match = "exact_layout_sensitive_lines"
            elif source_lines == candidate_lines:
                match = "normalized_line_sequence"
            elif (_explicit_fields(source_lines) and _explicit_fields(candidate_lines)
                  and Counter(source_lines) == Counter(candidate_lines)):
                match = "unique_explicit_field_line_multiset"
            elif (not _structured(source_lines) and not _structured(candidate_lines)
                  and source_sequence == candidate_sequence):
                match = "normalized_prose_token_sequence"
    fidelity.evidence.append(EvidenceItem(
        check_id=evidence_id,
        status=EvidenceStatus.OBSERVED if comparable else EvidenceStatus.UNKNOWN,
        measurement=float(match is not None) if comparable else None,
        unit="textual_match",
        details={"match_method": match, "semantic_fidelity_established": False,
                 "scope": "Case-sensitive words and punctuation; plain prose permits line wrapping; "
                          "structured content preserves normalized line boundaries; identifiable indentation/code "
                          "requires exact lines; reordering limited to unique explicit label:value lines."},
    ))
    if match is None:
        fidelity.findings.append(Finding(
            code="SOURCE_TEXT_PRESERVATION_UNPROVEN" if comparable else "SOURCE_TEXT_PRESERVATION_UNAVAILABLE",
            dimension="conversion_fidelity", severity="warning", evidence_ids=[evidence_id],
            message=("Text differs beyond the policy's bounded formatting and unique explicit field reorder allowances; "
                     "textual preservation is unproven." if comparable else
                     "This policy requires UTF-8 text/plain or text/markdown source and candidate artifacts."),
        ))
        if fidelity.verdict != Verdict.FAIL:
            fidelity.status = EvidenceStatus.UNKNOWN
            fidelity.verdict = Verdict.UNDETERMINED
    return fidelity
