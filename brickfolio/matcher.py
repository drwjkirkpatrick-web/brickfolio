"""Matcher engine: how much of a build can you raise from your own pile?

Pure coverage math — no I/O, no database, no network. Everything works on
the frozen dataclasses from :mod:`brickfolio.models`, so this module is
trivially testable and reusable: the report layer (Prompt 5), the LDraw
validator (Prompt 8) and the pipeline (Prompt 10) all lean on it.

Two matching modes:

``"exact"``
    A build requirement is covered only by inventory of the same part *and*
    the same color. Purist mode: the model looks exactly as designed.

``"shape"``
    Color is ignored — any color of the right part shape counts. When a
    requirement can only be fulfilled that way, ``color_substituted`` is
    set so the UI can say "buildable, but with swapped colors".

Coverage is per requirement *line*, not per piece: build lines are
aggregated per ``(part_num, color_id)`` with spare lines excluded (a spare
bag is a bonus, not a requirement), and each line either is fully
satisfied or lands in ``missing`` carrying its ``needed``/``have`` gap.
``coverage_pct`` is the share of requirement lines fully satisfied, so
100 % means "you can build it". A build with no requirements is vacuously
complete (100 %).

WHY line-based coverage: it mirrors the LDraw validator (a part line either
has sufficient quantity or it does not) and every ``missing`` entry maps
one-to-one to a shopping-list line. A per-piece ratio would hide *which*
lines you still need to shop for.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .models import Build, BuildPart, InventoryItem

#: The two matching modes accepted by :func:`match_build`/:func:`rank_builds`.
MODES: Tuple[str, ...] = ("exact", "shape")


@dataclass(frozen=True)
class MissingPart:
    """One build requirement the inventory cannot fully satisfy.

    ``have`` is the quantity the inventory can actually put toward the
    requirement — in ``"shape"`` mode that total pools every color of the
    part, because that pooled total is what decided coverage. ``color_id``
    stays the color the build *asked* for, so a shopping list stays honest.
    """

    part_num: str
    color_id: str
    needed: int
    have: int


@dataclass(frozen=True)
class MatchResult:
    """Coverage of one build against one inventory.

    ``required_parts``/``covered_parts`` count requirement lines (not
    pieces); the quantity-level detail lives in ``missing`` entries.
    """

    coverage_pct: float
    required_parts: int
    covered_parts: int
    missing: List[MissingPart]
    color_substituted: bool


@dataclass(frozen=True)
class RankedBuild:
    """A build paired with its match result, as produced by rank_builds."""

    build: Build
    match: MatchResult


def _check_mode(mode: str) -> None:
    """Raise ``ValueError`` unless ``mode`` is one of :data:`MODES`."""
    if mode not in MODES:
        raise ValueError(f"unknown match mode {mode!r}; expected one of {MODES}")


def _aggregate_inventory(
    inventory: Iterable[InventoryItem],
) -> Dict[Tuple[str, str], int]:
    """Sum inventory lines per ``(part_num, color_id)`` key.

    WHY aggregate first: the user's collection may hold the same part+color
    across several lines (scanned and added at different times); a
    requirement is matched against the *total*, not any single line.
    """
    totals: Dict[Tuple[str, str], int] = {}
    for item in inventory:
        key = (item.part_num, item.color_id)
        totals[key] = totals.get(key, 0) + item.quantity
    return totals


def _requirements(build_parts: Iterable[BuildPart]) -> Dict[Tuple[str, str], int]:
    """Sum build lines per ``(part_num, color_id)`` key, dropping spares.

    WHY spares are excluded: Rebrickable marks spare-bag lines
    ``is_spare=True``; they are optional extras, so a build is complete
    without them. The matcher decides how to use them — by ignoring them.
    """
    required: Dict[Tuple[str, str], int] = {}
    for bp in build_parts:
        if bp.is_spare:
            continue
        key = (bp.part_num, bp.color_id)
        required[key] = required.get(key, 0) + bp.quantity
    return required


def _match(
    have: Dict[Tuple[str, str], int],
    required: Dict[Tuple[str, str], int],
    mode: str,
) -> MatchResult:
    """Core comparison on pre-aggregated maps (mode already validated)."""
    # In shape mode a requirement may draw on every color of a part, so pool
    # per-part totals once instead of rescanning the inventory per line.
    per_part: Dict[str, int] = {}
    if mode == "shape":
        for (part_num, _color_id), qty in have.items():
            per_part[part_num] = per_part.get(part_num, 0) + qty

    covered = 0
    missing: List[MissingPart] = []
    color_substituted = False

    # Sorted keys -> deterministic ``missing`` order, so reports are stable.
    for part_num, color_id in sorted(required):
        needed = required[(part_num, color_id)]
        exact_qty = have.get((part_num, color_id), 0)
        usable = per_part.get(part_num, 0) if mode == "shape" else exact_qty
        if usable >= needed:
            covered += 1
            # A covered line that outgrew its own color's stock was fulfilled
            # with other colors -> flag it for the "swapped colors" note.
            if exact_qty < needed:
                color_substituted = True
        else:
            missing.append(MissingPart(part_num, color_id, needed, usable))

    total = len(required)
    # NOTE: no requirements at all -> nothing can be missing -> 100 %.
    coverage = 100.0 if total == 0 else round(100.0 * covered / total, 2)
    return MatchResult(
        coverage_pct=coverage,
        required_parts=total,
        covered_parts=covered,
        missing=missing,
        color_substituted=color_substituted,
    )


def match_build(
    inventory: Iterable[InventoryItem],
    build_parts: Iterable[BuildPart],
    mode: str,
) -> MatchResult:
    """Match one build's parts list against an inventory.

    Inventory lines and build lines are aggregated per
    ``(part_num, color_id)`` first; spare build lines (``is_spare=True``)
    are excluded from the requirements. See the module docstring for the
    two modes and the coverage definition.
    """
    _check_mode(mode)
    return _match(_aggregate_inventory(inventory), _requirements(build_parts), mode)


def rank_builds(
    inventory: Iterable[InventoryItem],
    builds_with_parts: Iterable[Tuple[Build, List[BuildPart]]],
    mode: str,
    top: Optional[int] = None,
) -> List[RankedBuild]:
    """Match many builds against one inventory, best-coverage-first.

    ``builds_with_parts`` yields ``(Build, List[BuildPart])`` pairs. The
    result is sorted by coverage (descending) with ties broken by build
    name, so output is deterministic. ``top`` truncates the list
    (``None`` = all).
    """
    _check_mode(mode)  # fail fast, even for empty inputs
    # The inventory is shared by every build — aggregate it once, not per build.
    have = _aggregate_inventory(inventory)
    ranked = [
        RankedBuild(build=build, match=_match(have, _requirements(parts), mode))
        for build, parts in builds_with_parts
    ]
    ranked.sort(key=lambda rb: (-rb.match.coverage_pct, rb.build.name))
    if top is not None:
        # NOTE: clamp so a negative top means "no results", not "all but N"
        # (a bare [:top] would silently drop the *worst*-ranked builds).
        ranked = ranked[: max(0, top)]
    return ranked