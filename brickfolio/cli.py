"""Command-line interface for brickfolio.

NOTE: Wiring plan — each later prompt gains its commands here:
  Prompt 2:    catalog import|stats            (wired)
  Prompts 3+5: builds list|search|show|recommend (list/search/show wired; recommend lands with P5)
  Prompts 6+7: photo identify|scan             (wired)
  Prompt 9:    generate                        (wired)
  Prompt 10:   demo + inventory export         (wired)
This file is parent-owned (shared-file rule); subagents request wiring in
their summaries instead of editing it.

WHY nested subparsers per topic: keeps `brickfolio catalog import ...`
natural while each topic grows its own command set.
"""

import argparse
import itertools
import sys
from typing import List, Optional

from .builds import build_parts, get_build, iter_builds, search_builds
from .db import Database
from .generator import generate_build
from .importer import import_catalog
from .llm import ChatClient
from .models import InventoryItem
from .pipeline import export_rebrickable_csv, run_demo
from .report import recommend_builds, render_recommendations
from .vision.brickognize import BrickognizeError, identify_part
from .vision.detector import DetectorError, ManualRegionsDetector, StaticDetector
from .vision.session import ScanSession

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

    exp_p = inv_sub.add_parser(
        "export", help="Export your collection as a Rebrickable part-list CSV"
    )
    exp_p.add_argument("path")

    # catalog (Prompt 2) --------------------------------------------------------
    cat = sub.add_parser("catalog", help="Import/query the Rebrickable catalog")
    cat_sub = cat.add_subparsers(dest="catalog_command", required=True)

    imp_p = cat_sub.add_parser(
        "import", help="Import Rebrickable CSV downloads from a directory"
    )
    imp_p.add_argument(
        "--dir", required=True, help="Directory holding the Rebrickable *.csv files"
    )
    cat_sub.add_parser("stats", help="Row counts for catalog + collection tables")

    # builds (Prompt 3) ----------------------------------------------------------
    builds = sub.add_parser("builds", help="Browse buildable sets/MOCs")
    builds_sub = builds.add_subparsers(dest="builds_command", required=True)

    list_p = builds_sub.add_parser("list", help="List known builds")
    list_p.add_argument("--limit", type=int, default=20)
    srch_p = builds_sub.add_parser("search", help="Search builds by number or name")
    srch_p.add_argument("query")
    srch_p.add_argument("--limit", type=int, default=20)
    show_p = builds_sub.add_parser("show", help="Show a build and its parts list")
    show_p.add_argument("build_num")

    rec_p = builds_sub.add_parser(
        "recommend", help="What can I build with the parts I have?"
    )
    rec_p.add_argument("--mode", default="shape", choices=["exact", "shape"])
    rec_p.add_argument("--top", type=int, default=10)
    rec_p.add_argument(
        "--verbose", action="store_true", help="Show top missing-part lines per build"
    )

    # photo (Prompts 6+7) ----------------------------------------------------------
    photo = sub.add_parser("photo", help="Identify parts from photos")
    photo_sub = photo.add_subparsers(dest="photo_command", required=True)

    id_p = photo_sub.add_parser("identify", help="Identify the part in a photo")
    id_p.add_argument("image")

    scan_p = photo_sub.add_parser("scan", help="Scan photo(s) into the collection")
    scan_p.add_argument("images", nargs="+")
    scan_p.add_argument(
        "--regions",
        action="store_true",
        help="Use <image>.regions.json sidecars for per-part regions",
    )
    scan_p.add_argument(
        "--all",
        action="store_true",
        help="Accept every recognized candidate (unknown color -> Black)",
    )
    scan_p.add_argument(
        "--accept",
        default=None,
        help="Comma-separated candidate indices (from the printed list) to accept",
    )
    scan_p.add_argument(
        "--session", default=None, help="Save the review session to this JSON path"
    )

    # generate (Prompt 9) --------------------------------------------------------
    gen = sub.add_parser(
        "generate", help="Generate a build constrained to your parts (local LLM)"
    )
    gen.add_argument("theme")
    gen.add_argument("--rounds", type=int, default=3)
    gen.add_argument("--mode", default="exact", choices=["exact", "shape"])
    gen.add_argument(
        "--base-url", default=None,
        help="OpenAI-compatible server URL (env BRICKFOLIO_LLM_BASE_URL)",
    )
    gen.add_argument(
        "--model", default=None, help="Model name (env BRICKFOLIO_LLM_MODEL)"
    )
    gen.add_argument(
        "--out", default=None, help="Write the generated LDraw model to this file"
    )

    # demo (Prompt 10) -------------------------------------------------------------
    demo_p = sub.add_parser(
        "demo", help="Run the offline end-to-end demo (fixtures, no network)"
    )
    demo_p.add_argument(
        "--csv-dir", default=None,
        help="Catalog CSV dir override (default: bundled fixtures)",
    )
    demo_p.add_argument(
        "--theme", default=None,
        help="Also generate a build for this theme (requires a local LLM endpoint)",
    )

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

    if args.inventory_command == "export":
        count = export_rebrickable_csv(db, args.path)
        print(f"Exported {count} part line(s) to {args.path}.")
        return 0

    return 1  # unreachable; argparse enforces the subcommand


def cmd_catalog(args, db: Database) -> int:
    if args.catalog_command == "import":
        report = import_catalog(db, args.dir)
        # NOTE: ImportReport invariants — rows+skipped = all data rows seen;
        # files + len(notes) = all expected files (missing file = one note).
        print(
            f"Imported {report.rows} rows from {report.files} files "
            f"({report.skipped} skipped)."
        )
        for note in report.notes:
            print(f"  note: {note}")
        return 0

    if args.catalog_command == "stats":
        stats = db.catalog_stats()
        width = max(len(table) for table in stats)
        for table, n in stats.items():
            print(f"{table:<{width}}  {n:>8}")
        return 0

    return 1


def _print_build_rows(rows) -> None:
    print(f"{'Build':<12} {'Name':<32} {'Parts':>6}")
    for build in rows:
        print(f"{build.build_num:<12} {build.name:<32} {build.num_parts:>6}")


def cmd_builds(args, db: Database) -> int:
    if args.builds_command == "list":
        rows = list(itertools.islice(iter_builds(db), args.limit))
        if not rows:
            print("No builds in the catalog yet. Try: brickfolio catalog import --dir PATH")
            return 0
        _print_build_rows(rows)
        return 0

    if args.builds_command == "search":
        rows = search_builds(db, args.query, limit=args.limit)
        if not rows:
            print(f"No builds match '{args.query}'.")
            return 0
        _print_build_rows(rows)
        return 0

    if args.builds_command == "show":
        build = get_build(db, args.build_num)
        if build is None:
            print(f"Error: no build '{args.build_num}'", file=sys.stderr)
            return 1
        parts = build_parts(db, args.build_num)
        print(f"{build.build_num}  {build.name}  ({build.num_parts} parts, {build.source})")
        if parts:
            print(f"{'Part':<10} {'Color':<8} {'Qty':>4}  Spare")
            for p in parts:
                spare = "spare" if p.is_spare else ""
                print(f"{p.part_num:<10} {p.color_id:<8} {p.quantity:>4}  {spare}")
        return 0

    if args.builds_command == "recommend":
        ranked = recommend_builds(db, mode=args.mode, top=args.top)
        if not ranked:
            print(
                "No builds in the catalog yet. "
                "Try: brickfolio catalog import --dir PATH"
            )
            return 0
        # NOTE: render_recommendations already ends with a newline.
        print(render_recommendations(ranked, verbose=args.verbose), end="")
        return 0

    return 1


def _parse_indices(raw: str) -> List[int]:
    """'2,5' -> [2, 5]; raises ValueError on anything non-numeric."""
    values = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    return [int(v) for v in values]


def _pending_count(session: ScanSession) -> int:
    return sum(len(entry.candidates) for entry in session.photos)


def cmd_photo(args, db: Database) -> int:
    if args.photo_command == "identify":
        try:
            candidates = identify_part(args.image)
        except BrickognizeError as exc:
            # WHY caught here, not in main(): it is photo-specific and should
            # not mask other error types.
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not candidates:
            print("No parts recognized in that image.")
            return 0
        print(f"{'Part':<10} {'Name':<28} {'Score':>5}")
        for c in candidates:
            print(f"{c.part_num:<10} {c.name:<28} {c.score:>5.2f}")
        return 0

    if args.photo_command == "scan":
        # Fail fast on a malformed --accept list before doing any work.
        if args.accept is not None:
            try:
                accept_indices = _parse_indices(args.accept)
            except ValueError:
                print(f"Error: --accept must be comma-separated integers, got '{args.accept}'",
                      file=sys.stderr)
                return 1
        else:
            accept_indices = []

        detector = ManualRegionsDetector() if args.regions else StaticDetector()
        session = ScanSession(db)
        failed = 0
        for image in args.images:
            try:
                session.add_photo(image, detector, identify_part)
            except (BrickognizeError, DetectorError) as exc:
                # WHY per-photo: one bad photo (API error, missing sidecar)
                # must not lose the scans that worked.
                print(f"Error scanning {image}: {exc}", file=sys.stderr)
                failed += 1

        # Review listing — the [i] numbers are exactly accept()'s indices.
        print(session.summary())

        if args.all:
            # Accept every pending candidate. WHY descending: accept() pops
            # the candidate, so high indices disappear first and every index
            # printed above stays valid while we work down the list.
            accept_indices = list(range(_pending_count(session) - 1, -1, -1))

        accepted = 0
        for index in sorted(accept_indices, reverse=True):
            try:
                session.accept(index)
                accepted += 1
            except (IndexError, ValueError) as exc:
                print(f"Error accepting index {index}: {exc}", file=sys.stderr)
                failed += 1

        if accepted or args.all:
            print(f"Accepted {accepted} candidate(s) into the collection.")
            print(session.summary())
        if args.session:
            session.save(args.session)
            print(f"Session saved to {args.session}")
        return 1 if failed else 0

    return 1


def _fmt_num(value: float) -> str:
    """LDraw numbers without trailing noise: 24.0 -> '24', 0.5 -> '0.5'."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _serialize_ldr(model) -> str:
    """Render a parsed LDrawModel back to type-1 lines (LeoCAD/LPub3D-openable).

    NOTE: rebuilt from the *validated* parts — the checked geometry is the
    source of truth, not the LLM's raw prose. The 4th matrix row is emitted
    as the standard '0 0 1'.
    """
    lines = [f"0 brickfolio: {model.name}"]
    for part in model.parts:
        x, y, z = part.position
        values = " ".join(_fmt_num(v) for v in (x, y, z))
        values += " " + " ".join(_fmt_num(v) for row in part.matrix for v in row)
        lines.append(f"1 {part.color_id} {values} 0 0 1 {part.part_num}.dat")
    return "\n".join(lines) + "\n"


def cmd_generate(args, db: Database) -> int:
    inventory = db.list_user_parts()
    if not inventory:
        print("Inventory is empty — add parts first (or photo scan), then generate.")
        return 1

    # NOTE: None base-url/model fall through to env BRICKFOLIO_LLM_* then the
    # local llama-server default baked into ChatClient.
    client = ChatClient(base_url=args.base_url, model=args.model)
    result = generate_build(
        inventory, args.theme, client, max_rounds=args.rounds, mode=args.mode
    )

    status = "VALID — buildable from your parts" if result.valid else "INVALID"
    print(f"Generation: {status} ({result.rounds} round(s), mode {args.mode})")
    if result.usage:
        print(f"{'Part':<10} {'Color':<8} {'Qty':>4}")
        for part, color, qty in result.usage:
            print(f"{part:<10} {color:<8} {qty:>4}")
    for error in result.errors:
        print(f"  problem: {error}")
    if result.valid and result.model is not None:
        ldr = _serialize_ldr(result.model)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(ldr)
            print(f"Model written to {args.out}")
        else:
            print(ldr)
    return 0 if result.valid else 1


def cmd_demo(args, db: Database) -> int:
    # NOTE: run_demo opens its own Database from the path, scans a scripted
    # fixture pile offline, and prints the stage-by-stage summary itself.
    # A client is built only when --theme asks for generation; without it the
    # demo stays fully offline.
    client = ChatClient() if args.theme else None
    run_demo(db.path, csv_dir=args.csv_dir, theme=args.theme, client=client)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database(args.db)
    try:
        if args.command == "inventory":
            return cmd_inventory(args, db)
        if args.command == "catalog":
            return cmd_catalog(args, db)
        if args.command == "builds":
            return cmd_builds(args, db)
        if args.command == "photo":
            return cmd_photo(args, db)
        if args.command == "generate":
            return cmd_generate(args, db)
        if args.command == "demo":
            return cmd_demo(args, db)
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