"""First-class builds over the imported Rebrickable catalog.

A *build* is anything brickfolio can treat as a parts list to match against
the user's collection. Today that means official sets — rows in ``sets``
that also have a parts list in ``inventories``/``inventory_parts`` — but the
``Build`` contract (see models.py) already carries a ``source`` field, so
MOCs can join this same catalog later without touching the callers here.

WHY a set needs an inventory to count as a build: ``sets.num_parts`` is
just catalog metadata. Without an ``inventories`` row there is no parts list
to show, match, or recommend, so such a set is skipped here rather than
surfaced as a build with unknowable contents.

NOTE: ``build_parts`` reads the inventory with the highest ``version``
(highest inventory id breaks ties) — the same "current parts list" rule
rebrickable.com applies when a set's inventory was re-issued.

NOTE: spare lines (``is_spare = 1``) are included in ``build_parts`` output
on purpose: the matcher (Prompt 4) decides how spares affect coverage, so
the catalog must not silently drop them.
"""

import sqlite3
from typing import Iterator, List, Optional

from .db import Database
from .models import Build, BuildPart

__all__ = [
    "build_parts",
    "count_builds",
    "get_build",
    "iter_builds",
    "search_builds",
]

# The Build-projected columns shared by every query below.
# NOTE: COALESCE — sets.num_parts is nullable (an empty CSV cell imports as
# NULL), but Build.num_parts is an int by contract.
_BUILD_COLUMNS = (
    "s.set_num AS build_num, s.name AS name, COALESCE(s.num_parts, 0) AS num_parts"
)

# WHY EXISTS instead of a JOIN: a set with several inventory versions must
# still yield exactly one Build; EXISTS keeps the set row unique without a
# GROUP BY.
_HAS_INVENTORY = "EXISTS (SELECT 1 FROM inventories i WHERE i.set_num = s.set_num)"


def count_builds(db: Database) -> int:
    """Number of builds in the catalog (sets that have an inventory)."""
    row = db.conn.execute(
        f"SELECT COUNT(*) AS n FROM sets s WHERE {_HAS_INVENTORY}"
    ).fetchone()
    return int(row["n"])


def iter_builds(db: Database) -> Iterator[Build]:
    """Yield every build, ordered by build_num (the set number).

    NOTE: lazy on purpose — the real Rebrickable catalog has tens of
    thousands of sets, and callers (CLI listing, recommender) stream them.
    """
    rows = db.conn.execute(
        f"SELECT {_BUILD_COLUMNS} FROM sets s "
        f"WHERE {_HAS_INVENTORY} ORDER BY s.set_num"
    )
    for row in rows:
        yield _row_to_build(row)


def get_build(db: Database, build_num: str) -> Optional[Build]:
    """The build with exactly this build_num (e.g. ``"75192-1"``), or None.

    None both for unknown set numbers and for sets that exist in the catalog
    but have no inventory — neither is a build (see module docstring).
    """
    row = db.conn.execute(
        f"SELECT {_BUILD_COLUMNS} FROM sets s "
        f"WHERE s.set_num = ? AND {_HAS_INVENTORY}",
        (build_num,),
    ).fetchone()
    return None if row is None else _row_to_build(row)


def build_parts(db: Database, build_num: str) -> List[BuildPart]:
    """The build's parts list, spare lines included.

    Reads the inventory with the highest version (highest id on a tie), so a
    re-issued parts list supersedes the old one.

    Returns [] for an unknown build_num; callers that need to tell "unknown
    build" from "known build with an empty parts list" use get_build first.
    """
    rows = db.conn.execute(
        """
        SELECT ip.part_num, ip.color_id, ip.quantity, ip.is_spare
        FROM inventory_parts ip
        WHERE ip.inventory_id = (
            SELECT i.id FROM inventories i
            WHERE i.set_num = ?
            ORDER BY i.version DESC, i.id DESC
            LIMIT 1
        )
        ORDER BY ip.part_num, ip.color_id, ip.is_spare
        """,
        (build_num,),
    ).fetchall()
    return [
        BuildPart(
            part_num=row["part_num"],
            color_id=row["color_id"],
            quantity=int(row["quantity"]),
            # NOTE: is_spare is stored as SQLite INTEGER 0/1; the model
            # contract speaks bool.
            is_spare=bool(row["is_spare"]),
        )
        for row in rows
    ]


def search_builds(db: Database, query: str, limit: int = 20) -> List[Build]:
    """Builds whose set number or name contains ``query``.

    Case-insensitive for ASCII (SQLite's default LIKE behaviour). ``%`` and
    ``_`` in the query match literally, never as wildcards. Results are
    ordered by build_num and capped at ``limit``.
    """
    pattern = "%" + _escape_like(query) + "%"
    rows = db.conn.execute(
        f"""
        SELECT {_BUILD_COLUMNS} FROM sets s
        WHERE {_HAS_INVENTORY}
          AND (s.set_num LIKE ? ESCAPE '\\' OR s.name LIKE ? ESCAPE '\\')
        ORDER BY s.set_num
        LIMIT ?
        """,
        (pattern, pattern, limit),
    ).fetchall()
    return [_row_to_build(row) for row in rows]


def _escape_like(text: str) -> str:
    # WHY: user input is data, not a pattern — "100%" or "60_92" must not
    # suddenly match half the catalog. The backslash is escaped first so
    # user input cannot un-escape a wildcard we escaped.
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_build(row: sqlite3.Row) -> Build:
    """sqlite3.Row → Build; every build in the catalog is a set today."""
    return Build(
        build_num=row["build_num"],
        name=row["name"],
        num_parts=int(row["num_parts"]),
        # NOTE: source="moc" arrives with a later prompt; hard-code the only
        # source the catalog can currently produce.
        source="set",
    )