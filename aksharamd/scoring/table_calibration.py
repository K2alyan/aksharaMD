"""Table-quality signal calibration data and score-impact simulation.

Holds the locked baseline from the first parsebench corpus validation run
(2026-07-13, 45 stratified documents, 51 extracted tables). These calibration
records inform maturity-promotion decisions; no finding is promoted here —
all remain experimental until a larger, representative corpus is validated.

Calibration vocabulary
----------------------
"poor"  : grits_con < 0.5  (extracted table has significant content errors)
"good"  : grits_con >= 0.8 (extracted table matches reference well)
TP      : signal fires and table is poor
FP      : signal fires and table is good
TN      : signal does not fire and table is good
FN      : signal does not fire and table is poor

Limitations of this baseline
-----------------------------
- 51 extractable tables from a hand-curated benchmark; not a random sample
  of production documents. Defect types (fragmented cells, ragged rows) are
  rare in curated benchmarks, so most signals show 0 firings.
- grits_con measures content fidelity, not structural alignment. A table whose
  cells are in the wrong order has low grits_con but may show no structural
  signals.
- No stitched-table examples in the corpus (parsebench uses single-page PDFs),
  so TABLE_STITCHING_UNCERTAIN cannot be evaluated here.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Calibration record types ───────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalCalibration:
    """Observed statistics for a single signal against the parsebench corpus."""
    name: str
    fires_total: int        # TP + FP
    evaluated_total: int    # TP + FP + TN + FN
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float | None     # TP / (TP + FP)
    recall: float | None        # TP / (TP + FN)
    false_positive_rate: float | None  # FP / (FP + TN)
    notes: str = ""

    @property
    def fire_rate(self) -> float:
        if self.evaluated_total == 0:
            return 0.0
        return self.fires_total / self.evaluated_total


@dataclass(frozen=True)
class FindingCalibration:
    """Observed statistics for a consolidated finding against the parsebench corpus."""
    name: str
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float | None
    recall: float | None
    false_positive_rate: float | None
    maturity_decision: str  # "keep_experimental" | "promote_candidate" | "promote_stable"
    rationale: str


@dataclass(frozen=True)
class ScoreImpactSimulation:
    """Simulates score change if a finding carried a given penalty."""
    finding_name: str
    proposed_penalty: int          # e.g. -5 points
    good_docs_evaluated: int       # high-grits docs in sample
    good_docs_penalized: int       # FP: incorrectly penalized
    poor_docs_evaluated: int       # low-grits docs in sample
    poor_docs_correctly_penalized: int  # TP: correctly penalized
    false_safe_rate_delta: float   # FP / good_docs_evaluated
    detection_rate: float          # TP / poor_docs_evaluated


# ── Baseline calibration data (2026-07-13 run) ────────────────────────────────

BASELINE_RUN_DATE = "2026-07-13"
BASELINE_SAMPLES = {"good": 15, "mid": 15, "partial": 15, "seed": 42}
BASELINE_EXTRACTED = 51   # table records with data (2 docs had no tables extracted)

SIGNAL_CALIBRATIONS: list[SignalCalibration] = [
    # Signals that fired in the corpus
    SignalCalibration(
        name="table_near_bottom_margin",
        fires_total=17, evaluated_total=51,
        tp=8, fp=9, tn=24, fn=10,
        precision=0.471, recall=0.444, false_positive_rate=0.273,
        notes="High FPR; real tables near page bottom are common. "
              "Signal is only useful as part of TABLE_PAGE_FURNITURE_SUSPECTED "
              "compound logic (requires fragmentation corroboration).",
    ),
    SignalCalibration(
        name="table_near_top_margin",
        fires_total=13, evaluated_total=51,
        tp=3, fp=10, tn=23, fn=15,
        precision=0.231, recall=0.167, false_positive_rate=0.303,
        notes="High FPR (~30%); many well-formed tables appear at top of page. "
              "Only useful as part of compound logic with fragmentation signal.",
    ),
    SignalCalibration(
        name="duplicate_header_names",
        fires_total=6, evaluated_total=51,
        tp=4, fp=2, tn=31, fn=14,
        precision=0.667, recall=0.222, false_positive_rate=0.061,
        notes="Best isolated signal in corpus. FPR=6.1%. Low recall (22%) means "
              "most poor-quality tables have no duplicate headers. "
              "Drives TABLE_HEADER_UNCERTAIN finding.",
    ),
]

# Signals with 0 firings in this corpus — not calibrated, not tuned
UNCALIBRATED_SIGNALS: list[str] = [
    "avg_nonempty_cell_length",
    "duplicate_row_count",
    "empty_column_count",
    "empty_header_cells",
    "empty_row_count",
    "expected_grid_size",
    "explicit_cell_count",
    "explicit_empty_cell_count",
    "generic_header_count",
    "header_body_width_mismatch",
    "header_cell_coverage",
    "header_detection",
    "header_row_count",
    "median_cell_length",
    "missing_coordinate_count",
    "nonempty_cell_ratio",
    "numeric_only_cell_fraction",
    "numeric_only_headers",
    "punctuation_only_cell_fraction",
    "ragged_row_count",
    "repeated_header_in_body",
    "short_cell_fraction",
    "single_char_cell_fraction",
    "span_covered_coordinate_count",
    "table_bbox_available",
    "table_height_fraction",
    "table_one_column",
    "table_one_row",
    "table_width_fraction",
    "whitespace_only_cell_count",
]

FINDING_CALIBRATIONS: list[FindingCalibration] = [
    FindingCalibration(
        name="TABLE_HEADER_UNCERTAIN",
        tp=4, fp=2, tn=31, fn=14,
        precision=0.667, recall=0.222, false_positive_rate=0.061,
        maturity_decision="keep_experimental",
        rationale=(
            "Fires on 6/51 tables; precision 67%, recall 22%, FPR 6.1%. "
            "Sample too small (4 TP) to trust precision estimate. "
            "Needs 200+ evaluated tables before promotion to candidate."
        ),
    ),
    FindingCalibration(
        name="TABLE_PAGE_FURNITURE_SUSPECTED",
        tp=0, fp=0, tn=0, fn=0,
        precision=None, recall=None, false_positive_rate=None,
        maturity_decision="keep_experimental",
        rationale=(
            "Zero firings in corpus — no furniture tables sampled. "
            "Compound logic (margin AND fragmentation) successfully suppresses "
            "the noisy margin signals. Cannot evaluate precision/recall. "
            "Design is sound but unverified on real positive examples."
        ),
    ),
    FindingCalibration(
        name="TABLE_CELL_FRAGMENTATION",
        tp=0, fp=0, tn=33, fn=18,
        precision=None, recall=None, false_positive_rate=0.0,
        maturity_decision="keep_experimental",
        rationale=(
            "Zero firings in corpus. FPR=0% on 33 good-table controls. "
            "The defect (>50% single-char cells) is rare in benchmark documents. "
            "Not ready for promotion without positive examples."
        ),
    ),
    FindingCalibration(
        name="TABLE_STRUCTURE_INCOMPLETE",
        tp=0, fp=0, tn=33, fn=18,
        precision=None, recall=None, false_positive_rate=0.0,
        maturity_decision="keep_experimental",
        rationale=(
            "Zero firings in corpus. FPR=0% on good controls. "
            "Missing coordinates and ragged rows are structural defects not "
            "present in the curated benchmark. Cannot calibrate recall."
        ),
    ),
    FindingCalibration(
        name="TABLE_STITCHING_UNCERTAIN",
        tp=0, fp=0, tn=0, fn=0,
        precision=None, recall=None, false_positive_rate=None,
        maturity_decision="keep_experimental",
        rationale=(
            "Parsebench uses single-page PDFs; no stitched tables in corpus. "
            "Cannot validate. Requires production documents with cross-page tables."
        ),
    ),
]


def simulate_score_impact(
    finding_name: str,
    proposed_penalty: int,
    *,
    good_count: int = 33,
    poor_count: int = 18,
) -> ScoreImpactSimulation:
    """Simulate score impact of promoting a finding to stable with a penalty.

    Uses the baseline calibration statistics. Caller provides the good/poor
    counts to allow overriding with a larger future corpus.
    """
    cal = next((c for c in FINDING_CALIBRATIONS if c.name == finding_name), None)
    if cal is None:
        raise ValueError(f"No calibration for finding {finding_name!r}")

    good_penalized = cal.fp
    poor_penalized = cal.tp
    false_safe_delta = cal.fp / good_count if good_count > 0 else 0.0
    detection_rate = cal.tp / poor_count if poor_count > 0 else 0.0

    return ScoreImpactSimulation(
        finding_name=finding_name,
        proposed_penalty=proposed_penalty,
        good_docs_evaluated=good_count,
        good_docs_penalized=good_penalized,
        poor_docs_evaluated=poor_count,
        poor_docs_correctly_penalized=poor_penalized,
        false_safe_rate_delta=round(false_safe_delta, 4),
        detection_rate=round(detection_rate, 4),
    )


def maturity_summary() -> dict[str, str]:
    """Return {finding_name: maturity_decision} for all calibrated findings."""
    return {c.name: c.maturity_decision for c in FINDING_CALIBRATIONS}
