"""Core domain models for brickfolio.

NOTE: These frozen dataclasses are the shared contract used by every other
module (catalog, builds, matcher, report, vision, ldraw, generator). Only the
parent agent edits this file — subagents request changes via their summary.

WHY frozen dataclasses: a part or a parts-list line is an immutable fact.
Freezing makes instances hashable, so the matcher can use them directly in
sets/dicts, and accidental mutation bugs are impossible.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Color:
    """A Rebrickable color (id "0" = Black, "4" = Red, ...)."""

    id: str
    name: str
    rgb: str = "000000"
    is_trans: bool = False


@dataclass(frozen=True)
class Part:
    """A LEGO part design, identified by its Rebrickable part number."""

    part_num: str
    name: str
    part_cat_id: Optional[int] = None


@dataclass(frozen=True)
class InventoryItem:
    """One line of the user's collection: N copies of a part in one color."""

    part_num: str
    color_id: str
    quantity: int = 1


@dataclass(frozen=True)
class BuildPart:
    """One line of a build's parts list (what a model requires)."""

    part_num: str
    color_id: str
    quantity: int = 1
    is_spare: bool = False


@dataclass(frozen=True)
class Build:
    """A buildable model: an official set or a fan MOC.

    NOTE: build_num follows Rebrickable conventions, e.g. "75192-1" for a
    set or "MOC-12345" for a MOC, so IDs stay interchangeable with the
    rebrickable.com ecosystem.
    """

    build_num: str
    name: str
    num_parts: int = 0
    source: str = "set"  # "set" | "moc"