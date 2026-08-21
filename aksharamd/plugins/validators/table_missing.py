"""Table-missing (leader-dot) validator — W_TABLE_MISSING.

Emits W_TABLE_MISSING when the extracted output contains leader-dot sequences
(runs of ".", ". . . .", etc.) at a density that indicates a table of contents
or a real table was rendered as tab-separated prose and the parser did not
recover its structure.

Trigger A (per Phase 3 scope, docs/calibration/PLAN.md) fires on EITHER of two
sub-conditions across the extracted text:

    regex:      r'(\\.\\s){4,}|\\.{5,}'
    fires when: leader_dot_lines >= 3
                OR total_leader_dot_matches >= 5

The second sub-condition is the ``strikeUnderline`` fallback added after user
confirmation on the design in ``docs/calibration/PHASE_3_DETECTION.md`` §3.1
(Option A). ``text_simple__strikeUnderline`` emits its entire TOC on a single
smushed line with 54 regex matches; the per-line count is 1 so the primary
``leader_dot_lines`` gate misses the doc. Total-match fallback catches it
without loosening controls (all 20 dev-split controls observed 0 total
matches — the ``>= 5`` threshold has ample margin).

Skip guards from TABLE_READINESS_DIAGNOSTIC_DESIGN §Signal 3:
    * File types: pdf, docx, doc, html only.
    * Do not fire if OCR_REQUIRED already fired (image-only pages are a
      different failure class already surfaced by the OCR pipeline).
    * Do not fire on documents with fewer than 20 non-empty lines
      (tiny-doc guard — leader-dot detection needs enough content to
      be meaningful).

Design notes:
    * Signal 3 was chosen as the v1 leader-dot trigger because it is the
      most decisive of the four table-quality signals proposed in
      TABLE_READINESS_DIAGNOSTIC_DESIGN. Leader dots are a near-certain
      indicator that a TOC or table was flattened into prose.
    * Calibration positives (from ParseBench text_content dev split):
        - text_misc__ikea3            — 21 lines with leader-dot matches
        - text_simple__strikeUnderline — 1 line, 54 total matches
          (caught only by the total-match fallback added in this PR)
    * Calibration controls (must stay silent — 0 lines AND 0 total matches):
        - text_multicolumns__2colmercedes, __battery, __3colpres, __4c,
          __gridofnumbers, __pwc, __simple2, __elpais
        - text_simple__budget, __edits, __eastbaytimes, __minutes2, __refpage,
          __webprint, __letter3, __myctophidae
        - text_misc__docusigned
        - text_dense__de, __japanese

This validator emits only the warning. No readiness score cap is attached
in this PR (see docs/calibration/SCORING_POLICY.md §5 and Phase 3 rules:
detection and scoring ship in separate PRs).
"""
from __future__ import annotations

import re

from ...context import CompilationContext
from ...models.block import BlockType
from ..base import ValidatorPlugin
from ..registry import register_plugin

# Trigger A regex per TABLE_READINESS_DIAGNOSTIC_DESIGN §Signal 3.
# Matches either a run of ". " pairs (4 or more) or a run of "." (5 or more).
LEADER_DOT_RE = re.compile(r"(\.\s){4,}|\.{5,}")

# Minimum lines with a leader-dot match required to fire the warning
# via the primary per-line gate.
_LEADER_DOT_LINES_THRESHOLD: int = 3

# Minimum total regex matches across the whole document required to fire
# via the smushed-single-line fallback gate. Locked at 5 per user
# confirmation on PHASE_3_DETECTION.md §3.1 Option A. All 20 dev-split
# controls observed 0 total matches, so this threshold has broad margin.
_LEADER_DOT_TOTAL_MATCHES_THRESHOLD: int = 5

# Tiny-doc guard: below this number of non-empty lines, do not evaluate.
_MIN_NONEMPTY_LINES: int = 20

# File types eligible for this signal. Plain-text formats and structured
# formats like markdown/csv are excluded — leader dots in those are either
# intentional or preserved by the format.
_ELIGIBLE_FILE_TYPES: frozenset[str] = frozenset({"pdf", "docx", "doc", "html"})

# When OCR_REQUIRED has already fired, the document has a distinct failure
# class (image-only pages) already surfaced. Do not compound.
_SUPPRESS_IF_WARNINGS: frozenset[str] = frozenset({"OCR_REQUIRED"})


def _count_leader_dot_signals(blocks: list) -> tuple[int, int, int]:
    """
    Return (leader_dot_lines, total_matches, nonempty_lines).

    * leader_dot_lines: count of block-content lines with at least one match.
    * total_matches: total regex matches across all block content.
    * nonempty_lines: count of non-empty content lines across all blocks.
    """
    leader_dot_lines = 0
    total_matches = 0
    nonempty_lines = 0
    for block in blocks:
        content = block.content or ""
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            nonempty_lines += 1
            matches = LEADER_DOT_RE.findall(line)
            if matches:
                leader_dot_lines += 1
                total_matches += len(matches)
    return leader_dot_lines, total_matches, nonempty_lines


class TableMissingValidator(ValidatorPlugin):
    """
    W_TABLE_MISSING — Trigger A (leader-dot detection).

    Fires when EITHER
        leader_dot_lines >= 3         (per-line gate)
        OR total_matches >= 5         (smushed-single-line fallback)
    AND the eligibility guards (file type, no OCR_REQUIRED, min lines) hold.

    Warning maturity is ``candidate`` — the leader-dot trigger has a
    well-defined positive class (TOC-as-prose, table-as-prose) and controls
    that historically emit zero leader-dot lines and zero total matches.
    Calibration on the 21-doc ParseBench dev set shows 2 positives (ikea3 via
    the primary gate, strikeUnderline via the fallback) and 19 controls silent.
    """

    name = "table_missing_validator"
    priority = 37  # after multicolumn (35) and header/footer table (36)

    # Detection-only signal. Cap attachment lives in a separate PR
    # (per feedback_detection_vs_scoring_separation.md convention).
    warning_maturity = "candidate"

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        if ctx.document is None:
            return ctx

        doc = ctx.document

        # Guard 1: file type eligibility.
        if doc.file_type not in _ELIGIBLE_FILE_TYPES:
            return ctx

        # Guard 2: OCR_REQUIRED already fired — do not compound signals.
        active_codes = {
            getattr(i, "code", "")
            for i in ctx.validation.issues
        }
        if _SUPPRESS_IF_WARNINGS & active_codes:
            doc.metadata["table_missing_diagnostics"] = {
                "leader_dot_lines": 0,
                "total_leader_dot_matches": 0,
                "nonempty_lines": 0,
                "warned": False,
                "suppressed_reason": "OCR_REQUIRED already active",
                "warning_maturity": self.warning_maturity,
            }
            return ctx

        # Compute signals across all blocks — text-bearing types only.
        text_blocks = [
            b for b in doc.blocks
            if b.type in (BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST)
        ]
        leader_dot_lines, total_matches, nonempty_lines = _count_leader_dot_signals(
            text_blocks
        )

        diagnostics = {
            "leader_dot_lines": leader_dot_lines,
            "total_leader_dot_matches": total_matches,
            "nonempty_lines": nonempty_lines,
            "warned": False,
            "warning_maturity": self.warning_maturity,
        }

        # Guard 3: tiny-doc guard — need enough content for the signal
        # to be meaningful.
        if nonempty_lines < _MIN_NONEMPTY_LINES:
            diagnostics["suppressed_reason"] = (
                f"too few nonempty lines ({nonempty_lines} < {_MIN_NONEMPTY_LINES})"
            )
            doc.metadata["table_missing_diagnostics"] = diagnostics
            return ctx

        # Trigger A: fire on EITHER the per-line gate OR the total-match
        # fallback. The two sub-conditions target the same underlying
        # failure mode (leader-dot prose) but differ in how the parser
        # emits the affected block.
        primary_fires = leader_dot_lines >= _LEADER_DOT_LINES_THRESHOLD
        fallback_fires = total_matches >= _LEADER_DOT_TOTAL_MATCHES_THRESHOLD
        if not (primary_fires or fallback_fires):
            doc.metadata["table_missing_diagnostics"] = diagnostics
            return ctx

        # Record which sub-trigger(s) fired so the eventual cap PR and any
        # downstream calibration audit can distinguish primary vs fallback.
        fired_triggers: list[str] = []
        if primary_fires:
            fired_triggers.append("leader_dot_lines")
        if fallback_fires:
            fired_triggers.append("total_leader_dot_matches")
        diagnostics["warned"] = True
        diagnostics["fired_triggers"] = fired_triggers
        doc.metadata["table_missing_diagnostics"] = diagnostics

        ctx.warn(
            "W_TABLE_MISSING",
            (
                f"Leader-dot patterns detected on {leader_dot_lines} line(s) "
                f"({total_matches} total match(es)). This usually means a table "
                "of contents or a real table was rendered as dotted prose and its "
                "structure was not recovered. The extracted text may miss the "
                "trailing columns of each row (e.g. page numbers in a TOC)."
            ),
        )

        return ctx


register_plugin(TableMissingValidator)
