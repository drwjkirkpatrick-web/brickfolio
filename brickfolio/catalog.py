"""Read-side helpers for the imported Rebrickable catalog.

``brickfolio.importer`` fills the catalog tables; this module is the read
API the rest of the codebase uses: fetch a Part or Color as a frozen
dataclass from ``models.py``, or grab quick table counts.

WHY a separate module instead of call sites poking ``Database`` SQL
directly: one query layer means SQL never leaks into feature code (CLI,
builds, matcher, reports), and there is exactly one place to optimize if
lookups ever need caching.
"""

from .db import Database
from .models import Color, Part

__all__ = ["catalog_stats", "get_part", "get_color"]


def catalog_stats(db: Database) -> dict:
    """Row counts for every catalog table plus ``user_parts``.

    NOTE: delegates to ``Database.catalog_stats()`` (Prompt 1) — the
    counting SQL belongs to the schema owner. This function is the
    stable, domain-level entry point callers (e.g. the CLI's
    ``catalog stats`` command) are expected to use.
    """
    return db.catalog_stats()


def get_part(db: Database, part_num: str) -> Part | None:
    """Return the Part with this Rebrickable part number, or None."""
    row = db.conn.execute(
        "SELECT part_num, name, part_cat_id FROM parts WHERE part_num = ?",
        (part_num,),
    ).fetchone()
    if row is None:
        return None
    return Part(
        part_num=row["part_num"], name=row["name"], part_cat_id=row["part_cat_id"]
    )


def get_color(db: Database, color_id: str) -> Color | None:
    """Return the Color with this Rebrickable color id, or None.

    NOTE: color ids are TEXT (Rebrickable's include "-1" for Unknown),
    so they are never coerced through int(); ``is_trans`` is stored as
    the SQLite integers 0/1 and becomes a real bool here.
    """
    row = db.conn.execute(
        "SELECT id, name, rgb, is_trans FROM colors WHERE id = ?",
        (color_id,),
    ).fetchone()
    if row is None:
        return None
    return Color(
        id=str(row["id"]),
        name=row["name"],
        rgb=row["rgb"],
        is_trans=bool(row["is_trans"]),
    )