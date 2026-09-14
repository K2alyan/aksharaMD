"""Uniform normalization applied to every parser's markdown output.

Applied before any comparison — conventional metric, adjudication, or
cross-parser inspection. Versioned via ``NORMALIZATION_VERSION`` so any
change is a visible instrument-shift.

**V1 scope — deliberately narrow.** Under Authorization A.1 review the
initial rule set (frontmatter stripping, soft-hyphen removal,
dehyphenation across line breaks, horizontal whitespace collapse,
multi-blank-line collapse, per-line rtrim) was rejected because each
of those transformations interacts with structure-sensitive Markdown
constructs — fenced code indentation, indented code blocks, table cell
content, heading syntax, list nesting, or trailing-two-space hard line
breaks. The evaluation instrument must not manufacture or erase such
structural differences before comparison.

The two rules that remain are demonstrably safe across all Markdown
constructs:

1. NFKC Unicode normalization — character-level, does not alter block
   structure or line boundaries.
2. Line-ending unification to LF — line-terminator-only, does not
   alter cell content, indentation, or fenced-code payloads.

Broader normalization (Markdown-aware, structure-preserving) is
deferred to a subsequent authorization. Bumping this file's rule set is
an instrument change and must appear as a `NORMALIZATION_VERSION` bump.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

NORMALIZATION_VERSION = "2"


@dataclass(frozen=True)
class NormalizationResult:
    text: str
    version: str
    rules_applied: tuple[str, ...]


class UnicodeWhitespaceNormalizer:
    """V1 default normalizer — NFKC + LF line-endings only."""

    def normalize(self, markdown: str) -> NormalizationResult:
        rules: list[str] = []
        s = markdown

        after = unicodedata.normalize("NFKC", s)
        if after != s:
            rules.append("nfkc")
        s = after

        after = s.replace("\r\n", "\n").replace("\r", "\n")
        if after != s:
            rules.append("lf_line_endings")
        s = after

        return NormalizationResult(
            text=s, version=NORMALIZATION_VERSION, rules_applied=tuple(rules)
        )


def get_default_normalizer() -> UnicodeWhitespaceNormalizer:
    return UnicodeWhitespaceNormalizer()
