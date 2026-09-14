"""Placeholder-stub validator — W_PLACEHOLDER_STUB (Phase 1).

Emits ``W_PLACEHOLDER_STUB`` when the extracted output shows visible
evidence of stub or placeholder content — either from the source (an
unfilled form template) or the parser (image/table stubs, LLM refusal
messages).

Design origin: Phase 1 substance-detector portfolio, driven by RAG
postmortem research (arXiv 2606.15020 and adjacent) showing that
placeholder-stub silent failures — parsed markdown looks fine, indexes
fine, poisons retrieval quietly — are among the top named silent-
failure modes in production document AI.

**Framing as a LOWER BOUND** — per Rojas et al. arXiv 2605.07293,
pattern-based detectors report at-least counts, not ceiling counts.
The emitted diagnostic states "at least N stubs detected"; the true
count is at least this number.

Four independent triggers OR together:

Trigger A — Bracketed placeholders:
    regex:      r'\\[[A-Z][A-Za-z ]{2,30}\\]'
    fires when: bracket_count >= 3 across all text-bearing blocks

    Requires first letter uppercase and 3+ total inside-bracket chars
    to exclude ``[1]``, ``[a]``, ``[note 3]`` (real footnote references)
    while catching ``[Signature]``, ``[Employee ID]``, ``[Date]``,
    ``[Witness Name and Signature]`` (form-template placeholders).

Trigger B — Extraction stubs:
    literals:   '[Image omitted]', '[Figure omitted]', '[Table not extracted]',
                '<figure>', '<image_placeholder>', '<image>', '[image]',
                '[figure]', '[table]', '[chart]'
    fires when: extraction_stub_count >= 1

    Single occurrence suffices — these are unambiguous parser-side stubs.

Trigger C — LLM refusal stubs:
    substrings: 'I cannot process', 'As an AI', "I'm unable to",
                'I apologize, but'
    fires when: refusal_count >= 1

    Single occurrence suffices. Case-insensitive.

Trigger D — Underscored form blanks:
    regex:      r'_{5,}'
    fires when: underscore_run_count >= 3

    5+ consecutive underscores (typical form-blank marker); 3+ separate
    runs required so a single legit line separator does not trigger.

Any trigger firing causes the warning. Diagnostics record per-trigger
counts so downstream analysis can distinguish "unfilled form template"
(A, D dominant) from "parser-side stub" (B, C dominant).

Skip guards:
    * File types: pdf, docx, doc, html, md, txt (all text-bearing outputs)
    * Tiny-doc guard: fewer than 10 non-empty lines
    * No cross-warning suppression — this is orthogonal to OCR failures.

Maturity: **experimental**. The P0.5 corpus ships one positive + one
negative per class; not enough to lock at candidate. Cap attachment is
intentionally deferred to a follow-up PR per the detection-vs-scoring
separation convention (see ``feedback_detection_vs_scoring_separation.md``).
This PR emits the warning; the follow-up PR wires the score cap.
"""
from __future__ import annotations

import re

from ...context import CompilationContext
from ...models.block import BlockType
from ...scoring.detector_budget import DetectorBudget
from ..base import ValidatorPlugin
from ..registry import register_plugin

# Trigger A: bracketed placeholders (form-template style).
BRACKET_PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Za-z ]{2,30}\]")

# Trigger B: known parser-side extraction stubs. Case-insensitive literals.
_EXTRACTION_STUBS: tuple[str, ...] = (
    "[image omitted]",
    "[figure omitted]",
    "[table not extracted]",
    "[table omitted]",
    "[chart omitted]",
    "<figure>",
    "<image_placeholder>",
    "<image>",
    "[image]",
    "[figure]",
    "[table]",
    "[chart]",
)

# Trigger C: LLM refusal fingerprints. Case-insensitive.
_LLM_REFUSAL_SUBSTRINGS: tuple[str, ...] = (
    "i cannot process",
    "as an ai",
    "i'm unable to",
    "i am unable to",
    "i apologize, but",
)

# Trigger D: underscored form blanks.
UNDERSCORE_RUN_RE = re.compile(r"_{5,}")

# Firing thresholds.
_BRACKET_THRESHOLD: int = 3
_EXTRACTION_STUB_THRESHOLD: int = 1
_REFUSAL_THRESHOLD: int = 1
_UNDERSCORE_RUN_THRESHOLD: int = 3

# Tiny-doc guard: below this many non-empty lines, do not evaluate.
_MIN_NONEMPTY_LINES: int = 10

# Eligible file types — all text-bearing extraction outputs.
_ELIGIBLE_FILE_TYPES: frozenset[str] = frozenset(
    {"pdf", "docx", "doc", "html", "md", "txt"}
)

# Per-detector wall-clock budget (ms). Pattern matching is cheap; 500ms
# is plenty for a 100-page document.
_BUDGET_MS: int = 500


def _collect_signals(blocks: list) -> tuple[int, int, int, int, int]:
    """Return (bracket_count, extraction_stub_count, refusal_count,
    underscore_run_count, nonempty_lines) across the given blocks."""
    bracket_count = 0
    extraction_stub_count = 0
    refusal_count = 0
    underscore_run_count = 0
    nonempty_lines = 0
    for block in blocks:
        content = block.content or ""
        bracket_count += len(BRACKET_PLACEHOLDER_RE.findall(content))
        underscore_run_count += len(UNDERSCORE_RUN_RE.findall(content))
        lower = content.lower()
        for stub in _EXTRACTION_STUBS:
            extraction_stub_count += lower.count(stub)
        for refusal in _LLM_REFUSAL_SUBSTRINGS:
            refusal_count += lower.count(refusal)
        for line in content.splitlines():
            if line.strip():
                nonempty_lines += 1
    return (
        bracket_count,
        extraction_stub_count,
        refusal_count,
        underscore_run_count,
        nonempty_lines,
    )


class PlaceholderStubValidator(ValidatorPlugin):
    """W_PLACEHOLDER_STUB — placeholder / stub content detector (Phase 1).

    Fires when any of the four triggers exceeds its threshold, subject to
    the file-type and tiny-doc guards. Diagnostic dict is stored at
    ``ctx.document.metadata['placeholder_stub_diagnostics']`` for
    downstream cap logic to consume.
    """

    name = "placeholder_stub_validator"
    # Runs after encoding_artifacts (38); placeholder detection is a
    # whole-document signal so ordering after block-level detectors is fine.
    priority = 39

    # Cap attachment lives in a separate PR per
    # feedback_detection_vs_scoring_separation.md. Ships experimental —
    # only two seed fixtures in the P0.5 corpus.
    warning_maturity = "experimental"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document

        # Guard 1: file type eligibility.
        if doc.file_type not in _ELIGIBLE_FILE_TYPES:
            return ctx

        with DetectorBudget(ctx, "W_PLACEHOLDER_STUB", budget_ms=_BUDGET_MS):
            text_blocks = [
                b for b in doc.blocks
                if b.type in (BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST)
            ]
            (
                bracket_count,
                extraction_stub_count,
                refusal_count,
                underscore_run_count,
                nonempty_lines,
            ) = _collect_signals(text_blocks)

            diagnostics: dict = {
                "bracket_count": bracket_count,
                "extraction_stub_count": extraction_stub_count,
                "refusal_count": refusal_count,
                "underscore_run_count": underscore_run_count,
                "nonempty_lines": nonempty_lines,
                "warned": False,
                "warning_maturity": self.warning_maturity,
            }

            # Guard 2: tiny-doc guard.
            if nonempty_lines < _MIN_NONEMPTY_LINES:
                diagnostics["suppressed_reason"] = (
                    f"too few nonempty lines ({nonempty_lines} < {_MIN_NONEMPTY_LINES})"
                )
                doc.metadata["placeholder_stub_diagnostics"] = diagnostics
                return ctx

            # Evaluate each trigger.
            bracket_fires = bracket_count >= _BRACKET_THRESHOLD
            stub_fires = extraction_stub_count >= _EXTRACTION_STUB_THRESHOLD
            refusal_fires = refusal_count >= _REFUSAL_THRESHOLD
            underscore_fires = underscore_run_count >= _UNDERSCORE_RUN_THRESHOLD

            if not (bracket_fires or stub_fires or refusal_fires or underscore_fires):
                doc.metadata["placeholder_stub_diagnostics"] = diagnostics
                return ctx

            fired_triggers: list[str] = []
            if bracket_fires:
                fired_triggers.append("bracket_placeholder")
            if stub_fires:
                fired_triggers.append("extraction_stub")
            if refusal_fires:
                fired_triggers.append("llm_refusal")
            if underscore_fires:
                fired_triggers.append("underscore_run")

            diagnostics["warned"] = True
            diagnostics["fired_triggers"] = fired_triggers
            doc.metadata["placeholder_stub_diagnostics"] = diagnostics

            reason_parts: list[str] = []
            if bracket_fires:
                reason_parts.append(f"{bracket_count} bracketed placeholder(s)")
            if stub_fires:
                reason_parts.append(f"{extraction_stub_count} extraction stub(s)")
            if refusal_fires:
                reason_parts.append(f"{refusal_count} LLM-refusal stub(s)")
            if underscore_fires:
                reason_parts.append(f"{underscore_run_count} underscore blank(s)")
            reason = "; ".join(reason_parts)

            ctx.warn(
                "W_PLACEHOLDER_STUB",
                (
                    f"Placeholder or stub content detected in extracted output "
                    f"(at least: {reason}). This is a LOWER BOUND — the true "
                    f"count may be higher. Sources: unfilled form templates "
                    f"(bracket / underscore triggers), parser stubs "
                    f"(extraction_stub trigger), or LLM refusal responses "
                    f"(llm_refusal trigger)."
                ),
            )

        return ctx


register_plugin(PlaceholderStubValidator)
