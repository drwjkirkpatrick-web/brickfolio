"""Tests for brickfolio.ldraw.parser (Prompt 8).

Covers: parse counts/fields on the fixture model, ignored line types,
the 9-value vs 12-value matrix forms, .mpd sub-model flattening with
composed transforms, root-model selection, and the strict error paths.
All input is inline text or hand-written fixture files -- no network.
"""

from pathlib import Path

import pytest

from brickfolio.ldraw import LDrawParseError, PlacedPart, parse_ldraw

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ldraw"

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
ROT_Y90 = ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))


def fixture_text(filename):
    return (FIXTURES / filename).read_text(encoding="utf-8")


def test_parse_counts_and_fields():
    model = parse_ldraw(fixture_text("clean_house.ldr"), name="clean_house.ldr")
    assert model.name == "clean_house.ldr"
    assert [p.part_num for p in model.parts] == ["3001", "3001", "3003", "3005"]
    assert model.parts[0] == PlacedPart(
        line_num=4,
        color_id="4",
        part_num="3001",
        position=(-40.0, 0.0, 0.0),
        matrix=IDENTITY,
    )
    assert model.parts[2].color_id == "0"
    assert model.parts[2].position == (0.0, 24.0, 0.0)
    assert model.parts[3].position == (0.0, 0.0, -60.0)


def test_default_name_and_empty_text():
    assert parse_ldraw(fixture_text("clean_house.ldr")).name == "model"
    empty = parse_ldraw("")
    assert empty.name == "model"
    assert empty.parts == []


def test_other_line_types_are_ignored():
    text = "\n".join(
        [
            "0 // comment only",
            "2 24 0 0 0 40 0 0",
            "3 0 0 0 0 20 0 0 0 24 0",
            "4 0 0 0 0 20 0 0 0 24 0 20 24 0",
            "5 24 0 0 0 20 0 0 20 0 0 0 0 20",
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat",
        ]
    )
    model = parse_ldraw(text)
    assert [p.part_num for p in model.parts] == ["3005"]


def test_nine_value_matrix_is_classic_form():
    # Real-world .ldr files carry only the 3x3 rotation (9 values).
    text = "1 4 10 20 30 1 0 0 0 1 0 0 0 1 3005.dat"
    part = parse_ldraw(text).parts[0]
    assert part.position == (10.0, 20.0, 30.0)
    assert part.matrix == IDENTITY


def test_twelve_value_matrix_drops_fourth_row():
    # The 4x3 form appends the homogeneous row (here "5 5 5"); the
    # parser must record only the first nine values as the rotation.
    text = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 5 5 5 3005.dat"
    part = parse_ldraw(text).parts[0]
    assert part.matrix == IDENTITY


def test_dat_extension_is_stripped():
    text = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3001.dat"
    assert parse_ldraw(text).parts[0].part_num == "3001"


def test_unresolved_reference_kept_as_part_name():
    # A plain .ldr may reference sibling files we were not given; keep
    # the raw name so the validator can report it as missing.
    text = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 body.ldr"
    assert parse_ldraw(text).parts[0].part_num == "body.ldr"


def test_malformed_placement_raises():
    with pytest.raises(LDrawParseError) as excinfo:
        parse_ldraw("1 4 0 0 3005.dat")
    assert "line 1" in str(excinfo.value)


def test_bad_matrix_arity_raises():
    # Ten matrix values: neither the 9-value nor the 12-value form.
    # WHY built with join: hand-counting 16 tokens invites typos.
    line = " ".join(["1", "4", "0", "0", "0"] + ["0"] * 10 + ["3005.dat"])
    with pytest.raises(LDrawParseError, match="9 or 12"):
        parse_ldraw(line)


def test_non_numeric_value_raises():
    text = "1 4 x 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat"
    with pytest.raises(LDrawParseError) as excinfo:
        parse_ldraw(text)
    assert "line 1" in str(excinfo.value)


def test_bom_and_crlf_tolerated():
    text = "\ufeff1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat\r\n"
    assert [p.part_num for p in parse_ldraw(text).parts] == ["3005"]


def test_mpd_flattens_submodels_with_composed_transforms():
    model = parse_ldraw(fixture_text("car.mpd"), name="car.mpd")
    assert model.name == "car.mpd"
    assert [p.part_num for p in model.parts] == [
        "3001",
        "3001",
        "3001",
        "3001",
        "3003",
    ]
    # Flatten order follows the main model: base(1) -> base(2) -> direct part.
    assert [p.line_num for p in model.parts] == [3, 4, 3, 4, 10]
    # Identity placement of base.ldr: pure translation add.
    assert model.parts[0].position == (-40.0, 24.0, 0.0)
    assert model.parts[0].matrix == IDENTITY
    assert model.parts[1].position == (40.0, 24.0, 0.0)
    # Rotated placement: position = R @ p + t and matrix = R @ M.
    assert model.parts[2].position == (0.0, 48.0, 40.0)
    assert model.parts[2].matrix == ROT_Y90
    assert model.parts[3].position == (0.0, 48.0, -40.0)
    assert model.parts[3].matrix == ROT_Y90
    # A direct part in the main model, unaffected by the sub-model calls.
    assert model.parts[4].color_id == "0"
    assert model.parts[4].position == (0.0, 72.0, 0.0)


def test_mpd_submodel_parts_keep_authored_color():
    # The reference lines carry colors 1 and 7, but parts keep the color
    # written on their own type-1 lines (v1 does not model the LDraw
    # color-16 inheritance scheme).
    model = parse_ldraw(fixture_text("car.mpd"))
    assert {p.color_id for p in model.parts} == {"4", "0"}


def test_mpd_root_is_first_unreferenced_section():
    # wheel.ldr comes first but is referenced, so main.ldr is the root.
    text = "\n".join(
        [
            "0 FILE wheel.ldr",
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3062.dat",
            "0 NOFILE",
            "0 FILE main.ldr",
            "1 4 0 24 0 1 0 0 0 1 0 0 0 1 0 0 1 wheel.ldr",
            "0 NOFILE",
        ]
    )
    model = parse_ldraw(text, name="main")
    assert [p.part_num for p in model.parts] == ["3062"]
    assert model.parts[0].position == (0.0, 24.0, 0.0)


def test_mpd_forward_reference_to_later_section():
    text = "\n".join(
        [
            "0 FILE main.ldr",
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 sub.ldr",
            "0 NOFILE",
            "0 FILE sub.ldr",
            "1 0 10 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat",
            "0 NOFILE",
        ]
    )
    model = parse_ldraw(text)
    assert [p.part_num for p in model.parts] == ["3005"]
    assert model.parts[0].position == (10.0, 0.0, 0.0)


def test_mpd_submodel_names_match_case_insensitively():
    text = "\n".join(
        [
            "0 FILE Base.ldr",
            "1 0 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat",
            "0 NOFILE",
            "0 FILE top.ldr",
            "1 4 0 24 0 1 0 0 0 1 0 0 0 1 0 0 1 base.ldr",
            "0 NOFILE",
        ]
    )
    model = parse_ldraw(text)
    assert [p.part_num for p in model.parts] == ["3005"]
    assert model.parts[0].position == (0.0, 24.0, 0.0)


def test_mpd_sections_split_without_nofile():
    text = "\n".join(
        [
            "0 FILE a.ldr",
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3005.dat",
            "0 FILE b.ldr",
            "1 4 40 0 0 1 0 0 0 1 0 0 0 1 0 0 1 3004.dat",
            "0 NOFILE",
        ]
    )
    # Nothing references anything, so the first section is the root.
    model = parse_ldraw(text)
    assert [p.part_num for p in model.parts] == ["3005"]


def test_mpd_circular_reference_raises():
    text = "\n".join(
        [
            "0 FILE a.ldr",
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 b.ldr",
            "0 NOFILE",
            "0 FILE b.ldr",
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 0 0 1 a.ldr",
            "0 NOFILE",
        ]
    )
    with pytest.raises(LDrawParseError, match="circular"):
        parse_ldraw(text)