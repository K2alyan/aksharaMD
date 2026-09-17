"""Frozen complexity classification for FinTabNet.c tables (B1a-8b).

Definitions locked in STUDY_FREEZE_MANIFEST_V1.md §6.3 (pre-execution
corpus-capability correction, 2026-09-16):

  SIMPLE:   every cell has len(row_nums) == 1 and len(column_nums) == 1.
  COMPOUND: at least one cell has len(row_nums) > 1 or len(column_nums) > 1.
  (Multi-page spanning: not measurable from FinTabNet.c V1 ground truth;
   removed from the stratification design.)

Fail-closed contract:
  - Missing or non-list ``row_nums`` / ``column_nums`` on any cell → ValueError.
  - Empty cells list → ValueError.
  - The function never silently defaults a malformed annotation to SIMPLE.
"""
from __future__ import annotations

TIER_SIMPLE = "simple"
TIER_COMPOUND = "compound"

TIERS = (TIER_SIMPLE, TIER_COMPOUND)


def classify_table_complexity(cells: list[dict]) -> str:
    """Return TIER_SIMPLE or TIER_COMPOUND for a FinTabNet.c table.

    ``cells`` is the list of cell dicts from the PDF_Annotations JSON, each
    containing at minimum ``row_nums`` (list[int]) and ``column_nums``
    (list[int]).

    Raises ValueError on malformed input rather than silently reclassifying.
    """
    if not cells:
        raise ValueError(
            "classify_table_complexity: cells list is empty; "
            "cannot classify a table with no cells"
        )
    for i, cell in enumerate(cells):
        row_nums = cell.get("row_nums")
        col_nums = cell.get("column_nums")
        if row_nums is None:
            raise ValueError(
                f"classify_table_complexity: cell {i} is missing 'row_nums'"
            )
        if col_nums is None:
            raise ValueError(
                f"classify_table_complexity: cell {i} is missing 'column_nums'"
            )
        if not isinstance(row_nums, list):
            raise ValueError(
                f"classify_table_complexity: cell {i} 'row_nums' must be a list, "
                f"got {type(row_nums).__name__}: {row_nums!r}"
            )
        if not isinstance(col_nums, list):
            raise ValueError(
                f"classify_table_complexity: cell {i} 'column_nums' must be a list, "
                f"got {type(col_nums).__name__}: {col_nums!r}"
            )
        if len(row_nums) > 1 or len(col_nums) > 1:
            return TIER_COMPOUND
    return TIER_SIMPLE


def largest_remainder_allocate(
    counts: dict[str, int],
    target: int,
) -> dict[str, int]:
    """Proportional allocation with floor + largest-remainder rounding.

    Given ``counts`` (stratum → eligible count) and a total ``target``,
    returns an integer allocation per stratum that sums to exactly ``target``.

    Algorithm (frozen):
    1. Compute expected allocation: target × count / total for each stratum.
    2. Floor each expected allocation.
    3. Assign remaining slots one-by-one to the strata with the largest
       fractional parts, breaking ties by stratum name (alphabetical).

    This produces a deterministic, reproducible allocation for any input.
    """
    total = sum(counts.values())
    if total == 0:
        raise ValueError("largest_remainder_allocate: all stratum counts are zero")
    if target > total:
        raise ValueError(
            f"largest_remainder_allocate: target {target} exceeds "
            f"total eligible {total}"
        )
    expected = {k: target * v / total for k, v in counts.items()}
    floor_alloc = {k: int(v) for k, v in expected.items()}
    remainder = target - sum(floor_alloc.values())
    frac_parts = sorted(
        counts.keys(),
        key=lambda k: (-(expected[k] - floor_alloc[k]), k),
    )
    for k in frac_parts[:remainder]:
        floor_alloc[k] += 1
    assert sum(floor_alloc.values()) == target, (
        f"allocation bug: sum={sum(floor_alloc.values())} != target={target}"
    )
    return floor_alloc


__all__ = [
    "TIER_COMPOUND",
    "TIER_SIMPLE",
    "TIERS",
    "classify_table_complexity",
    "largest_remainder_allocate",
]
