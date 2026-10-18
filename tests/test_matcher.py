"""Tests for brickfolio.matcher — the Prompt-4 pure coverage engine.

Everything here is in-memory frozen dataclasses: no db, no disk fixtures,
no network. Each test pins a sentence of the Prompt-4 spec (or a documented
convention the report layer in Prompt 5 will rely on).
"""

import dataclasses

import pytest

from brickfolio.matcher import (
    MODES,
    MissingPart,
    MatchResult,
    RankedBuild,
    match_build,
    rank_builds,
)
from brickfolio.models import Build, BuildPart, InventoryItem


def build(num: str, name: str) -> Build:
    """Tiny Build factory — num_parts/source defaults are irrelevant here."""
    return Build(build_num=num, name=name)


# --- exact mode --------------------------------------------------------------


def test_exact_full_coverage():
    inventory = [InventoryItem("3001", "4", 2), InventoryItem("3002", "0", 1)]
    build_parts = [BuildPart("3001", "4", 2), BuildPart("3002", "0", 1)]
    result = match_build(inventory, build_parts, "exact")
    assert result.coverage_pct == 100.0
    assert result.required_parts == 2
    assert result.covered_parts == 2
    assert result.missing == []
    assert result.color_substituted is False


def test_exact_mode_rejects_wrong_color():
    # Five red 3001 in stock, but the build wants black: 0 %, no substitution.
    inventory = [InventoryItem("3001", "4", 5)]
    build_parts = [BuildPart("3001", "0", 1)]
    result = match_build(inventory, build_parts, "exact")
    assert result.coverage_pct == 0.0
    assert result.missing == [MissingPart("3001", "0", needed=1, have=0)]
    assert result.color_substituted is False


def test_missing_quantity_math():
    # The quantity gap lands in MissingPart: needed vs have.
    inventory = [InventoryItem("3001", "4", 3)]
    build_parts = [BuildPart("3001", "4", 5)]
    result = match_build(inventory, build_parts, "exact")
    assert result.coverage_pct == 0.0
    assert result.covered_parts == 0
    assert result.missing == [
        MissingPart(part_num="3001", color_id="4", needed=5, have=3)
    ]


def test_coverage_counts_requirement_lines():
    # One line fully covered, one short -> 50 % (a per-piece ratio would
    # give 4/8; this pins the per-line definition from the docstring).
    inventory = [InventoryItem("3001", "4", 2), InventoryItem("3002", "0", 2)]
    build_parts = [BuildPart("3001", "4", 2), BuildPart("3002", "0", 6)]
    result = match_build(inventory, build_parts, "exact")
    assert result.coverage_pct == 50.0
    assert result.required_parts == 2
    assert result.covered_parts == 1


def test_coverage_pct_rounds_to_two_decimals():
    inventory = [InventoryItem("3001", "4", 1)]
    build_parts = [
        BuildPart("3001", "4", 1),
        BuildPart("3002", "0", 1),
        BuildPart("3003", "2", 1),
    ]
    result = match_build(inventory, build_parts, "exact")
    assert result.coverage_pct == 33.33


# --- shape mode --------------------------------------------------------------


def test_shape_mode_substitutes_color():
    # Only red in stock; the build wants black -> still 100 %, but flagged.
    inventory = [InventoryItem("3001", "4", 2)]
    build_parts = [BuildPart("3001", "0", 2)]
    result = match_build(inventory, build_parts, "shape")
    assert result.coverage_pct == 100.0
    assert result.missing == []
    assert result.color_substituted is True


def test_shape_mode_exact_stock_is_not_substitution():
    inventory = [InventoryItem("3001", "0", 2)]
    build_parts = [BuildPart("3001", "0", 2)]
    result = match_build(inventory, build_parts, "shape")
    assert result.coverage_pct == 100.0
    assert result.color_substituted is False


def test_shape_mode_partial_substitution_still_covers():
    # Need 5 black; 3 black + 2 red -> shape mode pools all colors: covered.
    inventory = [InventoryItem("3001", "0", 3), InventoryItem("3001", "4", 2)]
    build_parts = [BuildPart("3001", "0", 5)]
    result = match_build(inventory, build_parts, "shape")
    assert result.coverage_pct == 100.0
    assert result.color_substituted is True


def test_shape_mode_missing_reports_pooled_have():
    # Need 5 black; 1 black + 2 red -> still short: the missing line reports
    # have=3 (all colors pooled), not the 1 exact-color piece.
    inventory = [InventoryItem("3001", "0", 1), InventoryItem("3001", "4", 2)]
    build_parts = [BuildPart("3001", "0", 5)]
    result = match_build(inventory, build_parts, "shape")
    assert result.coverage_pct == 0.0
    assert result.missing == [MissingPart("3001", "0", needed=5, have=3)]
    # Nothing was fulfilled, so nothing was substituted.
    assert result.color_substituted is False


def test_shape_mode_flag_or_across_lines():
    # Line 1 covered via red (substitution), line 2 short -> 50 %, flagged.
    inventory = [InventoryItem("3001", "4", 2), InventoryItem("3002", "0", 1)]
    build_parts = [BuildPart("3001", "0", 2), BuildPart("3002", "0", 5)]
    result = match_build(inventory, build_parts, "shape")
    assert result.coverage_pct == 50.0
    assert result.color_substituted is True
    assert result.missing == [MissingPart("3002", "0", needed=5, have=1)]


# --- spares ------------------------------------------------------------------


def test_spares_excluded_from_requirements():
    inventory = [InventoryItem("3001", "4", 2)]
    build_parts = [BuildPart("3001", "4", 2), BuildPart("3024", "0", 1, True)]
    result = match_build(inventory, build_parts, "exact")
    assert result.required_parts == 1
    assert result.coverage_pct == 100.0
    assert result.missing == []


def test_spare_only_build_is_vacuously_complete():
    result = match_build([], [BuildPart("3024", "0", 1, True)], "exact")
    assert result.coverage_pct == 100.0
    assert result.required_parts == 0
    assert result.missing == []


# --- aggregation -------------------------------------------------------------


def test_duplicate_inventory_lines_aggregate():
    # Same key added twice (e.g. two scan sessions) -> one pool of 5.
    inventory = [InventoryItem("3001", "4", 2), InventoryItem("3001", "4", 3)]
    build_parts = [BuildPart("3001", "4", 5)]
    result = match_build(inventory, build_parts, "exact")
    assert result.coverage_pct == 100.0


def test_duplicate_build_lines_aggregate():
    inventory = [InventoryItem("3001", "4", 3)]
    build_parts = [BuildPart("3001", "4", 2), BuildPart("3001", "4", 2)]
    result = match_build(inventory, build_parts, "exact")
    assert result.required_parts == 1
    assert result.missing == [MissingPart("3001", "4", needed=4, have=3)]


def test_match_build_accepts_any_iterable():
    # Spec says Iterable — generators must work for both arguments.
    inventory = (item for item in [InventoryItem("3001", "4", 1)])
    build_parts = (bp for bp in [BuildPart("3001", "4", 1)])
    result = match_build(inventory, build_parts, "exact")
    assert result.coverage_pct == 100.0


# --- edge cases ---------------------------------------------------------------


def test_empty_inventory_is_zero_percent():
    result = match_build([], [BuildPart("3001", "4", 2)], "exact")
    assert result.coverage_pct == 0.0
    assert result.missing == [MissingPart("3001", "4", needed=2, have=0)]


def test_empty_build_parts_is_vacuously_complete():
    result = match_build([InventoryItem("3001", "4", 1)], [], "exact")
    assert result == MatchResult(
        coverage_pct=100.0,
        required_parts=0,
        covered_parts=0,
        missing=[],
        color_substituted=False,
    )


def test_invalid_mode_raises_value_error():
    assert MODES == ("exact", "shape")
    with pytest.raises(ValueError):
        match_build([], [], "colour")
    with pytest.raises(ValueError):
        rank_builds([], [], "loose")


def test_missing_is_sorted_deterministically():
    build_parts = [
        BuildPart("3002", "4", 1),
        BuildPart("3001", "0", 1),
        BuildPart("3001", "4", 1),
    ]
    result = match_build([], build_parts, "exact")
    assert [(m.part_num, m.color_id) for m in result.missing] == [
        ("3001", "0"),
        ("3001", "4"),
        ("3002", "4"),
    ]


def test_result_dataclasses_are_frozen():
    result = match_build(
        [InventoryItem("3001", "4", 1)], [BuildPart("3001", "4", 2)], "exact"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.coverage_pct = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.missing[0].needed = 0


# --- ranking -----------------------------------------------------------------


def test_rank_builds_orders_by_coverage_then_name():
    inventory = [InventoryItem("3001", "4", 2)]
    builds_with_parts = [
        (build("75192-1", "Millennium Falcon"), [BuildPart("3001", "4", 2)]),
        # Two 50 % builds: the name decides between them.
        (
            build("hh-1", "Hutt Hut"),
            [BuildPart("3001", "4", 1), BuildPart("3002", "0", 1)],
        ),
        (
            build("gh-1", "Gungan Hut"),
            [BuildPart("3001", "4", 1), BuildPart("3003", "0", 1)],
        ),
        (build("car-1", "Tiny Car"), [BuildPart("9999", "0", 1)]),
    ]
    ranked = rank_builds(inventory, builds_with_parts, "exact")
    assert [rb.build.name for rb in ranked] == [
        "Millennium Falcon",
        "Gungan Hut",
        "Hutt Hut",
        "Tiny Car",
    ]
    assert all(isinstance(rb, RankedBuild) for rb in ranked)
    assert ranked[0].match.coverage_pct == 100.0
    assert ranked[-1].match.coverage_pct == 0.0


def test_rank_builds_top_truncates():
    inventory = [InventoryItem("3001", "4", 2)]
    builds_with_parts = [
        (build("a-1", "A"), [BuildPart("3001", "4", 2)]),
        (build("b-1", "B"), [BuildPart("3001", "4", 1)]),
        (build("c-1", "C"), [BuildPart("3002", "0", 1)]),
    ]
    # top=None -> everything; top too large -> everything as well.
    assert len(rank_builds(inventory, builds_with_parts, "exact")) == 3
    assert len(rank_builds(inventory, builds_with_parts, "exact", top=99)) == 3
    assert [
        rb.build.name for rb in rank_builds(inventory, builds_with_parts, "exact", top=2)
    ] == ["A", "B"]
    # Negative top means "no results", not "all but N".
    assert rank_builds(inventory, builds_with_parts, "exact", top=0) == []
    assert rank_builds(inventory, builds_with_parts, "exact", top=-1) == []


def test_rank_builds_empty_input():
    assert rank_builds([], [], "exact") == []


def test_rank_builds_accepts_generator_input():
    inventory = [InventoryItem("3001", "4", 2)]
    builds_with_parts = iter([(build("x-1", "X"), [BuildPart("3001", "4", 2)])])
    ranked = rank_builds(inventory, builds_with_parts, "shape")
    assert [rb.build.build_num for rb in ranked] == ["x-1"]


def test_rank_builds_shape_mode_lifts_coverage():
    # The black requirement fails in exact mode but is covered via the red
    # stock in shape mode — and the ranking reflects the swap.
    inventory = [InventoryItem("3001", "4", 2)]
    builds_with_parts = [
        (build("nb-1", "Needs Black"), [BuildPart("3001", "0", 2)]),
        (build("no-1", "Needs Other"), [BuildPart("3002", "0", 2)]),
    ]
    exact_ranked = rank_builds(inventory, builds_with_parts, "exact")
    assert [rb.match.coverage_pct for rb in exact_ranked] == [0.0, 0.0]
    # Tie at 0 % -> name order ("Needs Black" < "Needs Other").
    assert [rb.build.name for rb in exact_ranked] == ["Needs Black", "Needs Other"]

    shape_ranked = rank_builds(inventory, builds_with_parts, "shape")
    assert [rb.build.name for rb in shape_ranked] == ["Needs Black", "Needs Other"]
    assert shape_ranked[0].match.coverage_pct == 100.0
    assert shape_ranked[0].match.color_substituted is True
    assert shape_ranked[1].match.coverage_pct == 0.0