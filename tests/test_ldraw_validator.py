"""Tests for brickfolio.ldraw.validator (Prompt 8).

Covers: exact/shape validation against an inventory (clean pass, missing
part, insufficient quantity, wrong color, substitution warnings,
duplicate-line aggregation) and AABB collision checking (overlap, legal
touching, unknown parts, caller-supplied size tables).
"""

from pathlib import Path

import pytest

from brickfolio.ldraw import (
    DEFAULT_BBOXES,
    ValidationReport,
    check_collisions,
    parse_ldraw,
    validate_against_inventory,
)
from brickfolio.models import InventoryItem

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ldraw"


def parse_fixture(filename):
    return parse_ldraw((FIXTURES / filename).read_text(encoding="utf-8"))


def clean_house():
    return parse_fixture("clean_house.ldr")


def tower_crash():
    return parse_fixture("tower_crash.ldr")


# Exactly what clean_house.ldr needs: two red 2x4 bricks, one black 2x2
# brick, one red 1x1 brick.
FULL_INVENTORY = [
    InventoryItem("3001", "4", 2),
    InventoryItem("3003", "0", 1),
    InventoryItem("3005", "4", 1),
]


def test_report_defaults_and_ok_is_derived():
    report = ValidationReport()
    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []
    # ok is recomputed: a report listing errors can never claim ok.
    assert ValidationReport(ok=True, errors=["boom"]).ok is False


def test_clean_model_passes_exact():
    report = validate_against_inventory(clean_house(), FULL_INVENTORY, "exact")
    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []


def test_clean_model_passes_shape():
    report = validate_against_inventory(clean_house(), FULL_INVENTORY, "shape")
    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []


def test_duplicate_inventory_lines_aggregate():
    split = [
        InventoryItem("3001", "4", 1),
        InventoryItem("3001", "4", 1),
        InventoryItem("3003", "0", 1),
        InventoryItem("3005", "4", 1),
    ]
    report = validate_against_inventory(clean_house(), split, "exact")
    assert report.ok is True


def test_missing_part_is_error():
    model = parse_ldraw("1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 9999.dat")
    report = validate_against_inventory(model, FULL_INVENTORY, "exact")
    assert report.ok is False
    assert report.errors == ["part 9999 color 4: needed 1, have 0"]
    assert report.warnings == []


def test_insufficient_quantity_is_error():
    short = [
        InventoryItem("3001", "4", 1),
        InventoryItem("3003", "0", 1),
        InventoryItem("3005", "4", 1),
    ]
    report = validate_against_inventory(clean_house(), short, "exact")
    assert report.ok is False
    assert report.errors == ["part 3001 color 4: needed 2, have 1"]


def test_exact_mode_wrong_color_is_error_with_hint():
    recolored = [
        InventoryItem("3001", "0", 2),  # enough bricks, but black not red
        InventoryItem("3003", "0", 1),
        InventoryItem("3005", "0", 1),
    ]
    report = validate_against_inventory(clean_house(), recolored, "exact")
    assert report.ok is False
    assert "part 3001 color 4: needed 2, have 0" in report.errors
    assert "part 3005 color 4: needed 1, have 0" in report.errors
    # Exact mode still points at the colors that ARE in stock -- useful
    # feedback for the generator's repair loop.
    assert any("3001" in w for w in report.warnings)
    assert any("3005" in w for w in report.warnings)


def test_shape_mode_substitutes_color_with_warning():
    recolored = [
        InventoryItem("3001", "0", 2),
        InventoryItem("3003", "0", 1),
        InventoryItem("3005", "0", 1),
    ]
    report = validate_against_inventory(clean_house(), recolored, "shape")
    assert report.ok is True
    assert report.errors == []
    # 3001 and 3005 are requested in red but only black is in stock.
    assert len(report.warnings) == 2
    assert any("3001" in w for w in report.warnings)
    assert any("3005" in w for w in report.warnings)


def test_shape_mode_insufficient_total_is_error():
    short = [
        InventoryItem("3001", "4", 1),
        InventoryItem("3003", "0", 1),
        InventoryItem("3005", "4", 1),
    ]
    report = validate_against_inventory(clean_house(), short, "shape")
    assert report.ok is False
    assert report.errors == ["part 3001 color 4: needed 2, have 1"]
    assert report.warnings == []


def test_shape_mode_aggregates_across_colors():
    text = "\n".join(
        [
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3001.dat",
            "1 4 0 24 0 1 0 0 0 1 0 0 0 1 0 0 1 3001.dat",
            "1 0 0 48 0 1 0 0 0 1 0 0 0 1 0 0 1 3001.dat",
        ]
    )
    model = parse_ldraw(text)  # needs 3001 x3 across two colors
    inventory = [InventoryItem("3001", "0", 2)]  # only 2 in total
    report = validate_against_inventory(model, inventory, "shape")
    assert report.ok is False
    assert report.errors == ["part 3001 color 0/4: needed 3, have 2"]


def test_empty_model_is_vacuously_valid():
    report = validate_against_inventory(parse_ldraw(""), FULL_INVENTORY, "exact")
    assert report.ok is True


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="mode"):
        validate_against_inventory(clean_house(), FULL_INVENTORY, "sloppy")


def test_overlapping_placements_report_collision():
    messages = check_collisions(tower_crash())
    assert len(messages) == 1
    assert "collision" in messages[0]
    assert "3001" in messages[0]
    assert "line 2" in messages[0] and "line 3" in messages[0]


def test_clean_model_has_no_collisions():
    assert check_collisions(clean_house()) == []


def test_touching_parts_do_not_collide():
    # Bricks exactly one stud apart (or stacked one brick height apart)
    # touch face to face -- legal LDraw placement, not a collision.
    text = "\n".join(
        [
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat",
            "1 4 20 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat",
            "1 4 0 24 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat",
        ]
    )
    assert check_collisions(parse_ldraw(text)) == []


def test_unknown_part_warns_but_never_collides():
    text = "\n".join(
        [
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3001.dat",
            "1 4 5 0 0 1 0 0 0 1 0 0 0 1 0 0 1 8888xyz.dat",
        ]
    )
    messages = check_collisions(parse_ldraw(text))
    assert messages == [
        "warning: no bounding box for part 8888xyz (1 placement(s)); "
        "collision check skipped"
    ]


def test_custom_bbox_table_enables_and_replaces_defaults():
    text = "\n".join(
        [
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 7777.dat",
            "1 4 5 0 0 1 0 0 0 1 0 0 0 1 0 0 1 7777.dat",
        ]
    )
    model = parse_ldraw(text)
    # The default table does not know part 7777: one warning, no verdict.
    default_messages = check_collisions(model)
    assert len(default_messages) == 1
    assert "warning" in default_messages[0]
    # A caller-supplied table is used as-is and finds the overlap.
    custom_messages = check_collisions(model, {"7777": (20, 24, 20)})
    assert len(custom_messages) == 1
    assert "collision" in custom_messages[0]


def test_default_bbox_table_covers_common_parts():
    for part_num in ("3001", "3002", "3003", "3004", "3005", "3024"):
        assert part_num in DEFAULT_BBOXES
    # 1x1 brick: one stud wide (20 LDU), one brick high (24 LDU).
    assert DEFAULT_BBOXES["3005"] == (20, 24, 20)