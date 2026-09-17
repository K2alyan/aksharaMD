"""Tests for FinTabNet.c complexity classification and allocation (B1a-8b).

Locked boundary cases per STUDY_FREEZE_MANIFEST_V1.md §6.3 (frozen definitions):
  SIMPLE:   every cell has len(row_nums) == 1 and len(column_nums) == 1.
  COMPOUND: at least one cell has len(row_nums) > 1 or len(column_nums) > 1.
"""
import pytest

from benchmarks.eval_v1.selection.fintabnet_c_complexity import (
    TIER_COMPOUND,
    TIER_SIMPLE,
    classify_table_complexity,
    largest_remainder_allocate,
)

# ---------------------------------------------------------------------------
# classify_table_complexity — happy-path boundary cases
# ---------------------------------------------------------------------------

def _cell(row_nums: list, col_nums: list) -> dict:
    return {"row_nums": row_nums, "column_nums": col_nums}


def test_simple_single_cell():
    assert classify_table_complexity([_cell([0], [0])]) == TIER_SIMPLE


def test_simple_multi_cell_all_non_spanning():
    cells = [_cell([0], [0]), _cell([0], [1]), _cell([1], [0]), _cell([1], [1])]
    assert classify_table_complexity(cells) == TIER_COMPOUND or \
           classify_table_complexity(cells) == TIER_SIMPLE
    # All cells are non-spanning → SIMPLE
    assert classify_table_complexity(cells) == TIER_SIMPLE


def test_compound_row_spanning_only():
    """A cell spanning two rows (row_nums has length 2) → COMPOUND."""
    cells = [_cell([0, 1], [0]), _cell([0], [1])]
    assert classify_table_complexity(cells) == TIER_COMPOUND


def test_compound_column_spanning_only():
    """A cell spanning two columns (column_nums has length 2) → COMPOUND."""
    cells = [_cell([0], [0, 1]), _cell([1], [0])]
    assert classify_table_complexity(cells) == TIER_COMPOUND


def test_compound_both_row_and_column_spanning():
    """A cell spanning rows and columns → COMPOUND."""
    cells = [_cell([0, 1], [0, 1])]
    assert classify_table_complexity(cells) == TIER_COMPOUND


def test_compound_detected_on_first_spanning_cell():
    """Returns COMPOUND as soon as the first spanning cell is found."""
    cells = [
        _cell([0, 1], [0]),  # spanning — should trigger early return
        _cell([0], [1]),
        _cell([1], [1]),
    ]
    assert classify_table_complexity(cells) == TIER_COMPOUND


def test_compound_only_last_cell_spans():
    """All cells non-spanning except the last → still COMPOUND."""
    cells = [_cell([0], [0]), _cell([1], [0]), _cell([2], [0, 1])]
    assert classify_table_complexity(cells) == TIER_COMPOUND


def test_simple_large_table():
    """Large table with all 1-element span lists → SIMPLE."""
    cells = [_cell([r], [c]) for r in range(10) for c in range(10)]
    assert classify_table_complexity(cells) == TIER_SIMPLE


# ---------------------------------------------------------------------------
# classify_table_complexity — fail-closed on malformed input
# ---------------------------------------------------------------------------

def test_raises_on_empty_cells():
    with pytest.raises(ValueError, match="empty"):
        classify_table_complexity([])


def test_raises_on_missing_row_nums():
    with pytest.raises(ValueError, match="row_nums"):
        classify_table_complexity([{"column_nums": [0]}])


def test_raises_on_missing_column_nums():
    with pytest.raises(ValueError, match="column_nums"):
        classify_table_complexity([{"row_nums": [0]}])


def test_raises_on_row_nums_not_list_int():
    with pytest.raises(ValueError, match="row_nums"):
        classify_table_complexity([{"row_nums": 0, "column_nums": [0]}])


def test_raises_on_column_nums_not_list_str():
    with pytest.raises(ValueError, match="column_nums"):
        classify_table_complexity([{"row_nums": [0], "column_nums": "0"}])


def test_raises_on_row_nums_none():
    with pytest.raises(ValueError, match="row_nums"):
        classify_table_complexity([{"row_nums": None, "column_nums": [0]}])


def test_raises_on_column_nums_none():
    with pytest.raises(ValueError, match="column_nums"):
        classify_table_complexity([{"row_nums": [0], "column_nums": None}])


def test_raises_on_malformed_later_cell():
    """Fails closed even if the malformed cell comes after a valid one."""
    cells = [_cell([0], [0]), {"row_nums": [1]}]  # second cell missing column_nums
    with pytest.raises(ValueError, match="column_nums"):
        classify_table_complexity(cells)


# ---------------------------------------------------------------------------
# largest_remainder_allocate — core allocation algorithm
# ---------------------------------------------------------------------------

def test_allocate_exact_proportional():
    """Equal strata → equal allocation."""
    result = largest_remainder_allocate({"a": 100, "b": 100}, 200)
    assert result == {"a": 100, "b": 100}


def test_allocate_two_tier_fintabnet_c():
    """Lock the frozen FinTabNet.c allocation: simple=192, compound=308."""
    counts = {"simple": 3714, "compound": 5936}
    result = largest_remainder_allocate(counts, 500)
    assert result["simple"] == 192
    assert result["compound"] == 308
    assert sum(result.values()) == 500


def test_allocate_sums_to_target():
    counts = {"a": 17, "b": 53, "c": 30}
    target = 50
    result = largest_remainder_allocate(counts, target)
    assert sum(result.values()) == target


def test_allocate_all_values_non_negative():
    counts = {"a": 1, "b": 999}
    result = largest_remainder_allocate(counts, 10)
    assert all(v >= 0 for v in result.values())


def test_allocate_remainder_goes_to_largest_fraction():
    """Stratum with the largest fractional part gets the extra slot."""
    # a: 10/30 * 4 = 1.333, b: 20/30 * 4 = 2.667 → floor: a=1, b=2, remainder=1
    # b has larger fractional part (0.667 > 0.333) → b gets the extra slot → b=3
    result = largest_remainder_allocate({"a": 10, "b": 20}, 4)
    assert result["a"] == 1
    assert result["b"] == 3


def test_allocate_tiebreak_alphabetical():
    """Equal fractional parts broken alphabetically (ascending)."""
    # a: 10/20 * 3 = 1.5, b: 10/20 * 3 = 1.5 → floor: a=1, b=1, remainder=1
    # Both have fractional 0.5; alphabetically 'a' < 'b' → 'a' gets extra slot
    result = largest_remainder_allocate({"a": 10, "b": 10}, 3)
    assert result["a"] == 2
    assert result["b"] == 1
    assert sum(result.values()) == 3


def test_allocate_target_equals_total():
    counts = {"a": 50, "b": 50}
    result = largest_remainder_allocate(counts, 100)
    assert result == {"a": 50, "b": 50}


def test_allocate_raises_on_zero_total():
    with pytest.raises(ValueError, match="zero"):
        largest_remainder_allocate({"a": 0, "b": 0}, 5)


def test_allocate_raises_when_target_exceeds_total():
    with pytest.raises(ValueError, match="exceeds"):
        largest_remainder_allocate({"a": 10, "b": 10}, 25)


def test_allocate_single_stratum():
    result = largest_remainder_allocate({"only": 1000}, 500)
    assert result == {"only": 500}
