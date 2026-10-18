"""Recommender and reports: matcher coverage turned into human output.

Prompt 3 knows what builds exist (:func:`brickfolio.builds.iter_builds` and
:func:`brickfolio.builds.build_parts`); Prompt 4 knows how well a parts
list is covered by a pile (:func:`brickfolio.matcher.rank_builds`). This
module joins the two and renders what a person reads at the terminal:

- :func:`recommend_builds` — every catalog build ranked best-first against
  the user's collection (default ``mode="shape"``: building with swapped
  colors still builds, so a color swap outranks a gap).
- :func:`render_recommendations` — one line per build: name, a 10-char
  ASCII coverage bar (``██████░░░░ 66%`` style), ``READY`` at 100 %, and
  with ``verbose=True`` the top missing-part lines.
- :func:`render_missing_list` — one build's full shopping list: part,
  color, needed, have.

WHY rendering lives apart from ranking: ``rank_builds`` is pure coverage
math that the pipeline (Prompt 10) also consumes headless; collecting the
string shapes here means the CLI — or any future UI — can restyle a
report without touching the engine or its tests.
"""

from typing import List, Optional, Sequence

from .builds import build_parts, iter_builds
from .db import Database
from .matcher import MissingPart, RankedBuild, rank_builds

__all__ = [
    "recommend_builds",
    "render_recommendations",
    "render_missing_list",
]

#: Coverage bar width in characters — 10, one block per 10 % step
#: (fixed by PROMPTS.md).
BAR_WIDTH = 10

#: The two bar glyphs. WHY Unicode blocks: each 10 % step is exactly one
#: terminal cell, and the bar stays BAR_WIDTH cells wide in any font that
#: can display it at all.
BAR_FILLED = "█"
BAR_EMPTY = "░"

#: Missing-part lines shown per build by ``render_recommendations`` before
#: the rest fold into one ``... and N more`` note. WHY a cap: verbose mode
#: is for scanning the top of the list at a glance — a build missing dozens
#: of lines would bury the builds ranked after it. The full list is one
#: call away (:func:`render_missing_list`).
MAX_VERBOSE_MISSING = 3


def recommend_builds(
    db: Database,
    mode: str = "shape",
    top: Optional[int] = 10,
) -> List[RankedBuild]:
    """Rank every catalog build against the user's collection.

    Joins the catalog side (``iter_builds`` + ``build_parts``) with the
    matcher: one lazy pass over all builds, each streamed to
    :func:`brickfolio.matcher.rank_builds` together with the user's
    inventory. Sorting (coverage desc, then name) and ``top`` truncation
    are the matcher's contract, so an unknown ``mode`` raises ValueError
    from there, fail-fast, before any catalog row is read. ``top=None``
    keeps the whole ranking.

    NOTE: one ``build_parts`` query per build (an N+1 over the catalog) is
    deliberate: builds stream lazily and peak memory stays at a single
    parts list — which matters more on a catalog of tens of thousands of
    sets than the extra round-trips. Batch the query only if profiling
    ever blames this loop.
    """
    inventory = db.list_user_parts()
    builds_with_parts = (
        (build, build_parts(db, build.build_num)) for build in iter_builds(db)
    )
    return rank_builds(inventory, builds_with_parts, mode, top=top)


def render_recommendations(
    ranked: List[RankedBuild],
    verbose: bool = False,
) -> str:
    """Render ranked builds one line each, best first.

    Line shape: ``<name>  <bar> <pct>%`` plus `` READY`` for a fully
    covered build, e.g. ``Ferry  ██████████ 100% READY``. With
    ``verbose=True`` up to :data:`MAX_VERBOSE_MISSING` missing-part lines
    are indented under each build, the remainder folded into one
    ``... and N more`` note.

    NOTE: an empty ranking renders as a friendly one-liner (the CLI
    prints the return value verbatim), never as a blank string.
    """
    if not ranked:
        return "No builds to recommend."
    lines: List[str] = []
    for ranked_build in ranked:
        lines.append(_recommendation_line(ranked_build))
        if verbose:
            lines.extend(_verbose_missing_lines(ranked_build.match.missing))
    return "\n".join(lines)


def render_missing_list(ranked_build: RankedBuild) -> str:
    """Render one build's full shopping list: part, color, needed, have.

    The missing lines come from the matcher already sorted by
    (part_num, color_id), so the list is deterministic. A fully covered
    build renders as a nothing-missing line instead of an empty table.

    NOTE: the table shows raw Rebrickable ids, not names — resolving
    ``parts.name``/``colors.name`` would need a catalog join, and this
    pure function deliberately takes only the :class:`RankedBuild`.
    """
    build = ranked_build.build
    match = ranked_build.match
    pct = _format_pct(match.coverage_pct)
    if not match.missing:
        # NOTE: by matcher contract, ``missing`` is empty exactly when
        # every requirement line is covered (or the build had none) —
        # i.e. coverage is 100 %.
        return f"{build.name}: nothing missing — READY at {pct}."
    lines = [
        f"Shopping list for {build.name} ({pct} covered, "
        f"{_plural(len(match.missing), 'missing line')}):"
    ]
    headers = ("part", "color", "needed", "have")
    rows = [
        (mp.part_num, mp.color_id, str(mp.needed), str(mp.have))
        for mp in match.missing
    ]
    widths = [
        max(len(header), *(len(row[i]) for row in rows))
        for i, header in enumerate(headers)
    ]
    lines.append(_table_line(headers, widths))
    lines.extend(_table_line(row, widths) for row in rows)
    return "\n".join(lines)


# ---- private helpers -------------------------------------------------------


def _recommendation_line(ranked_build: RankedBuild) -> str:
    """``Name  ██████████ 100% READY`` — the one-line summary of a build."""
    match = ranked_build.match
    line = (
        f"{ranked_build.build.name}  "
        f"{_coverage_bar(match.coverage_pct)} {_format_pct(match.coverage_pct)}"
    )
    # NOTE: == 100, not >=: the matcher produces exactly 100.0 only for a
    # fully covered build, and READY must not decorate hand-crafted
    # out-of-range values either.
    if match.coverage_pct == 100:
        line += " READY"
    return line


def _coverage_bar(coverage_pct: float) -> str:
    """A BAR_WIDTH-char bar, filled blocks first: ``██████░░░░``.

    WHY floor, never round: a full bar must mean "you can build it", so
    100 % is the only coverage that lights all ten blocks — rounding up
    would hand a 95 % build the full bar. The clamp keeps the length
    exactly BAR_WIDTH even for hand-crafted out-of-range values.
    """
    filled = min(BAR_WIDTH, max(0, int(coverage_pct) // 10))
    return BAR_FILLED * filled + BAR_EMPTY * (BAR_WIDTH - filled)


def _format_pct(coverage_pct: float) -> str:
    # NOTE: the matcher rounds coverage to two decimals, and %g drops a
    # trailing ".0" so a whole-number line reads "72%" (PROMPTS.md's
    # example shape) while 66.67 keeps its precision.
    return f"{coverage_pct:g}%"


def _verbose_missing_lines(missing: List[MissingPart]) -> List[str]:
    """Up to MAX_VERBOSE_MISSING indented lines, plus an and-N-more note."""
    shown = missing[:MAX_VERBOSE_MISSING]
    lines = [
        f"    {mp.part_num} (color {mp.color_id}): "
        f"need {mp.needed}, have {mp.have}"
        for mp in shown
    ]
    hidden = len(missing) - len(shown)
    if hidden > 0:
        lines.append(f"    ... and {_plural(hidden, 'more missing line')}")
    return lines


def _table_line(fields: Sequence[str], widths: List[int]) -> str:
    # WHY dynamic widths: part numbers vary in length ("3001",
    # "MOC-1234"); fixed padding would misalign longer ids. rstrip so a
    # line ends clean at its last digit.
    return "  " + "  ".join(f.ljust(w) for f, w in zip(fields, widths)).rstrip()


def _plural(n: int, noun: str) -> str:
    # NOTE: keeps single-count grammar readable ("1 missing line").
    return f"{n} {noun}{'' if n == 1 else 's'}"