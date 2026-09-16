"""Normalization stage — delegate to the canonical frozen v2 normalizer.

The smoke harness does NOT maintain a second normalization algorithm.
It calls ``benchmarks.eval_v1.normalization.get_default_normalizer()``
and consumes its ``NormalizationResult``. Any change to normalization
must land in the canonical module and bump ``NORMALIZATION_VERSION``
there; the smoke picks the change up automatically.

Frozen v2 rules (see ``benchmarks.eval_v1.normalization``):
1. NFKC Unicode normalization.
2. Line-ending unification to LF.

Rejected in v2 (documented in the canonical module):
- Frontmatter stripping.
- Soft-hyphen removal.
- Dehyphenation across line breaks.
- Horizontal whitespace collapse.
- Multi-blank-line collapse.
- Per-line rtrim.

These transformations were rejected because they interact with
structure-sensitive Markdown constructs (trailing two-space hard
breaks, table-cell content, fenced code indentation, list nesting).
The smoke MUST NOT reintroduce any of them.
"""
from __future__ import annotations

from benchmarks.eval_v1.normalization import (
    NORMALIZATION_VERSION as _CANONICAL_VERSION,
)
from benchmarks.eval_v1.normalization import (
    NormalizationResult,
    get_default_normalizer,
)

# Re-export the canonical version constant so callers that reach into
# this module see the same value as the canonical module. Any drift
# between the two is a harness defect.
NORMALIZATION_VERSION = _CANONICAL_VERSION


def normalize(markdown: str) -> str:
    """Convenience wrapper returning only the normalized text.

    Delegates to the canonical frozen normalizer. Callers that need
    the ``rules_applied`` audit trail should call
    ``get_default_normalizer().normalize(markdown)`` directly and
    consume the full ``NormalizationResult``.
    """
    return get_default_normalizer().normalize(markdown).text


__all__ = [
    "NORMALIZATION_VERSION",
    "NormalizationResult",
    "get_default_normalizer",
    "normalize",
]
