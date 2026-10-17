"""SQLite storage layer for brickfolio.

NOTE: The schema covers the whole pipeline from day one — Rebrickable-style
catalog tables, user_parts for the collection, and a meta table for
bookkeeping — so later prompts never need migrations.

WHY Rebrickable table/column names: Rebrickable's public CSV downloads are
the canonical LEGO catalog. Mirroring their layout makes the importer
(Prompt 2) a straight copy and lets data cross-reference rebrickable.com.

WHY no foreign key on user_parts: the user can inventory parts before the
catalog is imported (e.g. straight from a Brickognize scan), so the
collection must stand alone.
"""

import sqlite3
from pathlib import Path
from typing import List, Optional

from .models import InventoryItem

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS colors (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    rgb      TEXT NOT NULL DEFAULT '000000',
    is_trans INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS part_categories (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parts (
    part_num    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    part_cat_id INTEGER,
    FOREIGN KEY (part_cat_id) REFERENCES part_categories (id)
);

CREATE TABLE IF NOT EXISTS themes (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id INTEGER
);

CREATE TABLE IF NOT EXISTS sets (
    set_num   TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    year      INTEGER,
    theme_id  INTEGER,
    num_parts INTEGER
);

CREATE TABLE IF NOT EXISTS inventories (
    id      INTEGER PRIMARY KEY,
    set_num TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (set_num) REFERENCES sets (set_num)
);

CREATE TABLE IF NOT EXISTS inventory_parts (
    inventory_id INTEGER NOT NULL,
    part_num     TEXT NOT NULL,
    color_id     TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    is_spare     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (inventory_id, part_num, color_id, is_spare),
    FOREIGN KEY (inventory_id) REFERENCES inventories (id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_parts_part
    ON inventory_parts (part_num);

CREATE TABLE IF NOT EXISTS user_parts (
    part_num TEXT NOT NULL,
    color_id TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (part_num, color_id)
);
"""

# Catalog tables in FK-safe import order (Prompt 2 fills them).
CATALOG_TABLES = (
    "part_categories",
    "themes",
    "colors",
    "parts",
    "sets",
    "inventories",
    "inventory_parts",
)


class Database:
    """Thin sqlite3 wrapper; one instance owns one connection."""

    def __init__(self, path):
        # NOTE: ":memory:" support — Path(":memory:").parent is ".", and
        # mkdir(exist_ok=True) on "." is a no-op, so this is safe.
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---- meta bookkeeping -------------------------------------------------

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---- user parts (the collection) ---------------------------------------

    def add_user_parts(self, items: List[InventoryItem], replace: bool = False) -> int:
        """Add inventory lines; accumulate quantities unless replace=True.

        Returns the number of lines processed.
        """
        for item in items:
            if item.quantity < 0:
                raise ValueError(
                    f"negative quantity for {item.part_num}: {item.quantity}"
                )
            self.conn.execute(
                "INSERT INTO user_parts (part_num, color_id, quantity) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (part_num, color_id) DO UPDATE SET quantity = "
                + (
                    "excluded.quantity" if replace
                    else "quantity + excluded.quantity"
                ),
                (item.part_num, item.color_id, item.quantity),
            )
        self.conn.commit()
        return len(items)

    def remove_user_parts(self, part_num: str, color_id: str, quantity: int = 1) -> int:
        """Remove up to `quantity` copies; delete the row when it hits 0.

        Returns the remaining quantity. Raises ValueError when the line does
        not exist or stock is insufficient.
        """
        row = self.conn.execute(
            "SELECT quantity FROM user_parts WHERE part_num = ? AND color_id = ?",
            (part_num, color_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"no {part_num} in color {color_id} in inventory")
        if row["quantity"] < quantity:
            raise ValueError(
                f"only {row['quantity']} x {part_num} (color {color_id}) in "
                f"inventory, cannot remove {quantity}"
            )
        remaining = row["quantity"] - quantity
        if remaining == 0:
            self.conn.execute(
                "DELETE FROM user_parts WHERE part_num = ? AND color_id = ?",
                (part_num, color_id),
            )
        else:
            self.conn.execute(
                "UPDATE user_parts SET quantity = ? "
                "WHERE part_num = ? AND color_id = ?",
                (remaining, part_num, color_id),
            )
        self.conn.commit()
        return remaining

    def list_user_parts(self) -> List[InventoryItem]:
        rows = self.conn.execute(
            "SELECT part_num, color_id, quantity FROM user_parts "
            "ORDER BY part_num, color_id"
        ).fetchall()
        return [
            InventoryItem(r["part_num"], r["color_id"], r["quantity"]) for r in rows
        ]

    def total_user_parts(self) -> int:
        # NOTE: SUM() returns NULL on an empty table — coalesce to 0.
        row = self.conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS total FROM user_parts"
        ).fetchone()
        return int(row["total"])

    # ---- catalog helpers (filled by Prompt 2) ------------------------------

    def catalog_stats(self) -> dict:
        """Row counts for the catalog tables + user_parts, for quick sanity."""
        stats = {}
        for table in CATALOG_TABLES + ("user_parts",):
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            stats[table] = int(row["n"])
        return stats