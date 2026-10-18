"""Tests for Prompt 2 — the Rebrickable catalog importer and read helpers.

WHY mini-CSV fixtures (tests/fixtures/rebrickable) instead of the real
multi-GB Rebrickable downloads: tests must run offline and fast. The
fixture files mirror Rebrickable's download layout exactly (same
names, same column order, same header line), just 4–8 rows each, and
contain exactly one malformed row (parts.csv: a row with a missing
field) to prove the skip-and-count path. FK violations get their own
scenario built in tmp_path so the shared fixtures stay clean.
"""

from pathlib import Path

from brickfolio.catalog import catalog_stats, get_color, get_part
from brickfolio.importer import import_catalog
from brickfolio.models import Color, Part

FIXTURES = Path(__file__).parent / "fixtures" / "rebrickable"

# Hand-counted fixture expectations. WHY hardcoded: the tests should
# *document* the fixtures, not re-derive them (re-deriving would just
# re-implement the importer and could hide the same bug twice).
EXPECTED_STATS = {
    "part_categories": 5,
    "themes": 5,
    "colors": 7,
    "parts": 6,  # 7 data rows in the fixture CSV, minus 1 malformed
    "sets": 5,
    "inventories": 5,
    "inventory_parts": 8,
    "user_parts": 0,
}
EXPECTED_ROWS = 41  # sum of the catalog tables in EXPECTED_STATS


def test_import_counts_match_fixtures(db):
    report = import_catalog(db, FIXTURES)
    assert report.files == 7
    assert report.rows == EXPECTED_ROWS
    assert report.skipped == 1  # the single malformed parts.csv row
    assert report.notes == ()
    assert catalog_stats(db) == EXPECTED_STATS


def test_import_is_idempotent(db):
    import_catalog(db, FIXTURES)
    before = catalog_stats(db)
    # WHY re-run the whole import: proves INSERT OR REPLACE leaves the
    # catalog identical even with child rows (inventories) already
    # referencing the parents (sets) being replaced.
    report = import_catalog(db, FIXTURES)
    assert catalog_stats(db) == before
    assert report.rows == EXPECTED_ROWS  # every valid row re-inserted
    assert report.skipped == 1  # the malformed row fails every time


def test_missing_file_is_noted_and_rest_skips_cleanly(db, tmp_path):
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "colors.csv").write_text(
        "id,name,rgb,is_trans\n0,Black,05131D,f\n", encoding="utf-8"
    )
    report = import_catalog(db, partial)
    assert report.files == 1
    assert report.rows == 1
    assert report.skipped == 0
    missing = {
        f"missing file: {name}.csv"
        for name in (
            "part_categories",
            "themes",
            "parts",
            "sets",
            "inventories",
            "inventory_parts",
        )
    }
    assert set(report.notes) == missing
    stats = catalog_stats(db)
    assert stats["colors"] == 1
    assert stats["parts"] == 0  # untouched, not an error


def test_missing_directory_yields_notes_only(db, tmp_path):
    report = import_catalog(db, tmp_path / "does-not-exist")
    assert report.files == 0
    assert report.rows == 0
    assert report.skipped == 0
    # invariant: every expected file is either imported or noted
    assert report.files + len(report.notes) == 7


def test_fk_violating_row_is_skipped_and_counted(db, tmp_path):
    fk_dir = tmp_path / "fk"
    fk_dir.mkdir()
    (fk_dir / "part_categories.csv").write_text(
        "id,name\n1,Bricks\n", encoding="utf-8"
    )
    (fk_dir / "parts.csv").write_text(
        "part_num,name,part_cat_id\n"
        "3001,Brick 2 x 4,1\n"
        "3002,Brick 2 x 3,999\n",  # 999: no such category → FK violation
        encoding="utf-8",
    )
    report = import_catalog(db, fk_dir)
    assert report.rows == 2  # the category + the one valid part
    assert report.skipped == 1
    assert get_part(db, "3001") is not None
    assert get_part(db, "3002") is None  # never landed
    assert catalog_stats(db)["parts"] == 1


def test_malformed_values_are_skipped_and_counted(db, tmp_path):
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "colors.csv").write_text(
        "id,name,rgb,is_trans\n"
        "0,Black,05131D,f\n"
        "999,Glitter,FFD800,maybe\n"  # bad boolean flag
        ",Nameless,000000,f\n",  # empty required id
        encoding="utf-8",
    )
    report = import_catalog(db, bad_dir)
    assert report.rows == 1
    assert report.skipped == 2
    assert get_color(db, "0") is not None
    assert get_color(db, "999") is None


def test_malformed_row_prints_one_stderr_warning_per_file(db, capsys):
    import_catalog(db, FIXTURES)
    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) == 1  # one warning for parts.csv, not per-row spam
    assert "parts.csv" in lines[0]


def test_get_part_roundtrip(db):
    import_catalog(db, FIXTURES)
    assert get_part(db, "3001") == Part("3001", "Brick 2 x 4", part_cat_id=1)
    assert get_part(db, "3020") == Part("3020", "Plate 2 x 4", part_cat_id=57)
    assert get_part(db, "3001bad") is None  # the malformed row never landed
    assert get_part(db, "9999") is None


def test_get_color_roundtrip(db):
    import_catalog(db, FIXTURES)
    assert get_color(db, "4") == Color("4", "Red", "C91A09", False)
    trans = get_color(db, "36")
    assert trans is not None and trans.is_trans is True
    assert get_color(db, "-1") is not None  # negative TEXT id survives
    assert get_color(db, "999") is None


def test_quoted_csv_fields_survive_import(db):
    import_catalog(db, FIXTURES)
    row = db.conn.execute(
        "SELECT name FROM part_categories WHERE id = 25"
    ).fetchone()
    # fixture category 25 is "Brick, Round" (quoted in the CSV) — the
    # csv module must keep the embedded comma as data, not a delimiter.
    assert row["name"] == "Brick, Round"