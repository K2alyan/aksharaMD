"""Structured models for auditable readiness scoring (Phase 3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SCORING_POLICY_VERSION = "1.5"


@dataclass
class ReadinessEvidence:
    """Quantitative evidence attached to one scoring finding."""
    metric_name: str
    metric_value: float
    threshold: float = 0.0
    pages: list[int] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
            "pages": self.pages,
            "block_ids": self.block_ids,
            "extras": self.extras,
        }


@dataclass
class DeductionRecord:
    """One scoring finding — may be active, suppressed, or informational (zero-penalty)."""
    rule_id: str
    description: str
    penalty: int
    suppressed: bool = False
    suppression_reason: str = ""
    evidence: ReadinessEvidence | None = None
    maturity: str = ""   # "stable" | "candidate" | "experimental" — empty means stable

    def to_dict(self) -> dict:
        d: dict = {
            "rule_id": self.rule_id,
            "description": self.description,
            "penalty": self.penalty,
        }
        if self.suppressed:
            d["suppressed"] = True
            d["suppression_reason"] = self.suppression_reason
        if self.evidence is not None:
            d["evidence"] = self.evidence.to_dict()
        if self.maturity:
            d["maturity"] = self.maturity
        return d


@dataclass
class ReadinessResult:
    """Full structured scoring result.

    Backward-compat: .score and .notes match the old ConfidenceResult interface.
    New callers may also inspect .deductions, .informational, and .scoring_policy_version.
    """
    score: int
    notes: list[str] = field(default_factory=list)
    deductions: list[DeductionRecord] = field(default_factory=list)
    informational: list[DeductionRecord] = field(default_factory=list)
    scoring_policy_version: str = SCORING_POLICY_VERSION

    @property
    def structural_score(self) -> int:
        """Sub-score from structural-category deductions only. Bounded [0, 100].

        Unknown rule_ids (not in SCORING_POLICY) do not contribute to either axis.
        Cap-based rules contribute their realized penalty as recorded on the
        DeductionRecord; axis semantics for pure caps may drift from single-score
        semantics — refinement is a follow-up.
        """
        return _axis_score(self.deductions, "structural")

    @property
    def substance_score(self) -> int:
        """Sub-score from content-category deductions only. Bounded [0, 100].

        See structural_score for handling of unknown rule_ids and caps.
        """
        return _axis_score(self.deductions, "content")


def _axis_score(deductions: list[DeductionRecord], category: str) -> int:
    penalty = 0
    for d in deductions:
        if d.suppressed:
            continue
        rule = SCORING_POLICY.get(d.rule_id)
        if rule is not None and rule.category == category:
            penalty += d.penalty
    return max(0, min(100, 100 - penalty))


# Backward-compat alias
ConfidenceResult = ReadinessResult


@dataclass
class ScoringRule:
    """Describes one scoring rule in the centralized policy."""
    rule_id: str
    description: str
    max_penalty: int
    formula: str          # human-readable formula string
    category: Literal["structural", "content", "meta"] = "structural"
    suppresses: list[str] = field(default_factory=list)   # rule_ids this suppresses
    suppressed_by: list[str] = field(default_factory=list)  # rule_ids that suppress this


# Central policy — single source of truth for all scoring rule metadata.
# Actual computation is still in readiness.py; this table documents intent.
#
# category: two-axis receipt classification (structural | content | meta).
# - structural: fires on layout/geometry/reading-order/block-structure failures
# - content:    fires on the extracted text or content being wrong or missing
# - meta:       informational/observational (not itself a parser-quality signal)
# See project_positioning_decisions_2026-09-13.md and P0.4 PR for rationale.
SCORING_POLICY: dict[str, ScoringRule] = {
    "FORMAT_BASELINE": ScoringRule(
        rule_id="FORMAT_BASELINE",
        description="Starting score for the source file format",
        max_penalty=0,
        formula="score = FORMAT_BASE.get(file_type, 72)",
        category="meta",
    ),
    "PARSE_ERRORS": ScoringRule(
        rule_id="PARSE_ERRORS",
        description="Penalty for parse errors — some content is missing",
        max_penalty=30,
        formula="min(30, n_errors × 12)",
        category="content",
    ),
    "MISSING_PAGE": ScoringRule(
        rule_id="MISSING_PAGE",
        description="Penalty for pages with no extractable text (PDF/DOCX)",
        max_penalty=38,  # 30 + 8 for ≥50%
        formula="min(30, n × 4) [+ 8 if ≥50% pages affected]",
        category="content",
    ),
    "LARGE_BLOCK": ScoringRule(
        rule_id="LARGE_BLOCK",
        description="Penalty for blocks >10 000 chars — likely parse/merge failure",
        max_penalty=10,
        formula="min(10, n × 4)",
        category="content",
    ),
    "HEADING_ISSUES": ScoringRule(
        rule_id="HEADING_ISSUES",
        description="Penalty for HEADING_SKIP or HEADING_HIERARCHY warnings",
        max_penalty=8,
        formula="min(8, n × 2)",
        category="structural",
    ),
    "IMAGE_PLACEHOLDER_NO_FALLBACK": ScoringRule(
        rule_id="IMAGE_PLACEHOLDER_NO_FALLBACK",
        description="Score cap when output contains only image placeholders and no asset bytes",
        max_penalty=45,  # expressed as a cap: score capped at 55
        formula="score = min(score, 55)",
        category="content",
    ),
    "OCR_REQUIRED": ScoringRule(
        rule_id="OCR_REQUIRED",
        description="Penalty when scanned/hybrid PDF has no OCR available",
        max_penalty=40,
        formula="min(40, int(image_ratio × 40) + 10)",
        category="content",
        suppresses=["NEAR_EMPTY_OUTPUT", "LOW_TEXT_DENSITY"],
    ),
    "OCR_ATTEMPTED_SPARSE": ScoringRule(
        rule_id="OCR_ATTEMPTED_SPARSE",
        description="Penalty when OCR ran but produced near-empty output",
        max_penalty=40,
        formula="min(40, int(image_ratio × 40) + 10)",
        category="content",
        suppresses=["NEAR_EMPTY_OUTPUT", "LOW_TEXT_DENSITY"],
    ),
    "NEAR_EMPTY_OUTPUT": ScoringRule(
        rule_id="NEAR_EMPTY_OUTPUT",
        description="Catastrophic penalty when almost no text was extracted",
        max_penalty=25,
        formula="25 (fixed)",
        category="content",
        suppressed_by=["OCR_REQUIRED", "OCR_ATTEMPTED_SPARSE"],
    ),
    "LOW_TEXT_DENSITY": ScoringRule(
        rule_id="LOW_TEXT_DENSITY",
        description="Penalty for very sparse text relative to page count (PDF)",
        max_penalty=20,
        formula="20 (fixed)",
        category="content",
        suppressed_by=["OCR_REQUIRED", "OCR_ATTEMPTED_SPARSE"],
    ),
    "GLYPH_ARTIFACTS": ScoringRule(
        rule_id="GLYPH_ARTIFACTS",
        description="Penalty for CID font glyph artifacts — text likely garbled",
        max_penalty=25,
        formula="25 (fixed)",
        category="content",
    ),
    "REPEATED_CONTENT": ScoringRule(
        rule_id="REPEATED_CONTENT",
        description="Penalty for repeated lines — boilerplate not fully removed",
        max_penalty=8,
        formula="8 (fixed)",
        category="content",
    ),
    "TOKEN_BLOAT": ScoringRule(
        rule_id="TOKEN_BLOAT",
        description="Penalty for unusually high token count per page (PDF)",
        max_penalty=8,
        formula="8 (fixed)",
        category="content",
    ),
    "NO_HEADINGS_MULTIPAGE": ScoringRule(
        rule_id="NO_HEADINGS_MULTIPAGE",
        description="Penalty when a multi-page document (>3 pages) has no headings",
        max_penalty=6,
        formula="6 (fixed, pages > 3 only)",
        category="structural",
    ),
    "COL_GENERIC_TABLES": ScoringRule(
        rule_id="COL_GENERIC_TABLES",
        description="Penalty for tables with auto-generated column headers (Col1, Col2)",
        max_penalty=5,
        formula="min(5, n × 2)",
        category="content",
    ),
    "NO_TEXT_IN_IMAGE": ScoringRule(
        rule_id="NO_TEXT_IN_IMAGE",
        description="Penalty when an image file has no extractable text",
        max_penalty=10,
        formula="10 (fixed)",
        category="content",
    ),
    "W_MULTICOLUMN_ORDER": ScoringRule(
        rule_id="W_MULTICOLUMN_ORDER",
        description="Score cap when multi-column reading order is likely incorrect (candidate maturity)",
        max_penalty=31,  # cap at 69; max realized penalty is 100 - 69 = 31
        formula="score = min(score, 69)  # RISKY band",
        category="structural",
    ),
    "W_HEADER_FOOTER_TABLE_GARBLED": ScoringRule(
        rule_id="W_HEADER_FOOTER_TABLE_GARBLED",
        description="Score cap when a table near header/footer likely represents garbled page furniture (experimental maturity — softer cap while evidence base grows)",
        max_penalty=16,  # cap at 84; max realized penalty is 100 - 84 = 16
        formula="score = min(score, 84)  # top of OK band",
        category="structural",
    ),
    "W_TABLE_MISSING": ScoringRule(
        rule_id="W_TABLE_MISSING",
        description="Score cap when leader-dot density indicates a TOC/table was flattened to prose (candidate maturity)",
        max_penalty=31,  # cap at 69; max realized penalty is 100 - 69 = 31
        formula="score = min(score, 69)  # RISKY band",
        category="structural",
    ),
    "W_ENCODING_ARTIFACTS": ScoringRule(
        rule_id="W_ENCODING_ARTIFACTS",
        description="Score cap when XML tag residue or mojibake density indicates encoding/segmentation failure (candidate maturity)",
        max_penalty=31,  # cap at 69; max realized penalty is 100 - 69 = 31
        formula="score = min(score, 69)  # RISKY band",
        category="content",
    ),
    "W_IMAGE_ONLY_TEXT_BAR_FAIL": ScoringRule(
        rule_id="W_IMAGE_ONLY_TEXT_BAR_FAIL",
        description="Score cap when a fully image-only PDF (scanned, zero text-layer pages) fails the text-only bar per USP §5.2 (candidate maturity)",
        max_penalty=31,  # cap at 69; max realized penalty is 100 - 69 = 31
        formula="score = min(score, 69)  # RISKY band",
        category="content",
    ),
    "W_TABLE_EXPECTED_NOT_EXTRACTED": ScoringRule(
        rule_id="W_TABLE_EXPECTED_NOT_EXTRACTED",
        description="Score cap when a page had table candidates rejected by the quality filter with corroborating caption/numeric-alignment signals (experimental maturity — softer cap while evidence base grows)",
        max_penalty=16,  # cap at 84; max realized penalty is 100 - 84 = 16
        formula="score = min(score, 84)  # top of OK band",
        category="content",
    ),
    "W_PDF_ATTACHMENT_IGNORED": ScoringRule(
        rule_id="W_PDF_ATTACHMENT_IGNORED",
        description="Informational: PDF contains embedded file attachments that were not extracted",
        max_penalty=0,
        formula="0 (observational only)",
        category="meta",
    ),
    "IMAGE_PLACEHOLDER_WITH_ASSETS": ScoringRule(
        rule_id="IMAGE_PLACEHOLDER_WITH_ASSETS",
        description="Informational: image-only page but asset bytes captured for multimodal use",
        max_penalty=0,
        formula="0 (informational only)",
        category="meta",
    ),
    "AUTO_OCR_BACKEND_SELECTED": ScoringRule(
        rule_id="AUTO_OCR_BACKEND_SELECTED",
        description="Informational: Auto Policy v1 selected an OCR backend",
        max_penalty=0,
        formula="0 (informational only)",
        category="meta",
    ),
    "AUTO_OCR_BACKEND_FALLBACK": ScoringRule(
        rule_id="AUTO_OCR_BACKEND_FALLBACK",
        description="Informational: Auto Policy v1 fell back from preferred backend",
        max_penalty=0,
        formula="0 (informational only)",
        category="meta",
    ),
    "W_DETECTOR_TIMEOUT": ScoringRule(
        rule_id="W_DETECTOR_TIMEOUT",
        description=(
            "Informational: a detector exceeded its per-detector wall-clock "
            "budget under the DetectorBudget primitive (P0.2). Measure-and-warn "
            "only; the detector's result is still consumed. No score penalty."
        ),
        max_penalty=0,
        formula="0 (informational only)",
        category="meta",
    ),
}
