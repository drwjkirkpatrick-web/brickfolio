# brickfolio — Build Prompts

**Goal:** photograph a LEGO pile → identify the parts (Brickognize API) →
maintain a Rebrickable-compatible inventory → present builds possible from
those parts → *generate* new LDraw builds constrained to the parts you own.

Open-source take on the closed-source Brickit concept, researched 2026-10-17.

## Conventions (apply to every prompt)

- Python 3.14, project venv at `.venv`. Test:
  `.venv/bin/python -m pytest -q` (single file:
  `.venv/bin/python -m pytest tests/test_<name>.py -q`).
- **stdlib only** — no new dependencies without parent approval.
- `brickfolio/models.py` is the shared contract: frozen dataclasses
  (`Color`, `Part`, `InventoryItem`, `BuildPart`, `Build`). Subagents never
  edit it; request changes in the task summary.
- **Shared files, parent-owned only:** `models.py`, `db.py`, `cli.py`,
  `README.md`, `Project_state.md`, `PROMPTS.md`. A subagent owns exactly the
  new files listed for its prompt — never another file.
- SQLite access only via the `Database` class (sqlite3.Row, foreign_keys ON).
- **Tests never touch the network.** Fake HTTP responses / fake detectors /
  fake LLMs at module boundaries.
- Teaching style: module docstrings everywhere; `NOTE:`/`WHY:` comments for
  non-obvious choices. No stray debug prints in modules.
- Commits are made by the parent, one per prompt, only after the full suite
  is green on disk.

## Dependency table

| # | Prompt | Needs | New files owned by subagent |
|---|--------|-------|------------------------------|
| 1 | Foundation | — | (parent: everything) |
| 2 | Catalog importer | 1 | `brickfolio/catalog.py`, `brickfolio/importer.py`, `tests/test_catalog.py`, `tests/fixtures/rebrickable/*` |
| 3 | Build catalog | 2 | `brickfolio/builds.py`, `tests/test_builds.py` |
| 4 | Matcher engine | 1 | `brickfolio/matcher.py`, `tests/test_matcher.py` |
| 5 | Recommender & reports | 3, 4 | `brickfolio/report.py`, `tests/test_report.py` |
| 6 | Brickognize client | 1 | `brickfolio/vision/__init__.py`, `brickfolio/vision/brickognize.py`, `tests/test_brickognize.py`, `tests/fixtures/brickognize/*` |
| 7 | Scan sessions | 6 | `brickfolio/vision/detector.py`, `brickfolio/vision/session.py`, `tests/test_session.py` |
| 8 | LDraw parser & validator | 1 | `brickfolio/ldraw/__init__.py`, `brickfolio/ldraw/parser.py`, `brickfolio/ldraw/validator.py`, `tests/test_ldraw_parser.py`, `tests/test_ldraw_validator.py` |
| 9 | Inventory-constrained generator | 8 | `brickfolio/llm.py`, `brickfolio/generator.py`, `tests/test_generator.py` |
| 10 | Pipeline, export, docs | all | `brickfolio/pipeline.py`, `tests/test_pipeline.py` (+ parent: CLI wiring, README, LICENSE) |

**Waves:** A = {2, 4, 6, 8} (mutually independent) → B = {3, 5, 7, 9} →
10 (parent-led integration).

## Testing strategy

- Pure-function modules (matcher, ldraw, report) carry the weightiest unit
  tests; I/O modules are tested through fakes at their boundaries.
- Fixtures over network: fixture Rebrickable mini-CSVs, fixture Brickognize
  JSON, fake detectors, scripted fake LLM, hand-written `.ldr`/`.mpd` files.
- One commit per prompt: the parent verifies the suite on disk first.

---

## Prompt 1 — Foundation *(parent)*

Package scaffold, frozen-dataclass contracts, full SQLite schema
(Rebrickable-compatible catalog tables + `user_parts` + `meta`), `Database`
CRUD for the user's collection, argparse CLI with working
`inventory add|list|remove` (other commands stubbed), pytest suite, README
stub, `Project_state.md`, this file.

**Done when:** suite green; `inventory add 3001 0 5` round-trips through
`list`; commit "Prompt 1: foundation".

## Prompt 2 — Catalog importer

Load Rebrickable's public CSV downloads into the catalog tables.

- `brickfolio/importer.py`: `import_catalog(db, csv_dir) -> ImportReport`.
  Files (Rebrickable downloads layout): `colors.csv`,
  `part_categories.csv`, `parts.csv`, `themes.csv`, `sets.csv`,
  `inventories.csv`, `inventory_parts.csv`. First line is a header. Insert
  in FK-safe order (see CATALOG_TABLES in `db.py`). Idempotent — safe to
  re-run (INSERT OR REPLACE). Malformed rows: skip, count in report, one
  stderr warning per file. Missing file: note in report, skip (no crash).
  `ImportReport` (dataclass): `files`, `rows`, `skipped`, `notes`.
- `brickfolio/catalog.py`: `catalog_stats(db)`, `get_part(db, part_num)`,
  `get_color(db, color_id)` returning models or `None`.
- `tests/fixtures/rebrickable/`: hand-written mini-CSVs (4–8 rows each)
  with exactly one malformed row somewhere to exercise the skip path.
- Tests: counts match fixtures; idempotency (import twice → same stats);
  FK-violating row skipped + counted; `get_part`/`get_color` round-trip.
- Parent wires: `catalog import --dir PATH`, `catalog stats`.

## Prompt 3 — Build catalog

First-class "builds" over the imported data (official sets now; MOCs later).

- `brickfolio/builds.py`: `count_builds(db) -> int`;
  `iter_builds(db) -> Iterator[Build]` (sets joined to inventories);
  `get_build(db, build_num) -> Optional[Build]`;
  `build_parts(db, build_num) -> List[BuildPart]` (via inventories +
  inventory_parts; include spare lines — the matcher decides how to use
  them); `search_builds(db, query, limit=20)` (LIKE on set_num/name).
- Tests run against a fixture db built by the Prompt-2 importer.
- Parent wires: `builds list`, `builds search QUERY`, `builds show NUM`.

## Prompt 4 — Matcher engine

Pure coverage math — no I/O, no db.

- `brickfolio/matcher.py`:
  - `MissingPart(part_num, color_id, needed, have)` frozen dataclass.
  - `MatchResult(coverage_pct, required_parts, covered_parts, missing,
    color_substituted)` frozen dataclass.
  - `match_build(inventory, build_parts, mode)` — mode `"exact"` matches
    (part_num, color_id); mode `"shape"` matches by part_num in any color
    and sets `color_substituted` when colors differ. Spare lines
    (`is_spare=True`) are excluded from requirements. Inventory lines are
    aggregated per key first.
  - `rank_builds(inventory, builds_with_parts, mode, top=None)` —
    `builds_with_parts` is `Iterable[(Build, List[BuildPart])]`; returns
    `RankedBuild(build, match)` sorted by coverage desc, then name.
- Tests: 100% coverage; missing-quantity math; shape-mode substitution;
  spares excluded; duplicate inventory lines aggregate; ranking order;
  empty inventory → 0%.

## Prompt 5 — Recommender & reports

Turn matcher results into human output.

- `brickfolio/report.py`:
  - `recommend_builds(db, mode="shape", top=10) -> List[RankedBuild]`
    (joins builds.build_parts + matcher).
  - `render_recommendations(ranked, verbose=False) -> str` — per build:
    name, ASCII coverage bar (10 chars; floored fill — `███████░░░ 72%`,
    only 100% renders a full bar), `READY` flag at 100%, and with
    `verbose=True` the top missing-part lines.
  - `render_missing_list(ranked_build) -> str` — full shopping list:
    part, color, needed, have.
- Tests against a fixture db: expected names/percentages present; READY
  flag correct; bar-length math; verbose truncates.
- Parent wires: `builds recommend [--mode shape|exact] [--top N]`.

## Prompt 6 — Brickognize client

Free, keyless part recognition from a photo.

- `brickfolio/vision/brickognize.py`:
  - `PartCandidate(part_num, name, score)` frozen dataclass.
  - `identify_part(image_path, endpoint="https://api.brickognize.com/predict/parts/",
    timeout=30, opener=None) -> List[PartCandidate]`.
  - Multipart/form-data POST with stdlib `urllib` only (build the MIME
    boundary by hand; content type from the file extension). The `opener`
    parameter lets tests substitute a fake response — tests never hit the
    network.
  - Parse JSON defensively: accept `items[]` entries with
    `part_num`/`id`/`num` and `name`/`description`; score as float 0–1.
    Empty/odd payloads return `[]` rather than raising.
  - `BrickognizeError` with a clear message for HTTP errors, timeouts,
    non-JSON responses.
- `tests/fixtures/brickognize/`: fixture JSON payloads (canonical shape +
  variant key shape) used by happy-path tests.
- Parent wires: `photo identify IMAGE`.

## Prompt 7 — Scan sessions

Photo → candidates → confirmed inventory, with pluggable detection.

- `brickfolio/vision/detector.py`:
  - `Region(x, y, w, h)` frozen dataclass.
  - `Detector` ABC: `detect(image_path) -> List[Region]`.
  - `ManualRegionsDetector`: reads a `<image>.regions.json` sidecar.
  - `StaticDetector(regions)`: for tests/demos.
  - WHY abstract: a real pile detector (YOLO on synthetic LDraw pile
    renders) plugs in later without touching session code.
- `brickfolio/vision/session.py`:
  - `PhotoCandidates(photo, candidates, regions)` dataclass.
  - `ScanSession`: `add_photo(image_path, detector, identifier)` —
    identifiers run per-region when Pillow is importable, else once per
    photo; candidates accumulate. `accept(index, color_id=None,
    quantity=1)` → `db.add_user_parts`; `reject(index)`;
    `save(path)` / `load(path)` (JSON); `summary() -> str`.
  - Optional Pillow via a `_HAS_PILLOW` guard for region crops; absent →
    whole-image identification. Tests must not require Pillow.
- Tests: fake detector + fake identifier drive the whole session;
  accept/reject flow updates the db; save/load round-trips.
- Parent wires: `photo scan IMAGE...`.

## Prompt 8 — LDraw parser & inventory validator

Read LDraw models; prove they are buildable from your parts.

- `brickfolio/ldraw/parser.py`:
  - `PlacedPart(line_num, color_id, part_num, position, matrix)` frozen —
    position = (x, y, z); matrix = 3x3 tuple-of-tuples.
  - `LDrawModel(name, parts: List[PlacedPart])`.
  - `parse_ldraw(text, name="model") -> LDrawModel`: line types — `0`
    meta/comment (`0 FILE`/`0 NOFILE` bound sub-files in .mpd), `1` part
    placement (color, x y z, 12 matrix values, part), others ignored v1.
    LDraw stores a 4x3 matrix; record the first 9 values as the 3x3
    rotation and ignore the 4th row (normally `0 0 1`). In .mpd files,
    `1` lines referencing a sub-model flatten with composed transforms
    (3x3 multiply + translation add).
- `brickfolio/ldraw/validator.py`:
  - `ValidationReport(ok, errors: List[str], warnings: List[str])`.
  - `validate_against_inventory(model, inventory, mode)` — every placed
    part must exist in the aggregated inventory with sufficient quantity
    (mode `"exact"` needs the color; `"shape"` ignores color and warns on
    substitution). Errors carry part, color, needed, have.
  - `check_collisions(model, bboxes) -> List[str]` — AABB overlap test;
    `bboxes` maps part_num → LDU size tuple. Unknown part → warning, not
    error. Ships a tiny default bbox table for common parts.
- Fixture `.ldr` + `.mpd` files (a few bricks each, incl. a sub-model call).
- Tests: parse counts/fields; .mpd flattening; validation errors for
  missing part / insufficient quantity / (exact mode) wrong color;
  collision detection for two overlapping placements; clean model passes.

## Prompt 9 — Inventory-constrained generator

The feature Brickit doesn't have: LLM designs constrained to your pile.

- `brickfolio/llm.py`: `ChatClient(base_url, model, timeout=60)` with
  `chat(messages, temperature=0.6) -> str` — OpenAI-compatible
  `POST {base_url}/v1/chat/completions` via stdlib urllib. Defaults from
  env: `BRICKFOLIO_LLM_BASE_URL`, `BRICKFOLIO_LLM_MODEL` (e.g. a local
  llama-server on the Jetson).
- `brickfolio/generator.py`:
  - `build_system_prompt(inventory) -> str` — "design LDraw models using
    ONLY these parts", with a compact part:quantity listing.
  - `generate_build(inventory, theme, client, max_rounds=3)
    -> GenerationResult(model, rounds, valid, usage, errors)`:
    ask for LDraw text → parse (Prompt 8) → validate → on failure feed the
    validation errors back to the LLM for a correction round → repeat
    until valid or out of rounds. `usage` lists (part, color, qty) the
    model actually uses.
- Tests: scripted `FakeChatClient` (round 1 returns a model using an
  unavailable part; round 2 returns the corrected model) → repair loop
  works, `valid=True`; max-rounds exhaustion → `valid=False` with errors;
  system prompt contains the inventory listing. No network.
- Parent wires: `generate THEME [--rounds N]` (prints model path + report).

## Prompt 10 — Pipeline, export, docs *(parent + subagent)*

- `brickfolio/pipeline.py`: `run_demo(db_path, photos_dir=None,
  identifier=None, client=None) -> DemoReport` — end-to-end: import fixture
  catalog → scan fixture photos (fake identifier when none given) →
  recommendations → optional constrained generation → printed summary.
  `export_rebrickable_csv(db, path)` — user_parts → Rebrickable part-list
  CSV (`part_num,color_id,quantity` header) for rebrickable.com imports.
- Tests: demo runs fully offline against fixtures; export matches the db.
- Parent: wire `demo` + `inventory export`, README rewrite (Phosphorus
  voice), LICENSE (MIT), `Project_state.md` final, full suite green,
  final commit.