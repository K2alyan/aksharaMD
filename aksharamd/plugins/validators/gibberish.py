"""Gibberish content detector — W_GIBBERISH (Phase 2).

Emits W_GIBBERISH when the extracted output contains a high density of
non-linguistic content — mojibake byte patterns, symbol junk, or extreme
character repetition — indicating the parser's decoding or OCR stage
produced garbage that a downstream LLM will happily ingest and treat
as valid text.

Ships a minimal, pattern-based, reference-free detector. Language-model-
based approaches (character-n-gram perplexity via a bundled small LM,
or word-dictionary lookup) are deferred — they add dependency,
calibration, and load-time cost not justified until we have a real
OCR-corrupted corpus to measure FPR against.

**Framing as a LOWER BOUND** — per Rojas et al. arXiv 2605.07293,
pattern-based detection reports at-least counts, not ceiling counts.

**Known limitations (documented, not bugs):**
- Character-substitution OCR corruption (`rn`->`m`, `cl`->`d`, `e`->`c`)
  where letter frequencies remain plausible is NOT caught by these
  signals. That failure mode needs a language-model or dictionary
  approach; slated for a future P2 v2 detector.
- Overlaps intentionally with W_ENCODING_ARTIFACTS mojibake trigger
  when a document has U+FFFD replacement chars AND mojibake byte
  patterns — both firing is fine; they surface the same failure from
  two orthogonal angles.

Two independent signals OR together:

Signal A — Non-standard character density:
    metric:     count(char not in {letter, digit, whitespace, common punct})
                / max(1, total_chars_in_body)
    fires when: density >= 0.15 across all body text

    "Common punct" is the ASCII printable punctuation set. Latin-1
    accented characters (e, e, o, i, u, n, c, ...) count as
    letters via str.isalpha(); NOT as non-standard. The signal fires
    on symbol junk, control characters, mojibake byte fragments, and
    binary-in-text leaks — not on legitimate international content.

Signal B — Extreme character repetition:
    metric:     count of runs of >= 8 identical characters, OR
                count of runs of >= 6 identical alphanumeric chars
    fires when: extreme_run_count >= 1 OR long_run_count >= 3

    Runs of 8+ identical chars are almost always parser failures
    (e.g., "aaaaaaaaa", "1111111111", "..............."). The 6-char
    alphanumeric threshold is looser but requires 3+ occurrences.

Skip guards:
    * File types: pdf, docx, doc, html, md, txt
    * Tiny-doc guard: fewer than 10 non-empty lines
    * Block-type filter: PARAGRAPH, HEADING, LIST only — tables and
      code blocks are excluded (they legitimately contain non-word
      content that would false-positive).
"""
from __future__ import annotations

import re
import string

from ...context import CompilationContext
from ...models.block import BlockType
from ...scoring.detector_budget import DetectorBudget
from ..base import ValidatorPlugin
from ..registry import register_plugin

# Standard character set: letters (any Unicode letter), digits,
# whitespace, ASCII punctuation. Anything else is "non-standard" for
# Signal A.
_ASCII_PUNCT: frozenset[str] = frozenset(string.punctuation)

# Signal A firing threshold.
_NON_STANDARD_DENSITY_THRESHOLD: float = 0.15

# Signal B: run-length thresholds.
_EXTREME_RUN_LENGTH: int = 8   # any run of >=8 identical chars fires alone
_LONG_RUN_LENGTH: int = 6      # >=6 identical alphanumeric chars — 3+ needed
_LONG_RUN_COUNT_THRESHOLD: int = 3

# Runs of 8+ identical NON-WHITESPACE characters. Whitespace runs would
# false-positive on deeply indented code and formatted tables where
# consecutive spaces are legitimate. Non-whitespace runs of 8+ are almost
# always parser garbage — mojibake filler, symbol soup, catastrophic OCR.
_EXTREME_RUN_RE = re.compile(r"(\S)\1{7,}")
# Runs of 6+ identical alphanumeric characters.
_LONG_ALNUM_RUN_RE = re.compile(r"([A-Za-z0-9])\1{5,}")

# Tiny-doc guard.
_MIN_NONEMPTY_LINES: int = 10

# Eligible file types.
_ELIGIBLE_FILE_TYPES: frozenset[str] = frozenset(
    {"pdf", "docx", "doc", "html", "md", "txt"}
)

# Per-detector wall-clock budget (ms). Pattern matching is cheap.
_BUDGET_MS: int = 500


def _is_non_standard_char(c: str) -> bool:
    """A character is non-standard iff it is not a letter, digit,
    whitespace, or ASCII punctuation. Latin-1 accented characters
    count as letters (str.isalpha() is Unicode-aware)."""
    if c.isalpha() or c.isdigit() or c.isspace():
        return False
    if c in _ASCII_PUNCT:
        return False
    return True


def _collect_signals(blocks: list) -> tuple[int, int, int, int, int]:
    """Return (total_chars, non_standard_chars, extreme_run_count,
    long_run_count, nonempty_lines) across the given blocks."""
    total_chars = 0
    non_standard_chars = 0
    extreme_run_count = 0
    long_run_count = 0
    nonempty_lines = 0
    for block in blocks:
        content = block.content or ""
        total_chars += len(content)
        non_standard_chars += sum(1 for c in content if _is_non_standard_char(c))
        extreme_run_count += len(_EXTREME_RUN_RE.findall(content))
        long_run_count += len(_LONG_ALNUM_RUN_RE.findall(content))
        for line in content.splitlines():
            if line.strip():
                nonempty_lines += 1
    return total_chars, non_standard_chars, extreme_run_count, long_run_count, nonempty_lines


class GibberishValidator(ValidatorPlugin):
    """W_GIBBERISH — gibberish content detector (Phase 2)."""

    name = "gibberish_validator"
    # Runs after placeholder_stub (39). Whole-document signal.
    priority = 40

    # Detection-only cap treatment lives in this same PR (following the
    # W_PLACEHOLDER_STUB convention that combined detection + cap for
    # detectors with well-established downstream patterns).
    warning_maturity = "experimental"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document

        if doc.file_type not in _ELIGIBLE_FILE_TYPES:
            return ctx

        with DetectorBudget(ctx, "W_GIBBERISH", budget_ms=_BUDGET_MS):
            text_blocks = [
                b for b in doc.blocks
                if b.type in (BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST)
            ]
            (
                total_chars,
                non_standard_chars,
                extreme_run_count,
                long_run_count,
                nonempty_lines,
            ) = _collect_signals(text_blocks)

            non_standard_density = (
                non_standard_chars / total_chars if total_chars > 0 else 0.0
            )

            diagnostics: dict = {
                "total_chars": total_chars,
                "non_standard_chars": non_standard_chars,
                "non_standard_density": non_standard_density,
                "extreme_run_count": extreme_run_count,
                "long_run_count": long_run_count,
                "nonempty_lines": nonempty_lines,
                "warned": False,
                "warning_maturity": self.warning_maturity,
            }

            if nonempty_lines < _MIN_NONEMPTY_LINES:
                diagnostics["suppressed_reason"] = (
                    f"too few nonempty lines ({nonempty_lines} < {_MIN_NONEMPTY_LINES})"
                )
                doc.metadata["gibberish_diagnostics"] = diagnostics
                return ctx

            density_fires = non_standard_density >= _NON_STANDARD_DENSITY_THRESHOLD
            extreme_fires = extreme_run_count >= 1
            long_run_fires = long_run_count >= _LONG_RUN_COUNT_THRESHOLD

            if not (density_fires or extreme_fires or long_run_fires):
                doc.metadata["gibberish_diagnostics"] = diagnostics
                return ctx

            fired_triggers: list[str] = []
            if density_fires:
                fired_triggers.append("non_standard_density")
            if extreme_fires:
                fired_triggers.append("extreme_run")
            if long_run_fires:
                fired_triggers.append("long_run")

            diagnostics["warned"] = True
            diagnostics["fired_triggers"] = fired_triggers
            doc.metadata["gibberish_diagnostics"] = diagnostics

            reason_parts: list[str] = []
            if density_fires:
                reason_parts.append(
                    f"non-standard character density {non_standard_density:.3f} "
                    f"({non_standard_chars} of {total_chars} chars)"
                )
            if extreme_fires:
                reason_parts.append(f"{extreme_run_count} extreme repetition run(s)")
            if long_run_fires:
                reason_parts.append(f"{long_run_count} long alphanumeric run(s)")
            reason = "; ".join(reason_parts)

            ctx.warn(
                "W_GIBBERISH",
                (
                    f"Gibberish content detected in extracted output "
                    f"(at least: {reason}). This is a LOWER BOUND — real "
                    f"gibberish may exceed these counts. Sources: mojibake "
                    f"byte fragments (density), symbol junk (density), or "
                    f"extreme character repetition from parser failure "
                    f"(extreme_run / long_run triggers)."
                ),
            )

        return ctx


register_plugin(GibberishValidator)
