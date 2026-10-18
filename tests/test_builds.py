"""Tests for brickfolio.builds against a fixture catalog built by the importer.

The database under test is built exactly the way production builds it —
``import_catalog`` over the hand-written Rebrickable CSVs in
``tests/fixtures/rebrickable`` — so these tests also catch importer or
schema drift that changes what a "build" looks like in SQLite.

Extra rows some tests need (a second inventory version, a set without an
inventory, a NULL num_parts) are written as tiny CSVs into ``tmp_path`` and
pushed through the same importer: no test touches the network or the shared
fixture files, and no raw SQL bypasses the storage layer.

The fixture catalog (numbers trace to the CSVs):
- 5 sets — 10497-1 Galaxy Explorer, 60290-1 Tow Truck, 60292-1 Burger Truck,
  60307-1 Ferry, 75192-1 Millennium Falcon — each with exactly one
  version-1 inventory.
- inventory 1 (60292-1) holds two regular lines plus one spare;
  inventory 5 (75192-1) holds one regular line plus one spare.
"""

from pathlib import Path

import pytest

from brickfolio.builds import (
    build_parts,
    count_builds,
    get_build,
    iter_builds,
    search_builds,
)
from brickfolio.db import Database
from brickfolio.importer import ImportReport, import_catalog
from brickfolio.models import Build, BuildPart

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rebrickable"

# All fixture sets, in the build_num (set_num) order iter_builds promises.
EXPECTED_BUILD_NUMS = ["10497-1", "60290-1", "60292-1", "60307-1", "75192-1"]


@pytest.fixture()
def catalog_db(tmp_path):
    """A throwaway database loaded from the fixture Rebrickable CSVs."""
    database = Database(tmp_path / "catalog.db")
    import_catalog(database, FIXTURE_DIR)
    yield database
    database.close()


def _import_extra(db, tmp_path, files) -> ImportReport:
    """Import extra CSV files (name -> content) through the real importer.

    WHY the importer and not direct SQL: PROMPTS.md says the test db is
    "built by the Prompt-2 importer" — test setup that bypasses it could
    land rows with a different shape than production writes.

    Returns the ImportReport so tests can assert the rows really landed
    (otherwise "get_build is None" assertions could pass vacuously).
    """
    extra_dir = tmp_path / "extra-catalog"
    extra_dir.mkdir(exist_ok=True)
    for name, content in files.items():
        (extra_dir / name).write_text(content, encoding="utf-8")
    return import_catalog(db, extra_dir)


# ---- count_builds --------------------------------------------------------


def test_count_builds_counts_every_set_with_an_inventory(catalog_db):
    assert count_builds(catalog_db) == len(EXPECTED_BUILD_NUMS)


def test_count_builds_on_empty_catalog(db):
    assert count_builds(db) == 0


# ---- iter_builds ---------------------------------------------------------


def test_iter_builds_yields_every_build_in_build_num_order(catalog_db):
    builds = list(iter_builds(catalog_db))
    assert [b.build_num for b in builds] == EXPECTED_BUILD_NUMS
    # Spot-check one whole Build, not just the key.
    assert builds[-1] == Build("75192-1", "Millennium Falcon", 7541, "set")


def test_iter_builds_is_a_lazy_iterator(db):
    # The contract is Iterator[Build], not a materialized list — the real
    # catalog holds tens of thousands of sets.
    it = iter_builds(db)
    assert iter(it) is it


def test_iter_builds_on_empty_catalog(db):
    assert list(iter_builds(db)) == []


# ---- get_build -------------------------------------------------------------


def test_get_build_returns_the_build(catalog_db):
    assert get_build(catalog_db, "10497-1") == Build(
        "10497-1", "Galaxy Explorer", 1254, "set"
    )


def test_get_build_unknown_num_is_none(catalog_db):
    assert get_build(catalog_db, "99999-1") is None
    # Exact match only — "75192" is not the build "75192-1".
    assert get_build(catalog_db, "75192") is None


def test_get_build_on_empty_catalog(db):
    assert get_build(db, "75192-1") is None


# ---- build_parts ----------------------------------------------------------


def test_build_parts_includes_spares_in_stable_order(catalog_db):
    # 60292-1 (Burger Truck): two regular lines plus a spare (3020 in red).
    assert build_parts(catalog_db, "60292-1") == [
        BuildPart("3001", "4", 2, False),
        BuildPart("3003", "0", 4, False),
        BuildPart("3020", "4", 1, True),
    ]
    # 75192-1 (Millennium Falcon): one regular line plus a spare.
    assert build_parts(catalog_db, "75192-1") == [
        BuildPart("3001", "0", 1, False),
        BuildPart("3941", "36", 2, True),
    ]


def test_build_parts_single_line_build(catalog_db):
    assert build_parts(catalog_db, "60290-1") == [
        BuildPart("3003", "14", 3, False)
    ]


def test_build_parts_unknown_build_is_empty(catalog_db, db):
    assert build_parts(catalog_db, "no-such-set") == []
    assert build_parts(db, "60292-1") == []


# ---- build-ness edge cases (extra rows via the importer) ------------------


def test_set_without_inventory_is_not_a_build(catalog_db, tmp_path):
    report = _import_extra(
        catalog_db,
        tmp_path,
        {"sets.csv": "set_num,name,year,theme_id,num_parts\n"
                     "99999-1,Phantom,2020,158,10\n"},
    )
    assert report.rows == 1  # the set landed; it simply has no inventory

    assert get_build(catalog_db, "99999-1") is None
    assert count_builds(catalog_db) == len(EXPECTED_BUILD_NUMS)
    assert search_builds(catalog_db, "Phantom") == []


def test_latest_inventory_version_wins(catalog_db, tmp_path):
    # Rebrickable re-issues parts lists as new inventory "versions";
    # build_parts must read the newest one only.
    report = _import_extra(
        catalog_db,
        tmp_path,
        {
            "inventories.csv": "id,set_num,version\n6,60292-1,2\n",
            "inventory_parts.csv": (
                "inventory_id,part_num,color_id,quantity,is_spare\n"
                "6,3001,4,5,f\n"
            ),
        },
    )
    assert report.rows == 2

    # Only the version-2 line comes back; version 1 is superseded.
    assert build_parts(catalog_db, "60292-1") == [BuildPart("3001", "4", 5, False)]
    # Still one build for that set, not two.
    assert count_builds(catalog_db) == len(EXPECTED_BUILD_NUMS)


def test_null_num_parts_becomes_zero_and_parts_may_be_empty(catalog_db, tmp_path):
    report = _import_extra(
        catalog_db,
        tmp_path,
        {
            "sets.csv": (
                "set_num,name,year,theme_id,num_parts\n"
                "88888-1,No Parts Count,2020,158,\n"
            ),
            "inventories.csv": "id,set_num,version\n7,88888-1,1\n",
        },
    )
    assert report.rows == 2

    # Build.num_parts is an int by contract; the NULL imports as 0.
    assert get_build(catalog_db, "88888-1") == Build(
        "88888-1", "No Parts Count", 0, "set"
    )
    # An inventory with no part lines is still a build — just an empty one.
    assert build_parts(catalog_db, "88888-1") == []


# ---- search_builds --------------------------------------------------------


def test_search_matches_name_case_insensitively(catalog_db):
    assert [b.build_num for b in search_builds(catalog_db, "truck")] == [
        "60290-1",
        "60292-1",
    ]
    assert search_builds(catalog_db, "FALCON") == [
        Build("75192-1", "Millennium Falcon", 7541, "set")
    ]


def test_search_matches_set_num(catalog_db):
    assert [b.build_num for b in search_builds(catalog_db, "75192")] == ["75192-1"]
    assert [b.build_num for b in search_builds(catalog_db, "Explorer")] == [
        "10497-1"
    ]


def test_search_no_match(catalog_db):
    assert search_builds(catalog_db, "castle") == []


def test_search_on_empty_catalog(db):
    assert search_builds(db, "truck") == []


def test_search_limit(catalog_db):
    # "60" hits three set numbers: 60290-1, 60292-1, 60307-1.
    assert len(search_builds(catalog_db, "60")) == 3
    assert len(search_builds(catalog_db, "60", limit=2)) == 2
    assert search_builds(catalog_db, "60", limit=0) == []


def test_search_escapes_like_wildcards(catalog_db):
    # "_" would wildcard-match "60292-1"; escaped it is literal junk.
    assert search_builds(catalog_db, "60_92-1") == []
    # "100%" must not act as "100 followed by anything".
    assert search_builds(catalog_db, "100%") == []