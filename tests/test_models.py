"""Tests for the shared dataclass contracts."""

import dataclasses

import pytest

from brickfolio.models import Build, BuildPart, Color, InventoryItem, Part


def test_color_defaults():
    c = Color(id="0", name="Black")
    assert c.rgb == "000000"
    assert c.is_trans is False


def test_inventory_item_equality_and_hash():
    a = InventoryItem("3001", "4", 2)
    b = InventoryItem("3001", "4", 2)
    assert a == b
    assert len({a, b}) == 1  # WHY: matcher dedupes via set()


def test_frozen_models_reject_mutation():
    item = InventoryItem("3001", "4", 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.quantity = 5


def test_build_part_spare_flag_default():
    assert BuildPart("3001", "4").is_spare is False


def test_build_defaults():
    b = Build(build_num="75192-1", name="Millennium Falcon")
    assert b.num_parts == 0
    assert b.source == "set"


def test_part_optional_category():
    assert Part("3001", "Brick 2x4").part_cat_id is None