"""End-to-end demo pipeline and Rebrickable CSV export (Prompt 10).

``run_demo`` stitches every earlier stage into one scripted walk-through
of the whole product story::

    fixture catalog   --import_catalog-->   the SQLite catalog
    demo photos       --ScanSession-->      user_parts (the collection)
    collection        --recommend_builds--> ranked builds
    (opt-in) LLM      --generate_build-->   a model proven buildable

It exists for two audiences.  For newcomers, ``brickfolio demo`` shows
the entire pipeline working in seconds — no network, no photos, no LLM.
For the tests, the same call is the Prompt-10 integration check: every
stage runs its real code, with fakes injected only at the two network
boundaries (the part identifier and the LLM client).

``export_rebrickable_csv`` closes the loop the other way: the
collection goes back out as a Rebrickable part-list CSV
(``part_num,color_id,quantity``), so a brickfolio inventory is never a
dead end — it imports straight into rebrickable.com's part lists.

WHY the demo is offline by construction: the default identifier is a
deterministic in-module fake keyed by photo *name*, and the generation
stage runs only when a caller passes an LLM client.  Nothing here opens
a socket, so ``run_demo(db_path)`` is safe to call from any test, ever.

WHY the demo auto-accepts every candidate: a script has no human in the
loop to review detections.  The scripted finds carry their accept
decision (color, quantity) with them; candidates the script does not
know (e.g. from a real identifier) fall back to the session defaults.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .generator import GenerationResult, generate_build
from .importer import ImportReport, import_catalog
from .matcher import RankedBuild
from .models import InventoryItem
from .report import recommend_builds, render_recommendations
from .vision.brickognize import PartCandidate
from .vision.detector import StaticDetector
from .vision.session import ScanSession

__all__ = [
    "DEFAULT_CATALOG_DIR",
    "DEMO_MODE",
    "DEMO_THEME",
    "DEMO_TOP",
    "DemoReport",
    "EXPORT_HEADER",
    "export_rebrickable_csv",
    "render_demo_report",
    "run_demo",
]

#: The catalog the demo imports: the Prompt-2 fixture mini-CSVs.  WHY a
#: repo-relative default: the demo is a fixture-driven walk-through — a
#: real user's catalog arrives via ``catalog import``.  Resolved from
#: this file, not the cwd, so the demo works from any directory.
DEFAULT_CATALOG_DIR = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "rebrickable"
)

#: Matching mode for the demo's recommendation stage.  WHY shape mode:
#: a color swap still builds, and "covered with other colors" makes a
#: more encouraging demo than exact mode's blanket 0% for the same pile.
DEMO_MODE = "shape"

#: Builds shown in the demo summary (the fixture catalog has five).
DEMO_TOP = 5

#: Design brief for the opt-in generation stage.
DEMO_THEME = "a small demo car"

#: The exact header rebrickable.com's part-list import expects.
EXPORT_HEADER = ("part_num", "color_id", "quantity")

#: File suffixes treated as photos when a caller-supplied photos_dir
#: is scanned.  Anything else (sidecars, notes) must not reach the
#: identifier.
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})


# ---- the built-in demo photos ---------------------------------------------
#
# The demo's "photos" are synthetic paths: StaticDetector never opens
# files, and the built-in identifier keys off the *name*, so no image
# data exists anywhere.  The finds are chosen so the demo tells a
# story: one fixture build exactly covered (READY, no substitution),
# three covered through color swaps, one short by half — every
# recommendation state the matcher can produce.


@dataclass(frozen=True)
class _DemoFind:
    """One part the fake identifier "recognizes", plus its accept decision.

    Brickognize reports shape (part + name + score) but not color or
    count; a demo has no reviewer, so the scripted find carries the
    review decision (``color_id``, ``quantity``) next to the
    recognition result.
    """

    part_num: str
    name: str
    score: float
    color_id: str
    quantity: int


@dataclass(frozen=True)
class _DemoPhoto:
    """One built-in demo photo: its name, regions, and scripted finds."""

    name: str
    regions: tuple[tuple[int, int, int, int], ...]
    finds: tuple[_DemoFind, ...]


_DEMO_PHOTOS: tuple[_DemoPhoto, ...] = (
    _DemoPhoto(
        name="pile-1.jpg",
        regions=((10, 10, 120, 90), (150, 30, 100, 80)),
        finds=(
            _DemoFind("3001", "Brick 2 x 4", 0.93, color_id="4", quantity=2),
            _DemoFind("3003", "Brick 2 x 2", 0.88, color_id="0", quantity=4),
        ),
    ),
    _DemoPhoto(
        name="pile-2.jpg",
        regions=((20, 15, 110, 70), (140, 40, 90, 60)),
        finds=(
            _DemoFind("3001", "Brick 2 x 4", 0.91, color_id="15", quantity=1),
            _DemoFind("3004", "Brick 1 x 2", 0.86, color_id="1", quantity=3),
        ),
    ),
)

_DEMO_PHOTOS_BY_NAME: dict[str, _DemoPhoto] = {
    spec.name: spec for spec in _DEMO_PHOTOS
}


@dataclass(frozen=True)
class DemoReport:
    """Facts about one completed demo run.

    db_path        -- where the collection lives on disk afterwards
    import_report  -- the catalog-import stage (files/rows/skipped/notes)
    photos         -- photos scanned
    candidates     -- part candidates the identifier produced
    accepted       -- inventory lines the auto-accept stage wrote,
                      in decision order
    inventory      -- the collection after scanning, in db order
                      (part_num, color_id)
    ranked         -- recommendations, best first (DEMO_MODE, DEMO_TOP)
    generation     -- the LLM stage's result, or None when no client
                      was given (the stage is opt-in)

    Rendering lives in :func:`render_demo_report`, report.py-style:
    the dataclass carries facts, a function turns them into text.
    """

    db_path: str
    import_report: ImportReport
    photos: int
    candidates: int
    accepted: tuple[InventoryItem, ...]
    inventory: tuple[InventoryItem, ...]
    ranked: tuple[RankedBuild, ...]
    generation: GenerationResult | None = None


def run_demo(
    db_path: str | os.PathLike,
    photos_dir: str | os.PathLike | None = None,
    identifier: Callable[[str | os.PathLike], list[PartCandidate]] | None = None,
    client: object | None = None,
    csv_dir: str | os.PathLike | None = None,
    theme: str | None = None,
) -> DemoReport:
    """Run the whole pipeline offline and print a summary.

    Args:
        db_path: SQLite file for the catalog + collection.  The demo
            creates it, fills it, and leaves it on disk — that database
            is the run's deliverable, and it opens as a perfectly
            ordinary brickfolio db afterwards.
        photos_dir: directory of photos to scan instead of the
            built-in demo photos (image files, sorted by name).
        identifier: any callable shaped like
            ``brickfolio.vision.brickognize.identify_part``.  ``None``
            uses the deterministic built-in fake (no network).
        client: anything with ``chat(messages) -> str`` (Prompt 9's
            contract) to run the constrained-generation stage; ``None``
            skips the stage entirely.
        csv_dir: Rebrickable download layout to import instead of the
            fixture catalog.
        theme: design brief for the generation stage; ``None`` uses
            :data:`DEMO_THEME`.

    Returns:
        A :class:`DemoReport`; the summary is printed (Prompt 10's
        "printed summary") and also derivable via
        :func:`render_demo_report`.
    """
    db = Database(db_path)
    try:
        stage_import = import_catalog(
            # NOTE: Path() normalizes the str | PathLike union (and the
            # already-Path default) into the str | Path import_catalog takes.
            db,
            Path(csv_dir) if csv_dir is not None else DEFAULT_CATALOG_DIR,
        )
        session, found = _scan_stage(db, photos_dir, identifier)
        inventory = db.list_user_parts()
        ranked = recommend_builds(db, mode=DEMO_MODE, top=DEMO_TOP)
        generation = None
        if client is not None:
            # WHY the materialized inventory and not the db: the
            # generator is transport-agnostic and takes the parts it
            # must respect, exactly like its own tests pass them.
            generation = generate_build(
                inventory, DEMO_THEME if theme is None else theme, client
            )
    finally:
        db.close()

    report = DemoReport(
        db_path=str(db_path),
        import_report=stage_import,
        photos=len(session.photos),
        candidates=found,
        accepted=tuple(session.accepted),
        inventory=tuple(inventory),
        ranked=tuple(ranked),
        generation=generation,
    )
    # The one deliberate print in the package: the printed summary is
    # the demo's deliverable (PROMPTS.md, Prompt 10).  Everything else
    # returns values and lets the CLI decide what to show.
    print(render_demo_report(report))
    return report


def export_rebrickable_csv(db: Database, path: str | os.PathLike) -> int:
    """Write the user's collection as a Rebrickable part-list CSV.

    ``user_parts`` is dumped with the exact header
    ``part_num,color_id,quantity`` that rebrickable.com's part-list
    import expects: one row per inventory line, in the db's
    deterministic (part_num, color_id) order.

    Returns:
        The number of part rows written (header excluded), so the CLI
        can report it.
    """
    items = db.list_user_parts()
    with open(path, "w", newline="", encoding="utf-8") as handle:
        # WHY newline="": csv.writer owns line endings (its dialect
        # uses \r\n, the CSV RFC's terminator); text mode's implicit
        # \n translation would corrupt them on Windows.
        writer = csv.writer(handle)
        writer.writerow(EXPORT_HEADER)
        for item in items:
            writer.writerow((item.part_num, item.color_id, item.quantity))
    return len(items)


def render_demo_report(report: DemoReport) -> str:
    """The demo's printed summary, one line per pipeline stage.

    The recommendation block reuses
    :func:`brickfolio.report.render_recommendations` — report.py owns
    the string shapes — so restyling that renderer restyles the demo
    too.
    """
    stage_import = report.import_report
    total_pieces = sum(item.quantity for item in report.inventory)
    lines = [
        f"brickfolio demo - database: {report.db_path}",
        (
            f"catalog: {stage_import.files} file(s), "
            f"{stage_import.rows} row(s) imported, "
            f"{stage_import.skipped} skipped, "
            f"{len(stage_import.notes)} missing"
        ),
        (
            f"scan: {report.photos} photo(s) -> "
            f"{report.candidates} candidate(s), "
            f"{len(report.accepted)} accepted -> "
            f"{len(report.inventory)} inventory line(s), "
            f"{total_pieces} part(s) total"
        ),
        f"recommendations ({DEMO_MODE} mode, top {DEMO_TOP}):",
        render_recommendations(list(report.ranked)),
        _generation_line(report.generation),
    ]
    return "\n".join(lines)


# ---- private helpers ---------------------------------------------------------


def _scan_stage(
    db: Database,
    photos_dir: str | os.PathLike | None,
    identifier: Callable[[str | os.PathLike], list[PartCandidate]] | None,
) -> tuple[ScanSession, int]:
    """Scan the demo photos and auto-accept every candidate.

    Returns (session, candidates_found).  A demo has no reviewer, so
    every candidate is confirmed — with the color/quantity scripted
    for that (photo, part), or the session defaults (black, 1) for
    candidates the script does not know, e.g. a real identifier's.
    """
    session = ScanSession(db)
    for photo_path, regions in _demo_photo_plan(photos_dir):
        session.add_photo(
            photo_path,
            StaticDetector(regions),
            identifier if identifier is not None else _demo_identifier,
        )
    # WHY a snapshot first: accept() pops pending candidates as it
    # goes, so the (photo, candidate) pairs must be captured before the
    # first accept to still know which photo each candidate came from.
    pending = [
        (entry.photo, candidate)
        for entry in session.photos
        for candidate in list(entry.candidates)
    ]
    for photo, candidate in pending:
        color_id, quantity = _accept_decision(photo, candidate.part_num)
        # Flat index 0 is always the oldest pending candidate — the
        # snapshot's next entry — so accepting index 0 repeatedly
        # drains the session in snapshot order.
        session.accept(0, color_id=color_id, quantity=quantity)
    return session, len(pending)


def _demo_photo_plan(
    photos_dir: str | os.PathLike | None,
) -> list[tuple[str, tuple[tuple[int, int, int, int], ...]]]:
    """The scan plan: [(photo_path, regions)], in scan order.

    ``photos_dir=None`` scans the built-in demo photos.  Their paths
    are synthetic (StaticDetector never opens files), so the demo needs
    no image data on disk, and the session's whole-photo fallback
    guarantees exactly one identifier call per photo — with or without
    Pillow (see ``_crop_regions`` in vision.session).

    A caller-supplied ``photos_dir`` is scanned file by file, sorted
    by name for determinism.  WHY no regions there: per-region crops
    need Pillow to open the file, so regions on real photos would make
    the demo's identifier-call pattern depend on the environment; with
    no regions the session always identifies the whole photo once.
    """
    if photos_dir is None:
        return [(spec.name, spec.regions) for spec in _DEMO_PHOTOS]
    photos = sorted(
        p
        for p in Path(photos_dir).iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )
    return [(str(p), ()) for p in photos]


def _demo_identifier(image_path: str | os.PathLike) -> list[PartCandidate]:
    """Deterministic offline stand-in for ``brickognize.identify_part``.

    Keyed by photo *name*: the demo's own scripted photos return their
    scripted finds; any other photo returns ``[]``.  WHY never invent
    parts for unknown photos: the fake exists so the demo runs offline,
    not to fabricate recognitions — an honest empty scan beats a
    made-up one.
    """
    spec = _DEMO_PHOTOS_BY_NAME.get(Path(os.fspath(image_path)).name)
    if spec is None:
        return []
    return [PartCandidate(find.part_num, find.name, find.score) for find in spec.finds]


def _accept_decision(photo: str, part_num: str) -> tuple[str | None, int]:
    """The scripted review decision for one (photo, candidate) pair.

    Returns (color_id, quantity), or (None, 1) — accept()'s own
    defaults (black, one copy) — for candidates outside the script.
    """
    spec = _DEMO_PHOTOS_BY_NAME.get(Path(photo).name)
    if spec is not None:
        for find in spec.finds:
            if find.part_num == part_num:
                return find.color_id, find.quantity
    return None, 1


def _generation_line(generation: GenerationResult | None) -> str:
    """One line about the (opt-in) generation stage."""
    if generation is None:
        return "generation: skipped (no client given)"
    if generation.valid:
        used = ", ".join(
            f"{part} (color {color}) x{qty}"
            for part, color, qty in generation.usage
        )
        suffix = f", uses {used}" if used else ""
        return f"generation: valid LDraw model in {generation.rounds} round(s){suffix}"
    # WHY only the first error: the summary is one line; the full list
    # is on the result for the CLI to print.
    reason = generation.errors[0] if generation.errors else "no errors recorded"
    return f"generation: failed after {generation.rounds} round(s): {reason}"