"""Validate parsed LDraw models against the user's real inventory (Prompt 8).

Two independent checks:

- :func:`validate_against_inventory` -- can I physically build this
  model from my pile?  Mode ``"exact"`` requires every part in the
  requested color; mode ``"shape"`` ignores color and warns about
  substitutions, mirroring the matcher's shape mode.  Inventory lines
  are aggregated per key first, so duplicate lines and per-color
  splits are counted correctly.

- :func:`check_collisions` -- do any two placements overlap in space?
  Axis-aligned bounding boxes (AABB) with a small built-in size table
  for common parts.  A part without a known size produces a *warning*,
  never an error: a missing dimension is an information problem, not a
  buildability problem.

Errors mean "you cannot build this as specified"; warnings mean "you
can, but look closer".  The generator (Prompt 9) feeds both back to the
LLM in its repair loop.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from brickfolio.ldraw.parser import LDrawModel, PlacedPart
from brickfolio.models import InventoryItem

__all__ = [
    "ValidationReport",
    "validate_against_inventory",
    "check_collisions",
    "DEFAULT_BBOXES",
]

MODES = ("exact", "shape")

# Nominal part sizes in LDraw units (LDU) as (x, y, z); y is the height
# axis.  Grid facts: 1 stud pitch = 20 LDU, a brick is 24 LDU high, a
# plate 8 LDU.  The long axis of a brick follows LDraw's modeling
# convention (along x).
DEFAULT_BBOXES: Dict[str, Tuple[int, int, int]] = {
    "3001": (80, 24, 40),  # Brick 2 x 4
    "3002": (60, 24, 40),  # Brick 2 x 3
    "3003": (40, 24, 40),  # Brick 2 x 2
    "3004": (40, 24, 20),  # Brick 1 x 2
    "3005": (20, 24, 20),  # Brick 1 x 1
    "3062": (20, 24, 20),  # Brick Round 1 x 1
    "3941": (40, 24, 40),  # Brick Round 2 x 2
    "3021": (60, 8, 40),  # Plate 2 x 3
    "3022": (40, 8, 40),  # Plate 2 x 2
    "3024": (20, 8, 20),  # Plate 1 x 1
}

# Parts that exactly touch (gap 0) are legally adjacent in LDraw, so the
# overlap test is strict; _EPS absorbs float noise around exact contact.
_EPS = 1e-6


@dataclass(frozen=True)
class ValidationReport:
    """Result of validating one model.

    ``ok`` is always recomputed from ``errors`` in ``__post_init__`` so a
    report can never claim success while listing errors.
    """

    ok: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # WHY recompute: "ok" is in the constructor for the documented
        # shape, but "no errors" is the only sane definition of ok, and a
        # frozen dataclass cannot be left in an inconsistent state.
        object.__setattr__(self, "ok", not self.errors)


def validate_against_inventory(
    model: LDrawModel,
    inventory: Iterable[InventoryItem],
    mode: str,
) -> ValidationReport:
    """Check that every placed part exists in ``inventory`` with enough copies.

    mode ``"exact"``: a (part_num, color_id) pair must be in stock in that
    exact color.  mode ``"shape"``: colors are ignored (shape-only match)
    and substitutions produce warnings, like the matcher's shape mode.
    Inventory lines are aggregated per key first, so duplicate lines and
    per-color splits are counted correctly.

    Every error string carries part, color, needed and have, because the
    generator repair loop shows these messages to the LLM verbatim.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected 'exact' or 'shape'")

    have_by_key: Dict[Tuple[str, str], int] = {}
    have_by_part: Dict[str, int] = {}
    for item in inventory:
        key = (item.part_num, item.color_id)
        have_by_key[key] = have_by_key.get(key, 0) + item.quantity
        have_by_part[item.part_num] = (
            have_by_part.get(item.part_num, 0) + item.quantity
        )

    # Aggregate the model's placements into requirements: one placement
    # is one physical part, so the count per key is the quantity needed.
    needed_by_key: Dict[Tuple[str, str], int] = {}
    for part in model.parts:
        key = (part.part_num, part.color_id)
        needed_by_key[key] = needed_by_key.get(key, 0) + 1

    errors: List[str] = []
    warnings: List[str] = []

    if mode == "exact":
        for (part, color), needed in sorted(needed_by_key.items()):
            have = have_by_key.get((part, color), 0)
            if needed > have:
                errors.append(
                    f"part {part} color {color}: needed {needed}, have {have}"
                )
                if have == 0:
                    others = _other_colors(have_by_key, part, color)
                    if others:
                        # Helpful hint for the repair loop: the part exists
                        # in other colors, so shape mode could save it.
                        warnings.append(
                            f"part {part}: color {color} not in stock; "
                            f"available in color(s): {', '.join(others)}"
                        )
    else:  # shape
        needed_by_part: Dict[str, int] = {}
        for (part, _color), needed in needed_by_key.items():
            needed_by_part[part] = needed_by_part.get(part, 0) + needed
        for part, needed in sorted(needed_by_part.items()):
            have = have_by_part.get(part, 0)
            if needed > have:
                colors = _requested_colors(needed_by_key, part)
                errors.append(
                    f"part {part} color {colors}: needed {needed}, have {have}"
                )
        for (part, color), needed in sorted(needed_by_key.items()):
            if have_by_key.get((part, color), 0) < needed:
                others = _other_colors(have_by_key, part, color)
                if others:
                    warnings.append(
                        f"part {part} color {color}: substituting from "
                        f"color(s): {', '.join(others)}"
                    )

    return ValidationReport(ok=not errors, errors=errors, warnings=warnings)


def check_collisions(
    model: LDrawModel,
    bboxes: Optional[Mapping[str, Tuple[float, float, float]]] = None,
) -> List[str]:
    """AABB-overlap test over every pair of placements -> messages.

    Messages are self-describing strings: ``"collision: ..."`` for
    overlapping pairs and ``"warning: ..."`` for parts without a
    bounding box (a missing size is an information problem, never a
    verdict).  ``bboxes`` maps part_num -> LDU size tuple; None uses
    :data:`DEFAULT_BBOXES`.  A passed table is used as-is, not merged
    with the defaults.

    NOTE v1 approximation: each part's position is treated as the
    CENTER of its box and rotation is ignored.  Real LDraw part origins
    sit on the part's base plane, so boxes are effectively shifted --
    fine for catching the "two bricks in one spot" mistakes an LLM
    generator makes, not for stud-accurate clash detection.
    """
    if bboxes is None:
        bboxes = DEFAULT_BBOXES

    unknown: Dict[str, int] = {}
    known: List[PlacedPart] = []
    for part in model.parts:
        if part.part_num in bboxes:
            known.append(part)
        else:
            unknown[part.part_num] = unknown.get(part.part_num, 0) + 1

    messages: List[str] = []
    for part_num in sorted(unknown):
        messages.append(
            f"warning: no bounding box for part {part_num} "
            f"({unknown[part_num]} placement(s)); collision check skipped"
        )

    # WHY sort by line_num: .mpd flattening interleaves sub-model line
    # numbers, so parse order is not a stable basis for messages.
    known.sort(key=lambda p: p.line_num)
    for i in range(len(known)):
        for j in range(i + 1, len(known)):
            a, b = known[i], known[j]
            if _aabb_overlap(
                a.position, bboxes[a.part_num], b.position, bboxes[b.part_num]
            ):
                messages.append(
                    f"collision: {a.part_num} at line {a.line_num} overlaps "
                    f"{b.part_num} at line {b.line_num}"
                )
    return messages


def _aabb_overlap(
    pa: Tuple[float, float, float],
    sa: Tuple[float, float, float],
    pb: Tuple[float, float, float],
    sb: Tuple[float, float, float],
) -> bool:
    """True if two centered boxes strictly overlap on all three axes.

    Exactly touching faces separate: LDraw bricks legally sit flush
    against each other, so a zero gap is a legal placement.
    """
    for k in range(3):
        if abs(pa[k] - pb[k]) >= (sa[k] / 2 + sb[k] / 2) - _EPS:
            return False
    return True


def _other_colors(
    have_by_key: Dict[Tuple[str, str], int], part: str, color: str
) -> List[str]:
    """Sorted colors in which ``part`` is in stock, excluding ``color``."""
    return sorted(
        c
        for (p, c), qty in have_by_key.items()
        if p == part and c != color and qty > 0
    )


def _requested_colors(
    needed_by_key: Dict[Tuple[str, str], int], part: str
) -> str:
    """The colors the model asks for ``part`` in, joined for one message."""
    return "/".join(sorted({c for (p, c) in needed_by_key if p == part}))