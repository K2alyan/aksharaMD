"""Exclusion + positive-evidence classifier for KV candidates.

Introduced in kv_promoter/v2. The goal is to reduce the FPR of the inline
heuristic (Round 1 measured 0.929 on hard negatives) by first running a set
of deterministic exclusion detectors (dialogue, configuration, citation,
section labels, numbered lists, legal clauses, academic definitions,
medical sections, financial footnotes) and then requiring at least one of
three positive-evidence rules before promotion:

    Rule A: >= 2 strongly-typed values (email/phone/url/time/date/currency/percentage)
    Rule B: >= 3 keys match a recognized field schema
    Soft:   >= 1 strongly-typed value AND >= 2 schema matches
"""
from __future__ import annotations

import re

from ..models.key_value import KeyValueGroupType, KeyValueValueType
from .key_value_config import (
    KeyValueCandidateAssessment,
    KeyValueCandidateCategory,
)

# ── Exclusion patterns ─────────────────────────────────────────────────────────

_BOOL_NULL_VALUES = frozenset({
    "true", "false", "yes", "no", "on", "off", "null", "nil", "none",
    "enabled", "disabled", "1", "0",
})
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# Year range 1500-2299 avoids matching arbitrary 4-digit numbers such as
# port numbers (e.g. 5432, 8080) that would otherwise trigger the citation
# exclusion incorrectly.
_YEAR_RE = re.compile(r"^(?:1[5-9]\d{2}|20\d{2}|21\d{2}|22\d{2})[a-z]?$")
_AUTHOR_RE = re.compile(r"^[A-Z][a-z]+(?: et al\.?)?$")

_SECTION_RE = re.compile(
    r"^(?:Section|Clause|Article|Part|Chapter)\s+\d",
    re.IGNORECASE,
)
_NUMBERED_LABEL_RE = re.compile(r"^\d+(?:\.\d+)*$")
_QUARTERLY_RE = re.compile(r"^Q[1-4]$", re.IGNORECASE)

_LEGAL_CLAUSE_RE = re.compile(
    r"^(?:Section|Clause|Article|Sub-?clause)\s+\d",
    re.IGNORECASE,
)

_DEFINITION_STARTERS = re.compile(r"^(?:A|An|The)\s+", re.IGNORECASE)

_MEDICAL_KEYS = frozenset({
    "impression", "findings", "assessment", "plan", "history", "diagnosis",
    "recommendation", "complaint", "examination", "review", "prognosis",
    "chief complaint", "physical exam", "lab results", "discharge summary",
})

_FOOTNOTE_RE = re.compile(r"^\([0-9a-zA-Z]\)$|^\d+\.$")


# ── Recognized schemas ─────────────────────────────────────────────────────────

_RECOGNIZED_SCHEMAS: dict[str, frozenset[str]] = {
    "contact": frozenset({
        "name", "email", "phone", "telephone", "mobile", "address", "website",
        "url", "fax", "contact", "department", "company", "organisation",
        "organization", "city", "country", "postal", "zip",
    }),
    "schedule": frozenset({
        "date", "day", "time", "location", "venue", "organizer", "service",
        "registration", "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday", "opening", "closing",
    }),
    "event": frozenset({
        "date", "time", "location", "venue", "organizer", "event", "booking",
        "booked by", "check-in", "check-out", "duration",
    }),
    "metadata": frozenset({
        "title", "author", "version", "created", "modified", "category",
        "subject", "description", "status", "owner", "project", "package",
        "license", "copyright", "doi", "isbn", "issn", "journal", "year",
        "publisher",
    }),
    "specification": frozenset({
        "model", "voltage", "dimensions", "weight", "capacity", "material",
        "power", "current", "frequency", "manufacturer", "serial", "warranty",
        "color", "colour", "size", "width", "height", "length", "depth",
        "temperature", "pressure", "operating range", "interface",
        "connectivity",
    }),
    "form": frozenset({
        "name", "date", "address", "reference", "ticket", "invoice", "client",
        "customer", "id", "number", "signature", "amount", "total", "due",
        "issued", "received",
    }),
}

_SCHEMA_TO_GROUP: dict[str, KeyValueGroupType] = {
    "contact": KeyValueGroupType.CONTACT,
    "schedule": KeyValueGroupType.SCHEDULE,
    "event": KeyValueGroupType.EVENT,
    "metadata": KeyValueGroupType.METADATA,
    "specification": KeyValueGroupType.SPECIFICATION,
    "form": KeyValueGroupType.FORM,
}

_STRONG_VALUE_TYPES = frozenset({
    KeyValueValueType.EMAIL,
    KeyValueValueType.PHONE,
    KeyValueValueType.URL,
    KeyValueValueType.TIME,
    KeyValueValueType.DATE,
    KeyValueValueType.DATETIME,
    KeyValueValueType.CURRENCY,
    KeyValueValueType.PERCENTAGE,
})


# ── Exclusion detectors ────────────────────────────────────────────────────────

def _is_dialogue(candidates) -> bool:
    """Dialogue signals: sentence-ending punctuation and/or single-word
    proper-noun keys.
    """
    sentence_endings = sum(
        1 for c in candidates if c.value.rstrip().endswith((".", "?", "!"))
    )
    name_like_keys = sum(
        1 for c in candidates if re.match(r"^[A-Z][a-z]+$", c.key)
    )
    return sentence_endings >= 2 or (
        name_like_keys >= 2 and sentence_endings >= 1
    )


def _is_configuration(candidates) -> bool:
    """YAML/config-like: identifier-shaped keys with boolean/null values, or
    all-identifier keys with 3+ entries.
    """
    identifier_keys = sum(
        1 for c in candidates if _IDENTIFIER_RE.match(c.key)
    )
    bool_values = sum(
        1 for c in candidates if c.value.strip().lower() in _BOOL_NULL_VALUES
    )
    all_identifier = identifier_keys == len(candidates)
    return (all_identifier and bool_values >= 1) or (
        all_identifier and len(candidates) >= 3
    )


def _is_citation(candidates) -> bool:
    """Bibliographic citation: values are 4-digit years, keys look like
    author surnames.
    """
    year_values = sum(
        1 for c in candidates if _YEAR_RE.match(c.value.strip())
    )
    author_keys = sum(
        1 for c in candidates if _AUTHOR_RE.match(c.key.strip())
    )
    return year_values >= 2 or (year_values >= 1 and author_keys >= 1)


def _is_section_label(candidates) -> bool:
    """Section/clause labels, pure numbered labels, or quarterly markers."""
    section_keys = sum(
        1 for c in candidates
        if (
            _SECTION_RE.match(c.key)
            or _NUMBERED_LABEL_RE.match(c.key)
            or _QUARTERLY_RE.match(c.key)
        )
    )
    return section_keys >= 2


def _is_numbered_list(candidates) -> bool:
    """All keys are pure integer/dotted-integer labels."""
    return (
        all(_NUMBERED_LABEL_RE.match(c.key) for c in candidates)
        and len(candidates) >= 2
    )


def _is_legal_clause(candidates) -> bool:
    return sum(
        1 for c in candidates if _LEGAL_CLAUSE_RE.match(c.key)
    ) >= 2


def _is_academic_definition(candidates) -> bool:
    """Values start with 'A ', 'An ' or 'The '."""
    definition_values = sum(
        1 for c in candidates if _DEFINITION_STARTERS.match(c.value)
    )
    return definition_values >= 2


def _is_medical_section(candidates) -> bool:
    return sum(
        1 for c in candidates if c.key.lower() in _MEDICAL_KEYS
    ) >= 2


def _is_financial_footnote(candidates) -> bool:
    return sum(
        1 for c in candidates if _FOOTNOTE_RE.match(c.key.strip())
    ) >= 2


# ── Positive-evidence helpers ─────────────────────────────────────────────────

def count_strongly_typed(candidates, infer_fn) -> int:
    count = 0
    for c in candidates:
        vtype = infer_fn(c.value)
        if vtype in _STRONG_VALUE_TYPES:
            count += 1
    return count


def best_schema_match(normalized_keys: list[str]) -> tuple[str, int, list[str]]:
    """Return (schema_name, match_count, matched_fields)."""
    best: tuple[str, int, list[str]] = ("unknown", 0, [])
    for name, fields in _RECOGNIZED_SCHEMAS.items():
        matched = [k for k in normalized_keys if k in fields]
        if len(matched) > best[1]:
            best = (name, len(matched), matched)
    return best


# ── Main assessment ────────────────────────────────────────────────────────────

def classify_kv_candidates(
    candidates: list,
    infer_value_type_fn,
    normalize_key_fn,
) -> KeyValueCandidateAssessment:
    """Assess a list of KVCandidates and return a promotion decision."""
    if len(candidates) < 2:
        return KeyValueCandidateAssessment(
            candidate_entries=len(candidates),
            strongly_typed_entries=0,
            inferred_group_type=KeyValueGroupType.UNKNOWN,
            group_type_confidence=0.0,
            category=KeyValueCandidateCategory.UNKNOWN,
            exclusion_categories=[],
            recognized_schema_fields=[],
            promotion_decision="reject",
            rejection_reason="insufficient_candidates",
        )

    # Exclusion pass — always evaluated so callers can inspect signals.
    exclusions: list[str] = []
    if _is_dialogue(candidates):
        exclusions.append(KeyValueCandidateCategory.DIALOGUE)
    if _is_configuration(candidates):
        exclusions.append(KeyValueCandidateCategory.CONFIGURATION)
    if _is_citation(candidates):
        exclusions.append(KeyValueCandidateCategory.CITATION)
    if _is_section_label(candidates):
        exclusions.append(KeyValueCandidateCategory.SECTION_LABEL)
    if _is_numbered_list(candidates):
        exclusions.append(KeyValueCandidateCategory.NUMBERED_LIST)
    if _is_legal_clause(candidates):
        exclusions.append(KeyValueCandidateCategory.LEGAL_CLAUSE)
    if _is_academic_definition(candidates):
        exclusions.append(KeyValueCandidateCategory.ACADEMIC_DEFINITION)
    if _is_medical_section(candidates):
        exclusions.append(KeyValueCandidateCategory.MEDICAL_SECTION)
    if _is_financial_footnote(candidates):
        exclusions.append(KeyValueCandidateCategory.FINANCIAL_FOOTNOTE)

    strongly_typed = count_strongly_typed(candidates, infer_value_type_fn)
    normalized_keys = [normalize_key_fn(c.key) for c in candidates]
    schema_name, schema_count, schema_fields = best_schema_match(
        normalized_keys
    )
    inferred_type = _SCHEMA_TO_GROUP.get(schema_name, KeyValueGroupType.UNKNOWN)
    confidence = min(1.0, schema_count / max(len(candidates), 1))

    if exclusions:
        return KeyValueCandidateAssessment(
            candidate_entries=len(candidates),
            strongly_typed_entries=strongly_typed,
            inferred_group_type=inferred_type,
            group_type_confidence=confidence,
            category=exclusions[0],
            exclusion_categories=exclusions,
            recognized_schema_fields=schema_fields,
            promotion_decision="reject",
            rejection_reason=f"exclusion_category:{exclusions[0]}",
        )

    # Rule A — 2+ strongly-typed values.
    if strongly_typed >= 2:
        return KeyValueCandidateAssessment(
            candidate_entries=len(candidates),
            strongly_typed_entries=strongly_typed,
            inferred_group_type=inferred_type,
            group_type_confidence=confidence,
            category=schema_name,
            exclusion_categories=[],
            recognized_schema_fields=schema_fields,
            promotion_decision="promote",
            rejection_reason=None,
        )

    # Rule B — 3+ recognized schema fields.
    if schema_count >= 3:
        return KeyValueCandidateAssessment(
            candidate_entries=len(candidates),
            strongly_typed_entries=strongly_typed,
            inferred_group_type=inferred_type,
            group_type_confidence=confidence,
            category=schema_name,
            exclusion_categories=[],
            recognized_schema_fields=schema_fields,
            promotion_decision="promote",
            rejection_reason=None,
        )

    # Soft rule — 1 strong typed + 2+ schema matches.
    if strongly_typed >= 1 and schema_count >= 2:
        return KeyValueCandidateAssessment(
            candidate_entries=len(candidates),
            strongly_typed_entries=strongly_typed,
            inferred_group_type=inferred_type,
            group_type_confidence=confidence,
            category=schema_name,
            exclusion_categories=[],
            recognized_schema_fields=schema_fields,
            promotion_decision="promote",
            rejection_reason=None,
        )

    return KeyValueCandidateAssessment(
        candidate_entries=len(candidates),
        strongly_typed_entries=strongly_typed,
        inferred_group_type=inferred_type,
        group_type_confidence=confidence,
        category=(
            schema_name
            if schema_name != "unknown"
            else KeyValueCandidateCategory.UNKNOWN
        ),
        exclusion_categories=[],
        recognized_schema_fields=schema_fields,
        promotion_decision="reject",
        rejection_reason="insufficient_positive_evidence",
    )
