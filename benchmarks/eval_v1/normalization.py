"""Uniform normalization applied to every parser's markdown output.

Applied before any comparison — conventional metric, adjudication, or
cross-parser inspection. Versioned via ``NORMALIZATION_VERSION`` so any
change is a visible instrument-shift.

Rules applied, in order:

1. NFKC Unicode normalization.
2. Line-ending unification to LF.
3. Soft-hyphen removal (``\\u00AD``).
4. Dehyphenation across prose line breaks — collapse ``word-\\nword`` to
   ``wordword`` only when neither side is a numeric span or an equation.
   Conservative: leaves hyphenated compounds inside a single line
   untouched.
5. Markdown-frontmatter stripping — a leading ``---\\n…\\n---`` block.
6. Whitespace collapse — runs of ASCII spaces/tabs to a single space;
   runs of 3+ blank lines to two blank lines.
7. Right-trim per line.

The output is a normalized string plus a record of which rules fired.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol


NORMALIZATION_VERSION = "1"


@dataclass(frozen=True)
class NormalizationResult:
    text: str
    version: str
    rules_applied: tuple[str, ...]


class Normalizer(Protocol):
    def normalize(self, markdown: str) -> NormalizationResult: ...


_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", flags=re.DOTALL)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_HORIZ_WS_RE = re.compile(r"[ \t]+")
_HYPHEN_LINEBREAK_RE = re.compile(r"([A-Za-z]{2,})-\n([A-Za-z]{2,})")


class UnicodeWhitespaceNormalizer:
    """A.1b default normalizer.

    Deterministic, purely lexical. Does not alter table structure, code
    fences, or heading levels.
    """

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

        after = s.replace("­", "")
        if after != s:
            rules.append("soft_hyphen_removed")
        s = after

        after = _HYPHEN_LINEBREAK_RE.sub(r"\1\2", s)
        if after != s:
            rules.append("dehyphenated_linebreak")
        s = after

        after = _FRONTMATTER_RE.sub("", s, count=1)
        if after != s:
            rules.append("frontmatter_stripped")
        s = after

        after = _HORIZ_WS_RE.sub(" ", s)
        if after != s:
            rules.append("horizontal_ws_collapsed")
        s = after

        after = _MULTI_BLANK_RE.sub("\n\n", s)
        if after != s:
            rules.append("multiple_blank_lines_collapsed")
        s = after

        lines = s.split("\n")
        after_lines = [ln.rstrip() for ln in lines]
        if after_lines != lines:
            rules.append("line_rtrim")
        s = "\n".join(after_lines)

        return NormalizationResult(
            text=s, version=NORMALIZATION_VERSION, rules_applied=tuple(rules)
        )


def get_default_normalizer() -> Normalizer:
    return UnicodeWhitespaceNormalizer()
