"""Conservative key-value structure detector.

Rules:
- Parser-native structure (extraction_method != 'inferred') always creates a group.
- Inferred structure requires >=2 adjacent candidate entries within the same block/text.
- Single-colon prose rejected unless accompanied by strong field pattern evidence.
- Rhetorical colons (e.g., "The result was clear: ...") rejected.
- Long prose values (>80 chars after stripping) retained as paragraphs.
- Ambiguous values (e.g., "10/11") not inferred as dates without context.

kv_promoter/v2 additions:
- A KeyValueDetectionProfile may be passed. When provided, heuristic
  promotion is gated by the classifier in ``key_value_classifier`` and by
  the profile's ``enable_inline_heuristic`` flag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models.key_value import (
    KeyValueEntry,
    KeyValueGroup,
    KeyValueGroupType,
    KeyValueValueType,
)

if TYPE_CHECKING:
    from .key_value_config import (
        KeyValueCandidateAssessment,
        KeyValueDetectionProfile,
    )


# ── Value-type patterns ────────────────────────────────────────────────────────

_TIME_RE = re.compile(r'^\d{1,2}:\d{2}(?:\s*[ap]m?)?$', re.IGNORECASE)
_DATE_RE = re.compile(r'^\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}$')
_PHONE_RE = re.compile(r'^[\+\(]?[\d\s\-\(\)]{7,20}$')
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
_URL_RE = re.compile(r'^https?://', re.IGNORECASE)
_CURRENCY_RE = re.compile(r'^[\$\£\€\¥][\d,\.]+$')
_NUMBER_RE = re.compile(r'^\-?\d+(?:[,\.]\d+)*$')
_PERCENT_RE = re.compile(r'^\-?\d+(?:\.\d+)?%$')


# ── Rejection patterns ─────────────────────────────────────────────────────────

# Rhetorical colons: "Note: ...", "Example: ...", "See: ..."
_RHETORICAL_LABELS = frozenset({
    "note", "example", "see", "result", "reason", "warning", "caution",
    "important", "tip", "however", "therefore", "summary", "conclusion",
    "overview", "background", "context", "purpose", "objective",
})

# Labels longer than this word count are probably prose, not field labels
_MAX_LABEL_WORDS = 5

# Values longer than this are prose
_MAX_VALUE_CHARS = 80


@dataclass
class KeyValueCandidate:
    key: str
    value: str
    line: str


@dataclass
class DetectionResult:
    group: KeyValueGroup | None
    signals: list[str] = field(default_factory=list)
    rejected_reason: str | None = None
    assessment: "KeyValueCandidateAssessment | None" = None


def detect_key_value_entries(
    text: str,
    page: int | None = None,
    profile: "KeyValueDetectionProfile | None" = None,
) -> DetectionResult:
    """Try to detect key-value pairs from a text block.

    Returns DetectionResult. ``group`` is None if detection failed or was
    rejected.

    When ``profile`` is provided the caller opts in to kv_promoter/v2
    behaviour: the classifier runs and populates ``assessment`` with the
    exclusion/positive-evidence findings. Promotion is only allowed when
    ``profile.enable_inline_heuristic`` is True and the assessment yields
    ``promote``. When ``profile`` is None the legacy v1 rules apply — this
    preserves the behaviour used by existing callers and by the calibration
    evaluator when no profile is set.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidates: list[KeyValueCandidate] = []
    signals: list[str] = []

    for line in lines:
        c = _try_parse_kv_line(line)
        if c is not None:
            candidates.append(c)

    if not candidates:
        return DetectionResult(group=None, rejected_reason="no_candidate_pairs")

    # v2 path — profile provided.
    if profile is not None:
        from .key_value_classifier import classify_kv_candidates

        assessment = classify_kv_candidates(
            candidates,
            infer_value_type_fn=_infer_value_type,
            normalize_key_fn=_normalize_key,
        )

        # Signals surfaced from the assessment for diagnostic use.
        signals = list(assessment.exclusion_categories)

        if not profile.enable_inline_heuristic:
            return DetectionResult(
                group=None,
                signals=signals,
                rejected_reason="heuristic_disabled",
                assessment=assessment,
            )

        if assessment.promotion_decision != "promote":
            return DetectionResult(
                group=None,
                signals=signals,
                rejected_reason=assessment.rejection_reason,
                assessment=assessment,
            )

        entries = _build_entries(candidates, page)
        extra_signals: list[str] = []
        keys = [e.key for e in entries]
        if len(keys) != len(set(keys)):
            extra_signals.append("KEY_VALUE_DUPLICATE_KEY")
        long_values = [e for e in entries if len(e.value) > _MAX_VALUE_CHARS]
        if long_values:
            extra_signals.append("KEY_VALUE_LONG_VALUE")

        group_type = (
            assessment.inferred_group_type
            if assessment.inferred_group_type != KeyValueGroupType.UNKNOWN
            else _infer_group_type(entries)
        )
        group = KeyValueGroup(
            entries=entries,
            group_type=group_type,
            page=page,
            extraction_method="inferred",
            confidence="inferred",
        )
        return DetectionResult(
            group=group,
            signals=extra_signals,
            assessment=assessment,
        )

    # v1 legacy path — profile not provided.
    if len(candidates) < 2:
        c = candidates[0]
        vtype = _infer_value_type(c.value)
        if vtype in (KeyValueValueType.PHONE, KeyValueValueType.EMAIL, KeyValueValueType.URL):
            pass
        else:
            return DetectionResult(
                group=None,
                signals=signals,
                rejected_reason="single_entry_insufficient_evidence",
            )

    entries = _build_entries(candidates, page)

    keys = [e.key for e in entries]
    if len(keys) != len(set(keys)):
        signals.append("KEY_VALUE_DUPLICATE_KEY")

    long_values = [e for e in entries if len(e.value) > _MAX_VALUE_CHARS]
    if long_values:
        signals.append("KEY_VALUE_LONG_VALUE")

    group = KeyValueGroup(
        entries=entries,
        group_type=_infer_group_type(entries),
        page=page,
        extraction_method="inferred",
        confidence="inferred",
    )
    return DetectionResult(group=group, signals=signals)


def _build_entries(
    candidates: list[KeyValueCandidate],
    page: int | None,
) -> list[KeyValueEntry]:
    entries: list[KeyValueEntry] = []
    for c in candidates:
        vtype = _infer_value_type(c.value)
        norm_key = _normalize_key(c.key)
        entries.append(KeyValueEntry(
            key=c.key,
            value=c.value,
            normalized_key=norm_key if norm_key != c.key.lower() else None,
            value_type=vtype,
            page=page,
            confidence="inferred",
        ))
    return entries


def _try_parse_kv_line(line: str) -> KeyValueCandidate | None:
    """Parse 'Key: Value' from a single line.

    Returns None if the line is not a candidate KV entry.
    """
    # Must contain ': ' separator
    if ': ' not in line and not line.endswith(':'):
        return None

    idx = line.index(':')
    key = line[:idx].strip()
    value = line[idx + 1:].strip()

    # Key must be short (not prose)
    if not key or len(key.split()) > _MAX_LABEL_WORDS:
        return None

    # Key must not start with common prose starters
    key_lower = key.lower()
    if key_lower in _RHETORICAL_LABELS:
        return None

    # Key must not start with sentence starters
    if key_lower.startswith(("the ", "a ", "an ", "in ", "at ", "on ", "for ", "by ")):
        return None

    # Value must not be empty (field with no value is OK for form detection, but
    # require at least whitespace presence for our conservative detector)
    if not value:
        return None

    # Value must not be long prose (>80 chars)
    if len(value) > _MAX_VALUE_CHARS:
        return None

    return KeyValueCandidate(key=key, value=value, line=line)


def _infer_value_type(value: str) -> KeyValueValueType:
    """Conservatively infer value type. Returns UNKNOWN when ambiguous."""
    v = value.strip()
    if _TIME_RE.match(v):
        return KeyValueValueType.TIME
    if _EMAIL_RE.match(v):
        return KeyValueValueType.EMAIL
    if _URL_RE.match(v):
        return KeyValueValueType.URL
    if _PHONE_RE.match(v):
        return KeyValueValueType.PHONE
    if _CURRENCY_RE.match(v):
        return KeyValueValueType.CURRENCY
    if _PERCENT_RE.match(v):
        return KeyValueValueType.PERCENTAGE
    if _NUMBER_RE.match(v):
        return KeyValueValueType.NUMBER
    # DATE only when unambiguous (DD/MM/YYYY pattern, not just DD/MM which could be fraction)
    if _DATE_RE.match(v) and len(v) >= 8:
        return KeyValueValueType.DATE
    return KeyValueValueType.TEXT


def _normalize_key(key: str) -> str:
    """Simple conservative normalization."""
    norm = key.lower().strip().rstrip(".")
    replacements = {
        "tel": "telephone", "tel.": "telephone", "ph": "phone",
        "e-mail": "email", "e mail": "email",
        "mob": "mobile", "mob.": "mobile",
        "sun": "sunday", "mon": "monday", "tue": "tuesday", "wed": "wednesday",
        "thu": "thursday", "fri": "friday", "sat": "saturday",
    }
    return replacements.get(norm, norm)


def _infer_group_type(entries: list[KeyValueEntry]) -> KeyValueGroupType:
    """Heuristic group type inference from value types present."""
    vtypes = {e.value_type for e in entries}
    keys_lower = {(e.normalized_key or e.key).lower() for e in entries}

    time_keys = {"time", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"}
    contact_keys = {"email", "telephone", "phone", "mobile", "fax", "address", "url", "website"}

    if KeyValueValueType.TIME in vtypes or keys_lower & time_keys:
        return KeyValueGroupType.SCHEDULE
    if KeyValueValueType.EMAIL in vtypes or KeyValueValueType.PHONE in vtypes:
        return KeyValueGroupType.CONTACT
    if keys_lower & contact_keys:
        return KeyValueGroupType.CONTACT

    return KeyValueGroupType.UNKNOWN
