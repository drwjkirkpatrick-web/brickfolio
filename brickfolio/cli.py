"""Command-line interface for brickfolio.

NOTE: Wiring plan — each later prompt gains its commands here:
  Prompt 2:    catalog import|stats
  Prompts 3+5: builds list|search|recommend
  Prompts 6+7: photo identify|scan
  Prompt 9:    generate
  Prompt 10:   demo + export
This file is parent-owned (shared-file rule); subagents request wiring in
their summaries instead of editing it.

WHY nested subparsers for `inventory`: it keeps
`brickfolio inventory add ...` natural while other top-level commands grow
the same way.
"""

import argparse
import sys
from typing import List, Optional

from .db import Database
from .models import InventoryItem

DEFAULT_DB = "data/brickfolio.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brickfolio",
        description="Photo-inventory your LEGO pile and find what you can build.",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help="Path to SQLite database (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # inventory ---------------------------------------------------------------
    inv = sub.add_parser("inventory", help="Manage your parts collection")
    inv_sub = inv.add_subparsers(dest="inventory_command", required=True)

    add_p = inv_sub.add_parser("add", help="Add parts to your collection")
    add_p.add_argument("part_num")
    add_p.add_argument("color_id")
    add_p.add_argument("quantity", type=int)

    inv_sub.add_parser("list", help="List your collection")

    rm_p = inv_sub.add_parser("remove", help="Remove parts from your collection")
    rm_p.add_argument("part_num")
    rm_p.add_argument("color_id")
    rm_p.add_argument("quantity", type=int, nargs="?", default=1)

    # stubs for later prompts ---------------------------------------------------
    sub.add_parser("catalog", help="Import/query the Rebrickable catalog (Prompt 2)")
    sub.add_parser("builds", help="Browse buildable sets/MOCs (Prompts 3+5)")
    sub.add_parser("photo", help="Identify parts from photos (Prompts 6+7)")
    sub.add_parser(
        "generate", help="Generate builds constrained to your parts (Prompt 9)"
    )
    sub.add_parser("demo", help="Run the end-to-end demo (Prompt 10)")

    return parser


def cmd_inventory(args, db: Database) -> int:
    if args.inventory_command == "add":
        db.add_user_parts([InventoryItem(args.part_num, args.color_id, args.quantity)])
        print(f"Added {args.quantity} x {args.part_num} (color {args.color_id}).")
        return 0

    if args.inventory_command == "list":
        items = db.list_user_parts()
        if not items:
            print("Inventory is empty. Try: brickfolio inventory add 3001 0 5")
            return 0
        print(f"{'Part':<12} {'Color':<8} {'Qty':>5}")
        for item in items:
            print(f"{item.part_num:<12} {item.color_id:<8} {item.quantity:>5}")
        print(f"{db.total_user_parts()} parts in {len(items)} lines.")
        return 0

    if args.inventory_command == "remove":
        remaining = db.remove_user_parts(
            args.part_num, args.color_id, args.quantity
        )
        print(
            f"Removed {args.quantity} x {args.part_num} (color {args.color_id}); "
            f"{remaining} remaining."
        )
        return 0

    return 1  # unreachable; argparse enforces the subcommand


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database(args.db)
    try:
        if args.command == "inventory":
            return cmd_inventory(args, db)
        print(f"'{args.command}' is not implemented yet — arrives in a later prompt.")
        return 0
    except ValueError as exc:
        # NOTE: user-facing errors (bad quantities, missing lines) surface as
        # ValueError from the storage layer; print and exit non-zero.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())