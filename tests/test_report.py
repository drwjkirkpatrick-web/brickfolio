"""Tests for brickfolio.report — the recommender's human-facing output.

The database under test is built exactly the way production builds it:
``import_catalog`` over the fixture Rebrickable CSVs
(tests/fixtures/rebrickable), then ``db.add_user_parts`` for the
collection — no raw SQL, no network.

Two kinds of tests live here:

- end-to-end: ``recommend_builds`` against the fixture catalog, asserting
  names, percentages, READY flags and order (these also catch drift in
  the importer, builds.py or matcher.py that changes what a recommendation
  looks like);
- pure rendering: hand-built :class:`RankedBuild` values, where a database
  would only obscure the string math under test (bar length, verbose
  truncation).

Fixture catalog map (traceable to the CSVs):

- 60292-1 Burger Truck — requires 2x 3001/4 + 4x 3003/0, spare 3020/4;
- 60290-1 Tow Truck — requires 3x 3003/14;
- 60307-1 Ferry — requires 2x 3001/15;
- 10497-1 Galaxy Explorer — requires 6x 3004/1;
- 75192-1 Millennium Falcon — requires 1x 3001/0, spare 3941/36.

The ``PILE`` below covers Burger Truck and Tow Truck in their exact
colors; in shape mode the same stock also covers Ferry and Millennium
Falcon via color swaps of part 3001 — which is exactly that mode's point.
"""

from pathlib import Path

import pytest

from brickfolio.builds import get_build
from brickfolio.db import Database
from brickfolio.importer import ImportReport, import_catalog
from brickfolio.matcher import MatchResult, MissingPart, RankedBuild
from brickfolio.models import Build, InventoryItem
from brickfolio.report import (
    MAX_VERBOSE_MISSING,
    recommend_builds,
    render_missing_list,
    render_recommendations,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rebrickable"

# The parts pile most tests stock: exact-color coverage for Burger Truck
# and Tow Truck; shape-mode coverage (with color swaps) for Ferry and
# Millennium Falcon; nothing at all for Galaxy Explorer's 3004/1.
PILE = [("3001", "4", 2), ("3003", "0", 4), ("3003", "14", 3)]


@pytest.fixture()
def catalog_db(tmp_path):
    """A throwaway database loaded from the fixture Rebrickable CSVs."""
    database = Database(tmp_path / "catalog.db")
    import_catalog(database, FIXTURE_DIR)
    yield database
    database.close()


def _fill(db, lines):
    """Add collection lines [(part_num, color_id, qty), ...] via the
    storage API — never raw SQL."""
    db.add_user_parts([InventoryItem(part, color, qty) for part, color, qty in lines])


def _import_extra(db, tmp_path, files) -> ImportReport:
    """Import extra CSV files (name -> content) through the real importer.

    WHY the importer and not direct SQL: the test db must be built the
    same way production builds it (same helper as test_builds.py), or
    extra rows could land in a shape production never writes.
    """
    extra_dir = tmp_path / "extra-catalog"
    extra_dir.mkdir(exist_ok=True)
    for name, content in files.items():
        (extra_dir / name).write_text(content, encoding="utf-8")
    return import_catalog(db, extra_dir)


def _ranked(name, coverage_pct, missing=(), build_num="00000-1"):
    """A hand-built RankedBuild for pure rendering tests (no database).

    NOTE: only coverage_pct and missing are rendered; the count fields are
    filler that merely satisfies the MatchResult contract.
    """
    build = Build(build_num=build_num, name=name, num_parts=0, source="set")
    match = MatchResult(
        coverage_pct=coverage_pct,
        required_parts=len(missing),
        covered_parts=0,
        missing=list(missing),
        color_substituted=False,
    )
    return RankedBuild(build=build, match=match)


# ---- recommend_builds (end to end against the fixture catalog) ------------


def test_recommend_default_is_shape_mode_and_ranks_swaps_above_gaps(catalog_db):
    _fill(catalog_db, PILE)
    ranked = recommend_builds(catalog_db)  # defaults: mode="shape", top=10

    # Four builds reach 100 % in shape mode (two of them only by swapping
    # the color of part 3001); ties break by name. Galaxy Explorer stays
    # at 0 % — its 3004/1 is nowhere in the pile, in any color.
    assert [rb.build.name for rb in ranked] == [
        "Burger Truck",
        "Ferry",
        "Millennium Falcon",
        "Tow Truck",
        "Galaxy Explorer",
    ]
    assert [rb.match.coverage_pct for rb in ranked] == [100.0, 100.0, 100.0, 100.0, 0.0]

    by_name = {rb.build.name: rb for rb in ranked}
    # Ferry (3001/15) and Millennium Falcon (3001/0) were fulfilled from
    # the red 3001 stock -> the matcher flagged the color substitution.
    assert by_name["Ferry"].match.color_substituted is True
    assert by_name["Millennium Falcon"].match.color_substituted is True
    assert by_name["Burger Truck"].match.color_substituted is False


def test_recommend_exact_mode_demands_exact_colors(catalog_db):
    _fill(catalog_db, PILE)
    ranked = recommend_builds(catalog_db, mode="exact")

    # Only the two exactly-covered builds survive; the 0 % ties fall back
    # to name order (Ferry < Galaxy Explorer < Millennium Falcon).
    assert [rb.build.name for rb in ranked] == [
        "Burger Truck",
        "Tow Truck",
        "Ferry",
        "Galaxy Explorer",
        "Millennium Falcon",
    ]
    assert [rb.match.coverage_pct for rb in ranked] == [100.0, 100.0, 0.0, 0.0, 0.0]


def test_recommend_returns_the_real_catalog_builds(catalog_db):
    _fill(catalog_db, PILE)
    ranked = recommend_builds(catalog_db, mode="exact")

    # The join must carry actual catalog Builds, not lookalikes.
    by_num = {rb.build.build_num: rb for rb in ranked}
    assert set(by_num) == {"10497-1", "60290-1", "60292-1", "60307-1", "75192-1"}
    assert by_num["60292-1"].build == get_build(catalog_db, "60292-1")


def test_recommend_top_truncates_the_ranking(catalog_db):
    _fill(catalog_db, PILE)
    top2 = recommend_builds(catalog_db, top=2)
    assert len(top2) == 2
    assert top2[0].build.name == "Burger Truck"  # best coverage first
    assert recommend_builds(catalog_db, top=0) == []
    # None keeps the whole ranking; a top past the catalog size does too.
    assert len(recommend_builds(catalog_db, top=None)) == 5
    assert len(recommend_builds(catalog_db, top=99)) == 5


def test_recommend_unknown_mode_raises(catalog_db):
    # ValueError comes from the matcher (fail-fast) before any catalog
    # query runs — recommend_builds just lets it bubble.
    with pytest.raises(ValueError, match="unknown match mode"):
        recommend_builds(catalog_db, mode="colour")


def test_recommend_on_empty_catalog(db):
    ranked = recommend_builds(db)
    assert ranked == []
    # The renderer turns "no builds" into a friendly line, not silence.
    assert render_recommendations(ranked) == "No builds to recommend."


def test_recommend_with_empty_inventory_covers_nothing(catalog_db):
    ranked = recommend_builds(catalog_db)  # no user parts added at all
    assert [rb.build.name for rb in ranked] == [
        "Burger Truck",
        "Ferry",
        "Galaxy Explorer",
        "Millennium Falcon",
        "Tow Truck",
    ]
    assert all(rb.match.coverage_pct == 0.0 for rb in ranked)


def test_vacuous_build_ranks_ready(catalog_db, tmp_path):
    # A build whose inventory has no part lines is vacuously complete
    # (matcher contract) — the recommender must surface it at 100 %.
    report = _import_extra(
        catalog_db,
        tmp_path,
        {
            "sets.csv": (
                "set_num,name,year,theme_id,num_parts\n"
                "88888-1,Parts Unknown,2020,158,\n"
            ),
            "inventories.csv": "id,set_num,version\n8,88888-1,1\n",
        },
    )
    assert report.rows == 2  # the set and its (empty) inventory landed

    _fill(catalog_db, PILE)
    ranked = recommend_builds(catalog_db, mode="exact")
    mystery = next(rb for rb in ranked if rb.build.name == "Parts Unknown")
    assert mystery.match.coverage_pct == 100.0
    assert mystery.match.missing == []
    # ...and it renders like any other READY build, full bar included.
    assert render_recommendations([mystery]) == (
        "Parts Unknown  ██████████ 100% READY"
    )


def test_partial_coverage_math(catalog_db):
    # Half of Burger Truck's two requirement lines: 1/2 = 50 %.
    _fill(catalog_db, [("3001", "4", 2)])
    ranked = recommend_builds(catalog_db, mode="exact")
    burger = next(rb for rb in ranked if rb.build.name == "Burger Truck")

    assert burger.match.coverage_pct == 50.0
    assert burger.match.covered_parts == 1
    assert burger.match.required_parts == 2
    assert burger.match.missing == [MissingPart("3003", "0", 4, 0)]


# ---- render_recommendations -------------------------------------------------


def test_render_recommendations_shape_report(catalog_db):
    _fill(catalog_db, PILE)
    out = render_recommendations(recommend_builds(catalog_db))
    assert out == (
        "Burger Truck  ██████████ 100% READY\n"
        "Ferry  ██████████ 100% READY\n"
        "Millennium Falcon  ██████████ 100% READY\n"
        "Tow Truck  ██████████ 100% READY\n"
        "Galaxy Explorer  ░░░░░░░░░░ 0%"
    )
    assert out.count("READY") == 4  # exactly the four fully covered builds


def test_render_empty_recommendations():
    assert render_recommendations([]) == "No builds to recommend."


@pytest.mark.parametrize(
    "pct, bar",
    [
        (0.0, "░░░░░░░░░░"),
        (5.0, "░░░░░░░░░░"),
        (9.99, "░░░░░░░░░░"),
        (10.0, "█░░░░░░░░░"),
        (33.33, "███░░░░░░░"),
        (50.0, "█████░░░░░"),
        (66.67, "██████░░░░"),
        (72.0, "███████░░░"),
        (99.99, "█████████░"),
        (100.0, "██████████"),
    ],
)
def test_bar_length_math(pct, bar):
    # NOTE: PROMPTS.md's illustrative example shows `██████░░░░ 72%`
    # (6 blocks), but no consistent rule that fills all 10 blocks at
    # 100 % also yields 6 at 72 — report.py floors at pct // 10, so 72 %
    # renders 7 blocks. Deviation flagged to the parent for arbitration.
    out = render_recommendations([_ranked("Whatever", pct)])
    expected = f"Whatever  {bar} {pct:g}%"
    if pct == 100:
        expected += " READY"  # full coverage is the only case that flags
    assert out == expected


def test_ready_flag_only_at_full_coverage():
    assert " READY" in render_recommendations([_ranked("A", 100.0)])
    assert " READY" not in render_recommendations([_ranked("A", 99.99)])
    assert " READY" not in render_recommendations([_ranked("A", 0.0)])


def test_verbose_shows_top_missing_lines_and_truncates():
    missing = [
        MissingPart(f"300{i}", "4", i + 1, 0)
        for i in range(MAX_VERBOSE_MISSING + 2)
    ]
    hidden = len(missing) - MAX_VERBOSE_MISSING

    out = render_recommendations([_ranked("Hoard", 0.0, missing)], verbose=True)
    assert out.splitlines() == [
        "Hoard  ░░░░░░░░░░ 0%",
        "    3000 (color 4): need 1, have 0",
        "    3001 (color 4): need 2, have 0",
        "    3002 (color 4): need 3, have 0",
        f"    ... and {hidden} more missing lines",
    ]
    # The folded-away lines (3003, 3004) must not leak into the report.
    assert "    3003" not in out
    assert "    3004" not in out

    # Without verbose, the same build is a single line — missing detail
    # is the shopping list's job, not the overview's.
    plain = render_recommendations([_ranked("Hoard", 0.0, missing)])
    assert plain == "Hoard  ░░░░░░░░░░ 0%"


def test_verbose_ready_build_stays_one_line():
    # READY builds have nothing to itemize, even in verbose mode.
    out = render_recommendations([_ranked("Done", 100.0)], verbose=True)
    assert out == "Done  ██████████ 100% READY"


def test_verbose_missing_lines_from_catalog(catalog_db):
    _fill(catalog_db, PILE)
    ranked = recommend_builds(catalog_db, mode="exact")
    ferry = next(rb for rb in ranked if rb.build.name == "Ferry")

    out = render_recommendations([ferry], verbose=True)
    assert out == "Ferry  ░░░░░░░░░░ 0%\n    3001 (color 15): need 2, have 0"


# ---- render_missing_list ------------------------------------------------------


def test_render_missing_list_full_shopping_list(catalog_db):
    # Empty pile: Burger Truck's whole requirement set is the list, in the
    # matcher's stable (part_num, color_id) order. The spare 3020/4 must
    # NOT appear — spares are optional, never shopped for.
    ranked = recommend_builds(catalog_db, mode="exact")
    burger = next(rb for rb in ranked if rb.build.name == "Burger Truck")

    assert render_missing_list(burger) == (
        "Shopping list for Burger Truck (0% covered, 2 missing lines):\n"
        "  part  color  needed  have\n"
        "  3001  4      2       0\n"
        "  3003  0      4       0"
    )


def test_render_missing_list_partial(catalog_db):
    # Half a Burger Truck: one fully covered line, one missing line.
    _fill(catalog_db, [("3001", "4", 2)])
    ranked = recommend_builds(catalog_db, mode="exact")
    burger = next(rb for rb in ranked if rb.build.name == "Burger Truck")

    assert render_missing_list(burger) == (
        "Shopping list for Burger Truck (50% covered, 1 missing line):\n"
        "  part  color  needed  have\n"
        "  3003  0      4       0"
    )


def test_render_missing_list_ready_build(catalog_db):
    _fill(catalog_db, PILE)
    ranked = recommend_builds(catalog_db)  # shape: Burger Truck fully covered
    burger = next(rb for rb in ranked if rb.build.name == "Burger Truck")

    assert render_missing_list(burger) == "Burger Truck: nothing missing — READY at 100%."