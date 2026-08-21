"""Encoding-artifact validator — W_ENCODING_ARTIFACTS.

Emits ``W_ENCODING_ARTIFACTS`` when the extracted output shows visible
evidence that the encoding or segmentation stage of the parser failed on
part of the content. Two independent triggers OR together — either firing
means the warning fires — because both point to the same underlying class
of failure ("content silently mangled or dropped because a decoding path
broke mid-stream").

Design origin: user-confirmed pick from
``docs/calibration/PHASE_3_DETECTION.md`` §3.2 — Candidates A + C jointly.
Positive dev-split reference doc is ``text_dense__de`` (German patent PDF):
extracted output covers only ~25% of GT sentences and shows both XML tag
fragments (``</pt192><pt193``) and replacement characters (``�``).

Trigger A — XML tag residue:
    regex:      r'</?(?:pt|font|span|div|tspan)\\d+[^>]*>?'
    fires when: xml_fragment_count >= 3

    The ``\\d+`` (numeric suffix) is the FP guard — see below.

Trigger C — Mojibake density:
    metric:     count("\\ufffd") / max(1, len(content))
    fires when: mojibake_density >= 0.005 (>= 0.5 percent)

Skip guards mirror ``W_TABLE_MISSING``:
    * File types: pdf, docx, doc, html only.
    * Do not fire if OCR_REQUIRED already fired — OCR failure modes are a
      distinct class already surfaced by the OCR pipeline.
    * Do not fire on documents with fewer than 20 non-empty lines.

FP guard on Trigger A:
    A legitimate code-block containing ``</span>`` inside HTML documentation
    must not fire the XML residue trigger. Two guards keep the trigger
    specific to PDF-to-XML residue:

    1. Numeric suffix (``\\d+``) required after the tag name. Real HTML
       tags do not carry a numeric suffix (``<span>``, ``</div>`` — no
       match), but PDF-to-XML residue does (``</pt192>``, ``</font17>``,
       ``<tspan42`` — match). The dev-split positive ``text_dense__de``
       exhibits many such fragments (``</pt192><pt193`` sequences).

    2. Threshold of 3 matches over the whole document. A single stray
       fragment from an OCR pass or a legitimate SVG snippet with a
       ``<tspan1>`` in it will not trip a warning; the trigger requires a
       density that indicates systematic pipeline breakage.

This validator emits only the warning. No readiness score cap is attached
in this PR (see ``docs/calibration/SCORING_POLICY.md`` §5 and Phase 3
rules: detection and scoring ship in separate PRs). The follow-up cap PR
will consume the ``encoding_artifacts_diagnostics`` metadata dict and
attach a maturity-aware cap.
"""
from __future__ import annotations

import re

from ...context import CompilationContext
from ...models.block import BlockType
from ..base import ValidatorPlugin
from ..registry import register_plugin

# Trigger A: XML tag residue. Matches tag-name patterns commonly emitted
# by broken PDF-to-XML pipelines. The ``\d+`` after the tag name is the
# corruption signal: real HTML tags never carry a numeric suffix
# (``<span>``, ``</div>`` — safe), but PDF-to-XML residue does
# (``</pt192>``, ``</font17>``, ``<tspan42``). This alone is enough to
# exclude legitimate HTML documentation content that discusses tags.
# See module docstring for the FP-guard rationale.
XML_FRAGMENT_RE = re.compile(
    r"</?(?:pt|font|span|div|tspan)\d+[^>]*>?"
)

# Trigger C: Mojibake replacement-character density.
_MOJIBAKE_CHAR: str = "�"
_MOJIBAKE_DENSITY_THRESHOLD: float = 0.005

# Trigger A firing threshold.
_XML_FRAGMENT_THRESHOLD: int = 3

# Tiny-doc guard: below this number of non-empty lines, do not evaluate.
_MIN_NONEMPTY_LINES: int = 20

# File types eligible for this signal — matches W_TABLE_MISSING scope.
_ELIGIBLE_FILE_TYPES: frozenset[str] = frozenset({"pdf", "docx", "doc", "html"})

# When OCR_REQUIRED has already fired, the document has a distinct failure
# class already surfaced. Do not compound.
_SUPPRESS_IF_WARNINGS: frozenset[str] = frozenset({"OCR_REQUIRED"})


def _collect_signals(blocks: list) -> tuple[int, int, int, int]:
    """
    Return (xml_fragment_count, mojibake_count, total_chars, nonempty_lines).
    """
    xml_fragment_count = 0
    mojibake_count = 0
    total_chars = 0
    nonempty_lines = 0
    for block in blocks:
        content = block.content or ""
        total_chars += len(content)
        mojibake_count += content.count(_MOJIBAKE_CHAR)
        xml_fragment_count += len(XML_FRAGMENT_RE.findall(content))
        for line in content.splitlines():
            if line.strip():
                nonempty_lines += 1
    return xml_fragment_count, mojibake_count, total_chars, nonempty_lines


class EncodingArtifactsValidator(ValidatorPlugin):
    """
    W_ENCODING_ARTIFACTS — encoding / segmentation pipeline failure detector.

    Fires when EITHER
        xml_fragment_count >= 3
        OR mojibake_density >= 0.005
    AND the eligibility guards (file type, no OCR_REQUIRED, min lines) hold.

    Warning maturity is ``candidate`` — the two sub-triggers are both direct
    textual evidence of a pipeline failure, and both stayed silent on the 20
    dev-split control docs used to lock the thresholds.
    """

    name = "encoding_artifacts_validator"
    # Runs after multicolumn (35), header/footer table (36), and
    # table_missing (37). Encoding artifacts are a whole-document signal
    # so ordering after the block-level detectors is fine.
    priority = 38

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
            doc.metadata["encoding_artifacts_diagnostics"] = {
                "xml_fragment_count": 0,
                "mojibake_count": 0,
                "mojibake_density": 0.0,
                "nonempty_lines": 0,
                "warned": False,
                "suppressed_reason": "OCR_REQUIRED already active",
                "warning_maturity": self.warning_maturity,
            }
            return ctx

        # Compute signals across all text-bearing blocks.
        text_blocks = [
            b for b in doc.blocks
            if b.type in (BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST)
        ]
        (
            xml_fragment_count,
            mojibake_count,
            total_chars,
            nonempty_lines,
        ) = _collect_signals(text_blocks)

        mojibake_density = mojibake_count / total_chars if total_chars > 0 else 0.0

        diagnostics: dict = {
            "xml_fragment_count": xml_fragment_count,
            "mojibake_count": mojibake_count,
            "mojibake_density": mojibake_density,
            "nonempty_lines": nonempty_lines,
            "warned": False,
            "warning_maturity": self.warning_maturity,
        }

        # Guard 3: tiny-doc guard.
        if nonempty_lines < _MIN_NONEMPTY_LINES:
            diagnostics["suppressed_reason"] = (
                f"too few nonempty lines ({nonempty_lines} < {_MIN_NONEMPTY_LINES})"
            )
            doc.metadata["encoding_artifacts_diagnostics"] = diagnostics
            return ctx

        # Fire on EITHER trigger.
        xml_fires = xml_fragment_count >= _XML_FRAGMENT_THRESHOLD
        mojibake_fires = mojibake_density >= _MOJIBAKE_DENSITY_THRESHOLD
        if not (xml_fires or mojibake_fires):
            doc.metadata["encoding_artifacts_diagnostics"] = diagnostics
            return ctx

        fired_triggers: list[str] = []
        if xml_fires:
            fired_triggers.append("xml_fragment")
        if mojibake_fires:
            fired_triggers.append("mojibake_density")
        diagnostics["warned"] = True
        diagnostics["fired_triggers"] = fired_triggers
        doc.metadata["encoding_artifacts_diagnostics"] = diagnostics

        reason_parts: list[str] = []
        if xml_fires:
            reason_parts.append(
                f"{xml_fragment_count} XML-tag fragment(s) in text"
            )
        if mojibake_fires:
            reason_parts.append(
                f"replacement-character density {mojibake_density:.4f} "
                f"({mojibake_count} of {total_chars} chars)"
            )
        reason = "; ".join(reason_parts)

        ctx.warn(
            "W_ENCODING_ARTIFACTS",
            (
                f"Encoding or segmentation artifacts detected in extracted output "
                f"({reason}). This usually means part of the source content was "
                "silently dropped or fragmented because a decoding path broke "
                "mid-stream. The extracted text may be incomplete or contain "
                "corrupt tag fragments."
            ),
        )

        return ctx


register_plugin(EncodingArtifactsValidator)
