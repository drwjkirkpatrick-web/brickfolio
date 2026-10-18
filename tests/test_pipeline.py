"""Tests for brickfolio.pipeline (Prompt 10).

Offline by construction: run_demo is called with no identifier and no
client, so its built-in deterministic fakes drive the vision stage and
the generation stage stays skipped; where the LLM stage *is* exercised,
a scripted FakeChatClient stands in for ChatClient — the same seam the
generator tests use.  Coverage map:

* run_demo: end-to-end facts (import counts, scan results, inventory,
  recommendation order), the printed summary, a caller-supplied photos
  dir, unknown photo names, a missing catalog dir, and the opt-in
  generation stage (first-try valid, repair loop, exhaustion);
* export_rebrickable_csv: header + rows match the db, accumulation
  math, empty inventory, and a round trip out of a demo-produced db.
"""

from __future__ import annotations

import csv
import dataclasses

import pytest

from brickfolio.db import Database
from brickfolio.matcher import MissingPart
from brickfolio.models import InventoryItem
from brickfolio.pipeline import (
    DEFAULT_CATALOG_DIR,
    DEMO_THEME,
    DemoReport,
    export_rebrickable_csv,
    render_demo_report,
    run_demo,
)

# The Prompt-2 fixture mini-catalog, counted by hand: seven files, 41
# good data rows, and exactly one malformed row (parts.csv's short
# "3001bad" line) that the importer must skip rather than crash on.
FIXTURE_FILES = 7
FIXTURE_ROWS = 41
FIXTURE_SKIPPED = 1

# The demo's scripted finds (pipeline._DEMO_PHOTOS) as inventory lines:
# what the auto-accept stage must write, in decision order.
EXPECTED_ACCEPTED = (
    InventoryItem("3001", "4", 2),
    InventoryItem("3003", "0", 4),
    InventoryItem("3001", "15", 1),
    InventoryItem("3004", "1", 3),
)

# The same collection in db order.  NOTE: color ids compare as TEXT,
# so "15" sorts before "4" — the order must match SQLite, not intuition.
EXPECTED_INVENTORY = (
    InventoryItem("3001", "15", 1),
    InventoryItem("3001", "4", 2),
    InventoryItem("3003", "0", 4),
    InventoryItem("3004", "1", 3),
)

# The fixture builds the demo pile ranks, best first: Burger Truck is
# covered exactly (READY, no color swap), Ferry / Millennium Falcon /
# Tow Truck only via substituted colors, Galaxy Explorer short by half.
EXPECTED_BUILD_ORDER = [
    "Burger Truck",
    "Ferry",
    "Millennium Falcon",
    "Tow Truck",
    "Galaxy Explorer",
]


# --- fakes (module boundaries, per the project testing strategy) --------------


class FakeChatClient:
    """Scripted stand-in for ChatClient: queued replies, recorded calls.

    Same contract the generator tests script: ``chat(messages)`` pops
    the next reply, so a test scripts a whole repair conversation.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, temperature=0.6):
        self.calls.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("FakeChatClient ran out of scripted replies")
        return self.replies.pop(0)


def part_line(part_num, color, x=0, y=0, z=0):
    """One canonical LDraw type-1 line (identity rotation, 4x3 matrix)."""
    return f"1 {color} {x} {y} {z} 1 0 0 0 1 0 0 0 1 0 0 1 {part_num}.dat"


# Buildable from the demo pile: both red 2x4s and one black 2x2.
DEMO_GOOD_MODEL = "\n".join(
    [
        part_line("3001", "4"),
        part_line("3001", "4", x=80),
        part_line("3003", "0", y=24),
    ]
)

# Round-1 mistake: one part the demo pile does not own.
DEMO_BAD_MODEL = "\n".join(
    [
        part_line("3001", "4"),
        part_line("9999", "4"),
    ]
)


# --- run_demo: end to end against the fixtures ----------------------------------


def test_default_catalog_dir_points_at_fixtures():
    # The demo's built-in catalog is the Prompt-2 fixture mini-catalog.
    assert DEFAULT_CATALOG_DIR.is_dir()
    assert (DEFAULT_CATALOG_DIR / "colors.csv").is_file()


def test_run_demo_end_to_end_offline(tmp_path):
    report = run_demo(tmp_path / "demo.db")
    assert isinstance(report, DemoReport)

    # Catalog stage: the whole fixture mini-catalog imports, with its
    # one malformed row skipped and nothing missing.
    assert report.import_report.files == FIXTURE_FILES
    assert report.import_report.rows == FIXTURE_ROWS
    assert report.import_report.skipped == FIXTURE_SKIPPED
    assert report.import_report.notes == ()

    # Scan stage: two demo photos, four scripted finds, all accepted.
    assert report.photos == 2
    assert report.candidates == 4
    assert report.accepted == EXPECTED_ACCEPTED
    assert report.inventory == EXPECTED_INVENTORY

    # Recommendation stage: the fixture ranking, best first.
    assert [rb.build.name for rb in report.ranked] == EXPECTED_BUILD_ORDER
    best = report.ranked[0].match
    assert best.coverage_pct == 100.0
    assert best.missing == []
    assert best.color_substituted is False
    ferry = report.ranked[1].match  # covered, but through swapped colors
    assert ferry.color_substituted is True
    worst = report.ranked[-1].match
    assert worst.coverage_pct == 0.0
    assert worst.missing == [MissingPart("3004", "1", 6, 3)]

    # No client given -> the LLM stage never ran.
    assert report.generation is None

    # House style: the report is an immutable fact.
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.photos = 99


def test_run_demo_prints_readable_summary(tmp_path, capsys):
    run_demo(tmp_path / "demo.db")
    out = capsys.readouterr().out
    assert out.startswith("brickfolio demo")
    assert f"catalog: {FIXTURE_FILES} file(s), {FIXTURE_ROWS} row(s) imported" in out
    assert "scan: 2 photo(s) -> 4 candidate(s), 4 accepted" in out
    assert "recommendations (shape mode, top 5):" in out
    assert "Burger Truck" in out
    assert "READY" in out
    assert "Galaxy Explorer" in out
    assert "generation: skipped" in out


def test_run_demo_scans_caller_photos_dir(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in ("pile-1.jpg", "pile-2.jpg"):
        # NOTE: zero-byte files on purpose — StaticDetector never reads
        # them and the fake identifier keys off the name, so the demo
        # needs no real image data anywhere (and stays deterministic
        # whether or not Pillow is installed).
        (photos / name).write_bytes(b"")
    report = run_demo(tmp_path / "demo.db", photos_dir=photos)
    assert report.photos == 2
    assert report.candidates == 4
    # Files named like the demo photos get the demo's scripted finds.
    assert report.accepted == EXPECTED_ACCEPTED
    assert report.inventory == EXPECTED_INVENTORY


def test_run_demo_with_unknown_photo_names(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "IMG_0001.jpg").write_bytes(b"")
    (photos / "notes.txt").write_text("not a photo")  # filtered by suffix
    report = run_demo(tmp_path / "demo.db", photos_dir=photos)
    # The built-in fake knows only the demo's own photo names: an
    # unknown photo scans cleanly but finds nothing, and non-image
    # files never reach the scan at all.
    assert report.photos == 1
    assert report.candidates == 0
    assert report.inventory == ()
    # An empty collection still ranks every build — at 0%.
    assert len(report.ranked) == 5
    assert all(rb.match.coverage_pct == 0.0 for rb in report.ranked)


def test_run_demo_survives_missing_catalog_dir(tmp_path):
    report = run_demo(tmp_path / "demo.db", csv_dir=tmp_path / "nope")
    # import_catalog never raises for missing files — one note each.
    assert report.import_report.files == 0
    assert len(report.import_report.notes) == 7
    assert report.ranked == ()
    assert "No builds to recommend." in render_demo_report(report)


# --- run_demo: the opt-in generation stage -------------------------------------


def test_run_demo_generation_valid_on_first_round(tmp_path):
    client = FakeChatClient(DEMO_GOOD_MODEL)
    report = run_demo(tmp_path / "demo.db", client=client)
    assert report.generation is not None
    assert report.generation.valid is True
    assert report.generation.rounds == 1
    assert report.generation.usage == [("3001", "4", 2), ("3003", "0", 1)]
    # The generator was handed the scanned collection...
    assert "3001 color 4: 2" in client.calls[0][0]["content"]
    # ...with the demo theme as the design brief.
    assert DEMO_THEME in client.calls[0][1]["content"]
    assert "generation: valid LDraw model in 1 round(s)" in render_demo_report(report)


def test_run_demo_generation_repairs_round_two(tmp_path):
    client = FakeChatClient(DEMO_BAD_MODEL, DEMO_GOOD_MODEL)
    report = run_demo(tmp_path / "demo.db", client=client)
    assert report.generation is not None
    assert report.generation.valid is True
    assert report.generation.rounds == 2
    assert report.generation.usage == [("3001", "4", 2), ("3003", "0", 1)]
    # Round 1's unavailable part was quoted back for the repair.
    assert "9999" in client.calls[1][-1]["content"]
    assert "generation: valid LDraw model in 2 round(s)" in render_demo_report(report)


def test_run_demo_generation_exhausts_rounds(tmp_path):
    client = FakeChatClient(DEMO_BAD_MODEL, DEMO_BAD_MODEL, DEMO_BAD_MODEL)
    report = run_demo(tmp_path / "demo.db", client=client)
    assert report.generation is not None
    assert report.generation.valid is False
    assert report.generation.rounds == 3
    # The demo still completes; the summary reports the failed stage.
    assert "generation: failed after 3 round(s)" in render_demo_report(report)


# --- export_rebrickable_csv -----------------------------------------------------


def test_export_rebrickable_csv_matches_db(db, tmp_path):
    # `db` is the conftest fixture: a fresh Database on a throwaway file.
    db.add_user_parts([InventoryItem("3001", "4", 2), InventoryItem("3003", "0", 4)])
    db.add_user_parts([InventoryItem("3001", "4", 3)])  # accumulates to 5
    export_path = tmp_path / "parts.csv"
    assert export_rebrickable_csv(db, export_path) == 2
    with open(export_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    # The exact header rebrickable.com's part-list import expects...
    assert rows[0] == ["part_num", "color_id", "quantity"]
    # ...and every row mirrors the db, in its deterministic order.
    assert rows[1:] == [["3001", "4", "5"], ["3003", "0", "4"]]
    parsed = [InventoryItem(r[0], r[1], int(r[2])) for r in rows[1:]]
    assert parsed == db.list_user_parts()


def test_export_empty_inventory_writes_header_only(db, tmp_path):
    export_path = tmp_path / "empty.csv"
    assert export_rebrickable_csv(db, export_path) == 0
    with open(export_path, newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [["part_num", "color_id", "quantity"]]


def test_export_round_trips_a_demo_database(tmp_path):
    demo_db = tmp_path / "demo.db"
    report = run_demo(demo_db)
    # run_demo closes its db; reopen to prove the collection persisted
    # as a perfectly ordinary brickfolio database.
    with Database(demo_db) as db:
        assert db.total_user_parts() == 10
        assert db.catalog_stats()["sets"] == 5
        export_path = tmp_path / "demo-parts.csv"
        assert export_rebrickable_csv(db, export_path) == len(report.inventory)
    with open(export_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["part_num", "color_id", "quantity"]
    # The export matches the db the demo produced, line for line.
    parsed = [InventoryItem(r[0], r[1], int(r[2])) for r in rows[1:]]
    assert parsed == list(report.inventory)