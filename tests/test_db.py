"""Tests for the SQLite storage layer."""

import pytest

from brickfolio.db import Database
from brickfolio.models import InventoryItem


def test_schema_created_on_init(tmp_path):
    db = Database(tmp_path / "fresh.db")
    stats = db.catalog_stats()
    assert set(stats.values()) == {0}
    db.close()


def test_meta_roundtrip(db):
    assert db.get_meta("schema_version") is None
    db.set_meta("schema_version", "1")
    assert db.get_meta("schema_version") == "1"
    db.set_meta("schema_version", "2")  # upsert
    assert db.get_meta("schema_version") == "2"
    assert db.get_meta("missing", "fallback") == "fallback"


def test_add_accumulates_then_replaces(db):
    db.add_user_parts([InventoryItem("3001", "0", 5)])
    db.add_user_parts([InventoryItem("3001", "0", 3)])
    assert db.list_user_parts() == [InventoryItem("3001", "0", 8)]
    db.add_user_parts([InventoryItem("3001", "0", 2)], replace=True)
    assert db.list_user_parts() == [InventoryItem("3001", "0", 2)]


def test_add_rejects_negative_quantity(db):
    with pytest.raises(ValueError):
        db.add_user_parts([InventoryItem("3001", "0", -1)])
    assert db.list_user_parts() == []


def test_remove_partial_then_exhausted(db):
    db.add_user_parts([InventoryItem("3001", "0", 5), InventoryItem("3004", "4", 1)])
    assert db.remove_user_parts("3001", "0", 2) == 3
    assert db.remove_user_parts("3001", "0", 3) == 0
    items = db.list_user_parts()
    assert items == [InventoryItem("3004", "4", 1)]  # 3001 row deleted


def test_remove_errors(db):
    with pytest.raises(ValueError):
        db.remove_user_parts("3001", "0")  # not in inventory
    db.add_user_parts([InventoryItem("3001", "0", 1)])
    with pytest.raises(ValueError):
        db.remove_user_parts("3001", "0", 2)  # insufficient stock


def test_list_sorted_and_total(db):
    db.add_user_parts(
        [
            InventoryItem("3004", "4", 1),
            InventoryItem("3001", "0", 2),
            InventoryItem("3001", "4", 2),
        ]
    )
    items = db.list_user_parts()
    assert [(i.part_num, i.color_id) for i in items] == [
        ("3001", "0"),
        ("3001", "4"),
        ("3004", "4"),
    ]
    assert db.total_user_parts() == 5


def test_user_parts_survive_without_catalog(db):
    # WHY no FK: scans can add parts before any catalog import.
    db.add_user_parts([InventoryItem("99999", "0", 1)])
    assert db.total_user_parts() == 1


def test_foreign_keys_enforced(db):
    with pytest.raises(Exception):
        db.conn.execute(
            "INSERT INTO parts (part_num, name, part_cat_id) "
            "VALUES ('3001', 'Brick 2x4', 999)"
        )
        db.conn.commit()